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
- **Tracing, grading, and burn guards are mechanical, not a dashboard.** `bin/trace_summary.py` turns the ledger into per-day/per-repo/per-tier totals; `bin/grade_run.py` scores each run's trajectory 0–100 from its transcript (first-edit step, test evidence, gates) at collection time and the score lands on the ledger line as an additive `grade` field; `bin/ledger_check.py` blocks unchanged re-briefs of issues with 2+ failures and no PR. The dashboard below is a *read-only view* over that state — it makes no decisions and writes nothing. Two guards cap pathological burn — a per-run `--max-tokens-out` in `agent.py` and a per-cycle `cycle_tokens_out_budget` in the supervisor — both off by default (`0`). They cap **completion tokens only**: prompt tokens are dominated by cache reads and do not separate good runs from bad ones, so they are never capped. Set the numbers above the p99 of your own measured runs (on the reference deployment: tokens_out p99 ≈ 148k, max ≈ 193k per run, so a 200k per-run cap and a 750k per-cycle budget trip only on unprecedented burn).

## Components

| Piece | What it does |
|---|---|
| `bin/agent.py` | The worker loop: provider chain with polite 429 backoff and quota failover, model tiers (cheap / balanced / flagship), step cap, empty-diff gate, machine manifest, JSONL transcript. `--max-tokens-out` stops a run cleanly once completion tokens reach the cap (off by default — set `workers.max_tokens_out` in the deployment config) |
| `bin/trace_summary.py` | Ledger aggregator: per-day / per-repo / per-tier totals from `worker_ledger.jsonl` — runs, PR-opened rate, tokens in/out, hit-cap rate, gate triggers, top-burn runs, repeat failures. JSON or one-line human summary |
| `bin/grade_run.py` | Mechanical trajectory grader: scores one run 0–100 from its transcript (first-edit step, exploration ratio, test evidence, gates, completion) plus a claim-vs-diff check against the worktree when run live at collection time. No LLM judge |
| `bin/ledger_check.py` | Per-issue retry guard: exits non-zero when an issue already has 2+ failed ledger rows and no PR opened — the supervisor calls it before briefing instead of eyeballing the ledger |
| `bin/dashboard.py` | Read-only dashboard over the state dir: one self-contained HTML page with the live view (workers in flight per repo, shepherd PRs, ledger tail) and the PR metrics (PR-opened rate, merges, failure classes, burn vs the cycle budget). No network, no credentials, never writes to the state dir |
| `bin/quick_check.py` | Tiny quota probe — sends a minimal completion to verify a plan's quota is live |
| `bin/dry_run.py` | Offline dry run: runs the real worker loop against a fake provider on 127.0.0.1 — no credential, no network, no real repo. Produces the diff that a supervisor would wrap into a pull request, so a reviewer can validate the setup before spending quota |
| `bin/render_prompts.py` | Renders the prompt templates with a deployment config into ready-to-install prompts |
| `bin/install_crons.py` | Renders the three scheduled prompts and writes `local/cron-manifest.json` (sha256 per cron body) — the installer's checklist for putting generated bodies into a scheduler and verifying them |
| `tests/` | Hermetic regression suite (stdlib `unittest`, no network, no credentials) covering config loading, both auth modes, the renderer, the installer manifest, and the publish gate's gitignore behavior. The publish gate runs it and aborts on failure |
| `config/deployment.example.json` | The deployment template — operator, repos, state/worktree paths, cadences, merge-policy specifics. Copy it to `config/deployment.local.json` (gitignored) and fill it in; the local file is the version actually used |
| `prompts/backlog-supervisor.md` | Orchestration prompt: collect stragglers, pick issues, isolate worktrees, write briefs with a code-discipline section, launch workers, review, PR, ledger |
| `prompts/pr-shepherd.md` | Orchestration prompt: CI triage, flake reruns, bounded mechanical fixes, and the merge policy |
| `prompts/harness-tune.md` | Orchestration prompt: the weekly evidence-gated self-improvement pass |
| `docs/OPERATIONS.md` | The operating policy in prose: isolation rules, tier routing, verification discipline, merge hard stops, ledger schema |
| `docs/ARCHITECTURE.md` | How the pieces fit: roles, state files, and the run lifecycle |
| `examples/` | A sample brief, sample ledger lines, and a sample worker-state file (all synthetic) |

The prompts are written for a scheduled agent runner (cron-style), but nothing in the worker loop depends on one — you can run `agent.py` by hand against any brief.

## Quick start — ten minutes to your first worker PR

Everything below runs in a fresh clone. Only step 4 needs a real credential, and
step 5 covers the "no credential yet" case. Every command in this section was
executed end to end on a fresh clone with placeholder values.

**Prerequisites** — `git`, Python 3.9+ (stdlib only; nothing to `pip install`;
verified on 3.12), the GitHub CLI (`gh`, already authenticated — `gh auth
status`), and a local clone of the repo the workers are allowed to touch.

### 1. Clone the harness

```bash
git clone https://github.com/you/plan-coder.git
cd plan-coder
```

### 2. Copy the deployment config and fill in the repo set

```bash
cp config/deployment.example.json config/deployment.local.json
```

`config/deployment.local.json` is gitignored and is the file the code actually
reads (search order: `$PLAN_CODER_CONFIG` → `config/deployment.local.json` →
`~/.plan-coder/deployment.json`). Edit at least these fields:

| Field | What to put there |
|---|---|
| `repos[]` | one entry per repo the workers may touch: `name`, `clone` (absolute path of your local clone), `slug` (`owner/repo` on GitHub), `base` (the branch workers branch from), `issues_per_cycle` |
| `operator.name` | your name — it is substituted into the rendered prompts |
| `skill.dir` | absolute path of **this clone** — the prompts call `<skill.dir>/bin/state_write.py`, `ledger_check.py`, `failover.py` |
| `state.dir`, `worktrees.dir` | where the supervisor keeps `workers.json`, the ledger, and one worktree per worker |
| `providers.zai.env_key`, `providers.minimax.env_key` | the names of the environment variables that hold your keys (defaults `ZAI_API_KEY`, `MINIMAX_API_KEY`) |
| `crons.backlog-supervisor.schedule.every` | `10m` for the first rehearsal; put it back to `2h` afterwards |
| `supervisor.max_parallel` | `1` for the first cycle |

The shipped example is full of placeholders (`you/repo-a`, `~/repos/repo-a`, …).
Every one of them must be yours, or the supervisor will read issues from the
wrong repo. `config/deployment.local.json` is never committed.

### 3. Credentials go in the environment, never in the config file

```bash
export ZAI_API_KEY=...        # z.ai (GLM Coding Plan key)
export MINIMAX_API_KEY=...    # MiniMax (token-plan key)
```

Keys are read from the environment at request time (`bin/agent.py` →
`_apply_auth`), or from a `dynamic_credentials` helper module in
`$PLAN_CODER_CRED_DIR` or `~/.plan-coder/cred/` if you use a vault. Do **not**
put a key in `config/deployment.local.json` — that file is gitignored, but the
rule of the repo is that key material never enters a file the harness reads.

### 4. Check that the credential is picked up (without printing it)

```bash
python3 -c "import os; k=os.environ.get('ZAI_API_KEY'); print('ZAI_API_KEY:', 'set' if k else 'NOT SET')"
python3 bin/quick_check.py --provider zai
```

Expected output:

```
ZAI_API_KEY: set
reply: ok
usage: {"prompt_tokens": 12, "completion_tokens": 3, ...}
```

The first command prints only `set` / `NOT SET` — never the key. The second
sends one tiny completion to the provider; it is the only step in this
quickstart that spends quota, and it is the end-to-end proof that the
credential was accepted.

### 5. Offline dry run — the no-op equivalent of a first PR

No credential, no network, no real repo: the worker loop runs against a fake
provider on 127.0.0.1 and produces the same artifact a pull request would
carry.

```bash
python3 bin/dry_run.py
```

Expected output (tail):

```
=== dry-run result ===
workdir: /tmp/plan-coder-dryrun-8xhppt31/repo
worker exit code: 0
changed paths:
   A DRY_RUN.md
diffstat:
DRY_RUN.md | 5 +++++
 1 file changed, 5 insertions(+)
DRY RUN OK: a diff exists — this is the artifact a supervisor turns into a pull request.
```

Point it at a clone you already have with
`python3 bin/dry_run.py --repo ~/repos/repo-a` — it creates a temporary git
worktree, runs the same flow, and removes the worktree afterwards.

### 6. Render and install the supervisor cron

```bash
python3 bin/install_crons.py
```

Expected output:

```
backlog-supervisor: /path/to/plan-coder/local/backlog-supervisor.rendered.md sha256=de8ba44b9070048c… (34728 chars)
harness-tune: /path/to/plan-coder/local/harness-tune.rendered.md sha256=6e5d0f4d96f24766… (13713 chars)
pr-shepherd: /path/to/plan-coder/local/pr-shepherd.rendered.md sha256=9ecd19ae2d8c21ba… (7298 chars)
manifest -> /path/to/plan-coder/local/cron-manifest.json
```

The script renders the three prompt templates into `local/` (gitignored) and
writes `local/cron-manifest.json` with each body's sha256. It does not talk to
any scheduler API — installing a body is an agent action:

1. take the **exact** content of `local/backlog-supervisor.rendered.md` and
   save it as the scheduled prompt (your scheduler's cron/update tool);
2. do the same for `local/pr-shepherd.rendered.md` and
   `local/harness-tune.rendered.md`;
3. re-read each saved body and compare its sha256 with the manifest:

```bash
python3 -c "import json; m=json.load(open('local/cron-manifest.json')); print(m['crons']['backlog-supervisor']['sha256'])"
```

Two checks worth running before you install anything:

```bash
# no unresolved tokens survived the render (an unresolved token aborts with exit 2)
grep -c '{{' local/*.rendered.md

# your repo list really made it into the body
grep -n 'you/repo-a' local/backlog-supervisor.rendered.md
```

Edit the template in `prompts/` or the config — never the installed body.

### 7. Give the supervisor one issue and watch the first PR

Open a small, well-specified issue in the target repo (the supervisor reads
issues with `gh issue list`; it never creates them):

```bash
gh issue create --repo you/repo-a \
  --title "Add a one-line note to README" \
  --body "Acceptance criteria: README.md contains the line 'Hello from plan-coder.' and the repo's test command still passes."
```

Then wait for the next supervisor tick (10 minutes with the rehearsal
cadence) and look for the pull request:

```bash
gh pr list --repo you/repo-a
```

The supervisor isolates a worktree at `<worktrees.dir>/<repo>-worker-<issue>`,
briefs a worker on branch `worker/<issue>-<short-slug>`, reviews the diff and
the machine manifest, and opens the PR. From there the PR shepherd keeps the
queue moving.

**Time budget**: clone + config + credentials + install ≈ 10 minutes of setup;
the PR lands on the next supervisor tick after that. If you have no scheduler
agent runner at all, step 5 is the documented no-op dry-run equivalent: it
produces the same diff, manifest, and report without spending quota.

### When there are no credentials

This is the first failure a new user hits. The worker fails fast — it does not
fall through to the fallback provider, because a missing credential is a setup
problem, not scarcity, and retrying would burn the other plan's quota:

```
## Summary

WORKER ABORTED: no credential available: set ZAI_API_KEY in the environment, or configure a credential helper ($PLAN_CODER_CRED_DIR or ~/.plan-coder/cred)
```

(`bin/quick_check.py` raises the same message, as a `RuntimeError` traceback.)

Where credentials go:

| Mode | Where the key lives | How the worker finds it |
|---|---|---|
| Environment key (default) | `ZAI_API_KEY` / `MINIMAX_API_KEY` in the shell that runs the worker; the names come from `providers.<name>.env_key` in the deployment config | `bin/agent.py` reads the variable at request time |
| Credential helper | a `dynamic_credentials.py` module in `$PLAN_CODER_CRED_DIR` or `~/.plan-coder/cred/` (the directory comes from `credentials.helper_dir`) | the helper injects the credential; the worker process never sees key material |

How to verify they are picked up, without printing anything secret:

```bash
# 1. is the variable set? (prints set / NOT SET only)
python3 -c "import os; k=os.environ.get('ZAI_API_KEY'); print('ZAI_API_KEY:', 'set' if k else 'NOT SET')"

# 2. is the helper directory the config points at?
python3 -c "import json,os; c=json.load(open('config/deployment.local.json')); d=os.path.expanduser(c.get('credentials',{}).get('helper_dir','~/.plan-coder/cred')); print(d, 'dynamic_credentials.py present:', os.path.isfile(os.path.join(d,'dynamic_credentials.py')))"

# 3. does the provider accept it? (one tiny completion — the only step that spends quota)
python3 bin/quick_check.py --provider zai
```

If (3) still fails, the key is wrong or the plan's quota is exhausted — the
provider's own error is in the output. If the worker aborts while the
supervisor runs, the same fix applies: the supervisor's shell must have the
key exported (a `~/.profile` line or your scheduler's environment settings),
because the cron body renders *paths*, never secrets.

## Running a worker by hand

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

## Plans, routes, and harnesses

A **plan** is the unit of subscription capacity. A plan belongs to a vendor, exposes one or more **routes**, and declares the **models** it can serve by tier. A route is either an API endpoint (`kind: "api"`) or a CLI harness (`kind: "cli"`). The harness name is an interface boundary, not a vendor runner built into this repository.

```json
{
  "plans": [
    {
      "id": "anthropic-max",
      "vendor": "anthropic",
      "routes": [
        {"kind": "cli", "harness": "claude"},
        {"kind": "api", "endpoint": "https://api.anthropic.com/v1/messages"}
      ],
      "models": {"balanced": "claude-sonnet-4-5"},
      "locked_to": [{"kind": "cli", "harness": "claude"}]
    }
  ]
}
```

`models` is a tier-to-model map, so the existing cheap/balanced/flagship ladder remains policy data rather than vendor-specific code. `locked_to` is an allow-list of routes; an empty list means unrestricted. If a dispatch asks a locked plan for any other route, resolution fails closed with `PlanLockError`, naming the plan and its allowed routes. It never silently falls back to another harness, API, vendor, or plan. The lock is configuration data: the resolver has no per-vendor exceptions.

For example, a Claude Pro/Max plan is limited to Anthropic's own tooling: only its Claude CLI harness is allowed by `locked_to`, even if another route is declared, and no generic API endpoint or another coding CLI may be substituted. The reference slice includes one generic CLI stub (`bin/plan_cli_stub.py`) that records the invocation a runner would receive without contacting a vendor or reading credentials; it is not a production Codex, Cursor, Grok, OpenCode, or Anthropic runner.

The optional `plans` section is validated when the deployment config is loaded. Invalid plan data raises `DeploymentConfigError`. Plans keep their declaration order unless `workers.plan_order` overrides it. For back-compat, a config containing only the existing `providers` records is represented as one API-only plan per provider (a single API route), with the same provider order, model tiers, auth behavior, and failover semantics as before.

## Deployment configuration

Everything specific to one installation lives in a single config file, not in the code or the prompts:

1. Copy `config/deployment.example.json` to `config/deployment.local.json` and fill in your values (operator name, repo list with clone paths/slugs/base branches and per-cycle issue counts, state and worktree directories, cadences, your merge-policy specifics like known repo-wide failures, and your credential-helper labels if you use one).
2. `deployment.local.json` is **gitignored** — and so is `local/`, where rendered prompts go. The publish gate's security scan only ever looks at files git would actually publish, so real values in the gitignored files can never leak into a commit.
3. `bin/agent.py` reads the config at startup (search order: `$PLAN_CODER_CONFIG`, `config/deployment.local.json`, `~/.plan-coder/deployment.json`) for credential-helper labels, the helper directory, and the default transcript directory. Everything is optional — with no config file, the env-key defaults apply.
4. `bin/render_prompts.py` substitutes the config into the `{{tokens}}` of the prompt templates and writes ready-to-install prompts to `local/`. Two tokens are derived, not stored: the repo table and the per-cycle split sentence are generated from the repo list. Unresolved tokens abort the render — a half-rendered prompt is never written.
5. The scheduled prompts are generated, not hand-maintained: in the reference deployment the scheduler's saved bodies are byte-identical to the rendered output. `bin/install_crons.py` renders them and emits `local/cron-manifest.json` with each body's sha256; whoever installs them (the operator's agent) updates the scheduler with the rendered content and verifies the saved bodies against the manifest. Edit the templates or the config — never the installed body.

## Design principles

1. **Cheap models type, expensive judgment decides.** Issue selection, brief quality, diff review, and merge decisions are the product; the worker loop is deliberately small (~560 lines, stdlib only).
2. **Every claim gets a check.** Worker prose is a hint. The manifest, the diff, and a fresh test run are the evidence.
3. **Isolation before parallelism.** One worktree per worker, never two git writers in one tree — branch switches destroy other writers' uncommitted work.
4. **State is boring on purpose.** One JSON state file, one append-only JSONL ledger, one transcript per run. When a state file corrupts, the ledger is the recovery source.
5. **Self-improvement is gated like everything else.** The tuning pass needs two runs of evidence per change and a human gate for anything structural. The system may tune its prompts; it may not rewrite its own authority.

## Dashboard — a read-only view over the state dir

`bin/dashboard.py` renders one self-contained HTML page (no CDN, no network, no
credentials) from the state dir the harness already writes:

```bash
# live view + PR metrics, straight to stdout (or --out page.html)
python3 bin/dashboard.py --state-dir ~/.plan-coder/state
```

* **Live view** — workers in flight per repo (`workers.json`), the PRs the shepherd
  tracks / has merged / has flagged (`shepherd.json`), and the tail of
  `worker_ledger.jsonl` as an activity feed.
* **PR metrics** — PR-opened rate, merges (including UNSTABLE merges), fix attempts and
  flagged PRs per distinct PR, failure classes, and token burn vs the configured cycle
  budget. The numbers come from `bin/trace_summary.py` and
  `bin/shepherd_ledger.py summary`, so the page and the CLIs agree.

It is read-only: it never writes to the state dir, needs no API key, and never calls
`gh` (CI state comes from `shepherd_ledger.jsonl`). A missing state file renders an empty
panel with a "no rows yet" note. The design and its status are in
[ROADMAP.md](ROADMAP.md) §"Dashboard design".

## What is *not* in this repo

The live system's private state — the real metrics ledger, transcripts, PR-tracking state, and operator-specific configuration — is excluded by design (see `.gitignore`). The prompts are templates: repo lists, paths, thresholds, and the merge policy's repo-specific exceptions are placeholders to fill in with your own.

## Status

Working system, actively used; published as a reference implementation and a starting point, not a packaged product. The worker loop is stable; the prompts are living documents that the weekly tuning pass keeps editing.

The theme-by-theme roadmap — plan adapters, quickstart, CI, external-CLI supervision, per-plan accounting, the brief-quality loop and the dashboard — with each theme's status and the dashboard's design is in [ROADMAP.md](ROADMAP.md).

## License

MIT — see [LICENSE](LICENSE).

## Maintenance

This repo is not a copy of the harness — since the October 2026 unification refactor, **it is the harness**: the author's live system executes `bin/agent.py` from this working tree (via symlinks from its skill directories), with machine-specific configuration kept outside the repo (the credential helper under `~/.plan-coder/cred`, the transcript directory passed as a flag, live state in a separate private directory). There is no translation step between "live" and "public" code.

What keeps that safe is a fail-closed publish gate, run at least weekly by the harness-tune pass: a script scans the whole tree for internal identifiers and secret shapes, compiles every Python file, and only then commits and pushes. The prompts and docs are generalized by hand in the same pass (they describe a private deployment: concrete repo names, paths, and state locations become placeholders). Live operational state — the real ledger, transcripts, and PR-tracking files — never enters this repo; if the scan or a compile check fails, the publish aborts instead of going out.
