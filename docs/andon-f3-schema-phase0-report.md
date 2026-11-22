# #147 Part F3, Phase 0 — schema & conformance inventory of plan-coder's emitted artifacts

Phase 0, measurement only. No schema was built, no artifact format changed, no
validator added. Parts F1 and F2 of #147 were NOT measured here (they need data
outside the repo); this report covers F3 only.

Reproduce: `python3 bin/schema_inventory_phase0.py --json`
(script: `bin/schema_inventory_phase0.py`; tests: `tests/test_schema_inventory_phase0.py`).
Census method: AST scan over `bin/*.py` for emission sites, patterns P1–P7
(`open(...,"w"/"a"/"x")`, `Path.write_text`, `json.dump`, `json.dumps`,
`fh.write(json.dumps(...))`, `os.fdopen`/`os.replace`, `print(json.dumps(...))`),
46 files scanned. Prompt-driven artifacts cite their prompt line instead.
A schema counts only if machine-readable (a schema file, or a validator in code
that enforces field names/types); a prose field list in a docstring does not
count. A conformance check counts only if a named test/validator runs against
emitted instances or producer output.

## Headline

| metric | value |
|---|---|
| artifacts inventoried | 24 |
| with a machine-readable schema | 5 (20.8%) |
| with a conformance check | 22 (91.7%) |
| with BOTH | 5 (20.8%) |

Decision (per the issue's kill rule): **PROCEED F3** — coverage is NOT
effectively complete (5/24 artifacts have both a schema and a conformance
check = 20.8%), so there is real gap work for a later phase. The pattern is
consistent: conformance-by-test is nearly universal, but the tests assert
shapes ad hoc per tool; only 5 artifacts carry a schema a second consumer
could validate against. Phase 1 (a separate tap) would be the schema work;
Phase 0 changed nothing.

## Inventory

| artifact | producer | format | schema | conformance check | example | both? |
|---|---|---|---|---|---|---|
| worker_ledger.jsonl row (metrics ledger) | prompts/backlog-supervisor.md:46 | JSONL, one JSON object per collected worker | schemas/ledger-line.schema.json | tests/test_plan_coder_cli.py::test_ledger_lines_match_the_ledger_line_schema | examples/worker_ledger.example.jsonl | YES |
| receipts.jsonl row (proof receipt) | bin/receipt.py:136 | JSONL, one JSON object per PR proof receipt | none | tests/test_harness.py::test_record_and_summary_report_coverage_over_window | examples/receipts.example.jsonl | no |
| shepherd_ledger.jsonl row (shepherd action ledger) | bin/shepherd_ledger.py:215 | JSONL, one JSON object per shepherd action | none | tests/test_shepherd_ledger.py::test_all_new_arguments_land_with_the_right_shape |  | no |
| verification_ledger.jsonl row (verification stamp) | bin/verify_stamp.py:241 | JSONL, one JSON object per stamped run | none | tests/test_verify_stamp.py::test_stamp_row_shape_and_collection_separation |  | no |
| goldfish_ledger.jsonl row (brief-review ledger) | bin/goldfish.py:271 | JSONL, one JSON object per brief review | none | tests/test_harness.py::test_log_appends_goldfish_ledger_row |  | no |
| jittest_ledger.jsonl row (hypothesis-test ledger) | bin/jittest.py:1144 | JSONL, one JSON object per JiT run | none | tests/test_harness.py::test_log_appends_jittest_ledger_row_with_adjudication |  | no |
| edd_experiments.jsonl row (tune-bundle experiment) | bin/edd.py:401 | JSONL, one JSON object per edit bundle | none | tests/test_edd.py::test_two_bundles_logged_in_the_same_second_get_distinct_cuts |  | no |
| receipt_capture/<run_id>.json (EDD capture record) | bin/edd.py:344 | JSON document, one per run | none | none |  | no |
| EDD harness fingerprint (stdout JSON document) | bin/edd.py:227 | JSON document on stdout | none | none |  | no |
| experiments.jsonl row (experiment registry) | bin/experiments.py:193 | JSONL, one JSON object per experiment | bin/experiments.py:validate_row | tests/test_experiments.py::test_add_appends_a_row_that_validates | examples/experiments.example.jsonl | YES |
| workers.json (cycle state: workers) | prompts/backlog-supervisor.md:42 | JSON document (written through bin/state_write.py commit) | schemas/workers.schema.json | tests/test_plan_coder_cli.py::test_status_json_matches_the_schemas | examples/workers.example.json | YES |
| shepherd.json (cycle state: tracked PRs) | prompts/pr-shepherd.md:7 | JSON document (written through bin/state_write.py commit) | schemas/shepherd.schema.json | tests/test_plan_coder_cli.py::test_status_json_matches_the_schemas |  | YES |
| next-actions JSON (manager CLI next-moves list) | bin/plan_coder.py:605 | JSON document on stdout | schemas/next-actions.schema.json | tests/test_plan_coder_cli.py::test_next_actions_matches_the_schema |  | YES |
| WORKER_REPORT.md machine manifest | bin/agent.py:638 | markdown bullet block inside WORKER_REPORT.md | none | tests/test_harness.py::test_claim_vs_diff_match_and_mismatch |  | no |
| worker transcript JSONL (<run_id>.jsonl) | bin/agent.py:1117 | JSONL, one event object per step | none | tests/test_harness.py::test_good_run_passes |  | no |
| cron-manifest.json (rendered cron bodies) | bin/install_crons.py:113 | JSON document | none | tests/test_harness.py::test_installer_manifest_full |  | no |
| dashboard HTML page (live view + metrics) | bin/dashboard.py:1358 | self-contained HTML | none | tests/test_dashboard.py::test_renders_both_panels |  | no |
| flag_batch_ledger.jsonl row (flag-batch counts) | bin/flagbatch.py:302 | JSONL, one JSON object per generated batch | none | tests/test_flagbatch.py::test_generate_enabled_writes_view_and_counts |  | no |
| metrics_rollup.jsonl row (PR-lifecycle + daily rows) | bin/metrics_rollup.py:534 | JSONL, one JSON object per PR and per day | none | tests/test_metrics_rollup.py::test_exit_zero_and_rollup_file_written |  | no |
| grade_run verdict (trajectory grade JSON) | bin/grade_run.py:299 | JSON document on stdout | none | tests/test_harness.py::test_good_run_passes |  | no |
| worktree_slots.json (cycle state: persisted worktrees) | bin/worktree_slots.py:119 | JSON document (staged, committed via bin/state_write.py) | none | tests/test_worktree_slots.py::test_record_lookup_roundtrip |  | no |
| judging_files verdict (per-PR touch classification) | bin/judging_files.py:173 | JSON document on stdout | none | tests/test_judging_files.py::test_classes |  | no |
| Phase-0 spike run payload (docs/spikes/*.json) | bin/delete_comment_phase0.py:810 | JSON document (one per spike run) | none | tests/test_delete_comment_phase0.py::test_cli_json_output_is_stable | docs/spikes/delete-comment-phase0-runs.json | no |
| factory-failure ticket (GitHub issue body) | bin/factory_failure_tickets.py:210 | markdown issue body filed on GitHub | none | tests/test_factory_failure_tickets.py::test_issue_body_carries_the_required_fields |  | no |

## Searched for, not found on this base (origin/main)

- **claimlink ledger rows** — grep `claimlink|claim_link` over bin/, prompts/,
  docs/, schemas/, tests/: 0 hits. No producer, reader, or test in this tree.
- **flight-deck inputs** — no producer in bin/ today: docs/METRICS.md still
  names bin/flightdeck.py, which does not exist in this tree (the live view is
  bin/dashboard.py).
- **a distinct dispatch record** — grep over bin/, prompts/, schemas/, docs/:
  0 hits. A dispatch leaves no artifact of its own; its evidence is the
  workers.json entry and the worker_ledger.jsonl row (both inventoried above).

## Verification (supervisor re-runs at collection)

- `python3 bin/schema_inventory_phase0.py --json` → exit 0; coverage
  {artifacts: 24, with_schema: 5, with_conformance_check: 22, with_both: 5,
  coverage_pct: 20.8}.
- `python3 -m pytest tests/test_schema_inventory_phase0.py -q` → 25 passed.
- Full suite after supervisor fallout fix (below):
  `python3 -m pytest tests/test_delete_comment_phase0.py tests/test_schema_inventory_phase0.py -q` → 41 passed;
  the earlier full run was 614 passed / 1 failed (the census failure fixed below).

## Provenance note

The worker run (balanced, MiniMax-M2.7) hit its 60-step cap after producing
the census script and its tests but before writing this report, and spent its
last steps on the repo's delete-comment census test, which classifies every
comment block in new files. Supervisor fallout at collection: removed the new
script's standalone comment-banner blocks (the census failure; the
schema_kind explanation they carried is folded into this report's method
paragraph), re-ran the script (identical coverage output), and authored this
report from the script's JSON output. A second fallout fix followed from the
JiTTest check on the opened PR: the not-found grep counted this report itself
(it names the searched families), so the shipped NotFound test failed on the
PR tree (claimlink hits 3, flight-deck 6). The script now also excludes its
own report file (SELF_REPORT, same mechanism as SELF/SELF_TEST); re-run on
the PR tree gives claimlink 0 / flight-deck 4 / dispatch 0, exactly as this
report states, and the 41-test combined run passes.
