# Can plan-coder supervise an external CLI agent (OS3 / OpenCode)?

**Investigation slice for issue #10 — supervising external CLI agents.**
Docs-only: no supervision code is built here. Every statement about plan-coder is
grounded in a named in-repo file/symbol (grep-verifiable). Every statement about
OpenCode's CLI mechanisms is labelled either **VERIFIED** (fetched from a cited
public source during this run) or **UNVERIFIED — requires the desktop probe**
(section 6).

## 0. Scope, vocabulary, and evidence rules

* **plan-coder** = this repository (reference implementation of a manager/worker
  harness: `README.md` "Why", "Components").
* **The supervisor** = the scheduled prompt in `prompts/backlog-supervisor.md`
  (one cycle: collect stragglers → launch workers → collect → review → commit/push/PR
  → ledger). **The pr-shepherd** = `prompts/pr-shepherd.md` (CI triage, merges under
  a policy). **The worker** = a cheap plan model driven by `bin/agent.py`.
* **OS3** = the operator's external CLI agent designation. The string `OS3` does
  not appear anywhere in this repository (`grep -rni "OS3" .` → no hits), so this
  document treats it as an *external CLI agent product*, hypothesised to be
  OpenCode-based. Nothing below depends on that hypothesis being true.
* **OpenCode** = an external coding CLI. This run had **no network access**
  (`curl https://opencode.ai/docs/cli/` → curl exit 35, SSL connect error;
  `curl https://example.com` → exit 35), so **no OpenCode mechanism could be
  verified**. Every OpenCode-specific mechanism below is therefore marked
  **UNVERIFIED** and is the explicit target of the section 6 probe. The only
  OpenCode mention that *is* in-repo is `README.md:92`, which states the generic
  CLI stub "is not a production Codex, Cursor, Grok, **OpenCode**, or Anthropic
  runner" — i.e. this repo has deliberately never contained an OpenCode runner.
* **"fluxion"** = the example target repository slug used throughout
  `examples/PR_BODY.gap.example.md:3`, `examples/PR_BODY.numeric.example.md:3`
  and `examples/receipts.example.jsonl`. The single-writer rule below is
  expressed in repo terms against that repo.
* **Citation rule used throughout**: `file:line` plus the symbol. Every citation
  below can be re-checked with, e.g.
  `grep -n "_machine_manifest" bin/agent.py`,
  `grep -n "def commit" bin/state_write.py`.

---

## 1. Current state in this repo: one worker round, end to end

### 1.1 Launch

| Step | Where it lives | Grounding |
|---|---|---|
| Issue selection + premise check | `prompts/backlog-supervisor.md` step 2–3 | `gh issue list …`; skip issues with an open PR; retry guard `python3 bin/ledger_check.py <repo> <issue>` |
| Isolation (worktree + branch) | `prompts/backlog-supervisor.md` step 4 | `git worktree add {{worktrees.dir}}/<repo>-worker-<issue> -b worker/<issue>-<short-slug> origin/<base>`; stale worktree/branch cleanup first |
| Brief authoring | `prompts/backlog-supervisor.md` step 4 + `examples/BRIEF.example.md` | Every brief opens with `SCOPE:` and `ROOM:` header blocks, plus a `PROOF:` block and a "Code discipline" section |
| Process launch | `prompts/backlog-supervisor.md` step 5 | `python3 {{skill.dir}}/bin/agent.py --brief … --workdir … --run-id … --transcript-dir … --provider … --model-tier … --max-steps … --max-tokens-out …` with `background=true`, staggered ~2 min apart |
| Worker loop | `bin/agent.py` `main()` (line 653), `run_step()` (line 772) | One chat + tool-execution round per step; tools are `TOOLS` (line 200): `exec`, `read_file`, `write_file`, `edit_file`, `list_dir`; `SYSTEM_PROMPT` (line 174) carries the workdir-confinement rules |
| Provider / plan routing | `bin/agent.py` `PROVIDERS` (42), `resolve_dispatch()` (150), `resolve_plan_dispatch()` (166) → `bin/plans.py` `resolve_dispatch()` (278) | Plan = capacity unit; `locked_to` is an allow-list that fails closed with `PlanLockError` (`bin/plans.py` 23); CLI routes exist as data (`Route`, line 32) and `plans.cli_invocation()` (line 330) returns `[harness, "--model", model]` as a *generic* invocation |
| Workdir confinement | `bin/agent.py` `Worker._safe()` (433), `Worker._exec()` (455), `Worker._write()` (506) | Tool calls are rejected when the path escapes the worktree; writes are atomic (temp file + `os.replace`, `_atomic_write` 488) |

### 1.2 How a round is detected as complete

* **Harness side.** `bin/agent.py` `main()` writes `WORKER_REPORT.md` into the
  workdir at the end of the run (the `open(os.path.join(workdir,
  "WORKER_REPORT.md"), "w")` block) and then appends a `finished` event to the
  transcript (`tlog({"event": "finished", …})`). The report is *not* prose only:
  `_machine_manifest()` (line 377) appends git facts — `base_commit`,
  `head_commit`, `files_changed_git`, `diffstat`, `gate_triggers`, `token_cap` —
  and explicitly refuses to fabricate them ("unreadable facts degrade to status
  unknown with a named blocker").
* **Supervisor side.** `prompts/backlog-supervisor.md` step 6: poll the
  background sessions until each finishes or the deadline hits; "Collect only
  from the worker's final state: wait for the background session's finished
  event and WORKER_REPORT.md — an intermediate 'done' notice in the transcript
  tail is not a collection". If prose and git disagree, git wins.
* **Run-level guards inside the loop** (all env-gated, defaults stated in
  `bin/agent.py`): `EMPTY_DIFF_GATE_ENABLED` (284) + `EMPTY_DIFF_GATE_MAX_ROUNDS`
  (285) — a "done" with no diff is nudged for discriminating evidence;
  `MIDRUN_GATE_ENABLED` (295) — one halfway no-edit checkpoint;
  the per-run output-token cap (checked after each step in `main()`, recorded as
  outcome `stopped:token-cap` by the supervisor).
* **Grading.** `prompts/backlog-supervisor.md` step 8 runs
  `python3 bin/grade_run.py --run-id … --ledger … --report … --workdir …`.
  `bin/grade_run.py` `load_transcript()` (86) parses the JSONL transcript,
  `grade()` (204) scores the trajectory (first-edit step, test evidence, gate
  triggers), `find_ledger_row()` (96) links the transcript to a ledger row.

### 1.3 How a round is recorded

* **`workers.json`** (live state): one JSON object, one entry per worker slot —
  `docs/ARCHITECTURE.md` "State" table. Fields written by the supervisor:
  `{status, repo, issue, branch, workdir, brief, started_at, pr, tokens,
  provider_used}` (`prompts/backlog-supervisor.md` "State file:" paragraph).
  Status vocabulary: `working / collecting / done / failed / escalated /
  split_suggested` (`docs/ARCHITECTURE.md`; `examples/workers.example.json`).
  Saving is **never** a direct write: stage to `<file>.new`, then
  `python3 {{skill.dir}}/bin/state_write.py commit <file>.new <file>`
  (`prompts/backlog-supervisor.md`, "State saves are atomic or they do not
  happen"; `prompts/pr-shepherd.md` step 3 for `shepherd.json`).
  `bin/state_write.py` `commit()` refuses a staging file that is not valid JSON,
  copies the live file to `<dest>.statebak-<UTC ts>`, then writes a temp file,
  `fsync`s and `os.replace()`s it over the destination (`_prune_backups()`
  keeps the newest `--keep`, default 5). `validate()` is the pre-check.
* **`worker_ledger.jsonl`** (append-only metrics record): one JSON line per
  collected worker, appended in `prompts/backlog-supervisor.md` step 8 with the
  fields `ts, repo, issue, branch, run_id, tier, providers_used, tokens_in,
  tokens_out, steps, outcome, pr_url, gate_triggers, grade`
  (`outcome ∈ {pr_opened | failed:<class> | stopped:token-cap | stale |
  split_suggested | escalated}`); a worked example is
  `examples/worker_ledger.example.jsonl`.
  Readers: `bin/trace_summary.py` `summarize()` (156) for the weekly tune,
  `default_ledger_path()` (57) for the default location, `bin/ledger_check.py`
  `check()` (32) as the per-issue retry guard, `bin/failover.py` `decide()` (162)
  + `reflection_for()` (102) for the substitution ladder.
* **Proof receipts** (per PR): `bin/receipt.py` `classify()` (85),
  `append_row()` (136), `summarize()` (164); invoked from
  `prompts/backlog-supervisor.md` 7-proof. Proof is explicitly **not** a merge
  gate.
* **Shepherd actions ledger**: `bin/shepherd_ledger.py` `append_row()` (59),
  `summarize()` (71), `ACTIONS` vocabulary.

### 1.4 How a round is registered with the pr-shepherd

`prompts/backlog-supervisor.md` step 7: after review, delete `WORKER_REPORT.md`,
commit ("Closes #<n>"), `git fetch origin && git rebase origin/<base>` (abort on
conflict and open the PR flagged), push, check
`gh pr list --repo <slug> --head <branch> --state open` for an existing PR
(never create a duplicate), `gh pr create` with the `## Proof` section, and
finally **append the PR URL to the `tracked_prs` array in `{{state.dir}}/shepherd.json`**
(add the array if missing, never duplicate a URL), saved with the same atomic
`state_write.py commit`. The shepherd then picks it up:
`prompts/pr-shepherd.md` step 1 loads `shepherd.json` and calls
`gh pr view` / `gh pr checks`, and step 4 writes the `pending_trigger` marker
that queues the next supervisor cycle.

### 1.5 The plan/route design material (issue #1 slice) — the seam an external CLI would plug into

* `bin/plans.py`: `Plan` (57), `Route` (32) with `kind ∈ {"api","cli"}`,
  `Dispatch` (68), `validate_plans()` (119), `plans_from_config()` (199),
  `resolve_dispatch()` (278) — lock check *before* route availability, no silent
  fallback — and `cli_invocation()` (330): "the harness name is the command; a
  caller may hand this list to its own runner, while `plan_cli_stub.py` records
  it without invoking anything."
* `bin/plan_cli_stub.py` `main()`: records `{"argv": [harness, "--model", model],
  "harness", "model", "stub": true}` to a JSON file; it "never contacts a vendor
  and never reads credentials".
* `docs/ARCHITECTURE.md` §"Plans, routes, and harnesses": "A CLI route
  identifies a harness; this slice defines the route interface and one recording
  stub, not vendor-specific production runners."
* `config/deployment.example.json` shows the shipped shape: `plans[]` with
  `id`, `vendor`, `routes[]` (`{"kind":"cli","harness":"claude"}`),
  `models` tier→model, `locked_to[]`.

**Consequence for #10:** the *routing* half of an OpenCode integration already
exists as data (`kind: "cli"`, `harness: "opencode"`). The *supervision* half —
everything in 1.1–1.4 that assumes the worker is a `bin/agent.py` child
process of the supervisor — does not.

---

## 2. The external-agent gap

| # | Lifecycle step | `bin/agent.py` worker today | External CLI agent today | Gap |
|---|---|---|---|---|
| 1 | Dispatch / plan resolution | `plans.resolve_dispatch()` (`bin/plans.py` 278) + `agent.resolve_dispatch()` (150); API providers only in `PROVIDERS` (42) | A `{"kind":"cli","harness":"opencode"}` route is expressible in config, but `bin/plan_cli_stub.py` only *records* an invocation | **No runner.** `cli_invocation()` returns `[harness,"--model",model]`; nothing executes a real CLI, so a dispatch to `opencode` cannot happen |
| 2 | Isolation | One `git worktree` per worker, created by the supervisor (`prompts/backlog-supervisor.md` step 4) | The external agent must be given a worktree it may not leave | **No enforcement.** `Worker._safe()` (433) enforces confinement for *tool calls inside agent.py*; an external CLI's own file tools are outside that boundary entirely (UNVERIFIED whether the CLI has any such confinement) |
| 3 | Brief delivery | `--brief <path>`; the brief text becomes the single `user` message (`main()` builds `messages`) | UNVERIFIED — whether OpenCode accepts a file path, stdin, or only a prompt string | **Format adapter needed**; the repo's brief format (SCOPE/ROOM/PROOF blocks, `examples/BRIEF.example.md`) is repo-specific prose an external agent has no reason to honour |
| 4 | Run lifecycle (steps, budgets) | `run_step()` (772) loop, `--max-steps`, `--max-tokens-out` per-run cap, mid-run gate | UNVERIFIED — whether OpenCode exposes a step/turn budget or token cap at all | **No budget control.** The repo's burn guards (`workers.max_tokens_out`, `workers.cycle_tokens_out_budget` in `config/deployment.example.json`) have no analogue to bind to |
| 5 | Progress evidence | Per-step JSONL transcript (`tlog`, 724) with `step`, `tool_calls`, `tool_results`, `tool_result_kinds`, `duration_ms` | UNVERIFIED — OpenCode may have its own session log, of unknown format and location | **Transcript incompatibility.** `bin/grade_run.py` `load_transcript()` (86) / `grade()` (204) parse *this* event vocabulary; a foreign log would score as "no write/edit tool call in transcript" → worst grade |
| 6 | Completion detection | `WORKER_REPORT.md` written by `main()` + a `finished` transcript event; supervisor waits for the background session's finished event + the report (`prompts/backlog-supervisor.md` step 6) | UNVERIFIED — exit code semantics, session-state files, or neither | **No completion signal.** The supervisor has no "child process finished" event to await |
| 7 | Machine manifest / diff facts | `_machine_manifest()` (377): `base_commit`, `head_commit`, `files_changed_git`, `diffstat`, `gate_triggers` | n/a — but the facts are git-only, so they are recoverable | **Recoverable, but unowned.** The manifest is produced by the harness, not the agent; an external agent produces no manifest, so the supervisor must synthesize one (it can: `_machine_manifest()` needs only `(workdir, base, gate_triggers)`) |
| 8 | Empty-diff / no-edit gates | `EMPTY_DIFF_GATE_ENABLED` (284), `MIDRUN_GATE_ENABLED` (295) — the harness nudges the *model* mid-run | The gates are prompt injections into the worker model; an external agent's prompt is not the supervisor's to inject | **Gate bypass.** A "done" with an empty tree would arrive un-nudged, and the classification step (`failed:empty-diff`) would only catch it *after* the fact |
| 9 | Live state (`workers.json`) | Status protocol `working/collecting/done/failed/escalated/split_suggested`; atomic commit via `bin/state_write.py` `commit()` | n/a | **New status needed** (e.g. `external_running`) so overlapping supervisor cycles do not double-collect (`prompts/backlog-supervisor.md` step 1 + step 6 "another overlapping cycle got there first") |
| 10 | Metrics ledger | One `worker_ledger.jsonl` row per collected worker, `outcome` classified by the supervisor (`prompts/backlog-supervisor.md` step 8) | n/a | **Outcome taxonomy gap.** External rounds have no `tokens_in/out`, no `providers_used`, no `steps`; the row shape (`examples/worker_ledger.example.jsonl`) needs new fields or nulls, and `bin/trace_summary.py` `summarize()` (156) buckets by tier/outcome |
| 11 | Retry / duplication guards | `bin/ledger_check.py` `check()` (32) blocks re-briefing an issue with ≥2 `failed:*` rows and no `pr_opened`; `bin/failover.py` `decide()` (162) substitutes a tier and carries reflections | n/a | **Guard applies unchanged but must see external rows** — so external outcomes must land in the same ledger file with the same `repo`/`issue` keys |
| 12 | Commit / push / PR | Supervisor owns the git write: commit "Closes #<n>", rebase, push, `gh pr create` (`prompts/backlog-supervisor.md` step 7) | UNVERIFIED — whether the external agent commits/pushes itself (many coding CLIs are configured not to) | **Authority split.** If the external agent pushes, the supervisor loses the "one writer" property; if it does not, the supervisor must collect from a dirty tree it did not author |
| 13 | Shepherd registration | Append PR URL to `tracked_prs` in `shepherd.json` + atomic commit; shepherd then does `gh pr checks` / merges under the merge policy | Same, provided the PR exists and the head branch is `worker/*` | **Low gap.** Registration is agent-agnostic; the hard constraint is the head-branch pattern `worker/*` (`prompts/pr-shepherd.md` MERGE POLICY) |
| 14 | Merge policy / hard stops | `prompts/pr-shepherd.md` MERGE POLICY "NEVER merge" list; `bin/goldfish.py` `HARD_STOP_MARKERS` (109) scope-path screen | Same policy applies to whatever PR arrives | **Low gap** (policy is per-PR, not per-agent), *provided* the PR is registered |
| 15 | Weekly tuning | `bin/trace_summary.py` `summarize()` (156) + `prompts/harness-tune.md`; evidence gate = ≥2 distinct runs per change | External runs would need to be in the ledger to be tunable | **Ledger integration only** |
| 16 | Credentials | Keys live in env or the credential-helper dir (`bin/agent.py` `_load_dc()` 544, `_apply_auth()` 574); workers "never see credentials" (`docs/OPERATIONS.md` "Safety rails") | An external CLI authenticates with its own account/token on the machine it runs on | **Boundary shift.** The repo's credential model assumes the supervisor process never holds key material; an external CLI holds *its own* credential, on the operator's desktop. The supervisor never sees it — but the supervisor also cannot run that CLI on this VM without violating the operator's own boundary (and this run's hard stop: no account/credential access) |

---

## 3. Completion-detection candidates for an external round

Four candidates, each with its failure modes and the evidence that would settle
it. **Every OpenCode-specific behaviour below is UNVERIFIED** — none of it was
confirmable in this run (no network; no desktop access; no CLI installed).

### 3.1 Process exit (exit code of the CLI invocation)

* **FP mode:** the CLI exits 0 while the task is incomplete — e.g. it printed a
  question/plan and exited, or it hit an internal soft-stop after a partial
  edit. A 0 exit is not a "work is done" statement.
* **FN mode:** the work is complete but the process ends non-zero (the CLI
  reports a non-fatal error at teardown), or the CLI daemonises / detaches, so
  the supervisor's child handle exits before the work is done, or the supervisor
  itself kills the process on the cycle deadline (`prompts/backlog-supervisor.md`
  step 6 "or the deadline hits") and later reads a *finished* tree as if the run
  had completed normally.
* **Settling evidence:** one real round (probe P1) that records the exit code
  **together with** the resulting worktree state (`git status --porcelain`,
  `git diff --stat <base>`) and the agent's own final message. If exit code 0
  always coincides with "tree changed or agent explicitly said no change needed",
  the exit code becomes a usable *corroborating* signal — but only after
  cross-checking it against git, exactly as the repo already insists
  ("If prose and git disagree in either direction … trust git",
  `prompts/backlog-supervisor.md` step 6).

### 3.2 Session-state files (the CLI's own session/storage files)

* **FP mode:** stale session files from a *previous* round in the same location
  are read as this round's completion; a session file is written at session
  *start* (so its existence means "started", not "finished"); a session file
  that is rewritten incrementally makes "mtime changed" ambiguous between
  "still working" and "finished writing".
* **FN mode:** the CLI writes no session file in headless mode; or it writes it
  only on a graceful exit that a kill prevents; or the file lives in a
  per-user config dir that the supervisor (running as a different user, on a
  different machine) cannot read at all.
* **Settling evidence:** the probe must capture, for one round, (a) the CLI's
  documented storage location and file format, (b) the file list + mtimes
  before launch and after exit, (c) whether a file appeared *before* the work was
  finished (start marker) or only after (end marker). Until then, session files
  are at best a *secondary* signal and at worst a false-completion generator.
  **UNVERIFIED.**

### 3.3 New PR carrying the agent's commit trailer (via `gh pr list`)

* **FP mode:** the trailer is not unique to this agent (any PR that mentions the
  same model/agent name, or a human-authored PR quoting it); a *re-push* of the
  same PR re-lists as "new"; a PR from a *previous* round of the same issue is
  still open and matches.
* **FN mode:** the external agent does not commit or push at all (very plausible
  for a CLI configured to leave changes uncommitted — which is exactly this
  repo's own worker rule, `examples/BRIEF.example.md` "no commits, pushes, or
  merges"); the PR body carries no trailer (the trailer may be in the *commit
  message*, not the PR body, so `gh pr list --search` on the body would miss it);
  the PR is created by a human or by another tool; the agent pushes to a branch
  that is not `worker/*` and is therefore never registered (see §1.4/§2 row 13).
* **Settling evidence:** the probe must show, for one round, the *exact* place
  the trailer lands (commit message vs PR body), and the supervisor must query
  the narrowest possible predicate — e.g. `gh pr list --repo <slug> --head
  worker/<issue>-<slug> --state open` (as `prompts/backlog-supervisor.md` step 7
  already does) **plus** the trailer check — rather than a repo-wide trailer
  search. A repo-wide trailer search is unsafe against FP mode 1.

### 3.4 Push events (watching the branch for new commits)

* **FP mode:** a push by any other actor to the same branch (a human hotfix, a
  concurrent supervisor cycle — the repo explicitly anticipates overlapping
  cycles, `prompts/backlog-supervisor.md` step 1); a force-push or a rebase
  push; a push that carries no new work (e.g. a `git commit --allow-empty` or a
  branch re-creation).
* **FN mode:** the agent never pushes (same as 3.3); the push lands on a branch
  whose name the supervisor does not know (the external agent may choose its
  own branch naming); the supervisor is watching from the wrong clone or the
  wrong remote; the push happens while the supervisor is asleep and its poll
  window misses it (the supervisor polls at ~2 h intervals,
  `prompts/backlog-supervisor.md` cadence, `config/deployment.example.json`
  `crons.backlog-supervisor.every = "2h"`).
* **Settling evidence:** the probe must show whether the agent pushes at all,
  under what branch name, and with what commit metadata; plus a negative case
  (an agent that must *not* push, per the repo's worker rules) to confirm the
  supervisor does not mis-read it.

### 3.5 Ranked conclusion (before the probe)

1. **Primary signal: git facts in the worktree** — `git diff --name-only <base>`
   plus a *completion artifact*. This is the one signal the repo already trusts
   ("trust git", `prompts/backlog-supervisor.md` step 6) and the one that
   survives the loss of the agent's own telemetry.
2. **Corroborating signal: process exit code**, if the probe shows it is
   meaningful.
3. **Secondary: session files** — only after the probe proves their lifecycle
   (start marker vs end marker) and their reachability from the supervisor's
   host.
4. **Last resort: PR/trailer and push watchers** — these detect *publication*,
   not *completion*, and carry the worst FP/FN profile; they are also the
   signals the repo already gets for free from GitHub once the PR exists.

The probe in §6 must produce a **single round with a captured timeline** of all
four signals before any of this becomes a design decision.

---

## 4. Next-round dispatch options, and where supervision could run

### 4.1 Dispatch: fresh headless run vs resumed session

| | **A. Fresh headless run per round** | **B. Resumed session across rounds** |
|---|---|---|
| Shape | One CLI invocation per issue, in a fresh worktree, exactly like `bin/agent.py` per issue (`prompts/backlog-supervisor.md` step 5) | One long-lived CLI session per issue, resumed for the next round with the same context |
| Repo fit | High. The repo's model is "Workers are interchangeable and disposable" (`docs/ARCHITECTURE.md` "Roles"); one worktree per worker; per-round budget; per-round ledger row | Low. A session is stateful and survives the round boundary; the repo's state model has exactly three state files (`docs/ARCHITECTURE.md` "State") and no notion of a live agent session |
| Completion detection | Exit code + tree state per round (see §3.1) | Harder: "round complete" is ambiguous when the session is idle-waiting; the supervisor must distinguish "session paused" from "session finished" |
| Budget | Per-round caps map onto existing knobs (`workers.max_tokens_out`) | Caps must be enforced across rounds; the repo's cycle budget (`workers.cycle_tokens_out_budget`) is per *supervisor cycle*, which does not align with a session's lifetime |
| Isolation | Clean: fresh worktree, same rule as `bin/agent.py` ("One git worktree per worker, always", `docs/ARCHITECTURE.md` "Concurrency and isolation") | The session carries context across worktrees — exactly the cross-contamination the isolation rule exists to prevent |
| Evidence | Per-round transcript → `bin/grade_run.py` `grade()` works if the adapter emits the same event vocabulary | Grading spans rounds; `grade()` (204) assumes one transcript per run |
| Verdict | **Preferred** | Only for a specific "issue too big for one round" case, and only if the repo's `split_suggested` path (`prompts/backlog-supervisor.md` step 8) is the model instead |

The CLI flag names for either mode are **UNVERIFIED** (e.g. whether a
non-interactive/"run once" mode exists at all, and whether it can resume). The
probe must capture the CLI's own `--help` output verbatim.

### 4.2 Where the supervision could run

| | **S1. Desktop-runner "gist queue"** | **S2. Desktop-local watcher** | **S3. This VM (the supervisor host)** |
|---|---|---|---|
| What exists in-repo today | **No queue transport exists.** The only dispatch transports are (a) a background child process on the same host (`prompts/backlog-supervisor.md` step 5) and (b) cron-installed prompt bodies rendered by `bin/render_prompts.py` `derived()`/`lookup()` (60/89) and installed by `bin/install_crons.py` `render_all()` (41) | **No watcher exists.** The repo's only "watcher" is the shepherd's per-cycle `gh pr view` / `gh pr checks` polling in `prompts/pr-shepherd.md` step 1–2 | The supervisor runs wherever its cron owner runs (`config/deployment.example.json` `crons.*.owner = "goal:<your-goal-slug>"`); the repo does not assume a particular host |
| Two-way channel | Would need a *new* round-trip transport (brief out, result back). Today's cron install is one-way (operator installs the rendered body) — `README.md` "Deployment configuration" item 5 | Natural: the watcher lives next to the CLI, sees the worktree and the session files, and writes a result file the supervisor can read | Already exists (the ledger/state files) |
| Credential exposure | Keeps desktop credentials on the desktop; the supervisor never holds them — consistent with `docs/OPERATIONS.md` "Safety rails" | Same | Would require the desktop CLI's account on this VM — out of scope for this run's hard stops, and contrary to "Workers never see credentials" |
| Main risk | Silent staleness: the queue is invisible to `workers.json`, so the supervisor's "no more than N active" accounting and its overlapping-cycle protection (status protocol) break | A second writer in the same tree — the repo's oldest and most expensive lesson ("never two git-writing workers in one tree", `docs/ARCHITECTURE.md` "Concurrency and isolation") | The supervisor would be supervising a CLI it cannot vouch for, on a host where the CLI's own state files are reachable |
| Repo-shape verdict | **Viable if** the queue carries the same brief format (`examples/BRIEF.example.md`) and the watcher is a *reporter*, not a *writer* | **Viable if** the watcher only reads git state and writes a result artifact; it must never commit | **Rejected for now** — needs desktop credentials on this VM |

**Recommended shape (buildable, no new authority):** S1/S2 hybrid — the
external agent runs on the desktop (where its credentials live); a *thin
desktop-local reporter* (the "watcher") observes the worktree and writes a
result artifact (transcript + manifest + exit code) into a known path; the
supervisor picks the artifact up through the existing state mechanism
(`workers.json` + `worker_ledger.jsonl` + `shepherd.json`) exactly as it does
for `bin/agent.py` runs, and registers the PR with the shepherd exactly as in
`prompts/backlog-supervisor.md` step 7. The supervisor never gains git write
authority over the external tree; it gains *read* authority plus the same
review authority it already has.

---

## 5. Guardrails mapping — what applies unchanged

| Repo guardrail | Where it lives today | How it applies to an external round | Change needed? |
|---|---|---|---|
| **Hard stop: results moved in ways needing domain judgment** (physics/simulation outputs) | `prompts/pr-shepherd.md` MERGE POLICY "NEVER merge" list: `{{merge_policy.hard_stop_domain}}` ("simulation/physics outputs" in `config/deployment.example.json`); pre-dispatch screen in `bin/goldfish.py` `HARD_STOP_MARKERS` (109) — `.github/workflows/`, `migrat`, `/physics`, `physics/`, `simulation output` — matched against the brief's scope paths in `goldfish.decide_trigger()` (135) | Unchanged: the policy is per-PR, and the shepherd cannot tell who authored the diff. The *pre-dispatch* screen fires on the brief's declared scope paths, so an external round must have its brief written in the repo's format (`examples/BRIEF.example.md` "Allowed paths") for that screen to still apply | **No policy change**; the brief format must be preserved (that is what the adapter is for) |
| **Hard stop: `.github/workflows`, branch protection, release config** | same MERGE POLICY list; `bin/goldfish.py` `HARD_STOP_MARKERS`; `bin/jittest.py` `HARD_STOP_MARKERS` (163) / `decide_trigger()` (224) matches `"hard-stop"` against diff paths | Unchanged — same reasoning. Note `bin/jittest.py` `decide_trigger()` fires on diff paths, so an external round's diff is screenable by the same code once the diff exists | **No change** |
| **Hard stop: unverifiable changes** | `prompts/pr-shepherd.md` "anything where you cannot verify the implementation is correct"; `prompts/backlog-supervisor.md` step 6 (the supervisor must review the diff itself); `docs/ARCHITECTURE.md` "Prose is not evidence" | Unchanged *and* it is the strongest argument for the adapter: the supervisor's verification is git-based (diff, manifest, re-run tests), so it works identically on an external agent's diff — provided the base commit is pinned and the diff is attributable to one round | **No change**, plus one addition: the supervisor must pin `base_commit` for an external round the way `bin/agent.py` `main()` does (`base_commit = _git_out(workdir, "rev-parse", "HEAD")`) — the external agent cannot be trusted to pin it |
| **Round caps / budgets** | `--max-steps` and `--max-tokens-out` (`bin/agent.py` `main()`); `workers.max_steps`, `workers.max_tokens_out`, `workers.cycle_tokens_out_budget` (`config/deployment.example.json`); supervisor cycle cap on parallel workers (`supervisor.max_parallel`, `prompts/backlog-supervisor.md` step 1) | The *cycle* caps (parallel workers, cycle token budget) apply unchanged. The *per-run* caps have no binding until the probe shows the CLI exposes a budget knob (UNVERIFIED). Until then, the supervisor's own deadline (`supervisor.collect_deadline_hours`) is the only cap, and the repo's burn guard becomes unenforceable | **Gap** — needs either (a) an UNVERIFIED-then-verified CLI budget flag, or (b) an out-of-band kill (process kill on deadline), which must be part of the adapter contract |
| **Single-writer enforcement** | `docs/ARCHITECTURE.md` "Concurrency and isolation": "One git worktree per worker, always… Two git-writing agents in one shared tree destroy each other's uncommitted work"; `prompts/backlog-supervisor.md` step 3 ("never run two workers on the same issue") and the Standing rules ("One worktree per worker, never two git-writing workers in one tree") | Applies unchanged and is *stricter* for external rounds: while the external agent owns the fluxion worktree (`examples/PR_BODY.*.example.md` use `you/fluxion`), the supervisor must not launch its own `bin/agent.py` worker on any issue in that repo, and must not launch a *fix attempt* into that tree. The repo's mechanism for "an issue is already held" is the `workers.json` status set + `bin/ledger_check.py` `check()` (32) — the external round must occupy a `workers.json` slot with a status the supervisor recognises as held, or the "never two workers on the same issue" rule is unenforceable | **One addition:** an explicit "held by external agent" status (e.g. `external_running`) + a check in step 3's skip list, plus the `bin/ledger_check.py` ledger rows carrying the same `repo`/`issue` keys |
| **Retry guard** | `bin/ledger_check.py` `check()` (32): ≥2 `failed:*` rows and no `pr_opened` → blocked; `bin/failover.py` `decide()` (162) → tier substitution + reflections | Unchanged *if* external outcomes are written to `worker_ledger.jsonl` with the same `repo`/`issue`/`outcome` keys | **No change** (ledger integration) |
| **Atomic state writes** | `bin/state_write.py` `commit()`; `prompts/backlog-supervisor.md` "State saves are atomic or they do not happen"; `prompts/pr-shepherd.md` step 3 | Unchanged — the external agent does not write `workers.json`/`shepherd.json` at all; only the supervisor does, atomically | **No change** |
| **Merge authority** | `prompts/pr-shepherd.md` MERGE POLICY (merge only under the policy, `gh pr merge --merge`, never `--admin`/`--squash`/rebase); supervisor never merges at open time (`prompts/backlog-supervisor.md` step 7) | Unchanged — but only if the external PR is registered in `shepherd.json` `tracked_prs`; an unregistered external PR would sit unmerged *and* unmerged-by-policy-visible | **No change** (registration is mandatory) |
| **Harness isolation** | `prompts/backlog-supervisor.md` Standing rules: "harness edits (agent.py, SKILL.md, these prompts) happen only in the weekly harness-tune pass or the main chat, never delegated into a worker run" | Applies unchanged: the external agent must receive a *brief*, never authority to edit the harness | **No change** |

---

## 6. The desktop probe (must pass before any implementation)

**Goal of the probe:** produce the missing real-round evidence — one complete
OS3/OpenCode round, observed end to end, with every signal in §3 captured on a
timeline, so that the design in §4 can be built without guessing.

**Constraints on the operator:** do this on the desktop where the CLI is
installed and authenticated; use a disposable repo/branch; do not merge
anything; do not touch the real fluxion worktrees.

### P0 — CLI surface capture (no work yet; ~5 minutes)

Run and save the verbatim output of:

```
opencode --version
opencode --help
opencode run --help          # or whatever the non-interactive entry point is
opencode --help | grep -i -E "session|resume|continue|model|json|print|quiet"
```

Capture, as answers to explicit questions:

1. Is there a non-interactive / one-shot mode? (What are its flags?)
2. Is there a way to select a model? (Flag name and accepted values.)
3. Is there a session-identifier / resume / continue mechanism? (Flag names.)
4. Is there a machine-readable output mode (JSON events, NDJSON, log file)?
5. Where does the CLI store session/state data? (Path, format, when written.)
6. What are the exit codes, and is a non-zero exit used for failure vs. for a
   completed run with findings?

Save all of it to `probe/P0-cli-surface.txt` **with the command line above each
block**. This is the artefact that converts every **UNVERIFIED** label in §3
and §4 into a verified one — or exposes that the mechanism does not exist.

### P1 — one real round (~1 hour, sized to one small issue)

1. **Prepare the tree exactly as the repo's supervisor does**
   (`prompts/backlog-supervisor.md` step 4), so the probe exercises the real
   shape:
   ```
   git fetch origin
   git worktree add <probe-dir>/probe-<issue> -b worker/<issue>-<slug> origin/<base>
   ```
   Record the base SHA: `git -C <probe-dir>/probe-<issue> rev-parse HEAD` →
   `probe/P1-base.txt`.
2. **Write the brief** at `<probe-dir>/probe-<issue>-brief/BRIEF.md` in the repo's
   format (`examples/BRIEF.example.md`): `SCOPE:` (goal, allowed paths, hard
   stops, done-definition), `ROOM:` (worktree, base branch, isolation rules:
   *no commits, pushes, or merges* — unless the probe deliberately tests the
   committing variant, see P1c), `PROOF:` (the evidence to leave behind and its
   type: visual/numeric/docs), acceptance criteria, verification commands, and
   the code-discipline section. Choose an issue whose acceptance criterion is
   checkable by one command and whose change is 1–3 files.
3. **Snapshot state before launch** (for the §3 timeline):
   ```
   ls -la ~/.local/share/opencode ~/.config/opencode 2>/dev/null > probe/P1-state-before.txt
   find ~ -maxdepth 4 -newermt '-1 minute' -iname '*opencode*' > probe/P1-files-before.txt
   ```
   (adjust to whatever P0 says the storage location is).
4. **Launch the agent**, exactly as the supervisor would launch a worker, with
   stdout/stderr captured:
   ```
   ( time opencode run "<brief path or prompt text>" ) > probe/P1-stdout.txt 2> probe/P1-stderr.txt
   echo "exit=$?" >> probe/P1-stdout.txt
   ```
   Record the **wall-clock start and end** (the `time` output) → `probe/P1-timing.txt`.
   The exact invocation is **UNVERIFIED** until P0 confirms the flag; use the
   P0-verified form.
5. **Snapshot state after exit** (same two commands as step 3) →
   `probe/P1-files-after.txt`, `probe/P1-state-after.txt`.
6. **Capture the tree result** — the primary completion signal:
   ```
   git -C <probe-dir>/probe-<issue> status --porcelain > probe/P1-git-status.txt
   git -C <probe-dir>/probe-<issue> diff --stat <base-sha> > probe/P1-diff-stat.txt
   git -C <probe-dir>/probe-<issue> diff --name-only <base-sha> > probe/P1-diff-names.txt
   ```
   And the agent's own final message → `probe/P1-agent-summary.txt`.
7. **Decide, from the captured evidence, the answers to §3:**
   * exit code vs. tree state: do they agree? (corroborating or contradicting?)
   * session files: written before or after the work? start-marker or end-marker?
   * did the agent try to commit/push? (check `git status` for a staged/committed
     state and `git log <branch>` for new commits)
   * did the agent leave any summary artifact (a report file) in the worktree?
     If yes, what is its name and format? If no, the supervisor must synthesize
     the manifest — `bin/agent.py` `_machine_manifest()` (377) needs only
     `(workdir, base, gate_triggers)` and is reusable as-is.
8. **Probe the publication path** (needed for §3.3/§3.4):
   * Push the branch yourself (the supervisor's job, per
     `prompts/backlog-supervisor.md` step 7), open a PR with
     `gh pr create` whose body contains a stable agent trailer (e.g.
     `Agent-Tool: opencode`, `Agent-Run-Id: <run id>`), then capture:
     ```
     gh pr list --repo <slug> --head worker/<issue>-<slug> --state open --json number,url,headRefName,title > probe/P1-gh-pr-list.json
     gh pr view <n> --repo <slug> --json body,commits > probe/P1-gh-pr-view.json
     ```
   * Record whether the trailer is visible from `gh pr list --search` (body) or
     only from the commit objects (`gh pr view --json commits`). This settles
     §3.3's FN mode.
   * **Do not merge.** Leave the PR open (or close it unmerged) — the shepherd's
     merge policy is not under test here.
9. **P1c (optional, high value): the "agent commits and pushes" variant.**
   Repeat steps 2–7 with a brief that *does* allow the agent to commit and
   push, to learn whether the CLI can be trusted with git write access in the
   worktree, and what the commit metadata looks like (trailer placement).
   Without P1c the supervisor must keep the repo's own rule: the agent leaves
   changes uncommitted and the supervisor commits them.

### P2 — bring back (one archive, ≤ a few MB)

```
probe/
  P0-cli-surface.txt
  P1-base.txt P1-timing.txt P1-stdout.txt P1-stderr.txt
  P1-files-before.txt P1-files-after.txt P1-state-before.txt P1-state-after.txt
  P1-git-status.txt P1-diff-stat.txt P1-diff-names.txt
  P1-agent-summary.txt
  P1-gh-pr-list.json P1-gh-pr-view.json
  P1-diff.patch        # git diff <base-sha> from the probe worktree
```

Bring the archive back into the repo (as a scratch artefact, *not* committed to
the tracked tree) and re-run the four §3 evaluations against it.

### P3 — pass/fail rule (this is the deciding evidence)

The probe **passes** iff all of the following hold:

1. **A deterministic completion signal exists** that the supervisor can read
   without asking the agent: exit code and/or a session file whose lifecycle is
   unambiguous, **and** it agrees with the git facts at least once.
2. **The diff is git-verifiable** in exactly the repo's terms: a pinned
   `base_commit`, a non-empty `files_changed_git` (or an explicit, evidenced
   "no change needed"), and a diff the supervisor can review and re-test.
3. **The agent stays in its worktree** for the whole round (no writes outside
   it) — otherwise §2's isolation row and §5's single-writer rule fail
   immediately.
4. **A summary artifact exists or is synthesizable**: either the agent leaves a
   report the supervisor can parse, or the supervisor can synthesize the
   manifest from git facts alone.
5. **The publication path works** with the repo's conventions: the branch is
   `worker/<issue>-<slug>`, the PR is registered in `shepherd.json`
   `tracked_prs`, and the agent's identity is attributable on the PR (trailer
   or equivalent).

**If any of 1–3 fails, the verdict flips to No-go** with the failing item named
(as the deciding evidence). If 4–5 fail, the verdict stays Go but the build
must include the synthesis/registration work explicitly.

---

## 7. Verdict

### **GO — conditional on the desktop probe (P3) passing.**

**What "Go" means here.** The design is buildable, and it needs no new
authority, no new state file, and no change to the merge policy. It is:

1. **An adapter with the same contract as `bin/agent.py`.** A small runner that
   (a) reads the repo-format `BRIEF.md`, (b) invokes the external CLI in the
   worktree, (c) emits a `WORKER_REPORT.md` containing the machine manifest
   (reusing `bin/agent.py` `_machine_manifest()`, 377, which needs only
   `(workdir, base, gate_triggers)`), and (d) emits a transcript JSONL in the
   event vocabulary `bin/grade_run.py` `grade()` (204) already parses — so
   grading, the ledger, the retry guard and the weekly tune keep working
   unchanged.
2. **The plan/route seam is already in place.** A `{"kind":"cli","harness":
   "opencode"}` route is expressible in `config/deployment.local.json` and
   resolvable through `bin/plans.py` `resolve_dispatch()` (278); `cli_invocation()`
   (330) already names the generic shape `[harness, "--model", model]`. The
   adapter replaces the `bin/plan_cli_stub.py` stub for that one harness.
3. **Supervision stays where it already is.** The supervisor prompt gains one
   dispatch branch (launch the adapter instead of `bin/agent.py`) and one
   collection rule (read the external round's report/manifest exactly as it
   reads a worker's); `workers.json` gains a "held by external agent" status so
   the "never two workers on the same issue" rule and the single-writer rule
   stay enforceable; `worker_ledger.jsonl` gains rows with the same
   `repo`/`issue`/`outcome` keys so `bin/ledger_check.py` `check()` (32) and
   `bin/failover.py` (162) apply unchanged; the PR is registered with the
   shepherd by the same `shepherd.json` `tracked_prs` append as step 7.
4. **All guardrails in §5 carry over unchanged**, except two acknowledged
   holes: (a) the per-run token/step cap has no binding until the CLI exposes a
   budget knob (**UNVERIFIED** — the probe must answer it), and (b) the
   empty-diff / mid-run gates are prompt injections into a model the supervisor
   does not own, so for external rounds they degrade to *post-hoc
   classification* (`failed:empty-diff` at collect time) instead of *prevention*.

**The deciding evidence is the P1/P3 probe**, not this document. Specifically:
one real round with a captured timeline of (exit code, session-file lifecycle,
tree state, publication events) that satisfies P3 items 1–3.

**Named blocker if the probe fails.** If P3 item 1 fails — i.e. the external CLI
offers **no machine-checkable completion signal** that the supervisor can read
without asking the agent — then the design is **No-go**, blocked on
*"unobservable round completion"*: the supervisor would have to treat "the tree
looks different" or "a PR appeared" as proof that the round ended, which is
exactly the class of guess the repo forbids ("Prose is not evidence",
`docs/ARCHITECTURE.md`; "trust git", `prompts/backlog-supervisor.md` step 6).
If P3 item 2 fails (the diff is not git-verifiable against a pinned base) the
verdict is **No-go**, blocked on *"unverifiable external diff"*, because the
merge policy's hard stop "anything the shepherd cannot verify is correct"
(`prompts/pr-shepherd.md` MERGE POLICY) would then block every external PR by
construction.

**Honest limits of this investigation.**

* No OpenCode behaviour was verified in this run: the sandbox had no network
  (`curl` exit 35 on every attempt) and the hard stops forbid installing or
  running any external CLI agent, touching any account/credential, or accessing
  any desktop machine. Every OpenCode mechanism above is therefore labelled
  **UNVERIFIED** and is the target of §6.
* The strongest plan-coder claims in this document were re-checked by reading
  the cited files; the API/CLI behaviour claims are limited to what the repo's
  own docs and code state (e.g. `README.md:92` explicitly disclaims any
  OpenCode runner existing here).
* `bin/plan_cli_stub.py` shows the repo's intended *shape* for a CLI harness
  (record the invocation, never execute it) but proves nothing about OpenCode's
  own CLI.
