# Architecture

plan-coder has two kinds of participants and three pieces of state. Everything else is prompts.

## Roles

**The manager** is a frontier agent (or a human following the same checklist). It owns every decision with consequences: which issues to work, what the brief says, whether a diff is correct, whether a PR merges. It runs the orchestration prompts — supervisor, shepherd, tuning pass — as scheduled jobs.

**The worker** is a cheap plan model inside `bin/agent.py`. It owns exactly one thing: turning a brief into an uncommitted diff inside one worktree. It has five tools, a step budget, and no git write access to anything that matters — it never commits, pushes, or merges.

The split is the architecture. Workers are interchangeable and disposable; judgment accumulates in the manager's prompts, the ledger, and the merge policy.

## One run, end to end

```
backlog issue
  → supervisor picks it, writes BRIEF.md  (scope + acceptance + test commands
  │                                        + code-discipline section)
  → git worktree add  (isolated tree on worker/<issue>-<slug> branch)
  → agent.py runs the worker loop
  │     · provider chain: MiniMax → z.ai failover on quota/429
  │     · empty-diff gate: "done" with no diff triggers a budgeted
  │       demand for discriminating evidence
  │     · writes WORKER_REPORT.md + harness-generated machine manifest
  │     · appends a truncated JSONL transcript
  → supervisor collects: reads the manifest against the real diff,
  │   re-runs tests, applies the stale-test protocol
  → commit → rebase onto base → push → PR
  → shepherd registers the PR: polls CI, reruns flakes once,
  │   fixes mechanical failures (≤2 attempts), merges when the
  │   merge policy is satisfied, escalates the rest
  → ledger line appended (tier, tokens, outcome class, PR url)
  → weekly tuning pass reads ledger + transcripts, applies
      evidence-gated prompt/routing edits, flags structural ones
```

## State

| File | Shape | Role |
|---|---|---|
| `workers.json` | one JSON object, one entry per worker slot | Live state: status, repo, issue, branch, workdir, PR, tokens. Read at cycle start; a status protocol (working / collecting / done / failed / escalated / split_suggested) keeps overlapping supervisor cycles from double-collecting |
| `worker_ledger.jsonl` | append-only, one line per collected worker | The metrics record and the tuning pass's loss signal: ts, repo, issue, tier, providers used, tokens in/out, steps, outcome class, PR url, gate triggers |
| `shepherd.json` | one JSON object | The shepherd's tracked-PR list, rerun/fix-attempt counts, merged history, and the supervisor-trigger marker |
| `transcripts/<run-id>.jsonl` | one line per worker step, fields truncated | Diagnosis and tuning evidence. Never whole-file reading — grep for the failure, read the surrounding steps |
| `WORKER_REPORT.md` | per run, inside the worktree | Worker summary **plus** the machine manifest. Deleted before commit; the manifest is what the supervisor actually verifies |

Two rules keep the state honest:

1. **The ledger is the recovery source.** It is append-only and written after review, from verified facts. When a state file corrupts or goes stale, rebuild from the ledger and live PR state — don't hand-repair JSON from memory. (This rule exists because it was needed: a state file truncated by a write fault was reconstructed from the ledger with no loss of the record.)
2. **Prose is not evidence.** A worker's summary, a state file's note, and a PR description are all claims. The diff, the manifest, CI, and a fresh test run are the checks.

## Concurrency and isolation

- One git worktree per worker, always. Two git-writing agents in one shared tree destroy each other's uncommitted work on branch switches — this is the system's oldest and most expensive lesson.
- Parallel workers in the same repo take issues in different areas of the codebase so their PRs don't conflict; foundations merge before dependents.
- The shepherd only ever touches `worker/*` branches, never rebases, and caps its own fix attempts. The supervisor never merges at PR-open time (CI hasn't run); merging belongs to the shepherd under policy, or to the human.

## Providers

`agent.py` speaks the OpenAI chat-completions shape to two plan providers:

- **z.ai GLM Coding Plan** — the coding-plan endpoint only; plan keys and pay-as-you-go keys are different products, and only plan quota should ever be spent here.
- **MiniMax token plan** — default priority; fails over to z.ai when quota-limited through polite retries (honoring `Retry-After`). A 401/403 fails fast: it signals a configuration problem, not scarcity, and must not burn the fallback's quota.

Tiers route work to model size: cheap for small fully-specified jobs, balanced as the default, flagship for genuinely hard work (deep debugging, large refactors, domain-sensitive changes). Tier and token totals are recorded per run so credit burn per job stays visible.
