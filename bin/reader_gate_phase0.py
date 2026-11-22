#!/usr/bin/env python3
"""Phase-0 census for issue #152: the weakest-link reader gate (Part A)
and the compute-matched multi-agent baseline (Part B).

Phase 0 is measurement only.  Nothing here is wired into the live
harness: no prompt change, no gate, no halt/escalate rule, no config
change, no ledger format change, no LLM call at run time.  The script
reads snapshots and prints measurements.  Phase 1 (the gate itself) is
a separate tap and is gated on the Phase 0 verdicts printed here.

Part A -- the weakest-link reader gate
--------------------------------------
Question: when a summary is *acted on* (it reached a decision), how
often does re-reading the summary's cited sources contradict the
summary?  A reader gate would only pay for itself if that rate is
non-trivial; if summaries are faithful, the gate is pure cost.

Inputs (both read-only, both given on the command line -- the real
operator state dir lives outside this repository and is NEVER guessed):

  --ledger      a worker/shepherd ledger (JSONL, one row per run).
  --premises    a premises file: the acted-on summary claims with the
                sources each one cites.  JSONL (one object per line) or
                a JSON array.
  --sources-root  the directory relative source paths are resolved
                against (default: the current directory).

A premise row looks like:

  {"premise_id": "p-1",
   "summary_id": "run-123",          # joins to the ledger's run_id
   "claim": "the ledger append is fsync'd before the merge",
   "sources": [{"path": "docs/x.md", "quote": "fsync'd"}],
   "acted_on": true,                 # optional; defaults to the ledger
   "decision": "merge"}              # optional; "merge" = the summary
                                     # reached a merge decision

Each cited source is RE-READ from disk and the claim is scored:

  supported    the quoted text is present in the source (whitespace and
               case normalised).
  contradicted one of the source's `contradicts` strings is present in
               the source -- the source says the opposite of the claim.
  unsupported  the source was readable but the quoted text is absent.
  unanchored   the citation carries no `quote`, so the re-read cannot
               check it.
  unreadable   the source file could not be read.

Premise verdict (the weakest link of the claim wins):

  corrupted    at least one cited source is contradicted.
  incomplete   no contradiction, but at least one citation is
               unsupported / unanchored / unreadable.
  accurate     every citation was readable and supported.

Divergence = (incomplete + corrupted) / acted-on premises.

The Part A gate, as stated in issue #152:

  PROCEED  divergence >= 5% on acted-on premises, OR any corrupted
           premise reached a merge decision.
  KILL     divergence < 2% AND zero corrupted premises reached a merge
           decision.
  HOLD     anything in between (2% <= divergence < 5% and no corrupted
           premise reached a merge decision).

Part B -- the compute-matched multi-agent baseline
---------------------------------------------------
Question: does a multi-agent dispatch that shares evidence between
agents beat the nearest comparable single-agent dispatch at the SAME
compute?  If multi-agent only wins while spending more tokens, the
baseline is not a win and the reader gate's motivation weakens.

Input: --ledger (the same worker ledger format; every row is one
dispatch).  Each dispatch is classified into exactly one class by
documented, deterministic heuristics (first match wins):

  shared-evidence-communication  the dispatch records evidence or
                                 findings shared between agents
                                 (`shared_evidence`, `evidence_shared`,
                                 `shared_findings`, `communication`,
                                 `agent_messages`, `cross_agent_notes`).
  external-verification         the dispatch verifies against an
                                 external source (`external_source`,
                                 `verified_against`, `verification_source`,
                                 `external_verification`).
  model-diversity               the dispatch used more than one provider
                                 or model tier (`providers_used` with more
                                 than one entry, `tiers_used` with more
                                 than one entry, `model_diversity`).
  partitioned-evidence          everything else -- one agent (or several
                                 agents) each working on its own slice of
                                 the evidence with nothing shared.

The comparator for the shared-evidence class is the nearest comparable
SINGLE-agent dispatch: single-agent rows in the same repo when there
are any, otherwise single-agent rows from any repo (the report says
which was used).

The Part B gate, as stated in issue #152:

  PROCEED  the shared-evidence class shows a measurable win over the
           comparators: a strictly higher success rate at no more
           tokens_out per dispatch.
  KILL     no measurable win (success rate not higher, or more
           tokens_out per dispatch than the comparators).
  HOLD     the class or its comparator is empty -- not measurable.

Usage:
    python3 bin/reader_gate_phase0.py reader \
        --ledger phase0_inputs/worker_ledger.jsonl \
        --premises phase0_inputs/premises.jsonl \
        --sources-root /path/to/repo
    python3 bin/reader_gate_phase0.py multiagent \
        --ledger phase0_inputs/worker_ledger.jsonl
    python3 bin/reader_gate_phase0.py reader ... --json

Exit 0 on success; exit 1 when an input file cannot be read.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter

CHARS_PER_TOKEN = 4.0


TIERS = ("cheap", "balanced", "flagship")  # harness tiers (bin/agent.py)

ACTED_ON_OUTCOMES = frozenset({  # the run's output was consumed
    "pr_opened", "merged", "merged_by_shepherd", "accepted", "approved",
    "deployed", "closed_unmerged",
})

MERGE_OUTCOMES = frozenset({"merged", "merged_by_shepherd"})  # a decision

SUCCESS_OUTCOMES = frozenset({
    "pr_opened", "merged", "merged_by_shepherd", "succeeded", "success",
    "accepted", "approved", "deployed",
})
FAILURE_OUTCOMES = frozenset({
    "failed", "failed_no_pr", "error", "timeout", "aborted", "crashed",
    "stopped:token-cap", "stopped:max-steps",
})


def read_jsonl(path: str) -> tuple[list[dict], int]:
    """Read a JSONL file into row dicts.

    Returns ``(rows, skipped)``; unparseable or non-object lines are
    skipped and counted, never fatal (a real ledger may carry a
    truncated last line), and the count is reported so a silent data
    loss cannot masquerade as a clean census.
    """
    rows: list[dict] = []
    skipped = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(row, dict):
                skipped += 1
                continue
            rows.append(row)
    return rows, skipped


def read_rows(path: str) -> tuple[list[dict], int]:
    """Read a JSONL *or* JSON-array snapshot into row dicts."""
    with open(path, encoding="utf-8") as handle:
        head = handle.read(4096)
        handle.seek(0)
        if head.lstrip().startswith("["):
            try:
                payload = json.load(handle)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}: unreadable JSON ({exc})")
            if not isinstance(payload, list):
                raise SystemExit(f"{path}: JSON payload is not an array")
            rows = [r for r in payload if isinstance(r, dict)]
            return rows, len(payload) - len(rows)
    return read_jsonl(path)


def as_int(value) -> int:
    """Best-effort int, 0 when the value is missing or not numeric."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def as_list(value) -> list:
    """Coerce a ledger field to a list (list, comma string, or empty)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if str(v).strip()]
    if isinstance(value, (int, float)):
        return [value]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def truthy(value) -> bool:
    """True for a non-empty string / list / dict / True."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {
            "false", "no", "none", "0"}
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return value != 0
    return True


def token_estimate(chars: int) -> int:
    """Token estimate from a character count (the repo's chars/4 rule)."""
    return int(math.ceil(chars / CHARS_PER_TOKEN))


def pct(numerator: int, denominator: int) -> float:
    """Ratio as a percentage, 0.0 when the denominator is empty."""
    if not denominator:
        return 0.0
    return round(100.0 * numerator / denominator, 2)


def mean(values: list[int]) -> float:
    """Arithmetic mean, 0.0 for an empty list."""
    if not values:
        return 0.0
    return round(sum(values) / len(values), 1)


def load_ledger(path: str) -> tuple[list[dict], int]:
    """Load a worker/shepherd ledger snapshot.

    The path always comes from the command line: the real operator
    state dir is outside this repository and is never hard-coded.
    """
    if not os.path.exists(path):
        raise SystemExit(f"ledger not found: {path}")
    rows, skipped = read_rows(path)
    loaded = []
    for row in rows:
        loaded.append({
            "run_id": str(row.get("run_id") or row.get("run") or ""),
            "repo": str(row.get("repo") or ""),
            "issue": as_int(row.get("issue")),
            "tier": str(row.get("tier") or "").strip().lower(),
            "outcome": str(row.get("outcome") or "").strip(),
            "tokens_in": as_int(row.get("tokens_in")),
            "tokens_out": as_int(row.get("tokens_out")),
            "tokens_total": as_int(row.get("tokens_total")),
            "pr_url": str(row.get("pr_url") or ""),
            "raw": row,
        })
    return loaded, skipped



VERDICT_ACCURATE = "accurate"
VERDICT_INCOMPLETE = "incomplete"
VERDICT_CORRUPTED = "corrupted"

DIVERGENCE_PROCEED = 5.0    # percent of acted-on premises (issue #152)
DIVERGENCE_KILL = 2.0       # percent of acted-on premises


def normalize(text) -> str:
    """Whitespace- and case-normalised text, for quote matching."""
    return " ".join(str(text or "").split()).lower()


def resolve_source_path(path: str, sources_root: str) -> str:
    """Resolve a cited source path against the sources root."""
    text = str(path or "").strip()
    if not text:
        return ""
    if os.path.isabs(text):
        return text
    return os.path.join(sources_root, text)


def read_source(path: str) -> tuple[str | None, int]:
    """Re-read one cited source.

    Returns ``(content, chars)``; ``content`` is None when the file
    cannot be read at all.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return None, 0
    return content, len(content)


def score_source(source: dict, sources_root: str) -> dict:
    """Re-read one cited source and score it against the claim."""
    path = str(source.get("path") or "")
    resolved = resolve_source_path(path, sources_root)
    quote = str(source.get("quote") or "")
    contradicts = as_list(source.get("contradicts"))
    result = {
        "path": path,
        "resolved": resolved,
        "status": "unreadable",
        "chars": 0,
        "tokens": 0,
    }
    if not resolved:
        result["status"] = "unreadable"
        return result
    content, chars = read_source(resolved)
    result["chars"] = chars
    result["tokens"] = token_estimate(chars)
    if content is None:
        result["status"] = "unreadable"
        return result
    flat = normalize(content)
    for marker in contradicts:
        if marker and normalize(marker) in flat:
            result["status"] = "contradicted"
            return result
    if not quote.strip():
        result["status"] = "unanchored"
        return result
    if normalize(quote) in flat:
        result["status"] = "supported"
        return result
    result["status"] = "unsupported"
    return result


def score_premise(premise: dict, sources_root: str) -> dict:
    """Score one premise: the weakest cited source decides the verdict."""
    sources = premise.get("sources")
    if not isinstance(sources, list):
        sources = []
    scored = [score_source(src, sources_root)
              for src in sources if isinstance(src, dict)]
    statuses = [s["status"] for s in scored]
    if any(status == "contradicted" for status in statuses):
        verdict = VERDICT_CORRUPTED
    elif any(status in ("unsupported", "unanchored", "unreadable")
             for status in statuses):
        verdict = VERDICT_INCOMPLETE
    elif not scored:
        verdict = VERDICT_INCOMPLETE
    else:
        verdict = VERDICT_ACCURATE
    return {
        "verdict": verdict,
        "sources": scored,
        "tokens": sum(s["tokens"] for s in scored),
        "chars": sum(s["chars"] for s in scored),
    }


def premise_is_acted_on(premise: dict, ledger_row: dict | None) -> bool:
    """Was this premise acted on?

    An explicit ``acted_on`` flag on the premise wins; otherwise the
    joined ledger row's outcome decides.  A premise that joins to no
    ledger row and carries no flag is NOT counted as acted on.
    """
    flag = premise.get("acted_on")
    if isinstance(flag, bool):
        return flag
    if ledger_row is None:
        return False
    return ledger_row["outcome"] in ACTED_ON_OUTCOMES


def premise_reached_decision(premise: dict, ledger_row: dict | None) -> bool:
    """Did this premise reach a merge decision?"""
    decision = str(premise.get("decision") or "").strip().lower()
    if decision in ("merge", "merged"):
        return True
    if ledger_row is None:
        return False
    return ledger_row["outcome"] in MERGE_OUTCOMES


def index_ledger(ledger_rows: list[dict]) -> dict:
    """Index ledger rows by run_id (and by pr_url as a second key)."""
    index: dict[str, dict] = {}
    for row in ledger_rows:
        if row["run_id"]:
            index.setdefault("run:" + row["run_id"], row)
        if row["pr_url"]:
            index.setdefault("url:" + row["pr_url"], row)
    return index


def census_part_a(ledger_rows: list[dict], premises: list[dict],
                  sources_root: str) -> dict:
    """The Part A census: divergence by reader tier and token cost."""
    index = index_ledger(ledger_rows)
    scored = []
    for premise in premises:
        summary_id = str(premise.get("summary_id") or premise.get("run_id")
                         or "")
        ledger_row = (index.get("run:" + summary_id)
                      or index.get("url:" + summary_id))
        result = score_premise(premise, sources_root)
        tier = ""
        if ledger_row and ledger_row["tier"]:
            tier = ledger_row["tier"]
        elif premise.get("tier"):
            tier = str(premise.get("tier")).strip().lower()
        acted_on = premise_is_acted_on(premise, ledger_row)
        decision = premise_reached_decision(premise, ledger_row)
        scored.append({
            "premise_id": str(premise.get("premise_id") or ""),
            "summary_id": summary_id,
            "claim": str(premise.get("claim") or ""),
            "tier": tier or "unknown",
            "joined": ledger_row is not None,
            "acted_on": acted_on,
            "decision": decision,
            "verdict": result["verdict"],
            "tokens": result["tokens"],
            "chars": result["chars"],
            "sources": result["sources"],
            "ledger_tokens_out": ledger_row["tokens_out"] if ledger_row else 0,
        })

    by_tier: dict[str, dict] = {}
    for tier in list(TIERS) + ["unknown"]:
        subset = [s for s in scored if s["tier"] == tier]
        if not subset:
            continue
        acted = [s for s in subset if s["acted_on"]]
        verdicts = Counter(s["verdict"] for s in acted)
        divergence = verdicts[VERDICT_INCOMPLETE] + verdicts[VERDICT_CORRUPTED]
        by_tier[tier] = {
            "tier": tier,
            "premises": len(subset),
            "acted_on": len(acted),
            "accurate": verdicts[VERDICT_ACCURATE],
            "incomplete": verdicts[VERDICT_INCOMPLETE],
            "corrupted": verdicts[VERDICT_CORRUPTED],
            "divergence_rate": pct(divergence, len(acted)),
            "reread_tokens": sum(s["tokens"] for s in acted),
            "reread_chars": sum(s["chars"] for s in acted),
            "ledger_tokens_out": sum(s["ledger_tokens_out"] for s in acted),
        }

    acted_all = [s for s in scored if s["acted_on"]]
    verdicts_all = Counter(s["verdict"] for s in acted_all)
    divergence_all = (verdicts_all[VERDICT_INCOMPLETE]
                      + verdicts_all[VERDICT_CORRUPTED])
    corrupted_to_decision = [s for s in acted_all
                             if s["verdict"] == VERDICT_CORRUPTED
                             and s["decision"]]
    overall = {
        "premises": len(scored),
        "acted_on": len(acted_all),
        "unmatched": sum(1 for s in scored if not s["joined"]),
        "accurate": verdicts_all[VERDICT_ACCURATE],
        "incomplete": verdicts_all[VERDICT_INCOMPLETE],
        "corrupted": verdicts_all[VERDICT_CORRUPTED],
        "divergence_rate": pct(divergence_all, len(acted_all)),
        "reread_tokens": sum(s["tokens"] for s in acted_all),
        "reread_chars": sum(s["chars"] for s in acted_all),
        "ledger_tokens_out": sum(s["ledger_tokens_out"] for s in acted_all),
        "corrupted_to_decision": len(corrupted_to_decision),
    }
    verdict = part_a_verdict(overall["divergence_rate"],
                             overall["corrupted_to_decision"])
    return {
        "by_tier": by_tier,
        "overall": overall,
        "gate": {
            "verdict": verdict,
            "divergence_rate": overall["divergence_rate"],
            "corrupted_to_decision": overall["corrupted_to_decision"],
            "proceed_threshold": DIVERGENCE_PROCEED,
            "kill_threshold": DIVERGENCE_KILL,
        },
        "corrupted_to_decision_rows": [
            {"premise_id": s["premise_id"], "tier": s["tier"],
             "claim": s["claim"],
             "sources": [src["path"] for src in s["sources"]]}
            for s in corrupted_to_decision
        ],
        "premises": scored,
    }


def part_a_verdict(divergence_rate: float, corrupted_to_decision: int) -> str:
    """The Part A gate arithmetic, straight from issue #152."""
    if divergence_rate >= DIVERGENCE_PROCEED or corrupted_to_decision > 0:
        return "PROCEED"
    if divergence_rate < DIVERGENCE_KILL and corrupted_to_decision == 0:
        return "KILL"
    return "HOLD"



CLS_SHARED = "shared-evidence-communication"
CLS_EXTERNAL = "external-verification"
CLS_DIVERSITY = "model-diversity"
CLS_PARTITIONED = "partitioned-evidence"

CLASS_ORDER = (CLS_SHARED, CLS_EXTERNAL, CLS_DIVERSITY, CLS_PARTITIONED)

SHARED_MARKERS = ("shared_evidence", "evidence_shared", "shared_findings",
                  "communication", "agent_messages", "cross_agent_notes")
EXTERNAL_MARKERS = ("external_source", "verified_against",
                    "verification_source", "external_verification")
DIVERSITY_MARKERS = ("model_diversity",)


def multi_agent_flag(row: dict) -> bool:
    """Does this dispatch involve more than one agent?"""
    raw = row.get("raw", row)
    if truthy(raw.get("multi_agent")):
        return True
    if as_int(raw.get("agent_count")) > 1:
        return True
    if len(as_list(raw.get("agents"))) > 1:
        return True
    if len(as_list(raw.get("agent_roles"))) > 1:
        return True
    return False


def marker_flags(row: dict, markers: tuple[str, ...]) -> bool:
    """Is any of the named marker fields set on this row?"""
    raw = row.get("raw", row)
    for marker in markers:
        if truthy(raw.get(marker)):
            return True
    return False


def diversity_flag(row: dict) -> bool:
    """Did the dispatch use more than one provider or model tier?"""
    raw = row.get("raw", row)
    if marker_flags(row, DIVERSITY_MARKERS):
        return True
    providers = as_list(raw.get("providers_used"))
    if len({str(p).strip() for p in providers}) > 1:
        return True
    tiers = as_list(raw.get("tiers_used"))
    if len({str(t).strip() for t in tiers}) > 1:
        return True
    return False


def classify_dispatch(row: dict) -> tuple[str, dict]:
    """Classify one dispatch into exactly one evidence-sharing class.

    Precedence (first match wins, documented so the census is
    reproducible): shared evidence, then external verification, then
    model diversity, then partitioned evidence.
    """
    flags = {
        "shared": marker_flags(row, SHARED_MARKERS),
        "external": marker_flags(row, EXTERNAL_MARKERS),
        "diversity": diversity_flag(row),
        "multi_agent": multi_agent_flag(row),
    }
    if flags["shared"]:
        return CLS_SHARED, flags
    if flags["external"]:
        return CLS_EXTERNAL, flags
    if flags["diversity"]:
        return CLS_DIVERSITY, flags
    return CLS_PARTITIONED, flags


def outcome_class(outcome: str) -> str:
    """success / failure / other, from a ledger outcome string."""
    text = str(outcome or "").strip().lower()
    if text in SUCCESS_OUTCOMES:
        return "success"
    if text in FAILURE_OUTCOMES or text.startswith("failed"):
        return "failure"
    return "other"


def census_part_b(ledger_rows: list[dict]) -> dict:
    """The Part B census: class table and the shared-evidence comparison."""
    dispatches = []
    for row in ledger_rows:
        klass, flags = classify_dispatch(row)
        dispatches.append({
            "run_id": row["run_id"],
            "repo": row["repo"],
            "issue": row["issue"],
            "tier": row["tier"] or "unknown",
            "outcome": row["outcome"],
            "outcome_class": outcome_class(row["outcome"]),
            "tokens_out": row["tokens_out"],
            "tokens_in": row["tokens_in"],
            "class": klass,
            "flags": flags,
            "single_agent": not flags["multi_agent"],
        })

    by_class: dict[str, dict] = {}
    for klass in CLASS_ORDER:
        subset = [d for d in dispatches if d["class"] == klass]
        if not subset:
            continue
        successes = sum(1 for d in subset if d["outcome_class"] == "success")
        failures = sum(1 for d in subset if d["outcome_class"] == "failure")
        graded = successes + failures
        by_class[klass] = {
            "class": klass,
            "dispatches": len(subset),
            "successes": successes,
            "failures": failures,
            "ungraded": len(subset) - graded,
            "success_rate": pct(successes, graded),
            "mean_tokens_out": mean([d["tokens_out"] for d in subset]),
            "total_tokens_out": sum(d["tokens_out"] for d in subset),
            "repos": sorted({d["repo"] for d in subset if d["repo"]}),
        }

    shared = [d for d in dispatches if d["class"] == CLS_SHARED]
    single = [d for d in dispatches if d["single_agent"]
              and d["class"] == CLS_PARTITIONED]
    shared_repos = {d["repo"] for d in shared if d["repo"]}
    same_repo = [d for d in single if d["repo"] in shared_repos]
    comparator = same_repo if same_repo else single
    comparator_basis = "same_repo" if same_repo else "all_repos"

    def stats(subset: list[dict]) -> dict:
        successes = sum(1 for d in subset if d["outcome_class"] == "success")
        failures = sum(1 for d in subset if d["outcome_class"] == "failure")
        graded = successes + failures
        return {
            "dispatches": len(subset),
            "successes": successes,
            "failures": failures,
            "ungraded": len(subset) - graded,
            "success_rate": pct(successes, graded),
            "mean_tokens_out": mean([d["tokens_out"] for d in subset]),
            "total_tokens_out": sum(d["tokens_out"] for d in subset),
        }

    shared_stats = stats(shared)
    comparator_stats = stats(comparator)
    comparison = {
        "shared": shared_stats,
        "comparator": comparator_stats,
        "comparator_basis": comparator_basis,
        "shared_repos": sorted(shared_repos),
    }
    verdict = part_b_verdict(shared_stats, comparator_stats)
    comparison["verdict"] = verdict
    return {
        "dispatches": len(dispatches),
        "by_class": by_class,
        "comparison": comparison,
        "gate": {
            "verdict": verdict,
            "shared_success_rate": shared_stats["success_rate"],
            "comparator_success_rate": comparator_stats["success_rate"],
            "shared_mean_tokens_out": shared_stats["mean_tokens_out"],
            "comparator_mean_tokens_out": comparator_stats["mean_tokens_out"],
            "comparator_basis": comparator_basis,
        },
        "rows": dispatches,
    }


def part_b_verdict(shared_stats: dict, comparator_stats: dict) -> str:
    """The Part B gate: a measurable win over the comparators.

    A win needs BOTH a strictly higher success rate AND no more
    tokens_out per dispatch (the comparison is compute-matched).
    """
    if not shared_stats["dispatches"] or not comparator_stats["dispatches"]:
        return "HOLD"
    better_outcome = (shared_stats["success_rate"]
                       > comparator_stats["success_rate"])
    no_more_compute = (shared_stats["mean_tokens_out"]
                       <= comparator_stats["mean_tokens_out"])
    if better_outcome and no_more_compute:
        return "PROCEED"
    return "KILL"




def render_part_a(census: dict) -> list[str]:
    """Markdown tables for Part A."""
    lines = ["## Part A -- weakest-link reader gate", ""]
    lines.append("### Table A1 -- divergence by reader tier")
    lines.append("")
    lines.append("| tier | acted-on premises | accurate | incomplete | "
                 "corrupted | divergence | re-read tokens |")
    lines.append("|---|---|---|---|---|---|---|")
    for tier in list(TIERS) + ["unknown"]:
        row = census["by_tier"].get(tier)
        if not row:
            continue
        lines.append(
            f"| {tier} | {row['acted_on']} | {row['accurate']} | "
            f"{row['incomplete']} | {row['corrupted']} | "
            f"{row['divergence_rate']}% | {row['reread_tokens']} |")
    overall = census["overall"]
    lines.append(
        f"| **all** | {overall['acted_on']} | {overall['accurate']} | "
        f"{overall['incomplete']} | {overall['corrupted']} | "
        f"{overall['divergence_rate']}% | {overall['reread_tokens']} |")
    lines.append("")
    lines.append("### Table A2 -- re-read cost")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| premises scored | {overall['premises']} |")
    lines.append(f"| acted-on premises | {overall['acted_on']} |")
    lines.append(f"| premises not joined to a ledger row | "
                 f"{overall['unmatched']} |")
    lines.append(f"| re-read characters | {overall['reread_chars']} |")
    lines.append(f"| re-read tokens (chars/4) | {overall['reread_tokens']} |")
    lines.append(f"| ledger tokens_out of the same runs | "
                 f"{overall['ledger_tokens_out']} |")
    ratio = (round(100.0 * overall["reread_tokens"]
                   / overall["ledger_tokens_out"], 2)
             if overall["ledger_tokens_out"] else 0.0)
    lines.append(f"| re-read tokens as % of those runs' tokens_out | "
                 f"{ratio}% |")
    lines.append("")
    lines.append("### Table A3 -- corrupted premises that reached a decision")
    lines.append("")
    rows = census["corrupted_to_decision_rows"]
    if not rows:
        lines.append("| premise | tier | claim | sources |")
        lines.append("|---|---|---|---|")
        lines.append("| (none) | - | - | - |")
    else:
        lines.append("| premise | tier | claim | sources |")
        lines.append("|---|---|---|---|")
        for row in rows:
            lines.append(f"| {row['premise_id']} | {row['tier']} | "
                         f"{row['claim']} | {', '.join(row['sources'])} |")
    lines.append("")
    gate = census["gate"]
    lines.append("### Part A gate")
    lines.append("")
    lines.append(f"- divergence on acted-on premises: "
                 f"{gate['divergence_rate']}% "
                 f"(PROCEED at >= {gate['proceed_threshold']}%)")
    lines.append(f"- corrupted premises that reached a merge decision: "
                 f"{gate['corrupted_to_decision']}")
    lines.append(f"- **verdict: {gate['verdict']}**")
    lines.append("")
    return lines


def render_part_b(census: dict) -> list[str]:
    """Markdown tables for Part B."""
    lines = ["## Part B -- compute-matched multi-agent baseline", ""]
    lines.append("### Table B1 -- dispatches by class")
    lines.append("")
    lines.append("| class | dispatches | successes | failures | ungraded | "
                 "success rate | mean tokens_out |")
    lines.append("|---|---|---|---|---|---|---|")
    for klass in CLASS_ORDER:
        row = census["by_class"].get(klass)
        if not row:
            continue
        lines.append(
            f"| {klass} | {row['dispatches']} | {row['successes']} | "
            f"{row['failures']} | {row['ungraded']} | "
            f"{row['success_rate']}% | {row['mean_tokens_out']} |")
    lines.append("")
    comparison = census["comparison"]
    shared = comparison["shared"]
    comparator = comparison["comparator"]
    lines.append("### Table B2 -- shared evidence vs nearest comparator")
    lines.append("")
    lines.append("| group | dispatches | successes | failures | ungraded | "
                 "success rate | mean tokens_out |")
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(f"| shared-evidence | {shared['dispatches']} | "
                 f"{shared['successes']} | {shared['failures']} | "
                 f"{shared['ungraded']} | {shared['success_rate']}% | "
                 f"{shared['mean_tokens_out']} |")
    lines.append(f"| single-agent comparator "
                 f"({comparison['comparator_basis']}) | "
                 f"{comparator['dispatches']} | {comparator['successes']} | "
                 f"{comparator['failures']} | {comparator['ungraded']} | "
                 f"{comparator['success_rate']}% | "
                 f"{comparator['mean_tokens_out']} |")
    lines.append("")
    lines.append("### Part B gate")
    lines.append("")
    lines.append(f"- shared-evidence success rate: {shared['success_rate']}% "
                 f"vs comparator {comparator['success_rate']}%")
    lines.append(f"- shared-evidence mean tokens_out: "
                 f"{shared['mean_tokens_out']} vs comparator "
                 f"{comparator['mean_tokens_out']} "
                 f"(compute-matched: a win needs a higher success rate at "
                 f"no more tokens)")
    lines.append(f"- **verdict: {comparison['verdict']}**")
    lines.append("")
    return lines




def cmd_reader(args) -> int:
    """Part A: the weakest-link reader census."""
    if not os.path.exists(args.ledger):
        raise SystemExit(f"ledger not found: {args.ledger}")
    if not os.path.exists(args.premises):
        raise SystemExit(f"premises file not found: {args.premises}")
    ledger_rows, ledger_skipped = load_ledger(args.ledger)
    premises, premises_skipped = read_rows(args.premises)
    census = census_part_a(ledger_rows, premises, args.sources_root)
    census["inputs"] = {
        "ledger": args.ledger,
        "premises": args.premises,
        "sources_root": args.sources_root,
        "ledger_rows": len(ledger_rows),
        "ledger_rows_skipped": ledger_skipped,
        "premise_rows": len(premises),
        "premise_rows_skipped": premises_skipped,
    }
    if args.json:
        print(json.dumps(census, indent=1))
        return 0
    print("# Part A -- reader gate census (issue #152 Phase 0)")
    print("")
    print(f"ledger: {args.ledger} ({len(ledger_rows)} rows, "
          f"{ledger_skipped} skipped)")
    print(f"premises: {args.premises} ({len(premises)} rows, "
          f"{premises_skipped} skipped)")
    print(f"sources root: {args.sources_root}")
    print("")
    for line in render_part_a(census):
        print(line)
    return 0


def cmd_multiagent(args) -> int:
    """Part B: the compute-matched multi-agent baseline census."""
    if not os.path.exists(args.ledger):
        raise SystemExit(f"ledger not found: {args.ledger}")
    ledger_rows, ledger_skipped = load_ledger(args.ledger)
    census = census_part_b(ledger_rows)
    census["inputs"] = {
        "ledger": args.ledger,
        "ledger_rows": len(ledger_rows),
        "ledger_rows_skipped": ledger_skipped,
    }
    if args.json:
        print(json.dumps(census, indent=1))
        return 0
    print("# Part B -- multi-agent baseline census (issue #152 Phase 0)")
    print("")
    print(f"ledger: {args.ledger} ({len(ledger_rows)} rows, "
          f"{ledger_skipped} skipped)")
    print("")
    for line in render_part_b(census):
        print(line)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    reader = sub.add_parser(
        "reader",
        help="Part A: the weakest-link reader gate census")
    reader.add_argument("--ledger", required=True,
                        help="worker/shepherd ledger JSONL (read-only)")
    reader.add_argument("--premises", required=True,
                        help="premises file: acted-on claims with sources")
    reader.add_argument("--sources-root", default=".",
                        help="root the cited source paths resolve against")
    reader.add_argument("--json", action="store_true",
                        help="emit JSON instead of markdown tables")
    reader.set_defaults(func=cmd_reader)

    multi = sub.add_parser(
        "multiagent",
        help="Part B: the compute-matched multi-agent baseline census")
    multi.add_argument("--ledger", required=True,
                       help="worker ledger JSONL (read-only)")
    multi.add_argument("--json", action="store_true",
                       help="emit JSON instead of markdown tables")
    multi.set_defaults(func=cmd_multiagent)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
