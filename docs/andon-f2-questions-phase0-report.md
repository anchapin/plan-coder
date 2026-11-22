# F2 questions census -- shepherd flags / Alex-facing questions (Phase 0)

Issue #147, Part F2, Phase 0. **Measurement only.** Nothing was built,
no prompt was changed, no ledger format was changed, no harness
behaviour was changed. This document reports what the shepherd ledger
and the acted-on log actually say about how many questions were raised
in the last 4 weeks, how many were acked or acted on, and how many were
repeats of an already-open question.

Reproduce every table below with:

```
python3 bin/questions_census_phase0.py \
    --ledger phase0_inputs/shepherd_ledger.jsonl \
    --acted-on phase0_inputs/acted-on.jsonl
```

(the script prints exactly the tables in this document; `--json` prints
the same numbers as a machine payload).

**Verdict: PROCEED F2.** The kill criterion is not met: repeats are not
rare. 93.8% of all flag rows repeat a question that was already open
for that PR, and the PR-level numbers show 1251 flag rows collapsing
onto only 78 distinct PRs. The chat channel is losing something.
See "Verdict" at the end for the reasoning tied to the measured
numbers.

---

## Raw tables

### Table 1 -- ledger action vocabulary (in window)

| action | rows | share % | census role |
|---|---|---|---|
| needs_alex | 1141 | 44.6 | flag |
| merge_conflicts | 424 | 16.6 | other |
| other | 340 | 13.3 | other |
| merged_by_shepherd | 202 | 7.9 | terminal |
| trigger_requested | 139 | 5.4 | other |
| merged | 110 | 4.3 | terminal |
| needs_operator | 110 | 4.3 | flag |
| rerun | 39 | 1.5 | other |
| fix_attempt | 38 | 1.5 | other |
| checked | 12 | 0.5 | other |
| closed_unmerged | 3 | 0.1 | terminal |
| snapshot | 1 | 0.0 | other |

### Table 2 -- flags raised (in window)

| flag action | flag rows | share % of flags |
|---|---|---|
| needs_alex | 1141 | 91.2 |
| needs_operator | 110 | 8.8 |

Total flag rows: **1251**  |  distinct repo+PR flagged: **78**  |  distinct (repo, PR, action, outcome) question texts: **674**.

### Table 3 -- repeats of an already-open question

| flag action | flag rows | repeats | repeat rate % |
|---|---|---|---|
| needs_alex | 1141 | 1067 | 93.5 |
| needs_operator | 110 | 106 | 96.4 |

Repeats overall: **1173** of **1251** flag rows (**93.8%**).  Of those repeats, 432 carry the SAME outcome text as the still-open question and 741 carry a DIFFERENT outcome text (a new reason on a PR whose question was still open).

Per-PR repeat leaders (top 15):

| repo | PR | flag rows | repeats | first flag | last flag |
|---|---|---|---|---|---|
| anchapin/planar-nexus | 2942 | 143 | 142 | 2026-11-12T15:28:15+00:00 | 2026-11-19T05:24:42+00:00 |
| anchapin/armored-archer | 1839 | 139 | 138 | 2026-11-13T04:25:27+00:00 | 2026-11-20T01:31:46+00:00 |
| anchapin/planar-nexus | 2955 | 114 | 113 | 2026-11-13T06:24:39+00:00 | 2026-11-17T23:55:54+00:00 |
| anchapin/retrofit-copilot | 22 | 92 | 91 | 2026-11-14T06:01:39+00:00 | 2026-11-18T18:23:46+00:00 |
| anchapin/planar-nexus | 2899 | 59 | 58 | 2026-11-09T13:58:15+00:00 | 2026-11-11T13:53:32+00:00 |
| anchapin/plan-coder | 53 | 56 | 55 | 2026-11-14T22:53:13+00:00 | 2026-11-17T14:24:08+00:00 |
| anchapin/matchline | 1352 | 50 | 49 | 2026-11-10T03:54:02+00:00 | 2026-11-11T13:53:32+00:00 |
| anchapin/fluxion | 4647 | 42 | 41 | 2026-11-09T21:25:54+00:00 | 2026-11-12T11:51:09+00:00 |
| anchapin/armored-archer | 1862 | 40 | 39 | 2026-11-17T13:26:37+00:00 | 2026-11-20T05:23:14+00:00 |
| anchapin/matchline | 1349 | 39 | 38 | 2026-11-09T06:28:51+00:00 | 2026-11-11T00:22:22+00:00 |
| anchapin/fluxion | 4723 | 38 | 37 | 2026-11-09T15:02:37+00:00 | 2026-11-11T00:22:22+00:00 |
| anchapin/planar-nexus | 2884 | 38 | 37 | 2026-11-09T13:58:15+00:00 | 2026-11-11T00:22:22+00:00 |
| anchapin/planar-nexus | 2901 | 38 | 37 | 2026-11-09T13:58:15+00:00 | 2026-11-11T00:22:22+00:00 |
| anchapin/plan-coder | 58 | 22 | 21 | 2026-11-16T19:23:15+00:00 | 2026-11-18T18:23:46+00:00 |
| anchapin/plan-coder | 6 | 18 | 17 | 2026-11-13T03:25:50+00:00 | 2026-11-13T17:54:15+00:00 |

### Table 4 -- ack proxy A: terminal disposition after the flag

| measure | value |
|---|---|
| flag rows with a later terminal disposition | 1248 |
| flag rows with no later terminal disposition | 3 |
| flag-level resolution % | 99.8 |
| flagged PRs that reached a terminal disposition | 75 |
| flagged PRs still open at window end | 3 |
| PR-level resolution % | 96.2 |
| median hours first flag -> terminal | 5.0 |
| p90 hours first flag -> terminal | 62.9 |
| max hours first flag -> terminal | 165.5 |
| flagged PRs terminal within 24h | 56 |

### Table 5 -- ack proxy B: acted-on log cross-check

| measure | value |
|---|---|
| acted-on rows in the snapshot | 29 |
| acted-on rows inside the window | 29 |
| acted-on entries naming a flagged repo+PR | 5 |
| flagged PRs named by an acted-on entry | 7 |

Acted-on entries in the window that name a flagged PR:

| ts | loop | item | outcome | flagged PRs |
|---|---|---|---|---|
| 2026-11-18T15:15:30+00:00 | pr-shepherd | matchline#1410 merge tap | acted | anchapin/matchline#1410 |
| 2026-11-19T04:32:56+00:00 | proactive-edition | remaining-items suggestions: armored 1839 fix tests, 1862 coverage fix + one dashboard read, planar 2942 rebase in owning chat | acted | anchapin/armored-archer#1839, anchapin/armored-archer#1862, anchapin/planar-nexus#2942 |
| 2026-11-20T05:31:29+00:00 | pr-shepherd | armored #1862 workflow-touch approval and merge | acted | anchapin/armored-archer#1862 |
| 2026-11-20T13:55:59+00:00 | pr-shepherd | matchline #1420/#1422 test-count workflow bump | acted | anchapin/matchline#1420, anchapin/matchline#1422 |
| 2026-11-20T16:45:50+00:00 | pr-shepherd | matchline #1424 workflow-touch merge | acted | anchapin/matchline#1424 |

### Table 6 -- union of the two ack proxies (upper bound)

| measure | value |
|---|---|
| flagged PRs | 78 |
| flagged PRs with terminal disposition OR acted-on entry | 78 |
| union % | 100.0 |
| flagged PRs with no signal from either proxy | 0 |

Ack proxy definitions (stated, not smoothed over):

  - proxy A: flag row / PR whose PR later reaches a terminal disposition.
  - proxy B: acted-on entry naming the flagged repo and PR number, timestamped at or after the flag.
  - the ledger has no ack field and the acted-on log covers only partial records, so both proxies are bounds, not measurements.

### Table 7 -- per-repo flag / repeat / disposition summary

| repo | flag rows | PRs | repeats | repeat rate % | PRs with terminal |
|---|---|---|---|---|---|
| anchapin/planar-nexus | 399 | 6 | 393 | 98.5 | 6 |
| anchapin/matchline | 247 | 28 | 219 | 88.7 | 25 |
| anchapin/armored-archer | 180 | 3 | 177 | 98.3 | 3 |
| anchapin/plan-coder | 162 | 22 | 140 | 86.4 | 22 |
| anchapin/fluxion | 140 | 15 | 125 | 89.3 | 15 |
| anchapin/retrofit-copilot | 92 | 1 | 91 | 98.9 | 1 |
| anchapin/staging-studio | 17 | 1 | 16 | 94.1 | 1 |
| anchapin/passive-income-pocs | 9 | 1 | 8 | 88.9 | 1 |
| anchapin/building-ai-pocs | 5 | 1 | 4 | 80.0 | 1 |

### Table 8 -- flagged PRs still open at window end

| repo | PR | flag rows | repeats | disposition |
|---|---|---|---|---|
| anchapin/matchline | 1420 | 1 | 0 | none (still open at window end) |
| anchapin/matchline | 1422 | 1 | 0 | none (still open at window end) |
| anchapin/matchline | 1424 | 1 | 0 | none (still open at window end) |

---

## Window and input notes

* **Window**: the last 4 weeks present in the ledger snapshot,
  `2026-10-25T08:52:24+00:00 .. 2026-11-22T08:52:24+00:00`.
* **The snapshot is shorter than the window.** The ledger snapshot
  spans only 13.16 days (`2026-11-09T04:59:56Z .. 2026-11-22T08:52:24Z`),
  so all 2559 rows are in the window. The measured "last 4 weeks" are
  therefore really "the whole snapshot" -- **there is no 4-week history
  in this input**. Any rate in this report is a rate over ~13 days, not
  over 4 weeks. The snapshot's own first row says so explicitly:
  "Historical per-action rows do not exist; counting starts here."
* **Terminal disposition vocabulary.** The issue names the terminal
  dispositions "merge / closed / merged_by_shepherd". The ledger's own
  spellings are `merged`, `closed_unmerged` and `merged_by_shepherd`;
  the census uses the ledger's spellings (Table 1 marks which action is
  which role) and the script carries the mapping in
  `TERMINAL_MAPPING`.
* **PR identity.** Rows are keyed by repo slug + PR number. The ledger
  writes some repos both as `anchapin/fluxion` and as `fluxion`; the
  slug normalises those to the same PR, so a flag and its disposition
  are never split across two spellings.

## Ack proxy definitions (and what each number can and cannot mean)

The ledger records what the shepherd *did*. It has **no ack field**:
there is no row saying "Alex answered this question". So "how many
flags were acked" is not directly measurable from these inputs, and
this report does not pretend otherwise. Two proxies are reported
separately, each with the bias it carries.

**Proxy A -- terminal disposition after the flag.**
A flag is treated as resolved when its PR later reaches a terminal
disposition (`merged`, `merged_by_shepherd`, `closed_unmerged`).

* Flag-level: 1248 of 1251 flag rows have a later terminal disposition
  for their PR (99.8%).
* PR-level: 75 of 78 flagged PRs reached a terminal disposition (96.2%).
* Latency from the PR's *first* flag to its terminal disposition:
  median 5.0 h, p90 62.9 h, max 165.5 h; 56 of 75 PRs were terminal
  within 24 h.
* **Bias**: this measures *resolution*, not *acknowledgement*. A PR can
  be merged by the shepherd without Alex ever answering the question
  that was raised (the ledger has a `merged_by_shepherd` action and a
  `merged` action whose own outcome text says "merged externally ...
  Alex merged"). It is an **upper bound** on the ack rate.
* **Bias**: a PR with 143 flag rows contributes 143 "resolved" flag
  rows. The flag-level number is therefore dominated by the repeat
  count, which is why the PR-level number is the one the verdict uses.

**Proxy B -- acted-on log cross-check.**
An acted-on entry counts as an ack of a flag when it names that flag's
repo and PR number and is timestamped at or after the flag. The matcher
reads only the entry's `item` text (a `note` routinely mentions PRs
that were *skipped*, not acted on) and only counts PRs that were
actually flagged in the window.

* 29 acted-on rows in the snapshot, all 29 inside the window.
* 5 entries name a flagged repo+PR; they cover 7 flagged PRs
  (Table 5). One entry names three PRs at once.
* **Bias (important)**: the acted-on log is the *proactive-loop* tap
  log. It only contains taps that were recorded as proactive offers.
  A flag answered in chat without a recorded proactive tap is
  **invisible** to this proxy. 5 of 29 entries matching is a statement
  about the acted-on log's coverage, **not** about Alex's attention.
  This number is a **lower bound**.

**Union (upper bound on the PR-level ack rate).**
A flagged PR counts as "acked or acted on" when it reached a terminal
disposition **or** has a matching acted-on entry: 78 of 78 flagged PRs
(100%). The three PRs that never reached a terminal disposition
(matchline #1420, #1422, #1424 -- Table 8) are exactly the three that
proxy B covers, via the "matchline #1420/#1422 test-count workflow bump"
and "matchline #1424 workflow-touch merge" entries. So the union is
100% only because the two proxies cover disjoint failure modes; the
union is **not** a claim that 100% of questions were answered.

## Verdict

**PROCEED F2.**

The issue's kill criterion for F2 is: *kill if the chat channel is
demonstrably not losing anything -- e.g. repeats are rare and flagged
items reach a terminal disposition or an acted-on entry promptly.*

Measured against the tables above:

| kill-criterion half | measured | result |
|---|---|---|
| repeats are rare | 1173 of 1251 flag rows (**93.8%**) repeat an already-open question (threshold: rare = <= 10%) | **fails** |
| flagged items reach a terminal disposition or an acted-on entry promptly | 75 of 78 flagged PRs (**96.2%**) reach a terminal disposition, median 5.0 h from first flag | passes |

One half of the criterion passes and one half fails, so the criterion
as a whole is not met and F2 is **not** killed. The verdict follows
the numbers: the channel *is* demonstrably losing something.

What is being lost is repetition, not silence:

* 1251 flag rows land on only **78 distinct PRs** -- 16 flag rows per
  flagged PR on average. One PR (planar-nexus#2942) was flagged 143
  times in 7 days; armored-archer#1839 139 times; retrofit-copilot#22
  92 times.
* 432 of the repeats carry the **identical outcome text** as the
  question already open: the same question asked again, verbatim,
  because the answer did not arrive. The other 741 repeats carry a
  different outcome text (a new reason on a PR whose previous question
  was still open) -- those are re-asks of a stale PR with fresh
  content, which is a different failure mode.
* The questions that are genuinely hard for Alex are all
  workflow-touch / test-count-gate questions: every one of the three
  PRs still open at the end of the window is a `needs_operator`
  workflow-touch flag (matchline #1420, #1422, #1424, "test-count gate
  ... workflow bump via desktop route"), and the acted-on log shows
  Alex approving exactly these as "workflow-touch approval and merge".

So the loss is: the chat channel carries the *same* Alex-facing
question repeatedly while the answer is pending, and there is no
per-question identity in the ledger to tell "still waiting" from "asked
again". That is what a first-class questions channel (F2 Phase 1) would
fix. Phase 0 built nothing.

### What Phase 0 did NOT measure (and cannot, from these inputs)

* **Real ack rate.** Not measurable: the ledger has no ack field and
  the acted-on log covers only proactive taps (see the proxy
  definitions above). Only bounds are reported.
* **A true 4-week history.** Not available: the ledger snapshot covers
  13.16 days and states that historical rows do not exist.
* **Whether a repeat was "the same question".** The census uses the
  issue's definition (same repo+PR, no intervening terminal
  disposition) and additionally splits repeats by whether the outcome
  text is identical or different; there is no question id in the
  ledger, so anything finer is not measurable.
* **Chat-channel content.** Not measurable: no chat transcript is in
  scope for this Phase 0.

### Per-part verdict

| part | verdict | basis |
|---|---|---|
| F2 Phase 0 (this census) | **done** -- measurement only, nothing built | tables above are reproducible from the two input snapshots via `bin/questions_census_phase0.py` |
| F2 Phase 1 (questions channel) | **Proceed** (not started here) | kill criterion not met: 93.8% repeat rate |
| F1 | out of scope for this part | separate part |
| F3 | out of scope for this part | Phase 0 already landed in PR #150 |
