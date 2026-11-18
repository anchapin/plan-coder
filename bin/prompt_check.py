#!/usr/bin/env python3
"""Prompt self-test runner: static checks + behavioural specs for a prompt template.

Phase 0 of issue #55 ("plan-coder's prompts are its skills, but the test
suite tests bin/ code, not prompt behaviour"). A prompt edit today is
validated only by render checks and live cycles; this runner gives a
prompt the same treatment a bin/ module gets: a deterministic score
computed from the prompt's own text.

Two layers, no LLM anywhere:

  static checks   structural facts about the template:
                    - required sections/phrases are present
                    - {{token}} render is clean (no unresolved token)
                    - every bin/*.py path the prompt names exists in bin/
                      (the 2026-11-08 stale-path incident: a prompt that
                      names a renamed bin/ path)
                    - length / step-budget sanity

  behavioural specs  scenarios drawn from the prompt's own decision
                    rules (merge bar, hard stops, known-failure
                    exemptions, workflow-touch flags). Each spec is a
                    small JSON fixture: an input state snippet and the
                    expected decision. The decision is computed by a
                    deterministic rule engine that mirrors the prompt,
                    and every rule is gated on the clause that states
                    it being present in the prompt text: delete the
                    clause and the scenario stops being decidable the
                    way the prompt says it is -- the spec then fails
                    and names the clause. Specs that would need a
                    model judgement (e.g. "does this diff look
                    well-scoped?") are marked deterministic=false and
                    are excluded from the score rather than faked.

Usage:
    python3 bin/prompt_check.py [--prompt PATH] [--specs PATH]
                                [--repo-root PATH] [--json]
                                [--min-score N] [--remove-clause ID]

Exit 0 = every deterministic check passed (and score >= --min-score,
default 100: the live prompt must be perfect).
Exit 1 = at least one check or deterministic spec failed.
Exit 2 = usage error / missing file.

--remove-clause ID scores a mutated COPY of the prompt (the clause is
deleted from the text) instead of the prompt itself. It is the
keep/revert demonstration switch: it never writes to the prompt file.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import tempfile

TOKEN_RE = re.compile(r"\{\{([a-zA-Z0-9_.]+)\}\}")
# A bin/ path named by the prompt: bare "bin/x.py" or "{{skill.dir}}/bin/x.py".
BIN_PATH_RE = re.compile(r"(?:\{\{skill\.dir\}\}/)?(bin/[A-Za-z0-9_./-]+\.py)")
STEP_RE = re.compile(r"^(\d+)[.)]\s", re.MULTILINE)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
DEFAULT_PROMPT = os.path.join("prompts", "pr-shepherd.md")
DEFAULT_SPECS = os.path.join("tests", "fixtures", "prompt_specs",
                             "pr-shepherd.json")

# Issue #119 uses the smaller-shape fallback: the shepherd profile stays
# here, while the other two live prompts have self-contained profile modules.
# The dispatcher recognises the live prompt filename, so every profile has
# the same command-line contract as the Phase 0 runner.
PROFILE_MODULES = {
    "backlog-supervisor.md": "prompt_check_supervisor",
    "harness-tune.md": "prompt_check_harness_tune",
}


def profile_for_prompt(prompt_path: str) -> str:
    """Return the profile name for a prompt path (``pr-shepherd`` default)."""
    return PROFILE_MODULES.get(os.path.basename(prompt_path), "pr-shepherd")


def profile_module_for_prompt(prompt_path: str):
    """Import and return a non-shepherd profile module, or None."""
    module_name = PROFILE_MODULES.get(os.path.basename(prompt_path))
    if module_name is None:
        return None
    if __package__:
        return importlib.import_module("." + module_name, __package__)
    return importlib.import_module(module_name)

# ---------------------------------------------------------------------------
# Clause catalogue.
#
# A clause is a sentence of the prompt that states a rule. The rule
# engine consults clause ids; a clause that is missing from the prompt
# text means the rule is not stated, so the scenario is not decidable
# the way the prompt says it is -> the spec fails and names the clause.
#
# Each clause carries the exact sentence used to delete it when the
# --remove-clause switch builds a mutated copy.
# ---------------------------------------------------------------------------
CLAUSES: dict[str, str] = {
    "merge_requires_mergeable": "`mergeable` is MERGEABLE",
    "merge_requires_worker_branch": "The head branch is `worker/*`",
    "merge_requires_fresh_sha": "never merge on a stale check",
    "merge_requires_diff_review": "You have reviewed the full diff",
    "merge_requires_no_flag": "flag was ever raised on this PR",
    "known_failure_exemption": (
        "UNSTABLE is acceptable when the only failures are pre-existing "
        "develop-wide issues already tracked in follow-up issues"),
    "no_jittest_merge": "The PR is not JiT-flagged",
    "no_tamper_merge": "The PR is not test-tamper flagged",
    "merge_command": "gh pr merge --merge",
    "never_admin": "never `--admin`",
    "never_squash": "never `--squash`",
    "hard_stop_workflows": "PRs touching `.github/workflows`",
    "hard_stop_branch_protection": "branch protection",
    "hard_stop_domain": "domain judgment is needed",
    "hard_stop_unverifiable":
        "anything where you cannot verify the implementation is correct",
    "conflict_skip_ci": "skip CI fixing for it this cycle",
    "rerun_once": "Only one rerun per PR per shepherd cycle",
    "fix_attempts_bounded": "fix attempts per PR",
    "never_touch_non_worker": "Never touch branches that are not worker/*",
    "never_merge_rebase": "never rebase",
    "flag_label": "{{operator.flag_label}}",
    "state_write_atomic": "state_write.py commit",
    "shepherd_ledger": "shepherd_ledger.py append",
    "terminal_merge_packet_must": (
        "never append a terminal merge row without the decision-time packet"),
    "trigger_cooldown": "{{shepherd.trigger_cooldown_minutes}}",
    "final_message_states": "MERGED-BY-SHEPHERD",
}

# Full sentences deleted when a clause is removed (the sentence that
# carries the clause, so removing it removes exactly that rule).
CLAUSE_SENTENCES: dict[str, str] = {
    # NB: the sentence carries a leading space so that deleting it from
    # the prompt leaves no dangling whitespace behind.
    "known_failure_exemption": (
        " `mergeStateStatus` CLEAN is preferred, but UNSTABLE is acceptable "
        "when the only failures are pre-existing develop-wide issues already "
        "tracked in follow-up issues ({{merge_policy.known_failures}}) — "
        "these must not block merges."),
}

# Phrases a pr-shepherd template must contain (structural, not rules).
REQUIRED_PHRASES = [
    "MERGE POLICY",
    "NEVER merge",
    "gh pr merge --merge",
    "MERGE CONFLICTS",
    "TRIGGER REQUESTED",
    "{{operator.flag_label}}",
]

# Synthetic render values. Deliberately NOT the operator's
# config/deployment.local.json: the runner must be hermetic and
# identical on every machine. The point of the check is that every
# token in the body has a value, not what the value is.
SYNTHETIC_VALUES = {
    "operator.name": "Test Operator",
    "operator.flag_label": "NEEDS HUMAN",
    "state.dir": "/tmp/pc-state",
    "worktrees.dir": "/tmp/pc-wt",
    "skill.dir": "/tmp/pc-skill",
    "shepherd.interval_minutes": "30",
    "shepherd.max_fix_attempts": "2",
    "shepherd.trigger_cooldown_minutes": "10",
    "shepherd.trigger_note": "Trigger note.",
    "shepherd.final_note": "Final note.",
    "merge_policy.directive_date": "2026-11-01",
    "merge_policy.directive_quote": "merge aggressively",
    "merge_policy.known_failures": "known failures",
    "merge_policy.hard_stop_domain": "domain judgment",
}

LENGTH_MIN = 3000
LENGTH_MAX = 20000
STEP_MIN = 4
STEP_MAX = 12

# Step 0 accounting for issue #119.  Read-only history inspection of
# prompts/pr-shepherd.md since the #55 prompt-self-testing seed found these
# longest numbered-section sizes on the deployed-template line:
# 5525 (issue #25), 6721 (2026-11-17 reconciliation), 8071 (2026-11-18
# template sync), and 9301 (2026-11-18 live-preservation merge).  The old
# 8000-char limit therefore rejected live prompt text that had not exceeded
# the prompt's body budget (19966 <= 20000 chars).  10000 is the smallest
# round budget above the largest accounted section (699 chars of headroom);
# it is not a relaxation of the body, step-count, or 3000-char minimum
# budgets.  Other prompt profiles carry their own independently accounted
# budgets below the scoring helpers.
STEP_MAX_CHARS = 10000

STATIC_WEIGHT = 40.0
SPEC_WEIGHT = 60.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def split_prompt(text: str) -> tuple[str, str]:
    """Split a template into (header, body) on the lone '---' separator.

    Only the body is rendered by bin/render_prompts.py, so only the body
    is checked: the header is human-facing prose and may mention tokens
    literally.
    """
    lines = text.splitlines()
    try:
        cut = lines.index("---")
    except ValueError:
        return text, text
    return "\n".join(lines[:cut]), "\n".join(lines[cut + 1:])


def present_clauses(text: str) -> set[str]:
    """Ids of the clauses whose sentence is present in the text."""
    return {cid for cid, needle in CLAUSES.items() if needle in text}


def render_body(body: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Render {{token}}s; return (rendered, unresolved token names)."""
    unresolved: list[str] = []

    def sub(match: re.Match) -> str:
        token = match.group(1)
        if token not in values:
            unresolved.append(token)
            return match.group(0)
        return values[token]

    return TOKEN_RE.sub(sub, body), sorted(set(unresolved))


# ---------------------------------------------------------------------------
# static checks
# ---------------------------------------------------------------------------
def static_checks(text: str, repo_root: str) -> list[dict]:
    """Structural checks; each returns {name, passed, detail}."""
    _header, body = split_prompt(text)
    results: list[dict] = []

    missing = [p for p in REQUIRED_PHRASES if p not in body]
    results.append({
        "name": "required_sections",
        "passed": not missing,
        "detail": ("all required phrases present"
                   if not missing else
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

    # The whole file, not just the body: a stale bin/ path can live in
    # the human-facing header too (the 2026-11-08 incident).
    referenced = sorted(set(BIN_PATH_RE.findall(text)))
    stale = [p for p in referenced
             if not os.path.isfile(os.path.join(repo_root, p))]
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
    problems = []
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


# ---------------------------------------------------------------------------
# behavioural rule engine
# ---------------------------------------------------------------------------
def decide(pr: dict, clauses: set[str]) -> tuple[str, str, list[str]]:
    """Decide what the shepherd does with a PR, given the prompt's clauses.

    Returns (decision, clause_id, consulted_clauses). The clause_id is
    empty when the decision follows from a rule the prompt states
    plainly; when a rule is missing from the prompt the decision is
    "hold_unstated" and the clause_id names the missing clause.
    """
    state = pr.get("state", "open")
    if state == "merged":
        return ("drop_merged", "", [])
    if state == "closed":
        return ("closed_unmerged", "", [])

    if "merge_requires_mergeable" not in clauses:
        return ("hold_unstated", "merge_requires_mergeable",
                ["merge_requires_mergeable"])
    if pr.get("mergeable") != "MERGEABLE":
        if "conflict_skip_ci" not in clauses:
            return ("hold_unstated", "conflict_skip_ci", ["conflict_skip_ci"])
        return ("merge_conflicts", "conflict_skip_ci", ["conflict_skip_ci"])

    ci = pr.get("ci", "green")
    if ci == "flaky":
        if "rerun_once" not in clauses:
            return ("hold_unstated", "rerun_once", ["rerun_once"])
        return ("rerun", "rerun_once", ["rerun_once"])
    if ci == "mechanical":
        if pr.get("head_branch", "").startswith("worker/"):
            if "fix_attempts_bounded" not in clauses:
                return ("hold_unstated", "fix_attempts_bounded",
                        ["fix_attempts_bounded"])
            return ("fix_attempt", "fix_attempts_bounded",
                    ["fix_attempts_bounded"])
        if "never_touch_non_worker" not in clauses:
            return ("hold_unstated", "never_touch_non_worker",
                    ["never_touch_non_worker"])
        return ("flag", "never_touch_non_worker", ["never_touch_non_worker"])
    if ci == "judgment":
        return ("flag", "", [])

    checks = [
        ("merge_requires_worker_branch",
         lambda: str(pr.get("head_branch", "")).startswith("worker/"), "flag"),
        ("merge_requires_fresh_sha",
         lambda: not pr.get("head_sha_stale"), "hold_stale_sha"),
        ("merge_requires_diff_review",
         lambda: bool(pr.get("diff_reviewed", True)), "flag"),
        ("merge_requires_no_flag",
         lambda: not pr.get("flag_raised"), "flag"),
        ("no_jittest_merge",
         lambda: not pr.get("jittest_catch"), "flag"),
        ("no_tamper_merge",
         lambda: not pr.get("test_tamper_flag"), "flag"),
        ("hard_stop_workflows",
         lambda: not pr.get("touches_workflows"), "flag"),
        ("hard_stop_domain",
         lambda: not pr.get("domain_judgment"), "flag"),
        ("hard_stop_unverifiable",
         lambda: not pr.get("unverifiable"), "flag"),
    ]
    for cid, ok, decision in checks:
        if cid not in clauses:
            return ("hold_unstated", cid, [cid])
        if not ok():
            return (decision, cid, [cid])

    status = pr.get("merge_state_status", "CLEAN")
    if status == "UNSTABLE":
        cid = "known_failure_exemption"
        if cid not in clauses:
            return ("hold_unstated", cid, [cid])
        failures = pr.get("ci_failures") or []
        if not failures or any(f.get("scope") != "pre_existing_tracked"
                               for f in failures):
            return ("flag", cid, [cid])
    elif status != "CLEAN":
        return ("flag", "merge_requires_mergeable",
                ["merge_requires_mergeable"])

    if "merge_command" not in clauses:
        return ("hold_unstated", "merge_command", ["merge_command"])
    return ("merge", "merge_command", ["merge_command"])


def evaluate_specs(text: str, specs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Run the behavioural specs; return (deterministic results, excluded)."""
    clauses = present_clauses(text)
    results: list[dict] = []
    excluded: list[dict] = []
    for spec in specs:
        sid = spec.get("id", "<unnamed>")
        deterministic = spec.get("deterministic", True)
        if not deterministic:
            excluded.append({
                "id": sid,
                "description": spec.get("description", ""),
                "reason": spec.get("judgement_note",
                                   "needs a model judgement"),
            })
            continue
        pr = spec.get("input", {})
        decision, clause, consulted = decide(pr, clauses)
        expected = spec.get("expected_decision")
        missing_clauses = [c for c in spec.get("required_clauses", [])
                           if c not in clauses]
        passed = (decision == expected) and not missing_clauses
        detail = ""
        if decision != expected:
            detail = ("expected %s, rule engine gave %s" % (expected, decision))
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


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def score(static: list[dict], specs: list[dict]) -> float:
    """One score, 0-100. Static checks carry 40, deterministic specs 60."""
    total = STATIC_WEIGHT + SPEC_WEIGHT
    earned = 0.0
    if static:
        earned += STATIC_WEIGHT * sum(1 for c in static if c["passed"]) / len(static)
    if specs:
        earned += SPEC_WEIGHT * sum(1 for s in specs if s["passed"]) / len(specs)
    return round(100.0 * earned / total, 1)


def run(prompt_path: str, specs_path: str, repo_root: str) -> dict:
    profile_module = profile_module_for_prompt(prompt_path)
    if profile_module is not None:
        return profile_module.run(prompt_path, specs_path, repo_root)

    with open(prompt_path, encoding="utf-8") as fh:
        text = fh.read()
    with open(specs_path, encoding="utf-8") as fh:
        spec_doc = json.load(fh)
    specs = spec_doc.get("specs", spec_doc if isinstance(spec_doc, list) else [])
    static = static_checks(text, repo_root)
    spec_results, excluded = evaluate_specs(text, specs)
    sc = score(static, spec_results)
    return {
        "prompt": os.path.relpath(prompt_path, repo_root),
        "specs_file": os.path.relpath(specs_path, repo_root),
        "score": sc,
        "static": static,
        "specs": spec_results,
        "excluded_specs": excluded,
        "spec_counts": {
            "deterministic": len(spec_results),
            "deterministic_passed": sum(1 for s in spec_results if s["passed"]),
            "excluded": len(excluded),
        },
        "static_counts": {
            "total": len(static),
            "passed": sum(1 for c in static if c["passed"]),
        },
    }


# ---------------------------------------------------------------------------
# mutation switch (keep/revert demonstration)
# ---------------------------------------------------------------------------
def remove_clause(text: str, clause_id: str) -> str:
    """Delete the sentence carrying a clause. Raises ValueError if absent."""
    sentence = CLAUSE_SENTENCES.get(clause_id)
    if sentence is None:
        if clause_id not in CLAUSES:
            raise ValueError("unknown clause id: %s" % clause_id)
        sentence = CLAUSES[clause_id]
    if sentence not in text:
        raise ValueError("clause %s not present in the prompt" % clause_id)
    return text.replace(sentence, "", 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", default=os.path.join(REPO_ROOT, DEFAULT_PROMPT))
    ap.add_argument("--specs", default=os.path.join(REPO_ROOT, DEFAULT_SPECS))
    ap.add_argument("--repo-root", default=REPO_ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-score", type=float, default=100.0)
    ap.add_argument("--remove-clause", default=None,
                    help="score a mutated COPY of the prompt with this "
                         "clause deleted (never writes to the prompt)")
    args = ap.parse_args(argv)

    # The backlog-supervisor and harness-tune profiles are deliberately
    # self-contained modules (issue #119's smaller-shape fallback).  Route
    # them through the same CLI so callers do not need a second command
    # name, while leaving the shepherd profile below unchanged.
    profile_module = profile_module_for_prompt(
        os.path.realpath(args.prompt))
    if profile_module is not None:
        return profile_module.main(argv)

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
            with open(prompt_path, encoding="utf-8") as fh:
                text = fh.read()
            try:
                mutated = remove_clause(text, args.remove_clause)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            tmpdir = tempfile.mkdtemp(prefix="prompt-check-")
            base = os.path.basename(prompt_path)
            prompt_path = os.path.join(tmpdir, base)
            with open(prompt_path, "w", encoding="utf-8") as fh:
                fh.write(mutated)

        result = run(prompt_path, specs_path, repo_root)
    finally:
        if tmpdir:
            for name in os.listdir(tmpdir):
                os.unlink(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)
    result["prompt"] = label
    if args.remove_clause:
        result["mutation"] = args.remove_clause

    failed_static = [c for c in result["static"] if not c["passed"]]
    failed_specs = [s for s in result["specs"] if not s["passed"]]
    ok = (not failed_static) and (not failed_specs) and \
        result["score"] >= args.min_score
    result["ok"] = ok

    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print("prompt: %s" % result["prompt"])
        if args.remove_clause:
            print("mutation: clause %r removed from a temporary copy"
                  % args.remove_clause)
        print("static checks (%d/%d passed):"
              % (result["static_counts"]["passed"],
                 result["static_counts"]["total"]))
        for c in result["static"]:
            print("  %s %s -- %s" % ("PASS" if c["passed"] else "FAIL",
                                     c["name"], c["detail"]))
        counts = result["spec_counts"]
        print("behavioural specs (%d/%d deterministic passed, %d excluded):"
              % (counts["deterministic_passed"], counts["deterministic"],
                 counts["excluded"]))
        for s in result["specs"]:
            print("  %s %s -> %s (expected %s)%s"
                  % ("PASS" if s["passed"] else "FAIL", s["id"],
                     s["decision"], s["expected_decision"],
                     "  [%s]" % s["detail"] if s["detail"] else ""))
        for s in result["excluded_specs"]:
            print("  SKIP %s -- %s (excluded: %s)"
                  % (s["id"], s["description"], s["reason"]))
        print("score: %s/100" % result["score"])
        print("result: %s" % ("PASS" if ok else "FAIL"))

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
