# plan-coder

**A manager/worker harness for running background coding agents on cheap token-plan credits.**

A frontier "manager" agent does the judging — picks backlog issues, writes briefs, isolates git worktrees, reviews diffs, verifies tests, opens PRs, and shepherds CI. Cheap plan models (z.ai GLM Coding Plan, MiniMax token plan) do the typing, inside a small tool-calling worker loop that never commits, never pushes, and never merges.

Built and battle-tested on a real multi-repo backlog: hundreds of worker runs, a metrics ledger, a CI shepherd that merges green PRs under an explicit policy, and a weekly self-tuning pass that treats worker transcripts as a loss signal. This repo is that working system — the code the author actually runs, its operating policy, and the three orchestration prompts — with personal state and deployment identifiers kept out (see Maintenance).

## Why

Token plans (flat-rate coding plans from z.ai, MiniMax, and similar providers) reset on 5-hour and weekly windows. Unused quota is wasted quota — but a cheap model left alone produces cheap-model work: empty diffs declared "done," tests hand-waved as pre-existing failures, scope creep. plan-coder is the discipline layer that makes plan credits produce mergeable PRs:

- **The worker only types.** It gets a brief, a worktree, and five tools (`exec`, `read_file`, `write_file`, `edit_file`, `list_dir`), all confined to its workdir. It cannot commit, push, merge, or reach outside its tree.
- **Done is verified, never claimed.** An empty-diff completion gate refuses to let a worker finish with no changes unless it quotes discriminating evidence from the brief's own acceptance steps. Every run ends with a harness-generated *machine manifest* — base/head commits, files changed, diffstat, gate triggers — git facts the manager checks against the real diff, never model prose.
- **Verification is independent.** The manager re-reads the diff, re-runs the tests, and applies a stale-test protocol (stash, re-run on unmodified code, pop) before believing any "pre-existing failure" claim.
- **The queue keeps itself flowing.** A PR shepherd polls CI every 30 minutes, reruns flakes once, auto-fixes clearly mechanical failures with bounded attempts, and merges well-scoped PRs once their PR-specific CI is green — under a written merge policy with hard stops (domain-judgment changes, workflow/branch-protection edits, anything unverifiable) reserved for the human.
- **The harness tunes itself, with evidence.** Every run appends one line to a metrics ledger and a truncated JSONL transcript. A weekly tuning pass aggregates outcomes, samples the top failure classes' transcripts, and applies small edits to prompts and routing — each backed by at least two distinct worker runs. Structural changes are flagged for the human, never applied autonomously.

## Components

| Piece | What it does |
|---|---|
| `bin/agent.py` | The worker loop: provider chain with polite 429 backoff and quota failover, model tiers (cheap / balanced / flagship), step cap, empty-diff gate, machine manifest, JSONL transcript |
| `bin/quick_check.py` | Tiny quota probe — sends a minimal completion to verify a plan's quota is live |
| `bin/render_prompts.py` | Renders the prompt templates with a deployment config into ready-to-install prompts |
| `config/deployment.example.json` | The deployment template — operator, repos, state/worktree paths, cadences, merge-policy specifics. Copy it to `config/deployment.local.json` (gitignored) and fill it in; the local file is the version actually used |
| `prompts/backlog-supervisor.md` | Orchestration prompt: collect stragglers, pick issues, isolate worktrees, write briefs with a code-discipline section, launch workers, review, PR, ledger |
| `prompts/pr-shepherd.md` | Orchestration prompt: CI triage, flake reruns, bounded mechanical fixes, and the merge policy |
| `prompts/harness-tune.md` | Orchestration prompt: the weekly evidence-gated self-improvement pass |
| `docs/OPERATIONS.md` | The operating policy in prose: isolation rules, tier routing, verification discipline, merge hard stops, ledger schema |
| `docs/ARCHITECTURE.md` | How the pieces fit: roles, state files, and the run lifecycle |
| `examples/` | A sample brief, sample ledger lines, and a sample worker-state file (all synthetic) |

The prompts are written for a scheduled agent runner (cron-style), but nothing in the worker loop depends on one — you can run `agent.py` by hand against any brief.

## Quick start

```bash
export ZAI_API_KEY=...        # z.ai (GLM Coding Plan key)
export MINIMAX_API_KEY=...    # MiniMax (token-plan key)

# Isolate the work
git worktree add /tmp/my-worker -b worker/123-fix-thing origin/main

# Write a brief (see examples/BRIEF.example.md), then run the worker
python3 bin/agent.py \
  --brief /tmp/briefs/BRIEF.md \
  --workdir /tmp/my-worker \
  --provider minimax,zai \
  --model-tier balanced \
  --max-steps 60
```

The worker writes `WORKER_REPORT.md` into the workdir: provider/tier used, token totals, its summary, and the machine manifest. The manager (you, or your orchestrating agent) reviews the diff, verifies, commits, and opens the PR.

Provider notes:

- Auth is dual-mode: a provider API key from the environment (`ZAI_API_KEY` / `MINIMAX_API_KEY`) if set, otherwise a `dynamic_credentials` credential-helper module (found via `$PLAN_CODER_CRED_DIR` or `~/.plan-coder/cred/`) that injects a vault-held credential into the request without the worker process ever seeing key material. Missing or broken credentials fail fast with configuration guidance.
- z.ai traffic goes to the **coding-plan endpoint** (`https://api.z.ai/api/coding/paas/v4`), never the pay-as-you-go endpoint.
- MiniMax is the default priority provider; the chain fails over automatically when a provider stays rate/quota-limited through polite retries. Auth failures (401/403) fail fast instead of burning the fallback provider's quota.
- Tiers: z.ai cheap `glm-5.3-flashx`, balanced `glm-5.3-flash`, flagship `glm-5.3`; MiniMax cheap `MiniMax-M2.5-highspeed`, balanced `MiniMax-M2.7`, flagship `MiniMax-M3`.

## Deployment configuration

Everything specific to one installation lives in a single config file, not in the code or the prompts:

1. Copy `config/deployment.example.json` to `config/deployment.local.json` and fill in your values (operator name, repo list with clone paths/slugs/base branches and per-cycle issue counts, state and worktree directories, cadences, your merge-policy specifics like known repo-wide failures, and your credential-helper labels if you use one).
2. `deployment.local.json` is **gitignored** — and so is `local/`, where rendered prompts go. The publish gate's security scan only ever looks at files git would actually publish, so real values in the gitignored files can never leak into a commit.
3. `bin/agent.py` reads the config at startup (search order: `$PLAN_CODER_CONFIG`, `config/deployment.local.json`, `~/.plan-coder/deployment.json`) for credential-helper labels, the helper directory, and the default transcript directory. Everything is optional — with no config file, the env-key defaults apply.
4. `bin/render_prompts.py` substitutes the config into the `{{tokens}}` of the prompt templates and writes ready-to-install prompts to `local/`. Two tokens are derived, not stored: the repo table and the per-cycle split sentence are generated from the repo list. Unresolved tokens abort the render — a half-rendered prompt is never written.

## Design principles

1. **Cheap models type, expensive judgment decides.** Issue selection, brief quality, diff review, and merge decisions are the product; the worker loop is deliberately small (~560 lines, stdlib only).
2. **Every claim gets a check.** Worker prose is a hint. The manifest, the diff, and a fresh test run are the evidence.
3. **Isolation before parallelism.** One worktree per worker, never two git writers in one tree — branch switches destroy other writers' uncommitted work.
4. **State is boring on purpose.** One JSON state file, one append-only JSONL ledger, one transcript per run. When a state file corrupts, the ledger is the recovery source.
5. **Self-improvement is gated like everything else.** The tuning pass needs two runs of evidence per change and a human gate for anything structural. The system may tune its prompts; it may not rewrite its own authority.

## What is *not* in this repo

The live system's private state — the real metrics ledger, transcripts, PR-tracking state, and operator-specific configuration — is excluded by design (see `.gitignore`). The prompts are templates: repo lists, paths, thresholds, and the merge policy's repo-specific exceptions are placeholders to fill in with your own.

## Status

Working system, actively used; published as a reference implementation and a starting point, not a packaged product. The worker loop is stable; the prompts are living documents that the weekly tuning pass keeps editing.

## License

MIT — see [LICENSE](LICENSE).

## Maintenance

This repo is not a copy of the harness — since the October 2026 unification refactor, **it is the harness**: the author's live system executes `bin/agent.py` from this working tree (via symlinks from its skill directories), with machine-specific configuration kept outside the repo (the credential helper under `~/.plan-coder/cred`, the transcript directory passed as a flag, live state in a separate private directory). There is no translation step between "live" and "public" code.

What keeps that safe is a fail-closed publish gate, run at least weekly by the harness-tune pass: a script scans the whole tree for internal identifiers and secret shapes, compiles every Python file, and only then commits and pushes. The prompts and docs are generalized by hand in the same pass (they describe a private deployment: concrete repo names, paths, and state locations become placeholders). Live operational state — the real ledger, transcripts, and PR-tracking files — never enters this repo; if the scan or a compile check fails, the publish aborts instead of going out.
