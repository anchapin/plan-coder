#!/usr/bin/env python3
"""Phase-0 schema + conformance census of plan-coder's emitted artifacts
(issue #147, Part F3).

Phase 0 is measurement only: no schema is built, no artifact format is
changed, no validator is added.  This script answers one question and
prints the evidence for it:

    For every artifact family the harness emits, is there a
    machine-readable schema today, and does some test / validator
    actually check emitted instances against it?

Two halves:

  1. CENSUS -- an AST scan over ``bin/*.py`` that enumerates every
     place the harness *emits* an artifact (file writes, JSON dumps,
     JSONL appends, stdout JSON).  The patterns used are named in the
     output so the census is reproducible:

       P1  open(path, "w"|"a"|"x"|...)      file write / ledger append
       P2  Path.write_text(...)              file write
       P3  json.dump(obj, fh)                JSON to a file handle
       P4  json.dumps(obj)                   JSON serialisation
       P5  fh.write(json.dumps(...))         JSONL row append
       P6  os.fdopen(fd, "w") + os.replace   atomic write helper
       P7  print(json.dumps(...))            stdout JSON document

     The scan is bounded to ``bin/*.py`` at the repo root (the tools
     directory).  It does not scan ``adapters/``, ``prompts/`` or
     ``docs/`` -- prompt-driven artifacts are inventoried separately and
     cite their prompt line.

  2. INVENTORY -- one row per artifact family.  Every row cites the
     producing code as ``file:line`` (found by grepping the file for the
     row's evidence string), the schema (path, or ``none`` -- a prose
     description does not count), the conformance check (a named test or
     validator, or ``none``), and one committed example instance if the
     repo has one.

Every claim in the inventory is verified by this script before it is
printed: the producer's evidence string must exist, a declared schema
file must exist and parse (and carry a ``$schema``), a declared code
schema must name a function that exists, a declared conformance check
must name a test that exists in the repo, and a declared example must
exist on disk.  A row that fails its own verification is printed with
the failure, never silently.

Usage:
    python3 bin/schema_inventory_phase0.py            # markdown report
    python3 bin/schema_inventory_phase0.py --json     # machine payload
    python3 bin/schema_inventory_phase0.py --verify    # fail on any
                                                        # broken claim

Exit 0 on success; exit 1 when --verify finds a broken claim.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = "bin/schema_inventory_phase0.py"
SELF_TEST = "tests/test_schema_inventory_phase0.py"

CENSUS_PATTERNS = [
    ("P1", "open(path, mode) with mode in w/a/x/+",
     "file write / ledger append"),
    ("P2", "Path(...).write_text(...)",
     "file write"),
    ("P3", "json.dump(obj, fh)",
     "JSON document to a file handle"),
    ("P4", "json.dumps(obj)",
     "JSON serialisation"),
    ("P5", "fh.write(json.dumps(...))",
     "JSONL row append"),
    ("P6", "os.fdopen(fd, \"w\") / os.replace(...)",
     "atomic write helper"),
    ("P7", "print(json.dumps(...))",
     "stdout JSON document"),
]

WRITE_KINDS = {"file-open-write", "file-open-append", "path-write-text",
               "json-dump", "json-dumps", "jsonl-append", "fdopen-write",
               "atomic-replace", "stdout-json"}


def _enclosing_function(tree: ast.AST):
    """Map every node id to the name of the function that contains it."""
    owner: dict[int, str] = {}
    stack: list[str] = []

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # noqa: N802 - ast API
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

        def visit_Lambda(self, node):  # noqa: N802 - ast API
            stack.append("<lambda>")
            self.generic_visit(node)
            stack.pop()

        def generic_visit(self, node):
            if stack:
                owner[id(node)] = stack[-1]
            super().generic_visit(node)

    V().visit(tree)
    return owner


def _is_json_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr in ("dump", "dumps")
            and isinstance(node.value, ast.Name)
            and node.value.id == "json")


def _mode_of(call: ast.Call) -> str:
    """The literal mode argument of an open(...) call, if any."""
    for arg in list(call.args[1:2]) + [kw.value for kw in call.keywords
                                        if kw.arg == "mode"]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return ""


def _contains_json_call(node: ast.AST) -> bool:
    """True when the expression contains a json.dump/json.dumps call."""
    for child in ast.walk(node):
        if _is_json_call(child):
            return True
    return False


def scan_file(path: str) -> list[dict]:
    """Every emission site in one python file."""
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    owner = _enclosing_function(tree)
    lines = source.splitlines()
    sites: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        kind = None
        if isinstance(func, ast.Name) and func.id == "open":
            mode = _mode_of(node)
            if any(ch in mode for ch in "wax+"):
                kind = ("file-open-append" if "a" in mode
                        else "file-open-write")
        elif isinstance(func, ast.Attribute) and func.attr == "write_text":
            kind = "path-write-text"
        elif _is_json_call(func):
            kind = "json-dump" if func.attr == "dump" else "json-dumps"
        elif isinstance(func, ast.Attribute) and func.attr == "write":
            if any(_contains_json_call(a) for a in node.args):
                kind = "jsonl-append"
            else:
                kind = "write-call"
        elif isinstance(func, ast.Attribute) and func.attr == "fdopen":
            kind = "fdopen-write"
        elif isinstance(func, ast.Attribute) and func.attr == "replace":
            kind = "atomic-replace"
        if kind is None:
            continue
        if kind == "write-call":
            continue
        snippet = lines[node.lineno - 1].strip()[:110]
        sites.append({
            "file": os.path.relpath(path, REPO_ROOT),
            "line": node.lineno,
            "function": owner.get(id(node), "<module>"),
            "kind": kind,
            "snippet": snippet,
        })
    return sites


def scan_census(root: str = REPO_ROOT) -> dict:
    """Scan bin/*.py for emission sites (the census)."""
    bin_dir = os.path.join(root, "bin")
    files = sorted(f for f in os.listdir(bin_dir)
                   if f.endswith(".py") and f != os.path.basename(SELF))
    sites: list[dict] = []
    for name in files:
        sites.extend(scan_file(os.path.join(bin_dir, name)))
    by_kind: dict[str, int] = {}
    for site in sites:
        by_kind[site["kind"]] = by_kind.get(site["kind"], 0) + 1
    return {
        "scope": "bin/*.py (tools directory), excluding this census script",
        "patterns": [{"id": pid, "pattern": pat, "catches": what}
                     for pid, pat, what in CENSUS_PATTERNS],
        "files_scanned": len(files),
        "write_sites": sites,
        "by_kind": dict(sorted(by_kind.items())),
    }


def sites_in_function(census: dict, rel_file: str, function: str) -> list[dict]:
    return [s for s in census["write_sites"]
            if s["file"] == rel_file and s["function"] == function]


INVENTORY = [
    {
        "artifact": "worker_ledger.jsonl row (metrics ledger)",
        "producer_file": "prompts/backlog-supervisor.md",
        "producer_evidence": "append one JSON line per collected worker to the metrics ledger",
        "format": "JSONL, one JSON object per collected worker",
        "schema_kind": "json-schema",
        "schema_ref": "schemas/ledger-line.schema.json",
        "schema_note": "the row's field set is also spelled out in the prompt",
        "conformance_test": "tests/test_plan_coder_cli.py",
        "conformance_name": "test_ledger_lines_match_the_ledger_line_schema",
        "example": "examples/worker_ledger.example.jsonl",
    },
    {
        "artifact": "receipts.jsonl row (proof receipt)",
        "producer_file": "bin/receipt.py",
        "producer_evidence": "def append_row(ledger_path: str, row: dict) -> dict:",
        "format": "JSONL, one JSON object per PR proof receipt",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "the row shape is described in the docstring only -- prose, not a schema",
        "conformance_test": "tests/test_harness.py",
        "conformance_name": "test_record_and_summary_report_coverage_over_window",
        "example": "examples/receipts.example.jsonl",
    },
    {
        "artifact": "shepherd_ledger.jsonl row (shepherd action ledger)",
        "producer_file": "bin/shepherd_ledger.py",
        "producer_evidence": "def append_row(ledger_path: str, row: dict) -> dict:",
        "format": "JSONL, one JSON object per shepherd action",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "field set documented in the module docstring -- prose, not a schema",
        "conformance_test": "tests/test_shepherd_ledger.py",
        "conformance_name": "test_all_new_arguments_land_with_the_right_shape",
        "example": "",
    },
    {
        "artifact": "verification_ledger.jsonl row (verification stamp)",
        "producer_file": "bin/verify_stamp.py",
        "producer_evidence": "def build_stamp(run_id: str",
        "format": "JSONL, one JSON object per stamped run",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "stamp fields listed in the docstring -- prose, not a schema",
        "conformance_test": "tests/test_verify_stamp.py",
        "conformance_name": "test_stamp_row_shape_and_collection_separation",
        "example": "",
    },
    {
        "artifact": "goldfish_ledger.jsonl row (brief-review ledger)",
        "producer_file": "bin/goldfish.py",
        "producer_evidence": "def ledger_row(policy: dict, args) -> dict:",
        "format": "JSONL, one JSON object per brief review",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema file, no validator for the row shape",
        "conformance_test": "tests/test_harness.py",
        "conformance_name": "test_log_appends_goldfish_ledger_row",
        "example": "",
    },
    {
        "artifact": "jittest_ledger.jsonl row (hypothesis-test ledger)",
        "producer_file": "bin/jittest.py",
        "producer_evidence": "def ledger_row(policy: dict, args) -> dict:",
        "format": "JSONL, one JSON object per JiT run",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema file, no validator for the row shape",
        "conformance_test": "tests/test_harness.py",
        "conformance_name": "test_log_appends_jittest_ledger_row_with_adjudication",
        "example": "",
    },
    {
        "artifact": "edd_experiments.jsonl row (tune-bundle experiment)",
        "producer_file": "bin/edd.py",
        "producer_evidence": '"edit_summary": args.summary',
        "format": "JSONL, one JSON object per edit bundle",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "the row's keys are listed in the docstring -- prose, not a schema",
        "conformance_test": "tests/test_edd.py",
        "conformance_name": "test_two_bundles_logged_in_the_same_second_get_distinct_cuts",
        "example": "",
    },
    {
        "artifact": "receipt_capture/<run_id>.json (EDD capture record)",
        "producer_file": "bin/edd.py",
        "producer_evidence": 'record["harness"] = fp',
        "format": "JSON document, one per run",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema; the record's keys are produced ad hoc",
        "conformance_test": "",
        "conformance_name": "",
        "example": "",
    },
    {
        "artifact": "EDD harness fingerprint (stdout JSON document)",
        "producer_file": "bin/edd.py",
        "producer_evidence": "def compute_fingerprint(cfg: dict) -> dict:",
        "format": "JSON document on stdout",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema file for the fingerprint object",
        "conformance_test": "",
        "conformance_name": "",
        "example": "",
    },
    {
        "artifact": "experiments.jsonl row (experiment registry)",
        "producer_file": "bin/experiments.py",
        "producer_evidence": "def append_row(path: str, row: dict) -> dict:",
        "format": "JSONL, one JSON object per experiment",
        "schema_kind": "code-schema",
        "schema_ref": "bin/experiments.py:validate_row",
        "schema_note": "METRICS.md section 6 schema enforced in code by validate_row()",
        "conformance_test": "tests/test_experiments.py",
        "conformance_name": "test_add_appends_a_row_that_validates",
        "example": "examples/experiments.example.jsonl",
    },
    {
        "artifact": "workers.json (cycle state: workers)",
        "producer_file": "prompts/backlog-supervisor.md",
        "producer_evidence": "save workers.json",
        "format": "JSON document (written through bin/state_write.py commit)",
        "schema_kind": "json-schema",
        "schema_ref": "schemas/workers.schema.json",
        "schema_note": "normalised record as the manager CLI reports it",
        "conformance_test": "tests/test_plan_coder_cli.py",
        "conformance_name": "test_status_json_matches_the_schemas",
        "example": "examples/workers.example.json",
    },
    {
        "artifact": "shepherd.json (cycle state: tracked PRs)",
        "producer_file": "prompts/pr-shepherd.md",
        "producer_evidence": "shepherd.json",
        "format": "JSON document (written through bin/state_write.py commit)",
        "schema_kind": "json-schema",
        "schema_ref": "schemas/shepherd.schema.json",
        "schema_note": "normalised PR record as the manager CLI reports it",
        "conformance_test": "tests/test_plan_coder_cli.py",
        "conformance_name": "test_status_json_matches_the_schemas",
        "example": "",
    },
    {
        "artifact": "next-actions JSON (manager CLI next-moves list)",
        "producer_file": "bin/plan_coder.py",
        "producer_evidence": "def build_next_actions(state_dir: str, config: dict, state: dict,",
        "format": "JSON document on stdout",
        "schema_kind": "json-schema",
        "schema_ref": "schemas/next-actions.schema.json",
        "schema_note": "",
        "conformance_test": "tests/test_plan_coder_cli.py",
        "conformance_name": "test_next_actions_matches_the_schema",
        "example": "",
    },
    {
        "artifact": "WORKER_REPORT.md machine manifest",
        "producer_file": "bin/agent.py",
        "producer_evidence": "def _machine_manifest(workdir: str, base: str | None, gate_triggers: int,",
        "format": "markdown bullet block inside WORKER_REPORT.md",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema; the manifest's keys are prose bullets",
        "conformance_test": "tests/test_harness.py",
        "conformance_name": "test_claim_vs_diff_match_and_mismatch",
        "example": "",
    },
    {
        "artifact": "worker transcript JSONL (<run_id>.jsonl)",
        "producer_file": "bin/agent.py",
        "producer_evidence": '_tlog = open(transcript_path, "w", encoding="utf-8")',
        "format": "JSONL, one event object per step",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema for the event objects",
        "conformance_test": "tests/test_harness.py",
        "conformance_name": "test_good_run_passes",
        "example": "",
    },
    {
        "artifact": "cron-manifest.json (rendered cron bodies)",
        "producer_file": "bin/install_crons.py",
        "producer_evidence": '"cron-manifest.json"',
        "format": "JSON document",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema for the manifest object",
        "conformance_test": "tests/test_harness.py",
        "conformance_name": "test_installer_manifest_full",
        "example": "",
    },
    {
        "artifact": "dashboard HTML page (live view + metrics)",
        "producer_file": "bin/dashboard.py",
        "producer_evidence": "def render_html(live: dict, metrics: dict, generated_at: str,",
        "format": "self-contained HTML",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "not a data artifact -- no schema applies",
        "conformance_test": "tests/test_dashboard.py",
        "conformance_name": "test_renders_both_panels",
        "example": "",
    },
    {
        "artifact": "flag_batch_ledger.jsonl row (flag-batch counts)",
        "producer_file": "bin/flagbatch.py",
        "producer_evidence": '"flag_batch_ledger.jsonl"',
        "format": "JSONL, one JSON object per generated batch",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema for the counts row",
        "conformance_test": "tests/test_flagbatch.py",
        "conformance_name": "test_generate_enabled_writes_view_and_counts",
        "example": "",
    },
    {
        "artifact": "metrics_rollup.jsonl row (PR-lifecycle + daily rows)",
        "producer_file": "bin/metrics_rollup.py",
        "producer_evidence": "fh.writelines(json.dumps(row",
        "format": "JSONL, one JSON object per PR and per day",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema for the rollup rows",
        "conformance_test": "tests/test_metrics_rollup.py",
        "conformance_name": "test_exit_zero_and_rollup_file_written",
        "example": "",
    },
    {
        "artifact": "grade_run verdict (trajectory grade JSON)",
        "producer_file": "bin/grade_run.py",
        "producer_evidence": "def grade(events: list, ledger_row: dict | None = None,",
        "format": "JSON document on stdout",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema for the grade/verdict object",
        "conformance_test": "tests/test_harness.py",
        "conformance_name": "test_good_run_passes",
        "example": "",
    },
    {
        "artifact": "worktree_slots.json (cycle state: persisted worktrees)",
        "producer_file": "bin/worktree_slots.py",
        "producer_evidence": "def _save_state(path: str, state: dict) -> None:",
        "format": "JSON document (staged, committed via bin/state_write.py)",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema for the slots object",
        "conformance_test": "tests/test_worktree_slots.py",
        "conformance_name": "test_record_lookup_roundtrip",
        "example": "",
    },
    {
        "artifact": "judging_files verdict (per-PR touch classification)",
        "producer_file": "bin/judging_files.py",
        "producer_evidence": "def classify_pr(changed: list[tuple[str, str]],",
        "format": "JSON document on stdout",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema for the verdict object",
        "conformance_test": "tests/test_judging_files.py",
        "conformance_name": "test_classes",
        "example": "",
    },
    {
        "artifact": "Phase-0 spike run payload (docs/spikes/*.json)",
        "producer_file": "bin/delete_comment_phase0.py",
        "producer_evidence": "json.dump(ledger, handle",
        "format": "JSON document (one per spike run)",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "spike payloads are ad hoc; no schema file",
        "conformance_test": "tests/test_delete_comment_phase0.py",
        "conformance_name": "test_cli_json_output_is_stable",
        "example": "docs/spikes/delete-comment-phase0-runs.json",
    },
    {
        "artifact": "factory-failure ticket (GitHub issue body)",
        "producer_file": "bin/factory_failure_tickets.py",
        "producer_evidence": "def build_body(record: dict, fp: str) -> str:",
        "format": "markdown issue body filed on GitHub",
        "schema_kind": "none",
        "schema_ref": "",
        "schema_note": "no schema; the body's fields are template prose",
        "conformance_test": "tests/test_factory_failure_tickets.py",
        "conformance_name": "test_issue_body_carries_the_required_fields",
        "example": "",
    },
]

NOT_FOUND = [
    {
        "name": "claimlink ledger rows",
        "grep": "claimlink|claim_link",
        "scope": "bin/, prompts/, docs/, schemas/, tests/",
        "note": "no producer, no reader and no test anywhere in the repo",
    },
    {
        "name": "flight-deck inputs",
        "grep": "flight[-_ ]?deck",
        "scope": "bin/, prompts/, docs/, schemas/, tests/",
        "expected_path": "bin/flightdeck.py",
        "note": "no producer in bin/ today: docs/METRICS.md still names "
                "bin/flightdeck.py, which does not exist in this tree (the "
                "live view is bin/dashboard.py), so 'in flight' is prose "
                "about workers, not an artifact family",
    },
    {
        "name": "dispatch record (a distinct artifact)",
        "grep": r"dispatch[_-]?(record|ledger|jsonl|log)",
        "scope": "bin/, prompts/, schemas/, docs/",
        "note": "a dispatch leaves no artifact of its own: its evidence is the "
                "workers.json entry and the worker_ledger.jsonl row (both "
                "inventoried above); 'dispatch' elsewhere is prose or code, "
                "not an artifact family",
    },
]


def _find_evidence(root: str, rel_file: str, evidence: str) -> int:
    path = os.path.join(root, rel_file)
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for number, line in enumerate(fh, 1):
            if evidence in line:
                return number
    return 0


def _function_of_line(root: str, rel_file: str, line: int) -> str:
    path = os.path.join(root, rel_file)
    if not rel_file.endswith(".py") or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    best = ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= line <= end:
                best = node.name
    return best


def _test_exists(root: str, rel_file: str, name: str) -> bool:
    path = os.path.join(root, rel_file)
    if not name or not os.path.exists(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return (f"def {name}(" in text) or (f"class {name}(" in text)


def build_rows(root: str = REPO_ROOT, census: dict | None = None) -> list[dict]:
    """Verify every inventory claim and return the rows."""
    if census is None:
        census = scan_census(root)
    rows = []
    for entry in INVENTORY:
        problems: list[str] = []
        rel = entry["producer_file"]
        line = _find_evidence(root, rel, entry["producer_evidence"])
        if not line:
            problems.append("producer evidence string not found in "
                            f"{rel}: {entry['producer_evidence']!r}")
        function = _function_of_line(root, rel, line) if line else ""
        linked = sites_in_function(census, rel, function) if function else []

        schema_kind = entry["schema_kind"]
        schema_ref = entry["schema_ref"]
        schema_ok = False
        schema_path = ""
        if schema_kind == "json-schema":
            schema_path = schema_ref
            full = os.path.join(root, schema_ref)
            if not os.path.exists(full):
                problems.append(f"schema file missing: {schema_ref}")
            else:
                try:
                    with open(full, encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, json.JSONDecodeError) as exc:
                    problems.append(f"schema file unreadable: {schema_ref} ({exc})")
                else:
                    if "$schema" not in data:
                        problems.append(f"schema file has no $schema: {schema_ref}")
                    else:
                        schema_ok = True
        elif schema_kind == "code-schema":
            schema_path = schema_ref
            file_part, _, func = schema_ref.partition(":")
            full = os.path.join(root, file_part)
            if not os.path.exists(full):
                problems.append(f"schema file missing: {file_part}")
            else:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                if f"def {func}(" not in text:
                    problems.append(f"code schema function missing: {schema_ref}")
                else:
                    schema_ok = True
        else:
            schema_ok = False

        conformance_test = entry["conformance_test"]
        conformance_name = entry["conformance_name"]
        conformance_ok = False
        if conformance_test:
            if not os.path.exists(os.path.join(root, conformance_test)):
                problems.append(f"conformance test file missing: {conformance_test}")
            elif not _test_exists(root, conformance_test, conformance_name):
                problems.append(f"conformance test missing: "
                                f"{conformance_test}::{conformance_name}")
            else:
                conformance_ok = True

        example = entry["example"]
        example_ok = True
        if example and not os.path.exists(os.path.join(root, example)):
            problems.append(f"example instance missing: {example}")
            example_ok = False

        rows.append({
            "artifact": entry["artifact"],
            "producer": f"{rel}:{line}" if line else f"{rel}:NOT FOUND",
            "producer_evidence": entry["producer_evidence"],
            "producer_function": function,
            "census_write_sites": [f"{s['file']}:{s['line']} ({s['kind']})"
                                   for s in linked],
            "format": entry["format"],
            "schema": schema_path if schema_ok else "none",
            "schema_kind": schema_kind,
            "schema_note": entry["schema_note"],
            "conformance": (f"{conformance_test}::{conformance_name}"
                            if conformance_ok else "none"),
            "conformance_ok": conformance_ok,
            "schema_ok": schema_ok,
            "example": example if example_ok else "none",
            "covered": bool(schema_ok and conformance_ok),
            "problems": problems,
        })
    return rows


def coverage(rows: list[dict]) -> dict:
    total = len(rows)
    with_schema = sum(1 for r in rows if r["schema_ok"])
    with_check = sum(1 for r in rows if r["conformance_ok"])
    covered = sum(1 for r in rows if r["covered"])
    pct = (100.0 * covered / total) if total else 0.0
    return {
        "artifacts": total,
        "with_schema": with_schema,
        "with_conformance_check": with_check,
        "with_both": covered,
        "coverage_pct": round(pct, 1),
        "schema_pct": round(100.0 * with_schema / total, 1) if total else 0.0,
        "conformance_pct": round(100.0 * with_check / total, 1) if total else 0.0,
    }


def read_decision(rows: list[dict], cov: dict) -> str:
    """The Kill / Proceed read for F3 (kill when coverage is complete)."""
    if cov["coverage_pct"] >= 90.0:
        return ("KILL F3: coverage is effectively complete "
                f"({cov['with_both']}/{cov['artifacts']} artifacts have both a "
                "schema and a conformance check) -- there is nothing left for "
                "F3 to build.")
    return ("PROCEED F3: coverage is NOT effectively complete "
            f"({cov['with_both']}/{cov['artifacts']} artifacts have both a "
            f"schema and a conformance check = {cov['coverage_pct']}%), so "
            "there is real gap work for a later phase; Phase 0 changed "
            "nothing.")


def not_found_rows(root: str = REPO_ROOT) -> list[dict]:
    out = []
    for item in NOT_FOUND:
        hits = 0
        for scope in item["scope"].split(", "):
            base = os.path.join(root, scope.rstrip("/"))
            if not os.path.isdir(base):
                continue
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    if not name.endswith((".py", ".md", ".json", ".jsonl")):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, name), root)
                    if rel in (SELF, SELF_TEST):   # never count our own files
                        continue
                    try:
                        with open(os.path.join(dirpath, name), encoding="utf-8",
                                  errors="replace") as fh:
                            text = fh.read()
                    except OSError:
                        text = ""
                    hits += len(re.findall(item["grep"], text))
        row = {"name": item["name"], "grep": item["grep"],
               "scope": item["scope"], "hits": hits, "note": item["note"]}
        expected = item.get("expected_path")
        if expected:
            row["expected_path"] = expected
            row["expected_path_exists"] = os.path.exists(
                os.path.join(root, expected))
        out.append(row)
    return out


def render_markdown(payload: dict) -> str:
    rows = payload["inventory"]
    cov = payload["coverage"]
    out = []
    out.append("# Emitted-artifact schema & conformance census (Phase 0)\n")
    out.append("| artifact | producer (file:line) | format | schema | "
               "conformance check | example instance |")
    out.append("|---|---|---|---|---|---|")
    for row in rows:
        out.append("| {artifact} | `{producer}` | {format} | {schema} | "
                   "{conformance} | {example} |".format(
                       artifact=row["artifact"],
                       producer=row["producer"],
                       format=row["format"],
                       schema=row["schema"],
                       conformance=row["conformance"],
                       example=row["example"]))
    out.append("")
    out.append(f"Coverage: **{cov['with_both']}/{cov['artifacts']} = "
               f"{cov['coverage_pct']}%** of inventoried artifacts have BOTH a "
               f"schema and a conformance check "
               f"(schema: {cov['with_schema']}/{cov['artifacts']} = "
               f"{cov['schema_pct']}%; conformance check: "
               f"{cov['with_conformance_check']}/{cov['artifacts']} = "
               f"{cov['conformance_pct']}%).")
    out.append("")
    out.append(payload["decision"])
    out.append("")
    out.append("Census: {files} files scanned under {scope}; {sites} emission "
               "sites found.".format(files=payload["census"]["files_scanned"],
                                     scope=payload["census"]["scope"],
                                     sites=len(payload["census"]["write_sites"])))
    for pattern in payload["census"]["patterns"]:
        out.append(f"  - {pattern['id']}: `{pattern['pattern']}` -> "
                   f"{pattern['catches']}")
    out.append("")
    out.append("Looked for and NOT found:")
    for item in payload["not_found"]:
        extra = ""
        if item.get("expected_path"):
            extra = (" (expected path {p}: {state})".format(
                p=item["expected_path"],
                state="present" if item.get("expected_path_exists")
                else "ABSENT from the repo"))
        out.append(f"  - {item['name']} (grep `{item['grep']}` over "
                   f"{item['scope']}: {item['hits']} hits){extra} -- "
                   f"{item['note']}")
    return "\n".join(out) + "\n"


def build_payload(root: str = REPO_ROOT) -> dict:
    census = scan_census(root)
    rows = build_rows(root, census)
    cov = coverage(rows)
    return {
        "issue": "#147 Part F3, Phase 0 (measurement only)",
        "census": census,
        "inventory": rows,
        "coverage": cov,
        "decision": read_decision(rows, cov),
        "not_found": not_found_rows(root),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="emit the machine payload as JSON on stdout")
    ap.add_argument("--root", default=REPO_ROOT,
                    help="repo root to scan (default: the repo this file is in)")
    ap.add_argument("--verify", action="store_true",
                    help="exit 1 when any inventory claim fails its own check")
    args = ap.parse_args(argv)

    payload = build_payload(args.root)
    if args.json:
        print(json.dumps(payload, indent=1, sort_keys=False))
    else:
        print(render_markdown(payload), end="")

    broken = [r for r in payload["inventory"] if r["problems"]]
    if broken:
        print("BROKEN INVENTORY CLAIMS:", file=sys.stderr)
        for row in broken:
            for problem in row["problems"]:
                print(f"  {row['artifact']}: {problem}", file=sys.stderr)
        if args.verify:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
