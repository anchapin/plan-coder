# Assurance receipts — Phase 0 report (plan-coder #30)

Phase 0 (measurement only) for the assurance-receipt spike: retrospective reconstruction of the
assurance block over the receipt spike window **2026-11-10 to 2026-11-24**, from the sealed
receipts joined to the shepherd, worker, goldfish, and JiTTest ledgers. No code was built;
Phase 1 requires a Phase 0 Proceed plus a separate operator tap.

Assembly note: the reconstruction (per-PR rows, level assignment, gate variants) was produced
by two worker runs (MiniMax-M2.7, balanced) whose scripts and `rows.json` are preserved in the
run transcripts; both runs hit their step caps before writing this report, so the supervisor
assembled the report from those finished artifacts. Every headline number below was re-derived
by the supervisor directly from `rows.json` at assembly time (commands in §8).

## 1. Population

Population of record: supervisor-origin terminal PRs with a sealed receipt in the window, PLUS
worker-opened terminal PRs with no sealed receipt (the reconstruction adds those and labels
them `unverified`; they are exactly the rows with an empty `receipt_id`).

- n = **283** rows: 269 with a sealed receipt, 14 receipt-less.
- Dispositions: merged 258, merged_unstable 20, merged_by_shepherd 2, closed_unmerged 3.
- Reconciliation against the receipt verdict (279 terminal PRs, 265 sealed, 95.0%): the
  populations differ by exactly 4 rows on both sides (283 vs 279; 269 vs 265). One candidate
  explanation visible in the data is the 4 sealed rows whose `merge_state` is `not_available`
  (3) or `UNKNOWN` (1); the verdict artifact itself is not among the inputs, so this cannot be
  settled from the reconstruction inputs and is stated as a candidate, not a finding.
- Seal coverage over this population: 269/283 = 95.1%. Over the verdict population: 95.0%.

## 2. Raw per-PR table

| repo | PR | disposition | level | skipped checks | downgrade visible |
|---|---|---|---|---|---|
| armored-archer | 1797 | merged | full | — | — |
| armored-archer | 1798 | merged | full | — | — |
| armored-archer | 1800 | merged | full | — | — |
| armored-archer | 1807 | merged | full | — | — |
| armored-archer | 1808 | merged | full | — | — |
| armored-archer | 1810 | merged | full | — | — |
| armored-archer | 1818 | merged | full | — | — |
| armored-archer | 1819 | merged | full | — | — |
| armored-archer | 1834 | merged | full | — | — |
| armored-archer | 1839 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| armored-archer | 1840 | merged | full | — | — |
| armored-archer | 1841 | merged | full | — | — |
| armored-archer | 1842 | merged | full | — | — |
| armored-archer | 1843 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| armored-archer | 1844 | merged | full | — | — |
| armored-archer | 1853 | merged | full | — | — |
| armored-archer | 1854 | merged | full | — | — |
| armored-archer | 1855 | merged | full | — | — |
| armored-archer | 1856 | merged | full | — | — |
| armored-archer | 1862 | merged | full | — | — |
| armored-archer | 1863 | merged | full | — | — |
| armored-archer | 1880 | merged | full | — | — |
| armored-archer | 1881 | merged | full | — | — |
| armored-archer | 1882 | merged | full | — | — |
| armored-archer | 1883 | merged | full | — | — |
| armored-archer | 1884 | merged | full | — | — |
| armored-archer | 1885 | merged | full | — | — |
| building-ai-pocs | 22 | merged | full | — | — |
| building-ai-pocs | 25 | merged | full | — | — |
| building-ai-pocs | 29 | merged | full | — | — |
| building-ai-pocs | 30 | merged | full | — | — |
| building-ai-pocs | 31 | merged | full | — | — |
| building-ai-pocs | 35 | merged | full | — | — |
| building-ai-pocs | 36 | merged | full | — | — |
| building-ai-pocs | 40 | merged | full | — | — |
| building-ai-pocs | 42 | merged | full | — | — |
| building-ai-pocs | 44 | merged | full | — | — |
| building-ai-pocs | 49 | merged | full | — | — |
| building-ai-pocs | 52 | merged | full | — | — |
| building-ai-pocs | 55 | merged | full | — | — |
| building-ai-pocs | 58 | merged | full | — | — |
| building-ai-pocs | 59 | merged | full | — | — |
| building-ai-pocs | 60 | merged | reduced | jittest (outcome unavailable) | not-visible |
| building-ai-pocs | 61 | merged | full | — | — |
| building-ai-pocs | 62 | merged | full | — | — |
| building-ai-pocs | 63 | merged | full | — | — |
| building-ai-pocs | 66 | merged | full | — | — |
| fluxion | 4647 | merged_unstable | reduced | grade (not_available) | not-visible |
| fluxion | 4651 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| fluxion | 4652 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| fluxion | 4709 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| fluxion | 4710 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| fluxion | 4714 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| fluxion | 4723 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| fluxion | 4725 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| fluxion | 4736 | merged_by_shepherd | unverified | all required checks (no sealed receipt) | not-visible |
| fluxion | 4747 | merged | full | — | — |
| fluxion | 4748 | merged | full | — | — |
| fluxion | 4760 | merged | full | — | — |
| fluxion | 4761 | merged | full | — | — |
| fluxion | 4762 | merged | full | — | — |
| fluxion | 4764 | merged | full | — | — |
| fluxion | 4766 | closed_unmerged | full | — | — |
| fluxion | 4768 | merged | full | — | — |
| fluxion | 4769 | merged | full | — | — |
| fluxion | 4770 | merged | full | — | — |
| fluxion | 4771 | merged_unstable | full | — | — |
| fluxion | 4778 | merged_unstable | full | — | — |
| fluxion | 4779 | merged_unstable | full | — | — |
| fluxion | 4781 | merged_unstable | full | — | — |
| fluxion | 4782 | merged_unstable | full | — | — |
| fluxion | 4783 | merged_unstable | full | — | — |
| fluxion | 4785 | merged_unstable | reduced | goldfish (outcome unavailable); jittest (outcome unavailable) | not-visible |
| fluxion | 4787 | merged_unstable | full | — | — |
| fluxion | 4788 | merged_unstable | full | — | — |
| fluxion | 4796 | merged_unstable | full | — | — |
| fluxion | 4797 | merged_unstable | full | — | — |
| fluxion | 4799 | merged_unstable | reduced | goldfish (outcome unavailable) | not-visible |
| fluxion | 4802 | merged_unstable | full | — | — |
| fluxion | 4803 | merged_unstable | reduced | goldfish (outcome unavailable) | not-visible |
| home-energy-twin | 18 | merged | full | — | — |
| home-energy-twin | 19 | merged | full | — | — |
| home-energy-twin | 20 | merged | full | — | — |
| home-energy-twin | 21 | merged | full | — | — |
| home-energy-twin | 22 | merged | full | — | — |
| home-energy-twin | 23 | merged | full | — | — |
| home-energy-twin | 24 | merged | full | — | — |
| home-energy-twin | 25 | merged | full | — | — |
| home-energy-twin | 26 | merged | full | — | — |
| home-energy-twin | 27 | merged | full | — | — |
| matchline | 1316 | closed_unmerged | reduced | grade (not_available) | not-visible |
| matchline | 1349 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| matchline | 1352 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| matchline | 1356 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| matchline | 1358 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| matchline | 1367 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| matchline | 1368 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| matchline | 1370 | merged | full | — | — |
| matchline | 1376 | merged | full | — | — |
| matchline | 1378 | closed_unmerged | full | — | — |
| matchline | 1379 | merged | full | — | — |
| matchline | 1381 | merged | full | — | — |
| matchline | 1388 | merged | full | — | — |
| matchline | 1391 | merged_unstable | full | — | — |
| matchline | 1400 | merged_unstable | full | — | — |
| matchline | 1401 | merged | full | — | — |
| matchline | 1403 | merged_unstable | full | — | — |
| matchline | 1405 | merged_unstable | reduced | goldfish (outcome unavailable) | not-visible |
| matchline | 1410 | merged_unstable | full | — | — |
| passive-income-pocs | 4 | merged | full | — | — |
| passive-income-pocs | 5 | merged | full | — | — |
| passive-income-pocs | 6 | merged | full | — | — |
| plan-coder | 6 | merged | full | — | — |
| plan-coder | 7 | merged | full | — | — |
| plan-coder | 9 | merged | full | — | — |
| plan-coder | 11 | merged | full | — | — |
| plan-coder | 12 | merged | full | — | — |
| plan-coder | 14 | merged | full | — | — |
| plan-coder | 20 | merged | full | — | — |
| plan-coder | 31 | merged | full | — | — |
| plan-coder | 32 | merged | full | — | — |
| plan-coder | 33 | merged | full | — | — |
| plan-coder | 36 | merged | full | — | — |
| plan-coder | 37 | merged | full | — | — |
| plan-coder | 39 | merged | full | — | — |
| plan-coder | 40 | merged | full | — | — |
| plan-coder | 41 | merged | full | — | — |
| plan-coder | 43 | merged | full | — | — |
| plan-coder | 44 | merged | full | — | — |
| plan-coder | 46 | merged | full | — | — |
| plan-coder | 47 | merged | full | — | — |
| plan-coder | 48 | merged | full | — | — |
| plan-coder | 49 | merged | full | — | — |
| plan-coder | 50 | merged | full | — | — |
| plan-coder | 51 | merged | full | — | — |
| plan-coder | 53 | merged | reduced | goldfish (outcome skipped_escalated,unavailable) | not-visible |
| plan-coder | 57 | merged | full | — | — |
| plan-coder | 61 | merged | full | — | — |
| plan-coder | 62 | merged | full | — | — |
| plan-coder | 63 | merged | full | — | — |
| plan-coder | 64 | merged | full | — | — |
| plan-coder | 68 | merged | full | — | — |
| plan-coder | 75 | merged | full | — | — |
| plan-coder | 76 | merged | full | — | — |
| plan-coder | 77 | merged | full | — | — |
| plan-coder | 92 | merged | full | — | — |
| plan-coder | 111 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| plan-coder | 122 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| plan-coder | 126 | merged | full | — | — |
| plan-coder | 128 | merged | full | — | — |
| plan-coder | 133 | merged | full | — | — |
| plan-coder | 135 | merged | full | — | — |
| plan-coder | 137 | merged | full | — | — |
| plan-coder | 143 | merged | full | — | — |
| plan-coder | 144 | merged | reduced | goldfish (outcome skipped_escalated,unavailable) | not-visible |
| plan-coder | 145 | merged | full | — | — |
| plan-coder | 149 | merged | full | — | — |
| plan-coder | 150 | merged | full | — | — |
| plan-coder | 151 | merged | full | — | — |
| plan-coder | 155 | merged | full | — | — |
| plan-coder | 157 | merged | reduced | jittest (outcome unavailable) | not-visible |
| plan-coder | 160 | merged | full | — | — |
| plan-coder | 161 | merged | full | — | — |
| planar-nexus | 2881 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| planar-nexus | 2884 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| planar-nexus | 2901 | merged | unverified | all required checks (no sealed receipt) | not-visible |
| planar-nexus | 2913 | merged_by_shepherd | unverified | all required checks (no sealed receipt) | not-visible |
| planar-nexus | 2916 | merged | full | — | — |
| planar-nexus | 2917 | merged | full | — | — |
| planar-nexus | 2922 | merged | full | — | — |
| planar-nexus | 2923 | merged | full | — | — |
| planar-nexus | 2931 | merged | full | — | — |
| planar-nexus | 2932 | merged | full | — | — |
| planar-nexus | 2934 | merged | full | — | — |
| planar-nexus | 2935 | merged | full | — | — |
| planar-nexus | 2941 | merged | full | — | — |
| planar-nexus | 2942 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2943 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2944 | merged | full | — | — |
| planar-nexus | 2945 | merged | full | — | — |
| planar-nexus | 2947 | merged | reduced | grade (not_available); goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2953 | merged | full | — | — |
| planar-nexus | 2954 | merged | full | — | — |
| planar-nexus | 2955 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2957 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2959 | merged | full | — | — |
| planar-nexus | 2961 | merged | full | — | — |
| planar-nexus | 2964 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2968 | merged | full | — | — |
| planar-nexus | 2969 | merged | full | — | — |
| planar-nexus | 2971 | merged | full | — | — |
| planar-nexus | 2979 | merged | full | — | — |
| planar-nexus | 2980 | merged | full | — | — |
| planar-nexus | 2981 | merged | full | — | — |
| planar-nexus | 2983 | merged | full | — | — |
| planar-nexus | 2985 | merged | full | — | — |
| planar-nexus | 2986 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2988 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2992 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| planar-nexus | 2995 | merged | full | — | — |
| portkit-v2 | 423 | merged | full | — | — |
| portkit-v2 | 424 | merged | full | — | — |
| portkit-v2 | 425 | merged | full | — | — |
| portkit-v2 | 426 | merged | full | — | — |
| portkit-v2 | 427 | merged | full | — | — |
| portkit-v2 | 428 | merged | full | — | — |
| portkit-v2 | 429 | merged | full | — | — |
| portkit-v2 | 430 | merged | full | — | — |
| portkit-v2 | 431 | merged | full | — | — |
| portkit-v2 | 432 | merged | full | — | — |
| portkit-v2 | 433 | merged | full | — | — |
| portkit-v2 | 435 | merged | full | — | — |
| portkit-v2 | 437 | merged | full | — | — |
| portkit-v2 | 440 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 441 | merged | full | — | — |
| portkit-v2 | 443 | merged | full | — | — |
| portkit-v2 | 444 | merged | reduced | goldfish (outcome skipped_escalated) | not-visible |
| portkit-v2 | 445 | merged | reduced | goldfish (outcome skipped_escalated) | not-visible |
| portkit-v2 | 446 | merged | reduced | goldfish (outcome skipped_escalated) | not-visible |
| portkit-v2 | 447 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 448 | merged | full | — | — |
| portkit-v2 | 449 | merged | reduced | goldfish (outcome skipped_escalated,unavailable) | not-visible |
| portkit-v2 | 450 | merged | reduced | goldfish (outcome skipped_escalated,unavailable) | not-visible |
| portkit-v2 | 451 | merged | full | — | — |
| portkit-v2 | 452 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 453 | merged | full | — | — |
| portkit-v2 | 454 | merged | full | — | — |
| portkit-v2 | 455 | merged | full | — | — |
| portkit-v2 | 456 | merged | full | — | — |
| portkit-v2 | 457 | merged | full | — | — |
| portkit-v2 | 458 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 459 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 460 | merged | full | — | — |
| portkit-v2 | 461 | merged | full | — | — |
| portkit-v2 | 462 | merged | full | — | — |
| portkit-v2 | 466 | merged | full | — | — |
| portkit-v2 | 467 | merged | full | — | — |
| portkit-v2 | 468 | merged | full | — | — |
| portkit-v2 | 469 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 470 | merged | full | — | — |
| portkit-v2 | 471 | merged | full | — | — |
| portkit-v2 | 472 | merged | full | — | — |
| portkit-v2 | 473 | merged | full | — | — |
| portkit-v2 | 474 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 475 | merged | full | — | — |
| portkit-v2 | 476 | merged | full | — | — |
| portkit-v2 | 477 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 478 | merged | full | — | — |
| portkit-v2 | 479 | merged | full | — | — |
| portkit-v2 | 480 | merged | reduced | goldfish (outcome unavailable) | not-visible |
| portkit-v2 | 481 | merged | full | — | — |
| portkit-v2 | 482 | merged | full | — | — |
| portkit-v2 | 483 | merged | full | — | — |
| portkit-v2 | 484 | merged | full | — | — |
| portkit-v2 | 485 | merged | full | — | — |
| retrofit-copilot | 8 | merged | full | — | — |
| retrofit-copilot | 9 | merged | full | — | — |
| retrofit-copilot | 14 | merged | full | — | — |
| retrofit-copilot | 15 | merged | full | — | — |
| retrofit-copilot | 16 | merged | full | — | — |
| retrofit-copilot | 17 | merged | full | — | — |
| retrofit-copilot | 18 | merged | full | — | — |
| retrofit-copilot | 19 | merged | full | — | — |
| retrofit-copilot | 20 | merged | full | — | — |
| retrofit-copilot | 21 | merged | full | — | — |
| staging-studio | 1515 | merged | full | — | — |
| staging-studio | 1516 | merged | full | — | — |
| staging-studio | 1517 | merged | full | — | — |
| staging-studio | 1522 | merged | full | — | — |
| staging-studio | 1523 | merged | full | — | — |
| staging-studio | 1524 | merged | full | — | — |
| staging-studio | 1525 | merged | full | — | — |
| staging-studio | 1531 | merged | full | — | — |
| staging-studio | 1532 | merged | full | — | — |
| staging-studio | 1533 | merged | full | — | — |
| staging-studio | 1534 | merged | full | — | — |
| staging-studio | 1538 | merged | reduced | jittest (outcome unavailable) | not-visible |
| staging-studio | 1539 | merged | full | — | — |
| staging-studio | 1540 | merged | full | — | — |
| staging-studio | 1544 | merged | full | — | — |
| staging-studio | 1548 | merged | full | — | — |
| staging-studio | 1549 | merged | reduced | jittest (outcome unavailable) | not-visible |
| staging-studio | 1550 | merged | full | — | — |

## 3. Level distribution

| level | n | share |
|---|---|---|
| full | 226 | 79.9% |
| reduced | 43 | 15.2% |
| unverified | 14 | 4.9% |
| reduced + unverified | 57 | 20.1% |

Cross-check: an independent second implementation of the level rule (`variants.level_of`, run
via `gates.py`) assigns the same level to every one of the 283 PRs — 0 per-PR disagreements.

## 4. Which checks account for the reduced cases

Of the 43 reduced rows, the rows whose `skipped` list names each check (a row can name more
than one):

| check | rows | detail |
|---|---|---|
| goldfish | 37 | outcome unavailable 30; skipped_escalated+unavailable 4; skipped_escalated 3 |
| jittest | 5 | outcome unavailable 5 |
| grade | 3 | grade not_available 3 |

Goldfish unavailability is the dominant downgrade cause by an order of magnitude.

## 5. Visibility of the downgrades

Two rules, reported side by side:

- **Strict** (the `downgrade_visible` field in the reconstruction): a downgrade counts as
  visible only when a `needs_alex`/`needs_operator` shepherd row for that PR names the missing
  or failed check itself. Result: **0 of 57** downgraded PRs visible; 57 not-visible.
- **Loose** (`gates.py` keyword counter): keyword matching over the PR's flag text counts
  **11 of 57** as visible, 46 not-visible. This rule is weaker: for receipt-less rows the
  missing set is the token `all`, which matches almost any flag text, and the two
  implementations compute visibility over different missing-check token sets — divergence
  between the counts is an algorithmic difference, not a data error.

Merged while the downgrade was not visible: **56** on the strict rows measure (disposition in
{merged, merged_unstable, merged_by_shepherd}, level not full, downgrade not-visible; the one
remaining downgraded PR was closed_unmerged) and **45** on the `gates.py` shepherd-ledger
measure (different merge population and the loose visibility rule). Both are reported; the
gate in §7 needs only "at least one", which holds under every variant.

Method limit: visibility was judged from shepherd-ledger flag rows only. Cycle reports and
flightdeck lines were not among the inputs, so visibility through those two channels is
not-determinable from this reconstruction — not negative evidence.

## 6. Sensitivity (gate variants, from `gates.py`)

| variant | rule change | full | reduced+unverified | merged, downgrade not visible |
|---|---|---|---|---|
| primary | as defined in the issue | 226 (79.9%) | 57 (20.1%) | 45 |
| C | JiTTest coverage_incomplete counts as not-ran | 204 (72.1%) | 79 (27.9%) | 67 |
| D | goldfish skipped_escalated counts as ran | 233 (82.3%) | 50 (17.7%) | 38 |
| E1 | manager review requires an exact capture match | 224 (79.2%) | 59 (20.8%) | 47 |
| E2 | shepherd merge counts as self-review (reviewer not distinct) | 60 (21.2%) | 223 (78.8%) | 211 |
| F | a failed check also downgrades | 214 (75.6%) | 69 (24.4%) | 49 |

## 7. Verdict

Pre-registered gates from the issue:

- Kill: ≥95% full, or every downgrade already visible. **Not met** — full is 79.9%, and under
  neither visibility rule was every downgrade visible (strict 0/57 visible; loose 11/57).
- Park: seal coverage below the receipt spike's ≥90% bar. **Not met** — coverage is 95.0% on
  the verdict population (the population the Park test is applied to) and 95.1% on this
  reconstruction's population.
- Proceed: ≥5% of terminal PRs reconstruct as reduced/unverified AND at least one such PR
  merged without the downgrade appearing in any line the operator saw. **Met** — 20.1%
  downgraded, and 56 (strict) / 45 (loose) merged with the downgrade not visible.

**Verdict: PROCEED** (Phase 1 still requires its own separate operator tap, per the issue).

## 8. Reproduction

Inputs: snapshots of `receipts_ledger.jsonl`, `shepherd_ledger.jsonl`, `worker_ledger.jsonl`,
`goldfish_ledger.jsonl`, `jittest_ledger.jsonl`, `claimlink_ledger.jsonl`, and the sealed
receipts, window 2026-11-10 to 2026-11-24. Reconstruction artifacts: `recon.py` (builds the
per-PR rows), `variants.py` + `gates.py` (independent level implementation and gate variants).

- `python3 local/phase0/gates.py` → primary: n=283, full=226 (79.9%), reduced=43,
  unverified=14, visibility {not-visible: 46, visible: 11}, merged-while-not-visible 45;
  variants C/D/E1/E2/F as tabled in §6.
- Supervisor tally over the rows at assembly: dispositions {merged: 258, merged_unstable: 20,
  merged_by_shepherd: 2, closed_unmerged: 3}; levels {full: 226, reduced: 43, unverified: 14};
  receipt-less rows 14; sealed rows 269; strict merged-while-not-visible 56.
