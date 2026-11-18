# Shepherd typed-snapshot — Phase 0 (retrospective, kill-gated)

Issue: plan-coder #124, Phase 0 only. Goal: reconstruct typed shepherd snapshots as of
`ledger_ts` for the 40 most recent terminal dispositions in the shepherd ledger and
measure (1) per-field completeness, (2) agreement between the reconstructed CI state and
the ledger's prose `--ci` string, (3) invisible-error yield, (4) median capture cost in
gh calls per disposition.

**Verdict: KILL (provisional — blocked on data access, not on approach).**
Two of the three Proceed gates could not be measured in this environment because the
shepherd ledger is outside the worktree and the isolation rules forbid reading it.
Per the issue's stated bars, an unmeasurable gate cannot be claimed as met, so the
spike does not certify PROCEED. See [Gates and verdict](#gates-and-verdict) and
[Re-run plan](#re-run-plan-what-one-ledger-read-would-settle).

Every number below is **measured** (produced by a command quoted in this report) unless
it is explicitly labelled *inferred* or *unavailable*. Labels follow the brief's
evidence ranking: gh API facts > ledger prose > inference.

---

## 1. Scope limitation — the ledger was not readable

The brief names the ledger as
`~/workspace/goals/token-plan-coding-manager-setup/hidden_files/shepherd_ledger.jsonl`.
That path is outside this worktree, and the isolation rules for this run forbid reading
or writing anything outside the worktree except `gh` API reads and `/tmp` scratch.
The ledger was therefore **not read**, and **no ledger data is invented anywhere in this
report**.

Consequences, stated explicitly (as the brief requires):

| Measure | Status | Reason |
|---|---|---|
| Ledger prose `--ci` string | **unavailable — no ledger access** | path outside worktree |
| Ledger `ledger_ts` per disposition | **unavailable**; disposition time used as the as-of anchor instead | same |
| Agreement between reconstructed CI state and ledger prose | **unverifiable — no ledger access** (0 of 40 dispositions verifiable) | same |
| Invisible-error yield, in the issue's definition | **unmeasurable** (see §6) | needs ledger prose + the shepherd's decision-time evidence |
| Per-field completeness of the reconstruction | **measured** from gh alone | see §5 |
| Capture cost in gh calls per disposition | **measured** from gh alone | see §7 |

Sample substitution (as the brief directs): the disposition sample is the 40 most recent
merged/closed PRs across the plan-coder-managed repos, taken from
`gh pr list --state merged` / `--state closed` with timestamps.

---

## 2. Terminal disposition kinds (read from the code, not guessed)

Read from `bin/shepherd_ledger.py` (the append-only shepherd action ledger) before
sampling:

* `ACTIONS = ("rerun", "fix_attempt", "merged", "merged_by_shepherd",
  "needs_operator", "closed_unmerged", "merge_conflicts", "trigger_requested",
  "checked", "snapshot", "other")`
* `MERGE_ACTIONS = ("merged", "merged_by_shepherd")`

Terminal dispositions (an action that ends shepherd handling of a PR) are therefore:
**`merged`, `merged_by_shepherd`, `closed_unmerged`, `merge_conflicts`,
`needs_operator`**. Non-terminal actions (`rerun`, `fix_attempt`, `checked`,
`trigger_requested`, `snapshot`, `other`) are cycle-internal and were not sampled.

The gh-side substitute maps onto the same set: a merged PR ≡ `merged` /
`merged_by_shepherd`; a closed-unmerged PR ≡ `closed_unmerged`. `merge_conflicts` and
`needs_operator` have no gh timestamp of their own (they are shepherd-side outcomes),
so they cannot be sampled without the ledger — noted as a coverage gap, not hidden.

---

## 3. Sample definition

**Repos.** The issue's repo list lives in the deployment config (`config/deployment.local.json`,
gitignored, not present in this worktree — `config/deployment.example.json` is the
placeholder). The managed-repo set was therefore derived from gh evidence: a repo counts
as plan-coder-managed iff it carries PRs whose head branch is `worker/*`, the branch
namespace the supervisor creates (`prompts/backlog-supervisor.md`: `-b worker/<issue>-<short-slug>`)
and the only namespace the shepherd is allowed to merge (`prompts/pr-shepherd.md`:
"The head branch is `worker/*`").

Command (one call per candidate repo; 25 repos scanned, 12 qualified):

```
gh pr list --repo <owner>/<repo> --state all --limit 200 \
   --json number,headRefName,state,mergedAt,closedAt,createdAt,url,title,isDraft,author,mergeCommit
```

Qualifying repos (12) and their `worker/*` PR counts:

| repo | worker/* PRs seen |
|---|---|
| anchapin/planar-nexus | 67 |
| anchapin/fluxion | 108 |
| anchapin/matchline | 72 |
| anchapin/staging-studio | 55 |
| anchapin/plan-coder | 37 |
| anchapin/portkit-v2 | 36 |
| anchapin/armored-archer | 35 |
| anchapin/building-ai-pocs | 35 |
| anchapin/retrofit-copilot | 10 |
| anchapin/home-energy-twin | 7 |
| anchapin/passive-income-pocs | 1 |
| anchapin/manamind | 1 |

Non-qualifying repos (scanned, zero `worker/*` PRs, therefore out of sample):
`openstudio-server-operator`, `pAIssive_income`, `OSimFlow`, `foundry-x`, `portkit`,
`mindforge`, `ArbitrageAI`, `ResumeAI`, `conan-recipes`, `foreman`,
`179d-surrogate-optimizations`, `nexus-proxy`, `openstudio-server-nomad-pack`.

**Disposition.** For every `worker/*` PR in the qualifying repos: disposition time =
`mergedAt` if merged, else `closedAt`; disposition kind = `merged` or `closed_unmerged`.
Sort descending by disposition time, take the first 40.

**Sample (n = 40), 2026-11-17T17:24:40Z → 2026-11-18T15:24:25Z:**

| # | disposition time (UTC) | repo | PR | kind | head branch |
|---|---|---|---|---|---|
| 1 | 2026-11-18T15:24:25Z | home-energy-twin | 24 | merged | worker/9-onboarding-hpxml |
| 2 | 2026-11-18T15:15:01Z | matchline | 1410 | merged | worker/1409-stage6-geometric-visibility |
| 3 | 2026-11-18T14:47:42Z | fluxion | 4840 | merged | worker/4838-verify-entry |
| 4 | 2026-11-18T13:27:10Z | home-energy-twin | 23 | merged | worker/8-hpxml-adapter |
| 5 | 2026-11-18T13:27:06Z | portkit-v2 | 466 | merged | worker/463-unknown-why |
| 6 | 2026-11-18T12:43:10Z | matchline | 1413 | merged | worker/1411-verify-entry |
| 7 | 2026-11-18T11:22:36Z | home-energy-twin | 22 | merged | worker/3-spike-a-hands-on |
| 8 | 2026-11-18T10:52:39Z | planar-nexus | 2981 | merged | worker/2419-state-copy-benchmark |
| 9 | 2026-11-18T10:22:28Z | home-energy-twin | 21 | merged | worker/7-golden-house |
| 10 | 2026-11-18T09:52:03Z | portkit-v2 | 462 | merged | worker/299-alt-entry-functions |
| 11 | 2026-11-18T09:24:54Z | home-energy-twin | 20 | merged | worker/4-ochre-stepping-spike |
| 12 | 2026-11-18T07:54:16Z | home-energy-twin | 19 | merged | worker/2-privacy-notice |
| 13 | 2026-11-18T07:24:26Z | home-energy-twin | 18 | merged | worker/6-core-types |
| 14 | 2026-11-18T06:52:37Z | portkit-v2 | 461 | merged | worker/243-hydraulic-research |
| 15 | 2026-11-18T06:23:27Z | plan-coder | 111 | merged | worker/80-native-codemap |
| 16 | 2026-11-18T05:54:33Z | fluxion | 4839 | merged | worker/r154-pyo3-measures-fix |
| 17 | 2026-11-18T05:41:16Z | planar-nexus | 2964 | merged | worker/2963-mwg-fixes |
| 18 | 2026-11-18T05:06:22Z | planar-nexus | 2971 | merged | worker/2965-explore-keyword |
| 19 | 2026-11-18T04:43:57Z | planar-nexus | 2954 | merged | worker/2951-bloodrush |
| 20 | 2026-11-18T04:26:33Z | planar-nexus | 2945 | merged | worker/2940-discard-dialog-memo |
| 21 | 2026-11-18T04:22:29Z | matchline | 1412 | merged | worker/1407-repair-loop-enable |
| 22 | 2026-11-18T04:12:50Z | planar-nexus | 2955 | merged | worker/2950-emerge-cost |
| 23 | 2026-11-18T03:48:19Z | planar-nexus | 2941 | merged | worker/2938-unskip-idb-checksum |
| 24 | 2026-11-18T03:25:00Z | matchline | 1403 | merged | worker/1402-phase0-export-gate |
| 25 | 2026-11-18T03:24:55Z | planar-nexus | 2944 | merged | worker/2936-sba-token-cease-map |
| 26 | 2026-11-18T03:18:23Z | passive-income-pocs | 5 | merged | worker/2-seller-profit-tracker |
| 27 | 2026-11-18T03:00:36Z | plan-coder | 92 | merged | worker/86-edd-tune-cost |
| 28 | 2026-11-18T02:53:05Z | planar-nexus | 2943 | merged | worker/2937-mana-pool-assert |
| 29 | 2026-11-18T02:48:26Z | plan-coder | 91 | merged | worker/89-worktree-persistence |
| 30 | 2026-11-18T02:32:21Z | matchline | 1408 | merged | worker/1407-repair-loop |
| 31 | 2026-11-18T02:23:05Z | portkit-v2 | 460 | merged | worker/299-loot-function-conditions |
| 32 | 2026-11-18T01:24:55Z | planar-nexus | 2980 | merged | worker/2978-replay-coverage |
| 33 | 2026-11-18T00:53:09Z | fluxion | 4834 | merged | worker/r153-example-diffuse-shgc-fix |
| 34 | 2026-11-18T00:41:18Z | fluxion | 4835 | merged | worker/r153-sccache-prime-redesign |
| 35 | 2026-11-18T00:24:59Z | fluxion | 4836 | merged | worker/r153-audit-advisory-drift |
| 36 | 2026-11-17T23:53:15Z | planar-nexus | 2979 | merged | worker/2975-state-at-position-leaf |
| 37 | 2026-11-17T20:40:37Z | planar-nexus | 2947 | merged | worker/2946-game-board-bundle-slim |
| 38 | 2026-11-17T20:21:30Z | planar-nexus | 2977 | merged | worker/2976-picomatch-fix |
| 39 | 2026-11-17T17:36:38Z | plan-coder | 76 | merged | worker/73-manager-cli-phase0 |
| 40 | 2026-11-17T17:24:40Z | portkit-v2 | 459 | merged | worker/282-stacked-item-layers |

Boundary check (measured): the 41st and 42nd dispositions in the same ordering are
`anchapin/plan-coder#75` @ 2026-11-17T16:55:02Z and `anchapin/portkit-v2#458` @
2026-11-17T16:23:55Z — the sample is a contiguous, unbroken recency window.

Sample composition (measured): all 40 are `merged`; **0** `closed_unmerged`
(the most recent closed-unmerged `worker/*` PR predates this window). 7 repos
contribute: planar-nexus 13, home-energy-twin 7, fluxion 5, matchline 5, portkit-v2 5,
plan-coder 4, passive-income-pocs 1 (13+7+5+5+5+4+1 = 40).

---

## 4. Method

### 4.1 As-of filtering rule

A check run belongs to the snapshot of a disposition iff
**`check_runs[].completed_at <= disposition time`**. Checks still running at the
disposition time (`status != "completed"`) are counted as *pending*, never as passed.
Reviews count iff `reviews[].submitted_at <= disposition time`; label events count iff
`timeline[].created_at <= disposition time`. The disposition time is the as-of anchor
because the ledger's `ledger_ts` is unavailable (§1).

### 4.2 Reconstruction (retrospective, gh only)

Per disposition, 6 gh calls:

```
gh api repos/<owner>/<repo>/pulls/<n>                                   # state, merged_at, closed_at, head sha, draft, changed_files
gh api repos/<owner>/<repo>/commits/<merge_commit_sha>                  # merge-commit parents -> head sha AS OF the merge
gh api repos/<owner>/<repo>/commits/<head_sha>/check-runs?per_page=100 # per-check status/conclusion/completed_at
gh api repos/<owner>/<repo>/actions/runs?head_sha=<head_sha>&per_page=100
gh api repos/<owner>/<repo>/pulls/<n>/reviews?per_page=100
gh api repos/<owner>/<repo>/issues/<n>/timeline?per_page=100
```

Head sha as of the disposition is taken as the **second parent of the merge commit**
(a merge commit's parents are [base, PR head]); where the merge was not a merge commit
the PR's head sha is used. Measured: for all 40 dispositions the merge commit had 2
parents and the derived head sha equalled the PR's current `head.sha` (40/40) — the
branch had not moved after the merge, so the PR's checks are the checks of the merged
sha.

### 4.3 Prospective capture (what a live Phase-1 tool would cost)

```
gh pr view <n> --repo <repo> --json state,mergedAt,closedAt,mergeable,mergeStateStatus,\
headRefOid,headRefName,reviewDecision,statusCheckRollup,labels,isDraft,author,title,url,createdAt,updatedAt
gh pr checks <n> --repo <repo> --json name,state,bucket,completedAt,startedAt,link,workflow,event
```

`gh pr view` alone returns the whole typed snapshot in **one** call: `statusCheckRollup`
carries per-check `status`, `conclusion`, `startedAt`, `completedAt`, `name`,
`workflowName`. `gh pr checks` is the optional second call that adds gh's own
`bucket` (pass/fail/pending) view of the same checks.

### 4.4 Typed snapshot schema

14 fields, chosen from what the shepherd's own decision needs (`prompts/pr-shepherd.md`
steps 1–2 and the MERGE POLICY): disposition kind and time; head sha; CI state, CI
check set, failing check names, pending count; review decision; labels; draft flag;
mergeable; merge-state status; shepherd flags.

### 4.5 Reproduce

All artefacts are throwaway under `/tmp` (no repo file outside this report was touched):
`/tmp/select.py` (sample), `/tmp/collect.py` (reconstruction), `/tmp/metrics.py`
(tables), `/tmp/live.py` (live capture), raw JSON in `/tmp/raw/`.

---

## 5. Result 1 — per-field completeness of the reconstruction (measured, n = 40)

| # | snapshot field | available | rate | note |
|---|---|---|---|---|
| 1 | disposition kind | 40/40 | 100% | all `merged` |
| 2 | disposition timestamp | 40/40 | 100% | `merged_at` |
| 3 | head sha as of disposition | 40/40 | 100% | merge-commit 2nd parent |
| 4 | head sha == PR head sha | 40/40 | 100% | branch did not move post-merge |
| 5 | CI check count | 40/40 | 100% | 0 checks on 1 PR |
| 6 | CI state at T | 40/40 | 100% | pass 33 / fail 6 / none 1 |
| 7 | CI failing check names at T | 40/40 | 100% | empty on 34, populated on 6 |
| 8 | CI pending count at T | 40/40 | 100% | 0 on all 40 (no check was in flight) |
| 9 | review decision at T | 40/40 | 100% | none 21 / commented 12 / approved 7 / changes-requested 0 |
| 10 | labels at T | 40/40 | 100% | all 40 label sets empty |
| 11 | draft flag at T | 40/40 | 100% | no draft events in any timeline |
| 12 | mergeable at T | 0/40 | 0% | **structurally unavailable**: GitHub returns `null`/`UNKNOWN` for closed PRs |
| 13 | merge-state status at T | 0/40 | 0% | same |
| 14 | shepherd flags at T (test-tamper / jittest / needs-operator) | 0/40 | 0% | **not on GitHub**: lives in `shepherd.json` / local state |

Totals:

* gh-reconstructable decision fields (1–11): **440 / 440 field-slots = 100.0%**
* extended 14-field schema (incl. 12–14): **440 / 560 = 78.6%**
* per-disposition: 11/11 fields on all 40 dispositions (40/40 dispositions fully
  reconstructed on the gh-reconstructable schema).

Field 12/13 are a **retrospective-method limitation, not a snapshot-design limitation**
— measured live on 22 open `worker/*` PRs (§7), `gh pr view` returns `mergeable`
`MERGEABLE` on 18, `CONFLICTING` on 1, `UNKNOWN` on 3 (GitHub still computing; a live
tool must retry) and `mergeStateStatus` `UNSTABLE` 7 / `BLOCKED` 6 / `CLEAN` 4 /
`DIRTY` 1 / `BEHIND` 1 / `UNKNOWN` 3. Field 14 needs no gh call at all in a live tool
(it is local state).

---

## 6. Result 2 — agreement with the ledger prose `--ci` string (unverifiable)

| outcome class | count |
|---|---|
| agree (reconstructed CI state == ledger prose) | 0 |
| disagree | 0 |
| **unverifiable — no ledger access** | **40** |
| total | **40** |

Reason: the ledger is outside the worktree and the isolation rules forbid reading it
(§1). No ledger `--ci` string was read, quoted, or inferred. Per the brief, unparseable
or absent prose counts as *unverifiable*, not as disagreement — here it is absent for
all 40.

Reconstructed CI state at T (measured, for the record): `pass` 33, `fail` 6, `none` 1.
The 6 failures are, in full: `dependency security audit = failure` on 5 matchline PRs
(#1403, #1408, #1410, #1412, #1413) and `Sourcery review = failure` on
`anchapin/plan-coder#91` — i.e. all 6 are **non-blocking third-party checks**, not the
PR's own test/lint gate. That is a typing requirement for any future snapshot: a
boolean "CI green" field would have mis-classified 5 legitimate merges as failures.

---

## 7. Result 3 — invisible-error yield (unmeasurable as defined; measured floor and proxy)

The issue defines invisible-error yield over three components, all of which are stated
against the shepherd's **decision-time evidence**:

| component | measurability | measured value |
|---|---|---|
| decision-time evidence empty | **partially measurable** (gh side only) | **1 / 40 = 2.5%** |
| stale by more than one cycle | **unmeasurable** — the "one cycle" anchor is `shepherd.interval_minutes` from the deployment config, which is not in this worktree | see staleness proxy below |
| contradicting the prose | **unmeasurable** — needs the ledger prose | n/a |

**Measured floor (2.5%, inferred from gh):** `anchapin/passive-income-pocs#5` was merged
at 2026-11-18T03:18:23Z with **zero check runs** on its head sha; `gh pr checks` exits
1 with "no checks reported on the 'worker/2-seller-profit-tracker' branch". The
shepherd's CI evidence at decision time was therefore necessarily empty for that PR.

**Staleness proxy (measured, threshold unanchored):** age of the newest completed check
on the merged head sha at the disposition time — min 0.0 min, median 5.9 min, p90
40.2 min, max **4358.1 min (72.6 h)**, over the 39 dispositions that had checks.
16/39 dispositions have CI evidence older than 15 min, 6/39 older than 30 min (the
reference cadence in `config/deployment.example.json` is
`shepherd.interval_minutes: 30`), 2/39 older than 60 min. The worst case is
`anchapin/matchline#1403` (merged 2026-11-18T03:25:00Z; newest completed check
72.6 h earlier — and that check was the *failing* one). Label **inferred**: without
the live deployment config the real cycle interval cannot be read, so "more than one
cycle" cannot be evaluated against a fixed threshold.

**Detectable-error proxy (measured):** the fraction of dispositions whose reconstructed
as-of snapshot contained a condition a typed snapshot would have surfaced — failing
check (6) or no CI evidence at all (1) = **7 / 40 = 17.5%**; 0/40 had a check pending
at T and 0/40 had a changes-requested review at T. This proxy is **not** the issue's
gate metric (it cannot say whether the shepherd's prose already carried the
information), but it is the quantity the gate is trying to bound, and it sits above
the 5% bar.

**Gate 3 verdict: UNMEASURABLE.** The measured floor is 2.5% (below the 5% bar), the
proxy is 17.5% (above it), and the two remaining components cannot be evaluated at
all. The gate can be neither passed nor failed from gh alone.

---

## 8. Result 4 — capture cost in gh calls per disposition (measured)

**Prospective capture, 40 dispositions** (`gh pr view` + `gh pr checks`):

| call | success | note |
|---|---|---|
| `gh pr view --json …` | 40/40 rc=0 | carries the whole typed snapshot incl. `statusCheckRollup` |
| `gh pr checks --json …` | 39/40 rc=0, 1 rc=1 | rc=1 = "no checks reported" (passive-income-pocs#5) — a *data* condition, not a tool failure |

* **1-call variant** (`gh pr view` only): median **1 gh call / disposition**
* **2-call variant** (`gh pr view` + `gh pr checks`): median **2 gh calls / disposition**
* measured wall clock per disposition: `gh pr view` median 2.09 s, `gh pr checks` median 2.20 s (22 live open PRs, 6-way parallel)

**Retrospective reconstruction** (what this Phase 0 measurement itself cost): a median
of **6 gh calls per disposition** (6 on 40/40), because reconstructing *history* needs
the check-run, workflow-run, review, timeline and merge-commit endpoints. That is the
cost of the *measurement*, not of the *product*; the product captures live.

**Gate 4: PASS** — median capture cost 1–2 gh calls/disposition ≤ the 2-call bar.

---

## 9. Gates and verdict

| # | Proceed gate | Bar | Measured | Result |
|---|---|---|---|---|
| 1 | reconstruction ≥ 90% complete | ≥ 90% | **100%** (440/440) on the 11 gh-reconstructable decision fields, 40/40 dispositions; **78.6%** (440/560) on the extended 14-field schema | **PASS** (see note) |
| 2 | snapshot ↔ ledger `--ci` agreement | measurable | **unverifiable — no ledger access** (40/40) | **UNMEASURED** |
| 3 | invisible-error yield | ≥ 5% | **unmeasurable**; measured floor 2.5%, detectable-error proxy 17.5% | **UNMEASURED** |
| 4 | median capture cost | ≤ 2 gh calls/disposition | **1** (single call) / **2** (with `gh pr checks`) | **PASS** |

Note on gate 1: the two fields that fail retrospectively (`mergeable` at T,
`merge-state status` at T) are unavailable because GitHub does not retain mergeability
history for closed PRs — measured live, they resolve on 19/22 open PRs (3 transient
`UNKNOWN` needing a retry). The third zero (`shepherd flags`) is not a GitHub fact at
all and would come from local state at zero gh cost. The gate is therefore met on the
decision fields that a snapshot exists to carry, and the shortfall is a
data-retention property of the retrospective method, not a schema defect.

### Verdict: **KILL** (provisional — blocked on data access)

Reason: the issue's bars require all three gates to be demonstrated. Gates 2 and 3 are
not measurable from `gh` alone and the ledger was not readable in this environment, so
PROCEED cannot be certified. Per the brief's rule ("an honest 'unmeasurable, therefore
KILL by the stated bars' beats an estimate"), the spike returns **KILL**, with the
cause recorded as **data access, not approach**: the two measurable gates (1 and 4)
pass, the typed-schema and capture-cost design is settled, and the measured proxy yield
(17.5%) sits above the 5% bar.

The KILL is *not* a statement that typed snapshots are wrong; it is a statement that
Phase 0 could not produce the evidence the issue asks for without the ledger.

### Re-run plan: what one ledger read would settle

Mount or copy the ledger read-only into the worktree (or hand the worker a
ledger-path + the 40 `ledger_ts` values and `--ci` strings) and re-run:

1. Replace the disposition-time anchor with `ledger_ts` (the ledger's own as-of stamp)
   and re-run the as-of filter — this also removes the ~1-cycle skew between the
   shepherd's actual decision and the merge timestamp.
2. Parse each ledger `--ci` prose string into a CI state and compare with the
   reconstructed `ci_state` → gate 2 becomes a measurement (agreement / disagreement /
   unparseable-unverifiable).
3. Compute gate 3 exactly as defined: decision-time evidence empty (vs. the 2.5%
   measured floor), stale by more than one cycle (anchored on the live
   `shepherd.interval_minutes`), and contradicting the prose.
4. Carry over this report's schema, sample definition and capture-cost measurement
   unchanged (they are already reproducible from gh).

Expected effort: one command plus the three tables above; no new code.

---

## 10. Validation — the report's tables reconcile

**Sample size = sum of outcome classes.**

| table | classes | sum |
|---|---|---|
| §3 sample composition by repo | planar-nexus 13 + home-energy-twin 7 + fluxion 5 + matchline 5 + portkit-v2 5 + plan-coder 4 + passive-income-pocs 1 | **40** |
| §5 CI state at T | pass 33 + fail 6 + none 1 | **40** |
| §5 review decision at T | none 21 + commented 12 + approved 7 (+ changes-requested 0) | **40** |
| §5 failing-check sets | failing 6 + not failing 34 | **40** |
| §6 agreement | agree 0 + disagree 0 + unverifiable 40 | **40** |
| §7 decision-time CI evidence | evidence empty (measured, gh) 1 + evidence present 39 | **40** |
| §7 detectable-error proxy | error surfaced 7 + no error 33 | **40** |
| §8 capture attempts | `gh pr view` rc=0 40 + rc≠0 0 | **40** |

**Gate numbers restated:** reconstruction completeness **100%** (gh-reconstructable
decision fields; 78.6% extended schema) — bar ≥ 90%; ledger-`--ci` agreement
**unverifiable, 40/40**; invisible-error yield **unmeasurable** (floor 2.5%, proxy
17.5%) — bar ≥ 5%; median capture cost **1** gh call/disposition (**2** with
`gh pr checks`) — bar ≤ 2. Verdict: **KILL** (provisional, blocked on ledger access).

---

## 11. Discipline notes

* **No code deliverable.** No `bin/shepherd_snapshot.py` was written; no prompt, ledger
  schema, config, workflow or existing `bin/` tool was touched. The only repo change is
  this report file.
* **No ledger access, no invented data.** The ledger path is outside the worktree; it
  was not read. Everything about the ledger `--ci` string is reported as unavailable.
* **No tests were run.** This delivery is a report, not code; no repo test command was
  executed, so no test result is claimed.
* **Throwaway scripts** live only under `/tmp` (`select.py`, `collect.py`, `metrics.py`,
  `live.py`, `capture.py`) with raw responses under `/tmp/raw/`.
* **Environment.** `gh` (authenticated as `anchapin`) with the egress proxy exported
  (`HTTPS_PROXY=http://hatch-egress-proxy:3128`); authenticated rate limit 5000/h,
  729 used / 4271 remaining at the end of the run (`gh api rate_limit`); no installs,
  no packages beyond python3 + gh.
* **Labels.** *measured* = produced by a command quoted here; *inferred* = derived from
  measured facts by stated reasoning; *unavailable* = could not be obtained, with the
  reason given.
