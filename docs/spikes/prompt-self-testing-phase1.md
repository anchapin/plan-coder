# Prompt self-testing — Phase 1 extension (issue #119)

This extends the Phase 0 runner in `bin/prompt_check.py` to all three live
prompt templates.  It is a **coverage extension only**:

* no file under `prompts/` changed;
* no live configuration, cron, or cycle behaviour changed;
* no LLM, promptfoo, network call, or live harness state is involved;
* the shepherd profile keeps the same 10 specs (9 deterministic and 1
  excluded judgement) and the same decisions.

The implementation uses issue #119's smaller-shape fallback.  The shepherd
profile remains in `bin/prompt_check.py`; `bin/prompt_check_supervisor.py` and
`bin/prompt_check_harness_tune.py` are self-contained per-prompt profiles
that share only the 0--100 scoring helper.  `bin/prompt_check.py` routes by
the live prompt filename, so all three profiles retain the same CLI:

```text
python3 bin/prompt_check.py --prompt PATH --specs PATH [--json]
```

## Step 0 — length-budget accounting

The only existing budget change is the shepherd's numbered-section ceiling,
from 8000 to 10000 characters.  Read-only inspection of
`prompts/pr-shepherd.md` history since the #55 seed found these longest
numbered sections on the deployed-template line:

| history point | longest numbered section |
|---|---:|
| issue #25 | 5525 |
| 2026-11-17 reconciliation | 6721 |
| 2026-11-18 template sync | 8071 |
| 2026-11-18 live-preservation merge | 9301 |

The 9301-char section was still inside the 20000-char body budget
(19966 chars), so the old section ceiling rejected otherwise-valid live
prompt text.  10000 is the smallest round ceiling above the largest
accounted section (699 chars of headroom).  The body ceiling (20000), step
count (4--12), and minimum body (3000) did not change.

The other profiles have separately accounted, profile-local ceilings.  Their
current numbered-section sizes are recorded here so a later budget change
can be compared with a known baseline rather than guessed:

| profile | body | numbered-section sizes | body ceiling | section ceiling |
|---|---:|---|---:|---:|
| shepherd | 11993 | 510, 6755, 1727, 2083, 321 | 20000 | 10000 |
| supervisor | 51638 | 1391, 273, 3624, 18806, 1185, 2511, 14180, 7554 | 55000 | 20000 |
| harness-tune | 17467 | 4374, 225, 185, 733, 1960, 1832, 2392, 982, 701 | 35000 | 14000 |

History inspection found larger harness-tune bodies/sections (32073/12616)
than the current checkout; the profile-local ceilings cover that inspected
history as well.  No global budget was relaxed for the supervisor or
harness-tune profiles.

## Build report — clause-to-spec mapping

The machine-readable form of this report is the `coverage` object emitted by
`--json`.  Each deterministic clause is present in the prompt catalogue and
maps to at least one deterministic spec.  The test suite mutates a copy of
the live prompt once per deterministic clause and proves that the mapped
spec fails and names that clause.  No mutation is written to `prompts/`.

### Backlog supervisor — 31 deterministic clauses

| #118 census clause | deterministic spec |
|---|---|
| `collect_existing_first` | `collect_existing_first_spec` |
| `stale_working_collecting_only` | `stale_working_collecting_only_spec` |
| `stale_excludes_escalated_split` | `stale_excludes_escalated_split_spec` |
| `fresh_collecting_skip` | `fresh_collecting_skip_spec` |
| `stale_collecting_reset` | `stale_collecting_reset_spec` |
| `max_parallel_active` | `max_parallel_active_spec` |
| `cycle_token_budget` | `cycle_token_budget_spec` |
| `fetch_before_issue_list` | `fetch_before_issue_list_spec` |
| `backlog_empty_skip` | `backlog_empty_skip_spec` |
| `never_create_issues` | `never_create_issues_spec` |
| `skip_factory_failure` | `skip_factory_failure_spec` |
| `skip_open_pr_issue` | `skip_open_pr_issue_spec` |
| `skip_assigned_issue` | `skip_assigned_issue_spec` |
| `retry_guard` | `retry_guard_spec` |
| `retry_requires_change` | `retry_requires_change_spec` |
| `failover_action` | `failover_action_spec` |
| `failover_reflection` | `failover_reflection_spec` |
| `failover_flag_stops` | `failover_flag_stops_spec` |
| `greenfield_brief_mode` | `greenfield_brief_mode_spec` |
| `premise_symbol_exists` | `premise_symbol_exists_spec` |
| `premise_gate_not_tripped` | `premise_gate_not_tripped_spec` |
| `clear_stale_worktree` | `clear_stale_worktree_spec` |
| `delete_stale_branch` | `delete_stale_branch_spec` |
| `worktree_from_base` | `worktree_from_base_spec` |
| `brief_scope_room` | `brief_scope_room_spec` |
| `brief_proof` | `brief_proof_spec` |
| `optional_deliverable` | `optional_deliverable_spec` |
| `structured_diagnostics` | `structured_diagnostics_spec` |
| `atomic_state_save` | `atomic_state_save_spec` |
| `collect_claim_before_review` | `collect_claim_before_review_spec` |
| `ledger_outcome_required` | `ledger_outcome_required_spec` |

**Unmapped deterministic clauses: none.  Untestable-deterministic clauses:
none.**  Every one of the 31 clauses has a mutation-failing spec.

### Backlog supervisor — 14 judgement clauses (excluded)

These are present in the prompt and represented explicitly as
`deterministic: false` specs.  They are reported as `SKIP` and do not enter
the score.

| #118 census clause | excluded spec |
|---|---|
| `issue_well_specified` | `issue_well_specified_judgement` |
| `issue_priority_judgement` | `issue_priority_judgement` |
| `merge_ordering` | `merge_ordering_judgement` |
| `same_repo_conflict_avoidance` | `same_repo_conflict_avoidance_judgement` |
| `skip_note_judgement` | `skip_note_judgement` |
| `machine_build_budget` | `machine_build_budget_judgement` |
| `failover_narrowing_judgement` | `failover_narrowing_judgement` |
| `diff_review_quality` | `diff_review_quality_judgement` |
| `trivial_fallout_judgement` | `trivial_fallout_judgement` |
| `deadline_progress_judgement` | `deadline_progress_judgement` |
| `split_breakdown_judgement` | `split_breakdown_judgement` |
| `escalation_diagnosis_judgement` | `escalation_diagnosis_judgement` |
| `proof_material_judgement` | `proof_material_judgement` |
| `tier_selection_judgement` | `tier_selection_judgement` |

### Harness tune — 10 deterministic clauses

| #118 census clause | deterministic spec |
|---|---|
| `aggregate_with_script` | `aggregate_with_script_spec` |
| `previous_pass_cost` | `previous_pass_cost_spec` |
| `rates_with_n` | `rates_with_n_spec` |
| `transcript_excerpts` | `transcript_excerpts_spec` |
| `refresh_merge_status` | `refresh_merge_status_spec` |
| `edit_evidence_gate` | `edit_evidence_gate_spec` |
| `small_safe_edits` | `small_safe_edits_spec` |
| `agent_edit_suite_gate` | `agent_edit_suite_gate_spec` |
| `issue_registry_dispositions` | `issue_registry_dispositions_spec` |
| `issue_text_untrusted` | `issue_text_untrusted_spec` |

**Unmapped deterministic clauses: none.  Untestable-deterministic clauses:
none.**  Every one of the 10 clauses has a mutation-failing spec.

### Harness tune — 8 judgement clauses (excluded)

| #118 census clause | excluded spec |
|---|---|
| `failure_class_selection` | `failure_class_selection_judgement` |
| `successful_contrast_selection` | `successful_contrast_selection_judgement` |
| `candidate_edit_selection` | `candidate_edit_selection_judgement` |
| `edit_safety_judgement` | `edit_safety_judgement` |
| `issue_classification_judgement` | `issue_classification_judgement` |
| `incorporate_disposition_judgement` | `incorporate_disposition_judgement` |
| `queue_vs_flag_judgement` | `queue_vs_flag_judgement` |
| `question_answer_judgement` | `question_answer_judgement` |

## Verification

The rendered/validated proof for this extension is the output of the three
profile checks and the repository's publish-gate test suite:

```text
$ python3 bin/prompt_check.py --prompt prompts/pr-shepherd.md --specs tests/fixtures/prompt_specs/pr-shepherd.json
...
score: 100.0/100
result: PASS
$ echo $?
0

$ python3 bin/prompt_check.py --prompt prompts/backlog-supervisor.md --specs tests/fixtures/prompt_specs/backlog-supervisor.json
...
behavioural specs (31/31 deterministic passed, 14 excluded):
...
score: 100.0/100
result: PASS
$ echo $?
0

$ python3 bin/prompt_check.py --prompt prompts/harness-tune.md --specs tests/fixtures/prompt_specs/harness-tune.json
...
behavioural specs (10/10 deterministic passed, 8 excluded):
...
score: 100.0/100
result: PASS
$ echo $?
0
```

The exact final full-suite result is quoted in the change report.
