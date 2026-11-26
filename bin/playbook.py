#!/usr/bin/env python3
"""Per-repo learned playbooks (plan-coder #184, Phase 1).

A playbook is a store of short, repo-specific, failure-linked lessons
that sit between a target repo's CONTRACT.md (human-maintained,
rules-only) and global prompt edits. Phase 0 (offline mining of the
worker ledger + transcripts, 2026-11-26) found 4 distinct recurring
lessons for fluxion at 37.5% duplication vs CONTRACT.md/prompts, so
this store was built for fluxion first, seeded from that report.

Store format (decision, #184)
-----------------------------
One Markdown file per entry, with a small machine-readable header
between ``---`` fences at the top:

    ---
    id: fluxion-cold-build-step-economics
    repo: fluxion
    title: Cold-build step economics: warm the target before you write code
    status: active
    created: 2026-11-26
    keywords: cargo, build, cold-build, step-cap
    failure_classes: step-cap, build-wait
    paths: Cargo.toml, benches/
    ---

    <lesson body in Markdown: Lesson / Evidence / Trigger sections>

Why this shape, over JSON or one big file per repo:

* Markdown matches the repo's conventions: briefs, CONTRACT.md and
  the prompts are all Markdown a manager reads and edits by hand, and
  an entry is ultimately pasted into a brief verbatim.
* The header is deliberately a flat ``key: value`` subset (comma
  lists, no nesting) parseable with the stdlib alone -- no YAML
  dependency, consistent with the harness's stdlib-only rule.
* One file per entry makes the lifecycle mechanical: a ``pending``
  entry can be approved by flipping one ``status:`` line, and an
  archived entry is *moved* (never deleted) under ``archive/``.

Layout of a runtime store (outside every target repo)::

    <store>/<repo>/<entry-id>.md          active / stale entries
    <store>/<repo>/pending/<entry-id>.md  staged, awaiting approval
    <store>/<repo>/archive/<entry-id>.md  archived, never deleted
    <store>/usage.jsonl                    append-only usage ledger

Store resolution: ``--store PATH`` wins, then
``$PLAN_CODER_PLAYBOOKS_DIR``, then ``workers.playbooks.dir`` in the
deployment config, then ``<state.dir>/playbooks`` from the deployment
config (same search order as trace_summary.py), then
``~/.plan-coder/state/playbooks``. Live state is never committed; the
seed entries shipped with this repo live in ``examples/playbooks/``
and can be copied into a runtime store by the operator.

Guardrails (from the issue, Hermes-derived, not Hermes' autonomy):
staged-approval writes only (``propose`` writes *pending*, never
active), archive-never-delete, and no LLM consolidation anywhere in
this tool -- it is deterministic stdlib code.

Lifecycle: an entry is ``active`` while its last activity (creation
or last recorded usage) is <= 14 days old, ``stale`` at 15-30 days,
and ``archived`` past 30 days. The lifecycle *report* only classifies;
it moves and deletes nothing. An entry already under ``archive/``
stays archived regardless of dates.

Usage:
    python3 bin/playbook.py list [--repo NAME] [--json]
    python3 bin/playbook.py match --repo NAME
        [--keywords a,b] [--failure-class CLASS] [--text TEXT]
        [--paths p1,p2] [--json]
    python3 bin/playbook.py record-usage --repo NAME --entry ID
        [--issue N] [--outcome TEXT] [--run-id ID] [--ts ISO]
    python3 bin/playbook.py lifecycle [--repo NAME] [--now ISO]
        [--json]
    python3 bin/playbook.py propose --repo NAME --title TEXT
        (--body TEXT | --body-file PATH)
        [--keywords a,b] [--failure-classes a,b] [--paths a,b]
        [--entry-id ID] [--now ISO]

Exit 0 on success; exit 2 on a missing/unreadable store or bad input.
This tool never edits supervisor prompts and is not wired into
dispatch: integration is a later, separately approved step.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_summary import _load_deployment_config

STALE_DAYS = 14
ARCHIVE_DAYS = 30
STATUSES = ("active", "pending", "archived")


def default_store_path() -> str:
    env = os.environ.get("PLAN_CODER_PLAYBOOKS_DIR")
    if env:
        return os.path.expanduser(env)
    cfg = _load_deployment_config()
    pb = ((cfg.get("workers") or {}).get("playbooks") or {})
    if isinstance(pb, dict) and pb.get("dir"):
        return os.path.expanduser(str(pb["dir"]))
    state_dir = ((cfg.get("state") or {}).get("dir")
                 or "~/.plan-coder/state")
    return os.path.join(os.path.expanduser(state_dir), "playbooks")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "entry"


def _parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _split_list(value: str) -> list:
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_entry_text(text: str, source: str = "") -> dict:
    """Parse one entry file: flat header + Markdown body."""
    entry = {"id": "", "repo": "", "title": "", "status": "active",
             "created": "", "keywords": [], "failure_classes": [],
             "paths": [], "body": text.strip(), "source": source}
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            for line in lines[1:end]:
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if key in ("keywords", "failure_classes", "paths"):
                    entry[key] = _split_list(val)
                elif key in entry:
                    entry[key] = val
            entry["body"] = "\n".join(lines[end + 1:]).strip()
    if not entry["id"] and source:
        entry["id"] = os.path.splitext(os.path.basename(source))[0]
    return entry


def render_entry(entry: dict) -> str:
    def joined(key):
        return ", ".join(entry.get(key) or [])

    header = [
        "---",
        f"id: {entry['id']}",
        f"repo: {entry['repo']}",
        f"title: {entry['title']}",
        f"status: {entry.get('status') or 'active'}",
        f"created: {entry.get('created') or ''}",
        f"keywords: {joined('keywords')}",
        f"failure_classes: {joined('failure_classes')}",
        f"paths: {joined('paths')}",
        "---",
        "",
        entry.get("body", "").strip(),
        "",
    ]
    return "\n".join(header)


def _entry_files(store: str, repo: str | None) -> list:
    out = []
    if not os.path.isdir(store):
        return out
    repos = [repo] if repo else sorted(
        d for d in os.listdir(store)
        if os.path.isdir(os.path.join(store, d)))
    for name in repos:
        base = os.path.join(store, name)
        if not os.path.isdir(base):
            continue
        for sub in ("", "pending", "archive"):
            folder = os.path.join(base, sub) if sub else base
            if not os.path.isdir(folder):
                continue
            for fname in sorted(os.listdir(folder)):
                if fname.endswith(".md"):
                    out.append(os.path.join(folder, fname))
    return out


def load_entries(store: str, repo: str | None = None) -> list:
    entries = []
    for path in _entry_files(store, repo):
        with open(path, encoding="utf-8") as fh:
            entry = parse_entry_text(fh.read(), source=path)
        rel = os.path.relpath(path, store)
        parts = rel.split(os.sep)
        if "archive" in parts and entry["status"] != "pending":
            entry["status"] = "archived"
        if "pending" in parts:
            entry["status"] = "pending"
        entries.append(entry)
    return entries


def load_usage(store: str) -> list:
    path = os.path.join(store, "usage.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def last_activity(entry: dict, usage: list) -> date | None:
    latest = _parse_date(entry.get("created"))
    for row in usage:
        if row.get("entry") == entry["id"] and \
                row.get("repo") == entry["repo"]:
            when = _parse_date(row.get("ts"))
            if when and (latest is None or when > latest):
                latest = when
    return latest


def lifecycle_status(entry: dict, usage: list,
                     now: date) -> str:
    """active / stale / archived / pending for one entry at `now`."""
    if entry.get("status") == "pending":
        return "pending"
    if entry.get("status") == "archived":
        return "archived"
    last = last_activity(entry, usage)
    if last is None:
        return "active"
    age = (now - last).days
    if age > ARCHIVE_DAYS:
        return "archived"
    if age > STALE_DAYS:
        return "stale"
    return "active"


def lifecycle_report(store: str, repo: str | None = None,
                     now: date | None = None) -> dict:
    now = now or date.today()
    usage = load_usage(store)
    rows = []
    counts = {"active": 0, "stale": 0, "archived": 0, "pending": 0}
    for entry in load_entries(store, repo):
        status = lifecycle_status(entry, usage, now)
        last = last_activity(entry, usage)
        counts[status] = counts.get(status, 0) + 1
        rows.append({
            "id": entry["id"], "repo": entry["repo"],
            "title": entry["title"], "status": status,
            "last_activity": last.isoformat() if last else None,
            "age_days": (now - last).days if last else None,
        })
    return {"now": now.isoformat(), "counts": counts, "entries": rows}


def match_entries(store: str, repo: str, keywords=None,
                  failure_class: str = "", text: str = "",
                  paths=None, now: date | None = None) -> list:
    """Entries that would attach to a brief for `repo`.

    An entry attaches when at least one signal hits: a keyword, the
    failure class, or a path trigger, searched against the entry's
    header lists plus its title/body (so a brief whose text mentions
    'cargo build' attaches the cold-build entry). Pending and
    archived entries never attach. Score = number of distinct
    signals hit; results sort by score, then id, for determinism.
    """
    now = now or date.today()
    usage = load_usage(store)
    wanted_kw = {k.strip().lower() for k in (keywords or []) if k.strip()}
    wanted_paths = [p.strip() for p in (paths or []) if p.strip()]
    hay_terms = set(wanted_kw)
    for token in re.findall(r"[a-z0-9][a-z0-9_-]+", text.lower()):
        hay_terms.add(token)
    scored = []
    for entry in load_entries(store, repo):
        if entry["repo"] != repo:
            continue
        status = lifecycle_status(entry, usage, now)
        if status in ("pending", "archived"):
            continue
        signals = []
        entry_kw = {k.lower() for k in entry["keywords"]}
        for kw in sorted(wanted_kw & entry_kw):
            signals.append(f"keyword:{kw}")
        if failure_class and failure_class.lower() in {
                c.lower() for c in entry["failure_classes"]}:
            signals.append(f"failure_class:{failure_class}")
        for path in wanted_paths:
            for trigger in entry["paths"]:
                if path == trigger or path.startswith(
                        trigger.rstrip("/") + "/") or \
                        trigger.startswith(path.rstrip("/") + "/"):
                    signals.append(f"path:{trigger}")
                    break
        body_hay = (entry["title"] + "\n" + entry["body"]).lower()
        for term in sorted(hay_terms - wanted_kw):
            if term in entry_kw or term in body_hay:
                signals.append(f"text:{term}")
        if signals:
            scored.append({"id": entry["id"], "repo": entry["repo"],
                           "title": entry["title"], "status": status,
                           "score": len(signals),
                           "signals": signals, "body": entry["body"]})
    scored.sort(key=lambda r: (-r["score"], r["id"]))
    return scored


def record_usage(store: str, repo: str, entry_id: str, issue="",
                 outcome: str = "", run_id: str = "",
                 ts: str = "") -> dict:
    """Append one row to usage.jsonl. Append-only: never rewrites."""
    os.makedirs(store, exist_ok=True)
    row = {"ts": ts or datetime.now().astimezone().isoformat(),
           "repo": repo, "entry": entry_id}
    if issue != "" and issue is not None:
        row["issue"] = issue
    if outcome:
        row["outcome"] = outcome
    if run_id:
        row["run_id"] = run_id
    path = os.path.join(store, "usage.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


def propose_entry(store: str, repo: str, title: str, body: str,
                  keywords=None, failure_classes=None, paths=None,
                  entry_id: str = "", now: date | None = None) -> dict:
    """Stage a PENDING entry for human review. Never activates."""
    now = now or date.today()
    entry = {
        "id": entry_id or slugify(f"{repo}-{title}"),
        "repo": repo, "title": title, "status": "pending",
        "created": now.isoformat(),
        "keywords": list(keywords or []),
        "failure_classes": list(failure_classes or []),
        "paths": list(paths or []), "body": body,
    }
    folder = os.path.join(store, repo, "pending")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, entry["id"] + ".md")
    if os.path.exists(path):
        raise FileExistsError(f"pending entry already exists: {path}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_entry(entry))
    entry["source"] = path
    return entry


def _print_entries(entries: list) -> None:
    if not entries:
        print("(no entries)")
        return
    for e in entries:
        print(f"{e['repo']}/{e['id']} [{e.get('status') or 'active'}] "
              f"{e['title']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default=None,
                    help="playbook store dir (default: deployment "
                         "config state dir / playbooks)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--repo", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("match")
    p.add_argument("--repo", required=True)
    p.add_argument("--keywords", default="")
    p.add_argument("--failure-class", default="")
    p.add_argument("--text", default="")
    p.add_argument("--paths", default="")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("record-usage")
    p.add_argument("--repo", required=True)
    p.add_argument("--entry", required=True)
    p.add_argument("--issue", default="")
    p.add_argument("--outcome", default="")
    p.add_argument("--run-id", default="")
    p.add_argument("--ts", default="")

    p = sub.add_parser("lifecycle")
    p.add_argument("--repo", default=None)
    p.add_argument("--now", default="")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("propose")
    p.add_argument("--repo", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--body-file", default="")
    p.add_argument("--keywords", default="")
    p.add_argument("--failure-classes", default="")
    p.add_argument("--paths", default="")
    p.add_argument("--entry-id", default="")
    p.add_argument("--now", default="")

    args = ap.parse_args(argv)
    store = args.store or default_store_path()

    if args.cmd == "list":
        if not os.path.isdir(store):
            print(f"store not found: {store}", file=sys.stderr)
            return 2
        entries = load_entries(store, args.repo)
        if args.json:
            print(json.dumps(entries, indent=1))
        else:
            _print_entries(entries)
        return 0

    if args.cmd == "match":
        if not os.path.isdir(store):
            print(f"store not found: {store}", file=sys.stderr)
            return 2
        results = match_entries(
            store, args.repo, keywords=_split_list(args.keywords),
            failure_class=args.failure_class, text=args.text,
            paths=_split_list(args.paths))
        if args.json:
            print(json.dumps(results, indent=1))
        elif not results:
            print("(no matches)")
        else:
            for r in results:
                print(f"{r['repo']}/{r['id']} [{r['status']}] "
                      f"score={r['score']} "
                      f"signals={','.join(r['signals'])}")
                print(f"  {r['title']}")
        return 0

    if args.cmd == "record-usage":
        row = record_usage(store, args.repo, args.entry,
                           issue=args.issue, outcome=args.outcome,
                           run_id=args.run_id, ts=args.ts)
        print(json.dumps(row, sort_keys=True))
        return 0

    if args.cmd == "lifecycle":
        if not os.path.isdir(store):
            print(f"store not found: {store}", file=sys.stderr)
            return 2
        now = _parse_date(args.now) if args.now else None
        report = lifecycle_report(store, args.repo, now=now)
        if args.json:
            print(json.dumps(report, indent=1))
        else:
            c = report["counts"]
            print(f"as of {report['now']}: active={c['active']} "
                  f"stale={c['stale']} archived={c['archived']} "
                  f"pending={c['pending']}")
            for row in report["entries"]:
                print(f"  {row['repo']}/{row['id']} [{row['status']}] "
                      f"last_activity={row['last_activity']} "
                      f"age_days={row['age_days']}")
        return 0

    if args.cmd == "propose":
        body = args.body
        if args.body_file:
            with open(args.body_file, encoding="utf-8") as fh:
                body = fh.read()
        if not body.strip():
            print("propose needs --body or --body-file",
                  file=sys.stderr)
            return 2
        now = _parse_date(args.now) if args.now else None
        try:
            entry = propose_entry(
                store, args.repo, args.title, body,
                keywords=_split_list(args.keywords),
                failure_classes=_split_list(args.failure_classes),
                paths=_split_list(args.paths),
                entry_id=args.entry_id, now=now)
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"PENDING (staged for human review, not active): "
              f"{entry['source']}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
