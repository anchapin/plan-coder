# Checklist — Pocock's 5 questions, run over plan-coder's tasks

Source: Matt Pocock, `software-factory` skill, mattpocock/skills PR #1316 (merged 2026-10-31, reviewed 2026-11-13). Alex's tap 2026-11-13: "Draft the 5-question checklist for plan-coder's tasks."

Landed here as the factory's task register (plan-coder issue #25): one row-set per task, answers kept current when a prompt changes a trigger, a cap, or a re-entry point.

Status: **landed — this is the factory's task register; two of the blanks it found are now closed** (the relay exactly-once rule, Task 5 Q5; the merge-time head-SHA re-check, Task 2 Q5 — both written into `prompts/pr-shepherd.md` by issue #25). Everything else below is unchanged from the draft. Answers below were premise-checked 2026-11-13 against the live prompts (`plan-coder-repo/prompts/*.md`), `config/deployment.local.json`, and the recorded incident history — not against anyone's summary. Companion piece, not a duplicate: the TLA+ spike brief (`tla-supervisor-shepherd-spike-brief.md`, drafted same day) models the supervisor/shepherd protocol's interleavings; this checklist is the inventory that tells you *which tasks exist and where their answers are missing* — its blanks are candidate invariants and design questions for that model and for plan-coder #10/#14.

## The checklist (Pocock, verbatim in substance)

For **each task**, answer:

1. What triggers it?
2. What does it do, and which services does it read and write?
3. What does it trigger next?
4. Where does a human re-enter?
5. What stops two runs doing the same work?

For **each ticket source**, answer: what stops it filing the same ticket twice?

A design is finished when every task and ticket source has an answer to every question. Rating key used below: **STATED** = written in a prompt/config, citable · **IMPLICIT** = true in practice, written nowhere as a rule · **BLANK** = no answer exists · **RACE** = an answer exists but is check-then-act prose over shared state another actor can change mid-check.

Services shorthand (Pocock's slots, mapped): tracker = GitHub Issues · git host = GitHub · orchestrator = Muse crons + the prompts themselves · runners = this VM + Alex's desktop runner · observability = state dir (workers.json / shepherd.json), the ledgers, flightdeck.

---

## Task 1 — Backlog supervisor (the implementer)

| Q | Answer | Rating |
|---|---|---|
| 1 Trigger | Cron every 2h; plus a relayed `pending_trigger` from the shepherd (relayed by the main agent via `cron.run` — a cron cannot trigger another cron); plus manual runs. | STATED |
| 2 Does / reads / writes | Reads: tracker (open issues per repo), git host (fresh base fetch), `workers.json`, worker ledger. Writes: briefs, worker runs in per-worker worktrees (≤ `max_parallel` 7; slots per repo from config — fluxion 0/paused, 7 repos + plan-coder at 1), branches + PRs on the git host, `workers.json`, receipt capture records, shepherd registration for each PR opened. Collect deadline 2.5h — cycles overlap by design. | STATED |
| 3 Triggers next | Worker runs; each opened PR triggers the shepherd (via registration); ESCALATION / SPLIT SUGGESTED / BACKLOG EMPTY blocks trigger the main agent → Alex. | STATED |
| 4 Human re-enters | Never mid-run (a run ends; Alex's later action starts a new one — matches Pocock). Re-entry points: escalations, split suggestions, hard stops (physics judgment, workflow files, branch protection), and any brief class Goldfish escalates. | STATED |
| 5 Dedupe | "Never run two workers on the same issue" (steps 3/9) + retry guard: `ledger_check.py` blocks re-picking an issue with ≥2 `failed:*` rows and no later success + collect protocol re-reads `workers.json`, marks `collecting`, saves, so an overlapping cycle skips it. **But:** no claim exists in the tracker itself (no assignee/label lease on the issue), and the collect claim is read→check→write on shared JSON with atomic replace but no compare-and-swap — two overlapping cycles can both pass the check inside the window. | **RACE** |

Caps (Pocock's "cap every loop at a few tries"): 200k tokens-out/run, 750k/cycle, 60 steps/worker, retry guard above. STATED in config.

## Task 2 — PR shepherd (review / merge / rework)

| Q | Answer | Rating |
|---|---|---|
| 1 Trigger | Cron every 30 min. | STATED |
| 2 Does / reads / writes | Reads: `shepherd.json` `tracked_prs`, git host CI state per PR, ledgers. Writes: merges (`gh pr merge --merge` only, under MERGE POLICY), CI reruns, bounded mechanical fix attempts on `worker/*` branches, flags (`NEEDS HUMAN`), `pending_trigger`, shepherd ledger + receipt seals. | STATED |
| 3 Triggers next | A merge (or an empty queue) + cooldown elapsed + no marker already set → writes `pending_trigger` → main agent relays → supervisor run. Flags trigger Alex via the main agent. Fix pushes trigger fresh CI → a later shepherd cycle. | STATED |
| 4 Human re-enters | Every flag; every hard stop (physics, workflows, branch protection, release config); every plan-coder PR (no CI — parks for Alex); every JiT-caught PR (flag only Alex clears); flagcheck's physics-gate/held-green classes resolve only from Alex's word via `flag_resolutions.json`. | STATED |
| 5 Dedupe | One shepherd instance by schedule; `tracked_prs` append is "never duplicate a URL"; `seen_merged` prevents re-processing a merge; rerun ≤1 per PR per cycle, fix attempts ≤ `max_fix_attempts` (2) per PR, both counted in `shepherd.json`. **Gap inside the answer — closed (issue #25):** merge eligibility is checked, then the merge executes, so a head push between the CI check and the merge could invalidate the check; the merge step now re-checks the head SHA at merge time — the merge executes on the SHA whose CI was checked, or the PR is re-triaged next cycle, never merged on a stale check. (This is TLA invariant I5's territory.) | STATED |

## Task 3 — Harness-tune (the retro)

| Q | Answer | Rating |
|---|---|---|
| 1 Trigger | Weekly cron (Sunday). | STATED |
| 2 Does / reads / writes | Reads: every ledger (worker, shepherd, goldfish, jittest, flagcheck, receipts), transcripts, spike Go/No-go windows, public plan-coder issues. Writes: aggregated stats, dispositions in the tune registry (INCORPORATE / QUEUED / FLAGGED), small safe fixes (cap: ≤2 issues or ~30 min), daily-log observations. | STATED |
| 3 Triggers next | Incorporated fixes → PRs → shepherd. Window verdicts → Alex decisions. FLAGGED items → Alex. | STATED |
| 4 Human re-enters | FLAGGED class (scope / structural / security / contested) is Alex's; Go/No-go verdicts on spike windows are reported, enabling is always Alex's separate tap. | STATED |
| 5 Dedupe | The registry disposition is written before the pass finishes, so an issue is not re-triaged from scratch next week; window stats are computed over ledger rows, not re-derived from prose. **Weak spot:** nothing stops tune from re-*proposing* an incorporated-but-unmerged fix, or re-reporting a window verdict, except the registry + memory — and restating a claim does not strengthen it (standing Mnemos rule), so tune must cite, not re-assert. | IMPLICIT |

Pocock's Retro runs throttled at 1-in-10 merges and proposes *factory-instruction changes as PRs*; tune is the weekly, broader version and can also implement. The throttle difference is deliberate (weekly batch suits one operator), not a gap.

## Task 4 — Embedded checks inside other tasks (Goldfish · JiTTest · flagcheck · receipts)

These are sub-tasks: each runs inside a step of Task 1 or 2. Pocock's checklist treats them as tasks anyway, because each has its own trigger and its own failure mode.

| Sub-task | Q1 Trigger | Q2 Does | Q3 Next | Q4 Human | Q5 Dedupe / cap |
|---|---|---|---|---|---|
| **Goldfish** brief-review (supervisor step 4b) | Brief class: flagship / failover re-brief / hard-stop-adjacent / >5 files / no criteria | Zero-context cheap-tier review of the brief; writes `goldfish_ledger.jsonl` | Revised brief → dispatch; escalate → supervisor judgment | Escalation class surfaces to Alex with the flag | **Cap STATED:** ≤2/cycle, 30k tokens-out. **Per-issue dedupe BLANK:** a failover re-brief of the same issue in a later cycle earns a fresh review by design, with no cross-cycle cap — bounded only by the retry guard in Task 1 |
| **JiTTest** catching check (supervisor step 7b; shepherd enforces) | PR opened in step 7, before shepherd registration counts as complete | Hypothesis-first generated test checked for a surviving catch against the PR; catch recorded on the PR's shepherd entry | Catch → PR is NOT merge-eligible, flagged once | Only Alex clears a JiT catch | Latest-row-per-(repo, PR) collapse in tune aggregation prevents double-counting the same catch. STATED |
| **Flagcheck** (shepherd step 4c, ships **disabled**) | Each PR flagged this cycle, new or still open | Deterministic re-verification of whether the flag's condition still holds (8-class taxonomy); appends to `flagcheck_ledger.jsonl` **only on state change** | Resolved-verified flags drop out of Alex's queue | Physics-gate / held-green classes resolve only from Alex's word | Append-only-on-change *is* the dedupe — the cleanest Q5 answer in the whole factory. STATED |
| **Receipt** capture/seal (supervisor step 5b; shepherd seals) | Capture at dispatch; seal at terminal disposition | Records dispatch facts that exist only at launch; seals merge/close outcomes with CI summary | Sealed receipts feed tune's spike-window judging | A merge delayed by the receipt path (one instance) is an automatic spike No-go → Alex | Run-id keyed; capture is once per dispatch by construction. STATED |

## Task 5 — Trigger relay (main agent)

| Q | Answer | Rating |
|---|---|---|
| 1 Trigger | Every shepherd handoff is inspected for `pending_trigger` (standing rule in AGENTS.md). | STATED |
| 2 Does / reads / writes | Verifies supervisor idle (cron status + `workers.json`), calls `cron.run` on the supervisor, clears the marker, preserves `last_trigger`. Exists only because a cron execution cannot call `cron.run` on another cron (no durable chat owner — learned 2026-09-28). | STATED |
| 3 Triggers next | Exactly one supervisor run per marker. | STATED |
| 4 Human re-enters | The relay *is* a human-session actor — that's the workaround, and its soft underbelly: if no live chat session happens, the marker waits (it waited all day 2026-10-23/24 and again until 2026-11-12, harmlessly only because the 2h schedule kept firing anyway). | IMPLICIT |
| 5 Dedupe | Marker is a single slot: shepherd writes it only when none is set; relay clears after firing; `last_trigger` updates only on a successful marker write. Exactly-once *effect* rests on the relay's verify-idle-then-clear sequence being executed by one main-agent session at a time — and it is now written as a rule (issue #25): only the session that verified the supervisor idle clears `pending_trigger`, immediately after `cron.run` returns (`prompts/pr-shepherd.md`, step 4). | STATED |

## Task 6 — External CLI supervision (OS3/OpenCode — plan-coder #10, investigation #14)

This is the task the checklist was drafted for. Its row is mostly blank, and the blanks *are* the spike's design questions:

| Q | Answer | Rating |
|---|---|---|
| 1 Trigger | Today: Alex manually checks OS3 and tells it to start each round. Target: round-completion detection. | **BLANK** (manual) |
| 2 Does / reads / writes | Target: dispatch next brief, ledger + shepherd bookkeeping, enforce hard stops and single-writer. Which state file an external runner's runs register in — `workers.json`? its own ledger? — is undecided. | **BLANK** |
| 3 Triggers next | Next round on completion (target); PRs → shepherd (assumed, unwritten). | IMPLICIT |
| 4 Human re-enters | Hard stops, same classes as Task 1 — assumed to carry over, not yet written for an external runner that doesn't read plan-coder prompts. | IMPLICIT |
| 5 Dedupe | Single-writer enforcement is named in #10 as a requirement with no mechanism yet: no claim, no lease, no worktree discipline an external agent is bound by. Two OS3 rounds (or OS3 + a supervisor worker) on one issue/tree is precisely the collision class that paused the fluxion supervisor slot on 2026-11-13. | **BLANK** |

Spike criterion already on #10 (two consecutive auto-started rounds, or a No-go naming the blocker) is unchanged; this row says Q5 must be answered *before* the first auto-started round, not after.

## Task 7 — Refill / decompose (main agent, Alex-tapped)

| Q | Answer | Rating |
|---|---|---|
| 1 Trigger | Alex's tap ("run refill round N") — a stale duplicate tap was correctly not re-run on 2026-11-12. | STATED (in practice) |
| 2 Does / reads / writes | Reads live base + live issue list at filing time (re-verification is a standing rule); writes batches of issues (round 4: 26, round 5: 29) across the supervisor repos. | STATED |
| 3 Triggers next | New issues enter Task 1's picking pool. | STATED |
| 4 Human re-enters | Alex approves each round; physics-hard-stop issues are flagged at filing (e.g. #4775). | STATED |
| 5 Dedupe | Re-verify against the live issue list at filing time — the duplicate-round tap incident is the proof this works when followed. No automated near-duplicate check on issue text; dedupe is one careful pass by the filing agent. | IMPLICIT |

---

## Ticket sources — the +1 question (what stops it filing the same ticket twice?)

| Source | Dedupe answer | Rating |
|---|---|---|
| Alex, filing directly | Alex himself; main agent checks the live list before filing on his behalf | IMPLICIT |
| Refill rounds (Task 7) | Live-list re-verification at filing time | IMPLICIT |
| Harness-tune triage of public issues | Registry disposition written before the pass ends | STATED |
| Research paper watches (Matchline / Fluxion / Staging Studio) | Deduped against prior reports' seen lists; filing only on Alex's say | STATED |
| **Factory failures themselves** (a supervisor cycle dies, a shepherd run errors, a worker harness crashes) | **Nothing files a ticket.** Failures surface as chat handoffs and daily-log entries; harness-tune reads the wreckage weekly. Pocock's *Fix* task (failed run → ticket + fix PR, never chain-triggers, always human review) has no counterpart here | **BLANK** |
| External monitors / users | None exist for plan-coder (single-operator factory) — a deliberate none, like Pocock's observability "deliberate none" | n/a — deliberate |

---

## Findings — the blanks, ranked

1. **Q5 for external runners (Task 6) is fully blank** — claim/lease/single-writer has no mechanism. This is the only blank that can cause active harm (parallel writers in one tree), it's already named in #10, and the fluxion pause shows the collision is real, not theoretical. Answer it in the #10 spike before any auto-looping.
2. **The supervisor's dedupe (Task 1, Q5) is a RACE, not a mechanism** — prose + check-then-act on `workers.json`, with cycles overlapping by design (2h interval, 2.5h collect deadline). No incident has been traced to it yet, but it's the same shape as the TLA brief's I1/I3. Cheap hardening candidate if ever wanted: a tracker-side claim (assign/label the issue at dispatch, cleared at collection) makes dedupe visible to *every* actor including OS3 — one mechanism answers Task 1 Q5 and Task 6 Q5 at once.
3. **No Fix task (ticket sources, last-but-one row)** — factory failures are the one input with no ticket source. A bounded version fits the existing shape: cron-run failure → auto-drafted issue in plan-coder, capped at one open auto-issue per failure class, never auto-dispatched (human review always — plan-coder PRs already park for Alex). Deliberately *not* Pocock's full version (no auto fix PR) until the class counts justify it.
4. **Merge-time head-SHA re-check (Task 2, Q5)** — small, concrete, already inside TLA I5; if the model clears it, a one-line shepherd step ("re-verify head SHA + checks at merge time") is the whole fix. **Closed (issue #25):** the shepherd merge step now re-verifies the head SHA at merge time — merge on the SHA whose CI was checked, or re-triage next cycle, never a stale check (`prompts/pr-shepherd.md`).
5. **Relay exactly-once (Task 5, Q5) is IMPLICIT** — it holds by session structure. Writing the one sentence down ("only the session that verified idle clears the marker, immediately after `cron.run` returns") costs nothing and is the kind of sentence the TLA sign-off list needs anyway. **Closed (issue #25):** the sentence is written in `prompts/pr-shepherd.md` (step 4), so the Task 5 Q5 rating above is now STATED.
6. **Goldfish has a per-cycle cap but no per-issue cross-cycle cap (Task 4)** — lowest stakes; the retry guard bounds the blast radius. Note it, don't build for it.

**What the checklist confirms is *not* missing:** caps on every loop that exists (fix attempts 2, rerun 1/cycle, Goldfish 2/cycle, token budgets, retry guard), human re-entry points on every task, and runs that never wait for a human. The factory's brakes are in better shape than its claims.

## Adoption path (item 1 taken by issue #25; the rest await Alex's tap)

- [x] This inventory lands in plan-coder `docs/task-register.md` as the factory's task register (issue #25; serves the 2026-11-12 genericization goal: it's the first document a new adopter needs).
- [ ] Finding 1's Q5 answer becomes a required section of the #10/#14 spike report.
- [ ] Findings 2/4 are handed to the TLA spike as already-answered inventory (they map to I1/I3/I5) — no separate work.
- [ ] Finding 3 (Fix-task-lite) filed as a plan-coder issue only on Alex's word.

*House rules applied: every answer checked against the live prompts/config on 2026-11-13; blanks and races reported as findings, not smoothed over; nothing filed, built, or enabled by this draft.*

*Updates since the draft (issue #25): Task 2 Q5 and Task 5 Q5 are now STATED — the shepherd merge step re-checks the head SHA at merge time, and the relay exactly-once rule is written as one sentence in `prompts/pr-shepherd.md` (step 4). Every other answer, rating, and finding is unchanged.*
