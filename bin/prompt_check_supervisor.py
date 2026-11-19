"""Deterministic prompt checks for ``prompts/backlog-supervisor.md``.

This is the smaller-shape implementation requested by issue #119: the
backlog-supervisor profile is self-contained here and shares only the
0--100 scoring helper with ``bin/prompt_check.py``.  No model, network, or
live harness state is involved.

The #118 census for this prompt contains 31 deterministic clauses and 14
judgement clauses.  Every deterministic clause has one behavioural spec;
judgement clauses are represented by excluded specs and never scored.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

try:  # Executed as ``python3 bin/prompt_check_supervisor.py``.
    from prompt_check import score
except ModuleNotFoundError:  # Imported as ``bin.prompt_check_supervisor``.
    from bin.prompt_check import score

TOKEN_RE = re.compile(r"\{\{([a-zA-Z0-9_.]+)\}\}")
BIN_PATH_RE = re.compile(r"(?:\{\{skill\.dir\}\}/)?(bin/[A-Za-z0-9_./-]+\.py)")
STEP_RE = re.compile(r"^(\d+)[.)]\s", re.MULTILINE)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
DEFAULT_PROMPT = os.path.join("prompts", "backlog-supervisor.md")
DEFAULT_SPECS = os.path.join(
    "tests", "fixtures", "prompt_specs", "backlog-supervisor.json"
)

# Step 0 length accounting for this prompt.  Read-only history inspection
# found a largest live body of 51639 chars and a largest numbered section of
# 18806 chars.  The limits below add 3361 and 1194 chars respectively; they
# are profile-local accounting, not a relaxation of the shared shepherd
# budget.  Both bounds also cover the deployed-template history inspected in
# issue #119.
LENGTH_MIN = 3000
LENGTH_MAX = 55000
STEP_MIN = 4
STEP_MAX = 12
STEP_MAX_CHARS = 20000

STATIC_WEIGHT = 40.0
SPEC_WEIGHT = 60.0

# Exact, stable fragments of the live prompt.  Removing a fragment removes
# the rule it names.  The fragments are deliberately sentence-sized so a
# mutation cannot accidentally remove an unrelated rule.
DETERMINISTIC_CLAUSES: dict[str, str] = {
    "collect_existing_first": "collect them first",
    "stale_working_collecting_only": (
        "The 4-hour stale rule applies to status=working and status=collecting ONLY"
    ),
    "stale_excludes_escalated_split": (
        "Never apply the stale rule to escalated or split_suggested entries"
    ),
    "fresh_collecting_skip": (
        "status=collecting with a fresh started_at means another overlapping "
        "cycle is handling it"
    ),
    "stale_collecting_reset": (
        "status=collecting with started_at older than 1 hour means its "
        "collector died"
    ),
    "max_parallel_active": (
        "no more than {{supervisor.max_parallel}} workers are active"
    ),
    "cycle_token_budget": (
        "Launch no new workers once that total passes "
        "{{workers.cycle_tokens_out_budget}}"
    ),
    "fetch_before_issue_list": "`git fetch origin`. `gh issue list",
    "backlog_empty_skip": (
        'Zero open issues → record "BACKLOG EMPTY: <repo>"'
    ),
    "never_create_issues": "Do NOT create issues yourself.",
    "skip_factory_failure": (
        "Never brief or dispatch an auto-filed factory-failure issue yourself."
    ),
    "skip_open_pr_issue": "Skip issues with an already-open PR",
    "skip_assigned_issue": (
        "Skip issues that already have a worker with status working, escalated, "
        "collecting, or split_suggested in workers.json"
    ),
    "retry_guard": (
        "before picking an issue, run `python3 {{skill.dir}}/bin/ledger_check.py "
        "<repo> <issue>`"
    ),
    "retry_requires_change": (
        "either materially narrow or split the brief, change tier with a "
        "stated reason, or skip the issue"
    ),
    "failover_action": (
        "also run `python3 {{skill.dir}}/bin/failover.py <repo> <issue>` "
        "before briefing and obey its action"
    ),
    "failover_reflection": (
        "paste its REFLECTION lines verbatim into the brief"
    ),
    "failover_flag_stops": "do NOT dispatch the issue at all",
    "greenfield_brief_mode": (
        "brief it under the greenfield mode in step 4c"
    ),
    "premise_symbol_exists": (
        "if it already exists on the base branch, skip the issue"
    ),
    "premise_gate_not_tripped": (
        "if a stated gate (beta soak, nightly streak, ADR supersession) has "
        "not tripped, skip the issue"
    ),
    "clear_stale_worktree": (
        "first clear any stale worktree at the target path"
    ),
    "delete_stale_branch": (
        "delete it first (`git branch -D` locally, `git push origin --delete` "
        "remotely)"
    ),
    "worktree_from_base": (
        "using the repo's base branch from the table above"
    ),
    "brief_scope_room": (
        "Every brief opens with two header blocks that never merge: SCOPE:"
    ),
    "brief_proof": "Every brief also carries a short `PROOF:` block",
    "optional_deliverable": (
        "Every brief may ALSO carry one optional `DELIVERABLE:` line"
    ),
    "structured_diagnostics": (
        "every brief names the repo's structured-diagnostics command"
    ),
    "atomic_state_save": "State saves are atomic or they do not happen",
    "collect_claim_before_review": (
        "Immediately set its status to collecting and save workers.json"
    ),
    "ledger_outcome_required": (
        "append one JSON line per collected worker to the metrics ledger"
    ),
}

JUDGEMENT_CLAUSES: dict[str, str] = {
    "issue_well_specified": "Pick well-specified issues",
    "issue_priority_judgement": "as judgment dictates",
    "merge_ordering": "Respect merge ordering",
    "same_repo_conflict_avoidance": "choose issues in different areas",
    "skip_note_judgement": "Skip secrets/hardware/paid services.",
    "machine_build_budget": "{{machine.build_note}}",
    "failover_narrowing_judgement": (
        "narrow the brief if it says a narrowed brief is required"
    ),
    "diff_review_quality": (
        "review the diff yourself against the acceptance criteria"
    ),
    "trivial_fallout_judgement": "Fix only trivial fallout",
    "deadline_progress_judgement": "If it looks close to finishing",
    "split_breakdown_judgement": (
        "a proposed breakdown into 2-4 concrete sub-tasks"
    ),
    "escalation_diagnosis_judgement": "If it is stuck or thrashing",
    "proof_material_judgement": "Then add the material for the PR's type",
    "tier_selection_judgement": (
        "Tier routing: cheap = small fully-specified jobs, balanced = "
        "ordinary issues (default), flagship = tricky work"
    ),
}

CLAUSES = {**DETERMINISTIC_CLAUSES, **JUDGEMENT_CLAUSES}

# (input field, trigger value, decision) mirrors the prompt rule.  A spec
# names its rule in ``input.rule`` and supplies the trigger field.  If the
# clause is absent from the prompt, the rule is unstated and the decision is
# ``hold_unstated`` instead.
RULES: dict[str, tuple[str, object, str]] = {
    "collect_existing_first": ("existing_workers", True, "collect_existing"),
    "stale_working_collecting_only": (
        "stale_working_or_collecting", True, "reset_stale_worker"
    ),
    "stale_excludes_escalated_split": (
        "escalated_or_split", True, "preserve_for_main_agent"
    ),
    "fresh_collecting_skip": (
        "fresh_overlapping_collector", True, "skip_overlapping_collector"
    ),
    "stale_collecting_reset": ("dead_collector", True, "reset_to_working"),
    "max_parallel_active": ("parallel_limit_reached", True, "hold_new_launch"),
    "cycle_token_budget": ("token_budget_exceeded", True, "stop_new_launches"),
    "fetch_before_issue_list": ("fetch_missing", True, "fetch_first"),
    "backlog_empty_skip": ("zero_open_issues", True, "report_backlog_empty"),
    "never_create_issues": ("would_create_issue", True, "skip_issue_creation"),
    "skip_factory_failure": ("factory_failure", True, "skip_factory_failure"),
    "skip_open_pr_issue": ("open_pr_exists", True, "skip_open_pr_issue"),
    "skip_assigned_issue": ("worker_exists", True, "skip_assigned_issue"),
    "retry_guard": ("retry_guard_blocks", True, "hold_retry_guard"),
    "retry_requires_change": (
        "unchanged_rebrief", True, "require_material_brief_change"
    ),
    "failover_action": ("failover_action", "failover", "dispatch_failover"),
    "failover_reflection": (
        "reflection_required", True, "dispatch_with_reflection"
    ),
    "failover_flag_stops": (
        "failover_action", "flag_for_operator", "flag_for_operator"
    ),
    "greenfield_brief_mode": ("greenfield", True, "dispatch_greenfield"),
    "premise_symbol_exists": (
        "symbol_already_present", True, "skip_already_present"
    ),
    "premise_gate_not_tripped": (
        "gate_not_tripped", True, "skip_gate_blocked"
    ),
    "clear_stale_worktree": ("stale_worktree", True, "remove_stale_worktree"),
    "delete_stale_branch": ("stale_branch", True, "delete_stale_branch"),
    "worktree_from_base": ("wrong_base", True, "use_repo_base_branch"),
    "brief_scope_room": (
        "scope_or_room_missing", True, "require_scope_and_room"
    ),
    "brief_proof": ("proof_block_missing", True, "require_proof_block"),
    "optional_deliverable": (
        "deliverable_named", True, "check_named_deliverable"
    ),
    "structured_diagnostics": (
        "raw_diagnostics", True, "require_structured_diagnostics"
    ),
    "atomic_state_save": ("direct_state_write", True, "reject_direct_write"),
    "collect_claim_before_review": (
        "overlapping_collector", True, "claim_before_review"
    ),
    "ledger_outcome_required": (
        "collected_worker", True, "append_ledger_outcome"
    ),
}

REQUIRED_PHRASES = [
    "State saves are atomic or they do not happen",
    "Do NOT create issues yourself.",
    "Every brief opens with two header blocks that never merge: SCOPE:",
    "Every brief also carries a short `PROOF:` block",
    "never merge a PR yourself",
    "append one JSON line per collected worker",
]

# Synthetic, hermetic render values.  They are deliberately independent of
# config/deployment.local.json.
SYNTHETIC_VALUES = {
    "machine.build_note": "avoid heavy local builds",
    "operator.name": "Test Operator",
    "operator.flag_label": "NEEDS HUMAN",
    "public_repo.local_clone": "/tmp/pc-public",
    "public_repo.slug": "example/public",
    "repos.brief_extras": "",
    "repos.bullets": "repo-a (clone, slug, base)",
    "repos.skip_notes": "skip generated files",
    "repos.split_sentence": "in configured priority",
    "skill.dir": "/tmp/pc-skill",
    "state.dir": "/tmp/pc-state",
    "state.transcripts_dir": "/tmp/pc-state/transcripts",
    "supervisor.auth_note": "",
    "supervisor.collect_deadline_hours": "2.5",
    "supervisor.interval_hours": "2",
    "supervisor.max_parallel": "6",
    "supervisor.memory_note": "",
    "supervisor.merge_order_example": "",
    "supervisor.quota_context": "Quota is not the constraint.",
    "tune.flag_label": "[flag for human]",
    "tune.ideas_brief_file": "ideas-brief.md",
    "tune.log_step": "Log the pass.",
    "worktrees.dir": "/tmp/pc-wt",
    "workers.cycle_tokens_out_budget": "0",
    "workers.goldfish.max_per_cycle": "2",
    "workers.goldfish.max_steps": "25",
    "workers.goldfish.max_tokens_out": "30000",
    "workers.goldfish.tier": "cheap",
    "workers.jittest.hypothesis_tier": "balanced",
    "workers.jittest.max_per_cycle": "2",
    "workers.jittest.max_steps": "25",
    "workers.jittest.max_three_state_runs": "3",
    "workers.jittest.max_tokens_out_hypothesis": "30000",
    "workers.jittest.max_tokens_out_test": "30000",
    "workers.jittest.test_tier": "cheap",
    "workers.max_steps": "60",
    "workers.max_tokens_out": "0",
    "workers.provider_order": "minimax",
    "workers.worktree_persistence.max_slots_per_repo": "8",
}


def split_prompt(text: str) -> tuple[str, str]:
    """Split a template into (header, body) on the lone ``---`` separator."""
    lines = text.splitlines()
    try:
        cut = lines.index("---")
    except ValueError:
        return text, text
    return "\n".join(lines[:cut]), "\n".join(lines[cut + 1:])


def present_clauses(text: str) -> set[str]:
    """Ids of clauses whose exact fragment is present in the prompt."""
    return {cid for cid, needle in CLAUSES.items() if needle in text}


def render_body(body: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Render known template tokens; return (rendered, unresolved names)."""
    unresolved: list[str] = []

    def sub(match: re.Match) -> str:
        token = match.group(1)
        if token not in values:
            unresolved.append(token)
            return match.group(0)
        return values[token]

    return TOKEN_RE.sub(sub, body), sorted(set(unresolved))


def static_checks(text: str, repo_root: str) -> list[dict]:
    """Return the four structural checks for the supervisor prompt."""
    _header, body = split_prompt(text)
    results: list[dict] = []

    missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in body]
    results.append({
        "name": "required_sections",
        "passed": not missing,
        "detail": ("all required phrases present" if not missing else
                   "missing phrase(s): " + ", ".join(missing)),
    })

    rendered, unresolved = render_body(body, SYNTHETIC_VALUES)
    leftover = sorted(set(TOKEN_RE.findall(rendered)))
    results.append({
        "name": "tokens_resolved",
        "passed": not leftover,
        "detail": ("every token renders (body %d chars)" % len(rendered)
                   if not leftover else
                   "unresolved token(s): " + ", ".join(leftover)),
    })

    referenced = sorted(set(BIN_PATH_RE.findall(text)))
    stale = [path for path in referenced
             if not os.path.isfile(os.path.join(repo_root, path))]
    results.append({
        "name": "bin_paths_exist",
        "passed": not stale,
        "detail": ("%d bin path(s) referenced, all exist" % len(referenced)
                   if not stale else
                   "stale bin path(s): " + ", ".join(stale)),
    })

    steps = STEP_RE.findall(body)
    longest = max((len(part) for part in
                   re.split(r"(?m)^\d+[.)]\s", body)[1:]), default=0)
    problems: list[str] = []
    if not (LENGTH_MIN <= len(body) <= LENGTH_MAX):
        problems.append("body length %d outside [%d, %d]"
                        % (len(body), LENGTH_MIN, LENGTH_MAX))
    if not (STEP_MIN <= len(steps) <= STEP_MAX):
        problems.append("step count %d outside [%d, %d]"
                        % (len(steps), STEP_MIN, STEP_MAX))
    if longest > STEP_MAX_CHARS:
        problems.append("longest step %d chars > %d"
                        % (longest, STEP_MAX_CHARS))
    results.append({
        "name": "length_budget",
        "passed": not problems,
        "detail": ("body %d chars, %d steps, longest step %d chars"
                   % (len(body), len(steps), longest)
                   if not problems else "; ".join(problems)),
    })
    return results


def decide(case: dict, clauses: set[str]) -> tuple[str, str, list[str]]:
    """Apply the one prompt rule named by a supervisor spec."""
    rule_id = case.get("rule", "")
    if rule_id not in RULES:
        return ("invalid_rule", rule_id, [])
    if rule_id not in clauses:
        return ("hold_unstated", rule_id, [rule_id])
    trigger, value, decision = RULES[rule_id]
    if case.get(trigger) != value:
        return ("continue", rule_id, [rule_id])
    return (decision, rule_id, [rule_id])


def evaluate_specs(text: str, specs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (deterministic results, excluded judgement specs)."""
    clauses = present_clauses(text)
    results: list[dict] = []
    excluded: list[dict] = []
    for spec in specs:
        sid = spec.get("id", "<unnamed>")
        deterministic = spec.get("deterministic", True)
        if not deterministic:
            clause = spec.get("clause", "")
            excluded.append({
                "id": sid,
                "description": spec.get("description", ""),
                "clause": clause,
                "clause_present": clause in clauses,
                "reason": spec.get(
                    "judgement_note", "needs a model judgement"
                ),
            })
            continue

        case = spec.get("input", {})
        decision, clause, consulted = decide(case, clauses)
        expected = spec.get("expected_decision")
        missing_clauses = [cid for cid in spec.get("required_clauses", [])
                           if cid not in clauses]
        passed = decision == expected and not missing_clauses
        detail = ""
        if decision != expected:
            detail = ("expected %s, rule engine gave %s"
                      % (expected, decision))
            if decision == "hold_unstated":
                detail += " (clause missing: %s)" % clause
        elif missing_clauses:
            detail = ("clause(s) missing from the prompt: "
                      + ", ".join(missing_clauses))
        results.append({
            "id": sid,
            "description": spec.get("description", ""),
            "expected_decision": expected,
            "decision": decision,
            "clause": clause,
            "consulted_clauses": consulted,
            "missing_clauses": missing_clauses,
            "passed": passed,
            "detail": detail,
        })
    return results, excluded


def coverage_report(specs: list[dict]) -> dict:
    """Build the clause-to-spec report used by the docs and tests."""
    deterministic_mapping: dict[str, list[str]] = {}
    judgement_mapping: dict[str, list[str]] = {}
    for spec in specs:
        sid = spec.get("id", "<unnamed>")
        if spec.get("deterministic", True):
            for cid in spec.get("required_clauses", []):
                deterministic_mapping.setdefault(cid, []).append(sid)
        else:
            cid = spec.get("clause", "")
            judgement_mapping.setdefault(cid, []).append(sid)

    unmapped = sorted(set(DETERMINISTIC_CLAUSES) - set(deterministic_mapping))
    untestable = [
        {
            "clause": cid,
            "reason": "no deterministic spec maps this census clause",
        }
        for cid in unmapped
    ]
    return {
        "prompt": "prompts/backlog-supervisor.md",
        "census_issue": "#118",
        "deterministic_clause_count": len(DETERMINISTIC_CLAUSES),
        "judgement_clause_count": len(JUDGEMENT_CLAUSES),
        "deterministic_clause_to_spec": deterministic_mapping,
        "judgement_clause_to_excluded_spec": judgement_mapping,
        "unmapped_deterministic": unmapped,
        "untestable_deterministic": untestable,
    }


def run(prompt_path: str, specs_path: str, repo_root: str) -> dict:
    """Score the supervisor prompt and its behavioural specs."""
    with open(prompt_path, encoding="utf-8") as handle:
        text = handle.read()
    with open(specs_path, encoding="utf-8") as handle:
        spec_doc = json.load(handle)
    specs = spec_doc.get("specs", spec_doc if isinstance(spec_doc, list) else [])
    static = static_checks(text, repo_root)
    spec_results, excluded = evaluate_specs(text, specs)
    return {
        "prompt": os.path.relpath(prompt_path, repo_root),
        "specs_file": os.path.relpath(specs_path, repo_root),
        "score": score(static, spec_results),
        "static": static,
        "specs": spec_results,
        "excluded_specs": excluded,
        "spec_counts": {
            "deterministic": len(spec_results),
            "deterministic_passed": sum(1 for spec in spec_results
                                        if spec["passed"]),
            "excluded": len(excluded),
        },
        "static_counts": {
            "total": len(static),
            "passed": sum(1 for check in static if check["passed"]),
        },
        "coverage": coverage_report(specs),
    }


def remove_clause(text: str, clause_id: str) -> str:
    """Delete a clause's exact fragment from a copy of the prompt."""
    needle = CLAUSES.get(clause_id)
    if needle is None:
        raise ValueError("unknown clause id: %s" % clause_id)
    if needle not in text:
        raise ValueError("clause %s not present in the prompt" % clause_id)
    return text.replace(needle, "", 1)


def _print_result(result: dict, mutation: str | None = None) -> None:
    print("prompt: %s" % result["prompt"])
    if mutation:
        print("mutation: clause %r removed from a temporary copy" % mutation)
    print("static checks (%d/%d passed):"
          % (result["static_counts"]["passed"],
             result["static_counts"]["total"]))
    for check in result["static"]:
        print("  %s %s -- %s" % ("PASS" if check["passed"] else "FAIL",
                                 check["name"], check["detail"]))
    counts = result["spec_counts"]
    print("behavioural specs (%d/%d deterministic passed, %d excluded):"
          % (counts["deterministic_passed"], counts["deterministic"],
             counts["excluded"]))
    for spec in result["specs"]:
        print("  %s %s -> %s (expected %s)%s"
              % ("PASS" if spec["passed"] else "FAIL", spec["id"],
                 spec["decision"], spec["expected_decision"],
                 "  [%s]" % spec["detail"] if spec["detail"] else ""))
    for spec in result["excluded_specs"]:
        print("  SKIP %s -- %s (excluded: %s)"
              % (spec["id"], spec["description"], spec["reason"]))
    print("score: %s/100" % result["score"])
    print("result: %s" % ("PASS" if result["ok"] else "FAIL"))


def main(argv=None) -> int:
    """Run the supervisor profile directly."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt", default=os.path.join(REPO_ROOT, DEFAULT_PROMPT))
    parser.add_argument("--specs", default=os.path.join(REPO_ROOT, DEFAULT_SPECS))
    parser.add_argument("--repo-root", default=REPO_ROOT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-score", type=float, default=100.0)
    parser.add_argument("--remove-clause", default=None)
    args = parser.parse_args(argv)

    repo_root = os.path.realpath(args.repo_root)
    prompt_path = os.path.realpath(args.prompt)
    specs_path = os.path.realpath(args.specs)
    for path in (prompt_path, specs_path):
        if not os.path.isfile(path):
            print("not found: %s" % path, file=sys.stderr)
            return 2

    tmpdir = None
    label = os.path.relpath(prompt_path, repo_root)
    try:
        if args.remove_clause:
            with open(prompt_path, encoding="utf-8") as handle:
                text = handle.read()
            try:
                mutated = remove_clause(text, args.remove_clause)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            tmpdir = tempfile.mkdtemp(prefix="prompt-check-supervisor-")
            prompt_path = os.path.join(tmpdir, os.path.basename(prompt_path))
            with open(prompt_path, "w", encoding="utf-8") as handle:
                handle.write(mutated)
        result = run(prompt_path, specs_path, repo_root)
    finally:
        if tmpdir:
            for name in os.listdir(tmpdir):
                os.unlink(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)

    result["prompt"] = label
    if args.remove_clause:
        result["mutation"] = args.remove_clause
    failed_static = [check for check in result["static"] if not check["passed"]]
    failed_specs = [spec for spec in result["specs"] if not spec["passed"]]
    result["ok"] = (not failed_static and not failed_specs
                    and result["score"] >= args.min_score)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        _print_result(result, args.remove_clause)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
