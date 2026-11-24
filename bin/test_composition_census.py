#!/usr/bin/env python3
"""Phase 0 test-composition census classifier (plan-coder issue #71).

The census classifies every test file under ``tests/`` into exactly one of
three classes:

  (a) golden / black-box -- a fixture or input goes in and the output is
      compared against an expected artifact, file or band;
  (b) property / invariant -- asserts a property or invariant over generated
      or parametrized cases, or checks a law / ratchet that must hold for all
      inputs of a kind;
  (c) per-unit -- tests one internal helper/module's behaviour directly with
      hand-built inputs.

The classifier is deterministic and stdlib-only: it reads each test file's
source, extracts a small set of named signals, and applies a fixed rule
order.  Every file lands in exactly one class, and the rule that put it
there (plus the signals that fired) is recorded, so a human reading the
report can re-derive every classification by hand.

Fallback rule: when the signals conflict (an (a)-side signal and a (b)-side
signal are both present) or when no class signal fires at all, the file is
recorded as class (c) *and* listed as uncertain, with the conflicting or
absent signals named.

Usage:
    python3 bin/test_composition_census.py --root . --json
    python3 bin/test_composition_census.py --root . --table
    python3 bin/test_composition_census.py --root . --json \
        --durations /tmp/pytest-durations.txt

``--durations`` takes the text output of ``pytest --durations=0 -q`` and
adds a per-class wall-clock share to the JSON payload.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

CLASS_A = "a-golden-blackbox"
CLASS_B = "b-property-invariant"
CLASS_C = "c-per-unit"

CLASS_LABELS = {
    CLASS_A: "golden / black-box",
    CLASS_B: "property / invariant",
    CLASS_C: "per-unit",
}

ARTIFACT_PATH_RE = re.compile(
    r"(^|[/\\])fixtures?([/\\]|$)"
    r"|(^|[/\\])(expected|golden|snapshot|baseline)[^/\\]*"
    r"\.(json|jsonl|csv|tsv|txt|md|ya?ml|toml|py|log)$",
    re.IGNORECASE,
)

ARTIFACT_NAME_RE = re.compile(
    r"^(FIXTURES?|FIX_DIR|FIXTURE_DIR|ARTIFACTS?|GOLDENS?|SNAPSHOTS?"
    r"|SPECS|REGRESSION[A-Z0-9_]*|BASELINE[A-Z0-9_]*)$"
)

EXPECTED_NAME_RE = re.compile(r"^EXPECTED[A-Z0-9_]*$")

READ_METHODS = {"open", "read_text", "read_bytes", "readlines"}

COMPARE_CALL_RE = re.compile(
    r"^(assertEqual|assertIn|assertMultiLineEqual|assertSequenceEqual"
    r"|assertListEqual|assertDictEqual|assertNotEqual|assertRegex"
    r"|assertCountEqual|assertAlmostEqual)$"
)

PROPERTY_FRAMEWORK_RE = re.compile(
    r"^(hypothesis|given|settings|strategies|parametrize|parametrize_with"
    r"|pytest_generate_tests|example|assume)$"
)

SCAN_CALL_RE = re.compile(
    r"^(rglob|glob|iglob|walk|iterdir|listdir|scandir|listdir)$"
)

LAW_NAME_RE = re.compile(
    r"ratchet|invariant|\blaw\b|monotonic|idempotent",
    re.IGNORECASE,
)

ASSERT_CALL_RE = re.compile(
    r"^(assert\w*|self\.assert\w*|fail|assertRaises\w*)$"
)


def _is_docstring(node: ast.AST) -> bool:
    """True when this Constant node sits in a docstring position."""
    parent = getattr(node, "_census_parent", None)
    if parent is None:
        return False
    body = getattr(parent, "body", None)
    if not isinstance(body, list) or not body:
        return False
    first = body[0]
    if isinstance(first, ast.Expr) and first.value is node:
        return True
    return first is node


def _collect_strings(tree: ast.AST) -> list[tuple[str, int]]:
    """String literal values with line numbers, docstrings excluded."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not _is_docstring(node):
                out.append((node.value, node.lineno))
    return out


def _call_name(node: ast.Call) -> str:
    """Best-effort dotted name of a call target."""
    func = node.func
    if isinstance(func, ast.Attribute):
        parts = []
        cur: ast.AST = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _assign_names(tree: ast.AST) -> set[str]:
    """Every name bound by an assignment / import / def in the file."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _imports(tree: ast.AST) -> list[str]:
    """Every imported module path mentioned in the file."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            out.append(module)
            out.extend(f"{module}.{alias.name}" for alias in node.names)
    return out


def _module_under_test(tree: ast.AST, root: Path) -> str | None:
    """Name of the internal module the file exercises directly, if any."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and \
                _call_name(node).endswith("spec_from_file_location"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if re.search(r"(^|[/\\])bin[/\\]", arg.value) and \
                            arg.value.endswith(".py"):
                        return arg.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node).endswith("path.insert"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if re.search(r"(^|[/\\])bin[/\\]?$", arg.value):
                        return "bin/"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "." not in alias.name:
                    if (root / "bin" / f"{alias.name}.py").exists():
                        return f"bin/{alias.name}.py"
    return None


def _scan_signals(tree: ast.AST) -> list[str]:
    """Names of filesystem-scan calls used anywhere in the file."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name and SCAN_CALL_RE.match(name.split(".")[-1]):
                out.add(name)
    return sorted(out)


def _collection_constants(tree: ast.AST) -> set[str]:
    """Names bound to a literal collection at module or class scope.

    These are the shared "collection of cases" a quantified invariant
    iterates over.  A collection written *inside* a test function is a
    hand-built input for that one test, so it does not count.
    """
    names: set[str] = set()

    def visit(body: list) -> None:
        for node in body:
            if isinstance(node, ast.Assign) and isinstance(
                    node.value, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(
                    node.value, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
                if isinstance(node.target, ast.Name):
                    names.add(node.target.id)
            elif isinstance(node, ast.ClassDef):
                visit(node.body)

    visit(getattr(tree, "body", []))
    return names


def _artifact_handles(tree: ast.AST) -> dict[str, str]:
    """Names bound to a path that points at a stored artifact.

    A name qualifies when the expression it is bound to contains a string
    literal that looks like a fixture / expected-artifact path, e.g.
    ``FIXTURE = REPO / "tests" / "fixtures" / "dashboard_state"``.
    """
    handles: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if value is None:
            continue
        literals = [s.value for s in ast.walk(value)
                    if isinstance(s, ast.Constant) and isinstance(s.value, str)]
        hit = next((lit for lit in literals if ARTIFACT_PATH_RE.search(lit)), None)
        if hit is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                handles[target.id] = hit
    return handles


def _artifact_reads(tree: ast.AST, handles: dict[str, str]) -> list[str]:
    """Read calls whose path resolves to a stored artifact."""
    reads: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node).split(".")[-1] not in READ_METHODS:
            continue
        used = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if ARTIFACT_PATH_RE.search(sub.value):
                    used = True
            elif isinstance(sub, ast.Name) and sub.id in handles:
                used = True
        if used:
            try:
                reads.append(ast.unparse(node))
            except Exception:  # noqa: BLE001 - ast.unparse guard: any failure means no signal
                reads.append("<read>")
    return sorted(set(reads))


def _quantified_loops(tree: ast.AST) -> tuple[list[str], list[str]]:
    """Loops that assert a property over a *collection* of cases.

    Returns ``(triggers, inline)`` where ``triggers`` are loops over a
    collection whose members are "inputs of a kind" rather than one
    hand-built input -- a filesystem scan (glob/rglob/walk/iterdir/listdir/
    scandir) or a name bound to a literal collection constant -- while
    ``inline`` are loops over a literal collection written inline in the
    loop header, i.e. hand-built cases (per-unit, not property).
    """
    constants = _collection_constants(tree)
    triggers: list[str] = []
    inline: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        asserts = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = _call_name(sub)
                if name and ASSERT_CALL_RE.match(name.split(".")[-1]):
                    asserts.append(name)
            elif isinstance(sub, ast.Assert):
                asserts.append("assert")
        if not asserts:
            continue
        iterable = node.iter
        kind = None
        if isinstance(iterable, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            kind = "inline-collection"
        elif isinstance(iterable, ast.Name) and iterable.id in constants:
            kind = "collection-constant"
        elif isinstance(iterable, ast.Attribute) and \
                isinstance(iterable.value, ast.Name) and \
                iterable.value.id in constants:
            kind = "collection-constant"
        else:
            try:
                text = ast.unparse(iterable)
            except Exception:  # noqa: BLE001 - ast.unparse guard: any failure means no signal
                text = ""
            if re.search(r"\b(glob|rglob|iglob|walk|iterdir|listdir|scandir)\b",
                         text):
                kind = "filesystem-scan"
        if kind:
            try:
                text = ast.unparse(iterable)
            except Exception:  # noqa: BLE001 - ast.unparse guard: any failure means no signal
                text = "<expr>"
            entry = f"{kind}:{text}"
            if kind == "inline-collection":
                inline.append(entry)
            else:
                triggers.append(entry)
    return sorted(set(triggers)), sorted(set(inline))


def _test_functions(tree: ast.AST) -> list[str]:
    """Names of every test function/method defined in the file."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                out.append(node.name)
    return sorted(out)


def _has_property_framework(names: set[str], imports: list[str]) -> list[str]:
    """Property-testing / parametrization framework markers, if any."""
    hits = sorted(n for n in names if PROPERTY_FRAMEWORK_RE.match(n))
    for imp in imports:
        if imp.startswith("hypothesis"):
            hits.append(imp)
    return sorted(set(hits))


def analyse(path: Path, root: Path | None = None) -> dict:
    """Analyse one test file and return its signals and classification."""
    if root is None:
        root = path.resolve().parent.parent
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body:
                first = body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    first.value._census_parent = node

    names = _assign_names(tree)
    imports = _imports(tree)

    artifact_handles = _artifact_handles(tree)
    expected_handles = sorted(n for n in names if EXPECTED_NAME_RE.match(n))
    artifact_reads = _artifact_reads(tree, artifact_handles)
    artifact_use = sorted({
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in artifact_handles
    })
    compare_calls = sorted({
        _call_name(node).split(".")[-1] for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and COMPARE_CALL_RE.match(_call_name(node).split(".")[-1])
    })
    if any(isinstance(node, ast.Assert) for node in ast.walk(tree)):
        compare_calls = sorted(set(compare_calls) | {"assert-statement"})
    framework_hits = _has_property_framework(names, imports)
    scan_calls = _scan_signals(tree)
    test_names = _test_functions(tree)
    law_tests = sorted(t for t in test_names if LAW_NAME_RE.search(t))
    module_under_test = _module_under_test(tree, root)

    a_signals: list[str] = []
    if artifact_handles:
        a_signals.append("artifact-handle:" + ";".join(
            sorted(artifact_handles)[:3]))
    if expected_handles:
        a_signals.append("expected-handle:" + ";".join(expected_handles[:3]))
    if artifact_reads:
        a_signals.append("artifact-read:" + artifact_reads[0][:60])
    elif artifact_use:
        a_signals.append("artifact-use:" + ";".join(artifact_use[:3]))
    compared = bool(compare_calls)
    if compared:
        a_signals.append("compare:" + ";".join(compare_calls[:3]))
    artifact_read = bool(artifact_reads or artifact_use or expected_handles)
    golden = artifact_read and compared

    b_signals: list[str] = []
    if framework_hits:
        b_signals.append("property-framework:" + ";".join(framework_hits[:3]))
    quantified, inline_cases = _quantified_loops(tree)
    if quantified:
        b_signals.append("quantified-invariant:" + quantified[0])
    if law_tests:
        b_signals.append("law-name:" + ";".join(law_tests[:3]))
    property_class = bool(framework_hits or quantified or law_tests)

    c_signals: list[str] = []
    if module_under_test:
        c_signals.append("unit-target:" + str(module_under_test))
    if compare_calls and not artifact_read:
        c_signals.append("direct-assert")
    if inline_cases:
        c_signals.append("hand-built-case-loop:" + inline_cases[0][:60])

    uncertain = False
    reasons: list[str] = []
    if golden and property_class:
        cls = CLASS_C
        rule = "R1-conflict-artifact-and-property"
        uncertain = True
        reasons.append("conflicting signals: (a)-side " + "; ".join(a_signals)
                       + " AND (b)-side " + "; ".join(b_signals))
    elif golden:
        cls = CLASS_A
        rule = "R2-artifact-compare"
    elif property_class:
        cls = CLASS_B
        rule = ("R3-property-framework" if framework_hits
                else "R4-quantified-invariant")
    elif artifact_read or artifact_handles or expected_handles:
        cls = CLASS_C
        rule = "R2b-artifact-without-comparison"
        uncertain = True
        reasons.append("partial (a)-side signal: artifact referenced but no "
                       "comparison assertion found; no (b)-side signal")
    elif module_under_test or compare_calls:
        cls = CLASS_C
        rule = "R5-per-unit"
    else:
        cls = CLASS_C
        rule = "R6-fallback-no-signal"
        uncertain = True
        reasons.append("absent signals: no artifact/compare, no property or "
                       "quantified-invariant, no direct module target")

    return {
        "file": path.as_posix(),
        "class": cls,
        "class_label": CLASS_LABELS[cls],
        "rule": rule,
        "uncertain": uncertain,
        "reasons": reasons,
        "signals": {
            "a_side": a_signals,
            "b_side": b_signals,
            "c_side": c_signals,
        },
        "test_functions": len(test_names),
        "test_function_names": test_names,
        "artifact_paths": sorted(artifact_handles.values()),
        "scan_calls": scan_calls,
        "module_under_test": module_under_test,
    }


def collect(root: Path) -> list[dict]:
    """Analyse every tests/test_*.py file under root, sorted by path."""
    tests_dir = root / "tests"
    files = sorted(p for p in tests_dir.glob("test_*.py") if p.is_file())
    records = []
    for path in files:
        record = analyse(path, root)
        record["file"] = path.relative_to(root).as_posix()
        records.append(record)
    return records


def shares(records: list[dict]) -> dict:
    """Per-class file counts, function counts and shares."""
    out = {}
    for cls in (CLASS_A, CLASS_B, CLASS_C):
        rows = [r for r in records if r["class"] == cls]
        out[cls] = {
            "label": CLASS_LABELS[cls],
            "files": len(rows),
            "test_functions": sum(r["test_functions"] for r in rows),
            "uncertain_files": sum(1 for r in rows if r["uncertain"]),
        }
    total_files = len(records)
    total_fns = sum(r["test_functions"] for r in records)
    for cls, data in out.items():
        data["file_share"] = (round(data["files"] / total_files, 4)
                              if total_files else 0.0)
        data["test_function_share"] = (round(data["test_functions"] / total_fns, 4)
                                       if total_fns else 0.0)
    out["totals"] = {
        "files": total_files,
        "test_functions": total_fns,
        "uncertain_files": sum(1 for r in records if r["uncertain"]),
    }
    return out


DURATION_RE = re.compile(
    r"^([0-9.]+)s\s+\S+\s+(tests/test_[^:\s]+\.py)::", re.MULTILINE)


def duration_shares(records: list[dict], durations_text: str) -> dict:
    """Per-class wall-clock share from a pytest --durations=0 output."""
    per_file: dict[str, float] = {}
    for value, path in DURATION_RE.findall(durations_text):
        per_file[path] = per_file.get(path, 0.0) + float(value)
    by_class = {cls: 0.0 for cls in (CLASS_A, CLASS_B, CLASS_C)}
    unmapped = 0.0
    for record in records:
        by_class[record["class"]] += per_file.get(record["file"], 0.0)
    known = {r["file"] for r in records}
    for path, value in per_file.items():
        if path not in known:
            unmapped += value
    total = sum(by_class.values())
    out = {}
    for cls, value in by_class.items():
        out[cls] = {
            "label": CLASS_LABELS[cls],
            "seconds": round(value, 3),
            "share": round(value / total, 4) if total else 0.0,
        }
    out["totals"] = {
        "seconds": round(total, 3),
        "unmapped_seconds": round(unmapped, 3),
        "files_measured": len(per_file),
    }
    return out


def main(argv: list[str] | None = None) -> int:
    """Print the per-file table and the class shares."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--table", action="store_true", help="emit a text table")
    parser.add_argument("--durations", default=None,
                        help="path to a pytest --durations=0 -q output file")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    records = collect(root)
    payload = {
        "root": root.name,
        "classes": {cls: CLASS_LABELS[cls] for cls in
                    (CLASS_A, CLASS_B, CLASS_C)},
        "files": records,
        "shares": shares(records),
    }
    if args.durations:
        text = Path(args.durations).read_text(encoding="utf-8")
        payload["wall_clock"] = duration_shares(records, text)

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(f"{'file':60s} {'class':26s} rule")
        for record in records:
            flag = " [uncertain]" if record["uncertain"] else ""
            print(f"{record['file']:60s} {record['class']:26s} "
                  f"{record['rule']}{flag}")
        data = payload["shares"]
        print()
        for cls in (CLASS_A, CLASS_B, CLASS_C):
            row = data[cls]
            print(f"{cls:26s} files={row['files']:3d} "
                  f"({row['file_share']:.1%})  tests={row['test_functions']:4d} "
                  f"({row['test_function_share']:.1%})")
        print(f"totals: files={data['totals']['files']} "
              f"tests={data['totals']['test_functions']} "
              f"uncertain={data['totals']['uncertain_files']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
