#!/usr/bin/env python3
"""Phase-0 census of shepherd flags / Alex-facing questions
(issue #147, Part F2).

Phase 0 is measurement only: no channel is built, no prompt is changed,
no ledger format is changed, no harness behaviour is changed.  This
script answers three questions over one window and prints the evidence
for each:

    1. How many flags were raised?
    2. How many of them were acked or acted on?
    3. How many of them were repeats of an already-open question?

Inputs are two read-only snapshots:

    --ledger     the shepherd ledger (JSONL, one row per action)
    --acted-on   the proactive acted-on log (JSONL, one row per tap)

Census definitions (taken from the issue, not redefined here):

  window    The last ``--weeks`` weeks (default 4) present in the ledger
            snapshot: the window ends at the newest ledger timestamp and
            starts ``--weeks`` weeks earlier.  When the snapshot itself
            is shorter than the window, every row in the snapshot is in
            the window -- the report says so instead of pretending the
            snapshot is older than it is.

  flag      A ledger row whose ``action`` is ``needs_alex`` or
            ``needs_operator``.  These are the two shepherd actions
            that put a question in front of Alex.  The ledger's full
            action vocabulary is printed as its own table so the
            definition can be audited.

  terminal  A ledger row whose ``action`` marks the end of a tracked
            PR: ``merged``, ``merged_by_shepherd`` or ``closed_unmerged``.
            (The issue names them "merge / closed / merged_by_shepherd";
            the ledger's actual spellings are used here and the mapping
            is printed.)

  repeat    A flag row for repo+PR that is NOT the first flag row for
            that PR in the window: there is an earlier flag row for the
            same repo+PR and no terminal disposition for that PR in
            between.  A terminal disposition in between starts a fresh
            question, so the next flag is not a repeat.

  ack       Not a field in the ledger: the shepherd ledger records what
            the shepherd did, never whether Alex answered.  The ack
            number is therefore a PROXY, and both proxies are reported
            separately, never merged into one clean-looking figure:

            proxy A (terminal disposition) -- the flag is treated as
                resolved when its PR later reaches a terminal
                disposition.  Reported per flag row and per PR.

            proxy B (acted-on log) -- the acted-on snapshot is the
                proactive-loop tap log.  An entry counts as an ack of a
                flag when it names that flag's repo and PR number and
                is timestamped at or after the flag.  This proxy is
                partial by construction: the acted-on log only covers
                taps that were recorded as proactive offers, so a
                missing entry is NOT evidence that Alex did not act.

            union -- a flagged PR counts as "acked or acted on" when it
                reaches a terminal disposition OR has a matching
                acted-on entry.  This is an upper bound on the true
                ack rate, not a measurement of it.

The script prints its tables to stdout (markdown) or as JSON with
``--json``.  It reads its two input files and writes nothing.

Usage:
    python3 bin/questions_census_phase0.py \
        --ledger phase0_inputs/shepherd_ledger.jsonl \
        --acted-on phase0_inputs/acted-on.jsonl
    python3 bin/questions_census_phase0.py --ledger ... --acted-on ... --json

Exit 0 on success; exit 1 when an input file cannot be read.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys

FLAG_ACTIONS = ("needs_alex", "needs_operator")
TERMINAL_ACTIONS = ("merged", "merged_by_shepherd", "closed_unmerged")
TERMINAL_MAPPING = {
    "merged": "merge",
    "merged_by_shepherd": "merged_by_shepherd",
    "closed_unmerged": "closed",
}
DEFAULT_WEEKS = 4
RARE_REPEAT_RATE = 0.10
PROMPT_TERMINAL_RATE = 0.90
PROMPT_TERMINAL_HOURS = 24.0


def parse_ts(value: str):
    """Parse a ledger / acted-on timestamp into an aware datetime."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.UTC)
    return stamp.astimezone(datetime.UTC)


def read_jsonl(path: str) -> list[dict]:
    """Read a JSONL snapshot into a list of row dicts."""
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{number}: unreadable JSONL row ({exc})")
            if not isinstance(row, dict):
                raise SystemExit(f"{path}:{number}: row is not a JSON object")
            row["_line"] = number
            rows.append(row)
    return rows


def repo_slug(repo: str) -> str:
    """Normalise a repo field to its short slug ('anchapin/x' -> 'x')."""
    text = str(repo or "").strip().lower()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text


def pr_number(row: dict) -> int:
    try:
        return int(row.get("pr"))
    except (TypeError, ValueError):
        return -1


def load_ledger(path: str) -> list[dict]:
    """Ledger rows, each stamped, sorted by (timestamp, file order)."""
    rows = []
    for row in read_jsonl(path):
        stamp = parse_ts(row.get("ts"))
        if stamp is None:
            continue
        rows.append({
            "ts": stamp,
            "repo": str(row.get("repo") or ""),
            "slug": repo_slug(row.get("repo")),
            "pr": pr_number(row),
            "action": str(row.get("action") or "other"),
            "outcome": str(row.get("outcome") or ""),
            "detail": str(row.get("detail") or ""),
            "line": row["_line"],
        })
    rows.sort(key=lambda r: (r["ts"], r["line"]))
    return rows


def load_acted_on(path: str) -> list[dict]:
    """Acted-on rows, each stamped, sorted by (timestamp, file order)."""
    rows = []
    for row in read_jsonl(path):
        stamp = parse_ts(row.get("ts"))
        if stamp is None:
            continue
        rows.append({
            "ts": stamp,
            "loop": str(row.get("loop") or ""),
            "item": str(row.get("item") or ""),
            "note": str(row.get("note") or ""),
            "outcome": str(row.get("outcome") or ""),
            "line": row["_line"],
        })
    rows.sort(key=lambda r: (r["ts"], r["line"]))
    return rows


def window_of(rows: list[dict], weeks: int) -> tuple:
    """The last ``weeks`` weeks present in the ledger snapshot."""
    if not rows:
        raise SystemExit("ledger snapshot is empty -- nothing to census")
    end = max(r["ts"] for r in rows)
    start = end - datetime.timedelta(weeks=weeks)
    return start, end


def action_vocabulary(rows: list[dict]) -> list[dict]:
    """Every action the ledger uses, with its row count (sorted by count)."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    total = sum(counts.values())
    out = []
    for action, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        role = ("flag" if action in FLAG_ACTIONS
                else "terminal" if action in TERMINAL_ACTIONS
                else "other")
        out.append({
            "action": action,
            "rows": count,
            "share_pct": round(100.0 * count / total, 1) if total else 0.0,
            "role": role,
        })
    return out


def classify_flags(rows: list[dict]) -> dict:
    """Split the in-window ledger into flags and terminal dispositions.

    A flag is a *repeat* when an earlier flag exists for the same
    repo+PR with no terminal disposition for that PR in between.
    """
    per_pr: dict[tuple, list[dict]] = {}
    for row in rows:
        per_pr.setdefault((row["slug"], row["pr"]), []).append(row)

    flags: list[dict] = []
    terminal_after: dict[int, list[dict]] = {}

    for key, pr_rows in per_pr.items():
        open_flag: int | None = None
        for index, row in enumerate(pr_rows):
            if row["action"] in FLAG_ACTIONS:
                flag = {
                    "index": index,
                    "ts": row["ts"],
                    "repo": row["repo"],
                    "slug": row["slug"],
                    "pr": row["pr"],
                    "action": row["action"],
                    "outcome": row["outcome"],
                    "repeat": open_flag is not None,
                    "same_text_as_open": False,
                }
                if open_flag is not None:
                    previous = pr_rows[open_flag]
                    flag["same_text_as_open"] = (
                        previous["outcome"] == row["outcome"])
                flags.append(flag)
                open_flag = index
            elif row["action"] in TERMINAL_ACTIONS:
                if open_flag is not None:
                    terminal_after.setdefault(open_flag, []).append(row)
                open_flag = None

    flags.sort(key=lambda f: (f["ts"], f["repo"], f["pr"], f["index"]))
    return {"flags": flags, "terminal_after": terminal_after,
            "per_pr": per_pr}


def flag_prs(flags: list[dict]) -> dict[tuple, list[dict]]:
    """Flag rows grouped by (repo slug, PR)."""
    grouped: dict[tuple, list[dict]] = {}
    for flag in flags:
        grouped.setdefault((flag["slug"], flag["pr"]), []).append(flag)
    return grouped


def terminal_rows_by_pr(rows: list[dict]) -> dict[tuple, list[dict]]:
    """Terminal disposition rows grouped by (repo slug, PR)."""
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["action"] in TERMINAL_ACTIONS:
            grouped.setdefault((row["slug"], row["pr"]), []).append(row)
    return grouped


def percentiles(values: list[float]) -> dict:
    """Median and p90 of a sorted-able list of floats."""
    if not values:
        return {"n": 0, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = (ordered[mid] if len(ordered) % 2
              else (ordered[mid - 1] + ordered[mid]) / 2.0)
    p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    return {"n": len(ordered), "median": median, "p90": p90,
            "max": ordered[-1]}


def repo_tokens(slug_set: list[str]) -> list[tuple[str, str]]:
    """(token, slug) pairs a prose line can use to name a repo.

    A repo can be named by its full slug ("armored-archer") or by a
    distinctive prefix of it ("armored").  Only prefixes of at least
    four characters are accepted, and a prefix is only accepted when it
    is unambiguous -- it must not be a prefix of any other repo slug.
    """
    tokens: list[tuple[str, str]] = []
    for slug in slug_set:
        if not slug:
            continue
        tokens.append((slug, slug))
        parts = slug.split("-")
        for size in range(1, len(parts)):
            prefix = "-".join(parts[:size])
            if len(prefix) < 4:
                continue
            if any(other != slug and other.startswith(prefix)
                   for other in slug_set):
                continue
            tokens.append((prefix, slug))
    return sorted(set(tokens))


def acted_on_matches(acted_rows: list[dict], flagged: dict[tuple, list[dict]],
                      known_slugs: list[str],
                      first_flag_ts: dict[tuple, object]) -> list[dict]:
    """Match acted-on entries to flagged PRs by repo token + PR number.

    The acted-on log is prose: it says "matchline#1410 merge tap".  The
    matcher therefore

      * looks for a repo token (full slug or an unambiguous prefix)
        in the entry's ``item`` text -- the ``note`` field is NOT
        scanned, because a note routinely mentions PRs that were
        *skipped*, not acted on;
      * takes the PR numbers that appear in the same clause as that
        token (the text between this token and the next repo token);
      * keeps only (repo, PR) pairs that were actually flagged in the
        window, and only when the entry is timestamped at or after the
        PR's first flag -- an entry that predates the flag cannot be an
        acknowledgement of it.

    The result is a LOWER bound on what the acted-on log covers: an
    entry that answers a flag without naming a repo or a PR number is
    invisible to this matcher.
    """
    tokens = repo_tokens(known_slugs)
    matches: list[dict] = []
    for entry in acted_rows:
        text = entry["item"]
        lowered = text.lower()
        hits: set[tuple] = set()
        for token, slug in tokens:
            position = lowered.find(token)
            while position != -1:
                end = len(text)
                for other, _slug in tokens:
                    if other == token:
                        continue
                    other_position = lowered.find(other, position + len(token))
                    if other_position != -1:
                        end = min(end, other_position)
                clause = text[position:end]
                for number in re.findall(r"\d+", clause):
                    key = None
                    for candidate in flagged:
                        if candidate[0] == slug and candidate[1] == int(number):
                            key = candidate
                            break
                    if key is None:
                        continue
                    first = first_flag_ts.get(key)
                    if first is not None and entry["ts"] < first:
                        continue
                    hits.add(key)
                position = lowered.find(token, position + len(token))
        if hits:
            matches.append({"entry": entry, "keys": sorted(hits)})
    return matches


def build_census(ledger_rows: list[dict], acted_rows: list[dict],
                 weeks: int = DEFAULT_WEEKS) -> dict:
    """Run the whole census and return the payload."""
    start, end = window_of(ledger_rows, weeks)
    in_window = [r for r in ledger_rows if start <= r["ts"] <= end]
    snapshot_start = min(r["ts"] for r in ledger_rows)

    vocabulary = action_vocabulary(in_window)
    classified = classify_flags(in_window)
    flags = classified["flags"]
    grouped = flag_prs(flags)
    terminals = terminal_rows_by_pr(in_window)

    by_action = []
    for action in FLAG_ACTIONS:
        count = sum(1 for f in flags if f["action"] == action)
        by_action.append({
            "action": action,
            "flags": count,
            "share_pct": round(100.0 * count / len(flags), 1) if flags else 0.0,
        })
    distinct_questions = len({(f["repo"], f["pr"], f["action"], f["outcome"])
                               for f in flags})

    repeats = [f for f in flags if f["repeat"]]
    repeat_by_action = []
    for action in FLAG_ACTIONS:
        total = sum(1 for f in flags if f["action"] == action)
        rep = sum(1 for f in repeats if f["action"] == action)
        repeat_by_action.append({
            "action": action,
            "flags": total,
            "repeats": rep,
            "repeat_rate_pct": round(100.0 * rep / total, 1) if total else 0.0,
        })
    same_text = sum(1 for f in repeats if f["same_text_as_open"])
    diff_text = len(repeats) - same_text

    per_pr_repeats = []
    for key, pr_flags in grouped.items():
        rep = sum(1 for f in pr_flags if f["repeat"])
        per_pr_repeats.append({
            "repo": pr_flags[0]["repo"],
            "pr": key[1],
            "flags": len(pr_flags),
            "repeats": rep,
            "first_flag": pr_flags[0]["ts"].isoformat(),
            "last_flag": pr_flags[-1]["ts"].isoformat(),
        })
    per_pr_repeats.sort(key=lambda row: (-row["repeats"], -row["flags"],
                                         row["repo"], row["pr"]))

    def terminal_after_flag(flag: dict) -> list[dict]:
        found = []
        for row in terminals.get((flag["slug"], flag["pr"]), []):
            if row["ts"] > flag["ts"]:
                found.append(row)
        return found

    pr_rows = []
    latencies: list[float] = []
    resolved_prs = 0
    open_prs = 0
    for key, pr_flags in grouped.items():
        term = sorted(terminals.get(key, []), key=lambda r: r["ts"])
        later = [t for t in term if t["ts"] > pr_flags[0]["ts"]]
        if later:
            resolved_prs += 1
            hours = (later[0]["ts"] - pr_flags[0]["ts"]).total_seconds() / 3600.0
            latencies.append(hours)
            disposition = later[0]["action"]
            disposition_hours = hours
        else:
            open_prs += 1
            disposition = "none (still open at window end)"
            disposition_hours = None
        pr_rows.append({
            "repo": pr_flags[0]["repo"],
            "slug": key[0],
            "pr": key[1],
            "flags": len(pr_flags),
            "repeats": sum(1 for f in pr_flags if f["repeat"]),
            "terminal_disposition": disposition,
            "hours_first_flag_to_terminal": disposition_hours,
        })
    pr_rows.sort(key=lambda row: (-row["flags"], row["repo"], row["pr"]))

    flags_with_terminal = 0
    for flag in flags:
        if terminal_after_flag(flag):
            flags_with_terminal += 1
    flags_without_terminal = len(flags) - flags_with_terminal

    latency = percentiles(latencies)
    within_prompt = sum(1 for h in latencies if h <= PROMPT_TERMINAL_HOURS)

    acted_in_window = [e for e in acted_rows if start <= e["ts"] <= end]
    known_slugs = sorted({r["slug"] for r in in_window if r["slug"]})
    first_flag_ts = {key: pr_flags[0]["ts"] for key, pr_flags
                     in grouped.items()}
    matches = acted_on_matches(acted_in_window, grouped, known_slugs,
                                first_flag_ts)
    matched_keys = set()
    for match in matches:
        matched_keys.update(match["keys"])
    acted_outcomes: dict[str, int] = {}
    for entry in acted_in_window:
        acted_outcomes[entry["outcome"] or "(none)"] = (
            acted_outcomes.get(entry["outcome"] or "(none)", 0) + 1)

    union_prs = set()
    for row in pr_rows:
        if row["terminal_disposition"] != "none (still open at window end)":
            union_prs.add((row["slug"], row["pr"]))
    union_prs |= matched_keys
    union_count = len(union_prs)
    flagged_pr_count = len(grouped)

    repo_rows = []
    repos = sorted({key[0] for key in grouped})
    for repo in repos:
        keys = [k for k in grouped if k[0] == repo]
        repo_flags = sum(len(grouped[k]) for k in keys)
        repo_repeats = sum(1 for f in flags
                           if f["slug"] == repo and f["repeat"])
        repo_resolved = sum(
            1 for row in pr_rows
            if row["slug"] == repo
            and row["terminal_disposition"] != "none (still open at window end)")
        repo_rows.append({
            "repo": grouped[keys[0]][0]["repo"],
            "flags": repo_flags,
            "prs": len(keys),
            "repeats": repo_repeats,
            "repeat_rate_pct": round(100.0 * repo_repeats / repo_flags, 1)
            if repo_flags else 0.0,
            "prs_with_terminal": repo_resolved,
        })
    repo_rows.sort(key=lambda row: (-row["flags"], row["repo"]))

    payload = {
        "issue": "#147 Part F2, Phase 0 (measurement only)",
        "window": {
            "weeks": weeks,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "ledger_rows_total": len(ledger_rows),
            "ledger_rows_in_window": len(in_window),
            "ledger_snapshot_start": snapshot_start.isoformat(),
            "snapshot_covers_whole_window": snapshot_start <= start,
            "snapshot_span_days": round((end - snapshot_start).total_seconds()
                                        / 86400.0, 2),
        },
        "definitions": {
            "flag_actions": list(FLAG_ACTIONS),
            "terminal_actions": list(TERMINAL_ACTIONS),
            "terminal_mapping": dict(TERMINAL_MAPPING),
            "repeat": ("a flag row for repo+PR with an earlier flag row for "
                       "the same repo+PR and no terminal disposition for "
                       "that PR in between"),
            "ack_proxy_a": ("flag row / PR whose PR later reaches a "
                            "terminal disposition"),
            "ack_proxy_b": ("acted-on entry naming the flagged repo and PR "
                            "number, timestamped at or after the flag"),
            "ack_note": ("the ledger has no ack field and the acted-on log "
                         "covers only partial records, so both proxies are "
                         "bounds, not measurements"),
        },
        "action_vocabulary": vocabulary,
        "flags": {
            "total": len(flags),
            "by_action": by_action,
            "distinct_prs": flagged_pr_count,
            "distinct_questions": distinct_questions,
        },
        "repeats": {
            "total": len(repeats),
            "rate_pct": round(100.0 * len(repeats) / len(flags), 1)
            if flags else 0.0,
            "by_action": repeat_by_action,
            "same_outcome_text_as_open": same_text,
            "different_outcome_text": diff_text,
            "per_pr": per_pr_repeats,
        },
        "ack_proxy_a_terminal": {
            "flags_with_later_terminal": flags_with_terminal,
            "flags_without_later_terminal": flags_without_terminal,
            "flag_level_pct": round(100.0 * flags_with_terminal / len(flags), 1)
            if flags else 0.0,
            "prs_resolved": resolved_prs,
            "prs_still_open": open_prs,
            "pr_level_pct": round(100.0 * resolved_prs / flagged_pr_count, 1)
            if flagged_pr_count else 0.0,
            "hours_first_flag_to_terminal": latency,
            "prs_terminal_within_24h": within_prompt,
        },
        "ack_proxy_b_acted_on": {
            "acted_on_rows_total": len(acted_rows),
            "acted_on_rows_in_window": len(acted_in_window),
            "outcomes": acted_outcomes,
            "entries_matching_a_flagged_pr": len(matches),
            "flagged_prs_named_by_an_acted_on_entry": len(matched_keys),
            "matches": [
                {"ts": m["entry"]["ts"].isoformat(),
                 "loop": m["entry"]["loop"],
                 "item": m["entry"]["item"],
                 "outcome": m["entry"]["outcome"],
                 "keys": [f"{grouped[k][0]['repo']}#{k[1]}" for k in m["keys"]]}
                for m in matches
            ],
        },
        "union": {
            "flagged_prs": flagged_pr_count,
            "acked_or_acted_on_prs": union_count,
            "pct": round(100.0 * union_count / flagged_pr_count, 1)
            if flagged_pr_count else 0.0,
            "flagged_prs_with_no_signal": flagged_pr_count - union_count,
        },
        "per_repo": repo_rows,
        "per_pr": pr_rows,
        "pr_rows": pr_rows,
    }
    payload["decision"] = read_decision(payload)
    return payload


def read_decision(payload: dict) -> str:
    """The Kill / Proceed read for F2, straight from the numbers.

    Kill criterion (issue #147 Part F2): kill F2 if the chat channel
    is demonstrably not losing anything -- repeats are rare AND flagged
    items reach a terminal disposition (or an acted-on entry) promptly.
    Either half failing means the channel IS losing something.
    """
    repeat_rate = payload["repeats"]["rate_pct"] / 100.0
    terminal_rate = payload["ack_proxy_a_terminal"]["pr_level_pct"] / 100.0
    latency = payload["ack_proxy_a_terminal"]["hours_first_flag_to_terminal"]
    median_hours = latency["median"]
    repeats_rare = repeat_rate <= RARE_REPEAT_RATE
    terminal_prompt = terminal_rate >= PROMPT_TERMINAL_RATE and (
        median_hours is not None and median_hours <= PROMPT_TERMINAL_HOURS)
    checks = {
        "repeats_rare": repeats_rare,
        "terminal_prompt": terminal_prompt,
        "repeat_rate_pct": payload["repeats"]["rate_pct"],
        "terminal_rate_pct": payload["ack_proxy_a_terminal"]["pr_level_pct"],
        "median_hours_to_terminal": median_hours,
    }
    if repeats_rare and terminal_prompt:
        verdict = (
            "KILL F2: the chat channel is not demonstrably losing anything "
            f"-- repeats are rare ({payload['repeats']['rate_pct']}% <= "
            f"{RARE_REPEAT_RATE * 100:.0f}%) and flagged items reach a "
            f"terminal disposition promptly "
            f"({payload['ack_proxy_a_terminal']['pr_level_pct']}% of flagged "
            f"PRs, median {median_hours:.1f}h)."
            if median_hours is not None else
            "KILL F2: repeats are rare and flagged items reach a terminal "
            "disposition promptly.")
    else:
        failed = []
        if not repeats_rare:
            failed.append(
                f"repeats are NOT rare ({payload['repeats']['rate_pct']}% of "
                f"flag rows repeat an already-open question, threshold "
                f"{RARE_REPEAT_RATE * 100:.0f}%)")
        if not terminal_prompt:
            failed.append(
                "flagged items do NOT all reach a terminal disposition "
                f"promptly ({payload['ack_proxy_a_terminal']['pr_level_pct']}% "
                f"of flagged PRs, median "
                f"{'n/a' if median_hours is None else format(median_hours, '.1f') + 'h'})")
        verdict = ("PROCEED F2: the chat channel IS losing something -- "
                   + "; ".join(failed) + ".")
    return verdict + "  [checks: " + json.dumps(checks, sort_keys=True) + "]"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return out


def _fmt(value, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(payload: dict) -> str:
    """Render the census as markdown tables (raw counts first)."""
    window = payload["window"]
    flags = payload["flags"]
    repeats = payload["repeats"]
    proxy_a = payload["ack_proxy_a_terminal"]
    proxy_b = payload["ack_proxy_b_acted_on"]
    union = payload["union"]

    out: list[str] = []
    out.append("# F2 questions census -- shepherd flags / Alex-facing "
               "questions, Phase 0 (measurement only)\n")
    out.append(f"Window: **{window['start']} .. {window['end']}** "
               f"(last {window['weeks']} weeks present in the ledger "
               f"snapshot).  Ledger rows: {window['ledger_rows_total']} "
               f"total, {window['ledger_rows_in_window']} in window.  The "
               f"snapshot itself spans {window['snapshot_span_days']} days, "
               f"so it is "
               f"{'shorter' if not window['snapshot_covers_whole_window'] else 'at least as long'}"
               f" as the {window['weeks']}-week window"
               f"{' -- every row in the snapshot is in the window' if not window['snapshot_covers_whole_window'] else ''}"
               ".\n")

    out.append("## Table 1 -- ledger action vocabulary (in window)\n")
    out.extend(_table(
        ["action", "rows", "share %", "census role"],
        [[v["action"], v["rows"], v["share_pct"], v["role"]]
         for v in payload["action_vocabulary"]]))
    out.append("")

    out.append("## Table 2 -- flags raised (in window)\n")
    out.extend(_table(
        ["flag action", "flag rows", "share % of flags"],
        [[b["action"], b["flags"], b["share_pct"]] for b in flags["by_action"]]))
    out.append("")
    out.append(f"Total flag rows: **{flags['total']}**  |  distinct "
               f"repo+PR flagged: **{flags['distinct_prs']}**  |  distinct "
               f"(repo, PR, action, outcome) question texts: "
               f"**{flags['distinct_questions']}**.")
    out.append("")

    out.append("## Table 3 -- repeats of an already-open question\n")
    out.extend(_table(
        ["flag action", "flag rows", "repeats", "repeat rate %"],
        [[b["action"], b["flags"], b["repeats"], b["repeat_rate_pct"]]
         for b in repeats["by_action"]]))
    out.append("")
    out.append(f"Repeats overall: **{repeats['total']}** of "
               f"**{flags['total']}** flag rows "
               f"(**{repeats['rate_pct']}%**).  Of those repeats, "
               f"{repeats['same_outcome_text_as_open']} carry the SAME "
               f"outcome text as the still-open question and "
               f"{repeats['different_outcome_text']} carry a DIFFERENT "
               f"outcome text (a new reason on a PR whose question was "
               f"still open).")
    out.append("")
    out.append("Per-PR repeat leaders (top 15):")
    out.extend(_table(
        ["repo", "PR", "flag rows", "repeats", "first flag", "last flag"],
        [[r["repo"], r["pr"], r["flags"], r["repeats"], r["first_flag"],
          r["last_flag"]] for r in repeats["per_pr"][:15]]))
    out.append("")

    out.append("## Table 4 -- ack proxy A: terminal disposition after the "
               "flag\n")
    out.extend(_table(
        ["measure", "value"],
        [["flag rows with a later terminal disposition",
          proxy_a["flags_with_later_terminal"]],
         ["flag rows with no later terminal disposition",
          proxy_a["flags_without_later_terminal"]],
         ["flag-level resolution %", proxy_a["flag_level_pct"]],
         ["flagged PRs that reached a terminal disposition",
          proxy_a["prs_resolved"]],
         ["flagged PRs still open at window end", proxy_a["prs_still_open"]],
         ["PR-level resolution %", proxy_a["pr_level_pct"]],
         ["median hours first flag -> terminal",
          _fmt(proxy_a["hours_first_flag_to_terminal"]["median"])],
         ["p90 hours first flag -> terminal",
          _fmt(proxy_a["hours_first_flag_to_terminal"]["p90"])],
         ["max hours first flag -> terminal",
          _fmt(proxy_a["hours_first_flag_to_terminal"]["max"])],
         [f"flagged PRs terminal within {int(PROMPT_TERMINAL_HOURS)}h",
          proxy_a["prs_terminal_within_24h"]]]))
    out.append("")

    out.append("## Table 5 -- ack proxy B: acted-on log cross-check\n")
    out.extend(_table(
        ["measure", "value"],
        [["acted-on rows in the snapshot", proxy_b["acted_on_rows_total"]],
         ["acted-on rows inside the window", proxy_b["acted_on_rows_in_window"]],
         ["acted-on entries naming a flagged repo+PR",
          proxy_b["entries_matching_a_flagged_pr"]],
         ["flagged PRs named by an acted-on entry",
          proxy_b["flagged_prs_named_by_an_acted_on_entry"]]]))
    out.append("")
    out.append("Acted-on entries in the window that name a flagged PR:")
    out.extend(_table(
        ["ts", "loop", "item", "outcome", "flagged PRs"],
        [[m["ts"], m["loop"], m["item"], m["outcome"], ", ".join(m["keys"])]
         for m in proxy_b["matches"]]))
    out.append("")

    out.append("## Table 6 -- union of the two ack proxies (upper bound)\n")
    out.extend(_table(
        ["measure", "value"],
        [["flagged PRs", union["flagged_prs"]],
         ["flagged PRs with terminal disposition OR acted-on entry",
          union["acked_or_acted_on_prs"]],
         ["union %", union["pct"]],
         ["flagged PRs with no signal from either proxy",
          union["flagged_prs_with_no_signal"]]]))
    out.append("")
    out.append("Ack proxy definitions (stated, not smoothed over):")
    out.append(f"  - proxy A: {payload['definitions']['ack_proxy_a']}.")
    out.append(f"  - proxy B: {payload['definitions']['ack_proxy_b']}.")
    out.append(f"  - {payload['definitions']['ack_note']}.")
    out.append("")

    out.append("## Table 7 -- per-repo flag / repeat / disposition summary\n")
    out.extend(_table(
        ["repo", "flag rows", "PRs", "repeats", "repeat rate %",
         "PRs with terminal"],
        [[r["repo"], r["flags"], r["prs"], r["repeats"], r["repeat_rate_pct"],
          r["prs_with_terminal"]] for r in payload["per_repo"]]))
    out.append("")

    out.append("## Table 8 -- flagged PRs still open at window end\n")
    still_open = [r for r in payload["per_pr"]
                  if r["terminal_disposition"] == "none (still open at window end)"]
    if still_open:
        out.extend(_table(
            ["repo", "PR", "flag rows", "repeats", "disposition"],
            [[r["repo"], r["pr"], r["flags"], r["repeats"], r["terminal_disposition"]]
             for r in still_open]))
    else:
        out.append("(none -- every flagged PR reached a terminal disposition)")
    out.append("")

    out.append("## Verdict\n")
    out.append(payload["decision"])
    out.append("")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", required=True,
                    help="path to the shepherd ledger JSONL snapshot")
    ap.add_argument("--acted-on", required=True,
                    help="path to the acted-on JSONL snapshot")
    ap.add_argument("--weeks", type=int, default=DEFAULT_WEEKS,
                    help=f"window length in weeks (default {DEFAULT_WEEKS})")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine payload as JSON on stdout")
    args = ap.parse_args(argv)

    try:
        ledger_rows = load_ledger(args.ledger)
        acted_rows = load_acted_on(args.acted_on)
    except OSError as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 1

    payload = build_census(ledger_rows, acted_rows, weeks=args.weeks)
    if args.json:
        print(json.dumps(payload, indent=1, sort_keys=False, default=str))
    else:
        print(render_markdown(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
