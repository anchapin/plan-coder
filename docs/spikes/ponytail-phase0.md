# Ponytail-ladder brief rule — Phase 0 retrospective measurement (issue #21)

Phase 0 only: **measurement, no change**. No prompt was edited, no ladder
paragraph was written, no treatment/control worker run was executed, no ledger
schema was touched. Phase 1 (the paired A/B runs of any brief-template
change) is gated on Alex's separate go and is deliberately **not** in this
change.

Produced by `bin/ponytail_phase0.py` (re-runnable end-to-end:
`python3 bin/ponytail_phase0.py` regenerates every table below verbatim;
`--format csv` gives the same tables as CSV). Everything below the
"Deliverable 1" heading is unedited script output; the prose comes after the
tables.

---

## Deliverable 1 — raw table (verbatim script output)

Sample: the most recent 60 `pr_opened` rows in the worker ledger (the ledger
holds 570 rows, 420 of them `pr_opened`; the tail of the ledger is
2026-11-14). All 60 rows were measured; no row was dropped.

### Provenance (script output)

- ledger: `/home/hatch/workspace/goals/token-plan-coding-manager-setup/hidden_files/worker_ledger.jsonl`
- rows selected: 60 most recent `pr_opened` rows
- rows measured: 60; rows with an unfetchable diff: 0
- GitHub API via `gh`: available (gh api reachable)
- p90 is nearest-rank; the median is the ordinary median; no means are reported

### Raw table

| run_id | repo | tier | tokens_in | tokens_out | steps | grade | files_changed | lines_added | lines_deleted | net_lines | diff_source |
|---|---|---|---|---|---|---|---|---|---|---|---|
| armored-archer-1832-1794554800 | armored-archer | cheap | 2382051 | 29117 | 60/60 | 55 | 3 | 270 | 19 | 251 | gh api pulls/files |
| portkit-v2-299-1794554800 | portkit-v2 | balanced | 2704818 | 30817 | 60/60 | 61 | 2 | 262 | 35 | 227 | gh api pulls/files |
| portkit-v2-335-1794557040 | portkit-v2 | balanced | 3313763 | 54417 | 60/60 | 56 | 5 | 443 | 4 | 439 | gh api pulls/files |
| plan-coder-2-1794557301 | plan-coder | balanced | 3942662 | 107041 | 60/60 | 75 | 11 | 27 | 27 | 0 | gh api pulls/files |
| plan-coder-5-1794559850 | plan-coder | balanced | 663515 | 15403 | 24/60 | 83 | 2 | 190 | 0 | 190 | gh api pulls/files |
| planar-nexus-2958-1794573255 | planar-nexus | balanced | 2429587 | 25993 | 60/60 | 69 | 5 | 300 | 84 | 216 | gh api pulls/files |
| armored-archer-1831-1794573188 | armored-archer | balanced | 3017706 | 52309 | 60/60 | 77 | 2 | 589 | 1 | 588 | gh api pulls/files |
| portkit-v2-296-1794573369 | portkit-v2 | flagship | 4071471 | 66365 | 60/60 | 51 | 4 | 392 | 3 | 389 | gh api pulls/files |
| portkit-v2-302-1794577257 | portkit-v2 | balanced | 3620643 | 50183 | 60/60 | 90 | 2 | 239 | 59 | 180 | gh api pulls/files |
| building-ai-pocs-37-1794581886 | building-ai-pocs | balanced | 4190414 | 94498 | 60/60 | 88 | 27 | 4645 | 0 | 4645 | gh api pulls/files |
| building-ai-pocs-41-1794584730 | building-ai-pocs | balanced | 2763067 | 44014 | 60/60 | 83 | 3 | 332 | 40 | 292 | gh api pulls/files |
| plan-coder-10-1794588962 | plan-coder | cheap | 1638551 | 25121 | 36/60 | 87 | 1 | 555 | 0 | 555 | gh api pulls/files |
| portkit-v2-434-1794595360 | portkit-v2 | balanced | 2247980 | 28843 | 60/60 | 55 | 3 | 445 | 1 | 444 | gh api pulls/files |
| plan-coder-15-1794595360 | plan-coder | balanced | 4300731 | 82603 | 60/60 | 80 | 4 | 941 | 1 | 940 | gh api pulls/files |
| building-ai-pocs-43-1794595360 | building-ai-pocs | balanced | 2967917 | 51718 | 60/60 | 70 | 10 | 1279 | 0 | 1279 | gh api pulls/files |
| plan-coder-17-1794598324 | plan-coder | balanced | 2811016 | 37392 | 60/60 | 60 | 5 | 45 | 3 | 42 | gh api pulls/files |
| building-ai-pocs-45-1794600861 | building-ai-pocs | cheap | 600023 | 15522 | 29/60 | 100 | 2 | 143 | 11 | 132 | gh api pulls/files |
| plan-coder-25-1794600861 | plan-coder | cheap | 1850070 | 22633 | 50/60 | 87 | 3 | 188 | 1 | 187 | gh api pulls/files |
| portkit-v2-436-1794600861 | portkit-v2 | balanced | 2216089 | 20775 | 60/60 | 83 | 3 | 76 | 23 | 53 | gh api pulls/files |
| retrofit-copilot-3-1794603432 | retrofit-copilot | balanced | 2526261 | 41000 | 54/60 | 90 | 4 | 739 | 86 | 653 | gh api pulls/files |
| plan-coder-16-1794603464 | plan-coder | cheap | 2262194 | 44208 | 51/60 | 77 | 2 | 19 | 3 | 16 | gh api pulls/files |
| staging-studio-1543-1794603412 | staging-studio | cheap | 1836858 | 24468 | 60/60 | 93 | 6 | 339 | 4 | 335 | gh api pulls/files |
| planar-nexus-2963-1794604067 | planar-nexus | balanced | 2489177 | 39574 | 60/60 | 93 | 9 | 342 | 15 | 327 | gh api pulls/files |
| retrofit-copilot-2-1794606094 | retrofit-copilot | balanced | 1460803 | 36826 | 38/60 | 97 | 17 | 1120 | 20 | 1100 | gh api pulls/files |
| plan-coder-27-1794606223 | plan-coder | balanced | 707071 | 18830 | 27/60 | 100 | 4 | 835 | 1 | 834 | gh api pulls/files |
| building-ai-pocs-47-1794606764 | building-ai-pocs | balanced | 2518253 | 40314 | 60/60 | 50 | 8 | 744 | 0 | 744 | gh api pulls/files |
| retrofit-copilot-4-1794609829 | retrofit-copilot | balanced | 1566422 | 25956 | 52/60 | 90 | 2 | 375 | 27 | 348 | gh api pulls/files |
| plan-coder-23-1794610079 | plan-coder | balanced | 2206088 | 35013 | 60/60 | 83 | 2 | 1467 | 0 | 1467 | gh api pulls/files |
| armored-archer-1851-1794611064 | armored-archer | balanced | 2605189 | 43544 | 59/60 | 71 | 2 | 235 | 3 | 232 | gh api pulls/files |
| planar-nexus-2966-1794611000 | planar-nexus | balanced | 457405 | 9890 | 27/60 | 70 | 3 | 290 | 27 | 263 | gh api pulls/files |
| staging-studio-1545-1794610888 | staging-studio | balanced | 1257892 | 18353 | 42/60 | 77 | 2 | 441 | 3 | 438 | gh api pulls/files |
| building-ai-pocs-53-1794612128 | building-ai-pocs | balanced | 1938639 | 33365 | 47/60 | 90 | 9 | 106386 | 31 | 106355 | gh api pulls/files |
| retrofit-copilot-11-1794613424 | retrofit-copilot | cheap | 152165 | 3592 | 13/60 | 92 | 4 | 157 | 3 | 154 | gh api pulls/files |
| armored-archer-1849-1794613424 | armored-archer | balanced | 2378392 | 36761 | 57/60 | 84 | 4 | 474 | 52 | 422 | gh api pulls/files |
| plan-coder-35-1794613424 | plan-coder | balanced | 1998864 | 37181 | 50/60 | 90 | 1 | 571 | 0 | 571 | gh api pulls/files |
| staging-studio-1547-1794613663 | staging-studio | cheap | 1112192 | 15615 | 47/60 | 87 | 8 | 8 | 1020 | -1012 | gh api pulls/files |
| planar-nexus-2967-1794616245 | planar-nexus | balanced | 434762 | 9113 | 26/60 | 70 | 3 | 362 | 224 | 138 | gh api pulls/files |
| matchline-1398-1794613424 | matchline | cheap | 4225166 | 160292 | 58/60 | 38 | 7 | 52 | 6 | 46 | gh api pulls/files |
| staging-studio-1546-1794618442 | staging-studio | balanced | 1119610 | 13245 | 48/60 | 90 | 2 | 124 | 2 | 122 | gh api pulls/files |
| armored-archer-1850-1794618457 | armored-archer | balanced | 2761964 | 47058 | 60/60 | 77 | 3 | 402 | 4 | 398 | gh api pulls/files |
| retrofit-copilot-12-1794618467 | retrofit-copilot | balanced | 2609842 | 67954 | 46/60 | 90 | 4 | 398 | 15 | 383 | gh api pulls/files |
| portkit-v2-439-1794618750 | portkit-v2 | flagship | 3967027 | 32954 | 60/60 | 93 | 4 | 333 | 21 | 312 | gh api pulls/files |
| plan-coder-34-1794618462 | plan-coder | balanced | 2092619 | 39703 | 60/60 | 59 | 2 | 491 | 0 | 491 | gh api pulls/files |
| matchline-1396-1794618447 | matchline | balanced | 3936187 | 159826 | 54/60 | 38 | 2 | 210 | 22 | 188 | gh api pulls/files |
| armored-archer-1848-1794620942 | armored-archer | balanced | 1782864 | 22942 | 49/60 | 94 | 3 | 324 | 15 | 309 | gh api pulls/files |
| retrofit-copilot-10-1794621260 | retrofit-copilot | balanced | 335397 | 7750 | 25/60 | 100 | 2 | 184 | 0 | 184 | gh api pulls/files |
| portkit-v2-438-1794621064 | portkit-v2 | balanced | 3446955 | 46919 | 60/60 | 61 | 4 | 344 | 26 | 318 | gh api pulls/files |
| plan-coder-38-1794621184 | plan-coder | balanced | 2903505 | 39325 | 60/60 | 83 | 3 | 1661 | 0 | 1661 | gh api pulls/files |
| retrofit-copilot-13-1794623737 | retrofit-copilot | cheap | 68746 | 2083 | 11/60 | 92 | 1 | 14 | 3 | 11 | gh api pulls/files |
| plan-coder-24-1794623737 | plan-coder | balanced | 1834024 | 45299 | 47/60 | 54 | 1 | 335 | 0 | 335 | gh api pulls/files |
| planar-nexus-2965-1794623737 | planar-nexus | balanced | 1914996 | 20032 | 56/60 | 63 | 7 | 523 | 4 | 519 | gh api pulls/files |
| building-ai-pocs-56-1794626301 | building-ai-pocs | balanced | 621507 | 24621 | 25/60 | 90 | 7 | 1166 | 0 | 1166 | gh api pulls/files |
| retrofit-copilot-5-1794626178 | retrofit-copilot | balanced | 2030084 | 56613 | 40/60 | 90 | 2 | 282 | 4 | 278 | gh api pulls/files |
| retrofit-copilot-6-1794627147 | retrofit-copilot | balanced | 1248383 | 31935 | 36/60 | 90 | 4 | 347 | 8 | 339 | gh api pulls/files |
| building-ai-pocs-57-1794627254 | building-ai-pocs | balanced | 2632759 | 45582 | 60/60 | 83 | 61 | 2486 | 0 | 2486 | gh api pulls/files |
| portkit-v2-442-1794630817 | portkit-v2 | balanced | 1732608 | 14417 | 46/60 | 77 | 5 | 262 | 1 | 261 | gh api pulls/files |
| retrofit-copilot-7-1794630817 | retrofit-copilot | balanced | 2385569 | 27046 | 60/60 | 80 | 4 | 349 | 17 | 332 | gh api pulls/files |
| plan-coder-42-1794630817 | plan-coder | balanced | 2162459 | 36901 | 60/60 | 63 | 6 | 1326 | 0 | 1326 | gh api pulls/files |
| building-ai-pocs-54-1794630817 | building-ai-pocs | balanced | 2390479 | 27626 | 60/60 | 69 | 5 | 13443 | 0 | 13443 | gh api pulls/files |
| plan-coder-28-1794634175 | plan-coder | balanced | 4763615 | 88489 | 60/60 | 58 | 2 | 1062 | 0 | 1062 | gh api pulls/files |

### Rows whose diff could not be fetched

None: every selected row's diff was fetched.

---

## Deliverable 2 — distributions (verbatim script output)

Distributions, not averages: the median and the p90 (nearest-rank) are
reported for `net_lines` and `tokens_out` per repo and per tier. No mean is
reported anywhere in this report.

### Per repo

| scope | key | rows | measured | unmeasured | net_n | net_median | net_p90 | tokens_n | tokens_median | tokens_p90 |
|---|---|---|---|---|---|---|---|---|---|---|
| repo | armored-archer | 6 | 6 | 0 | 6 | 353.5 | 588 | 6 | 40152.5 | 52309 |
| repo | building-ai-pocs | 9 | 9 | 0 | 9 | 1279 | 106355 | 9 | 40314 | 94498 |
| repo | matchline | 2 | 2 | 0 | 2 | 117.0 | 188 | 2 | 160059.0 | 160292 |
| repo | plan-coder | 15 | 15 | 0 | 15 | 555 | 1467 | 15 | 37392 | 88489 |
| repo | planar-nexus | 5 | 5 | 0 | 5 | 263 | 519 | 5 | 20032 | 39574 |
| repo | portkit-v2 | 9 | 9 | 0 | 9 | 312 | 444 | 9 | 32954 | 66365 |
| repo | retrofit-copilot | 10 | 10 | 0 | 10 | 335.5 | 653 | 10 | 29490.5 | 56613 |
| repo | staging-studio | 4 | 4 | 0 | 4 | 228.5 | 438 | 4 | 16984.0 | 24468 |

### Per tier

| scope | key | rows | measured | unmeasured | net_n | net_median | net_p90 | tokens_n | tokens_median | tokens_p90 |
|---|---|---|---|---|---|---|---|---|---|---|
| tier | balanced | 48 | 48 | 0 | 48 | 390.5 | 1661 | 48 | 37041.0 | 82603 |
| tier | cheap | 10 | 10 | 0 | 10 | 143.0 | 335 | 10 | 23550.5 | 44208 |
| tier | flagship | 2 | 2 | 0 | 2 | 350.5 | 389 | 2 | 49659.5 | 66365 |

### Whole sample

| scope | key | rows | measured | unmeasured | net_n | net_median | net_p90 | tokens_n | tokens_median | tokens_p90 |
|---|---|---|---|---|---|---|---|---|---|---|
| all | all rows | 60 | 60 | 0 | 60 | 335.0 | 1326 | 60 | 35887.0 | 67954 |

---

## Deliverable 3 — frozen paired-run case set (verbatim script output)

12 recently completed issues with known-good (landed) PRs, spanning 8 repos
and both the cheap and the balanced tier (plus one flagship case). These are
the frozen cases Phase 1 would replay as paired A/B runs; they are frozen
here so that Phase 1 cannot quietly pick easier cases after the fact.

Selection rule (implemented in `bin/ponytail_phase0.py`, deterministic for a
given ledger tail): from the measured rows of the sample, take the rows that
carry a grade and whose worker branch is already landed on the repo's default
branch (a known-good PR); anchor one case per required tier (cheap, balanced),
then add repo anchors until at least 3 repos are covered, then fill with
diversity-first (new repo or new tier) and finally least-used-repo-first, in
recency order.

| case | repo | issue | tier | pr | pr_url | grade | files_changed | net_lines | justification |
|---|---|---|---|---|---|---|---|---|---|
| C01 | retrofit-copilot | 13 | cheap | 18 | https://github.com/anchapin/retrofit-copilot/pull/18 | 92 | 1 | 11 | retrofit-copilot / cheap tier: issue #13 was completed by worker PR #18 (grade 92, 1 file(s) changed, net +11 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C02 | plan-coder | 42 | balanced | 43 | https://github.com/anchapin/plan-coder/pull/43 | 63 | 6 | 1326 | plan-coder / balanced tier: issue #42 was completed by worker PR #43 (grade 63, 6 file(s) changed, net +1326 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C03 | portkit-v2 | 442 | balanced | 443 | https://github.com/anchapin/portkit-v2/pull/443 | 77 | 5 | 261 | portkit-v2 / balanced tier: issue #442 was completed by worker PR #443 (grade 77, 5 file(s) changed, net +261 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C04 | building-ai-pocs | 57 | balanced | 59 | https://github.com/anchapin/building-ai-pocs/pull/59 | 83 | 61 | 2486 | building-ai-pocs / balanced tier: issue #57 was completed by worker PR #59 (grade 83, 61 file(s) changed, net +2486 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C05 | armored-archer | 1848 | balanced | 1856 | https://github.com/anchapin/armored-archer/pull/1856 | 94 | 3 | 309 | armored-archer / balanced tier: issue #1848 was completed by worker PR #1856 (grade 94, 3 file(s) changed, net +309 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C06 | portkit-v2 | 439 | flagship | 440 | https://github.com/anchapin/portkit-v2/pull/440 | 93 | 4 | 312 | portkit-v2 / flagship tier: issue #439 was completed by worker PR #440 (grade 93, 4 file(s) changed, net +312 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C07 | staging-studio | 1546 | balanced | 1550 | https://github.com/anchapin/staging-studio/pull/1550 | 90 | 2 | 122 | staging-studio / balanced tier: issue #1546 was completed by worker PR #1550 (grade 90, 2 file(s) changed, net +122 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C08 | matchline | 1398 | cheap | 1400 | https://github.com/anchapin/matchline/pull/1400 | 38 | 7 | 46 | matchline / cheap tier: issue #1398 was completed by worker PR #1400 (grade 38, 7 file(s) changed, net +46 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C09 | retrofit-copilot | 7 | balanced | 21 | https://github.com/anchapin/retrofit-copilot/pull/21 | 80 | 4 | 332 | retrofit-copilot / balanced tier: issue #7 was completed by worker PR #21 (grade 80, 4 file(s) changed, net +332 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C10 | building-ai-pocs | 56 | balanced | 58 | https://github.com/anchapin/building-ai-pocs/pull/58 | 90 | 7 | 1166 | building-ai-pocs / balanced tier: issue #56 was completed by worker PR #58 (grade 90, 7 file(s) changed, net +1166 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C11 | plan-coder | 24 | balanced | 41 | https://github.com/anchapin/plan-coder/pull/41 | 54 | 1 | 335 | plan-coder / balanced tier: issue #24 was completed by worker PR #41 (grade 54, 1 file(s) changed, net +335 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |
| C12 | armored-archer | 1850 | balanced | 1855 | https://github.com/anchapin/armored-archer/pull/1855 | 77 | 3 | 398 | armored-archer / balanced tier: issue #1850 was completed by worker PR #1855 (grade 77, 3 file(s) changed, net +398 lines, PR landed on the default branch) -- re-runnable because the issue and its known-good PR are both recorded in the ledger, so the same issue can be replayed from scratch on the same repo and tier |

---

## Method notes, caveats and what this does NOT show

Prose (the tables above are the evidence; this section only interprets it).

### How the diff sizes were fetched (and why the method column matters)

> **Supervisor regeneration note (2026-11-14, at collection).** The worker
> sandbox could not reach the GitHub API (TLS handshake failure, quoted
> below), so the worker's original tables were measured from the local
> clones via the git fallback. At collection the supervisor re-ran
> `bin/ponytail_phase0.py` unchanged in an environment where `gh api` is
> reachable; every table above (Deliverables 1–3) is that API run's verbatim
> output, and the distributions prose below was updated to match. 9 of the
> 64 table rows changed versus the clone-backed run (e.g. a branch whose
> reflog base overstated the PR's diff); the API numbers are the ones that
> match GitHub's own files-changed counts for each PR.
>
> **JiTTest catch + fix (2026-11-14, same collection).** The API regeneration
> exposed a seam bug the clone-backed run had masked: `fetch_diff_gh`'s
> stats carry no `head`, so `measure_rows` computed `landed=False` for every
> API-measured row and `select_cases`' known-good (landed) filter silently
> never operated — the first API-regenerated frozen set was justified as
> "PR opened", not "PR landed". A JiTTest check on this PR produced a
> surviving catch (seam test: real `fetch_diff_gh` → real `measure_rows`,
> only the `gh` boundary stubbed; fails on the pre-fix code, passes after).
> The supervisor folded the fix into this PR (`measure_rows` now resolves
> the branch head in the clone when the API stats carry none) together with
> the JiT test as a permanent regression test
> (`tests/test_ponytail_phase0_landed.py`, suite now 184 passed). The frozen
> set above is the post-fix run: all 12 cases are landed known-good PRs.

- The script prefers the GitHub API through the `gh` CLI
  (`gh api repos/anchapin/<repo>/pulls/<n>/files`). In this sandbox the API
  is **not reachable** — every call fails at the TLS handshake
  (`tls: first record does not look like a TLS handshake`), and the `gh`
  token in this environment is invalid as well. The script probes `gh` once
  and states the result in the Provenance block; on a machine with working
  API access the same script fetches the diff sizes over the API instead
  (per-row `diff_source` then reads `gh api pulls/files`), falling back to
  git per row if an individual API call fails.
- Because the API was unavailable in the worker sandbox, the worker's
  original run measured all 60 rows from the **local supervisor-loop clones** with `git diff --numstat <base>..<branch-tip>`,
  the same convention `bin/filesize_phase0.py` uses for issue #23. The clones
  are read only (`--clone-root`, default `~/workspace`); nothing in them was
  modified, fetched or written. The base
  commit is the branch-creation point recorded in the clone's reflog
  (`base: reflog branch-creation point` on every row); when a reflog entry is
  missing the script falls back to the merge-base with the default branch and
  then to the first parent of the PR merge commit. The per-row method is in
  the `diff_source` column so the reader can see which rows are API-backed
  and which are clone-backed.
- Consequence to keep in mind: a clone-backed number is the branch's diff
  against its fork point. If a worker pushed more commits to the branch
  after the PR was opened, or if the PR base moved, the number can differ
  from GitHub's own "files changed" for that PR. There was no row in this
  sample where the diff could not be fetched, so no row is missing a number
  and no number is estimated.
- `net_lines` = lines added − lines deleted, over all files in the diff
  (source, tests and docs together; binary files contribute 0 lines and are
  counted in `files_changed`). It is a diff-size measure, not a "useful
  code" measure.

### What the distributions say (descriptive only)

- Whole sample: median `net_lines` **335.0**, p90 **1326**; median
  `tokens_out` **35 887**, p90 **67 954**. 19 of the 60 rows produced more
  than 500 net lines, 11 more than 1000; 29 of 60 rows used the full 60/60
  step budget.
- Cheap tier (n=10): median `net_lines` 143.0, p90 335; median `tokens_out`
  23 550.5, p90 44 208. Balanced tier (n=48): median `net_lines` 390.5, p90
  1661; median `tokens_out` 37 041, p90 82 603. The balanced tier's diff
  tail is much heavier than the cheap tier's (only 1 of 10 cheap rows exceeds
  500 net lines, versus 18 of 48 balanced rows).
- `tokens_out` does **not** track diff size: the 6 rows with ≤50 net lines
  have a median `tokens_out` of 40 800, while the 19 rows with ≥500 net
  lines have a median of 37 181. Most of the output-token spend in this
  sample is process, not delivered diff — which is the observation the
  Ponytail-ladder hypothesis rests on, and it is an observation about the
  *existing* runs, not a measurement of the ladder.
- Per-repo percentiles on the small repos are coarse and should not be read
  as stable: matchline has n=2 rows (its p90 of 160 292 `tokens_out` is one
  run), plan-coder n=15, everything else n≤10. building-ai-pocs' p90
  `net_lines` of 106 355 is a single 106 355-line run
  (`building-ai-pocs-53`), not a typical outcome.

### Honest limitations

- **Phase 1 was not run.** No treatment/control worker run was executed, no
  brief template was changed, no ladder paragraph was written. This report
  contains **no evidence that a ladder brief rule helps**; it contains the
  baseline that such a rule would have to beat.
- The sample is the **most recent 60** `pr_opened` rows (ledger tail
  2026-11-14), not a random or stratified sample. It is dominated by the
  balanced tier (48/60), so per-tier numbers for `cheap` (10) and
  `flagship` (2) rest on small n.
- The ledger records no diff statistics of its own, so every diff number
  here is reconstructed from the GitHub API at collection (see the
  regeneration note above); the ledger itself was read
  read-only and was not modified.
- `grade` is the mechanical score from `bin/grade_run.py`; it is not a
  human judgement of the change.

### What Phase 1 would need (gated on Alex's separate go)

1. A written ladder brief rule (the template change itself — deliberately
   **not** written here).
2. Paired A/B runs over the frozen 12-case set above, same issue, same repo,
   same tier, control brief vs ladder brief.
3. The comparison metrics are already defined by this Phase 0 baseline:
   median and p90 of `net_lines` and of `tokens_out` per repo and per tier,
   plus the step-budget usage and the grade — read as distributions, not as
   averages.

## Reproducing

```
python3 bin/ponytail_phase0.py            # markdown tables, as embedded above
python3 bin/ponytail_phase0.py --format csv --out table.csv
python3 -m pytest tests/ -q
```

The script is read-only with respect to the ledger, caches nothing outside
the worktree, and is deterministic apart from the `gh` fetches it attempts
first.
