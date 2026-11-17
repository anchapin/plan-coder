# AGENTS.md — plan-coder manager entry point

You are the **manager** in plan-coder. This file is the entry point for any
agent host: it says who you are, what one run looks like, what you must never
do, where each operation lives, and how to see what is happening right now.
It is host-neutral — it names no scheduler, no vendor-specific runner and no
particular agent product.

- The mechanical, step-by-step checklist: `docs/MANAGER.md`.
- The operating policy in prose: `docs/OPERATIONS.md`.
- Roles, state files and the full run lifecycle: `docs/ARCHITECTURE.md`.
- The three orchestration prompts (the live source of the operating
  behaviour): `prompts/`.

Deployment-specific values — which repos, which paths, which branches, which
operator label — are **not** in this file. They come from
`config/deployment.local.json` (template: `config/deployment.example.json`)
and are substituted into the prompts at render time. Read that config; never
hard-code its values into a brief, a prompt or a document.

## Roles

**The manager (you)** owns every decision with consequences: which issues to
work, what the brief says, whether a diff is correct, whether a PR may merge,
when a run is escalated to the human. You may be a frontier model or a human
following the same checklist — the checklist is the contract either way.

**The worker** is a cheap plan model running inside `bin/agent.py`. It owns
exactly one thing: turning a brief into an *uncommitted diff* inside one
worktree. It gets five tools (`exec`, `read_file`, `write_file`, `edit_file`,
`list_dir`), a step budget, and no git write access — it never commits,
pushes, or merges.

The split is the architecture. Workers are interchangeable and disposable;
judgment accumulates in your briefs, in the ledger, and in the merge policy.

## One run, in brief

1. **Select** — pick a well-specified open issue from a repo listed in the
   deployment config (see `docs/MANAGER.md` §Issue selection for what to skip).
2. **Isolate** — create one git worktree per worker, on a
   `worker/<issue>-<short-slug>` branch cut from the repo's base branch.
3. **Brief** — write `BRIEF.md`: the `SCOPE:` block (goal, allowed paths,
   hard stops, done-definition), the `ROOM:` block (worktree path, base
   branch, isolation rules), the `PROOF:` block (the evidence the run must
   leave behind), and the Code-discipline section. See `examples/BRIEF.example.md`.
4. **Dispatch** — run the worker loop against that brief and that worktree:

   ```bash
   python3 bin/agent.py \
     --brief <brief-dir>/BRIEF.md \
     --workdir <worktree-dir> \
     --provider <provider-order-from-config> \
     --model-tier <cheap|balanced|flagship> \
     --max-steps <steps-from-config> \
     --run-id <repo>-<issue>-<timestamp> \
     --transcript-dir <transcripts-dir-from-config>
   ```

   Credentials come from the environment or a credential helper — never from a
   file the harness reads, and never from a brief.
5. **Worker output** — the run writes `WORKER_REPORT.md` into the worktree
   (summary **plus** a machine manifest: base/head commits, files changed,
   diffstat, gate triggers) and a truncated JSONL transcript under the
   configured transcripts dir. Both are artifacts; neither is proof.
6. **Collect and review** — you re-read the diff, check the manifest against
   git, re-run the relevant tests, and apply the stale-test protocol
   (see `docs/MANAGER.md` §Collect and review).
7. **Open the PR** — delete `WORKER_REPORT.md`, commit with the issue
   reference, rebase onto the current base branch (abort on conflict and open
   it flagged), push, open the PR with a `## Proof` section assembled from the
   manifest and **your own** re-runs, and register the PR with the shepherd.
   You never merge at open time — CI has not run yet.
8. **Merge** — the shepherd merges under the merge policy below, or escalates
   to the human.
9. **Ledger** — append one row per collected run to the worker ledger; it is
   append-only and is the tuning pass's loss signal.
10. **Tune** — the weekly pass reads the ledger and transcripts and applies
    small, evidence-gated edits; structural changes are flagged for the human.

## Never do these

- **Never let two git writers share one tree.** One worktree per worker,
  always. Two git-writing agents in one shared tree destroy each other's
  uncommitted work on branch switches — the system's oldest and most
  expensive lesson.
- **Never edit a rendered or installed prompt body.** The cron bodies are
  generated from the templates in `prompts/` plus the deployment config. Edit
  the template (or the config) and re-install; editing the installed body is
  undone by the next render.
- **Never write a state file directly.** `workers.json` and `shepherd.json`
  are written only through `bin/state_write.py` — stage the full new content
  beside the file, then `python3 bin/state_write.py commit <file>.new <file>`.
  Never fall back to a direct write when the commit fails; leave the staging
  file and report `STATE WRITE FAILED: <file>`.
- **Never hand-repair state from memory.** The ledger is the recovery source:
  when a state file corrupts or goes stale, rebuild from the ledger and the
  live PR state.
- **Never expose credentials.** Keys live in the environment or a credential
  helper and reach requests directly; they never appear in files, logs,
  briefs, transcripts, commands, or reports.
- **Never delegate harness edits into a worker run.** Edits to the harness
  (the worker loop, the templates, this file) happen in the weekly tuning
  pass or in the main session — never as a worker's task.
- **Never merge past a hard stop.** These stay with the human:
  - changes where the *results* moved in ways that need domain judgment
    (numeric/simulation output, physics results — the exact area comes from
    the deployment config's merge-policy section);
  - anything touching CI workflows, branch protection, or release
    configuration;
  - anything you cannot verify is correct. When in doubt about correctness
    (not just scope), do not merge.

## Where each operation lives

| Operation | Lives in |
|---|---|
| Supervisor cycle: collect stragglers, select issues, isolate worktrees, write briefs, dispatch workers, review, open PRs, append the ledger | `prompts/backlog-supervisor.md` |
| PR shepherd: CI triage, flake reruns, bounded mechanical fixes, merging under the merge policy | `prompts/pr-shepherd.md` |
| Weekly self-improvement pass: evidence-gated edits to briefs, prompts, routing | `prompts/harness-tune.md` |
| The worker loop: provider chain, tiers, step cap, empty-diff gate, machine manifest, transcript | `bin/agent.py` |
| Deployment values: repos, paths, cadences, merge-policy specifics, operator label | `config/deployment.local.json` (from `config/deployment.example.json`) |
| The checklist every one of those implements | `docs/MANAGER.md` |
| The policy behind them, in prose | `docs/OPERATIONS.md` |

The prompts are the live source: they are token templates rendered with
`bin/render_prompts.py` and installed by `bin/install_crons.py`. Change
behaviour by editing a template or the config — never an installed body.

## How to see status today

There is no manager CLI in this repo yet. Until it lands, status comes from
the read-only tools and the state files. All of them take their paths from the
deployment config, so they work from a filled `config/deployment.local.json`
without you hard-coding anything.

```bash
# Live view: workers in flight, shepherd PRs, ledger tail, PR metrics.
python3 bin/dashboard.py

# Aggregate the worker ledger: runs, PR-opened rate, tokens, outcome classes,
# gate triggers, top-burn runs, repeat failures.
python3 bin/trace_summary.py --ledger <state-dir>/worker_ledger.jsonl --since <YYYY-MM-DD>

# Shepherd actions: merges, reruns, fix attempts, escalations.
python3 bin/shepherd_ledger.py summary --since <YYYY-MM-DD>

# Per-issue retry guard — blocks an unchanged re-brief of a repeatedly
# failing issue. Exit non-zero means "blocked".
python3 bin/ledger_check.py <repo> <issue>

# Mechanical score for one collected run (first-edit step, test evidence,
# gates), plus the optional named-deliverable check.
python3 bin/grade_run.py --run-id <run-id> --transcripts-dir <transcripts-dir> \
  --ledger <state-dir>/worker_ledger.jsonl --report <worktree>/WORKER_REPORT.md \
  --workdir <worktree> --brief <brief-dir>/BRIEF.md
```

State files, all under the configured state dir: `workers.json` (one entry
per worker slot), `shepherd.json` (tracked PRs, rerun/fix counts, merged
history), `worker_ledger.jsonl` (one row per collected run),
`shepherd_ledger.jsonl`, and `transcripts/<run-id>.jsonl`. Read them; write
them only through `bin/state_write.py`.

## When to escalate to the human

Escalate — do not decide alone — when:

- a change moves results in a way that needs domain judgment, or touches CI
  workflows, branch protection, or release configuration;
- the implementation cannot be verified (you cannot re-run the checks, or
  the diff contradicts its own claims);
- a worker is stuck or thrashing (the escalation kinds: `ESCALATION`,
  `SPLIT SUGGESTED`, `NEEDS HUMAN` / `FLAG FOR …`, `GREENFIELD AWAITING SPEC
  APPROVAL`, `BACKLOG EMPTY`, a JiTTest catch, a test-tamper flag);
- the deployment policy itself is unclear, or a request asks you to change
  scheduling, parallelism, provider wiring or auth.

The manager's job is judgment and verification; the human's job is the
decisions that carry domain risk or unverifiable claims. When you escalate,
say what you verified, what you did not, and what decision you need.
