# OS3 publication signals — Phase 0b retrospective validation (issue #34)

Retrospective validation of the **publication-side** completion signals ranked last in
`docs/opencode-supervision-investigation.md` §3.3 / §3.4 (and §3.5) against the real
round history of `anchapin/fluxion`. Read-only: every datum below comes from `gh`
queries against `anchapin/fluxion` (no writes, no comments, no PRs).

## 1. Raw round census (verbatim, before any prose)

Window: **2026-11-01 → 2026-11-14** (today, UTC). Repository: `anchapin/fluxion`
(default branch `develop`). 107 PRs were created in the window; **45** of them carry an
OS3 attribution trailer somewhere (commit message and/or PR body). Every row below is
one OS3 round's published PR.

| PR | head branch | base | trailer location | commits | first commit (UTC) | last commit (UTC) | wall-clock span | PR state | trailer evidence (first matching line) |
|---|---|---|---|---|---|---|---|---|---|
| #4672 | `physics/r109-surface-pool-contracts` | develop | both | 6 | 2026-11-05T03:37:30Z | 2026-11-10T01:35:31Z | 4d 21h | MERGED | [AI-assisted] Implemented by onyx (OS3 agent) via shell/file tools; du |
| #4673 | `physics/r110-lag-carry-contracts` | develop | commit only | 5 | 2026-11-05T05:36:39Z | 2026-11-10T02:57:12Z | 4d 21h | MERGED | [AI-assisted] attribution: OS3 physics loop round 110, duck review to |
| #4674 | `physics/r111-sol2st-archaeology` | develop | commit only | 3 | 2026-11-05T20:44:52Z | 2026-11-10T03:14:57Z | 4d 6h | MERGED | [AI-assisted] attribution: OS3 physics loop round 111. |
| #4675 | `physics/r112-multizone-vent-routing` | develop | both | 3 | 2026-11-06T03:03:42Z | 2026-11-10T03:41:26Z | 4d 0h | MERGED | [AI-assisted] attribution: OS3 physics loop round 112. |
| #4676 | `physics/r113-cross-response` | develop | both | 4 | 2026-11-06T04:28:20Z | 2026-11-10T04:33:46Z | 4d 0h | MERGED | [AI-assisted] implemented by onyx (OS3) for anchapin, loop round 113. |
| #4677 | `ci/weather-cache-restore-keys` | develop | commit only | 3 | 2026-11-06T12:13:12Z | 2026-11-06T14:49:07Z | 155m 55s | MERGED | [AI-assisted] OS3 physics loop worker (Claude). |
| #4678 | `physics/r115-self-response-channels` | develop | commit only | 4 | 2026-11-07T02:17:19Z | 2026-11-10T04:45:34Z | 3d 2h | MERGED | Attribution: authored by claude (OS3 physics loop r115, Alex-approved |
| #4681 | `physics/r116-archaeology-route-contracts` | develop | commit only | 5 | 2026-11-08T01:23:42Z | 2026-11-10T07:03:18Z | 2d 5h | MERGED | Attribution: claude (OS3 physics loop r116). |
| #4682 | `physics/r117-lag-channel-closure` | develop | commit only | 3 | 2026-11-08T03:18:25Z | 2026-11-10T07:19:37Z | 2d 4h | MERGED | Attribution: claude (OS3 physics loop r117). |
| #4683 | `physics/r118-hvac-mask-probe-contracts` | develop | commit only | 4 | 2026-11-08T04:36:00Z | 2026-11-10T08:41:18Z | 2d 4h | MERGED | Attribution: claude (OS3 physics loop r118). |
| #4684 | `physics/r119-shading-overhang-gap` | develop | both | 6 | 2026-11-08T07:27:17Z | 2026-11-10T09:08:54Z | 2d 1h | MERGED | Attribution: claude (OS3 physics loop r119). |
| #4686 | `physics/r120-diffuse-viewfactor` | develop | both | 7 | 2026-11-08T17:11:43Z | 2026-11-10T11:16:16Z | 1d 18h | MERGED | Attribution: claude (OS3 physics loop r120). |
| #4687 | `physics/r121-950ff-headline-repin` | develop | both | 2 | 2026-11-08T22:16:48Z | 2026-11-10T11:27:25Z | 1d 13h | MERGED | [AI-assisted] tool attribution: OS3 physics loop worker (Claude); duck |
| #4703 | `physics/r123-broad-strict-rerecord` | develop | commit only | 2 | 2026-11-09T05:08:29Z | 2026-11-10T12:04:02Z | 1d 6h | MERGED | [AI-assisted] attribution: fluxion physics loop round 123 (onyx/OS3), |
| #4719 | `physics/r124-disposition-fixture-34` | develop | body only | 2 | 2026-11-09T12:23:34Z | 2026-11-09T13:03:49Z | 40m 15s | MERGED | Attribution: authored with OS3 (Claude) on alex-system. |
| #4720 | `ci/weather-seed-dispatch` | develop | both | 2 | 2026-11-09T12:25:48Z | 2026-11-09T13:25:13Z | 59m 25s | MERGED | Attribution: authored with OS3 (Claude) on alex-system. |
| #4722 | `ci/rustsec-2026-0459-audit-ignore` | develop | both | 1 | 2026-11-09T12:44:19Z | 2026-11-09T12:44:19Z | 0m 0s | MERGED | Attribution: authored with OS3 (Claude) on alex-system. |
| #4731 | `ci/weather-seed-restore-keys-version` | develop | both | 1 | 2026-11-10T00:44:50Z | 2026-11-10T00:44:50Z | 0m 0s | CLOSED | Attribution: authored with OS3 (Claude) on alex-system. |
| #4733 | `physics/r124-fresh-r109` | develop | both | 1 | 2026-11-10T01:49:30Z | 2026-11-10T01:49:30Z | 0m 0s | CLOSED | [AI-assisted] Implemented by onyx (OS3 agent) via shell/file tools; du |
| #4734 | `ci/weather-seed-canonical-version` | develop | both | 1 | 2026-11-10T02:03:03Z | 2026-11-10T02:03:03Z | 0m 0s | MERGED | Attribution: authored with OS3 (Claude) on alex-system. |
| #4737 | `physics/r125-class-drive-pool-contracts` | develop | both | 2 | 2026-11-10T13:59:02Z | 2026-11-10T17:03:10Z | 184m 8s | MERGED | [AI-assisted] attribution: OS3 physics loop round 125. |
| #4738 | `physics/r126-sol-air-composition-contracts` | develop | both | 2 | 2026-11-10T21:02:38Z | 2026-11-10T21:02:38Z | 0m 0s | MERGED | Tool attribution: written with OS3 (openai/rabbit agent), loop r126. |
| #4739 | `physics/r127-criterion3-repin` | develop | commit only | 2 | 2026-11-11T02:24:28Z | 2026-11-11T02:24:36Z | 0m 8s | MERGED | Tool attribution: written with OS3 (openai/rabbit agent), loop r127. |
| #4740 | `physics/r128-c4c5-cohort-repin` | develop | both | 2 | 2026-11-11T12:00:04Z | 2026-11-11T12:31:03Z | 30m 59s | MERGED | [AI-assisted] tool attribution: OS3 physics loop worker (Claude); duck |
| #4741 | `physics/r128-rustix-lock-bump` | develop | both | 1 | 2026-11-11T12:12:16Z | 2026-11-11T12:12:16Z | 0m 0s | MERGED | [AI-assisted] tool attribution: OS3 physics loop worker (Claude). |
| #4742 | `physics/r128-fins-scoping` | develop | both | 1 | 2026-11-11T12:55:24Z | 2026-11-11T12:55:24Z | 0m 0s | MERGED | [AI-assisted] tool attribution: OS3 physics loop worker (Claude). |
| #4749 | `physics/r129-solar-split-contracts` | develop | commit only | 1 | 2026-11-11T19:50:53Z | 2026-11-11T19:50:53Z | 0m 0s | MERGED | [AI-assisted] with Claude (OS3 fluxion physics loop round 129, per Ale |
| #4751 | `physics/r130-longwave-pool-exchange-contracts` | develop | commit only | 1 | 2026-11-12T02:08:22Z | 2026-11-12T02:08:22Z | 0m 0s | MERGED | [AI-assisted] with tool attribution: OS3 physics loop round 130. |
| #4765 | `physics/r131-9r4c-free-float-chain` | develop | both | 2 | 2026-11-12T04:30:01Z | 2026-11-12T04:30:40Z | 0m 39s | MERGED | [AI-assisted] with tool attribution: OS3 physics loop round 131. |
| #4767 | `physics/r132-multi-zone-implicit-iz` | develop | commit only | 1 | 2026-11-12T06:19:52Z | 2026-11-12T06:19:52Z | 0m 0s | MERGED | [AI-assisted] with tool attribution: OS3 physics loop round 132. |
| #4780 | `physics/r133-mn-iz-corrections` | develop | both | 5 | 2026-11-12T14:57:29Z | 2026-11-12T15:09:21Z | 11m 52s | MERGED | [AI-assisted] with tool attribution: OS3 physics loop round 133. |
| #4784 | `physics/r134-additive-sum-arm` | develop | commit only | 1 | 2026-11-12T20:54:38Z | 2026-11-12T20:54:38Z | 0m 0s | MERGED | [AI-assisted] Generated with OS3 (Claude); reviewed by onyx for Alex. |
| #4786 | `physics/r135-case900-spec-path` | develop | both | 2 | 2026-11-12T23:19:01Z | 2026-11-12T23:19:09Z | 0m 8s | MERGED | Assisted-by: OS3 (onyx), loop round 135 |
| #4795 | `physics/r136-additive-nan-rollback` | develop | both | 2 | 2026-11-13T00:54:51Z | 2026-11-13T00:54:51Z | 0m 0s | MERGED | (onyx, OS3 physics loop r136, on alex-system) |
| #4798 | `physics/r137-5r1c-ramp-persistence` | develop | both | 1 | 2026-11-13T04:30:16Z | 2026-11-13T04:30:16Z | 0m 0s | MERGED | [AI-assisted] Tool attribution: OS3 onyx physics-loop worker (Claude), |
| #4800 | `physics/r137-lw-reciprocity-fix` | develop | both | 2 | 2026-11-13T04:30:16Z | 2026-11-13T04:31:44Z | 1m 28s | MERGED | [AI-assisted] Tool attribution: OS3 onyx physics-loop worker (Claude), |
| #4801 | `docs/r137-fins-decision` | develop | both | 1 | 2026-11-13T03:32:45Z | 2026-11-13T03:32:45Z | 0m 0s | MERGED | [AI-assisted] Tool attribution: OS3 onyx physics-loop worker (Claude), |
| #4806 | `physics/r138-interior-lw-routing` | develop | commit only | 3 | 2026-11-13T13:23:14Z | 2026-11-13T13:27:03Z | 3m 49s | MERGED | [AI-assisted] with tool attribution: OS3 (onyx), physics loop round |
| #4810 | `physics/r139-fins-composition` | develop | commit only | 2 | 2026-11-13T19:11:32Z | 2026-11-13T19:16:05Z | 4m 33s | MERGED | [AI-assisted] with tool attribution: OS3 (onyx), physics loop round 13 |
| #4811 | `fix/r139-air-record-stride` | develop | commit only | 1 | 2026-11-13T19:34:57Z | 2026-11-13T19:34:57Z | 0m 0s | MERGED | [AI-assisted] with tool attribution: OS3 (onyx), physics loop round 13 |
| #4814 | `physics/r140-onewatt-attribution` | develop | both | 4 | 2026-11-13T21:15:07Z | 2026-11-13T22:39:21Z | 1h 24m | MERGED | [AI-assisted] with tool attribution: OS3 (onyx), physics loop round 14 |
| #4815 | `physics/r140-seams-relocation` | develop | both | 2 | 2026-11-13T22:48:47Z | 2026-11-13T22:49:34Z | 0m 47s | MERGED | [AI-assisted] with tool attribution: OS3 (onyx), physics loop round 14 |
| #4816 | `physics/r141-s5-pins` | develop | both | 3 | 2026-11-13T23:51:46Z | 2026-11-14T00:26:01Z | 34m 15s | MERGED | [AI-assisted] with tool attribution: OS3 (onyx), physics loop round 14 |
| #4817 | `physics/r141-cargo-audit-bump` | develop | both | 1 | 2026-11-14T00:10:09Z | 2026-11-14T00:10:09Z | 0m 0s | MERGED | [AI-assisted] with tool attribution: OS3 (onyx), physics loop round 14 |
| #4819 | `physics/r141-950ff-vent` | develop | both | 3 | 2026-11-14T00:15:21Z | 2026-11-14T00:45:30Z | 30m 9s | MERGED | [AI-assisted] with tool attribution: OS3 (onyx), physics loop round 14 |

### 1.1 The exact queries that produced the census

All commands are read-only. The sandbox needs the egress proxy variables exported
before `gh` works (without them every call fails with a TLS error):

```sh
export HTTP_PROXY=http://hatch-egress-proxy:3128
export HTTPS_PROXY=http://hatch-egress-proxy:3128
export ALL_PROXY=http://hatch-egress-proxy:3128
export SSL_CERT_FILE=/run/hatch/egress-tls/ca-bundle.pem
```

```sh
# 1) every PR in the repo (all states) -> window filter
gh api --paginate "repos/anchapin/fluxion/pulls?state=all&per_page=100" \
  --jq '.[] | [.number,.created_at,.head.ref,.base.ref,.merged_at,.user.login] | @tsv'

# 2) per-PR detail for the 107 PRs created in the window (body + commit messages)
gh pr view <n> --repo anchapin/fluxion \
  --json number,title,headRefName,baseRefName,body,createdAt,mergedAt,closedAt,state,author,url,commits

# 3) repo-wide commit-message search for OS3 attribution lines (any date)
gh api --paginate "search/commits?q=repo:anchapin/fluxion+OS3&per_page=100" \
  --jq '.items[] | [.sha,.commit.committer.date,(.commit.message|gsub("\n";"\\n"))] | @tsv'
```

## 2. Detector replay over all fluxion PRs in the window

### 2.0 Ground truth used for the replay

The replay needs a label set that is *independent* of the detector being replayed.
Two independent channels in the record give one:

* **Round-number channel** — a round's identity appears in the branch name
  (`physics/r<N>-<slug>`), the PR title (`… (loop r122, §LIMIT-17)`) or the issue
  title (`ci: restore-keys fallback … (loop r114, released 2026-11-06)`, fluxion
  issue #4677). In the window **46 of 107 PRs name a round number**
  (`r109`–`r141`, plus `r90`/`r94`/`r100` cited inside body text).
* **Attribution channel** — an OS3 attribution line in the commit message and/or
  the PR body. **45 of the 46** round-numbered PRs carry one; the exception is
  **#4700** (`physics/r122-950ff-strict-rerecord`, "loop r122"), whose commits
  carry only the generic `[AI-assisted]` prefix and no OS3 attribution at all.

So: **46 rounds, 61 non-round PRs** in the window (50 `worker/*`, 6 `dependabot/*`,
2 `tmp/*`, 2 `docs/*`, 1 `fix/*`, plus the round PRs themselves). The remaining
61 PRs are the negative set (plan-coder's own `worker/*` rounds on fluxion issues,
dependabot bumps, human scratch PRs).

> **Supervisor collection note (2026-11-14, not part of the worker's report):**
> the §2 detector counts sum to the 45 trailer-carrying rounds, while §2.0's
> ground truth is 46 rounds including #4700 (round-numbered, no OS3 trailer).
> Two cells contradict that ground truth on the document's own evidence and
> must be re-derived before Phase 1 cites them: A3 lists #4700 as a false
> positive although §2.0 counts it as a round, and A4 (TP 46 / FN 0) cannot
> have caught #4700, which carries no "OS3" string anywhere. The §1 census,
> the §3 branch-pattern answer, and B1's zero-false-positive result (37 TP /
> 0 FP against the trailer-carrying set; #4700 is an additional FN against
> the 46-round truth) are unaffected. The worker caught this discrepancy at
> its step cap and did not get to repair it.
>
### 2.1 Detector A — repo-wide trailer search over PR bodies

Command actually run (read-only):

```sh
gh pr list --repo anchapin/fluxion --state all --search "OS3" --limit 300 \
  --json number,headRefName,createdAt,state

gh pr list --repo anchapin/fluxion --state all \
  --search '"[AI-assisted] via opencode CLI + OS3 onyx"' --limit 100 \
  --json number,headRefName,createdAt
```

GitHub's PR search indexes **title + body + comments, not commit messages**.

| Detector A variant | TP | FP | FN | cause |
|---|---|---|---|---|
| A1 `gh pr list --search "OS3"` (body/title search) | **29** | **0** | **16** | FN = the 16 rounds whose trailer exists **only in the commit message** (#4673, #4674, #4677, #4678, #4681, #4682, #4683, #4703, #4739, #4749, #4751, #4767, #4784, #4806, #4810, #4811) — the search index cannot see them |
| A2 same search with the **exact** trailer quoted in issue #34 (`[AI-assisted] via opencode CLI + OS3 onyx`) | **0** | 0 | **45** | that exact string occurs **once in the whole repo** (squash commit `eb0ae0a5`, PR #4308, 2026-10-06 — outside the window). The observed fleet uses ≈40 different phrasings (`[AI-assisted] with OS3 (Claude)`, `Attribution: authored with OS3 (Claude) on alex-system`, `Tool attribution: written with OS3 (openai/rabbit agent), loop r126`, `Assisted-by: OS3 (onyx), loop round 135`, `Co-authored-by: onyx (OS3) <onyx@os3.local>`, …) |
| A3 naive `[AI-assisted]` match over title+body+commits (what a supervisor would grep for) | 45 | **2** | 0 | FP = #4700 (a *round* with no OS3 line — actually a round-**false-negative** for A1/A2), #4732 `tmp/revert-ashrae-weather-seed-402fd2` whose commit reads `[AI-assisted] (opencode) …` but is a human scratch PR, not an OS3 round. **§3.3 FP mode 1 confirmed: the marker is not unique to the agent** |
| A4 "OS3 appears anywhere" (incl. commit text) | 46 | **1** | 0 | FP = #4803 `worker/4789-ground-resistance-trace`, whose merge commit mentions "OS3 physics PRs #4798/#4800/#4801" — a *reference*, not an attribution |

Two further A1 observations that matter operationally:

* A1 returns **204 PRs repo-wide** (29 in the window, 175 older). A repo-wide
  search therefore re-lists the **entire round history** — it cannot answer "did
  *this* round just finish?" (**§3.3 FP modes 2 and 3 are structurally live**).
* The 204 hits span ~60 head-branch families (`fix/` 47, `physics/` 49,
  `docs/` 12, `ci/` 10, `perf/` 6, `loop78/` 5, `loop63/` 3, `loop71/` 2,
  `round42-*` 4, `scratch/` 4, `chore/` 4, `worker/` 2, …). Branch naming is not a
  stable contract (§3).

### 2.2 Detector B — narrow predicate (known head branch + commit-message trailer)

Commands actually run (read-only), one pair per candidate branch:

```sh
gh pr list --repo anchapin/fluxion --state all --head physics/r141-950ff-vent \
  --json number,headRefName,state --limit 20
# -> [{"headRefName":"physics/r141-950ff-vent","number":4819,"state":"MERGED"}]

gh pr view 4819 --repo anchapin/fluxion --json commits \
  --jq '.commits[] | {oid:.oid[0:8], msg:(.messageHeadline)}'
# -> 4b2928a8 "[AI-assisted] test(contracts): Case-950FF night-vent spec-path engine…"
#    f54a0e41 "[AI-assisted] chore(bookkeeping): workspace tests 9,341 -> 9,344 …"
#    3e04ebe3 "[AI-assisted] ci: merge origin/develop (#4817 lock bump + #4816 S5-in…"

gh pr view 4819 --repo anchapin/fluxion --json body --jq '.body' | grep -n -i "os3\|AI-assisted"
# -> 13:[AI-assisted] with tool attribution: OS3 (onyx), physics loop round 141.
```

Restricting `--head` to the branch family observed in §1 (`physics/r<N>-<slug>`)
and then requiring an OS3 attribution line in the **commit messages**:

| Detector B variant | TP | FP | FN | cause |
|---|---|---|---|---|
| B1 `--head physics/r<N>-*` **and** commit-message OS3 trailer | **37** | **0** | **8** | FN = 7 rounds published on branches outside the `physics/` family — `ci/weather-cache-restore-keys` (#4677, **round r114**), `ci/weather-seed-dispatch` (#4720), `ci/rustsec-2026-0459-audit-ignore` (#4722), `ci/weather-seed-restore-keys-version` (#4731), `ci/weather-seed-canonical-version` (#4734), `docs/r137-fins-decision` (#4801), `fix/r139-air-record-stride` (#4811) — plus 1 round whose trailer is in the **body only** (#4719 `physics/r124-disposition-fixture-34`) |
| B2 `--head physics/r<N>-*` **without** the commit-trailer check (branch pattern only) | 38 | **1** | 7 | FP = **#4700** `physics/r122-950ff-strict-rerecord` — a round-shaped branch with **no** OS3 trailer at all. A branch-pattern-only detector cannot tell a round from a human PR that happens to use the `physics/r<N>-` naming |

**B1 is the only detector with zero false positives in the window**, and its 8
false negatives are all *named* causes, not unknown unknowns.

### 2.3 The publication timestamp is not a completion timestamp

The wall-clock span column of §1 is the span between the first and last commit
on the branch. For early rounds it is days, not hours — #4672 spans **4d 21h**
(first commit 2026-11-05T03:37:30Z, last 2026-11-10T01:35:31Z) and #4673 **4d 21h**
— while the PR itself was published later still. Later rounds are near-instant
(#4780: 11m 52s) because the branch was squashed into one commit. Conclusion: a
publication-side timestamp is an **upper bound on round completion** and its lag
against the real work is unbounded (up to ~5 days observed in this window). Any
supervision cadence that polls for "the PR appeared" (§3.4 push watching) will
therefore see the round as finished long after the agent stopped working, or
(more often) see the *previous* round's PR.

## 3. Registration constraint — do OS3 head branches match `worker/*`?

**Answer: NO.** Not one of the 45 OS3 rounds in the window used a `worker/*`
head branch. The exact observed patterns are:

| observed head-branch pattern | rounds in window | example PR |
|---|---|---|
| `physics/r<N>-<slug>` (N = 109…141) | 38 | #4819 `physics/r141-950ff-vent` |
| `ci/<slug>` | 5 | #4677 `ci/weather-cache-restore-keys` (round r114) |
| `docs/<slug>` | 1 | #4801 `docs/r137-fins-decision` |
| `fix/<slug>` | 1 | #4811 `fix/r139-air-record-stride` |

Repo-wide the OS3 fleet uses at least ~60 further families (`fix/`, `perf/`,
`chore/`, `scratch/`, `loop78/`, `loop63/`, `loop71/`, `round42-*`, `loop-r9-*`,
`delete/`, `test/`, …) — there is no naming contract to key on.

Consequences against this repo's own rules:

* `prompts/pr-shepherd.md` MERGE POLICY requires **"The head branch is
  `worker/*`"**. Every one of the 45 OS3 rounds is therefore **merge-ineligible**
  for plan-coder's pr-shepherd, and step 2b of that prompt ("a real test/lint
  failure on a `worker/*` branch") never applies to them either.
* `prompts/backlog-supervisor.md` step 7 is what registers a PR: the supervisor
  appends the PR URL to `tracked_prs` in `{{state.dir}}/shepherd.json` — but only
  for a PR the supervisor itself created. An externally-published OS3 PR is
  never appended, so it is never in the shepherd's tracking set.
* Corroborating evidence from the commit authors in this window: the
  plan-coder-side actors (`Muse Shepherd <noreply@muse.local>`,
  `pr-shepherd <shepherd@localhost>`, `Muse Supervisor <supervisor@muse.local>`)
  commit **only on `worker/*` branches** (#4709, #4721, #4723) and never on
  `physics/`/`ci/`/`docs/`/`fix/`. The two shepherding worlds do not overlap.

**Shepherd registration per round: `unknown` for every row of the §1 census.**
No registration record is accessible from this worktree — the plan-coder
deployment state directory (`~/.plan-coder/state`, `shepherd.json`) is outside
this worktree and was deliberately not read (isolation rule), and no in-repo
file records fluxion PR registrations. The structural argument above (branch
pattern outside `worker/*`, PR not created by the supervisor) says such PRs
*would not* be registered, but that is an inference from the prompts, not a
cited record. Nothing above is invented to fill the column.

## 4. Verdict

**Which publication-side signal is reliable corroboration for the primary
git-facts signal (§3.5 ranking)?** — Exactly one, and only in its narrow form:
**the commit-message OS3 trailer on a head branch the supervisor already knows
(Detector B1: 37 TP, 0 FP, 8 FN in this window)**. Its false-positive count is
**0**, and its 8 false negatives are each attributable to a named cause
(7 rounds published under `ci/`, `docs/` or `fix/` branch families; 1 round with
a body-only trailer). It is therefore usable as a *corroborating* signal, never
as the completion signal itself: it detects **publication**, and §2.3 shows the
publication timestamp lags the real work by up to ~5 days in this window.

**Not reliable:** the repo-wide body search (A1: 29 TP, 0 FP, **16 FN** — the
majority of rounds put the trailer in the commit message only, which GitHub PR
search cannot see); the exact trailer string quoted in issue #34 (A2: **0 TP**,
45 FN — that phrasing exists exactly once in the entire repository, outside the
window); a naive `[AI-assisted]` grep (A3: **2 FP** — §3.3 FP mode 1 confirmed
by #4732 and #4700); and the branch-pattern-only variant (B2: **1 FP** — #4700).
Push-event watching (§3.4) adds nothing here: the OS3 agent's pushes are
invisible to a supervisor that does not already know the branch name, and in
this window no OS3 round pushed to a `worker/*` branch, so a `worker/*`-scoped
push watcher would have scored **0 of 45 rounds**.

**§3 FP/FN modes — observed vs still theoretical**

| §3 mode | status | evidence |
|---|---|---|
| 3.3 FP1 — trailer not unique to the agent | **OBSERVED** | #4732 (`[AI-assisted] (opencode) …`, not a round), #4803 (mentions "OS3 physics PRs #4798/#4800/#4801", a reference) |
| 3.3 FP2 — a re-push re-lists as "new" | **not observed** (mechanism still live) | 0 of the 45 window rounds is OPEN (43 MERGED, 2 CLOSED); but A1 repo-wide returns 175 older rounds, so any repo-wide query re-lists history |
| 3.3 FP3 — a previous round's PR still open and matching | **not observed** (mechanism still live) | same reason: no OPEN round PRs in the window |
| 3.3 FN1 — the agent never commits/pushes | **NOT observed** | all 46 rounds published a PR |
| 3.3 FN2 — trailer in the commit message, not the PR body | **OBSERVED, dominant** | 16 of 45 rounds (see §2.1 A1) |
| 3.3 FN3 — the PR is created by a human/other tool | **not observed for rounds** (but for round-shaped branches) | #4700 `physics/r122-…` — a round-shaped branch with no OS3 trailer |
| 3.3 FN4 — the branch is not `worker/*`, so it is never registered | **OBSERVED, universal** | 45/45 rounds on `physics/`/`ci/`/`docs/`/`fix/` branches (§3) |
| 3.4 FP1 — a push by another actor to the same branch | **not observed** | no `worker/*` OS3 push exists to be confused |
| 3.4 FN1/2 — the agent never pushes / pushes to an unknown branch name | **OBSERVED** | all 45 rounds published to branches outside `worker/*`, ~60 naming families repo-wide |
| 3.4 FN3 — the poll window misses the push | **not directly observable** | but §2.3 (up to ~5-day publication lag vs a 2h supervisor cadence) shows the timing exposure is real |
| 3.5 "publication ≠ completion" | **OBSERVED** | spans of 4d 21h (#4672/#4673) between the first and last branch commit |

**What this slice does NOT settle.** Exit-code (§3.1) and session-file (§3.2)
completion signals remain **UNVERIFIED** pending the desktop probe — nothing in
`anchapin/fluxion`'s GitHub record can observe the agent's process exit status
or its local session files, and this slice deliberately did not touch them.
Issue #10's "two consecutive automatic rounds" criterion remains **open**: this
slice can show that a round *published* (46 rounds, 2 of which published no OS3
trailer and 1 of which published no `physics/`-shaped branch), but it cannot
show that two consecutive rounds ran *automatically* and *completed* — that
requires the exit-code/session-file timeline from probe P1/P3 in §6 of the
investigation. Finally, the register-of-record question stays open: whether the
fluxion-side shepherd (a different shepherd from this repo's) tracks these
rounds is not answerable from the plan-coder worktree, and per §3 the answer
here is `unknown`, not "no".

## 5. Reproduction

The census and the replay are re-runnable from a clean checkout with only `gh`
(read-only) and `python3` (standard library). The helper script used for this
run is `docs/spikes/os3_publication_census.py` (same directory as this file);
it re-fetches the window, re-labels it and prints both tables. Nothing in the
repo's runtime code was changed by this spike.

