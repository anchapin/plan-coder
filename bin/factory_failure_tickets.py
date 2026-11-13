#!/usr/bin/env python3
"""Factory failures as a ticket source (issue #27, "Fix-lite").

A cron run failed. This script turns that failure into a ticket: it
computes a deterministic fingerprint of the failure, looks for an open
auto-filed issue with the same fingerprint, and either appends a
recurrence comment to it or files exactly one new issue.

This is a TICKET SOURCE ONLY. Nothing here fixes anything, and
nothing here dispatches work: an auto-filed issue is picked up by a
human (or by the supervisor only after a human has triaged it and
removed the auto-filed marker).

Input format (JSON object; one of):
  * on stdin
  * --input PATH (a JSON file)
  * individual CLI flags (flags win over the JSON object)

  {
    "repo":         "you/alpha",        # GitHub slug, required
    "cron":         "backlog-supervisor",  # cron id, required
    "run_id":       "run-20261113-0421",   # failed run id, required
    "timestamp":    "2026-11-13T04:21:07Z",  # failure time, required
    "error_class":  "timeout",           # failure class, required
    "error_text":   "…the failure text…" # failure text, required
    "last_good_run": "run-20261113-0200"  # optional: last known good
                                          # run of the SAME cron
  }

Fingerprint
-----------
The fingerprint is sha256(cron | error_class | normalized error text),
truncated to 16 hex chars. Normalization removes the volatile parts of
a failure message so that the same failure seen twice produces the
same fingerprint:

  * lowercased, whitespace collapsed
  * the failed run id (as given in the input)  -> <id>
  * ISO timestamps (with or without zone)      -> <ts>
  * plain dates                                -> <date>
  * epoch seconds/milliseconds                 -> <epoch>
  * UUIDs                                      -> <uuid>
  * hex blobs (>= 8 chars)                     -> <hex>
  * absolute paths                             -> <path>
  * http(s) URLs                               -> <url>
  * every remaining number                     -> <n>

So "run-42 timed out after 30s at /tmp/x.log" and "run-99 timed out
after 12s at /var/y.log" share one fingerprint, while a different
error class or a different message shape does not.

The fingerprint is carried in the issue body as a machine-readable
line:

  factory-failure-fingerprint: <hex>

Auto-filed issues are recognizable by their title prefix
"[factory-failure]" and the label "auto-filed"; a human removes the
marker when they triage the issue.

GitHub boundary
---------------
Everything that talks to GitHub goes through the `gh` CLI (one
function, `run_gh`), so the whole path is mockable in tests. The
capture step is fail-silent towards the factory: if `gh` fails or
GitHub is unreachable, nothing is filed, no exception escapes, and the
script exits with the documented code 3.

Exit codes
----------
  0  a ticket was filed, or a recurrence comment was appended
  2  the capture step itself failed (bad input, internal error):
     a plain message is printed and NOTHING is filed — in particular
     no issue is ever filed about the capture step's own failure
  3  GitHub/`gh` failed: nothing was filed, the caller is not
     crashed

Usage:
    python3 bin/factory_failure_tickets.py --repo you/alpha \\
        --cron backlog-supervisor --run-id run-1 \\
        --timestamp 2026-11-13T04:21:07Z --error-class timeout \\
        --error-text "worker timed out" [--last-good-run run-0] [--json]
    echo '{"repo": "...", ...}' | python3 bin/factory_failure_tickets.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys

# --- constants (single source of truth; the supervisor prompt rule
# --- quotes these exact strings) ---------------------------------------
TITLE_PREFIX = "[factory-failure]"
LABEL = "auto-filed"
FINGERPRINT_KEY = "factory-failure-fingerprint"
FINGERPRINT_LINE = f"{FINGERPRINT_KEY}: "
MAX_EXCERPT_LINES = 30
MAX_EXCERPT_LINE_CHARS = 400

EXIT_OK = 0
EXIT_CAPTURE_FAILED = 2
EXIT_GH_FAILED = 3

# The capture step's own cron id: a failure of the capture step is
# never turned into a ticket (it would file an issue about itself).
SELF_CRON_ID = "factory-failure-tickets"

REQUIRED_FIELDS = ("repo", "cron", "run_id", "timestamp", "error_class",
                    "error_text")

CRON_IDS = ("backlog-supervisor", "pr-shepherd", "harness-tune")


class CaptureError(Exception):
    """The capture step itself failed — file nothing, exit 2."""


class GhError(Exception):
    """`gh` failed or GitHub was unreachable — file nothing, exit 3."""


# --- normalization ------------------------------------------------------

_RE_RUN_ID = re.compile(r"\brun[-_ ]?[0-9a-z]+\b")
_RE_TS = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
    re.IGNORECASE)
_RE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RE_EPOCH = re.compile(r"\b\d{10,13}\b")
_RE_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_RE_HEX = re.compile(r"\b[0-9a-f]{8,}\b")
_RE_URL = re.compile(r"\bhttps?://\S+")
_RE_PATH = re.compile(r"(?:[A-Za-z]:)?(?:/[\w.+@-]+){2,}/?")
_RE_NUMBER = re.compile(r"\d+(?:\.\d+)*")


def normalize(text: str, run_id: str | None = None) -> str:
    """Normalize a failure text into its stable signature.

    See the module docstring for the rule list. The order matters:
    the most specific patterns run first.
    """
    out = str(text or "").lower()
    if run_id:
        out = out.replace(str(run_id).lower(), "<id>")
    out = _RE_TS.sub("<ts>", out)
    out = _RE_DATE.sub("<date>", out)
    out = _RE_EPOCH.sub("<epoch>", out)
    out = _RE_UUID.sub("<uuid>", out)
    out = _RE_HEX.sub("<hex>", out)
    out = _RE_URL.sub("<url>", out)
    out = _RE_PATH.sub("<path>", out)
    out = _RE_RUN_ID.sub("<run>", out)
    out = _RE_NUMBER.sub("<n>", out)
    return " ".join(out.split())


def fingerprint(cron: str, error_class: str, error_text: str,
                run_id: str | None = None) -> str:
    """Deterministic fingerprint of one failure."""
    payload = "|".join([
        str(cron or "").strip().lower(),
        str(error_class or "").strip().lower(),
        normalize(error_text, run_id),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --- body assembly -----------------------------------------------------

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{6,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{6,}"),
    re.compile(r"\bAKIA[0-9A-Z]{8,}"),
)


def redact(text: str) -> str:
    """Mask secret-shaped tokens before anything enters an issue body."""
    out = str(text or "")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("<redacted>", out)
    return out


def excerpt(error_text: str) -> list[str]:
    """Bounded excerpt of the failure text: at most 30 lines, each
    line truncated, secrets masked."""
    lines = redact(error_text).splitlines()
    out = []
    for line in lines[:MAX_EXCERPT_LINES]:
        line = line.rstrip()
        if len(line) > MAX_EXCERPT_LINE_CHARS:
            line = line[:MAX_EXCERPT_LINE_CHARS] + " …[truncated]"
        out.append(line)
    return out


def build_title(record: dict) -> str:
    cron = record.get("cron") or "unknown-cron"
    klass = record.get("error_class") or "unknown-error"
    return f"{TITLE_PREFIX} {cron} failed: {klass}"


def build_body(record: dict, fp: str) -> str:
    """The issue body: the machine-readable fingerprint line plus the
    bounded context a human needs."""
    lines = [
        f"{FINGERPRINT_LINE}{fp}",
        "",
        f"- Cron: {record.get('cron') or ''}",
        f"- Run id: {record.get('run_id') or ''}",
        f"- Timestamp: {record.get('timestamp') or ''}",
        f"- Error class: {record.get('error_class') or ''}",
        f"- Fingerprint: {fp}",
        f"- Last known good run: {record.get('last_good_run') or 'unknown'}",
        "",
        "## Error excerpt",
        "",
        f"(at most {MAX_EXCERPT_LINES} lines of the failure text)",
        "",
        "```",
    ]
    lines.extend(excerpt(record.get("error_text") or ""))
    lines.append("```")
    lines.append("")
    lines.append("Filed automatically by the factory-failure capture step. "
                 "A human triages this issue; the supervisor skips "
                 f"auto-filed (`{LABEL}`) issues until the marker is "
                 "removed.")
    return "\n".join(lines) + "\n"


def recurrence_comment(record: dict, fp: str) -> str:
    return (f"Factory failure recurrence — same fingerprint "
            f"(`{fp}`).\n\n"
            f"- Run id: {record.get('run_id') or ''}\n"
            f"- Timestamp: {record.get('timestamp') or ''}\n"
            f"- Error class: {record.get('error_class') or ''}")


# --- the gh boundary ---------------------------------------------------

def run_gh(args: list[str]) -> str:
    """The one place this script touches GitHub. Tests mock this."""
    try:
        proc = subprocess.run(["gh"] + list(args), capture_output=True,
                              text=True, check=False)
    except OSError as exc:
        raise GhError(f"gh could not be run: {exc}") from exc
    if proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout or
                       f"gh exited {proc.returncode}").strip())
    return proc.stdout or ""


def _list_open_auto_filed(repo: str, fp: str, gh=run_gh) -> list[dict]:
    out = gh([
        "issue", "list", "--repo", repo, "--state", "open",
        "--label", LABEL, "--search", f"{FINGERPRINT_KEY}: {fp} in:body",
        "--json", "number,title,url,body", "--limit", "100",
    ])
    try:
        items = json.loads(out or "[]")
    except json.JSONDecodeError as exc:
        raise GhError(f"gh issue list returned invalid JSON: {exc}") from exc
    if not isinstance(items, list):
        return []
    hits = []
    for item in items:
        if not isinstance(item, dict):
            continue
        body = str(item.get("body") or "")
        if FINGERPRINT_LINE + fp in body:
            hits.append(item)
    return hits


def _issue_number(url: str, number=None) -> int | None:
    if number is not None:
        try:
            return int(number)
        except (TypeError, ValueError):
            pass
    m = re.search(r"/issues/(\d+)", str(url or ""))
    return int(m.group(1)) if m else None


def capture_failure(record: dict, gh=run_gh) -> dict:
    """File or dedupe one factory failure.

    Returns a result dict. Raises CaptureError (the capture step
    itself failed) or GhError (GitHub unreachable / gh failed).
    """
    if not isinstance(record, dict):
        raise CaptureError("input is not a JSON object")
    missing = [f for f in REQUIRED_FIELDS
               if not str(record.get(f) or "").strip()]
    if missing:
        raise CaptureError("missing field(s): " + ", ".join(missing))
    cron = str(record["cron"]).strip()
    if cron == SELF_CRON_ID:
        raise CaptureError("refusing to file an issue about the capture "
                           "step's own failure")
    repo = str(record["repo"]).strip()
    fp = fingerprint(cron, record["error_class"], record["error_text"],
                     record.get("run_id"))

    existing = _list_open_auto_filed(repo, fp, gh=gh)
    if existing:
        item = existing[0]
        number = _issue_number(item.get("url"), item.get("number"))
        body = recurrence_comment(record, fp)
        gh(["issue", "comment", str(number), "--repo", repo, "--body", body])
        return {"action": "commented", "fingerprint": fp, "repo": repo,
                "cron": cron, "issue_number": number,
                "issue_url": item.get("url") or "",
                "recurrences": len(existing)}

    body = build_body(record, fp)
    title = build_title(record)
    out = gh(["issue", "create", "--repo", repo, "--title", title,
              "--label", LABEL, "--body", body])
    url = (out or "").strip().splitlines()[-1].strip() if out.strip() else ""
    return {"action": "filed", "fingerprint": fp, "repo": repo, "cron": cron,
            "issue_number": _issue_number(url), "issue_url": url,
            "title": title, "label": LABEL}


# --- input -------------------------------------------------------------

def load_input(args) -> dict:
    """Merge the JSON input (stdin or --input) with the CLI flags."""
    record: dict = {}
    raw = ""
    if args.input:
        try:
            with open(args.input, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            raise CaptureError(f"cannot read input file: {exc}") from exc
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CaptureError(f"input is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise CaptureError("input JSON must be an object")
        record.update(parsed)
    flags = {
        "repo": args.repo, "cron": args.cron, "run_id": args.run_id,
        "timestamp": args.timestamp, "error_class": args.error_class,
        "error_text": args.error_text, "last_good_run": args.last_good_run,
    }
    for key, value in flags.items():
        if value is not None:
            record[key] = value
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="File or dedupe a GitHub issue per factory failure.")
    ap.add_argument("--input", default=None,
                    help="JSON file with the failure record (default: stdin)")
    ap.add_argument("--repo", default=None, help="GitHub slug, e.g. you/alpha")
    ap.add_argument("--cron", default=None, help="cron id, e.g. pr-shepherd")
    ap.add_argument("--run-id", dest="run_id", default=None)
    ap.add_argument("--timestamp", default=None, help="ISO 8601 failure time")
    ap.add_argument("--error-class", dest="error_class", default=None)
    ap.add_argument("--error-text", dest="error_text", default=None)
    ap.add_argument("--last-good-run", dest="last_good_run", default=None,
                    help="last known good run of the same cron")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        record = load_input(args)
        result = capture_failure(record, gh=run_gh)
    except CaptureError as exc:
        print(f"factory-failure-tickets: capture failed: {exc}",
              file=sys.stderr)
        return EXIT_CAPTURE_FAILED
    except GhError as exc:
        print(f"factory-failure-tickets: GitHub unavailable, "
              f"no ticket filed: {exc}", file=sys.stderr)
        return EXIT_GH_FAILED
    except Exception as exc:  # noqa: BLE001 — the capture step must never
        # crash its caller and must never file anything about itself.
        print(f"factory-failure-tickets: capture failed: {exc}",
              file=sys.stderr)
        return EXIT_CAPTURE_FAILED

    if args.json:
        print(json.dumps(result, indent=1))
    else:
        if result["action"] == "commented":
            print(f"RECURRENCE: {result['repo']}#{result['issue_number']} "
                  f"fingerprint={result['fingerprint']} "
                  f"(comment appended, no new issue)")
        else:
            print(f"FILED: {result['repo']}#{result['issue_number']} "
                  f"fingerprint={result['fingerprint']} "
                  f"title={result.get('title') or ''}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
