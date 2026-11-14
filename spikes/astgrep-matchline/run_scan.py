#!/usr/bin/env python3
"""Phase-0 ast-grep rule spike scanner (plan-coder issue #18).

Runs the spike rules over the two fixture sets and writes a JSON result file.

Preferred engine: a real ast-grep binary, invoked as
``ast-grep scan --json --rule <rule.yml> <target>``.

The binary is discovered in this order:

1. ``$AST_GREP_BIN``
2. ``ast-grep`` on ``$PATH``
3. ``sg`` on ``$PATH`` — accepted ONLY if ``<bin> --version`` reports ast-grep
   (on some images ``/usr/bin/sg`` is the shadow-utils ``newgrp`` binary, which
   is NOT ast-grep and must never be used as such).

Fallback engine: the bundled *reference scanner* below, which interprets the
very same ast-grep YAML rule files over the Python ``ast`` module.  It exists
so the spike still produces evidence when the ast-grep binary is unavailable
in the environment; results produced by it are clearly labelled
``"engine": "reference-fallback"`` and are weaker evidence than real
ast-grep output.

Usage:
    python3 spikes/astgrep-matchline/run_scan.py
    python3 spikes/astgrep-matchline/run_scan.py --check
    python3 spikes/astgrep-matchline/run_scan.py --engine reference

Outputs (written next to this script):
    results/scan-results.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

SPIKE_DIR = Path(__file__).resolve().parent
RULES_DIR = SPIKE_DIR / "rules"
VIOLATION_DIR = SPIKE_DIR / "fixtures" / "violation"
CLEAN_DIR = SPIKE_DIR / "fixtures" / "clean"
RESULTS_PATH = SPIKE_DIR / "results" / "scan-results.json"

# ast-grep pattern metavariables: $$$NAME (variadic) and $NAME (single node).
_META_RE = re.compile(r"\$\$\$?([A-Za-z_][A-Za-z0-9_]*)?")
_META_PREFIX = "__AG_META_"
_VARIADIC_PREFIX = "__AG_VARIADIC_"


# --------------------------------------------------------------------------
# ast-grep binary discovery
# --------------------------------------------------------------------------
def discover_ast_grep() -> tuple[str | None, str]:
    """Return (binary_path_or_None, human readable reason)."""
    env_bin = os.environ.get("AST_GREP_BIN")
    candidates = []
    if env_bin:
        candidates.append((env_bin, "$AST_GREP_BIN"))
    for name in ("ast-grep", "sg"):
        found = shutil.which(name)
        if found:
            candidates.append((found, f"`{name}` on PATH"))
    for path, origin in candidates:
        try:
            proc = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            return None, f"candidate {path} ({origin}) could not be executed: {exc}"
        out = (proc.stdout or "") + (proc.stderr or "")
        if "ast-grep" in out.lower():
            return path, f"{path} ({origin}) reports: {out.strip().splitlines()[0]}"
        return None, (
            f"candidate {path} ({origin}) is not ast-grep: "
            f"`{path} --version` printed {out.strip()[:120]!r}"
        )
    return None, "no ast-grep binary found ($AST_GREP_BIN, `ast-grep`, `sg`)"


# --------------------------------------------------------------------------
# Reference scanner: an ast-grep-rule interpreter over the Python ast module
# --------------------------------------------------------------------------
_KIND_MAP = {
    ast.Call: "call",
    ast.Attribute: "attribute",
    ast.Name: "identifier",
    ast.Import: "import_statement",
    ast.ImportFrom: "import_from_statement",
    ast.keyword: "keyword_argument",
    ast.Module: "module",
    ast.FunctionDef: "function_definition",
    ast.ClassDef: "class_definition",
    ast.List: "list",
    ast.Tuple: "tuple",
    ast.Dict: "dictionary",
    ast.Assign: "assignment",
    ast.Return: "return_statement",
}


def node_kind(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return "string"
        if isinstance(node.value, bool):
            return "boolean"
        if isinstance(node.value, int):
            return "integer"
        if isinstance(node.value, float):
            return "float"
        return "constant"
    return _KIND_MAP.get(type(node), type(node).__name__.lower())


def compile_pattern(pattern: str) -> ast.AST:
    """Compile an ast-grep pattern string into a Python expression tree.

    ``$NAME`` becomes a wildcard node, ``$$$NAME`` a variadic placeholder.
    """
    counter = [0]

    def repl(match: re.Match) -> str:
        counter[0] += 1
        raw = match.group(0)
        if raw.startswith("$$$"):
            return f"{_VARIADIC_PREFIX}{counter[0]}__"
        return f"{_META_PREFIX}{counter[0]}__"

    src = _META_RE.sub(repl, pattern)
    try:
        return ast.parse(src, mode="eval").body
    except SyntaxError:
        # Statement-level patterns (`import jesse`, `from jesse import ...`).
        module = ast.parse(src)
        if not module.body:
            raise
        first = module.body[0]
        if isinstance(first, ast.Expr):
            return first.value
        return first


def _is_meta(node: ast.AST, prefix: str) -> bool:
    return isinstance(node, ast.Name) and node.id.startswith(prefix)


def _match_aliases(pnames: list, tnames: list) -> bool:
    variadic = [
        a
        for a in pnames
        if isinstance(a.name, str) and a.name.startswith(_VARIADIC_PREFIX)
    ]
    if variadic:
        fixed = [a for a in pnames if a not in variadic]
        target_names = {a.name for a in tnames}
        return all(a.name in target_names for a in fixed)
    if len(pnames) != len(tnames):
        return False
    return all(
        p.name == t.name and (p.asname or None) == (t.asname or None)
        for p, t in zip(pnames, tnames)
    )


def _match_args(pnode: ast.Call, tnode: ast.Call) -> bool:
    p_pos = [a for a in pnode.args if not isinstance(a, ast.keyword)]
    p_kw = [a for a in pnode.args if isinstance(a, ast.keyword)]
    t_pos = [a for a in tnode.args if not isinstance(a, ast.keyword)]
    t_kw = {a.arg: a for a in tnode.args if isinstance(a, ast.keyword)}
    if any(_is_meta(a, _VARIADIC_PREFIX) for a in p_pos):
        prefix = [a for a in p_pos if not _is_meta(a, _VARIADIC_PREFIX)]
        if len(t_pos) < len(prefix):
            return False
        for p, t in zip(prefix, t_pos):
            if not match_pattern(p, t):
                return False
    else:
        if len(p_pos) != len(t_pos):
            return False
        for p, t in zip(p_pos, t_pos):
            if not match_pattern(p, t):
                return False
    for p in p_kw:
        if p.arg not in t_kw:
            return False
        if not match_pattern(p.value, t_kw[p.arg].value):
            return False
    return True


def match_pattern(pnode: ast.AST, tnode: ast.AST) -> bool:
    """Structural match of a compiled ast-grep pattern against a target node."""
    if _is_meta(pnode, _META_PREFIX) or _is_meta(pnode, _VARIADIC_PREFIX):
        return True
    if isinstance(pnode, ast.Constant):
        return (
            isinstance(tnode, ast.Constant)
            and type(pnode.value) is type(tnode.value)
            and pnode.value == tnode.value
        )
    if type(pnode) is not type(tnode):
        return False
    if isinstance(pnode, ast.Attribute):
        return pnode.attr == tnode.attr and match_pattern(pnode.value, tnode.value)
    if isinstance(pnode, ast.Call):
        return match_pattern(pnode.func, tnode.func) and _match_args(pnode, tnode)
    if isinstance(pnode, ast.Import):
        return _match_aliases(pnode.names, tnode.names)
    if isinstance(pnode, ast.ImportFrom):
        if (pnode.module or None) != (tnode.module or None):
            return False
        return _match_aliases(pnode.names, tnode.names)
    if isinstance(pnode, ast.Name):
        return pnode.id == tnode.id
    return ast.dump(pnode) == ast.dump(tnode)


class ReferenceScanner:
    """Interpret ast-grep YAML rules over the Python ast module."""

    def __init__(self) -> None:
        self._patterns: dict[str, ast.AST] = {}

    def _pattern(self, text: str) -> ast.AST:
        if text not in self._patterns:
            self._patterns[text] = compile_pattern(text)
        return self._patterns[text]

    @staticmethod
    def _text(source: str, node: ast.AST) -> str:
        try:
            segment = ast.get_source_segment(source, node)
        except Exception:  # noqa: BLE001  # pragma: no cover - defensive
            segment = None
        if segment is None:
            segment = ast.unparse(node)
        return segment

    @staticmethod
    def _descendants(node: ast.AST) -> list[ast.AST]:
        out: list[ast.AST] = []
        for child in ast.iter_child_nodes(node):
            out.append(child)
            out.extend(ReferenceScanner._descendants(child))
        return out

    def rule_matches(self, rule: dict, node: ast.AST, source: str) -> bool:
        if "any" in rule:
            return any(self.rule_matches(r, node, source) for r in rule["any"])
        if "all" in rule:
            return all(self.rule_matches(r, node, source) for r in rule["all"])
        if "not" in rule:
            return not self.rule_matches(rule["not"], node, source)
        ok = True
        if "kind" in rule:
            ok = ok and node_kind(node) == rule["kind"]
        if "pattern" in rule:
            ok = ok and match_pattern(self._pattern(rule["pattern"]), node)
        if "regex" in rule:
            ok = ok and re.search(rule["regex"], self._text(source, node)) is not None
        if "has" in rule:
            ok = ok and any(
                self.rule_matches(rule["has"], d, source)
                for d in self._descendants(node)
            )
        return ok

    def scan_file(self, rule: dict, path: Path) -> list[dict]:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        hits = []
        for node in ast.walk(tree):
            if not self.rule_matches(rule, node, source):
                continue
            hits.append(
                {
                    "file": str(path),
                    "line": getattr(node, "lineno", 0),
                    "column": getattr(node, "col_offset", 0),
                    "text": self._text(source, node).splitlines()[0][:200],
                }
            )
        return hits


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------
def load_rules(rules_dir: Path) -> list[tuple[Path, dict]]:
    rules = []
    for path in sorted(rules_dir.glob("*.yml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        rules.append((path, spec))
    return rules


def scan_with_ast_grep(binary: str, rules: list[tuple[Path, dict]], target: Path) -> list[dict]:
    """Run `ast-grep scan --json` once per rule over `target`."""
    matches: list[dict] = []
    for rule_path, spec in rules:
        proc = subprocess.run(
            [binary, "scan", "--json", "--rule", str(rule_path), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ast-grep failed on {rule_path.name}: {proc.stderr.strip()[:400]}"
            )
        payload = _parse_ast_grep_json(proc.stdout)
        for file_entry in payload:
            file_path = file_entry.get("file", "")
            for hit in file_entry.get("matches", []):
                matches.append(
                    {
                        "file": file_path,
                        "rule": spec["id"],
                        "line": (hit.get("lines") or {}).get("start", 0),
                        "column": (hit.get("columns") or {}).get("start", 0),
                        "text": (hit.get("text") or "").splitlines()[0][:200],
                    }
                )
    return matches


def _parse_ast_grep_json(stdout: str) -> list[dict]:
    """ast-grep --json emits one JSON object per file; be liberal."""
    text = stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        data = entries
    if isinstance(data, dict):
        data = [data]
    out = []
    for entry in data:
        if isinstance(entry, dict) and "matches" in entry:
            out.append(entry)
    return out


def scan_with_reference(
    rules: list[tuple[Path, dict]], target: Path
) -> list[dict]:
    scanner = ReferenceScanner()
    matches: list[dict] = []
    for rule_path, spec in rules:
        for path in sorted(target.rglob("*.py")):
            for hit in scanner.scan_file(spec["rule"], path):
                hit["rule"] = spec["id"]
                matches.append(hit)
    return matches


def summarize(matches: list[dict], rules: list[tuple[Path, dict]]) -> dict:
    by_rule = {spec["id"]: 0 for _, spec in rules}
    for hit in matches:
        by_rule[hit["rule"]] = by_rule.get(hit["rule"], 0) + 1
    return {
        "total_matches": len(matches),
        "by_rule": by_rule,
        "matches": matches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", default=str(RULES_DIR))
    parser.add_argument("--violation", default=str(VIOLATION_DIR))
    parser.add_argument("--clean", default=str(CLEAN_DIR))
    parser.add_argument("--out", default=str(RESULTS_PATH))
    parser.add_argument(
        "--engine",
        choices=("auto", "ast-grep", "reference"),
        default="auto",
        help="auto = use ast-grep when a real binary is available",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero unless every rule fires on its seeded file and the "
        "clean set produces zero findings",
    )
    args = parser.parse_args(argv)

    rules = load_rules(Path(args.rules))
    if not rules:
        print("no rules found", file=sys.stderr)
        return 2

    binary, reason = discover_ast_grep()
    engine = "reference-fallback"
    version = None
    if args.engine == "ast-grep" and binary is None:
        print(f"ast-grep requested but unavailable: {reason}", file=sys.stderr)
        return 2
    if args.engine != "reference" and binary is not None:
        engine = "ast-grep"
        proc = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, check=False
        )
        version = (proc.stdout or proc.stderr or "").strip().splitlines()[0]

    def run(target: Path) -> list[dict]:
        if engine == "ast-grep":
            return scan_with_ast_grep(binary, rules, target)
        return scan_with_reference(rules, target)

    violation_matches = run(Path(args.violation))
    clean_matches = run(Path(args.clean))

    results = {
        "engine": engine,
        "engine_detail": reason,
        "ast_grep_version": version,
        "rules": [spec["id"] for _, spec in rules],
        "fixture_sets": {
            "violation": summarize(violation_matches, rules),
            "clean": summarize(clean_matches, rules),
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")

    print(f"engine: {engine} ({reason})")
    for name in ("violation", "clean"):
        summary_block = results["fixture_sets"][name]
        print(f"{name}: total_matches={summary_block['total_matches']}")
        for rule_id, count in sorted(summary_block["by_rule"].items()):
            print(f"  {rule_id}: {count}")
    print(f"results written to {out_path}")

    if args.check:
        ok = True
        by_rule_violation = results["fixture_sets"]["violation"]["by_rule"]
        for rule_id, count in by_rule_violation.items():
            if count < 1:
                print(f"CHECK FAILED: rule {rule_id} did not fire", file=sys.stderr)
                ok = False
        clean_total = results["fixture_sets"]["clean"]["total_matches"]
        if clean_total != 0:
            print(
                f"CHECK FAILED: clean fixture set produced {clean_total} finding(s)",
                file=sys.stderr,
            )
            ok = False
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
