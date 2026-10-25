# Operations

The operating policy the prompts implement, in one place. Thresholds shown are the values this system runs with; treat them as starting points, not constants of nature.

## The loop

- A **supervisor** cycle runs every ~2 hours and works up to **6 issues in parallel** across the configured repos (weighted toward the most active repo). A cycle first collects stragglers from previous cycles, then launches new workers so no more than 6 are active.
- A **PR shepherd** runs every **30 minutes** over the tracked worker PRs.
- A **harness-tune** pass runs **weekly**. Plans reset on 5-hour and weekly windows, so idle capacity is waste — the schedule is built to keep slots full without exceeding the human's review bandwidth, which is the real constraint.

## Issue selection

- Prefer well-specified issues with clear acceptance criteria: CI, scripts, tests, refactors, small bugfixes, and substantial issues with testable outcomes. Mix quick wins and substantial work.
- Respect dependency order: foundations before dependents, always.
- Skip: issues with an open PR, issues another worker already holds, secrets/hardware/paid-service work, and exploratory "roadmap" items.
- If a repo has **zero open issues**, flag `BACKLOG EMPTY` — that triggers a project-status review and new issue filing by the manager/human, never by the supervisor inventing work mid-cycle.

## Briefs

Every brief contains: the task, repo context, explicit acceptance criteria, the exact verification commands, and the worktree rules (no commits/pushes/merges, stay in the workdir, never touch `.git`, never move your own worktree).

Every brief also carries a **code discipline** section:

1. **Understand before changing** — read the relevant files first; enumerate *all* occurrences of a flaw with grep before fixing any.
2. **Verify, don't trust** — run the brief's test commands before finishing; fix failures you introduced.
3. **Stale-test protocol** — if a test fails, prove whether it predates your change: `git stash -u`, re-run the failing test, `git stash pop`. A test that passed before and fails after is *your* regression. "Pre-existing failure" is a claim that requires this evidence — asserting it without the check has been wrong often enough that the protocol is mandatory.
4. **Evidence ranking** — the project's own test suite beats new tests written to repo conventions, which beat ad-hoc self-written checks.
5. **No-change is a result, with receipts** — if the task needs no change, quote the exact acceptance/repro commands and their output. An unevidenced "already done" doesn't count (and the worker loop's empty-diff gate enforces this mechanically).

Repo-specific addenda belong in the brief, not in the worker: e.g. if a repo has generated inventories or baseline ratchets, the brief must name the regeneration commands and the gate that checks them.

## Tier routing

| Tier | Use for | z.ai | MiniMax |
|---|---|---|---|
| cheap | small, fully-specified jobs: docs, renames, mechanical test additions | `glm-5.3-flashx` | `MiniMax-M2.5-highspeed` |
| balanced | ordinary backlog issues (default) | `glm-5.3-flash` | `MiniMax-M2.7` |
| flagship | tricky work: deep debugging, refactors, domain-sensitive changes | `glm-5.3` | `MiniMax-M3` |

Record the tier and token totals for every run. If a task balloons past its step cap, stop and report — never burn quota on an unbounded run.

## Review discipline (supervisor, at collect time)

1. Re-read the worker state file first; collect only workers in a collectible status (guards against overlapping cycles).
2. Read `WORKER_REPORT.md`, then verify the **machine manifest** against reality: base commit matches the branch point, `files_changed_git` is non-empty for any code-change brief, gate triggers are sane. An empty diff behind a "done" claim is `failed:empty-diff` unless the brief was verification-only and the worker quoted discriminating evidence.
3. Review the diff yourself against the acceptance criteria. Independently re-run the relevant tests when cheap.
4. Fix only trivial fallout. If the work is bad, mark it failed with a classified note and move on — don't burn more quota polishing a bad diff.
5. Before pushing: fetch, rebase onto the current base (abort on conflict and open the PR flagged, rather than pushing a silently broken branch), delete `WORKER_REPORT.md`, commit with the issue reference, open the PR, and register it with the shepherd.

**Stuck-worker protocol** at the collect deadline — a decision, not just more time:

- **Close to done** → leave it running, refresh its timestamp for the next cycle.
- **Too big for one worker** → stop it, keep the worktree, mark `split_suggested` with a proposed 2–4-way breakdown for the manager to file as sub-issues.
- **Stuck/thrashing** → stop it, keep worktree + transcript for diagnosis, mark `escalated`. Never start a duplicate worker on the same issue.

## Merge policy (shepherd)

The shepherd merges a tracked PR when **all** of these hold:

- mergeable, and merge-state CLEAN — or UNSTABLE only where the red checks are known repo-wide failures already tracked in follow-up issues (name yours here; they must not block merges)
- head branch is `worker/*`
- the shepherd has reviewed the full diff: well-scoped change, clear intent, implementation looks correct
- PR-specific CI is green
- no human reviewer requested changes, and the PR was never flagged as needing the human

Merge with a merge commit (`gh pr merge --merge`) — never `--admin`, never squash/rebase merges. If a merge turns out wrong, revert or fix forward.

**Hard stops — never merge, escalate to the human:**

- changes where the *results* moved in ways that need domain judgment (e.g. simulation/physics outputs)
- anything touching `.github/workflows`, branch protection, or release configuration
- anything the shepherd cannot verify is correct. When in doubt about correctness (not just scope), don't merge.

**CI triage:** flaky/infra failures get one rerun per cycle. Real failures on `worker/*` branches get a bounded worker fix attempt — maximum **2 attempts**, manager-reviewed before pushing — when the fix is clearly mechanical. Everything else escalates.

When the queue drains or a PR merges, the shepherd queues the next supervisor cycle (subject to a cooldown and an idle check) so worker slots refill without waiting for the heartbeat.

## Self-improvement (weekly tune)

- Inputs: the ledger (aggregated with a script — runs, PR-opened rate by tier/repo, median tokens per PR, outcome histogram, failover frequency) and transcript excerpts for the top 2–3 failure classes, plus 1–2 successful runs for contrast.
- **Evidence gate:** every applied change needs evidence from **≥2 distinct worker runs**, cited by run id.
- Small, safe edits are applied directly: wording clarifications, checklist items, tier-routing tweaks, brief-template additions.
- **Flagged, never applied autonomously:** restructures, provider/auth changes, parallelism and scheduling changes.
- External ideas (papers, other harnesses, architecture reviews) enter as *candidate briefs*: each idea is marked small-edit or flag-for-human, checked against the current baseline so existing features aren't re-proposed, and annotated with the outcome once the pass disposes of it.

## Safety rails

- Never run two git-writing workers in one tree. Never trust a stale ref — fetch before branching.
- API keys live in the environment (or your secret store) and reach requests directly; they never appear in files, logs, briefs, or transcripts.
- Workers never see credentials, never leave their worktree, and never hold merge authority. Authority lives in the prompts and policy above — which is why prompt changes are the gated, evidence-backed kind.
