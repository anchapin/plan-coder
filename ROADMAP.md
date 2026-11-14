# Roadmap

plan-coder's roadmap is a list of **themes**, each with a status that says how far the
work actually got in *this* repository. The status column is not a wish list: every claim
below points at a file in this repo (or says plainly that nothing is built).

- **Built** — implemented here; the implementing file is named.
- **Partial** — a slice exists and is named; the rest is named too.
- **Planned (not built)** — no implementation exists in this repo. Nothing in this document
  should be read as "it works".

## How to read the issue links

Each theme links to the issue that tracks it. The tracker is the authority for a theme's
current state; this document only asserts what is visible in the code.

- Issue **#1** is locally verifiable: it is closed by commit `0ed5f5d` ("feat(plans): model
  subscription plans as the capacity unit with harness-lock enforcement (Closes #1)").
- The remaining numbers (**#2, #3, #4, #5, #10**) are the issue numbers issue #5 ties these
  themes to. This repository carries no issue metadata beyond the commit log above, so the
  theme→issue mapping for those numbers is quoted from issue #5 rather than verified here.

| # | Theme | Status | What exists in this repo | Issues |
|---|---|---|---|---|
| 1 | **Plan adapters** (plans as the unit of capacity, API + CLI routes) | **Partial** — the capacity model and route resolution are built; vendor-specific adapters are not | `bin/plans.py` (`Plan`, `Route`, `Dispatch`, `validate_plans`, `PlanLockError`, `RouteResolutionError`); the `plans` section in `config/deployment.example.json` (including a `kind: "cli"` route with `locked_to`); README §"Plans, routes, and harnesses". **Not built:** production vendor runners — `bin/plan_cli_stub.py` "records the invocation a runner would receive without contacting a vendor or reading credentials"; `docs/ARCHITECTURE.md` says the slice "defines the route interface and one recording stub, not vendor-specific production runners" | [#1](https://github.com/anchapin/plan-coder/issues/1) |
| 2 | **Fresh-clone genericization** (a clone runs on its own, with no operator state) | **Partial** — the published tree is self-contained; an end-to-end "clone → run → ledger" path is not | `config/deployment.example.json` is the committed template and `config/deployment.local.json` is gitignored; `bin/render_prompts.py`, `bin/install_crons.py` and the publish gate `bin/sync_public.py` make a clone self-contained; `examples/` holds synthetic samples of the state files; README §"What is *not* in this repo" and §"Maintenance" describe the live/public split. **Not built:** a clone still needs the operator to fill in `config/deployment.local.json`, the state dir and the cron bodies — `tests/` covers config loading, both auth modes, the renderer, the installer manifest and the publish gate, but there is no fresh-clone smoke run | [#2](https://github.com/anchapin/plan-coder/issues/2), [#5](https://github.com/anchapin/plan-coder/issues/5) |
| 3 | **Quickstart** (a new user can get a first run without reading the whole README) | **Partial** — a manual quickstart exists; a scripted smoke path does not | README §"Quick start" (`git worktree add`, `python3 bin/agent.py --brief … --workdir … --provider … --model-tier … --max-steps …`), `examples/BRIEF.example.md`, `bin/quick_check.py` (a quota probe, not a smoke test). **Not built:** a one-command "clone → probe → run" script; the quickstart still assumes operator API keys and a hand-made brief | [#3](https://github.com/anchapin/plan-coder/issues/3), [#5](https://github.com/anchapin/plan-coder/issues/5) |
| 4 | **CI** (continuous integration for plan-coder's own repo) | **Planned (not built)** — this repo has **no** CI configuration | There is no `.github/workflows` directory in this tree. The shepherd's CI handling is about *other* repos: `prompts/pr-shepherd.md` polls `gh pr checks <n>` on tracked worker PRs and `bin/shepherd_ledger.py` logs those CI actions. Nothing here runs this repo's own test suite automatically | [#4](https://github.com/anchapin/plan-coder/issues/4), [#5](https://github.com/anchapin/plan-coder/issues/5) |
| 5 | **External-CLI supervision** (running a plan through a vendor CLI harness) | **Planned (not built)** — the route *interface* exists, the supervision loop does not | `bin/plans.py` accepts `{"kind": "cli", "harness": …}` routes and resolves them (failing closed via `PlanLockError` when a plan is `locked_to`); `bin/plan_cli_stub.py` only records a command line. **Not built:** any process supervision for an external CLI (spawn, exit-code/timeout handling, transcript capture, quota accounting for a CLI session) | [#10](https://github.com/anchapin/plan-coder/issues/10), [#5](https://github.com/anchapin/plan-coder/issues/5) |
| 6 | **Desktop-runner parity** (the same harness driven by a desktop runner) | **Planned (not built)** — nothing in this repo addresses it | The runner surface here is the OpenAI-shaped API route (`bin/agent.py` speaks the chat-completions shape to the z.ai and MiniMax plan endpoints) plus the CLI *route* interface from #1. No desktop runner, its session model, or its parity layer exists in this tree | [#10](https://github.com/anchapin/plan-coder/issues/10), [#5](https://github.com/anchapin/plan-coder/issues/5) |
| 7 | **Per-plan cost/token accounting** (spend attributed to a plan, not just a tier) | **Planned (not built)** — tokens are recorded per run, not per plan | `worker_ledger.jsonl` rows carry `tier`, `providers_used`, `tokens_in`, `tokens_out` (row schema in `prompts/backlog-supervisor.md` step 8; sample rows in `examples/worker_ledger.example.jsonl`), and `bin/trace_summary.py` aggregates by day / repo / tier / issue only. Plans exist in config (`plans[].id`) but **no plan id is written onto a ledger row**, so per-plan totals cannot be computed from the data this repo writes today | [#5](https://github.com/anchapin/plan-coder/issues/5), [#10](https://github.com/anchapin/plan-coder/issues/10) |
| 8 | **Brief-quality feedback loop** (does the brief predict the run's outcome?) | **Partial** — the guard rails and the review spike exist; the closed loop does not | `bin/ledger_check.py` blocks an unchanged re-brief of an issue with 2+ `failed:*` rows and no `pr_opened` row; `bin/failover.py` feeds prior-attempt reflections and a tier change into the next brief; `bin/grade_run.py` writes the mechanical `grade` (0–100) onto the ledger row; `bin/goldfish.py` is a zero-context brief review whose rows land in `goldfish_ledger.jsonl` (`findings`, `blockers`, `dispositions`, `brief_changed`) — but it ships **disabled** (`workers.goldfish.enabled: false` in `config/deployment.example.json`). The weekly `harness-tune` pass is the consumer of all of this. **Not built:** a scored brief-quality signal that closes the loop from brief text to outcome | [#5](https://github.com/anchapin/plan-coder/issues/5) |
| 9 | **The dashboard** (one view over the harness's own state) | **Partial** — the MVP is built: `bin/dashboard.py` serves the live view and PR-metrics panels over a state dir; the quality-trends panel is not built | `bin/dashboard.py` renders one self-contained HTML page (no CDN, no network, no credentials) from `workers.json`, `shepherd.json`, `worker_ledger.jsonl` and `shepherd_ledger.jsonl`: **Panel 1 (live view)** — workers in flight per repo, shepherd-tracked / merged / flagged PRs, ledger tail as an activity feed; **Panel 2 (PR metrics)** — PR-opened rate (the `bin/trace_summary.py` ratio), merges incl. UNSTABLE merges, fix attempts and flagged PRs per distinct PR (the `bin/shepherd_ledger.py summary` counts), failure classes, and token burn vs the configured cycle budget. Read-only: it never writes to the state dir, and a missing state file renders an empty panel with a "no rows yet" note. **Not built:** the Panel 3 quality-trends view (grade trends, JiT/Goldfish panels) — that stays with the weekly tune pass for now | [#5](https://github.com/anchapin/plan-coder/issues/5) |

---

## Dashboard design (design + MVP status)

This section is the design for issue #5's third acceptance criterion and the status of
what was built from it. It is a
**presentation layer over the state directory the harness already writes**. The MVP lives
in `bin/dashboard.py` (Panel 1 and Panel 2 below); Panel 3 (quality trends) is specified
here but **not built** — the page says so.

### Constraints

1. **Read-only.** The dashboard opens the state dir and reads it. It never writes to
   `workers.json`, `shepherd.json` or any ledger, and never repairs them (the state files
   are the harness's to write, atomically, via `bin/state_write.py`).
2. **No new storage.** No database, no cache file, no service. Every number shown is
   derived on the fly from files that already exist.
3. **No hosted service, no credentials.** The dashboard runs locally against a local state
   dir and talks to nothing else. It must not need an API key, must not call `gh`, and must
   not embed operator identifiers.
4. **Same aggregation semantics as the CLIs.** Where a number already exists in
   `bin/trace_summary.py` or `bin/shepherd_ledger.py summary`, the dashboard must show the
   same value rather than a second, subtly different one.
5. **Graceful on missing files.** The ledgers may not exist yet (the harness-tune prompt
   treats a missing ledger as "the spike has not logged yet", not an error). The dashboard
   shows an empty panel with a "no rows yet" note.

### Data sources (all in the deployment config's state dir)

The state dir comes from the deployment config (`state.dir`, default `~/.plan-coder/state`),
resolved in the same order as `bin/agent.py` and `bin/trace_summary.py`:
`$PLAN_CODER_CONFIG` → `config/deployment.local.json` → `~/.plan-coder/deployment.json`.

| File | Shape | Role for the dashboard |
|---|---|---|
| `workers.json` | one JSON object, keyed by run id, one entry per worker slot | live state: `status`, `repo`, `issue`, `branch`, `workdir`, `brief`, `started_at`, `pr`, `tokens`, `provider_used`, `note` |
| `worker_ledger.jsonl` | append-only, one JSON object per collected worker | the metrics record: `ts`, `repo`, `issue`, `branch`, `run_id`, `tier`, `providers_used`, `tokens_in`, `tokens_out`, `steps` (`"used/max"`), `outcome`, `pr_url`, `gate_triggers`, `grade` |
| `shepherd_ledger.jsonl` | append-only, one JSON object per shepherd action | PR/CI history: `ts`, `repo`, `pr`, `pr_url`, `action`, `outcome`, `ci`, `merge_state`, `detail` |
| `jittest_ledger.jsonl` | append-only, one JSON object per completed catching check | quality signal: `ts`, `repo`, `issue`, `pr`, `trigger`, `hypotheses`, `tests_generated`, `three_state_passed`, `three_state_class`, `killed_stage_a`, `judge`, `survived`, `tokens_out`, `alex_disposition`, `label_adjudication`, `outcome` |
| `goldfish_ledger.jsonl` | append-only, one JSON object per completed brief review | brief-quality signal: `ts`, `repo`, `issue`, `trigger`, `tier`, `tokens_out`, `steps`, `findings`, `blockers`, `dispositions`, `brief_changed`, `outcome` |
| `shepherd.json` | one JSON object | live PR tracking: `tracked_prs` (per-PR `rerun_count`, `ci_fix_attempts`, a `jittest:catch` flag), `seen_merged`, `pending_trigger`, `last_trigger`, `jittest_catch_prs` |

(`examples/worker_ledger.example.jsonl` and `examples/workers.example.json` are synthetic
samples of the first two files and can be used to render the dashboard without touching
real state.)

Field names and value vocabularies are the ones the code writes: ledger row shapes are
documented in `prompts/harness-tune.md` (its "Inputs" section) and `prompts/backlog-supervisor.md`
(step 8), and are produced by `bin/trace_summary.py`, `bin/shepherd_ledger.py`,
`bin/jittest.py` (`ledger_row`) and `bin/goldfish.py` (`ledger_row`).

### Panels

#### Panel 1 — Live view (what the harness is doing right now)

*Purpose:* answer "is anything running, and what is it burning?" without opening three
files by hand.

*Reads:*

- `workers.json` — per worker entry: `status` (`working` / `collecting` / `done` / `failed`
  / `escalated` / `split_suggested`), `repo`, `issue`, `branch`, `workdir`, `started_at`,
  `pr`, `tokens`, `provider_used`, `note`.
- `worker_ledger.jsonl` — the most recent rows, for the just-finished tail: `ts`, `repo`,
  `issue`, `run_id`, `tier`, `outcome`, `tokens_in`, `tokens_out`, `pr_url`, `grade`.
- `shepherd.json` — `tracked_prs` (count and the PR numbers), `pending_trigger`.

*Derived:* active-worker count by `status`; tokens spent by active workers (`tokens`);
newest N ledger rows as a live activity feed; PRs currently tracked by the shepherd.

*Refresh cadence:* **every 60 seconds.** The underlying state only moves on the
supervisor's ~2-hour cycle (`crons.backlog-supervisor.schedule.every = "2h"`), so a
1-minute poll is cheap and shows collection/launch transitions promptly. If the panel is
rendered as a static snapshot, re-render it on the same cadence.

#### Panel 2 — PR metrics (how PRs are moving)

*Purpose:* answer "how many PRs opened, how many merged, where are they stuck?"

*Reads:*

- `worker_ledger.jsonl` — rows whose `outcome` is `pr_opened`: `ts`, `repo`, `issue`,
  `run_id`, `tier`, `tokens_in`, `tokens_out`, `pr_url`, `grade`.
- `shepherd_ledger.jsonl` — every action: `ts`, `repo`, `pr`, `pr_url`, `action`
  (`rerun`, `fix_attempt`, `merged`, `merged_by_shepherd`, `needs_alex`, `closed_unmerged`,
  `merge_conflicts`, `trigger_requested`, `checked`, `snapshot`, `other`), `outcome`, `ci`,
  `merge_state`, `detail`.
- `shepherd.json` — `tracked_prs` and `seen_merged` for the still-open tail.

*Derived:* PR-opened rate (the same `pr_opened / runs` ratio `bin/trace_summary.py`
computes); merges (`action` = `merged` / `merged_by_shepherd`), including the count of
merges whose `merge_state` was `UNSTABLE`; reruns and fix attempts per distinct PR
(`fix_attempt_distinct_prs`, `needs_alex_distinct_prs`); open-PR age from `shepherd.json`.

*Refresh cadence:* **every 5 minutes.** The shepherd itself acts every 30 minutes
(`shepherd.interval_minutes: 30`, `crons.pr-shepherd.schedule.every = "30m"`); a 5-minute
refresh shows CI reruns and merges as they are logged, well inside the shepherd's own cycle.

#### Panel 3 — Quality trends (are runs getting better or worse?)

*Purpose:* answer "is the harness's output quality improving, and where is it burning?"

*Reads:*

- `worker_ledger.jsonl` — `grade` (0–100 mechanical score from `bin/grade_run.py`), `outcome`
  (the class vocabulary is `pr_opened`, `failed:<class>`, `stopped:token-cap`, `stale`,
  `split_suggested`, `escalated`), `gate_triggers` (`empty_diff=1`, `midrun_no_edit=0`,
  `worker_hit_cap=0` — the three machine gates), `tier`, `providers_used`, `tokens_in`,
  `tokens_out`, `steps`.
- `jittest_ledger.jsonl` — `judge.catch` / `judge.intended` / `judge.unclear`,
  `hypotheses.kept`, `three_state_passed`, `survived`, `label_adjudication.ground_truth`,
  `label_adjudication.adjudicated_by`, `tokens_out`, `outcome`.
- `goldfish_ledger.jsonl` — `findings`, `blockers`, `dispositions.propose_fix` /
  `contest` / `escalate` / `decline`, `brief_changed`, `tokens_out`, `outcome`.

*Derived:* grade distribution and grade-vs-outcome disagreements (low grade + `pr_opened`
vs high grade + `failed:*`); outcome-class histogram; gate-trigger counts (the same
`MACHINE_GATES` split `bin/trace_summary.py` uses); tokens per PR-opened run; catch rate
from the JiT ledger; brief-review findings/blockers from the Goldfish ledger.

*Refresh cadence:* **daily, and immediately after the weekly tune pass.** The trends are
computed over rolling 7-day windows, so sub-daily changes are noise; the authoritative
recompute is the weekly `harness-tune` job (`crons.harness-tune`, Sunday), whose step 1
already aggregates exactly these ledgers with `bin/trace_summary.py` and
`bin/shepherd_ledger.py summary`. The dashboard reuses those aggregates rather than
inventing its own windows.

### Rendering and cadence summary

| Panel | Primary source | Cadence | Aggregation owner |
|---|---|---|---|
| Live view | `workers.json`, `shepherd.json`, tail of `worker_ledger.jsonl` | 60 s | dashboard reads state files directly |
| PR metrics | `worker_ledger.jsonl`, `shepherd_ledger.jsonl` | 5 min | `bin/trace_summary.py`, `bin/shepherd_ledger.py summary` semantics |
| Quality trends | `worker_ledger.jsonl`, `jittest_ledger.jsonl`, `goldfish_ledger.jsonl` | daily + after each weekly tune | `bin/trace_summary.py` semantics; tune-pass windows |

Intended shape of the implementation: one local, read-only command
(e.g. `bin/dashboard.py`) that resolves the state dir through the existing config search
order, reads the files above, and emits a **single self-contained HTML file** (no CDN, no
network calls, no external assets) opened with `file://`. The three panels are three
sections of that one page, each carrying its own timestamp so a stale panel is visible
rather than silently old. The implementation must not write to the state dir, must not
need credentials, and must not shell out to `gh` — CI state is shown from
`shepherd_ledger.jsonl` (`ci`, `merge_state`), not fetched live.

**What the MVP implements** (`bin/dashboard.py`): the command shape above
(`--state-dir`, plus `--config`, `--since`, `--tail`, `--out`, `--json`), the live view
and the PR metrics as two sections of the one HTML page, each with its own timestamp.
The page is written to stdout by default (`--out FILE` writes it elsewhere; the dashboard
refuses to write inside the state dir). Panel 3 (quality trends) is **not built**: the
page renders a placeholder that points at the weekly tune pass.

### Open questions for the build

1. Should the live view also surface `transcripts/<run-id>.jsonl` (per-step drill-down) or
   stay at the summary level? Transcripts are truncated on purpose and are read by grep,
   not whole-file.
2. Should the PR-metrics panel show only rows from the last 7 days (matching the tune
   pass's `--since` window) or the whole ledger, with the window selectable?
3. `workers.json` can be stale between supervisor cycles and — per the loop audit recorded
   in `bin/state_write.py`'s docstring and `prompts/backlog-supervisor.md` — has been
   truncated before. Should the panel read the
   `<file>.statebak-*` backups written by `bin/state_write.py` as a fallback, or show the
   last-known state with a "state may be stale" marker?
