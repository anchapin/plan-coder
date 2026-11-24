# Test-composition census — plan-coder repo (issue #71, Phase 0 item)

Phase 0 measurement only. No suite changes are proposed or made on the
strength of this census. The census item was added to #71 at Alex's tap on
2026-11-22; this report covers the item's classification for ONE repo.

**Scope narrowing (limitation):** the #71 item asks for a census of each
loop repo. This run covers the plan-coder repo's own `tests/` tree only,
because a worker run is confined to its own worktree and may not read the
other loop repos. Cross-repo shares, and any test of the source hypothesis
across repos, remain unmeasured. [observed: this run's worktree contained
only the plan-coder repo]

## Instrument

`bin/test_composition_census.py` (new, stdlib only) classifies every
`tests/test_*.py` file into exactly one class using deterministic
content-based rules, in precedence order: a file with both (a)-side and
(b)-side signals is a conflict and falls back to (c) marked uncertain;
otherwise the strongest signal wins — (a) artifact/fixture comparison,
(b) property-framework or quantified-invariant signals, (c) a direct
unit target or hand-built asserts. Every file's class, rule, signals, and
test-function count are emitted by
`python3 bin/test_composition_census.py --root . --json`; the per-file
table below is that output, so any reader can re-derive each row.
Classifier behaviour is pinned by `tests/test_test_composition_census.py`
(13 tests: one synthetic file per class, the conflict fallback, complete
coverage of a synthetic tree, durations grouping).

## Class definitions (as implemented)

- (a) golden / black-box — a fixture or input goes in and the output is
  compared against an expected artifact, file, or band.
- (b) property / invariant — asserts a property or invariant over
  generated or parametrized cases, or checks a law/ratchet that must hold
  for all inputs of a kind.
- (c) per-unit — tests one internal helper/module's behaviour directly
  with hand-built inputs.

## Results (base f598d7d + this change, 38 files, 741 test functions)

| class | files | file share | test functions | fn share | wall-clock (s) | wall-clock share |
|---|---|---|---|---|---|---|
| (a) golden / black-box | 7 | 18.4% | 153 | 20.7% | 4.76 | 9.7% |
| (b) property / invariant | 4 | 10.5% | 80 | 10.8% | 5.79 | 11.8% |
| (c) per-unit | 27 | 71.1% | 508 | 68.6% | 38.40 | 78.5% |

Wall-clock: `python3 -m pytest tests/ -q --durations=0` on this VM,
48.95 s of measured test time across 31 files (files whose tests all ran
under the 0.005 s reporting floor contribute no measured seconds);
totals are the classifier's `--durations` aggregation of that output.
[observed: the command and totals above are from the supervisor's
collection run, quoted in the PR proof section]

Full suite in the same run: 738 passed, 1 failed, 2 skipped, 62 subtests
passed in 51.60 s. The one failure
(`tests/test_context_mode.py::GradingTests::test_grade_run_still_detects_an_exit_zero_test_result`)
was proven pre-existing by the stale-test protocol: it fails identically
on the clean base with this change stashed. [observed: stash/re-run/pop
performed at collection]

## Per-file table

| file | class | rule | test functions | uncertain |
|---|---|---|---|---|
| tests/test_agent_timeout_failover.py | per-unit | R5-per-unit | 6 | no |
| tests/test_ast_grep_phase0_scan.py | per-unit | R5-per-unit | 1 | no |
| tests/test_astgrep_matchline.py | per-unit | R1-conflict-artifact-and-property | 4 | yes |
| tests/test_attention.py | per-unit | R5-per-unit | 20 | no |
| tests/test_codemap.py | property / invariant | R4-quantified-invariant | 23 | no |
| tests/test_context_mode.py | per-unit | R5-per-unit | 32 | no |
| tests/test_dashboard.py | golden / black-box | R2-artifact-compare | 9 | no |
| tests/test_dashboard_v2.py | golden / black-box | R2-artifact-compare | 22 | no |
| tests/test_delete_comment_phase0.py | per-unit | R5-per-unit | 16 | no |
| tests/test_deliverable_check.py | per-unit | R5-per-unit | 15 | no |
| tests/test_edd.py | per-unit | R5-per-unit | 9 | no |
| tests/test_experiments.py | per-unit | R5-per-unit | 22 | no |
| tests/test_factory_failure_tickets.py | per-unit | R5-per-unit | 19 | no |
| tests/test_flagbatch.py | per-unit | R5-per-unit | 11 | no |
| tests/test_generic_adapters.py | property / invariant | R4-quantified-invariant | 10 | no |
| tests/test_harness.py | per-unit | R5-per-unit | 92 | no |
| tests/test_headroom_phase0.py | per-unit | R5-per-unit | 21 | no |
| tests/test_headroom_phase1.py | per-unit | R5-per-unit | 12 | no |
| tests/test_judging_files.py | golden / black-box | R2-artifact-compare | 12 | no |
| tests/test_metrics_rollup.py | golden / black-box | R2-artifact-compare | 19 | no |
| tests/test_plan_coder_cli.py | per-unit | R5-per-unit | 13 | no |
| tests/test_ponytail_phase0_landed.py | per-unit | R5-per-unit | 2 | no |
| tests/test_prompt_check.py | golden / black-box | R2-artifact-compare | 24 | no |
| tests/test_provenance_phase0_jittest.py | per-unit | R5-per-unit | 3 | no |
| tests/test_questions_census_phase0.py | per-unit | R5-per-unit | 33 | no |
| tests/test_reader_gate_phase0.py | golden / black-box | R2-artifact-compare | 56 | no |
| tests/test_samesig_phase0.py | per-unit | R5-per-unit | 23 | no |
| tests/test_schema_inventory_phase0.py | per-unit | R5-per-unit | 25 | no |
| tests/test_shepherd_ledger.py | property / invariant | R4-quantified-invariant | 26 | no |
| tests/test_structural_discovery_phase0.py | per-unit | R5-per-unit | 32 | no |
| tests/test_sync_public.py | per-unit | R5-per-unit | 30 | no |
| tests/test_tamper.py | per-unit | R5-per-unit | 20 | no |
| tests/test_test_composition_census.py | per-unit | R1-conflict-artifact-and-property | 13 | yes |
| tests/test_trajectory_coupling_phase0.py | property / invariant | R4-quantified-invariant | 21 | no |
| tests/test_tune_pass_cost.py | per-unit | R5-per-unit | 6 | no |
| tests/test_verify_stamp.py | golden / black-box | R2-artifact-compare | 11 | no |
| tests/test_warm_verifier_census.py | per-unit | R5-per-unit | 16 | no |
| tests/test_worktree_slots.py | per-unit | R5-per-unit | 12 | no |
## Uncertain files (2)

Both fell back to (c) under the conflict rule; the classifier names the
conflicting signals in its JSON output:

- `tests/test_astgrep_matchline.py` — (a)-side artifact handles
  (CLEAN_DIR/VIOLATION_DIR, EXPECTED_PAIRS) conflict with a (b)-side
  quantified-invariant signal (the EXPECTED_PAIRS collection constant).
  Read by hand it is a golden scan over fixture directories; the conflict
  is the instrument being conservative, recorded rather than resolved by
  fiat.
- `tests/test_test_composition_census.py` — the classifier's own test
  file necessarily contains synthetic examples of every class, so its
  content signals conflict by construction. Self-reference, not a
  classification error.

## Hypothesis paragraph

The source claim is that per-unit test share tracks CI wall-clock growth
better than raw test count. With one repo and one point in time there is
no growth series to regress against, so the claim itself is
[unsupported] by this run's data. What this run does establish:
per-unit tests are 68.6% of test functions and 78.5% of measured
wall-clock in this repo [observed: table above], so IF the claim holds
anywhere it would be visible here first [inference]. Testing it needs
the cross-repo census plus a time series (the #71 growth-census part),
which this Phase 0 item does not supply.
