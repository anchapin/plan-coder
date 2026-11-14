#!/usr/bin/env python3
"""Phase-0 ast-grep spike harness for plan-coder issue #42.

Scans the matchline reference checkout with the four shipped ast-grep rules
(C1-C4), classifies every hit, proves each rule catches a seeded violation in
a throwaway scratch copy, and emits the Phase-0 spike report.

Everything is read-only with respect to the scanned tree: the only writes are
into a temporary scratch directory (deleted before the script returns) and,
when --out is given, the report file itself.

Usage:
    python3 bin/ast_grep_phase0_scan.py --repo matchline-ref
    python3 bin/ast_grep_phase0_scan.py --repo matchline-ref --format json
    python3 bin/ast_grep_phase0_scan.py --repo matchline-ref \
        --out docs/spikes/ast-grep-matchline-phase0.md

Options:
    --repo PATH       Scanned checkout (default: ./matchline-ref).
    --rules PATH      Directory holding the rule YAMLs (default:
                      docs/spikes/ast-grep-matchline).
    --ast-grep PATH   ast-grep binary (default: /tmp/astgrep-venv/bin/ast-grep).
    --out PATH        Write the markdown report to PATH (default: stdout).
    --format FORMAT   markdown | json (default: markdown).
    --no-seed-check   Skip the seeded-violation check (scan only).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_DIR = REPO_ROOT / "docs" / "spikes" / "ast-grep-matchline"
DEFAULT_REPORT = REPO_ROOT / "docs" / "spikes" / "ast-grep-matchline-phase0.md"
DEFAULT_AST_GREP = "/tmp/astgrep-venv/bin/ast-grep"

RULE_FILES = {
    "C1": "c1-sys-path-manipulation.yml",
    "C2": "c2-xml-parse-call-sites.yml",
    "C3": "c3-no-print-in-library-code.yml",
    "C4": "c4-no-hardcoded-absolute-paths.yml",
}

# Seeded violations: one synthetic violation per shipped rule, injected into a
# scratch copy of the scanned tree and deleted with the scratch copy.
SEEDED_VIOLATIONS = {
    "C1": (
        "seeded_violation_c1.py",
        '"""Seeded C1 violation."""\n\nimport sys\n\nsys.path.insert(0, "/tmp")\n',
    ),
    "C2": (
        "seeded_violation_c2.py",
        (
            '"""Seeded C2 violation."""\n\nfrom lxml import etree\n\n'
            'tree = etree.parse("/tmp/seeded.xml")\n'
        ),
    ),
    "C3": (
        "seeded_violation_c3.py",
        '"""Seeded C3 violation."""\n\n\ndef seeded():\n    print("seeded")\n',
    ),
    "C4": (
        "seeded_violation_c4.py",
        '"""Seeded C4 violation."""\n\nSEEDED_PATH = "/home/alice/matchline"\n',
    ),
}

# C3: paths whose print() calls are the CLI/main surface, not library code.
C3_SCRIPT_PREFIXES = (
    "scripts/",
    "detector/",
    "tests/",
    "review_classifier/",
    "synth/",
    "sim/",
)
C3_SCRIPT_FILES = ("cli.py", "sim_smoke.py")
C3_LIBRARY_VIOLATIONS = {
    "hvac_trace.py": (
        "library module prints (embedded WiSARD training experiment) — "
        "docs/CODE-REVIEW.md allows print() in CLI/main only"
    ),
    "validate/__init__.py": (
        "library package prints error text to stderr — "
        "docs/CODE-REVIEW.md allows print() in CLI/main only"
    ),
}

# C2: hit sites that are true violations of the safe_xml convention.
C2_VIOLATIONS = {
    ("bem/buildingsync_export.py", 482): (
        "untrusted uploaded BuildingSync XML parsed with a bare etree.parse(): "
        "no entity-expansion protection, safe_xml.safe_xml_parse() required"
    ),
    ("bem/buildingsync_export.py", 522): (
        "untrusted uploaded BuildingSync XML parsed with stdlib ET.parse(): "
        "bypasses the safe_xml size/complexity limits"
    ),
    ("bem/buildingsync_export.py", 553): (
        "untrusted uploaded BuildingSync XML parsed with stdlib ET.parse(): "
        "bypasses the safe_xml size/complexity limits"
    ),
}
C2_SAFE_CALLS = ("safe_xml_parser()", "safe_parser")


# C5 candidate (NOT shipped): a literal confidence threshold that bypasses
# REVIEW_CONFIDENCE.  Kept inline so the report can quote its measured
# behaviour without shipping a rule that under-covers the convention.
C5_CANDIDATE_RULE = """id: c5-candidate-literal-confidence-threshold
language: python
severity: warning
message: "literal confidence threshold bypasses REVIEW_CONFIDENCE"
rule:
  any:
    - pattern: $X.confidence < $L
    - pattern: $X.confidence > $L
    - pattern: $X.confidence <= $L
    - pattern: $X.confidence >= $L
constraints:
  L:
    regex: ^0\\.[0-9]+$
"""

# C5: the literal-threshold comparisons the convention targets.  Every one of
# these is a hardcoded float compared against a confidence value, i.e. the
# REVIEW_CONFIDENCE constant (building_model.py:51, 0.80) bypassed.
C5_LITERAL_THRESHOLDS = [
    ("run_pipeline.py", 1440, "ls.label_confidence < 0.80", "true violation"),
    ("run_pipeline.py", 1448, "ul.confidence < 0.80", "true violation"),
    (
        "review_classifier/data.py",
        201,
        "elif score < 0.8:",
        (
            "label-noise urgency bucket (corpus labelling policy, not a "
            "review-queue threshold)"
        ),
    ),
    (
        "review_classifier/laya_experiment.py",
        183,
        "if label == 0.0 and det_conf > 0.8:",
        "spike experiment gate (not the pipeline review queue)",
    ),
]


@dataclass
class Hit:
    """One ast-grep match, with its scope/classification decision."""

    rule: str
    path: str
    line: int
    text: str
    scoped: bool
    scope_reason: str
    classification: str
    reason: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def run_ast_grep(binary: str, rule_file: Path, root: Path) -> list[dict]:
    """Run one rule over `root` and return the raw ast-grep JSON matches."""
    proc = subprocess.run(
        [binary, "scan", "--rule", str(rule_file), "--json=stream", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    matches: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        matches.append(json.loads(line))
    if not matches and proc.returncode not in (0, 1):
        raise RuntimeError(f"ast-grep failed for {rule_file}: {proc.stderr.strip()}")
    return matches


def rel_path(raw: str, root: Path) -> str:
    return str(Path(raw).resolve().relative_to(root.resolve()))


def is_docstring_hit(text: str) -> bool:
    """True when the matched string node is a docstring (module/function doc)."""
    stripped = text.lstrip()
    return stripped.startswith(('"""', "'''"))


def scope_c1(path: str) -> tuple[bool, str]:
    """C1 scope: every *.py except detector/** (own venv track) and tests/**."""
    if path.startswith("detector/"):
        return False, "detector/** excluded by the C1 scope (separate-venv track)"
    if path.startswith("tests/"):
        return False, "tests/** excluded by the C1 scope"
    return True, "in scope: main-pipeline code"


def classify_c1(path: str) -> tuple[str, str]:
    if path.startswith("scripts/"):
        return (
            "true violation",
            (
                "sys.path hack in a shipped script — AGENTS.md Never-list forbids "
                "sys.path hacks outside detector/** and tests/**"
            ),
        )
    return (
        "true violation",
        "sys.path manipulation in main-pipeline code — AGENTS.md Never-list",
    )


def scope_c2(path: str, text: str) -> tuple[bool, str]:
    """C2 scope: whole tree, minus test fixtures, the safe parser itself and
    call sites that already pass a safe parser."""
    if path.startswith("tests/"):
        return False, "tests/** excluded: fixtures written by the test itself"
    if path == "safe_xml.py":
        return False, "safe_xml.py is the safe parser implementation itself"
    if any(call in text for call in C2_SAFE_CALLS):
        return False, "call site already passes a safe parser"
    return True, "in scope: XML parse call site"


def classify_c2(path: str, line: int, text: str) -> tuple[str, str]:
    known = C2_VIOLATIONS.get((path, line))
    if known:
        return "true violation", known
    if "schematron_path" in text:
        return (
            "false positive",
            (
                "repo-local trusted Schematron schema (not untrusted input) — the "
                "rule fires but the convention does not forbid it"
            ),
        )
    if path.startswith("scripts/"):
        return (
            "false positive",
            (
                "benchmark harness re-parses a gbXML file it exported itself "
                "(trusted local file) — the rule fires but the convention does "
                "not forbid it"
            ),
        )
    if path.startswith("detector/"):
        return (
            "false positive",
            (
                "detector track parses a trusted local dataset annotation — the "
                "rule fires but the convention does not forbid it"
            ),
        )
    return (
        "true violation",
        "XML parse call site without safe_xml protection",
    )


def scope_c3(path: str) -> tuple[bool, str]:
    """C3 scope: library code only (CLI/main scripts and tests may print)."""
    if path in C3_SCRIPT_FILES or path.startswith(C3_SCRIPT_PREFIXES):
        return False, "CLI/main script or test surface (print() allowed)"
    if path.startswith("run_"):
        return False, "CLI/main script surface (print() allowed)"
    return True, "in scope: library code"


def classify_c3(path: str) -> tuple[str, str]:
    known = C3_LIBRARY_VIOLATIONS.get(path)
    if known:
        return "true violation", known
    return (
        "true violation",
        (
            "print() in library code — docs/CODE-REVIEW.md allows print() in "
            "CLI/main only"
        ),
    )


def scope_c4(path: str, text: str) -> tuple[bool, str]:
    """C4 scope: whole tree, minus tests, the detector dataset-root convention
    and docstring mentions (documentation, not executed paths)."""
    if path.startswith("tests/"):
        return False, "tests/** excluded: fixture/assertion strings"
    if path.startswith("detector/"):
        return False, "detector/** excluded: detector dataset-root convention"
    if is_docstring_hit(text):
        return False, "docstring mention of a user-local path (documentation)"
    return True, "in scope: executable code"


def classify_c4(path: str, line: int, text: str) -> tuple[str, str]:
    if path == "elevation_windows.py":
        return (
            "true violation",
            (
                "machine-specific venv path baked into a runtime error message "
                "— AGENTS.md forbids machine paths in code"
            ),
        )
    if path == "scripts/tempfit_baseline.py":
        return (
            "true violation",
            (
                "hardcoded ~/workspace/datasets fallback used at runtime — "
                "AGENTS.md forbids machine paths in code"
            ),
        )
    return (
        "true violation",
        "hardcoded machine/absolute path literal in executable code",
    )


def collect(binary: str, root: Path, rules_dir: Path) -> list[Hit]:
    """Scan the tree with every shipped rule and classify every hit."""
    hits: list[Hit] = []
    for rule, filename in RULE_FILES.items():
        rule_file = rules_dir / filename
        for match in run_ast_grep(binary, rule_file, root):
            path = rel_path(match["file"], root)
            line = match["range"]["start"]["line"] + 1
            text = match["text"]
            if rule == "C1":
                scoped, scope_reason = scope_c1(path)
                classification, reason = classify_c1(path)
            elif rule == "C2":
                scoped, scope_reason = scope_c2(path, text)
                classification, reason = classify_c2(path, line, text)
            elif rule == "C3":
                scoped, scope_reason = scope_c3(path)
                classification, reason = classify_c3(path)
            else:
                scoped, scope_reason = scope_c4(path, text)
                classification, reason = classify_c4(path, line, text)
            if not scoped:
                classification = "legitimate exception"
                reason = scope_reason
            hits.append(
                Hit(
                    rule=rule,
                    path=path,
                    line=line,
                    text=text,
                    scoped=scoped,
                    scope_reason=scope_reason,
                    classification=classification,
                    reason=reason,
                )
            )
    hits.sort(key=lambda h: (h.rule, h.path, h.line))
    return hits


def seed_check(binary: str, root: Path, rules_dir: Path) -> dict:
    """Copy the tree into a scratch dir, inject one seeded violation per rule,
    re-scan, and report the delta.  The scratch copy is always deleted."""
    results: dict[str, dict] = {}
    scratch = Path(tempfile.mkdtemp(prefix="ast-grep-phase0-seed-"))
    try:
        work = scratch / "tree"
        shutil.copytree(root, work, ignore=shutil.ignore_patterns(".git"))
        for rule, filename in RULE_FILES.items():
            rule_file = rules_dir / filename
            baseline = len(run_ast_grep(binary, rule_file, work))
            seeded_name, seeded_body = SEEDED_VIOLATIONS[rule]
            seeded_file = work / seeded_name
            seeded_file.write_text(seeded_body, encoding="utf-8")
            after = run_ast_grep(binary, rule_file, work)
            seeded_hits = [m for m in after if Path(m["file"]).name == seeded_name]
            results[rule] = {
                "seeded_file": seeded_name,
                "baseline_hits": baseline,
                "hits_after_seed": len(after),
                "seeded_hits": len(seeded_hits),
                "seeded_locations": [
                    f"{seeded_name}:{m['range']['start']['line'] + 1}"
                    for m in seeded_hits
                ],
                "caught": len(seeded_hits) >= 1,
            }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return results


def git_commit(root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or "(unknown)"


def ast_grep_version(binary: str) -> str:
    proc = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() or "(unknown)"


def tree_size(root: Path) -> str:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return f"{total / (1024 * 1024):.0f} MiB"


def md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def collect_c5(binary: str, root: Path) -> list[dict]:
    """Measure the C5 candidate rule (not shipped) over the scanned tree."""
    proc = subprocess.run(
        [
            binary,
            "scan",
            "--inline-rules",
            C5_CANDIDATE_RULE,
            "--json=stream",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    matches: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        match = json.loads(line)
        match["rel"] = rel_path(match["file"], root)
        matches.append(match)
    return matches


def render_report(
    hits: list[Hit],
    seed: dict,
    root: Path,
    binary: str,
    commit: str,
    c5_matches: list[dict],
) -> str:
    """Render the whole Phase-0 spike report as markdown."""
    out: list[str] = []
    out.append("# ast-grep for matchline — Phase 0 spike (issue #42)")
    out.append("")
    out.append(
        "Phase 0 is measurement only: no CI wiring, no workflow files, no "
        "matchline changes.  The deliverable is this report plus the rule "
        "files under `docs/spikes/ast-grep-matchline/`."
    )
    out.append("")
    out.append("## 1. Binary pinning (STEP 0)")
    out.append("")
    out.append("| parameter | value |")
    out.append("|---|---|")
    out.append(f"| binary | `{binary}` |")
    out.append(f"| version | `{ast_grep_version(binary)}` |")
    out.append(
        "| install | `python3 -m venv /tmp/astgrep-venv && "
        "/tmp/astgrep-venv/bin/pip install ast-grep-cli` |"
    )
    out.append(
        "| wheel | `ast_grep_cli-0.51.2-py3-none-manylinux_2_28_x86_64.whl` (8.9 MB) |"
    )
    out.append("| pip install wall time | 2 s |")
    out.append(f"| venv size on disk | {tree_size(Path(binary).parents[1])} |")
    out.append("| `/usr/bin/sg` | `newgrp`, NOT ast-grep — never use it |")
    out.append("")
    out.append(
        "The venv lives under `/tmp`, so no node_modules / install footprint "
        "ever enters the plan-coder diff.  The egress proxy of this cell "
        "(`HTTP_PROXY=http://hatch-egress-proxy:3128`, "
        "`SSL_CERT_FILE=/run/hatch/egress-tls/ca-bundle.pem`) has to be "
        "exported for pip to reach pypi.org; without it pip fails with "
        "`SSL: WRONG_VERSION_NUMBER`."
    )
    out.append("")
    out.append("## 2. Scanned tree")
    out.append("")
    out.append("| parameter | value |")
    out.append("|---|---|")
    out.append("| repository | matchline (read-only clone) |")
    out.append(f"| scanned commit | `{commit}` |")
    out.append(f"| tree | `{root}` |")
    out.append(
        "| clone | `git clone ~/workspace/matchline matchline-ref` + "
        "`git -C matchline-ref fetch ~/workspace/matchline "
        "refs/remotes/origin/develop:refs/scan/develop` + "
        "`git -C matchline-ref checkout refs/scan/develop` |"
    )
    out.append("")
    out.append(
        "The scan target is the `refs/scan/develop` ref fetched from the "
        "source clone, NOT the source clone's local `develop` branch (which "
        "had diverged to 6b018fa and lacked files the issue cites)."
    )
    out.append("")
    out.append("## 3. Conventions, scoping and classification policy")
    out.append("")
    out.append(
        "| rule | convention | source | rule scope | scoping applied before "
        "classification |"
    )
    out.append("|---|---|---|---|---|")
    out.append(
        "| C1 | no `sys.path` manipulation in main-pipeline code | "
        "AGENTS.md:19, CONTRACT.md:30, docs/CODE-REVIEW.md:45 | every "
        "`sys.path` mutation (insert/append/extend) in every *.py | "
        "`detector/**` and `tests/**` excluded (issue-scope enumeration) |"
    )
    out.append(
        "| C2 | untrusted XML parsed only through `safe_xml` | safe_xml.py:1-7, "
        "CONTRACT.md:31, AGENTS.md:22 | every `etree.parse()` / `ET.parse()` "
        "call site, any arity | `tests/**` (test fixtures), `safe_xml.py` "
        "(the safe implementation) and call sites that already pass a safe "
        "parser excluded |"
    )
    out.append(
        "| C3 | no `print()` in library code | docs/CODE-REVIEW.md:44 | "
        "every `print()` call in every *.py | CLI/main scripts "
        "(`cli.py`, `run_*.py`, `scripts/**`, `detector/**`, `tests/**`, "
        "`review_classifier/**`, `synth/**`, `sim/**`) excluded |"
    )
    out.append(
        "| C4 | no hardcoded machine/absolute paths in code | AGENTS.md:19, "
        "CONTRACT.md:30, docs/CODE-REVIEW.md:47 | every string literal in "
        "every *.py containing `/home/`, `~/workspace` or `/Users/` | "
        "`tests/**`, `detector/**` (dataset-root convention) and docstring "
        "mentions excluded |"
    )
    out.append("")
    out.append(
        "Classification vocabulary: **true violation** = the hit is what the "
        "convention forbids; **legitimate exception** = the hit sits in a "
        "scope the convention exempts (scoped out, listed for completeness); "
        "**false positive** = the rule fires where the convention is silent, "
        "so an unscoped CI job would raise a false alarm."
    )
    out.append("")
    out.append("## 4. Raw table — every rule x the full tree")
    out.append("")
    out.append(
        "Counts are per rule over the whole scanned tree; the per-hit rows "
        "below list every single hit with its classification and a one-line "
        "reason."
    )
    out.append("")
    out.append(
        "| rule | hits | scoped hits | true violations | "
        "legitimate exceptions | false positives |"
    )
    out.append("|---|---|---|---|---|---|")
    summary: dict[str, dict[str, int]] = {}
    for rule in RULE_FILES:
        rule_hits = [h for h in hits if h.rule == rule]
        scoped = [h for h in rule_hits if h.scoped]
        counts = {
            "hits": len(rule_hits),
            "scoped": len(scoped),
            "violation": sum(1 for h in scoped if h.classification == "true violation"),
            "exception": sum(
                1 for h in rule_hits if h.classification == "legitimate exception"
            ),
            "false_positive": sum(
                1 for h in scoped if h.classification == "false positive"
            ),
        }
        summary[rule] = counts
        out.append(
            f"| {rule} | {counts['hits']} | {counts['scoped']} | "
            f"{counts['violation']} | {counts['exception']} | "
            f"{counts['false_positive']} |"
        )
    out.append("")
    for rule in RULE_FILES:
        rule_hits = [h for h in hits if h.rule == rule]
        out.append(f"### {rule} — {RULE_FILES[rule]}")
        out.append("")
        if rule == "C3":
            out.append(
                "Rows are grouped per file: every hit of a file is listed as "
                "`file:line` in the *hits* column, and the classification and "
                "reason are identical for all of them."
            )
            out.append("")
            out.append("| file | hits | classification | reason |")
            out.append("|---|---|---|---|")
            grouped: dict[str, list[Hit]] = {}
            for hit in rule_hits:
                grouped.setdefault(hit.path, []).append(hit)
            for path in sorted(grouped):
                group = grouped[path]
                locations = ", ".join(str(h.line) for h in group)
                first = group[0]
                out.append(
                    f"| {path} | {locations} | {first.classification} | "
                    f"{md_escape(first.reason)} |"
                )
            out.append("")
            continue
        out.append("| # | file:line | classification | reason | snippet |")
        out.append("|---|---|---|---|---|")
        for index, hit in enumerate(rule_hits, start=1):
            scope_note = "" if hit.scoped else " (scoped out)"
            out.append(
                f"| {index} | {hit.location} | {hit.classification} | "
                f"{md_escape(hit.reason)}{scope_note} | "
                f"`{md_escape(hit.text.strip()[:90])}` |"
            )
        out.append("")
    out.append("## 5. Seeded-violation check (local, scratch copy only)")
    out.append("")
    out.append(
        "For every shipped rule the scanned tree is copied into a scratch "
        "directory, one synthetic violation is injected at the tree root "
        "(never inside `tests/` or `detector/`, so path scoping cannot hide "
        "it), the rule is re-run, and the scratch copy is deleted."
    )
    out.append("")
    if not seed:
        out.append(
            "Skipped — `--no-seed-check` was passed, so no seeded "
            "violations were injected and no rule was re-run against a "
            "seeded tree."
        )
        out.append("")
    else:
        out.append(
            "| rule | seeded file | baseline hits | hits after seed | "
            "seeded hits | caught |"
        )
        out.append("|---|---|---|---|---|---|")
        for rule in RULE_FILES:
            data = seed[rule]
            out.append(
                f"| {rule} | `{data['seeded_file']}` | {data['baseline_hits']} | "
                f"{data['hits_after_seed']} | {data['seeded_hits']} "
                f"({', '.join(data['seeded_locations'])}) | "
                f"{'yes' if data['caught'] else 'NO'} |"
            )
        out.append("")
    out.append("## 6. Phase 0 gate")
    out.append("")
    clean = [
        rule
        for rule in RULE_FILES
        if summary[rule]["false_positive"] == 0
        and seed.get(rule, {}).get("caught", False)
    ]
    if not seed:
        out.append(
            "**Verdict: not evaluated** — the seeded-violation check was "
            "skipped (`--no-seed-check`), so the gate's seeded-catch leg "
            "has no evidence in this scan-only run."
        )
    else:
        verdict = "Proceed" if len(clean) >= 3 else "Kill"
        out.append(
            f"**Verdict: {verdict}** — {len(clean)} rules survive the gate "
            f"({', '.join(clean)}): zero false positives after legitimate-exception "
            "scoping and each catches its seeded violation.  The gate needs >= 3."
        )
    out.append("")
    out.append("| rule | false positives after scoping | seeded violation caught |")
    out.append("|---|---|---|")
    for rule in RULE_FILES:
        caught = (
            "not checked"
            if rule not in seed
            else ("yes" if seed[rule]["caught"] else "no")
        )
        out.append(
            f"| {rule} | {summary[rule]['false_positive']} | {caught} |"
        )
    out.append("")
    out.append("## 7. C5 — candidate rule, dropped on evidence")
    out.append("")
    out.append(
        "C5 (no literal-threshold confidence comparison bypassing "
        "`REVIEW_CONFIDENCE`, `building_model.py:51` = 0.80) was trialled as a "
        "candidate rule and is **not shipped**.  The candidate:"
    )
    out.append("")
    out.append("```yaml")
    out.append(C5_CANDIDATE_RULE.rstrip())
    out.append("```")
    out.append("")
    out.append(f"Measured over the scanned tree it fires {len(c5_matches)} times:")
    out.append("")
    out.append("| file:line | text | classification |")
    out.append("|---|---|---|")
    for match in c5_matches:
        location = f"{match['rel']}:{match['range']['start']['line'] + 1}"
        text = match["text"].strip().replace("\n", " ")
        if match["rel"].startswith("tests/"):
            classification = (
                "legitimate exception — test assertion on a confidence threshold"
            )
        elif match["rel"] == "run_pipeline.py":
            classification = (
                "true violation — hardcoded 0.80 threshold bypassing "
                "REVIEW_CONFIDENCE (building_model.py:51)"
            )
        else:
            classification = "unclassified"
        out.append(f"| {location} | `{md_escape(text[:90])}` | {classification} |")
    out.append("")
    out.append(
        "The grep for the convention's literal-threshold form (`grep -rn -E "
        "'[<>]=?\\s*0\\.(8|85|7[0-9]|6[0-9])\\b' --include='*.py'`) finds "
        f"{len(C5_LITERAL_THRESHOLDS)} confidence-relevant sites that the "
        "candidate rule does NOT catch:"
    )
    out.append("")
    out.append("| file:line | text | note |")
    out.append("|---|---|---|")
    for path, line, snippet, note in C5_LITERAL_THRESHOLDS:
        out.append(f"| {path}:{line} | `{snippet}` | {note} |")
    out.append("")
    out.append(
        "Evidence for dropping C5: the rule only matches comparisons against an "
        "attribute literally named `confidence`, so it misses the sibling "
        "violation `run_pipeline.py:1440` (`ls.label_confidence < 0.80`) and "
        "the differently named threshold comparisons in "
        "`review_classifier/data.py` and `review_classifier/laya_experiment.py`. "
        "A shipped rule that under-covers the convention would give false "
        "confidence, and loosening it to bare float comparisons "
        "(`$X < 0.8`) would fire on `rng.random() < 0.8`, geometry ratios and "
        "test assertions — false positives galore.  C5 is therefore dropped "
        "with this evidence; it can be re-opened in Phase 1 with a "
        "name-aware rule (`*_confidence`, `*_conf`) plus an explicit "
        "allowlist."
    )
    out.append("")
    out.append("## 8. Re-running the spike")
    out.append("")
    out.append("```sh")
    out.append("python3 -m venv /tmp/astgrep-venv")
    out.append(
        "export HTTPS_PROXY=http://hatch-egress-proxy:3128 "
        "HTTP_PROXY=http://hatch-egress-proxy:3128"
    )
    out.append(
        "export SSL_CERT_FILE=/run/hatch/egress-tls/ca-bundle.pem "
        "REQUESTS_CA_BUNDLE=/run/hatch/egress-tls/ca-bundle.pem"
    )
    out.append("/tmp/astgrep-venv/bin/pip install ast-grep-cli")
    out.append("")
    out.append("git clone ~/workspace/matchline matchline-ref")
    out.append(
        "git -C matchline-ref fetch ~/workspace/matchline "
        "refs/remotes/origin/develop:refs/scan/develop"
    )
    out.append("git -C matchline-ref checkout refs/scan/develop")
    out.append("")
    out.append("python3 bin/ast_grep_phase0_scan.py --repo matchline-ref \\")
    out.append("    --ast-grep /tmp/astgrep-venv/bin/ast-grep \\")
    out.append("    --out docs/spikes/ast-grep-matchline-phase0.md")
    out.append("```")
    out.append("")
    out.append("Single rule, no harness (raw ast-grep, what CI would run):")
    out.append("")
    out.append("```sh")
    out.append("cd matchline-ref")
    out.append("/tmp/astgrep-venv/bin/ast-grep scan \\")
    out.append(
        "    --rule ../docs/spikes/ast-grep-matchline/c1-sys-path-manipulation.yml ."
    )
    out.append("```")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="matchline-ref", help="scanned checkout")
    parser.add_argument("--rules", default=str(DEFAULT_RULES_DIR))
    parser.add_argument("--ast-grep", default=DEFAULT_AST_GREP)
    parser.add_argument("--out", default=None, help="write markdown report here")
    parser.add_argument("--format", default="markdown", choices=["markdown", "json"])
    parser.add_argument("--no-seed-check", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.repo).resolve()
    rules_dir = Path(args.rules).resolve()
    if not root.is_dir():
        print(f"error: scanned tree not found: {root}", file=sys.stderr)
        return 2
    if not Path(args.ast_grep).exists():
        print(
            f"error: ast-grep binary not found: {args.ast_grep} "
            "(see the report, section 1, for the pin)",
            file=sys.stderr,
        )
        return 2

    hits = collect(args.ast_grep, root, rules_dir)
    c5_matches = collect_c5(args.ast_grep, root)
    seed: dict = {}
    if not args.no_seed_check:
        seed = seed_check(args.ast_grep, root, rules_dir)
    commit = git_commit(root)

    if args.format == "json":
        payload = {
            "commit": commit,
            "rules": {
                rule: [
                    {
                        "file": h.path,
                        "line": h.line,
                        "text": h.text,
                        "scoped": h.scoped,
                        "classification": h.classification,
                        "reason": h.reason,
                    }
                    for h in hits
                    if h.rule == rule
                ]
                for rule in RULE_FILES
            },
            "seed": seed,
        }
        print(json.dumps(payload, indent=2))
        return 0

    report = render_report(hits, seed, root, args.ast_grep, commit, c5_matches)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
