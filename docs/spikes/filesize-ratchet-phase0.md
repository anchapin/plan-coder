# File-size ratchet — Phase 0 measurement (issue #23)

Phase 0 of the file-size-ratchet spike: measurement before any checker is built.
Produced by `bin/filesize_phase0.py` (committed in this change; re-runnable —
`python3 bin/filesize_phase0.py all` regenerates both tables). Token measure is
bytes/4; the limit is 5,000 tokens. Phase 1 (the shadow checker) is NOT built
here — it is gated on Alex's separate go per the issue.

Supervisor completion note: the worker run built and verified the script and
generated both tables, then hit its step cap while assembling this report; the
supervisor assembled this file from the script's own output verbatim (tables
below are unedited script output) and wrote this header + the verdict section.

## Method notes / classification caveats (from the script, disclosed)

- Retrospective sample: the most recent 60 `pr_opened` rows in
  `worker_ledger.jsonl`; 416 touched files classified.
- A file over the limit at both base and head whose growth is within the
  allowance (max(100 tokens, 2%)) is classified `over-growing` with reason
  `flat` (51 of the 111 over-growing rows). The would-block table therefore
  quotes two rates per repo: the literal rate (any new-over / crossing /
  over-growing non-exempt file blocks) and the threshold-strict rate (flat
  rows do not block). Read the strict rate as the Phase 1-relevant number.
- Measurement status per PR (base derived from the branch-creation reflog
  point) is listed at the end of the retrospective output; all 60 PRs were
  measured.

## Headline numbers

- Would-block rate per repo (threshold-strict; literal in the table below):
  fluxion 100% (7/7 PRs), matchline 100% (2/2), plan-coder 72.7%, portkit-v2
  66.7%, retrofit-copilot 66.7%, building-ai-pocs 42.9%, planar-nexus 42.9%,
  armored-archer 14.3%, staging-studio 0.0%.
- Median head_tokens of files workers actually touched: **2623.5**
  (416 files across 60 PRs).
- Class counts across the sample: under 253, over-growing 111 (51 of them
  `flat`), exempt 30, crossing 14, new-over 6, over-shrinking 2.

## Verdict vs the issue's Kill criterion

The Kill criterion is: would-block rate 0 across the 60-PR sample AND the
inventory not growing. **It is not met** — the strict would-block rate is
non-zero in 8 of 9 repos and the inventory holds hundreds of over-limit files
(fluxion alone: 331 src + 97 test files over). Phase 0 therefore completes
without a Kill; whether Phase 1 (shadow checker, pilot repo staging-studio or
matchline) is worth building is Alex's call per the issue's gating. Note the
pilot-relevant rows: staging-studio's strict rate is 0.0% and matchline's is
100% on a small n=2 sample — the pilot-repo choice should be made on the full
tables below, not the headline alone.

## Raw tables (verbatim script output)

## Inventory baseline (src / test / fixture)

| repo | clone | head | category | files | tokens | max_tokens | over_limit |
|---|---|---|---|---|---|---|---|
| fluxion | fluxion | b00569a6 | src | 1705 | 5938726 | 103514 | 331 |
| fluxion | fluxion | b00569a6 | test | 542 | 2239491 | 424996 | 97 |
| fluxion | fluxion | b00569a6 | fixture | 31 | 112572 | 7249 | 14 |
| staging-studio | staging-studio | 44c6204 | src | 505 | 1048883 | 98046 | 32 |
| staging-studio | staging-studio | 44c6204 | test | 298 | 512744 | 7303 | 8 |
| staging-studio | staging-studio | 44c6204 | fixture | 14 | 42067 | 7420 | 4 |
| matchline | matchline | 6b018fa | src | 92 | 246079 | 80224 | 10 |
| matchline | matchline | 6b018fa | test | 13 | 14170 | 3245 | 0 |
| matchline | matchline | 6b018fa | fixture | 2 | 2692 | 1405 | 0 |
| planar-nexus | planar-nexus | 7859dc2e | src | 2111 | 4418232 | 181722 | 204 |
| planar-nexus | planar-nexus | 7859dc2e | test | 952 | 2858488 | 30351 | 143 |
| planar-nexus | planar-nexus | 7859dc2e | fixture | 34 | 292238 | 105231 | 10 |
| armored-archer | armored-archer | f413e5c7 | src | 2065 | 4379166 | 229258 | 146 |
| armored-archer | armored-archer | f413e5c7 | test | 712 | 1247786 | 54384 | 43 |
| armored-archer | armored-archer | f413e5c7 | fixture | 22 | 38530 | 6690 | 1 |
| building-ai-pocs | building-ai-pocs | 0a91475 | src | 71 | 189334 | 15586 | 12 |
| building-ai-pocs | building-ai-pocs | 0a91475 | test | 25 | 26482 | 2398 | 0 |
| building-ai-pocs | building-ai-pocs | 0a91475 | fixture | 41 | 23270 | 1942 | 0 |
| portkit-v2 | portkit-v2 | 4787fa8 | src | 285 | 1230824 | 229206 | 49 |
| portkit-v2 | portkit-v2 | 4787fa8 | test | 240 | 590659 | 26229 | 16 |
| portkit-v2 | portkit-v2 | 4787fa8 | fixture | 71 | 37102 | 8834 | 2 |
| plan-coder | plan-coder-repo | 5bf3fb8 | src | 26 | 74900 | 16245 | 3 |
| plan-coder | plan-coder-repo | 5bf3fb8 | test | 1 | 17360 | 17360 | 1 |
| plan-coder | plan-coder-repo | 5bf3fb8 | fixture | 0 | 0 | 0 | 0 |
| retrofit-copilot | retrofit-copilot | 17dd58c | src | 13 | 31491 | 9481 | 1 |
| retrofit-copilot | retrofit-copilot | 17dd58c | test | 7 | 8628 | 4145 | 0 |
| retrofit-copilot | retrofit-copilot | 17dd58c | fixture | 0 | 0 | 0 | 0 |

## Inventory: files excluded from the src/test/fixture split

| repo | data_files | binary_files |
|---|---|---|
| fluxion | 307 | 18 |
| staging-studio | 0 | 44 |
| matchline | 2 | 1 |
| planar-nexus | 8 | 29 |
| armored-archer | 4501 | 33 |
| building-ai-pocs | 7 | 12 |
| portkit-v2 | 1346 | 18 |
| plan-coder | 0 | 0 |
| retrofit-copilot | 0 | 0 |

## Retrospective would-block analysis

| run_id | repo | pr | file | class | base_tokens | head_tokens | growth | reason |
|---|---|---|---|---|---|---|---|---|
| fluxion-4776-1794521700 | fluxion | 4788 | benches/spike_warmup_bridge_chunking.rs | over-growing | 7350 | 7997 | 647 |  |
| fluxion-4776-1794521700 | fluxion | 4788 | docs/perf/parallel-in-time-4699.md | under | 3060 | 4454 | 1394 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | AGENTS.md | over-growing | 10462 | 10462 | 0 | flat |
| fluxion-4774-1794522000 | fluxion | 4787 | README.md | over-growing | 6574 | 6574 | 0 | flat |
| fluxion-4774-1794522000 | fluxion | 4787 | benches/spike_warmup_bridge_chunking.rs | over-growing | 7350 | 7997 | 647 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | delta_config.yaml | under | 739 | 746 | 7 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | docs/compliance/section_coverage.md | under | 4402 | 4457 | 55 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | docs/perf/parallel-in-time-4699.md | under | 3060 | 4454 | 1394 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | fluxion-core/src/ground_coupling.rs | under | 4274 | 4349 | 75 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | fluxion-core/src/per_surface_conduction.rs | over-growing | 10426 | 10773 | 347 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | scripts/check_test_inventory_drift.py | over-growing | 23596 | 24298 | 702 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | src/physics/ground_conduction.rs | over-growing | 13021 | 13086 | 65 | flat |
| fluxion-4774-1794522000 | fluxion | 4787 | src/physics/multi_node_solver/mod.rs | over-growing | 18359 | 18376 | 17 | flat |
| fluxion-4774-1794522000 | fluxion | 4787 | src/sim/thermal_model_physics/physics_impl/step_9r4c.rs | over-growing | 26785 | 28609 | 1824 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | src/sim/thermal_model_scratch.rs | over-growing | 5289 | 5790 | 501 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | tests/ashrae_140/ground_periodic.rs | new-over | 0 | 8454 | 8454 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | tests/ashrae_140/main.rs | under | 89 | 137 | 48 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | tests/reference_data/test_inventory_baseline.json | under | 606 | 606 | 0 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | tests/step_9r4c_free_float_contract.rs | over-growing | 26884 | 33970 | 7086 |  |
| fluxion-4774-1794522000 | fluxion | 4787 | tests/test_inventory.json | under | 477 | 477 | 0 |  |
| portkit-v2-298-1794529160 | portkit-v2 | 424 | src/portkit/cli.py | over-growing | 19608 | 20148 | 540 |  |
| portkit-v2-298-1794529160 | portkit-v2 | 424 | src/portkit/converters/loot.py | over-growing | 11192 | 11262 | 70 | flat |
| portkit-v2-298-1794529160 | portkit-v2 | 424 | src/portkit/data/vanilla_block_tags.py | under | 0 | 2940 | 2940 |  |
| portkit-v2-298-1794529160 | portkit-v2 | 424 | src/portkit/datatags.py | crossing | 4283 | 5569 | 1286 |  |
| portkit-v2-298-1794529160 | portkit-v2 | 424 | tests/test_datatags.py | under | 2111 | 3046 | 935 |  |
| portkit-v2-298-1794529160 | portkit-v2 | 424 | tests/test_vanilla_block_tags.py | under | 0 | 1650 | 1650 |  |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | src/portkit/cli.py | over-growing | 19608 | 20148 | 540 |  |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | src/portkit/converters/loot.py | over-growing | 11192 | 11262 | 70 | flat |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | src/portkit/converters/recipes.py | over-growing | 32035 | 33176 | 1141 |  |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | src/portkit/data/vanilla_block_tags.py | under | 0 | 2940 | 2940 |  |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | src/portkit/datatags.py | crossing | 4283 | 5569 | 1286 |  |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | tests/test_datatags.py | under | 2111 | 3046 | 935 |  |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | tests/test_item_list_recipes.py | under | 1679 | 2792 | 1113 |  |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | tests/test_recipe_result_residue.py | under | 2040 | 2066 | 26 |  |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | tests/test_vanilla_block_tags.py | under | 0 | 1650 | 1650 |  |
| building-ai-pocs-32-1794532182 | building-ai-pocs | 35 | README.md | under | 1567 | 1599 | 32 |  |
| building-ai-pocs-32-1794532182 | building-ai-pocs | 35 | scripts/claimcheck.py | under | 0 | 2619 | 2619 |  |
| armored-archer-1826-1794532182 | armored-archer | 1834 | test/run_all_tests.gd | under | 1746 | 1856 | 110 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | AGENTS.md | over-growing | 10462 | 10462 | 0 | flat |
| fluxion-4790-1794532182 | fluxion | 4796 | README.md | over-growing | 6574 | 6574 | 0 | flat |
| fluxion-4790-1794532182 | fluxion | 4796 | delta_config.yaml | under | 739 | 746 | 7 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | fluxion-core/src/per_surface_conduction.rs | over-growing | 10426 | 10773 | 347 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | scripts/check_test_inventory_drift.py | over-growing | 23676 | 24258 | 582 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | scripts/ci/test_surrogate_scaler_convention.py | under | 0 | 4108 | 4108 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | scripts/export_onnx.py | crossing | 4383 | 5080 | 697 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | scripts/surrogate_weight_summary.py | over-growing | 7806 | 7985 | 179 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | scripts/train_surrogate.py | over-growing | 5380 | 5766 | 386 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | src/physics/multi_node_solver/mod.rs | over-growing | 18359 | 18376 | 17 | flat |
| fluxion-4790-1794532182 | fluxion | 4796 | src/sim/thermal_model_physics/physics_impl/step_9r4c.rs | over-growing | 26785 | 28609 | 1824 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | src/sim/thermal_model_scratch.rs | over-growing | 5289 | 5790 | 501 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | tests/reference_data/test_inventory_baseline.json | under | 606 | 606 | 0 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | tests/step_9r4c_free_float_contract.rs | over-growing | 31797 | 33970 | 2173 |  |
| fluxion-4790-1794532182 | fluxion | 4796 | tests/test_inventory.json | under | 477 | 477 | 0 |  |
| staging-studio-1535-1794532182 | staging-studio | 1538 | src/app/api/label-instances/route.ts | under | 1270 | 1416 | 146 |  |
| staging-studio-1535-1794532182 | staging-studio | 1538 | tests/api/label-instances-route.test.ts | under | 3838 | 4365 | 527 |  |
| portkit-v2-274-1794532182 | portkit-v2 | 426 | docs/decisions.md | over-growing | 229206 | 230092 | 886 | flat |
| portkit-v2-274-1794532182 | portkit-v2 | 426 | src/portkit/converters/armor.py | crossing | 4858 | 6230 | 1372 |  |
| portkit-v2-274-1794532182 | portkit-v2 | 426 | src/portkit/converters/recipes.py | over-growing | 32035 | 33176 | 1141 |  |
| portkit-v2-274-1794532182 | portkit-v2 | 426 | tests/test_armor.py | crossing | 3943 | 5102 | 1159 |  |
| portkit-v2-274-1794532182 | portkit-v2 | 426 | tests/test_item_list_recipes.py | under | 1679 | 2792 | 1113 |  |
| portkit-v2-274-1794532182 | portkit-v2 | 426 | tests/test_recipe_result_residue.py | under | 2040 | 2066 | 26 |  |
| planar-nexus-2952-1794532182 | planar-nexus | 2953 | e2e/performance/ai-performance.spec.ts | under | 550 | 1329 | 779 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | AGENTS.md | over-growing | 10462 | 10566 | 104 | flat |
| fluxion-4791-1794532182 | fluxion | 4797 | README.md | over-growing | 6574 | 6574 | 0 | flat |
| fluxion-4791-1794532182 | fluxion | 4797 | delta_config.yaml | under | 739 | 746 | 7 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | docs/compliance/section_coverage.md | under | 4402 | 4457 | 55 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | fluxion-core/src/ground_coupling.rs | under | 4274 | 4349 | 75 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | fluxion-core/src/per_surface_conduction.rs | over-growing | 10426 | 10773 | 347 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | scripts/check_test_inventory_drift.py | over-growing | 23676 | 24412 | 736 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | scripts/ci/test_surrogate_scaler_convention.py | under | 0 | 4108 | 4108 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | scripts/export_onnx.py | crossing | 4383 | 5080 | 697 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | scripts/surrogate_weight_summary.py | over-growing | 7806 | 7985 | 179 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | scripts/train_surrogate.py | over-growing | 5380 | 5766 | 386 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | src/batch_oracle.rs | over-growing | 9806 | 10652 | 846 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | src/physics/ground_conduction.rs | over-growing | 13021 | 13086 | 65 | flat |
| fluxion-4791-1794532182 | fluxion | 4797 | src/physics/multi_node_solver/mod.rs | over-growing | 18359 | 18376 | 17 | flat |
| fluxion-4791-1794532182 | fluxion | 4797 | src/sim/thermal_model_physics/physics_impl/step_9r4c.rs | over-growing | 26785 | 28609 | 1824 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | src/sim/thermal_model_scratch.rs | over-growing | 5289 | 5790 | 501 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | tests/QUARANTINE.md | over-growing | 16357 | 16474 | 117 | flat |
| fluxion-4791-1794532182 | fluxion | 4797 | tests/all_tests/lib_batch_oracle.rs | under | 1494 | 1476 | -18 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | tests/ashrae_140/ground_periodic.rs | new-over | 0 | 8454 | 8454 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | tests/ashrae_140/main.rs | under | 89 | 137 | 48 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | tests/reference_data/test_inventory_baseline.json | under | 606 | 606 | 0 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | tests/step_9r4c_free_float_contract.rs | over-growing | 31797 | 33970 | 2173 |  |
| fluxion-4791-1794532182 | fluxion | 4797 | tests/test_inventory.json | under | 477 | 477 | 0 |  |
| building-ai-pocs-33-1794538025 | building-ai-pocs | 36 | drawing-verifier/REPORT.md | over-shrinking | 9619 | 9611 | -8 |  |
| building-ai-pocs-33-1794538025 | building-ai-pocs | 36 | neural-operator-surrogate/REPORT.md | over-shrinking | 8675 | 8667 | -8 |  |
| portkit-v2-240-1794538029 | portkit-v2 | 427 | src/portkit/cli.py | over-growing | 20148 | 20206 | 58 | flat |
| portkit-v2-240-1794538029 | portkit-v2 | 427 | src/portkit/report.py | crossing | 2135 | 5191 | 3056 |  |
| portkit-v2-240-1794538029 | portkit-v2 | 427 | tests/test_report.py | under | 848 | 2543 | 1695 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | .github/workflows/ci.yml | under | 0 | 478 | 478 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | README.md | crossing | 3241 | 6113 | 2872 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | ROADMAP.md | under | 0 | 4344 | 4344 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | bin/agent.py | over-growing | 9066 | 9722 | 656 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | bin/dry_run.py | under | 0 | 1990 | 1990 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | bin/jittest.py | over-growing | 16245 | 16248 | 3 | flat |
| plan-coder-3-1794538032 | plan-coder | 6 | bin/plan_cli_stub.py | under | 0 | 440 | 440 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | bin/plans.py | under | 0 | 3494 | 3494 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | bin/receipt.py | under | 0 | 3123 | 3123 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | bin/shepherd_ledger.py | under | 1762 | 1771 | 9 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | config/deployment.example.json | under | 1458 | 1833 | 375 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | docs/ARCHITECTURE.md | under | 1384 | 1603 | 219 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | docs/opencode-supervision-investigation.md | new-over | 0 | 11549 | 11549 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | examples/BRIEF.example.md | under | 529 | 714 | 185 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | examples/BRIEF.gap.example.md | under | 0 | 680 | 680 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | examples/BRIEF.visual.example.md | under | 0 | 729 | 729 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | examples/PR_BODY.gap.example.md | under | 0 | 384 | 384 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | examples/PR_BODY.numeric.example.md | under | 0 | 379 | 379 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | examples/PR_BODY.visual.example.md | under | 0 | 303 | 303 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | examples/receipts.example.jsonl | under | 0 | 374 | 374 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | prompts/backlog-supervisor.md | over-growing | 8953 | 9864 | 911 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | prompts/harness-tune.md | under | 3594 | 3596 | 2 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | prompts/pr-shepherd.md | under | 1998 | 2000 | 2 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | ruff.toml | under | 0 | 1841 | 1841 |  |
| plan-coder-3-1794538032 | plan-coder | 6 | tests/test_harness.py | over-growing | 17360 | 20898 | 3538 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/animations/test_death_animations.gd | under | 0 | 46 | 46 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/animations/test_death_animations.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/autoloads/test_const.gd | under | 0 | 42 | 42 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/autoloads/test_const.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/autoloads/test_design_tokens.gd | under | 0 | 48 | 48 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/autoloads/test_design_tokens.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/autoloads/test_enemy_ai.gd | under | 0 | 45 | 45 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/autoloads/test_enemy_ai.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/campaign/test_campaign_persistence.gd | under | 0 | 49 | 49 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/campaign/test_campaign_persistence.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/combat/test_combat_juice_integration.gd | under | 0 | 50 | 50 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/combat/test_combat_juice_integration.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/combat/test_impact_manager.gd | under | 0 | 47 | 47 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/combat/test_impact_manager.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/combat/test_weapon_balance_manager.gd | under | 0 | 51 | 51 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/combat/test_weapon_balance_manager.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/gear/test_gear_balance_calculator.gd | under | 0 | 52 | 52 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/gear/test_gear_balance_calculator.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/network/test_matchmaking_analytics_manager_legacy.gd | under | 0 | 86 | 86 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/network/test_matchmaking_analytics_manager_legacy.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/network/test_matchmaking_pool_manager.gd | under | 0 | 48 | 48 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/network/test_matchmaking_pool_manager.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/network/test_player_rating_manager.gd | under | 0 | 46 | 46 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/network/test_player_rating_manager.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/progression/test_xp_manager.gd | under | 0 | 45 | 45 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/progression/test_xp_manager.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/season/test_season_leaderboard.gd | under | 0 | 48 | 48 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/season/test_season_leaderboard.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/season/test_season_messaging.gd | under | 0 | 46 | 46 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/season/test_season_messaging.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/ui/test_damage_overlay.gd | under | 0 | 50 | 50 |  |
| armored-archer-1827-1794538386 | armored-archer | 1839 | test/suites/ui/test_damage_overlay.gd.uid | under | 0 | 5 | 5 |  |
| staging-studio-1536-1794537980 | staging-studio | 1539 | src/app/api/v1/export-pdf/route.ts | under | 2861 | 3516 | 655 |  |
| staging-studio-1536-1794537980 | staging-studio | 1539 | src/lib/browserless.ts | under | 1119 | 1466 | 347 |  |
| staging-studio-1536-1794537980 | staging-studio | 1539 | tests/api/v1-export-pdf-route.test.ts | under | 3470 | 4780 | 1310 |  |
| planar-nexus-2951-1794538014 | planar-nexus | 2954 | docs/TEST_VIDEO_FIXTURES.md | exempt | 1870 | 1870 | 0 | generated-header |
| planar-nexus-2951-1794538014 | planar-nexus | 2954 | docs/onboarding.md | under | 1300 | 1300 | 0 |  |
| planar-nexus-2951-1794538014 | planar-nexus | 2954 | src/lib/game-state/__tests__/bloodrush.test.ts | under | 0 | 2435 | 2435 |  |
| planar-nexus-2951-1794538014 | planar-nexus | 2954 | src/lib/game-state/keyword-actions.ts | under | 572 | 583 | 11 |  |
| planar-nexus-2951-1794538014 | planar-nexus | 2954 | src/lib/game-state/keyword-actions/bloodrush.ts | under | 0 | 1102 | 1102 |  |
| planar-nexus-2951-1794538014 | planar-nexus | 2954 | src/lib/game-state/keyword-actions/hand-activations.ts | under | 2087 | 2628 | 541 |  |
| matchline-1383-1794537997 | matchline | 1388 | .github/workflows/ci.yml | over-growing | 8024 | 8036 | 12 | flat |
| matchline-1383-1794537997 | matchline | 1388 | AGENTS.md | under | 1032 | 1032 | 0 |  |
| matchline-1383-1794537997 | matchline | 1388 | README.md | under | 2435 | 2435 | 0 |  |
| matchline-1383-1794537997 | matchline | 1388 | datasets_adapter.py | over-growing | 15643 | 15937 | 294 | flat |
| matchline-1383-1794537997 | matchline | 1388 | docs/README.md | under | 1730 | 1730 | 0 |  |
| matchline-1383-1794537997 | matchline | 1388 | docs/api_reference.md | over-growing | 5382 | 5411 | 29 | flat |
| matchline-1383-1794537997 | matchline | 1388 | docs/ci.md | under | 2525 | 2525 | 0 |  |
| matchline-1383-1794537997 | matchline | 1388 | docs/repo-map.md | exempt | 3409 | 3420 | 11 | generated-header |
| matchline-1383-1794537997 | matchline | 1388 | docs/repo-map.mmd | exempt | 3335 | 3346 | 11 | generated-header |
| matchline-1383-1794537997 | matchline | 1388 | ingest/pdf_schedules.py | over-growing | 9770 | 10223 | 453 |  |
| matchline-1383-1794537997 | matchline | 1388 | tests/api_surface.json | over-growing | 6660 | 6694 | 34 | flat |
| matchline-1383-1794537997 | matchline | 1388 | tests/test_datasets_adapter.py | over-growing | 6614 | 6922 | 308 |  |
| fluxion-4794-1794538320 | fluxion | 4799 | AGENTS.md | over-growing | 10462 | 10462 | 0 | flat |
| fluxion-4794-1794538320 | fluxion | 4799 | ARCHITECTURE.md | over-growing | 38023 | 38023 | 0 | flat |
| fluxion-4794-1794538320 | fluxion | 4799 | scripts/check_physics_sim_cycle.py | under | 4558 | 4700 | 142 |  |
| fluxion-4794-1794538320 | fluxion | 4799 | scripts/check_test_inventory_drift.py | over-growing | 24412 | 24491 | 79 | flat |
| fluxion-4794-1794538320 | fluxion | 4799 | src/sim/boundary.rs | over-growing | 11248 | 13182 | 1934 |  |
| fluxion-4794-1794538320 | fluxion | 4799 | tests/test_inventory.json | under | 477 | 477 | 0 |  |
| staging-studio-1537-1794541411 | staging-studio | 1540 | AGENTS.md | over-growing | 13262 | 13262 | 0 | flat |
| fluxion-4793-1794541298 | fluxion | 4802 | scripts/refresh_ignored_tests_baseline.py | under | 0 | 2165 | 2165 |  |
| fluxion-4793-1794541298 | fluxion | 4802 | tests/QUARANTINE.md | over-growing | 16474 | 16771 | 297 | flat |
| fluxion-4793-1794541298 | fluxion | 4802 | tests/reference_data/ignored_tests_baseline.txt | over-growing | 5487 | 8223 | 2736 |  |
| portkit-v2-340-1794541461 | portkit-v2 | 428 | src/portkit/advancements.py | under | 3055 | 3471 | 416 |  |
| portkit-v2-340-1794541461 | portkit-v2 | 428 | tests/test_advancements.py | under | 1848 | 2998 | 1150 |  |
| armored-archer-1823-1794541441 | armored-archer | 1840 | autoloads/CombatJuiceManager.gd | under | 4315 | 4462 | 147 |  |
| armored-archer-1823-1794541441 | armored-archer | 1840 | scripts/character_body_2d.gd | crossing | 4285 | 5467 | 1182 |  |
| armored-archer-1823-1794541441 | armored-archer | 1840 | test/suites/combat/test_hit_reactions.gd | under | 0 | 83 | 83 |  |
| armored-archer-1823-1794541441 | armored-archer | 1840 | test/suites/combat/test_hit_reactions.gd.uid | under | 0 | 5 | 5 |  |
| armored-archer-1823-1794541441 | armored-archer | 1840 | test/test_hit_reactions.gd | under | 1002 | 1444 | 442 |  |
| planar-nexus-2950-1794541431 | planar-nexus | 2955 | src/lib/game-state/oracle-text-parser/alternative-costs.ts | crossing | 4917 | 5595 | 678 |  |
| planar-nexus-2950-1794541431 | planar-nexus | 2955 | src/lib/game-state/spell-casting/cast.ts | over-growing | 22946 | 24564 | 1618 |  |
| planar-nexus-2950-1794541431 | planar-nexus | 2955 | src/lib/game-state/validation-service.ts | over-growing | 7749 | 7861 | 112 | flat |
| plan-coder-1-1794541471 | plan-coder | 7 | README.md | under | 3241 | 3781 | 540 |  |
| plan-coder-1-1794541471 | plan-coder | 7 | bin/agent.py | over-growing | 9066 | 9722 | 656 |  |
| plan-coder-1-1794541471 | plan-coder | 7 | bin/plan_cli_stub.py | under | 0 | 440 | 440 |  |
| plan-coder-1-1794541471 | plan-coder | 7 | bin/plans.py | under | 0 | 3494 | 3494 |  |
| plan-coder-1-1794541471 | plan-coder | 7 | config/deployment.example.json | under | 1458 | 1834 | 376 |  |
| plan-coder-1-1794541471 | plan-coder | 7 | docs/ARCHITECTURE.md | under | 1384 | 1603 | 219 |  |
| plan-coder-1-1794541471 | plan-coder | 7 | tests/test_harness.py | over-growing | 17360 | 18637 | 1277 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | AGENTS.md | over-growing | 10462 | 10462 | 0 | flat |
| fluxion-4789-1794541401 | fluxion | 4803 | ARCHITECTURE.md | over-growing | 38023 | 38023 | 0 | flat |
| fluxion-4789-1794541401 | fluxion | 4803 | README.md | over-growing | 6574 | 6574 | 0 | flat |
| fluxion-4789-1794541401 | fluxion | 4803 | docs/investigations/fins-sky-diffuse-isotropic-scoping.md | under | 1125 | 1654 | 529 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | fluxion-core/src/construction.rs | over-growing | 20762 | 21680 | 918 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | scripts/check_physics_sim_cycle.py | under | 4558 | 4773 | 215 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | scripts/check_test_inventory_drift.py | over-growing | 24412 | 25018 | 606 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | scripts/ci/test_check_module_size.py | over-growing | 8259 | 8264 | 5 | flat |
| fluxion-4789-1794541401 | fluxion | 4803 | scripts/data/module_size_limits.json | under | 2204 | 2297 | 93 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | scripts/refresh_ignored_tests_baseline.py | under | 0 | 2165 | 2165 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | src/sim/boundary.rs | over-growing | 11248 | 16200 | 4952 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | src/sim/longwave_exchange.rs | over-growing | 8388 | 8722 | 334 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | src/sim/thermal_model_core/mod.rs | over-growing | 57827 | 58058 | 231 | flat |
| fluxion-4789-1794541401 | fluxion | 4803 | src/sim/thermal_model_physics/zone_balance.rs | over-growing | 63781 | 64093 | 312 | flat |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/QUARANTINE.md | over-growing | 16474 | 16771 | 297 | flat |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/longwave_pool_exchange_contract.rs | over-growing | 6827 | 7561 | 734 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/reference_data/ignored_tests_baseline.txt | over-growing | 5487 | 8223 | 2736 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/reference_data/module_size/thermal_model_core_mod_ratchet.json | under | 951 | 1057 | 106 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/reference_data/module_size/zone_balance_ratchet.json | under | 209 | 332 | 123 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/reference_data/test_inventory_baseline.json | under | 606 | 603 | -3 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/step_5r1c_ramp_persistence_contract.rs | under | 0 | 3594 | 3594 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/step_5r1c_ramp_persistence_mass_guard_contract.rs | under | 0 | 1287 | 1287 |  |
| fluxion-4789-1794541401 | fluxion | 4803 | tests/test_inventory.json | under | 477 | 477 | 0 |  |
| armored-archer-1830-1794545630 | armored-archer | 1841 | scenes/ui/matchmaking_menu.gd | under | 3495 | 3498 | 3 |  |
| armored-archer-1830-1794545630 | armored-archer | 1841 | scenes/ui/matchmaking_menu.tscn | under | 1864 | 1918 | 54 |  |
| armored-archer-1830-1794545630 | armored-archer | 1841 | test/suites/ui/test_matchmaking_menu_quick_join_1830.gd | under | 0 | 2496 | 2496 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | bin/receipt.py | under | 0 | 3123 | 3123 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | examples/BRIEF.example.md | under | 529 | 714 | 185 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | examples/BRIEF.gap.example.md | under | 0 | 680 | 680 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | examples/BRIEF.visual.example.md | under | 0 | 729 | 729 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | examples/PR_BODY.gap.example.md | under | 0 | 384 | 384 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | examples/PR_BODY.numeric.example.md | under | 0 | 380 | 380 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | examples/PR_BODY.visual.example.md | under | 0 | 304 | 304 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | examples/receipts.example.jsonl | under | 0 | 376 | 376 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | prompts/backlog-supervisor.md | over-growing | 8953 | 9862 | 909 |  |
| plan-coder-8-1794545650 | plan-coder | 9 | tests/test_harness.py | over-growing | 18637 | 20679 | 2042 |  |
| planar-nexus-2949-1794545620 | planar-nexus | 2957 | src/lib/game-state/__tests__/enchant.test.ts | under | 2156 | 3200 | 1044 |  |
| planar-nexus-2949-1794545620 | planar-nexus | 2957 | src/lib/game-state/card-instance.ts | over-growing | 5905 | 5938 | 33 | flat |
| planar-nexus-2949-1794545620 | planar-nexus | 2957 | src/lib/game-state/keyword-actions/enchant.ts | under | 2282 | 3511 | 1229 |  |
| planar-nexus-2949-1794545620 | planar-nexus | 2957 | src/lib/game-state/legal-choices.ts | over-growing | 5639 | 5720 | 81 | flat |
| planar-nexus-2949-1794545620 | planar-nexus | 2957 | src/lib/game-state/spell-casting/cast.ts | over-growing | 22946 | 22947 | 1 | flat |
| planar-nexus-2949-1794545620 | planar-nexus | 2957 | src/lib/game-state/spell-casting/resolve.ts | over-growing | 10698 | 10735 | 37 | flat |
| planar-nexus-2949-1794545620 | planar-nexus | 2957 | src/lib/game-state/types/cards.ts | over-growing | 10065 | 10115 | 50 | flat |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | fixtures/transcripts/residue_mod.jsonl | exempt | 1046 | 1038 | -8 | fixture |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | src/portkit/converters/recipes.py | over-growing | 33176 | 34417 | 1241 |  |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | tests/test_bare_tag_ingredients.py | under | 3404 | 3444 | 40 |  |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | tests/test_budgets.py | under | 2088 | 2103 | 15 |  |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | tests/test_convention_tags.py | over-growing | 6882 | 6882 | 0 | flat |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | tests/test_repeat.py | under | 2323 | 2328 | 5 |  |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | tests/test_residue_agent.py | under | 2960 | 2979 | 19 |  |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | tests/test_tags.py | under | 1554 | 1606 | 52 |  |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | tests/test_transcripts.py | under | 1578 | 1582 | 4 |  |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | tests/test_unknown_membership_recipes.py | under | 0 | 925 | 925 |  |
| armored-archer-1825-1794550486 | armored-archer | 1842 | scenes/ui/campaign_map.gd | over-growing | 6982 | 7020 | 38 | flat |
| armored-archer-1825-1794550486 | armored-archer | 1842 | scenes/ui/login_screen.gd | under | 2386 | 2458 | 72 |  |
| armored-archer-1825-1794550486 | armored-archer | 1842 | scenes/ui/main_menu.gd | under | 4140 | 4158 | 18 |  |
| matchline-1384-1794550393 | matchline | 1391 | .github/workflows/ci.yml | over-growing | 8036 | 8070 | 34 | flat |
| matchline-1384-1794550393 | matchline | 1391 | AGENTS.md | under | 1032 | 1032 | 0 |  |
| matchline-1384-1794550393 | matchline | 1391 | README.md | under | 2435 | 2435 | 0 |  |
| matchline-1384-1794550393 | matchline | 1391 | docs/README.md | under | 1730 | 1730 | 0 |  |
| matchline-1384-1794550393 | matchline | 1391 | docs/ci.md | under | 2525 | 2536 | 11 |  |
| matchline-1384-1794550393 | matchline | 1391 | docs/room_labels.md | under | 1330 | 1708 | 378 |  |
| matchline-1384-1794550393 | matchline | 1391 | link/_api.py | under | 1359 | 1415 | 56 |  |
| matchline-1384-1794550393 | matchline | 1391 | link/_elevation.py | under | 3186 | 3256 | 70 |  |
| matchline-1384-1794550393 | matchline | 1391 | link/_schedules.py | under | 228 | 924 | 696 |  |
| matchline-1384-1794550393 | matchline | 1391 | room_labels.py | crossing | 4032 | 6296 | 2264 |  |
| matchline-1384-1794550393 | matchline | 1391 | tests/test_elevation_schedule_join.py | under | 0 | 2476 | 2476 |  |
| matchline-1384-1794550393 | matchline | 1391 | tests/test_room_labels.py | under | 1112 | 2871 | 1759 |  |
| planar-nexus-2948-1794550549 | planar-nexus | 2959 | e2e/global-setup.ts | under | 1064 | 1534 | 470 |  |
| planar-nexus-2948-1794550549 | planar-nexus | 2959 | playwright.config.ts | under | 1161 | 1393 | 232 |  |
| planar-nexus-2948-1794550549 | planar-nexus | 2959 | scripts/data/decks/track2-tuned-decks.json | under | 0 | 4040 | 4040 |  |
| armored-archer-1832-1794554800 | armored-archer | 1843 | scenes/ui/game_over.gd | under | 1856 | 1970 | 114 |  |
| armored-archer-1832-1794554800 | armored-archer | 1843 | scenes/ui/matchmaking_menu.gd | under | 3498 | 3745 | 247 |  |
| armored-archer-1832-1794554800 | armored-archer | 1843 | test/suites/ui/test_ui_text_contrast_1832.gd | under | 0 | 2194 | 2194 |  |
| portkit-v2-299-1794554800 | portkit-v2 | 430 | src/portkit/converters/loot.py | over-growing | 11262 | 13116 | 1854 |  |
| portkit-v2-299-1794554800 | portkit-v2 | 430 | tests/test_loot.py | over-growing | 7302 | 8196 | 894 |  |
| portkit-v2-335-1794557040 | portkit-v2 | 431 | src/portkit/ingame/runtime/ingame.js | over-growing | 9558 | 11843 | 2285 |  |
| portkit-v2-335-1794557040 | portkit-v2 | 431 | src/portkit/ingame/runtime/ingame_core.js | under | 2921 | 3422 | 501 |  |
| portkit-v2-335-1794557040 | portkit-v2 | 431 | src/portkit/ingame/testpack.py | over-growing | 8391 | 9704 | 1313 |  |
| portkit-v2-335-1794557040 | portkit-v2 | 431 | tests/js/ingame_core.test.mjs | under | 2398 | 2808 | 410 |  |
| portkit-v2-335-1794557040 | portkit-v2 | 431 | tests/test_ingame.py | crossing | 4469 | 5332 | 863 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | bin/jittest.py | over-growing | 16245 | 16248 | 3 | flat |
| plan-coder-2-1794557301 | plan-coder | 11 | bin/shepherd_ledger.py | under | 1762 | 1771 | 9 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | config/deployment.example.json | under | 1834 | 1833 | -1 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | examples/PR_BODY.gap.example.md | under | 384 | 384 | 0 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | examples/PR_BODY.numeric.example.md | under | 380 | 379 | -1 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | examples/PR_BODY.visual.example.md | under | 304 | 303 | -1 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | examples/receipts.example.jsonl | under | 376 | 374 | -2 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | prompts/backlog-supervisor.md | over-growing | 9862 | 9864 | 2 | flat |
| plan-coder-2-1794557301 | plan-coder | 11 | prompts/harness-tune.md | under | 3594 | 3596 | 2 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | prompts/pr-shepherd.md | under | 1998 | 2000 | 2 |  |
| plan-coder-2-1794557301 | plan-coder | 11 | tests/test_harness.py | over-growing | 20679 | 20682 | 3 | flat |
| plan-coder-5-1794559850 | plan-coder | 12 | README.md | under | 3781 | 3841 | 60 |  |
| plan-coder-5-1794559850 | plan-coder | 12 | ROADMAP.md | under | 0 | 4344 | 4344 |  |
| planar-nexus-2958-1794573255 | planar-nexus | 2961 | e2e/helpers/navigation.ts | under | 0 | 1245 | 1245 |  |
| planar-nexus-2958-1794573255 | planar-nexus | 2961 | e2e/multiplayer-lobby.spec.ts | under | 2853 | 3026 | 173 |  |
| planar-nexus-2958-1794573255 | planar-nexus | 2961 | e2e/sealed-mode.spec.ts | over-growing | 5936 | 6448 | 512 |  |
| planar-nexus-2958-1794573255 | planar-nexus | 2961 | e2e/single-player.spec.ts | under | 1793 | 2200 | 407 |  |
| planar-nexus-2958-1794573255 | planar-nexus | 2961 | e2e/test-utils.ts | under | 1118 | 1198 | 80 |  |
| planar-nexus-2958-1794573255 | planar-nexus | 2961 | scripts/data/decks/track2-tuned-decks.json | under | 4040 | 2959 | -1081 |  |
| armored-archer-1831-1794573188 | armored-archer | 1844 | scenes/ui/components/base_button.gd | under | 2522 | 3655 | 1133 |  |
| armored-archer-1831-1794573188 | armored-archer | 1844 | test/suites/ui/test_base_button_anim_guard_1831.gd | under | 0 | 4334 | 4334 |  |
| portkit-v2-296-1794573369 | portkit-v2 | 432 | src/portkit/adapters/__init__.py | over-growing | 6644 | 7807 | 1163 |  |
| portkit-v2-296-1794573369 | portkit-v2 | 432 | src/portkit/converters/blocks.py | over-growing | 80625 | 81122 | 497 | flat |
| portkit-v2-296-1794573369 | portkit-v2 | 432 | src/portkit/converters/doors.py | over-growing | 8380 | 9880 | 1500 |  |
| portkit-v2-296-1794573369 | portkit-v2 | 432 | tests/test_two_tall_facing.py | under | 0 | 1466 | 1466 |  |
| portkit-v2-302-1794577257 | portkit-v2 | 433 | src/portkit/converters/recipes.py | over-growing | 34417 | 35036 | 619 | flat |
| portkit-v2-302-1794577257 | portkit-v2 | 433 | tests/test_item_list_recipes.py | under | 2792 | 4548 | 1756 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/README.md | under | 0 | 2144 | 2144 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/REPORT.md | under | 0 | 1458 | 1458 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/adversarial/requests.json | under | 0 | 1124 | 1124 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/assembler.py | new-over | 0 | 7930 | 7930 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/catalog.py | under | 0 | 2876 | 2876 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/baseline_throwaway.py | exempt | 0 | 304 | 304 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/case_requests.json | exempt | 0 | 699 | 699 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/gen_fixtures.py | exempt | 0 | 3669 | 3669 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/ground_truth/case1_ashrae140.ground_truth.json | exempt | 0 | 349 | 349 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/ground_truth/case2_ground_coupled.ground_truth.json | exempt | 0 | 312 | 312 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/ground_truth/case3_humid_latent.ground_truth.json | exempt | 0 | 305 | 305 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/requests_out_of_catalog.json | exempt | 0 | 628 | 628 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/runs/run_ashrae140_case1.json | exempt | 0 | 602 | 602 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/runs/run_ground_coupled_case2.json | exempt | 0 | 684 | 684 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/fixtures/runs/run_humid_latent_case3.json | exempt | 0 | 697 | 697 | fixture |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/manifest.py | under | 0 | 1056 | 1056 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/renderer.py | under | 0 | 3102 | 3102 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/run_poc.py | under | 0 | 4200 | 4200 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/tests/conftest.py | under | 0 | 116 | 116 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/tests/test_assembler.py | under | 0 | 1560 | 1560 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/tests/test_catalog.py | under | 0 | 680 | 680 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/tests/test_fixtures.py | under | 0 | 750 | 750 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/tests/test_manifest.py | under | 0 | 584 | 584 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/tests/test_renderer.py | under | 0 | 1624 | 1624 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/tests/test_run_poc.py | under | 0 | 361 | 361 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/tests/test_uispec.py | under | 0 | 1179 | 1179 |  |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | generative-ui-diagnostic-lens/uispec.py | under | 0 | 2695 | 2695 |  |
| building-ai-pocs-41-1794584730 | building-ai-pocs | 42 | generative-ui-diagnostic-lens/assembler.py | over-growing | 7930 | 8871 | 941 |  |
| building-ai-pocs-41-1794584730 | building-ai-pocs | 42 | generative-ui-diagnostic-lens/catalog.py | under | 2876 | 3382 | 506 |  |
| building-ai-pocs-41-1794584730 | building-ai-pocs | 42 | generative-ui-diagnostic-lens/tests/test_unit_vocabulary.py | under | 0 | 1622 | 1622 |  |
| plan-coder-10-1794588962 | plan-coder | 14 | docs/opencode-supervision-investigation.md | new-over | 0 | 11549 | 11549 |  |
| portkit-v2-434-1794595360 | portkit-v2 | 435 | .gitignore | under | 15 | 18 | 3 |  |
| portkit-v2-434-1794595360 | portkit-v2 | 435 | pyproject.toml | under | 333 | 338 | 5 |  |
| portkit-v2-434-1794595360 | portkit-v2 | 435 | tests/test_property_converters.py | under | 0 | 4366 | 4366 |  |
| plan-coder-15-1794595360 | plan-coder | 19 | bin/test_tamper.py | new-over | 0 | 5563 | 5563 |  |
| plan-coder-15-1794595360 | plan-coder | 19 | config/deployment.example.json | under | 1833 | 1974 | 141 |  |
| plan-coder-15-1794595360 | plan-coder | 19 | prompts/pr-shepherd.md | under | 2000 | 2361 | 361 |  |
| plan-coder-15-1794595360 | plan-coder | 19 | tests/test_tamper.py | under | 0 | 3232 | 3232 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/README.md | under | 0 | 262 | 262 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/REPORT.md | under | 0 | 1322 | 1322 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/contracts.py | under | 0 | 2650 | 2650 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/geometry_slice.py | under | 0 | 2056 | 2056 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/tests/conftest.py | under | 0 | 59 | 59 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/tests/test_contracts_hypothesis.py | under | 0 | 860 | 860 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/tests/test_slice.py | under | 0 | 1470 | 1470 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/tools/contract_search.py | under | 0 | 2274 | 2274 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/tools/repro_upstream_bug.py | under | 0 | 798 | 798 |  |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | smt-contract-checking/tools/run_crosshair.sh | under | 0 | 535 | 535 |  |
| plan-coder-17-1794598324 | plan-coder | 20 | docs/OPERATIONS.md | under | 1963 | 2366 | 403 |  |
| plan-coder-17-1794598324 | plan-coder | 20 | prompts/backlog-supervisor.md | over-growing | 9864 | 10155 | 291 |  |
| plan-coder-17-1794598324 | plan-coder | 20 | prompts/harness-tune.md | under | 3596 | 3732 | 136 |  |
| plan-coder-17-1794598324 | plan-coder | 20 | prompts/pr-shepherd.md | under | 2000 | 2111 | 111 |  |
| plan-coder-17-1794598324 | plan-coder | 20 | tests/test_harness.py | over-growing | 20898 | 21228 | 330 | flat |
| building-ai-pocs-45-1794600861 | building-ai-pocs | 49 | drawing-verifier/checker.py | over-growing | 8846 | 9090 | 244 |  |
| building-ai-pocs-45-1794600861 | building-ai-pocs | 49 | drawing-verifier/tests/test_wall_adjacency_overlap.py | under | 0 | 969 | 969 |  |
| plan-coder-25-1794600861 | plan-coder | 31 | docs/task-register.md | under | 0 | 4554 | 4554 |  |
| plan-coder-25-1794600861 | plan-coder | 31 | prompts/pr-shepherd.md | under | 2472 | 2637 | 165 |  |
| plan-coder-25-1794600861 | plan-coder | 31 | tests/test_harness.py | over-growing | 21228 | 21686 | 458 |  |
| portkit-v2-436-1794600861 | portkit-v2 | 437 | src/portkit/converters/blocks.py | over-growing | 81122 | 81284 | 162 | flat |
| portkit-v2-436-1794600861 | portkit-v2 | 437 | tests/test_blocks.py | under | 4103 | 4426 | 323 |  |
| portkit-v2-436-1794600861 | portkit-v2 | 437 | tests/test_property_converters.py | under | 4366 | 4469 | 103 |  |
| retrofit-copilot-3-1794603432 | retrofit-copilot | 8 | config/factors.placeholder.json | under | 0 | 40 | 40 |  |
| retrofit-copilot-3-1794603432 | retrofit-copilot | 8 | src/ecms.py | over-growing | 9481 | 11186 | 1705 |  |
| retrofit-copilot-3-1794603432 | retrofit-copilot | 8 | src/run_ecms.py | crossing | 4556 | 5760 | 1204 |  |
| retrofit-copilot-3-1794603432 | retrofit-copilot | 8 | tests/test_factors_config.py | under | 0 | 3772 | 3772 |  |
| plan-coder-16-1794603464 | plan-coder | 32 | docs/OPERATIONS.md | under | 2366 | 2992 | 626 |  |
| plan-coder-16-1794603464 | plan-coder | 32 | prompts/backlog-supervisor.md | over-growing | 10155 | 11553 | 1398 |  |
| staging-studio-1543-1794603412 | staging-studio | 1544 | src/app/(auth)/login/page.tsx | under | 2468 | 2527 | 59 |  |
| staging-studio-1543-1794603412 | staging-studio | 1544 | src/app/(dashboard)/projects/[id]/lookbook/auto-textarea.tsx | under | 208 | 611 | 403 |  |
| staging-studio-1543-1794603412 | staging-studio | 1544 | src/components/canvas/global-staging-directives-bar.tsx | under | 3865 | 4078 | 213 |  |
| staging-studio-1543-1794603412 | staging-studio | 1544 | tests/global-staging-directives-bar-ime-enter.test.tsx | under | 0 | 1319 | 1319 |  |
| staging-studio-1543-1794603412 | staging-studio | 1544 | tests/login-autocomplete.test.tsx | under | 0 | 327 | 327 |  |
| staging-studio-1543-1794603412 | staging-studio | 1544 | tests/lookbook-auto-textarea-field-sizing.test.tsx | under | 0 | 550 | 550 |  |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | docs/TEST_VIDEO_FIXTURES.md | exempt | 1870 | 1870 | 0 | generated-header |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | docs/onboarding.md | under | 1300 | 1300 | 0 |  |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | src/app/(app)/deck-builder/_components/card-search.tsx | over-growing | 11511 | 11728 | 217 | flat |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | src/app/(app)/deck-builder/_components/import-export-controls.tsx | over-growing | 6362 | 6602 | 240 |  |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | src/app/register/__tests__/register-page.test.tsx | under | 0 | 445 | 445 |  |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | src/app/register/page.tsx | under | 1473 | 1548 | 75 |  |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | src/components/chat/__tests__/chat-input.test.tsx | under | 0 | 1439 | 1439 |  |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | src/components/chat/chat-input.tsx | under | 638 | 950 | 312 |  |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | src/components/trade-dialog.tsx | crossing | 4774 | 5024 | 250 |  |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/README.md | exempt | 0 | 557 | 557 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE1-calibrated-20261106T110817Z-479c57/manifest.json | exempt | 0 | 389 | 389 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE1-true-20261106T111248Z-e7d88d/manifest.json | exempt | 0 | 379 | 379 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE2-calibrated-20261106T110859Z-45ced3/manifest.json | exempt | 0 | 388 | 388 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE2-true-20261106T111334Z-b37312/manifest.json | exempt | 0 | 378 | 378 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE3-calibrated-20261106T110942Z-047afe/manifest.json | exempt | 0 | 390 | 390 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE3-true-20261106T111428Z-577217/manifest.json | exempt | 0 | 380 | 380 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE4-calibrated-20261106T111026Z-6e6d8e/manifest.json | exempt | 0 | 392 | 392 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE4-true-20261106T111620Z-c3be43/manifest.json | exempt | 0 | 382 | 382 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE5-calibrated-20261106T111115Z-fc7474/manifest.json | exempt | 0 | 392 | 392 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmE5-true-20261106T114528Z-59bfd8/manifest.json | exempt | 0 | 382 | 382 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecm_results.json | exempt | 0 | 2876 | 2876 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmbase-calibrated-20261106T110730Z-950f94/manifest.json | exempt | 0 | 377 | 377 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/ecmbase-true-20261106T111204Z-398cbb/manifest.json | exempt | 0 | 367 | 367 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/fixtures/llm_prior_ranking.json | exempt | 0 | 358 | 358 | fixture |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/test_audit_trail.py | under | 849 | 1197 | 348 |  |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | tests/test_manifest.py | under | 746 | 963 | 217 |  |
| plan-coder-27-1794606223 | plan-coder | 33 | bin/factory_failure_tickets.py | under | 0 | 3928 | 3928 |  |
| plan-coder-27-1794606223 | plan-coder | 33 | docs/OPERATIONS.md | under | 2992 | 3230 | 238 |  |
| plan-coder-27-1794606223 | plan-coder | 33 | prompts/backlog-supervisor.md | over-growing | 11553 | 11672 | 119 | flat |
| plan-coder-27-1794606223 | plan-coder | 33 | tests/test_factory_failure_tickets.py | under | 0 | 3953 | 3953 |  |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | pysr-symbolic-surrogate/.gitignore | under | 0 | 10 | 10 |  |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | pysr-symbolic-surrogate/MEASUREMENTS.md | under | 0 | 1180 | 1180 |  |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | pysr-symbolic-surrogate/README.md | under | 0 | 385 | 385 |  |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | pysr-symbolic-surrogate/REPORT.md | under | 0 | 1193 | 1193 |  |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | pysr-symbolic-surrogate/requirements.txt | under | 0 | 8 | 8 |  |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | pysr-symbolic-surrogate/src/curves.py | under | 0 | 2248 | 2248 |  |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | pysr-symbolic-surrogate/src/run_measurements.py | under | 0 | 2832 | 2832 |  |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | pysr-symbolic-surrogate/tests/test_curves.py | under | 0 | 576 | 576 |  |
| retrofit-copilot-4-1794609829 | retrofit-copilot | 14 | src/run_ecms.py | over-growing | 5760 | 6675 | 915 |  |
| retrofit-copilot-4-1794609829 | retrofit-copilot | 14 | tests/test_rank_by.py | under | 0 | 2153 | 2153 |  |

## Would-block rate per repo

| repo | prs | blocked | rate | blocked_strict | rate_strict | median_head_tokens |
|---|---|---|---|---|---|---|
| armored-archer | 7 | 2 | 28.6 | 1 | 14.3 | 48 |
| building-ai-pocs | 7 | 3 | 42.9 | 3 | 42.9 | 1179.5 |
| fluxion | 7 | 7 | 100.0 | 7 | 100.0 | 7773.0 |
| matchline | 2 | 2 | 100.0 | 2 | 100.0 | 2703.5 |
| plan-coder | 11 | 10 | 90.9 | 8 | 72.7 | 2814.5 |
| planar-nexus | 7 | 4 | 57.1 | 3 | 42.9 | 2628 |
| portkit-v2 | 12 | 10 | 83.3 | 8 | 66.7 | 4426 |
| retrofit-copilot | 3 | 2 | 66.7 | 2 | 66.7 | 390 |
| staging-studio | 4 | 1 | 25.0 | 0 | 0.0 | 1996.5 |

median head_tokens of touched files: 2623.5
touched files counted: 416

## PR measurement status

| run_id | repo | pr | status | reason |
|---|---|---|---|---|
| fluxion-4776-1794521700 | fluxion | 4788 | measured | base from reflog branch-creation point |
| fluxion-4774-1794522000 | fluxion | 4787 | measured | base from reflog branch-creation point |
| portkit-v2-298-1794529160 | portkit-v2 | 424 | measured | base from reflog branch-creation point |
| portkit-v2-302-1794530652 | portkit-v2 | 425 | measured | base from reflog branch-creation point |
| building-ai-pocs-32-1794532182 | building-ai-pocs | 35 | measured | base from reflog branch-creation point |
| armored-archer-1826-1794532182 | armored-archer | 1834 | measured | base from reflog branch-creation point |
| fluxion-4790-1794532182 | fluxion | 4796 | measured | base from reflog branch-creation point |
| staging-studio-1535-1794532182 | staging-studio | 1538 | measured | base from reflog branch-creation point |
| portkit-v2-274-1794532182 | portkit-v2 | 426 | measured | base from reflog branch-creation point |
| planar-nexus-2952-1794532182 | planar-nexus | 2953 | measured | base from reflog branch-creation point |
| fluxion-4791-1794532182 | fluxion | 4797 | measured | base from reflog branch-creation point |
| building-ai-pocs-33-1794538025 | building-ai-pocs | 36 | measured | base from reflog branch-creation point |
| portkit-v2-240-1794538029 | portkit-v2 | 427 | measured | base from reflog branch-creation point |
| plan-coder-3-1794538032 | plan-coder | 6 | measured | base from reflog branch-creation point |
| armored-archer-1827-1794538386 | armored-archer | 1839 | measured | base from reflog branch-creation point |
| staging-studio-1536-1794537980 | staging-studio | 1539 | measured | base from reflog branch-creation point |
| planar-nexus-2951-1794538014 | planar-nexus | 2954 | measured | base from reflog branch-creation point |
| matchline-1383-1794537997 | matchline | 1388 | measured | base from reflog branch-creation point |
| fluxion-4794-1794538320 | fluxion | 4799 | measured | base from reflog branch-creation point |
| staging-studio-1537-1794541411 | staging-studio | 1540 | measured | base from reflog branch-creation point |
| fluxion-4793-1794541298 | fluxion | 4802 | measured | base from reflog branch-creation point |
| portkit-v2-340-1794541461 | portkit-v2 | 428 | measured | base from reflog branch-creation point |
| armored-archer-1823-1794541441 | armored-archer | 1840 | measured | base from reflog branch-creation point |
| planar-nexus-2950-1794541431 | planar-nexus | 2955 | measured | base from reflog branch-creation point |
| plan-coder-1-1794541471 | plan-coder | 7 | measured | base from reflog branch-creation point |
| fluxion-4789-1794541401 | fluxion | 4803 | measured | base from reflog branch-creation point |
| armored-archer-1830-1794545630 | armored-archer | 1841 | measured | base from reflog branch-creation point |
| plan-coder-8-1794545650 | plan-coder | 9 | measured | base from reflog branch-creation point |
| planar-nexus-2949-1794545620 | planar-nexus | 2957 | measured | base from reflog branch-creation point |
| portkit-v2-302-1794545640 | portkit-v2 | 429 | measured | base from reflog branch-creation point |
| armored-archer-1825-1794550486 | armored-archer | 1842 | measured | base from reflog branch-creation point |
| matchline-1384-1794550393 | matchline | 1391 | measured | base from reflog branch-creation point |
| planar-nexus-2948-1794550549 | planar-nexus | 2959 | measured | base from reflog branch-creation point |
| armored-archer-1832-1794554800 | armored-archer | 1843 | measured | base from reflog branch-creation point |
| portkit-v2-299-1794554800 | portkit-v2 | 430 | measured | base from reflog branch-creation point |
| portkit-v2-335-1794557040 | portkit-v2 | 431 | measured | base from reflog branch-creation point |
| plan-coder-2-1794557301 | plan-coder | 11 | measured | base from reflog branch-creation point |
| plan-coder-5-1794559850 | plan-coder | 12 | measured | base from reflog branch-creation point |
| planar-nexus-2958-1794573255 | planar-nexus | 2961 | measured | base from reflog branch-creation point |
| armored-archer-1831-1794573188 | armored-archer | 1844 | measured | base from reflog branch-creation point |
| portkit-v2-296-1794573369 | portkit-v2 | 432 | measured | base from reflog branch-creation point |
| portkit-v2-302-1794577257 | portkit-v2 | 433 | measured | base from reflog branch-creation point |
| building-ai-pocs-37-1794581886 | building-ai-pocs | 40 | measured | base from reflog branch-creation point |
| building-ai-pocs-41-1794584730 | building-ai-pocs | 42 | measured | base from reflog branch-creation point |
| plan-coder-10-1794588962 | plan-coder | 14 | measured | base from reflog branch-creation point |
| portkit-v2-434-1794595360 | portkit-v2 | 435 | measured | base from reflog branch-creation point |
| plan-coder-15-1794595360 | plan-coder | 19 | measured | base from reflog branch-creation point |
| building-ai-pocs-43-1794595360 | building-ai-pocs | 44 | measured | base from reflog branch-creation point |
| plan-coder-17-1794598324 | plan-coder | 20 | measured | base from reflog branch-creation point |
| building-ai-pocs-45-1794600861 | building-ai-pocs | 49 | measured | base from reflog branch-creation point |
| plan-coder-25-1794600861 | plan-coder | 31 | measured | base from reflog branch-creation point |
| portkit-v2-436-1794600861 | portkit-v2 | 437 | measured | base from reflog branch-creation point |
| retrofit-copilot-3-1794603432 | retrofit-copilot | 8 | measured | base from reflog branch-creation point |
| plan-coder-16-1794603464 | plan-coder | 32 | measured | base from reflog branch-creation point |
| staging-studio-1543-1794603412 | staging-studio | 1544 | measured | base from reflog branch-creation point |
| planar-nexus-2963-1794604067 | planar-nexus | 2964 | measured | base from reflog branch-creation point |
| retrofit-copilot-2-1794606094 | retrofit-copilot | 9 | measured | base from reflog branch-creation point |
| plan-coder-27-1794606223 | plan-coder | 33 | measured | base from reflog branch-creation point |
| building-ai-pocs-47-1794606764 | building-ai-pocs | 52 | measured | base from reflog branch-creation point |
| retrofit-copilot-4-1794609829 | retrofit-copilot | 14 | measured | base from reflog branch-creation point |

class counts: {"crossing": 14, "exempt": 30, "new-over": 6, "over-growing": 111, "over-shrinking": 2, "under": 253}
