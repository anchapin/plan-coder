# plan-coder — Metrics, Optimization Loop & Dashboard v2

**Spec / roadmap doc — APPROVED with decisions, 2026-11-14**
Date: 2026-11-14 · Status: design-first, per the plan-coder #5 precedent.
Decisions D1–D6 were delegated by Alex to the agent on 2026-11-14
("make decisions on my behalf and proceed") and are recorded in §10
with their reasoning; §4, §5.4 and §7 reflect them.
Supersedes/extends: issue #5 (roadmap + dashboard MVP, closed by PR #48),
ROADMAP theme 9 (dashboard, *Partial*), theme 7 (per-plan accounting, *Planned*),
theme 8 (brief-quality feedback loop, *Partial*)

> How to read this: §2 is measured fact about the live harness today.
> §3 is the proposed metric dictionary — every metric names its formula,
> its source fields, and whether it is computable today, approximated
> today, or needs new instrumentation. §4 proposes starter targets as
> *hypotheses*, not commitments. §8 phases the build. §10 lists the
> decisions that are Alex's. Nothing in this doc is built until Alex
> approves the metric definitions (spec-first rule).

---

## 1. Problem and principles

plan-coder already records a lot: a worker ledger (588 rows), a shepherd
ledger (1,808 rows), JiT and Goldfish ledgers, grades, transcripts. What
it does not have is (a) an agreed set of metrics with exact definitions,
(b) targets those metrics are steered against, (c) a closed loop that
turns a metric movement into a keep/revert decision on a harness change,
and (d) a dashboard that shows live state *and* trends — the MVP
dashboard (PR #48) shows live state and current PR metrics, but its
Panel 3 (quality trends over time) was explicitly not built.

Five principles govern everything below:

1. **Flat-rate economics.** The plans (z.ai GLM, MiniMax) are sunk-cost,
   resetting on 5-hour and weekly windows. The goal is therefore *not*
   "spend fewer tokens" — unused quota is wasted quota. The goal is
   **more merged, acceptance-clean PRs per plan window**, with tokens
   per merged PR as the efficiency lever that buys headroom for more
   runs inside the same window.
2. **No single-number targets (Goodhart guard).** Every optimization
   target is paired with guardrails that must not regress. Cutting
   tokens per PR by briefing vaguer issues is a failure, not a win, if
   the merge rate or acceptance rate falls with it.
3. **Distinct entities, never raw rows.** Shepherd ledger rows are
   polling events, not PRs. Live example (§2): `needs_alex` has 833
   rows but only 64 distinct PRs. Every PR-level metric counts distinct
   PRs; every run-level metric counts distinct runs.
4. **Stratify or lie.** Aggregates hide the signal. Every metric is
   reported overall *and* split by repo, tier, and provider/model.
   A change that helps staging-studio and hurts fluxion must be
   visible as exactly that.
5. **Presentation layer, same semantics.** Inherited from #5 and not
   up for redesign: the dashboard is read-only over the state dir,
   local-only, no credentials, no hosted service, and wherever a CLI
   (`trace_summary.py`, `shepherd_ledger.py summary`, `grade_run.py`)
   already defines a number, the dashboard shows that number.

---

## 2. Baseline snapshot — measured 2026-11-14 from the live state dir

State dir: `~/workspace/goals/token-plan-coding-manager-setup/hidden_files/`
Method: direct reads of `worker_ledger.jsonl`, `shepherd_ledger.jsonl`,
`workers.json`, `shepherd.json`; join on `pr_url`. Reproducible with
`bin/trace_summary.py --json` + `bin/shepherd_ledger.py summary` plus a
join script (that join script *is* Phase 0, §8).

| Fact | Value | Caveat |
|---|---|---|
| Worker runs recorded | 588 (2026-09-28 → 2026-11-14) | Early rows have null tokens / `steps: "unknown"` |
| PR-opened rate, all time | 432/588 = **73.5%** | Since Nov 1: 315 runs, **75.6%** |
| tokens_out per run | median **27.4k**, total 21.8M out | n=560 (28 rows lack tokens) |
| tokens_out per run, since Nov 1 | median **30.0k**, p90 **90.2k** | p90 is the burn problem, not the median |
| Distinct PRs merged (shepherd) | **193** | Shepherd ledger only starts 2026-11-09 (snapshot row); nothing before that is countable |
| Merged PRs joinable to a worker run | 173/193 (90%) | The other 20 were not worker-opened (Alex/OS3/pre-ledger) — the join must tolerate this, not force it |
| **tokens_out per merged PR** (joined, n=173) | median **29.7k**, mean **39.1k** | First time this number has been computed; it is the north-star denominator |
| Shepherd rows per merged PR | median 2, mean 7.3, **max 218** | The tail is polling/flag noise — medians only, and a "touches" definition (§3) is needed |
| Top worker failure classes | worker-error 28, empty-diff 21, bad-diff 16, worker-incomplete 6, step-cap 5 | Plus 6 `stale`, 5 `escalated` — outcome vocabulary needs freezing (§5) |
| Grade coverage | 247/588 rows (**42%**) | Grading started mid-stream; trend lines must start where coverage starts |
| Workers in flight at snapshot | 3 `working` of 443 cumulative entries | `workers.json` is cumulative history, not a slot table — utilization cannot be read off it directly (§5) |
| Currently tracked / Needs-Alex | 21 tracked PRs, 5 Needs-Alex | From `shepherd.json` at 19:22 EST |

**What this already tells us:** the median run is cheap and usually
works; the losses concentrate in (i) the p90 token tail, (ii) a small
set of repeat-failure classes, and (iii) shepherd-side dwell (PRs
polled for days). The metrics below are chosen to keep those three
loss pools visible and shrinking.

---

## 3. Metric dictionary

Status key: **● computable today** from existing ledgers ·
**◐ approximable today** (definition works, data is lossy — caveat
named) · **○ needs instrumentation** (§5 names the field to add).

### 3.0 North star

| Metric | Definition | Source | Status |
|---|---|---|---|
| **Merged acceptance-clean PRs per week** (and per plan window, once §5 plan ids land) | Count of distinct PRs with a shepherd `merged*` action in the window whose acceptance status (§3.2) is *clean* | shepherd_ledger ⨝ worker_ledger on `pr_url` | ◐ — merge half is exact since 2026-11-09; "acceptance-clean" is ○ until §5 |

### 3.1 Efficiency (cost per unit of merged work)

| Metric | Definition | Source | Status |
|---|---|---|---|
| tokens_out per merged PR | median + p90 of `tokens_out` over worker runs whose PR merged | join | ● (baseline: 29.7k / —) |
| tokens_out per PR opened | as in `trace_summary.py` (existing semantics, kept) | worker_ledger | ● |
| Wasted-token share | tokens_out on runs with `failed:*` / `stale` / escalated outcomes + repeat attempts on an issue beyond the first, ÷ total tokens_out | worker_ledger | ● |
| Repeat-attempt burn | tokens_out summed per (repo, issue) with ≥2 runs, split first-attempt vs retries | worker_ledger | ● |
| Steps per merged PR; hit-cap rate | `steps` used/max over joined runs; share hitting max | worker_ledger | ● (early rows excluded) |
| First-edit step; exploration ratio | from `grade_run.py` signals, median per stratum | ledger `grade` + transcripts | ◐ (42% grade coverage) |
| Shepherd touches per merged PR | distinct shepherd *action-days* per PR (not raw rows — kills the 218-row polling artifact) | shepherd_ledger | ◐ — definition new, data exists |
| Fix attempts / reruns per merged PR | distinct PRs with `fix_attempt` / `rerun` ÷ merged PRs (existing shepherd summary counts) | shepherd_ledger | ● |

### 3.2 Effectiveness (did the PR meet its issue?)

| Metric | Definition | Source | Status |
|---|---|---|---|
| PR-opened rate | `pr_opened` ÷ runs (`trace_summary` semantics) | worker_ledger | ● 73.5% |
| Merge rate | distinct merged PRs ÷ distinct PRs opened, cohort by open week (a PR counts in the week it opened, whenever it merges) | join | ◐ — cohorts before 2026-11-09 unmeasurable |
| First-pass CI green rate | PRs whose first recorded shepherd CI state is green ÷ PRs with any CI record | shepherd_ledger `ci` | ◐ — `ci` is free-text today; needs the §5 vocabulary freeze |
| Merged without human flag | merged PRs never appearing in `needs_alex` ÷ merged PRs | shepherd_ledger | ● |
| **Acceptance-clean rate** | merged PRs where, at collection: brief acceptance checklist verified + no unresolved JiT catch + no Goldfish blocker + flagcheck (when enabled) not failing, ÷ merged PRs | new field (§5) | ○ |
| JiT catch rate / disposition mix | catches per checked PR; Alex dispositions (fix-forward / park / dismiss) | jittest_ledger | ● (32 rows — small n, report counts not just %) |
| Goldfish disposition mix | propose-fix / contest / escalate / decline per review; brief-changed rate | goldfish_ledger | ● (67 rows) |
| Grade distribution | median + quartiles of `grade`, per repo/tier, over time | worker_ledger | ◐ (coverage) |
| Escape rate | merged PRs followed within 7d by a revert, a fix-forward PR referencing them, or a new issue citing them, ÷ merged PRs | shepherd_ledger + `gh` | ○ — needs a lightweight link convention (§5) |

### 3.3 Throughput & utilization (the downtime question)

| Metric | Definition | Source | Status |
|---|---|---|---|
| PRs opened / merged per day | distinct counts per calendar day (ET) | both ledgers | ● |
| Supervisor cycle throughput | PRs opened per supervisor cycle (~2h) | worker_ledger `ts` bucketed 2h (dashboard MVP's existing approximation, labelled as such) | ◐ → ● with `cycle_id` (§5) |
| **Slot occupancy** | worker-busy time ÷ (slots × elapsed time), per cycle; busy = `started_at` → collection | workers.json `started_at` + ledger `ts` | ◐ — `started_at` exists on 375/443 entries; no per-run end timestamp on the worker side, ledger `ts` is the proxy |
| Queue wait | issue selected → worker `started_at` (median) | — | ○ needs a `briefed_at` stamp (§5) |
| Stage dwell times | median time in each stage: briefed → working → PR opened → first CI → merged / flagged | join + §5 stamps | ◐ (PR-opened → merged is ●; earlier stages ○) |
| Shepherd latency | PR opened → first shepherd action; PR green → merged | shepherd_ledger | ● |
| Needs-Alex dwell | time a PR spends flagged before Alex's decision; queue age list | shepherd_ledger + shepherd.json | ● (first `needs_alex` row → resolving action) |
| Plan-window utilization | useful tokens_out (runs ending in a merged PR) ÷ total tokens_out in each 5h/weekly window; plus raw burn vs window | worker_ledger + §5 plan windows | ◐ → ● with `plan_id` (ROADMAP theme 7) |
| Idle-reason mix | share of non-busy slot time by reason: `no_backlog`, `quota_exhausted`, `blocked_shepherd`, `blocked_needs_alex`, `blocked_ci`, `paused_by_alex` (e.g. fluxion slot) | — | ○ new `idle_reason` recorded per cycle (§5) |

### 3.4 Reliability / health

| Metric | Definition | Source | Status |
|---|---|---|---|
| Failure-class mix | share of runs per `failed:*` class, over time (stacked) | worker_ledger | ● |
| Empty-diff / mid-run-no-edit gate rate | gate triggers ÷ runs (`trace_summary` machine gates) | worker_ledger `gate_triggers` | ● |
| Provider failover rate; 429 rate | runs with >1 provider in `providers_used` ÷ runs; failover ledger where present | worker_ledger | ● |
| Stale / conflict rate | PRs hitting `merge_conflicts` or closed unmerged ÷ opened PRs | shepherd_ledger | ● |
| Ledger health | grade coverage %, null-token %, shepherd/worker row rates — *the metrics about the metrics* | all ledgers | ● |

---

## 4. Starter targets — hypotheses to confirm after a 2-week baseline

Targets are set **relative to the §2 baseline**, reviewed at each
weekly harness-tune, and never traded against their guardrails.
**Decided (D2, §10):** these numbers are directional until 2 weeks
of clean rollup data land, then they lock; the repeat-failure row
is firm from 2026-11-14.

| Lever target (4-week horizon) | Baseline | Proposed | Guardrails (must not regress) |
|---|---|---|---|
| tokens_out per merged PR, median | 29.7k | **−15%** (≈25k) | merge rate, acceptance-clean rate |
| tokens_out per run, p90 | 90.2k | **−25%** (≈68k) | PR-opened rate |
| Wasted-token share | to be computed in Phase 0 | **−5 pts** | — |
| Merge rate (cohort) | Phase 0 computes | **+5 pts** | escape rate, Needs-Alex dwell |
| Slot occupancy (active windows) | Phase 0 computes | **≥70%** | tokens per merged PR (no busywork burn) |
| Median PR-opened → merged | Phase 0 computes | **<4h** for unflagged PRs | first-pass green rate |
| Repeat-failure issues (≥2 fails, no PR) | `ledger_check` list | **0 older than 7d** without a disposition | — |

First candidate experiments (each run through the §6 loop):
Context Mode A/B (already live as PR #53's gate — it becomes
Experiment 001 retroactively), p90-tail brief rule (hard "stop
measuring by step ~30" enforcement), tier-routing change for the
`worker-incomplete` class, shepherd polling cadence vs touches.

---

## 5. Instrumentation gaps — the only new data this spec asks for

All additions are **additive fields on existing ledgers** or stamps
on existing state writes — no new storage system, per principle 5.
Each is small; together they turn ◐/○ metrics into ●.

1. **`cycle_id`** on worker and shepherd rows (supervisor cycle
   timestamp, e.g. `2026-11-14T19:37Z`). Kills the 2h-bucketing
   approximation everywhere.
2. **`plan_id`** on worker rows (which subscription plan paid).
   Already specced as ROADMAP theme 7; this spec depends on it for
   per-window utilization.
3. **Stage stamps copied into the worker ledger row at collection:**
   `briefed_at`, `started_at` (already in `workers.json`), `pr_opened_at`
   (= ledger `ts` today). Enables queue-wait and dwell metrics.
4. **`acceptance_clean` (bool) + `acceptance_evidence` (short string)
   + `acceptance_checks_run` (list)** on the worker row, set by the
   supervisor at collection. **v1 semantics (decided, D3 in §10):**
   checklist verified **and** no unresolved JiT/Goldfish/flagcheck
   finding *among the checks that ran*; `acceptance_checks_run`
   records which those were, so the rate is always read next to its
   coverage. This is the single most important new field: without
   it "merged" is the only quality signal, and it is too weak.
5. **`idle_reason`** recorded once per supervisor cycle when slots
   go unfilled (vocabulary in §3.3), plus `slots_configured` and
   `slots_launched` — three integers/enums per cycle, in a new
   `cycle_ledger.jsonl` (one row per supervisor + shepherd cycle:
   also carries quota snapshot — plan window start, tokens burned
   in window so far — which no current file records).
6. **Vocabulary freezes:** worker `outcome` values and shepherd `ci`
   values become closed enums (current free-text is why first-pass
   green is ◐). Freeze = list them in `docs/OPERATIONS.md`, lint in
   `ledger_check.py`.
7. **Escape link convention:** a fix-forward/revert PR body line
   `Fixes-forward: <pr_url>`; the rollup (§7) greps for it. Cheap,
   no schema change.
8. **Worker heartbeat for the live panel:** the dashboard reads the
   run's transcript file mtime + last step number (files already
   exist in the transcripts dir) — no writer change, just a reader
   convention, documented so transcript layout doesn't break it
   silently.

**Rollup (the one new derived file):** `metrics_rollup.jsonl` in the
state dir — one row per PR lifecycle (joined worker+shepherd view:
opened, merged, tokens, touches, dwell per stage, acceptance,
escape flag) and one row per day per stratum (the §3 aggregates).
Generated by a new `bin/metrics_rollup.py`, read-only over ledgers,
re-runnable from scratch at any time (ledgers remain the truth;
the rollup is a cache, exactly like `FLIGHT_DECK.md`).

---

## 6. The optimization loop — harness-tune, formalized

The weekly harness-tune already consumes these ledgers; this spec
gives it a registry and a verdict rule so changes stop being
anecdotal.

**Experiment registry:** `experiments.jsonl` in the state dir, one
row per change: `id`, `date`, `hypothesis` (one sentence, names the
primary metric + expected direction/size), `change` (what was edited:
prompt file, routing rule, config knob — with commit/PR link),
`window` (start, min sample: ≥20 runs or ≥2 weeks, whichever later),
`primary_metric`, `guardrails[]`, `verdict` (`keep` / `revert` /
`inconclusive`), `verdict_evidence` (the stratified numbers).

**Rules:**
- One primary metric per experiment; guardrails from §4's table.
- Verdict compares the window against the immediately preceding
  equal-length window, **stratified** — an aggregate win with a
  per-repo loss is `inconclusive` until explained.
- The existing harness-tune evidence bar (≥2 distinct runs behind
  any prompt edit) stays; this adds the *outcome* bar on top.
- Structural changes (new gates, slot counts, merge policy) still
  stop for Alex — the loop may *propose* them with evidence, never
  apply them. Same authority boundary as today.
- The registry is rendered on the dashboard (§7, Panel 5), so Alex
  sees what was tried, not just what the numbers did.

Worked example — Experiment 001 (retroactive): *Context Mode
(bin/agent.py, PR #53) cuts tokens_in on tool-heavy runs ≥26% in
replay; hypothesis: tokens per merged PR −10% on tool-heavy repos
within 2 weeks of merge, guardrail: PR-opened rate and grade
median unchanged.* Verdict pending the live A/B Alex already gated.

---

## 7. Dashboard v2 — panel spec

Inherits all five #5 constraints (read-only, no new storage beyond
the §5 rollup cache, local-only, CLI-identical semantics, graceful
on missing files). MVP Panels 1–2 stay; this adds the rest.

| Panel | Answers | Key content | Refresh |
|---|---|---|---|
| **0 — Pulse header** (new) | "Is it working *right now*?" | Supervisor/shepherd last-cycle time + next expected; workers in flight (repo/issue, elapsed from `started_at`, last transcript step via §5.8, tier/provider); plan-window burn bars with reset countdowns; Needs-Alex count + oldest age | 60s (MVP cadence) |
| **1 — Live view** (MVP, kept) | "What's in flight / just finished?" | As PR #48 | 60s |
| **2 — PR metrics** (MVP, extended) | "How are PRs moving?" | MVP content + cohort merge rate, first-pass green, touches/merges using *distinct* counts (§1.3) | on render |
| **3 — Trends** (the unbuilt Panel 3, realized) | "Are we getting better?" | Small multiples, 7d/30d/90d: PRs/day, tokens per merged PR (median+p90 band), merge rate, wasted-token share, slot occupancy, failure-class stacked bars, grade quartiles. **Change annotations:** vertical markers from `experiments.jsonl` + prompt/config commits | daily rollup |
| **4 — Funnel & dwell** (new) | "Where does work wait?" | Stage funnel with conversion % and median dwell per stage (§3.3); Needs-Alex queue with per-PR age; longest-dwell PRs list | on render |
| **5 — Experiments** (new) | "What did we try, did it work?" | Registry table: hypothesis, window, primary metric Δ, guardrails, verdict | on render |
| **6 — Drill-down** (new) | "Which repo/tier is dragging?" | §3 tables per repo × tier × provider; click a PR → its joined lifecycle row (rollup) + links to transcript/grade | on render |

**Build choice (decided, D1 in §10): Option A** — static
self-contained HTML, regenerated at each supervisor/shepherd
cycle end plus an on-demand render command. Option B (tiny local
server) is the documented fallback if A feels stale in practice;
the render code must stay a pure function of the state dir so B
can wrap it without a rewrite.

Either way: single file, no CDN/network, no `gh` calls, no
credentials — the MVP's guarantees are not renegotiated.

---

## 8. Phasing & acceptance criteria

**Phase 0 — Measurement foundation** (no dashboard change, no targets yet)
- [ ] `bin/metrics_rollup.py`: joins ledgers → PR-lifecycle + daily
      rollup rows; handles the 20/193 unjoinable PRs and pre-2026-11-09
      shepherd gap explicitly (rows flagged, never silently dropped)
- [ ] Baseline report regenerated from the rollup, matching §2's
      hand-computed numbers within rounding (29.7k median tokens per
      merged PR is the checksum)
- [ ] §5 items 1–6 filed as briefs (ledger fields + vocabulary freeze
      + `cycle_ledger.jsonl`); item 7–8 as conventions in OPERATIONS.md
- [ ] 2 weeks of clean rollup data accumulate → §4 targets confirmed
      or adjusted by Alex

**Phase 1 — Dashboard v2**
- [ ] Panels 0, 3, 4 built (A or B per D1); Panels 1–2 numbers
      byte-identical to CLI outputs on the same state dir (MVP test
      discipline, extended)
- [ ] Panel 5 renders the registry (even if it holds only Exp. 001)
- [ ] Smoke: renders against `examples/` fixtures + the real state
      dir; missing-ledger degradation tested like the MVP's

**Phase 2 — Optimization loop live**
- [ ] `experiments.jsonl` + harness-tune prompt updated to open/
      close experiments under §6 rules (prompt edit = Alex's tap,
      as with all live prompt changes)
- [ ] First two experiments run to verdict (Context Mode + one
      p90-tail intervention)
- [ ] Monthly one-page metrics review lands in chat **only when it
      carries a decision** (D6, §10): an experiment verdict, a
      target lock/change, or a guardrail breach. Otherwise silence;
      everything else stays dashboard-on-demand (monitoring
      contract: quiet unless a decision is needed)

---

## 9. Non-goals

- No hosted/multi-user dashboard, no auth, no cloud anything.
- No real-time streaming infrastructure; 60s–5min freshness is fine
  for a 2h-cycle harness.
- No per-token *dollar* costing — plans are flat-rate; dollars enter
  only if a pay-as-you-go provider is ever added.
- No worker-level surveillance beyond what transcripts already hold;
  metrics are about the *system*, stratified by repo/tier/model —
  not leaderboards of individual runs beyond the existing top-burn
  list in `trace_summary.py`.
- No auto-tuning: the loop proposes and (for prompt/routing tweaks
  within existing harness-tune authority) applies with evidence;
  it never changes its own targets, slots, or merge policy.

---

## 10. Decisions — made on Alex's behalf, 2026-11-14

Alex delegated these ("rubber duck review the decisions, make
decisions on my behalf and proceed"). Each verdict includes the
counter-argument that was considered; Alex can reverse any of them
with a word, and the reversal updates this section.

- **D1 — Dashboard build: Option A, static regen.** Regenerate the
  single self-contained HTML at each supervisor and shepherd cycle
  end, plus an on-demand render command. *Why not the server:*
  a long-lived local process is one more thing that must survive
  VM replacement and be supervised — the exact failure class this
  harness keeps hitting — for a benefit (true 60s freshness,
  interactive filters) a 2h-cycle system does not need. The render
  stays a pure function of the state dir, so Option B remains a
  thin wrapper if A ever feels stale in practice.
- **D2 — Targets: direction confirmed, numbers provisional.** §4's
  targets steer Phase 0–1 work as *directional* goals but are not
  gates and lock only after 2 weeks of clean rollup data, because
  the §2 baseline is polluted by early null-token rows and the
  pre-2026-11-09 shepherd gap. One exception, firm from today:
  **zero repeat-failure issues (≥2 fails, no PR) older than 7 days
  without a disposition** — that is already the spirit of
  `ledger_check.py`, just given a clock.
- **D3 — Acceptance-clean: tiered (revises §5.4 as originally
  drafted).** The strict all-checks definition is *rejected for
  v1*: JiT and Goldfish are triggered checks, not universal ones
  (Goldfish ships disabled in the example config), and flagcheck
  is disabled — requiring them would measure check *coverage*,
  not PR quality, and would brand most good PRs unclean for
  never having been checked. **v1 definition:** brief acceptance
  checklist verified at collection **and** no unresolved JiT
  catch, Goldfish blocker, or flagcheck failure *among the checks
  that actually ran* — plus a mandatory `acceptance_checks_run`
  list on the row so every acceptance rate can be read next to
  its coverage. The strict definition returns automatically when
  those checks run on ≥90% of PRs.
- **D4 — Utilization scope: supervisor workers only in v1.**
  OS3/desktop sessions have no ledger rows or start stamps today;
  folding them in would corrupt the occupancy number. The
  `cycle_ledger` schema carries a `source` field from day one so
  external sessions can be admitted as their own stratum when
  #10 lands — never blended into the supervisor number.
- **D5 — This spec lives in the repo.** Filed as
  `docs/METRICS.md` plus a ROADMAP theme-9 pointer, via a
  docs-only PR. *Why not standalone:* standalone docs drift and
  die in chat compaction (the reason `flightdeck.py` exists);
  Phase 0 workers will be briefed from the repo copy, so the
  repo copy must be the authority. This document is that
  repo copy (first filed 2026-11-14); the pre-filing original
  lives outside the repo with the operator.
- **D6 — Review cadence: monthly, exception-led.** A monthly
  one-page review lands in chat **only** when it carries a
  decision: an experiment verdict, a target lock/change, or a
  guardrail breach. Otherwise silence and the dashboard is the
  record — consistent with Alex's monitoring contract (quiet
  loops; ping for decisions, not for status).

---

## Appendix — source map

| Data | File (state dir) | Aggregator today |
|---|---|---|
| Worker runs | `worker_ledger.jsonl` (588 rows) | `bin/trace_summary.py` |
| Shepherd actions | `shepherd_ledger.jsonl` (1,808 rows, from 2026-11-09) | `bin/shepherd_ledger.py summary` |
| Live workers | `workers.json` (443 cumulative entries) | `bin/flightdeck.py`, `bin/dashboard.py` |
| Live PR tracking | `shepherd.json` (21 tracked, 5 Needs-Alex) | `bin/flightdeck.py` |
| Brief quality | `goldfish_ledger.jsonl` (67 rows) | harness-tune |
| Test catches | `jittest_ledger.jsonl` (32 rows) | harness-tune |
| Grades | `grade` field on worker rows (247/588) | `bin/grade_run.py` |
| New in this spec | `metrics_rollup.jsonl`, `cycle_ledger.jsonl`, `experiments.jsonl` | `bin/metrics_rollup.py` (Phase 0) |

*Baseline numbers in §2 were computed directly from the live ledgers
on 2026-11-14 (~19:40 EST) for this spec; they will drift as the loop
runs — Phase 0's rollup is what keeps them current.*
