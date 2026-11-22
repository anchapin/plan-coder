#!/usr/bin/env python3
"""Phase-0 measurement for plan-coder issue #148 (same-signature recurrence).

Phase 0 is measurement only.  Nothing here is wired into the live harness:
no prompt change, no ``bin/agent.py`` change, no halt/escalate rule, no
config change.  The script only reads a transcript corpus and prints
measurements.

The failure signature is *not* re-implemented here.  It is imported from
``bin/tokeneconomizer_phase0.py`` (``failure_signature``), together with
the normalisation helpers that script already ships (``normalize_command``,
``path_tokens``, ``edit_touches``) and the transcript loader
(``load_run`` / ``load_corpus``).  One signature therefore means exactly
what it means in the TokenEconomizer spike.

The script answers the three Phase-0 questions of issue #148:

  Q1  How often does the *same* failure signature occur 2+ times within one
      run?  (per-run recurrence census)

  Q2  For those runs: how many attempts were made after the second
      occurrence, roughly how many tokens were spent after it, and did the
      run eventually succeed?

  Q3  False-positive shape: how often does a repeated signature look
      benign (flaky / environment noise / a re-test after a relevant fix)
      versus a genuine symptom-loop (same command, same failure, nothing
      relevant changed in between)?

Usage:
    python3 bin/samesig_phase0.py --transcripts DIR
    python3 bin/samesig_phase0.py --transcripts DIR --json

Options:
    --transcripts DIR   Transcript corpus to replay (read-only).  Required:
                        the real corpus lives outside this repository, so
                        the script never guesses a path.
    --json              Emit the measurements as JSON instead of tables.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokeneconomizer_phase0 import (
    FILE_TOOLS,
    edit_touches,
    failure_signature,
    load_corpus,
    normalize_command,
    path_tokens,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Signatures whose text names a transient environment condition.  A repeated
# signature that matches one of these is *probably* environment noise rather
# than a symptom of the code under repair -- but only when it is not also a
# same-command loop (that check has priority).
NOISE_PATTERNS = (
    "timed out",
    "timeout",
    "timedout",
    "econnreset",
    "etimedout",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
    "resource temporarily",
    "cannot resolve host",
    "temporary failure in name resolution",
    "network is unreachable",
    "no space left on device",
    "too many open files",
    "address already in use",
    "rate limit",
    "too many requests",
    "out of memory",
    "oom killer",
    "broken pipe",
    "read-only file system",
    "input/output error",
    "database is locked",
    "stale file handle",
    "no such device",
    "device or resource busy",
    "killed",
)

# Classification labels.
CLS_LOOP = "symptom_loop"
CLS_NOISE = "env_noise"
CLS_PROGRESS = "progress_after_edit"
CLS_OTHER = "benign_other"

CLASS_ORDER = (CLS_LOOP, CLS_PROGRESS, CLS_NOISE, CLS_OTHER)


# --------------------------------------------------------------------------
# transcript reading (reuses the TokenEconomizer loader)
# --------------------------------------------------------------------------


def read_finished_event(path):
    """Return the ``finished`` event of one transcript, or None.

    ``load_run`` keeps the token totals of the ``finished`` event but not
    its file list; the run outcome of this script needs
    ``files_changed`` / ``token_cap``, so the event is read separately
    with the same jsonl conventions as ``load_run``.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("event") == "finished":
                    return obj
    except OSError:
        return None
    return None


def load_corpus_with_outcomes(transcripts_dir):
    """Load every transcript in the corpus, with its finished-event facts.

    Returns a list of ``(run, finished_event)`` pairs.  The run objects are
    built by the TokenEconomizer loader (``load_corpus`` / ``load_run``), so
    the step / call model and the token fields are exactly the ones the
    existing spike measures.
    """
    pairs = []
    if not os.path.isdir(transcripts_dir):
        return pairs
    runs = load_corpus(transcripts_dir)
    for run in runs:
        path = os.path.join(transcripts_dir, run.path)
        pairs.append((run, read_finished_event(path)))
    return pairs


def run_outcome(run, finished):
    """Classify how a run ended, from the transcript alone.

    ``no_finished_event``  the transcript has no ``finished`` record (the
                           run was cut off before it reported).
    ``token_cap``          the run stopped on the output-token guard.
    ``edited``             the run finished having changed at least one
                           file -- the transcript-local proxy for "the run
                           produced a diff", i.e. it eventually succeeded.
    ``no_edit``            the run finished without changing a file.
    """
    if finished is None:
        return "no_finished_event"
    if finished.get("token_cap"):
        return "token_cap"
    if finished.get("files_changed"):
        return "edited"
    return "no_edit"


def outcome_succeeded(outcome):
    """True when the outcome proxy says the run eventually succeeded."""
    return outcome == "edited"


# --------------------------------------------------------------------------
# recurrence detection
# --------------------------------------------------------------------------


class Occurrence:
    """One failing command call inside a run."""

    def __init__(self, position, step_index, call_index, command, result,
                 related):
        self.position = position          # position of the step in the run
        self.step_index = step_index      # the step's own index field
        self.call_index = call_index      # position of the call inside the step
        self.command = command            # normalised command
        self.result = result              # raw tool result
        self.related = related            # path-ish tokens of the failure


class Recurrence:
    """A failure signature that occurs 2+ times inside one run."""

    def __init__(self, run, signature, occurrences):
        self.run = run
        self.signature = signature
        self.occurrences = occurrences
        self.kind = CLS_OTHER
        self.reason = ""
        self.loop_shaped = False
        self.tokens_out_after = 0
        self.tokens_in_after = 0
        self.attempts_after = 0
        self.failing_attempts_after = 0
        self.same_signature_attempts_after = 0

    @property
    def count(self):
        return len(self.occurrences)

    @property
    def second_position(self):
        """Position of the step carrying the *second* occurrence."""
        return self.occurrences[1].position

    @property
    def last_position(self):
        return self.occurrences[-1].position


def collect_occurrences(run):
    """Map failure signature -> list of Occurrence, in step order.

    Only command results carry a failure signature (a successful command
    has none), so a signature is always a *failure* repeated.
    """
    table = defaultdict(list)
    for position, step in enumerate(run.steps):
        for call_index, call in enumerate(step.calls):
            signature = failure_signature(call.result)
            if not signature:
                continue
            raw_command = call.args.get("command") or call.args.get("cmd") or ""
            raw_command = str(raw_command)
            related = path_tokens(raw_command) | path_tokens(call.result)
            table[signature].append(Occurrence(
                position=position,
                step_index=step.index,
                call_index=call_index,
                command=normalize_command(raw_command),
                result=call.result,
                related=related))
    return table


def edit_events(run):
    """Every file edit of the run as (position, call_index, path).

    Ordering matters: an edit that lands *after* the second failing call
    of a step is not a fix that came before the repeat.
    """
    events = []
    for position, step in enumerate(run.steps):
        for call_index, call in enumerate(step.calls):
            if call.name not in FILE_TOOLS:
                continue
            path = call.args.get("path") or call.args.get("file") or ""
            if path:
                events.append((position, call_index, str(path)))
    return events


def edits_between(run, first, second):
    """Paths edited between two Occurrence objects (exclusive)."""
    start = (first.position, first.call_index)
    stop = (second.position, second.call_index)
    return [path for position, call_index, path in edit_events(run)
            if start < (position, call_index) < stop]


def looks_like_noise(signature):
    """True when the signature names a transient environment condition."""
    text = signature.lower()
    return any(pattern in text for pattern in NOISE_PATTERNS)


def classify_recurrence(run, recurrence):
    """Classify a repeated signature: benign-looking or a symptom-loop.

    Two orthogonal facts are recorded:

    ``loop_shaped``
        True when two consecutive occurrences ran the *same* normalised
        command and nothing related to the failure was edited in between
        -- the worker re-ran the same thing and got the same failure.

    ``kind``
        The primary label, in priority order:

        1. *progress_after_edit* -- the worker edited something related to
           the failure between the occurrences: the repeat is a re-test
           after a fix, not a loop.
        2. *env_noise* -- the signature text names a transient environment
           condition (timeout, connection reset, ...).
        3. *symptom_loop* -- same command, same failure, nothing relevant
           changed: a genuine symptom-loop.
        4. *benign_other* -- two different commands collapsed onto the
           same normalised failure text, with no relevant edit in between.
    """
    edited_between = False
    for first, second in zip(recurrence.occurrences, recurrence.occurrences[1:]):
        related = first.related | second.related
        if any(edit_touches(edit, related)
               for edit in edits_between(run, first, second)):
            edited_between = True
            continue
        if first.command and first.command == second.command:
            recurrence.loop_shaped = True
    if recurrence.loop_shaped:
        if looks_like_noise(recurrence.signature):
            recurrence.kind = CLS_NOISE
            recurrence.reason = (
                "same command re-ran with the same failure, but the failure "
                "text names a transient environment condition")
        else:
            recurrence.kind = CLS_LOOP
            recurrence.reason = (
                "same command re-ran with the same failure and no relevant "
                "edit in between")
        return recurrence
    if edited_between:
        recurrence.kind = CLS_PROGRESS
        recurrence.reason = (
            "a relevant file was edited between the occurrences "
            "(repeat is a re-test, not a loop)")
        return recurrence
    if looks_like_noise(recurrence.signature):
        recurrence.kind = CLS_NOISE
        recurrence.reason = "signature names a transient environment condition"
        return recurrence
    recurrence.kind = CLS_OTHER
    recurrence.reason = "different commands collapsed onto the same signature"
    return recurrence


def token_totals(run, after_position):
    """Tokens spent in the steps *after* ``after_position``."""
    tokens_in = 0
    tokens_out = 0
    for step in run.steps[after_position + 1:]:
        tokens_in += step.tokens_in
        tokens_out += step.tokens_out
    return tokens_in, tokens_out


def calls_after(run, after_position):
    """(all command calls, failing command calls) made after a position."""
    total = 0
    failing = 0
    for position, step in enumerate(run.steps):
        if position <= after_position:
            continue
        for call in step.commands:
            total += 1
            if failure_signature(call.result):
                failing += 1
    return total, failing


def analyse_run(run):
    """Return the recurrences of one run (signatures seen 2+ times)."""
    table = collect_occurrences(run)
    recurrences = []
    for signature, occurrences in table.items():
        if len(occurrences) < 2:
            continue
        recurrence = Recurrence(run, signature, occurrences)
        recurrence.tokens_in_after, recurrence.tokens_out_after = \
            token_totals(run, recurrence.second_position)
        recurrence.attempts_after, recurrence.failing_attempts_after = \
            calls_after(run, recurrence.second_position)
        recurrence.same_signature_attempts_after = len(occurrences) - 2
        classify_recurrence(run, recurrence)
        recurrences.append(recurrence)
    recurrences.sort(key=lambda item: (-item.count, item.signature))
    return recurrences


# --------------------------------------------------------------------------
# corpus-level measurement
# --------------------------------------------------------------------------


def measure(pairs):
    """Measure the whole corpus and return the JSON-able payload."""
    runs = []
    for run, finished in pairs:
        outcome = run_outcome(run, finished)
        recurrences = analyse_run(run)
        if recurrences:
            first_second = min(rec.second_position for rec in recurrences)
            tokens_in_after, tokens_out_after = token_totals(run, first_second)
            attempts_after, failing_after = calls_after(run, first_second)
            same_signature_after = sum(rec.same_signature_attempts_after
                                      for rec in recurrences)
        else:
            first_second = None
            tokens_in_after = 0
            tokens_out_after = 0
            attempts_after = 0
            failing_after = 0
            same_signature_after = 0
        runs.append({
            "run_id": run.run_id,
            "path": run.path,
            "repo": run.repo,
            "issue": run.issue,
            "steps": len(run.steps),
            "tokens_in": run.total_in,
            "tokens_out": run.total_out,
            "outcome": outcome,
            "succeeded": outcome_succeeded(outcome),
            "recurring_signatures": len(recurrences),
            "recurrence_occurrences": sum(rec.count for rec in recurrences),
            "attempts_after_second": attempts_after,
            "failing_attempts_after_second": failing_after,
            "same_signature_attempts_after_second": same_signature_after,
            "tokens_out_after_second": tokens_out_after,
            "tokens_in_after_second": tokens_in_after,
            "tokens_after_second": tokens_in_after + tokens_out_after,
            "recurrences": [{
                "signature": rec.signature,
                "occurrences": rec.count,
                "first_step": rec.occurrences[0].step_index,
                "second_step": rec.occurrences[1].step_index,
                "last_step": rec.occurrences[-1].step_index,
                "kind": rec.kind,
                "reason": rec.reason,
                "loop_shaped": rec.loop_shaped,
                "attempts_after_second": rec.attempts_after,
                "failing_attempts_after_second": rec.failing_attempts_after,
                "same_signature_attempts_after_second":
                    rec.same_signature_attempts_after,
                "tokens_out_after_second": rec.tokens_out_after,
                "tokens_in_after_second": rec.tokens_in_after,
                "tokens_after_second": (rec.tokens_in_after
                                        + rec.tokens_out_after),
            } for rec in recurrences],
        })

    with_recurrence = [r for r in runs if r["recurring_signatures"] > 0]
    kind_counts = Counter()
    for run in runs:
        for rec in run["recurrences"]:
            kind_counts[rec["kind"]] += 1

    occurrence_histogram = Counter()
    for run in runs:
        for rec in run["recurrences"]:
            occurrence_histogram[rec["occurrences"]] += 1

    per_run_attempts = [r["attempts_after_second"] for r in with_recurrence]
    per_run_tokens = [r["tokens_after_second"] for r in with_recurrence]

    def stat(values):
        if not values:
            return {"count": 0, "total": 0, "mean": 0.0, "median": 0.0,
                    "min": 0, "max": 0}
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            median = ordered[middle]
        else:
            median = (ordered[middle - 1] + ordered[middle]) / 2
        return {
            "count": len(ordered),
            "total": sum(ordered),
            "mean": sum(ordered) / len(ordered),
            "median": median,
            "min": ordered[0],
            "max": ordered[-1],
        }

    total_recurring = sum(kind_counts.values())
    loop_count = kind_counts.get(CLS_LOOP, 0)

    payload = {
        "runs_scanned": len(runs),
        "runs_with_recurrence": len(with_recurrence),
        "runs_with_recurrence_pct": (
            100.0 * len(with_recurrence) / len(runs) if runs else 0.0),
        "recurring_signature_groups": total_recurring,
        "q1": {
            "runs_scanned": len(runs),
            "runs_with_recurrence": len(with_recurrence),
            "runs_with_recurrence_pct": (
                100.0 * len(with_recurrence) / len(runs) if runs else 0.0),
            "recurring_signature_groups": total_recurring,
            "occurrence_histogram": {
                str(k): occurrence_histogram[k]
                for k in sorted(occurrence_histogram)
            },
        },
        "q2": {
            "runs_with_recurrence": len(with_recurrence),
            "attempts_after_second": stat(per_run_attempts),
            "tokens_after_second": stat(per_run_tokens),
            "succeeded": sum(1 for r in with_recurrence if r["succeeded"]),
            "not_succeeded": sum(1 for r in with_recurrence
                                 if not r["succeeded"]),
            "outcomes": dict(Counter(r["outcome"] for r in with_recurrence)),
        },
        "q3": {
            "by_class": {kind: kind_counts.get(kind, 0) for kind in CLASS_ORDER},
            "loop_shaped_groups": sum(
                1 for run in runs for rec in run["recurrences"]
                if rec["loop_shaped"]),
            "loop_pct_of_repeated": (
                100.0 * loop_count / total_recurring if total_recurring else 0.0),
            "benign_pct_of_repeated": (
                100.0 * (total_recurring - loop_count) / total_recurring
                if total_recurring else 0.0),
        },
        "runs": runs,
    }
    return payload


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _short(text, width=70):
    text = " ".join(str(text).split())
    if len(text) <= width:
        return text
    return text[:width - 1] + "…"


def render_table(payload):
    """Render the three measurements as readable tables."""
    out = []
    add = out.append

    add("Same-signature recurrence -- Phase-0 measurement (issue #148)")
    add("=" * 72)
    add("")

    q1 = payload["q1"]
    add("Q1  How often does the same failure signature occur 2+ times in one run?")
    add("-" * 72)
    add(f"runs scanned                  : {q1['runs_scanned']}")
    add(f"runs with >=1 recurrence      : {q1['runs_with_recurrence']} "
        f"({q1['runs_with_recurrence_pct']:.1f}%)")
    add(f"recurring signature groups    : {q1['recurring_signature_groups']}")
    if q1["occurrence_histogram"]:
        add("occurrences per repeated signature:")
        for count, groups in q1["occurrence_histogram"].items():
            add(f"    {count} occurrences -> {groups} group(s)")
    else:
        add("occurrences per repeated signature: (none)")
    add("")

    q2 = payload["q2"]
    add("Q2  Cost after the second occurrence (runs with a recurrence)")
    add("-" * 72)
    attempts = q2["attempts_after_second"]
    tokens = q2["tokens_after_second"]
    add(f"runs measured                 : {q2['runs_with_recurrence']}")
    add(f"failing attempts after 2nd    : total {attempts['total']}, "
        f"mean {attempts['mean']:.2f}, median {attempts['median']:.1f}, "
        f"max {attempts['max']}")
    add(f"tokens after 2nd (in+out)     : total {tokens['total']}, "
        f"mean {tokens['mean']:.1f}, median {tokens['median']:.1f}, "
        f"max {tokens['max']}")
    add(f"runs that eventually succeeded: {q2['succeeded']} "
        f"(not succeeded: {q2['not_succeeded']})")
    if q2["outcomes"]:
        add("outcome mix                   : "
            + ", ".join(f"{k}={v}" for k, v in sorted(q2["outcomes"].items())))
    add("")
    add("per-run detail")
    add("")
    add("| run | repo | rec sigs | attempts after 2nd | tokens after 2nd | "
        "outcome | succeeded |")
    add("|---|---|---:|---:|---:|---|---|")
    for run in payload["runs"]:
        if run["recurring_signatures"] == 0:
            continue
        add(f"| {run['run_id']} | {run['repo'] or '-'} | "
            f"{run['recurring_signatures']} | "
            f"{run['attempts_after_second']} | "
            f"{run['tokens_after_second']} | {run['outcome']} | "
            f"{'yes' if run['succeeded'] else 'no'} |")
    add("")

    q3 = payload["q3"]
    add("Q3  False-positive shape of a repeated signature")
    add("-" * 72)
    add("| class | groups | share of repeated signatures | meaning |")
    add("|---|---:|---:|---|")
    meanings = {
        CLS_LOOP: "same command, same failure, no relevant edit -- genuine "
                  "symptom-loop",
        CLS_PROGRESS: "a relevant file was edited between the occurrences -- "
                      "re-test, not a loop",
        CLS_NOISE: "signature names a transient environment condition "
                   "(timeout, connection reset, ...)",
        CLS_OTHER: "different commands collapsed onto the same normalised "
                   "failure text",
    }
    total = payload["recurring_signature_groups"] or 0
    for kind in CLASS_ORDER:
        groups = q3["by_class"].get(kind, 0)
        share = (100.0 * groups / total) if total else 0.0
        add(f"| {kind} | {groups} | {share:.1f}% | {meanings[kind]} |")
    add("")
    add("per-recurrence detail")
    add("")
    add("| run | occurrences | class | signature |")
    add("|---|---:|---|---|")
    for run in payload["runs"]:
        for rec in run["recurrences"]:
            add(f"| {run['run_id']} | {rec['occurrences']} | {rec['kind']} | "
                f"{_short(rec['signature'])} |")
    add("")
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phase-0 measurement for plan-coder issue #148 "
                    "(same-signature recurrence).")
    parser.add_argument("--transcripts", required=True,
                        help="transcript corpus to replay (read-only)")
    parser.add_argument("--json", action="store_true",
                        help="emit the measurements as JSON instead of tables")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.transcripts):
        print(f"error: transcript corpus not found: {args.transcripts}",
              file=sys.stderr)
        return 2

    pairs = load_corpus_with_outcomes(args.transcripts)
    payload = measure(pairs)

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(render_table(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
