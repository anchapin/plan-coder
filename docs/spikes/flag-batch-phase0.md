# Flag-batch Phase 0 — measurement only (plan-coder issue #100)

**Status:** Phase 0 measurement. No harness behaviour change, no flagcheck enablement,
no Phase 1. This document is the only file changed in this task.

**Window (ledger extent at run time):** `2026-11-09T04:59:56Z` → `2026-11-23T10:20:33Z`
(ledger rows by ISO week: 2026-W46 = 1,966, 2026-W47 = 611, 2026-W48 = 22; the 22 W48
rows carry no `needs_alex` flag, so flagged PRs fall in W46 and W47 only).

**N used:** 2,599 ledger rows total; **1,141 `needs_alex` rows** (W46 = 966, W47 = 175);
**74 distinct flagged PRs**.

Raw tables first, verdict last.

### Data source and action vocabulary (census before any classification)

Read-only source: `~/workspace/goals/token-plan-coding-manager-setup/hidden_files/shepherd_ledger.jsonl`
(2,599 rows; opened read-only, never written). Action vocabulary, counted over the whole
ledger: `needs_alex` 1,141 · `merge_conflicts` 424 · `other` 340 ·
`merged_by_shepherd` 208 · `trigger_requested` 149 · `needs_operator` 131 ·
`merged` 110 · `fix_attempt` 41 · `rerun` 39 · `checked` 12 · `closed_unmerged` 3 ·
`snapshot` 1. The flagged set for this spike is the `needs_alex` rows (the same action
`bin/flagbatch.py` reads for a PR's flag count); `needs_operator` rows are treated as
non-flag ledger traffic for counting purposes, and the full row set for a PR (all
actions) is what feeds the class call, exactly as in the live view.

## 1. Per-PR flag table

Classifier: `bin/flagbatch.py::classify()` — the repo's only home for the flagcheck class
taxonomy (the brief names `bin/flagcheck.py`; **that file does not exist at HEAD** on this
branch, a contradiction already recorded in `docs/spikes/authority-drift-phase0.md:247`.
`bin/flagbatch.py` carries the same closed class names — `ci-none`, `ci-reds`,
`ci-unclassified`, `workflow-kind`, `security-scan`, `conflict-stale`, `physics-gate`,
`held-green`, plus `unverifiable` — and is imported, not copied, here). Per-PR class is
computed exactly as the live view computes it: `classify(all ledger rows for that PR)`.
"Deterministic" = the PR received a class from the closed 8-class taxonomy (i.e. not
`unverifiable`, i.e. the ledger carried a codable signal). `gh pr view` / `gh pr checks`
were **not** usable in this environment (`gh auth status` → invalid token; API call →
`tls: first record does not look like a TLS handshake`), so no PR needed external
enrichment — the ledger alone classified every PR.

| PR | repo | class | first flag (UTC) | last flag (UTC) | needs_alex rows | deterministic |
|---|---|---|---|---|---|---|
| #1349 | anchapin/matchline | workflow-kind | 2026-11-09T06:28:51Z | 2026-11-11T00:22:22Z | 39 | yes |
| #4710 | anchapin/fluxion | physics-gate | 2026-11-09T07:54:31Z | 2026-11-09T23:28:54Z | 6 | yes |
| #4709 | anchapin/fluxion | conflict-stale | 2026-11-09T08:27:06Z | 2026-11-10T06:22:27Z | 7 | yes |
| #4714 | anchapin/fluxion | physics-gate | 2026-11-09T09:58:44Z | 2026-11-10T00:52:04Z | 7 | yes |
| #4721 | anchapin/fluxion | conflict-stale | 2026-11-09T12:51:42Z | 2026-11-09T12:51:42Z | 1 | yes |
| #2881 | anchapin/planar-nexus | conflict-stale | 2026-11-09T13:58:14Z | 2026-11-09T23:28:53Z | 7 | yes |
| #2884 | anchapin/planar-nexus | held-green | 2026-11-09T13:58:15Z | 2026-11-11T00:22:22Z | 38 | yes |
| #2899 | anchapin/planar-nexus | security-scan | 2026-11-09T13:58:15Z | 2026-11-11T13:53:32Z | 59 | yes |
| #2901 | anchapin/planar-nexus | held-green | 2026-11-09T13:58:15Z | 2026-11-11T00:22:22Z | 38 | yes |
| #4723 | anchapin/fluxion | ci-reds | 2026-11-09T15:02:37Z | 2026-11-11T00:22:22Z | 38 | yes |
| #4725 | anchapin/fluxion | physics-gate | 2026-11-09T20:26:05Z | 2026-11-10T00:52:04Z | 5 | yes |
| #4647 | anchapin/fluxion | physics-gate | 2026-11-09T21:25:54Z | 2026-11-12T11:51:09Z | 42 | yes |
| #1316 | anchapin/matchline | workflow-kind | 2026-11-09T21:25:54Z | 2026-11-10T03:22:16Z | 7 | yes |
| #4651 | anchapin/fluxion | physics-gate | 2026-11-09T21:25:55Z | 2026-11-10T00:52:04Z | 4 | yes |
| #4652 | anchapin/fluxion | conflict-stale | 2026-11-09T21:25:55Z | 2026-11-10T00:52:04Z | 4 | yes |
| #1352 | anchapin/matchline | security-scan | 2026-11-10T03:54:02Z | 2026-11-11T13:53:32Z | 50 | yes |
| #1356 | anchapin/matchline | workflow-kind | 2026-11-10T13:22:03Z | 2026-11-11T00:22:23Z | 16 | yes |
| #1357 | anchapin/matchline | workflow-kind | 2026-11-11T14:22:10Z | 2026-11-11T20:55:22Z | 10 | yes |
| #1358 | anchapin/matchline | security-scan | 2026-11-11T15:22:16Z | 2026-11-11T20:55:23Z | 9 | yes |
| #1361 | anchapin/matchline | security-scan | 2026-11-11T20:25:53Z | 2026-11-11T20:55:24Z | 2 | yes |
| #1362 | anchapin/matchline | workflow-kind | 2026-11-11T21:25:03Z | 2026-11-12T03:54:13Z | 13 | yes |
| #1367 | anchapin/matchline | workflow-kind | 2026-11-12T03:54:13Z | 2026-11-12T03:54:13Z | 1 | yes |
| #1368 | anchapin/matchline | workflow-kind | 2026-11-12T04:23:29Z | 2026-11-12T11:51:10Z | 16 | yes |
| #1376 | anchapin/matchline | workflow-kind | 2026-11-12T13:53:09Z | 2026-11-12T17:29:51Z | 6 | yes |
| #1377 | anchapin/matchline | physics-gate | 2026-11-12T15:28:14Z | 2026-11-12T17:29:52Z | 3 | yes |
| #2942 | anchapin/planar-nexus | conflict-stale | 2026-11-12T15:28:15Z | 2026-11-17T23:55:53Z | 137 | yes |
| #1378 | anchapin/matchline | workflow-kind | 2026-11-12T15:53:54Z | 2026-11-12T15:53:54Z | 1 | yes |
| #4782 | anchapin/fluxion | physics-gate | 2026-11-12T16:54:30Z | 2026-11-12T17:29:53Z | 2 | yes |
| #1380 | anchapin/matchline | workflow-kind | 2026-11-12T18:06:34Z | 2026-11-12T18:51:30Z | 3 | yes |
| #1381 | anchapin/matchline | workflow-kind | 2026-11-12T19:23:05Z | 2026-11-12T19:52:50Z | 2 | yes |
| #4783 | anchapin/fluxion | ci-reds | 2026-11-12T20:23:37Z | 2026-11-12T21:22:35Z | 3 | yes |
| #4787 | anchapin/fluxion | physics-gate | 2026-11-13T00:21:18Z | 2026-11-13T01:24:39Z | 3 | yes |
| #4797 | anchapin/fluxion | conflict-stale | 2026-11-13T02:22:34Z | 2026-11-13T02:22:34Z | 1 | yes |
| #1387 | anchapin/matchline | security-scan | 2026-11-13T02:22:34Z | 2026-11-13T02:22:34Z | 1 | yes |
| #1388 | anchapin/matchline | security-scan | 2026-11-13T03:25:50Z | 2026-11-13T03:52:43Z | 2 | yes |
| #6 | anchapin/plan-coder | conflict-stale | 2026-11-13T03:25:50Z | 2026-11-13T17:54:15Z | 18 | yes |
| #4799 | anchapin/fluxion | physics-gate | 2026-11-13T03:52:43Z | 2026-11-13T03:52:43Z | 1 | yes |
| #1839 | anchapin/armored-archer | conflict-stale | 2026-11-13T04:25:27Z | 2026-11-17T23:55:54Z | 116 | yes |
| #7 | anchapin/plan-coder | ci-none | 2026-11-13T04:25:27Z | 2026-11-13T04:25:27Z | 1 | yes |
| #1389 | anchapin/matchline | security-scan | 2026-11-13T04:55:07Z | 2026-11-13T17:54:15Z | 17 | yes |
| #1541 | anchapin/staging-studio | workflow-kind | 2026-11-13T04:55:08Z | 2026-11-13T18:25:44Z | 17 | yes |
| #1390 | anchapin/matchline | security-scan | 2026-11-13T06:24:37Z | 2026-11-13T18:25:44Z | 16 | yes |
| #4804 | anchapin/fluxion | workflow-kind | 2026-11-13T06:24:38Z | 2026-11-13T19:28:53Z | 16 | yes |
| #2955 | anchapin/planar-nexus | ci-reds | 2026-11-13T06:24:39Z | 2026-11-17T23:55:54Z | 114 | yes |
| #1391 | anchapin/matchline | security-scan | 2026-11-13T06:53:42Z | 2026-11-13T18:25:44Z | 17 | yes |
| #11 | anchapin/plan-coder | ci-none | 2026-11-13T08:53:41Z | 2026-11-13T17:54:16Z | 13 | yes |
| #12 | anchapin/plan-coder | ci-none | 2026-11-13T09:24:18Z | 2026-11-13T17:54:16Z | 12 | yes |
| #39 | anchapin/building-ai-pocs | workflow-kind | 2026-11-13T14:25:10Z | 2026-11-13T18:25:44Z | 5 | yes |
| #13 | anchapin/plan-coder | workflow-kind | 2026-11-13T14:25:10Z | 2026-11-13T17:54:16Z | 4 | yes |
| #14 | anchapin/plan-coder | ci-none | 2026-11-13T17:23:09Z | 2026-11-13T17:54:16Z | 2 | yes |
| #1394 | anchapin/matchline | security-scan | 2026-11-13T19:54:26Z | 2026-11-13T19:54:26Z | 1 | yes |
| #20 | anchapin/plan-coder | ci-none | 2026-11-13T19:54:26Z | 2026-11-13T19:54:26Z | 1 | yes |
| #1395 | anchapin/matchline | security-scan | 2026-11-13T20:25:14Z | 2026-11-13T20:25:14Z | 1 | yes |
| #32 | anchapin/plan-coder | held-green | 2026-11-13T21:25:03Z | 2026-11-13T21:25:03Z | 1 | yes |
| #33 | anchapin/plan-coder | ci-reds | 2026-11-13T21:56:20Z | 2026-11-13T21:56:20Z | 1 | yes |
| #1399 | anchapin/matchline | security-scan | 2026-11-13T23:44:58Z | 2026-11-13T23:55:43Z | 2 | yes |
| #1854 | anchapin/armored-archer | ci-reds | 2026-11-14T00:38:58Z | 2026-11-14T00:38:58Z | 1 | yes |
| #1400 | anchapin/matchline | security-scan | 2026-11-14T01:27:41Z | 2026-11-14T01:55:07Z | 2 | yes |
| #40 | anchapin/plan-coder | ci-reds | 2026-11-14T02:54:30Z | 2026-11-14T03:54:00Z | 2 | yes |
| #22 | anchapin/retrofit-copilot | workflow-kind | 2026-11-14T06:01:39Z | 2026-11-17T23:55:54Z | 84 | yes |
| #46 | anchapin/plan-coder | ci-reds | 2026-11-14T07:01:53Z | 2026-11-14T07:01:53Z | 1 | yes |
| #49 | anchapin/plan-coder | ci-reds | 2026-11-14T09:27:35Z | 2026-11-14T13:53:37Z | 8 | yes |
| #51 | anchapin/plan-coder | workflow-kind | 2026-11-14T17:56:12Z | 2026-11-14T17:56:12Z | 1 | yes |
| #53 | anchapin/plan-coder | workflow-kind | 2026-11-14T22:53:13Z | 2026-11-17T14:24:08Z | 56 | yes |
| #62 | anchapin/plan-coder | ci-reds | 2026-11-15T02:54:19Z | 2026-11-15T02:54:19Z | 1 | yes |
| #63 | anchapin/plan-coder | ci-reds | 2026-11-15T03:22:08Z | 2026-11-15T03:22:08Z | 1 | yes |
| #64 | anchapin/plan-coder | ci-reds | 2026-11-15T04:23:24Z | 2026-11-15T04:23:24Z | 1 | yes |
| #58 | anchapin/plan-coder | ci-none | 2026-11-16T19:23:15Z | 2026-11-17T23:55:55Z | 14 | yes |
| #68 | anchapin/plan-coder | ci-reds | 2026-11-17T09:24:27Z | 2026-11-17T13:26:36Z | 3 | yes |
| #1862 | anchapin/armored-archer | security-scan | 2026-11-17T13:26:37Z | 2026-11-17T13:26:37Z | 1 | yes |
| #75 | anchapin/plan-coder | workflow-kind | 2026-11-17T16:52:35Z | 2026-11-17T16:52:35Z | 1 | yes |
| #76 | anchapin/plan-coder | ci-reds | 2026-11-17T17:24:56Z | 2026-11-17T17:24:56Z | 1 | yes |
| #77 | anchapin/plan-coder | ci-none | 2026-11-17T19:23:16Z | 2026-11-17T23:55:56Z | 3 | yes |
| #4 | anchapin/passive-income-pocs | ci-none | 2026-11-18T02:24:33Z | 2026-11-18T02:24:33Z | 1 | yes |

Class distribution over the 74 flagged PRs (PR-level class):

| class | PRs | note |
|---|---|---|
| workflow-kind | 19 | batchable |
| security-scan | 14 | **individual review — never batched** |
| ci-reds | 13 | batchable |
| physics-gate | 9 | **individual review — never batched** |
| conflict-stale | 8 | batchable |
| ci-none | 8 | batchable |
| held-green | 3 | batchable |
| ci-unclassified | 0 | — |
| unverifiable / unknown bucket | **0** | no PR landed in the unknown bucket |

Non-deterministic / unknown bucket count: **0 of 74 PRs (0.0%)**. Every PR received a
class from the closed 8-class taxonomy deterministically from ledger rows alone.

Note (contradiction, not a guess): 33 of the 74 PRs carry *more than one* class when
each `needs_alex` row is classified on its own (e.g. `anchapin/matchline#1352` has rows
that classify as `ci-none`, `ci-unclassified`, `security-scan`, `workflow-kind`). The
per-PR class in the table above is the PR-level class (all rows for that PR), which is
the same call the live view makes; the row-level spread is reported here rather than
hidden.

## 2. Weekly batch simulation (grouped by class)

Week = ISO week (Mon-start, UTC) of the PR's **first** `needs_alex` flag; a PR is
batched once, under its own class. Batchable classes are the taxonomy classes minus the
individual-review set that `bin/flagbatch.py` keeps out of every batch
(`INDIVIDUAL = security-scan, physics-gate, unverifiable`). **Security-scan and
physics-gate are reported individually below and are never folded into an aggregate
batch count** (they are also excluded from the batch totals, exactly as the live view
excludes them).

### 2a. Batchable classes

| week | class | batch size | members (repo#pr) |
|---|---|---|---|
| 2026-W46 | workflow-kind | 18 | building-ai-pocs#39, fluxion#4804, matchline#1316, matchline#1349, matchline#1356, matchline#1357, matchline#1362, matchline#1367, matchline#1368, matchline#1376, matchline#1378, matchline#1380, matchline#1381, plan-coder#13, plan-coder#51, plan-coder#53, retrofit-copilot#22, staging-studio#1541 |
| 2026-W46 | ci-reds | 11 | armored-archer#1854, fluxion#4723, fluxion#4783, plan-coder#33, plan-coder#40, plan-coder#46, plan-coder#49, plan-coder#62, plan-coder#63, plan-coder#64, planar-nexus#2955 |
| 2026-W46 | conflict-stale | 8 | armored-archer#1839, fluxion#4652, fluxion#4709, fluxion#4721, fluxion#4797, plan-coder#6, planar-nexus#2881, planar-nexus#2942 |
| 2026-W46 | ci-none | 5 | plan-coder#7, plan-coder#11, plan-coder#12, plan-coder#14, plan-coder#20 |
| 2026-W46 | held-green | 3 | plan-coder#32, planar-nexus#2884, planar-nexus#2901 |
| 2026-W47 | ci-none | 3 | passive-income-pocs#4, plan-coder#58, plan-coder#77 |
| 2026-W47 | ci-reds | 2 | plan-coder#68, plan-coder#76 |
| 2026-W47 | workflow-kind | 1 | plan-coder#75 |

Batch totals: **8 batches** covering **51 batchable PRs**; 23 PRs are individual-review
(§2b), 0 PRs are unknown. No week contains a class split across more than one batch
(each (week, class) pair is one batch).

### 2b. Individual-review classes (never batched, listed one flag at a time)

| week | class | flags (count) | members (repo#pr) |
|---|---|---|---|
| 2026-W46 | security-scan | 13 | matchline#1352, matchline#1358, matchline#1361, matchline#1387, matchline#1388, matchline#1389, matchline#1390, matchline#1391, matchline#1394, matchline#1395, matchline#1399, matchline#1400, planar-nexus#2899 |
| 2026-W46 | physics-gate | 9 | fluxion#4647, fluxion#4651, fluxion#4710, fluxion#4714, fluxion#4725, fluxion#4782, fluxion#4787, fluxion#4799, matchline#1377 |
| 2026-W47 | security-scan | 1 | armored-archer#1862 |

### 2c. Batch-shape summary

| measure | value | arithmetic |
|---|---|---|
| flagged PRs (N) | 74 | 51 batchable + 23 individual-review + 0 unknown |
| batchable batches | 8 | §2a |
| size-1 batches (no transfer benefit) | **1** | `2026-W47 / workflow-kind / plan-coder#75` |
| batch sizes (sorted) | 1, 2, 3, 3, 5, 8, 11, 18 | |
| median batch size | **4.0** | (3 + 5) / 2 |
| PRs in a same-class batch of ≥3 in their week | **48** | 18 + 11 + 8 + 5 + 3 (W46) + 3 (W47 ci-none) |
| — as a share of all 74 flagged PRs | **64.86 %** | 48 / 74 |
| — as a share of the 51 batchable PRs | 94.12 % | 48 / 51 |
| PRs sharing a class with ≥2 others in the same week (batchable classes) | **48** | identical set to the ≥3-batch set; the 23 individual-review PRs also share a class with ≥2 others (13 security-scan, 9 physics-gate in W46) but are never batched, by policy |

Sensitivity (not used in the verdict): if the individual-review classes were *also*
allowed to batch, `2026-W46 / security-scan` (13) and `2026-W46 / physics-gate` (9)
would each be a batch of ≥3 and the all-PR share would rise to 70/74 = 94.6 %. The
headline numbers above keep those classes out of every batch, per the hard stop.

## 3. Repeat-noise share

A `needs_alex` row is a **re-flag** when, against the immediately preceding
`needs_alex` row *for the same PR* (timestamp order):

* the row-level class is the same (`classify([row])` equal on both rows), and
* the PR's state signature is unchanged — `(head_sha, ci, merge_state)` identical.
  The ledger's `head_sha` field appears on 200 rows of the ledger but on **none** of the
  1,141 `needs_alex` rows, so for flag rows the effective signature is the
  `(ci, merge_state)` pair: a changed CI text or merge state always breaks the
  "unchanged" claim.

| measure | count | arithmetic |
|---|---|---|
| `needs_alex` rows in window (denominator) | **1,141** | all `action == "needs_alex"` rows |
| first row per PR (no predecessor → not a re-flag) | 74 | one per distinct flagged PR |
| rows with a predecessor | 1,067 | 1,141 − 74 |
| … of which state changed since previous row | 667 | `head_sha` / `ci` / `merge_state` differ |
| … of which class changed since previous row | 264 | row-level `classify()` differs |
| … of which state unchanged | 400 | 1,067 − 667 |
| **re-flags (same class AND unchanged state)** | **387** | 400 − 13 (state unchanged but class flipped) |
| **repeat-noise share** | **33.92 %** | 387 / 1,141 |

Row-level class spread over all 1,141 `needs_alex` rows: ci-reds 305, ci-none 241,
workflow-kind 204, ci-unclassified 153, security-scan 106, physics-gate 61,
held-green 51, conflict-stale 20 (sum = 1,141).

## 4. Review-cost proxy — context switches

A "context switch" = the repository changes between two consecutive flags in the
review order. Two views are compared.

| view | entries in the stream | repo switches | PR-identity switches |
|---|---|---|---|
| **Current per-PR stream** (all `needs_alex` rows, ledger timestamp order) | 1,141 | **804** | 1,123 |
| Current stream, deduplicated to one first-flag per PR (like-for-like size) | 74 | **38** | 73 |
| **Batched-by-class view** (8 class batches + individual-review blocks, PRs inside a batch sorted by repo) | 74 | **23** | 73 |
| — of which inside a batch | 51 | 13 | 50 |
| — of which between consecutive batches | — | 5 | — |
| — of which inside the individual-review blocks (never batched) | 23 | 4 | 22 |
| — of which at the batchable → individual-block boundary | — | 1 | — |

Reading: in the current stream the reviewer changes repository on 70.5 % of consecutive
flag rows (804 / 1,140 transitions); in the batched-by-class view the same review
touches a repository switch on 31.5 % of consecutive entries (23 / 73) — 804 → 23 is a
97.1 % reduction in repo switches, and at equal entry count (74 vs 74) 38 → 23 is a
39.5 % reduction. Individual-review classes contribute 4 of the 23 remaining switches
because their flags are deliberately not folded into a batch.

## 5. Verdict (pre-registered Proceed/Kill bars)

**PROCEED** — both Proceed bars are met and **no Kill bar fired**.
(Measurement only: this issue does not authorize Phase 1, and nothing here enables
flagcheck or changes harness behaviour.)

### Proceed bars (both required)

| bar | required | actual | arithmetic | met? |
|---|---|---|---|---|
| flagged PRs in a same-class batch of ≥3 in their week | ≥ 40 % | **64.86 %** | 48 / 74 | ✅ |
| repeat-noise share of `needs_alex` rows | ≥ 25 % | **33.92 %** | 387 / 1,141 | ✅ |

### Kill bars (any one kills)

| bar | triggers when | actual | arithmetic | fired? |
|---|---|---|---|---|
| median batch size is 1 | median = 1 | median = **4.0** | (3 + 5) / 2 over sizes 1,2,3,3,5,8,11,18 | ❌ not fired |
| fewer than 20 distinct flagged PRs in the window | < 20 | **74** distinct flagged PRs | 51 batchable + 23 individual | ❌ not fired |
| classes non-deterministic from available data for > 30 % of PRs | > 30 % | **0.0 %** | 0 unknown-bucket PRs / 74 | ❌ not fired |

### Caveats recorded with the verdict

* The window is the ledger extent at run time (2026-11-09 → 2026-11-23, ~14 days), so
  the weekly simulation has only two populated ISO weeks (W46, W47); a longer window
  would add batches and could move the median batch size.
* `gh pr view` / `gh pr checks` were unavailable in this environment (invalid `gh`
  token; API TLS interception), so every classification came from ledger rows alone.
  No PR needed enrichment, but a PR whose ledger rows carry no codable signal would
  have had to land in the unknown bucket.
* `bin/flagcheck.py` (named by the brief as the taxonomy home) does not exist on this
  branch; the taxonomy was reused from `bin/flagbatch.py` by import, not copied.

### Commands run (measurement, read-only)

```
# ledger read + action vocabulary census (read-only)
python3 - <<'EOF'  # json.load over the ledger, Counter over d["action"]
EOF
# classification + batch simulation + repeat-noise + context switches (scratch script, /tmp only)
python3 /tmp/flagbatch_phase0.py
# gh availability probe (failed: invalid token / TLS interception)
gh auth status ; gh pr view 1316 --repo anchapin/matchline --json number
```

Verification of this change:

```
python3 -m pytest tests/ -q
git status --porcelain
git diff --stat
```
