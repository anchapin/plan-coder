<!-- Supervisor note at collection (2026-11-13 cycle): the worker reached its 60-step cap after landing the script and its tests; this document was generated at collection by running `python3 bin/trajectory_coupling_phase0.py` against the live worker + shepherd ledgers, exactly as the script's markdown format emits it. Re-running the script regenerates it. -->

# Phase 0 trajectory-coupling measurement (issue #38)

Coupling class of same-repo, same-cohort worker run pairs, joined to run outcomes and shepherd PR history.  Measurement only.

## Parameters

| parameter | value |
|---|---|
| worker_ledger | /home/hatch/workspace/goals/token-plan-coding-manager-setup/hidden_files/worker_ledger.jsonl |
| shepherd_ledger | /home/hatch/workspace/goals/token-plan-coding-manager-setup/hidden_files/shepherd_ledger.jsonl |
| window_hours | 2.0 |
| cycle_gap_minutes | 30 |
| ratchet_min_prs | 3 |
| gh_enabled | True |

## Feasibility: is the 2h cohort window the supervisor cycle?

- dispatch-time cycles reconstructed from the epoch embedded in run_id: **177** cycles over 513 rows (gap threshold 30 min).
- cohorts checked against cycle evidence: **164**; misassigned: **131** (79.9% (131/164)).
- same check restricted to cohorts whose members' ledger ts is within one window of their dispatch time (late-backfilled rows excluded): 127 of 159 misassigned (79.9% (127/159)).
- dispatch-time span inside a cohort: median 80.9 min, max 33243.1 min.
- ledger ts minus dispatch time: median 0.4 h, max 552.6 h (the ledger ts is the run's END time, not its dispatch time).

Sensitivity of the misassignment rate to the dispatch-batch gap:

| batch gap (min) | cohorts checked | misassigned | rate |
|---|---|---|---|
| 5 | 164 | 142 | 86.6% (142/164) |
| 10 | 164 | 140 | 85.4% (140/164) |
| 15 | 164 | 140 | 85.4% (140/164) |
| 30 | 164 | 131 | 79.9% (131/164) |
| 60 | 164 | 83 | 50.6% (83/164) |
| 120 | 164 | 18 | 11.0% (18/164) |

- **FEASIBILITY FINDING: cycle reconstruction is unsound** (79.9% of checked cohorts span more than one dispatch cycle, above the issue's 20% bar).  Phase 1 would have to become a schema proposal (cycle_id + dispatch-time scope paths) rather than a routing rule.

Misassigned cohort examples:

| cohort | runs | ts |
|---|---|---|
| armored-archer@2026-09-29T21:20:37Z | armored-archer-1425-1790714891, armored-archer-1422-1790718177 | 2026-09-29T21:20:37.946117+00:00, 2026-09-29T23:08:57.342391+00:00 |
| armored-archer@2026-10-23T00:54:55Z | armored-archer-1322-1790728040, armored-archer-1726-1792722387 | 2026-10-23T00:54:55.055031+00:00, 2026-10-23T02:49:35.671580+00:00 |
| armored-archer@2026-10-26T20:35:37Z | armored-archer-1750-1793043807, armored-archer-1750-1793047377, armored-archer-1748-1793053117 | 2026-10-26T20:35:37.724148+00:00, 2026-10-26T21:10:36.317647+00:00, 2026-10-26T22:34:42.806797+00:00 |
| armored-archer@2026-11-03T21:01:04Z | armored-archer-1768-1793738834, armored-archer-1772-1793739314, armored-archer-1771-1793745057 | 2026-11-03T21:01:04.584488Z, 2026-11-03T21:14:33.781536Z, 2026-11-03T22:50:04.742913Z |
| armored-archer@2026-11-03T23:28:20Z | armored-archer-1765-1793745058, armored-archer-1770-1793749423, armored-archer-1767-1793749423, armored-archer-1766-1793749423, armored-archer-1769-1793752140 | 2026-11-03T23:28:20.182769Z, 2026-11-03T23:51:55.297351Z, 2026-11-03T23:56:52.364786Z, 2026-11-03T23:59:59.761487Z, 2026-11-04T00:39:54.944866Z |
| armored-archer@2026-11-11T18:48:36Z | armored-archer-1794-1794422636, armored-archer-1795-1794427226 | 2026-11-11T18:48:36.610867+00:00, 2026-11-11T20:10:09.826174+00:00 |
| armored-archer@2026-11-12T02:50:41Z | armored-archer-1804-1794452050, armored-archer-1805-1794454490, armored-archer-1803-1794458393 | 2026-11-12T02:50:41Z, 2026-11-12T03:59:58Z, 2026-11-12T04:43:03Z |
| armored-archer@2026-11-12T12:40:00Z | armored-archer-1814-1794486466, armored-archer-1815-1794491048 | 2026-11-12T12:40:00Z, 2026-11-12T13:59:30Z |
| armored-archer@2026-11-13T01:16:58Z | armored-archer-1826-1794532182, armored-archer-1827-1794538386 | 2026-11-13T01:16:58Z, 2026-11-13T03:03:00Z |
| armored-archer@2026-11-13T04:04:03Z | armored-archer-1823-1794541441, armored-archer-1830-1794545630 | 2026-11-13T04:04:03Z, 2026-11-13T05:01:28Z |
| armored-archer@2026-11-13T06:19:57Z | armored-archer-1825-1794550486, armored-archer-1832-1794554800 | 2026-11-13T06:19:57Z, 2026-11-13T07:34:00Z |
| armored-archer@2026-11-13T23:22:00Z | armored-archer-1851-1794611064, armored-archer-1849-1794613424, armored-archer-1850-1794618457 | 2026-11-13T23:22:00Z, 2026-11-14T00:10:21Z, 2026-11-14T01:17:00Z |
| building-ai-pocs@2026-11-06T04:59:59Z | building-ai-pocs-1-1793940163, building-ai-pocs-1-1793943286, building-ai-pocs-1-1793946505 | 2026-11-06T04:59:59Z, 2026-11-06T06:05:00Z, 2026-11-06T06:33:52Z |
| building-ai-pocs@2026-11-06T09:27:14Z | building-ai-pocs-2-1793955285, building-ai-pocs-3-1793950431, building-ai-pocs-3-1793960484 | 2026-11-06T09:27:14.430742Z, 2026-11-06T09:31:00Z, 2026-11-06T10:49:20Z |
| building-ai-pocs@2026-11-06T12:41:55Z | building-ai-pocs-2-slice2-1793962689, building-ai-pocs-5-1793969664 | 2026-11-06T12:41:55.402118Z, 2026-11-06T13:36:08.884356Z |

## Headline table: coupling class x outcome

| coupling class | pairs (n) | runs (n) | PR-opened rate | median tokens_out | shepherd fix attempts / PR | conflict rate | thin? |
|---|---|---|---|---|---|---|---|
| disjoint | 13 | 18 | 100.0% (18/18) | 25895.0 (n=18) | 0.4 (n=18 PRs, 7 attempts) | 22.2% (4/18) |  |
| same-module | 2 | 4 | 100.0% (4/4) | 88557.0 (n=4) | 0.5 (n=4 PRs, 2 attempts) | 25.0% (1/4) | thin (n<10) |
| file-overlap | 0 | 0 | n/a (0) | n/a (n=0) | n/a (n=0 PRs, 0 attempts) | n/a (0) | thin (n<10) |
| shared-ratchet | 8 | 16 | 93.8% (15/16) | 31531.5 (n=16) | 0.5 (n=14 PRs, 7 attempts) | 21.4% (3/14) | thin (n<10) |
| unclassifiable | 406 | 442 | 71.9% (318/442) | 26961 (n=439) | 0.1 (n=126 PRs, 15 attempts) | 15.1% (19/126) |  |

Evidence tier of the PR file lists behind these classes: A-diff=2, B-ledger-mention=80, none=344 (`A-diff` = file list fetched from the PR diff via gh; `B-ledger-mention` = file paths the shepherd/worker ledgers name for that PR; `none` = no file evidence).  A tier-B `disjoint` means "no file that either ledger mentioned for either PR", which over-counts disjoint pairs; tier-B `shared-ratchet` is sound in the other direction (a named file is certainly touched).


## Grades present per coupling class

| coupling class | runs with a grade | grade counts |
|---|---|---|
| disjoint | 17 | 100=1, 38=1, 56=1, 61=2, 75=1, 80=1, 83=4, 88=1, 90=2, 91=1, 95=2 |
| same-module | 4 | 38=1, 70=1, 80=1, 83=1 |
| file-overlap | 0 | - |
| shared-ratchet | 13 | 38=1, 44=1, 50=1, 55=2, 58=1, 60=1, 75=1, 83=2, 87=1, 90=1, 95=1 |
| unclassifiable | 176 | 100=4, 31=3, 33=1, 34=2, 37=1, 38=2, 41=1, 43=1, 44=1, 45=1, 46=1, 48=3, 49=4, 50=1, 51=3, 52=4, 55=6, 56=3, 57=4, 58=2, 59=5, 60=2, 61=8, 63=2, 65=4, 67=3, 70=9, 71=1, 73=2, 74=1, 75=2, 77=8, 78=2, 80=3, 81=3, 83=14, 84=6, 86=1, 87=7, 88=4, 89=2, 90=16, 91=3, 92=3, 93=9, 94=2, 95=5, 97=1 |

## Pair counts per repo and class

| repo | disjoint | same-module | file-overlap | shared-ratchet | unclassifiable |
|---|---|---|---|---|---|
| armored-archer | 0 | 0 | 0 | 0 | 29 |
| building-ai-pocs | 0 | 0 | 0 | 1 | 15 |
| fluxion | 11 | 1 | 0 | 5 | 192 |
| matchline | 1 | 1 | 0 | 2 | 41 |
| plan-coder | 0 | 0 | 0 | 0 | 10 |
| planar-nexus | 0 | 0 | 0 | 0 | 48 |
| portkit-v2 | 0 | 0 | 0 | 0 | 16 |
| retrofit-copilot | 1 | 0 | 0 | 0 | 5 |
| staging-studio | 0 | 0 | 0 | 0 | 50 |

## Attribution completeness

| quantity | count | of | note |
|---|---|---|---|
| worker rows read | 556 | - | malformed lines skipped: 0 |
| rows not cohort-assignable | 8 | 556 | 1.4% (8/556) |
| PRs with a gh diff fetched | 2 | 405 | 0.5% (2/405) |
| pairs classifiable | 23 | 429 | 5.4% (23/429) |
| pairs where BOTH runs opened a PR | 225 | 429 | the ceiling on classifiable pairs once diffs are fetchable |
| PRs with shepherd history | 158 | 405 | 39.0% (158/405) |
| gh fetch outcomes | error=403, no-pr-url=149, ok=2 | | |

Unassignable-row reasons: no-repo=2, no-ts=6.

Unclassifiable-pair reasons: no-file-evidence=202, no-pr=204 (`no-pr` = one of the two runs never opened a PR; `no-file-evidence` = both runs opened a PR but no file list could be obtained for at least one of them).

## Shared-ratchet files per repo (from the data)

| repo | ratchet files | PRs touching the file |
|---|---|---|
| building-ai-pocs | federated-calibration/REPORT.md | 3 |
| fluxion | AGENTS.md | 3 |
| fluxion | CONTRACT.md | 5 |
| fluxion | docs/doc-inventory.md | 6 |
| fluxion | scripts/check_test_inventory_drift.py | 12 |
| fluxion | tests/test_inventory.json | 13 |
| matchline | .github/workflows/ci.yml | 24 |
| matchline | CONTRACT.md | 12 |
| staging-studio | AGENTS.md | 3 |

## Cohort size distribution

| cohort size | cohorts |
|---|---|
| 1 | 99 |
| 2 | 105 |
| 3 | 45 |
| 4 | 14 |
| 5 | 6 |
| 6 | 3 |

## Verdict vs the issue's bars

**KILL (measured).** No coupling class with pair-n >= 15 shows a >=15 pt lower PR-opened rate, a >=1.5x fix-attempt ratio, or a >=25 pt higher conflict rate against disjoint.

## Classified pairs (raw rows)

Every pair that could be classified, verbatim.  The unclassifiable pairs (one run never opened a PR, or no file list was obtainable for one of the two PRs) are counted in the attribution table and in the per-repo class table above; re-running the script regenerates the complete pair list.

| repo | cohort | run A | run B | PR A | PR B | tier A | tier B | class | detail |
|---|---|---|---|---|---|---|---|---|---|
| building-ai-pocs | building-ai-pocs@2026-11-13T22:31:50Z | building-ai-pocs-47-1794606764 | building-ai-pocs-53-1794612128 | 52 | 55 | B-ledger-mention | B-ledger-mention | shared-ratchet | federated-calibration/REPORT.md |
| fluxion | fluxion@2026-11-09T07:30:55Z | fluxion-4688-1794206213 | fluxion-4690-1794213387 | 4711 | 4713 | B-ledger-mention | B-ledger-mention | shared-ratchet | docs/doc-inventory.md |
| fluxion | fluxion@2026-11-09T12:21:45Z | fluxion-4692-1794220754 | fluxion-4691-1794225608c | 4715 | 4721 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-09T12:21:45Z | fluxion-4692-1794220754 | fluxion-4697-1794228891 | 4715 | 4723 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-09T12:21:45Z | fluxion-4691-1794225608c | fluxion-4697-1794228891 | 4721 | 4723 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-09T20:13:12Z | fluxion-4693-1794250118 | fluxion-4694-1794257238 | 4724 | 4726 | B-ledger-mention | B-ledger-mention | shared-ratchet | docs/doc-inventory.md |
| fluxion | fluxion@2026-11-09T20:13:12Z | fluxion-4693-1794250118 | fluxion-4688-1794257119 | 4724 | 4727 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-09T20:13:12Z | fluxion-4694-1794257238 | fluxion-4688-1794257119 | 4726 | 4727 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-11T18:52:36Z | fluxion-4745-1794422615 | fluxion-4746-1794422612 | 4747 | 4748 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-12T03:15:25Z | fluxion-4752-1794451422 | fluxion-4754-1794451600 | 4760 | 4761 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-12T03:15:25Z | fluxion-4752-1794451422 | fluxion-4758-1794454490 | 4760 | 4764 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-12T03:15:25Z | fluxion-4754-1794451600 | fluxion-4758-1794454490 | 4761 | 4764 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-12T05:52:46Z | fluxion-4757-1794457913 | fluxion-4755-1794463848 | 4766 | 4768 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-12T05:52:46Z | fluxion-4757-1794457913 | fluxion-4756-1794468367 | 4766 | 4770 | B-ledger-mention | B-ledger-mention | disjoint |  |
| fluxion | fluxion@2026-11-12T05:52:46Z | fluxion-4755-1794463848 | fluxion-4756-1794468367 | 4768 | 4770 | B-ledger-mention | B-ledger-mention | shared-ratchet | tests/test_inventory.json |
| fluxion | fluxion@2026-11-12T16:05:00Z | fluxion-4777b-1794497704 | fluxion-4774-1794496708 | 4781 | 4782 | B-ledger-mention | B-ledger-mention | shared-ratchet | scripts/check_test_inventory_drift.py,tests/test_inventory.json |
| fluxion | fluxion@2026-11-12T19:20:52Z | fluxion-4670-1794511872 | fluxion-4775c-1794516171 | 4783 | 4785 | B-ledger-mention | B-ledger-mention | same-module | scripts |
| fluxion | fluxion@2026-11-13T01:31:03Z | fluxion-4791-1794532182 | fluxion-4794-1794538320 | 4797 | 4799 | B-ledger-mention | B-ledger-mention | shared-ratchet | scripts/check_test_inventory_drift.py,tests/test_inventory.json |
| matchline | matchline@2026-09-29T05:28:21Z | matchline-512-1790657548 | shepherd-523-testcount-fix | 523 | 523 | B-ledger-mention | B-ledger-mention | shared-ratchet | .github/workflows/ci.yml |
| matchline | matchline@2026-11-12T03:19:38Z | matchline-1364-1794452800 | matchline-1363-1794454490 | 1367 | 1368 | B-ledger-mention | B-ledger-mention | shared-ratchet | .github/workflows/ci.yml |
| matchline | matchline@2026-11-12T03:19:38Z | matchline-1364-1794452800 | matchline-1366-1794458153 | 1367 | 1370 | B-ledger-mention | B-ledger-mention | same-module | tests |
| matchline | matchline@2026-11-12T03:19:38Z | matchline-1363-1794454490 | matchline-1366-1794458153 | 1368 | 1370 | B-ledger-mention | B-ledger-mention | disjoint |  |
| retrofit-copilot | retrofit-copilot@2026-11-13T21:12:00Z | retrofit-copilot-3-1794603432 | retrofit-copilot-4-1794609829 | 8 | 14 | B-ledger-mention | B-ledger-mention | disjoint |  |

