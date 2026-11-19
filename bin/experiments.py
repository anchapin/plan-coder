#!/usr/bin/env python3
"""The experiment registry for the harness optimization loop.

Phase 2 of docs/METRICS.md (issue #132): the registry the loop writes
instead of changing the harness anonymously.  One row per change, in
`experiments.jsonl` in the state dir, with the schema of METRICS.md
section 6:

    id                the experiment's id ("001", "002", ...)
    date              the day the experiment was opened (YYYY-MM-DD)
    hypothesis        one sentence: the primary metric, expected
                      direction and expected size
    change            what was edited (prompt file, routing rule,
                      config knob) with the commit/PR link
    window            {"start": YYYY-MM-DD, "min_sample": N,
                      "min_weeks": N} -- the measurement window and its
                      minimum sample (section 6: >=20 runs or >=2
                      weeks, whichever is later)
    primary_metric    exactly one metric this experiment is judged on
    guardrails[]      the metrics that must not regress (section 4)
    verdict           keep | revert | inconclusive | in-progress
                      ("pending" is accepted as a synonym of
                      "in-progress" and normalized to it on write)
    verdict_evidence  the stratified numbers behind the verdict

The tool is deliberately mechanical.  It creates the registry, appends
one row, lists the rows, validates a registry file against the section
6 schema, and records a verdict on an existing row.  It never touches
targets, slot counts, merge policy, or any prompt: the optimization
loop proposes, the human decides.

Compatibility: `bin/dashboard.py` (Panel 5) already reads this file.
Its reader falls back from `ts` to `date` for the timestamp and from
`name` to `id` for the label, so a row written by this tool renders
without any dashboard change.

Usage:
    python3 bin/experiments.py init   [--state-dir DIR] [--registry PATH]
    python3 bin/experiments.py add    --id ID --hypothesis TEXT --change TEXT
                                     --window-start YYYY-MM-DD
                                     --primary-metric METRIC
                                     --guardrail "GUARDRAIL" [--guardrail ...]
                                     [--verdict in-progress] [--date YYYY-MM-DD]
                                     [--name NAME] [--min-sample 20]
                                     [--min-weeks 2] [--verdict-evidence TEXT]
    python3 bin/experiments.py close  --id ID --verdict keep|revert|inconclusive
                                     --verdict-evidence TEXT
    python3 bin/experiments.py list   [--verdict V] [--json]
    python3 bin/experiments.py validate [--json]

--registry overrides the path; otherwise the registry is
`<state dir>/experiments.jsonl`, where the state dir comes from
--state-dir or from the deployment config (same search order as
bin/trace_summary.py).  Exit 0 on success; exit 1 when a registry
fails validation or a row is rejected; exit 2 when a file cannot be
read or written.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import default_ledger_path

REGISTRY_FILE = "experiments.jsonl"

# METRICS.md section 6: the verdict vocabulary.  "in-progress" is the
# explicit state of an experiment whose window has not closed yet;
# "pending" is accepted as a synonym and normalized to it on write.
VERDICTS = ("keep", "revert", "inconclusive", "in-progress")
VERDICT_ALIASES = {"pending": "in-progress", "in_progress": "in-progress",
                   "inprogress": "in-progress", "open": "in-progress"}
CLOSED_VERDICTS = ("keep", "revert", "inconclusive")

REQUIRED_FIELDS = ("id", "date", "hypothesis", "change", "window",
                   "primary_metric", "guardrails", "verdict",
                   "verdict_evidence")

# Section 6's minimum sample: >=20 runs or >=2 weeks, whichever is
# later.  These are the defaults the tool writes into a new row.
DEFAULT_MIN_SAMPLE = 20
DEFAULT_MIN_WEEKS = 2


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def default_state_dir() -> str | None:
    """The state dir the deployment config points at (may be None)."""
    ledger = default_ledger_path()
    if not ledger:
        return None
    return os.path.dirname(os.path.abspath(os.path.expanduser(ledger)))


def registry_path(state_dir: str | None = None,
                  registry: str | None = None) -> str | None:
    """Resolve the registry path: explicit > state dir > deployment config."""
    if registry:
        return os.path.abspath(os.path.expanduser(registry))
    if state_dir:
        return os.path.join(os.path.abspath(os.path.expanduser(state_dir)),
                            REGISTRY_FILE)
    state = default_state_dir()
    if not state:
        return None
    return os.path.join(state, REGISTRY_FILE)


def today() -> str:
    """Today's UTC calendar day, 'YYYY-MM-DD'."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def is_date(value) -> bool:
    """True when the value is a YYYY-MM-DD calendar date."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        return False
    return True


def normalize_verdict(value: str) -> str:
    """A verdict string -> its canonical spelling."""
    text = str(value or "").strip().lower().replace(" ", "-")
    return VERDICT_ALIASES.get(text, text)


def split_list(values) -> list[str]:
    """A list of CLI strings (comma-separated allowed) -> clean list."""
    out = []
    for raw in values or []:
        for piece in str(raw).replace("\n", ",").split(","):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def read_rows(path: str) -> list[dict]:
    """Read a JSONL registry; unparseable lines are kept as markers."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                rows.append({"__line__": number, "__raw__": text})
                continue
            if isinstance(row, dict):
                row.setdefault("__line__", number)
                rows.append(row)
            else:
                rows.append({"__line__": number, "__raw__": text})
    return rows


def clean_row(row: dict) -> dict:
    """A row without the reader's bookkeeping fields."""
    return {key: value for key, value in row.items()
            if key not in ("__line__", "__raw__")}


def write_rows(path: str, rows: list[dict]) -> None:
    """Write a JSONL registry atomically (temp file + replace).

    A line that could not be parsed is written back verbatim: a rewrite
    never silently drops a row the reader could not understand.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in rows:
            if "__raw__" in row:
                fh.write(str(row["__raw__"]) + "\n")
                continue
            fh.write(json.dumps(clean_row(row), sort_keys=True,
                                default=str) + "\n")
    os.replace(tmp, path)


def append_row(path: str, row: dict) -> dict:
    """Append one row to the registry, creating it if needed."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row


# --------------------------------------------------------------------------
# the schema (METRICS.md section 6)
# --------------------------------------------------------------------------
def validate_row(row: dict, index: int = 0) -> list[dict]:
    """One registry row -> the list of schema problems it has (empty = ok)."""
    problems: list[dict] = []

    def bad(field: str, problem: str) -> None:
        problems.append({"row": index + 1,
                         "id": str(row.get("id") or ""),
                         "field": field,
                         "problem": problem})

    if "__raw__" in row:
        bad("row", "not a JSON object")
        return problems

    for field in REQUIRED_FIELDS:
        if field not in row:
            bad(field, "missing required field")

    identifier = row.get("id")
    if "id" in row and (not isinstance(identifier, str)
                        or not identifier.strip()):
        bad("id", "must be a non-empty string")

    if "date" in row and not is_date(row.get("date")):
        bad("date", "must be a YYYY-MM-DD date")

    for field in ("hypothesis", "change", "primary_metric"):
        value = row.get(field)
        if field in row and (not isinstance(value, str) or not value.strip()):
            bad(field, "must be a non-empty string")

    window = row.get("window")
    if "window" in row:
        if not isinstance(window, dict):
            bad("window", "must be an object with start and min_sample")
        else:
            if not is_date(window.get("start")):
                bad("window.start", "must be a YYYY-MM-DD date")
            sample = window.get("min_sample")
            if not isinstance(sample, int) or isinstance(sample, bool):
                bad("window.min_sample", "must be an integer")
            elif sample < 1:
                bad("window.min_sample", "must be >= 1")
            weeks = window.get("min_weeks")
            if weeks is not None and (not isinstance(weeks, int)
                                      or isinstance(weeks, bool) or weeks < 1):
                bad("window.min_weeks", "must be an integer >= 1")

    guardrails = row.get("guardrails")
    if "guardrails" in row:
        if not isinstance(guardrails, list) or not guardrails:
            bad("guardrails", "must be a non-empty list of metric names")
        else:
            for item in guardrails:
                if not isinstance(item, str) or not item.strip():
                    bad("guardrails", "every guardrail must be a non-empty "
                                      "string")

    if "verdict" in row:
        verdict = row.get("verdict")
        if not isinstance(verdict, str) or not verdict.strip():
            bad("verdict", "must be a non-empty string")
        elif normalize_verdict(verdict) not in VERDICTS:
            bad("verdict", "must be one of " + "|".join(VERDICTS))

    evidence = row.get("verdict_evidence")
    if "verdict_evidence" in row:
        verdict_now = normalize_verdict(row.get("verdict") or "")
        if isinstance(evidence, str):
            # An open experiment has no verdict numbers yet; a closed one
            # must carry them.
            if not evidence.strip() and verdict_now in CLOSED_VERDICTS:
                bad("verdict_evidence", "must not be empty for a closed "
                                        "verdict")
        elif isinstance(evidence, dict):
            if not evidence:
                bad("verdict_evidence", "must not be an empty object")
        elif isinstance(evidence, list):
            if not evidence:
                bad("verdict_evidence", "must not be an empty list")
        else:
            bad("verdict_evidence", "must be a string, object or list")

    return problems


def validate_rows(rows: list[dict]) -> list[dict]:
    """A whole registry -> every schema problem it has (empty = valid)."""
    problems: list[dict] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        problems.extend(validate_row(row, index))
        identifier = row.get("id")
        if isinstance(identifier, str) and identifier.strip():
            key = identifier.strip()
            if key in seen:
                problems.append({
                    "row": index + 1, "id": key, "field": "id",
                    "problem": f"duplicate id (first seen on row {seen[key]})",
                })
            else:
                seen[key] = index + 1
    return problems


def build_row(args, verdict: str | None = None,
              evidence: str | None = None) -> dict:
    """The registry row for an `add` (or `close`) invocation."""
    row = {
        "id": str(args.id).strip(),
        "date": str(args.date or today()),
        "hypothesis": str(args.hypothesis).strip(),
        "change": str(args.change).strip(),
        "window": {
            "start": str(args.window_start).strip(),
            "min_sample": int(args.min_sample),
            "min_weeks": int(args.min_weeks),
        },
        "primary_metric": str(args.primary_metric).strip(),
        "guardrails": split_list(args.guardrail),
        "verdict": normalize_verdict(verdict or args.verdict),
        "verdict_evidence": str(
            evidence if evidence is not None else args.verdict_evidence
        ).strip(),
    }
    if not row["verdict_evidence"] and row["verdict"] == "in-progress":
        # An open experiment has no verdict numbers yet; say so in the row
        # rather than leaving the field empty.
        row["verdict_evidence"] = "pending - no verdict numbers yet."
    if args.name:
        row["name"] = str(args.name).strip()
    return row


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render_row(entry: dict) -> str:
    """One registry row -> a one-line human summary."""
    window = entry.get("window") or {}
    guardrails = entry.get("guardrails") or []
    window_txt = f"{window.get('start', '?')} +{window.get('min_weeks', '?')}w/" \
                 f"{window.get('min_sample', '?')} runs"
    return (f"{entry.get('id', '?')}  {entry.get('date', '?')}  "
            f"[{entry.get('verdict', '?')}]  "
            f"{entry.get('primary_metric', '?')}  ({window_txt})  "
            f"guardrails: {', '.join(str(g) for g in guardrails) or '-'}  "
            f"-- {entry.get('hypothesis', '')}")


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
def cmd_init(args) -> int:
    """Create the registry file (empty) if it is not there yet."""
    path = registry_path(args.state_dir, args.registry)
    if not path:
        print("experiments: no registry path (no deployment config)",
              file=sys.stderr)
        return 2
    created = False
    if os.path.exists(path):
        if args.force:
            write_rows(path, [])
            created = True
        rows = read_rows(path)
    else:
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        write_rows(path, [])
        rows = []
        created = True
    result = {"registry": path, "created": created, "count": len(rows)}
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        state = "created" if created else "already exists"
        print(f"experiment registry {state}: {path} ({len(rows)} row(s))")
    return 0


def cmd_add(args) -> int:
    """Append one experiment row to the registry."""
    path = registry_path(args.state_dir, args.registry)
    if not path:
        print("experiments: no registry path (no deployment config)",
              file=sys.stderr)
        return 2
    if not os.path.exists(path):
        print(f"experiments: registry not found: {path} "
              "(run `experiments.py init` first)", file=sys.stderr)
        return 2
    row = build_row(args)
    problems = validate_row(row)
    if problems:
        print("experiments: row rejected:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem['field']}: {problem['problem']}",
                  file=sys.stderr)
        return 1
    existing = read_rows(path)
    for other in existing:
        if str(other.get("id") or "").strip() == row["id"]:
            print(f"experiments: duplicate id: {row['id']}", file=sys.stderr)
            return 1
    append_row(path, row)
    if args.json:
        print(json.dumps(row, indent=1, sort_keys=True))
    else:
        print(f"RECORDED: {row['id']} verdict={row['verdict']} -> {path}")
        print(f"  {render_row(row)}")
    return 0


def cmd_close(args) -> int:
    """Record a verdict on an existing row."""
    path = registry_path(args.state_dir, args.registry)
    if not path:
        print("experiments: no registry path (no deployment config)",
              file=sys.stderr)
        return 2
    if not os.path.exists(path):
        print(f"experiments: registry not found: {path}", file=sys.stderr)
        return 2
    verdict = normalize_verdict(args.verdict)
    if verdict not in CLOSED_VERDICTS:
        print("experiments: verdict must be one of "
              + "|".join(CLOSED_VERDICTS), file=sys.stderr)
        return 1
    rows = read_rows(path)
    target = None
    for row in rows:
        if str(row.get("id") or "").strip() == str(args.id).strip():
            target = row
            break
    if target is None:
        print(f"experiments: no experiment with id {args.id}", file=sys.stderr)
        return 2
    target["verdict"] = verdict
    target["verdict_evidence"] = str(args.verdict_evidence).strip()
    target["verdict_date"] = today()
    if getattr(args, "window_start", None):
        # A retroactive row's window can be corrected when the real start
        # date (the merge day) becomes known.
        target.setdefault("window", {})
        target["window"]["start"] = str(args.window_start).strip()
    problems = validate_row(target)
    if problems:
        print("experiments: row rejected:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem['field']}: {problem['problem']}",
                  file=sys.stderr)
        return 1
    write_rows(path, rows)
    if args.json:
        print(json.dumps(target, indent=1, sort_keys=True))
    else:
        print(f"CLOSED: {target['id']} verdict={verdict} -> {path}")
        print(f"  evidence: {target['verdict_evidence']}")
    return 0


def cmd_list(args) -> int:
    """List the registry rows."""
    path = registry_path(args.state_dir, args.registry)
    if not path:
        print("experiments: no registry path (no deployment config)",
              file=sys.stderr)
        return 2
    rows = read_rows(path)
    entries = [row for row in rows if "__raw__" not in row]
    if args.verdict:
        wanted = normalize_verdict(args.verdict)
        entries = [row for row in entries
                   if normalize_verdict(row.get("verdict") or "") == wanted]
    entries.sort(key=lambda row: (str(row.get("date") or ""),
                                  str(row.get("id") or "")))
    entries = [clean_row(row) for row in entries]
    if args.json:
        print(json.dumps({"registry": path, "count": len(entries),
                          "experiments": entries}, indent=1, default=str))
    else:
        print(f"experiment registry: {path} ({len(entries)} row(s))")
        for row in entries:
            print(f"  {render_row(row)}")
    return 0


def cmd_validate(args) -> int:
    """Validate a registry file against the section 6 schema."""
    path = registry_path(args.state_dir, args.registry)
    if not path:
        print("experiments: no registry path (no deployment config)",
              file=sys.stderr)
        return 2
    if not os.path.exists(path):
        print(f"experiments: registry not found: {path}", file=sys.stderr)
        return 2
    rows = read_rows(path)
    problems = validate_rows(rows)
    result = {
        "registry": path,
        "count": len(rows),
        "valid": not problems,
        "errors": problems,
    }
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        if problems:
            print(f"INVALID: {path} ({len(problems)} problem(s))")
            for problem in problems:
                print(f"  row {problem['row']} "
                      f"[{problem['id'] or '?'}] {problem['field']}: "
                      f"{problem['problem']}")
        else:
            print(f"VALID: {path} ({len(rows)} row(s))")
    return 0 if not problems else 1


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(parser):
        parser.add_argument("--state-dir", default=None,
                            help="state dir holding experiments.jsonl "
                                 "(default: deployment config state dir)")
        parser.add_argument("--registry", default=None,
                            help="path to the registry file (overrides "
                                 "--state-dir)")
        parser.add_argument("--json", action="store_true",
                            help="print machine-readable JSON")

    p_init = sub.add_parser("init", help="create the registry file")
    common(p_init)
    p_init.add_argument("--force", action="store_true",
                        help="truncate an existing registry")
    p_init.set_defaults(func=cmd_init)

    p_add = sub.add_parser("add", help="record one experiment row")
    common(p_add)
    p_add.add_argument("--id", required=True, help="the experiment id")
    p_add.add_argument("--hypothesis", required=True,
                       help="one sentence: metric, direction, size")
    p_add.add_argument("--change", required=True,
                       help="what was edited, with the commit/PR link")
    p_add.add_argument("--window-start", required=True,
                       help="the window start, YYYY-MM-DD")
    p_add.add_argument("--primary-metric", required=True,
                       help="the one metric this experiment is judged on")
    p_add.add_argument("--guardrail", action="append", default=[],
                       help="a metric that must not regress (repeatable)")
    p_add.add_argument("--verdict", default="in-progress",
                       help="keep|revert|inconclusive|in-progress "
                            "(default: in-progress)")
    p_add.add_argument("--verdict-evidence", default="",
                       help="the stratified numbers behind the verdict")
    p_add.add_argument("--date", default=None,
                       help="the day the experiment was opened "
                            "(default: today)")
    p_add.add_argument("--name", default="",
                       help="a short label for the dashboard's Panel 5")
    p_add.add_argument("--min-sample", type=int, default=DEFAULT_MIN_SAMPLE,
                       help=f"minimum runs in the window "
                            f"(default: {DEFAULT_MIN_SAMPLE})")
    p_add.add_argument("--min-weeks", type=int, default=DEFAULT_MIN_WEEKS,
                       help=f"minimum weeks in the window "
                            f"(default: {DEFAULT_MIN_WEEKS})")
    p_add.set_defaults(func=cmd_add)

    p_close = sub.add_parser("close", help="record a verdict on a row")
    common(p_close)
    p_close.add_argument("--id", required=True, help="the experiment id")
    p_close.add_argument("--verdict", required=True,
                         help="keep|revert|inconclusive")
    p_close.add_argument("--verdict-evidence", required=True,
                         help="the stratified numbers behind the verdict")
    p_close.add_argument("--window-start", default=None,
                         help="correct the row's window start (YYYY-MM-DD), "
                              "e.g. when a retroactive row's real merge "
                              "date becomes known")
    p_close.set_defaults(func=cmd_close)

    p_list = sub.add_parser("list", help="list the registry rows")
    common(p_list)
    p_list.add_argument("--verdict", default=None,
                        help="only rows with this verdict")
    p_list.set_defaults(func=cmd_list)

    p_val = sub.add_parser("validate", help="validate the registry")
    common(p_val)
    p_val.set_defaults(func=cmd_validate)

    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
