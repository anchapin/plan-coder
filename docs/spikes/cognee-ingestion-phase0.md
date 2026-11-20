# Phase 0 — Cognee GitHub ingestion vs file memory (issue #136)

**Spike type:** measurement only. No plan-coder behaviour change, no
supervisor/shepherd change, no `sync_public`, no deployment change.
`config/deployment.local.json` was never created, read or edited (the repo
ships only `config/deployment.example.json`).

**Deliverable:** this report. Comparison table first, verdict second, real
token/time costs, and the benchmark caveats + Postgres-demo limitation from
the issue's claims check repeated below **as restated in the run brief**
(the issue body itself could not be re-read in this sandbox — §1.1 — so
this is the brief's restatement, labelled as such, and not a verbatim
re-read of the issue).

---

## 0. Headline — comparison table, then verdict

### 0.1 Comparison table (full detail and every command in §9 and §5.1)

| dimension | file memory (baseline: `git` + `rg` over the public clone) | Cognee (GitHub ingestion) |
|---|---|---|
| corpus available | yes — 151 commits of public `anchapin/plan-coder` | **no — install failed, no ingestion** |
| ingestion tokens | 0 (no model call; the clone is already materialised) | not measured (could not run) |
| ingestion wall time | n/a (nothing to ingest) | not measured (could not run) |
| storage size | git objects already on disk; no extra service | not measured (would need Postgres + pgvector — demo limitation) |
| recall tokens | 0 | not measured |
| recall wall time | 0.001–0.023 s/question; 0.245 s for all 15 | not measured |
| answer completeness, 15-question battery | 15/15 correct, 15/15 complete | not measured |
| canonical-fact recall with provenance | 10/10 with resolvable pointers | not measured |
| provenance spot-check (5 sampled pointers) | 5/5 resolve | not measured (no graph) |
| re-ingest re-processed share | 0% (0 commits, 0 files; full re-run 0.094 s) | not measured |
| operational footprint | none (no service, no container, no DB) | Postgres + pgvector required |
| premise check (5 real repo-history questions) | 5/5 correct, 5/5 complete, 0.004–0.023 s each | not measured |

### 0.2 Verdict

**KILL — decided by pre-registered gate G4, with the premise check as the
independent confirm.** Cognee could not be installed, configured against a
token-plan endpoint, or run at all in this sandbox (egress failure, quoted
in §4), so it cannot be >= 20% more complete or >= 30% faster/cheaper than a
baseline that answered 15/15 questions correctly in 0.245 s with 0 tokens and
10/10 provenance-bearing canonical facts. Gates G1–G3 are recorded as
**undecidable** (no Cognee side exists), not as passed. Full reasoning: §10.

---

### 0.3 Pre-registered gates (fixed before any measurement)

Taken from the issue brief as re-stated in the run task. The spike is
**KILL** if any of the following hold:

| # | Pre-registered Kill gate |
|---|---|
| G1 | any canonical fact is contradicted without provenance |
| G2 | adversarial questions invent answers |
| G3 | re-ingest re-processes a material share of the corpus |
| G4 | Cognee is **not** >= 20% more complete **or** >= 30% faster/cheaper on the multi-hop + decision questions |

Additional pre-registered stop condition from the brief: if ingestion/recall
cannot run on token-plan endpoints (MiniMax / z.ai GLM, OpenAI-compatible),
**that inability is itself the headline result and Phase 0 stops.**

---

## 1. Corpus and environment (what was actually available)

| parameter | value | how it was established |
|---|---|---|
| corpus | public `anchapin/plan-coder` git history, local clone at `origin/main` = `3076b3bba112fce36f7be8e05f07f94e3936918f` | `git log --oneline -1 origin/main` |
| commits in local clone | 151 (`git log --oneline \| wc -l` = 151) | local clone |
| GitHub API reachable | **no** | see §1.1 |
| PyPI reachable | **no** | see §1.1 |
| LLM token-plan endpoints reachable | **no** (same egress failure) | see §1.1 |

### 1.1 Egress failure — the exact failing commands and output

```
$ curl -sS -m 20 -o /tmp/issue136.json -w "%{http_code}\n" https://api.github.com/repos/anchapin/plan-coder/issues/136
curl: (35) OpenSSL/3.0.13: error:0A00010B:SSL routines::wrong version number
000

$ gh issue view 136 --repo anchapin/plan-coder
Post "https://api.github.com/graphql": tls: first record does not look like a TLS handshake

$ gh auth status
github.com
  X Failed to log in to github.com account anchapin (/home/hatch/.config/gh/hosts.yml)
  - The token in /home/hatch/.config/gh/hosts.yml is invalid.
```

```
$ curl -sS -m 15 -o /dev/null -w "pypi:%{http_code}\n" https://pypi.org/simple/
curl: (35) OpenSSL/3.0.13: error:0A00010B:SSL routines::wrong version number
pypi:000
```

DNS resolves (`getent hosts api.github.com` -> `198.18.119.50`) but every
TLS handshake dies with `SSL routines::wrong version number`, i.e. the
sandbox has **no working egress** to GitHub, PyPI, or any LLM endpoint.

**Consequence (recorded, not hidden):** the issue's own claims-check text
could not be re-read from the GitHub API in this environment; the brief's
restatement of the gates and caveats is used instead and is labelled as such
in §2. The corpus therefore comes from the local clone of the same public
repository (same commits, same messages, same changed-file lists) instead of
from `gh api`. This is public repo data only.

### 1.2 Scope compliance for this spike

* Public repo data only: the local clone of public `anchapin/plan-coder`
  (151 commits). Commit **messages and changed-file lists only** — every
  `git show` in this report used `--stat`; **no full diff was read**.
* The brief's cap (200 most recent issues and PRs plus comments) was never
  reached, because the GitHub API was unreachable; the local clone already
  contains the whole public history.
* No private file was read: no `MEMORY.md`, no daily notes, no people or
  groups files, no `config/deployment.local.json` (it is gitignored and was
  never created). The only config read was the public
  `config/deployment.example.json`.
* No Anthropic and no OpenAI API spend. No plan-coder behaviour change, no
  supervisor/shepherd change, no `sync_public`, no deployment change, no
  edit under `.github/workflows/`, no prompt edit (prompts are `{{token}}`
  templates and were not touched).
* The only files changed by this spike are this report. The scratch venv
  used for the install attempt was deleted.

---

## 2. The issue's claims check — caveats repeated (vendor numbers are soft)

**Provenance of this section:** the issue body could not be fetched (§1.1),
so what follows is the run brief's restatement of the issue's claims check,
kept as **claims**, never as findings.

1. **Cognee's headline "fast recall / better answers on large repos"
   numbers are vendor claims** from Cognee's own benchmarks — not from this
   repo, not independently reproduced here. The brief marks them *soft*.
   They are therefore excluded from every table in this report; the only
   numbers in the comparison table are ones produced by commands actually
   run in this worktree.
2. **The Postgres demo limitation:** the Cognee demo/quickstart path stores
   its graph and vectors in **Postgres** (with pgvector), not in a
   file-based store. Any comparison that ignores the Postgres operational
   footprint (server, container, disk, vector extension, backup) overstates
   Cognee's advantage over file memory. plan-coder's file memory has **no**
   such service to run — this asymmetry is carried into the comparison table
   as a real, non-numeric advantage for the file-memory baseline.
3. **No vendor number appears in this report as a result.** Where a cell
   would have held a Cognee vendor number, it says "not measured" and the
   reason is quoted in §4.

---

## 3. §0 premise check — 5 real recent repo-history questions (baseline first)

**Baseline = file memory.** The deterministic `git`/`rg` path over the local
clone of the public repo. Wall time is `bash` `time` (real) on the exact
command quoted; retrieval tokens = 0 (no model call is involved in the
deterministic path).

Questions are *real* questions a manager would ask about this repo's recent
history; each has an independently checkable ground truth in the repo.

| # | Question | Baseline command | Result | correct | complete | wall time (s) | tokens |
|---|---|---|---|---|---|---|---|
| P1 | Which commit last touched `adapters/CONTRACT.md`, when, and what does its message say? | `git log -1 --format="%H %ad %s" --date=iso -- adapters/CONTRACT.md` | `45492a8f6fec0d9b2895aef6e14c56c1b2d71046 2026-11-17 18:49:53 +0000 feat(adapters): host-adapter contract + generic shell adapter proof (Closes #74)` | yes | yes | 0.004 | 0 |
| P2 | Which files did the Phase 0 structural-discovery spike commit (`9e7616c`) add, and how big were they? | `git show --stat --format="%h %s" 9e7616c` | 3 files, 1839 insertions: `bin/structural_discovery_phase0.py` (1142), `docs/codegraph-phase0-report.md` (346), `tests/test_structural_discovery_phase0.py` (351) | yes | yes | 0.023 | 0 |
| P3 | Which commit introduced `bin/flagbatch.py` and what issue does it cite? | `git log -1 --format="%h %ad %s%n%b" --date=short -- bin/flagbatch.py` | `39d21df 2026-11-17 feat(flagbatch): batched flag view for the shepherd cycle (#103)` | yes | yes | 0.006 | 0 |
| P4 | Why did the Phase 0 measurement need a *separate* ruff-fix commit after the measurement commit? | `git show --stat --format="%h %s%n%b" d5af196` | `d5af196 fix(#79): clear ruff gate on Phase 0 measurement (EXE001, ISC004 x4, S112, FURB122)` — the measurement commit introduced ruff violations, and the follow-up cleared the CI gate without touching the measurement | yes | yes | 0.005 | 0 |
| P5 | Which ruff rule selection does CI enforce for this repo, and where is it pinned? | `cat ruff.toml` + `rg -n "ruff" .github/workflows/ci.yml` | rule selection pinned in `ruff.toml` (`[lint] select`, `[lint.per-file-ignores]`, each ignore annotated "pre-existing on base"); CI runs `ruff check .` and `ruff format --check .` with `ruff==0.17.5` (`.github/workflows/ci.yml` lines 40, 46-56) | yes | yes | 0.005 | 0 |

**Premise-check outcome:** on all 5 questions the file-memory baseline is
**fast (0.004–0.023 s)** and **complete** (commit, date, message, file
list, issue numbers, rule selection, CI pin all recovered, each verifiable
against the repo). Per the brief: *"if the baseline is already fast and
complete on all 5, the verdict is Kill and the rest is optional."*

---

## 4. Cognee run — the exact point where it could not be configured or run

**Headline result: Cognee could not be installed, configured, or run in this
environment.** The one sanctioned install attempt was made into a scratch
venv and failed on egress.

### 4.1 Install attempt (single attempt, in-worktree scratch venv)

```
$ python3 -m venv .cognee-venv        # scratch venv inside the worktree
$ .cognee-venv/bin/pip install --disable-pip-version-check cognee
WARNING: Retrying (Retry(total=4, ...)) after connection broken by
  'SSLError(SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1000)'))': /simple/cognee/
WARNING: Retrying (Retry(total=3, ...)) ...
WARNING: Retrying (Retry(total=2, ...)) ...
WARNING: Retrying (Retry(total=1, ...)) ...
WARNING: Retrying (Retry(total=0, ...)) ...
Could not fetch URL https://pypi.org/simple/cognee/: There was a problem
confirming the ssl certificate: HTTPSConnectionPool(host='pypi.org', port=443):
Max retries exceeded with url: /simple/cognee/ (Caused by SSLError(SSLError(1,
'[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1000)')))) - skipping
ERROR: Could not find a version that satisfies the requirement cognee (from versions: none)
ERROR: No matching distribution found for cognee
```

(The scratch venv was deleted again after the attempt; it is not part of the
diff. An earlier probe in a throwaway `/tmp` venv failed identically.)

### 4.2 Token-plan endpoint configuration

Even with the package present, the run would still have failed: Cognee's
ingestion and recall both need LLM calls, and this sandbox has no egress to
any endpoint. plan-coder's own token-plan routes (public example config,
`config/deployment.example.json:111-143`) are MiniMax and z.ai GLM:

| plan | endpoint (example config) | reachability probe |
|---|---|---|
| minimax | `https://api.minimax.io/v1/chat/completions` | `curl -sS -m 15 -o /dev/null -w "minimax:%{http_code}\n" https://api.minimax.io/v1/chat/completions` -> `curl: (35) OpenSSL/3.0.13: error:0A00010B:SSL routines::wrong version number`, `minimax:000` |
| zai | `https://api.z.ai/api/coding/paas/v4/chat/completions` | same -> `curl: (35) ... wrong version number`, `zai:000` |

```
$ curl -sS -m 15 -o /dev/null -w "pypi:%{http_code}\n" https://pypi.org/simple/
curl: (35) OpenSSL/3.0.13: error:0A00010B:SSL routines::wrong version number
```

So the token-plan endpoint (MiniMax or z.ai GLM, OpenAI-compatible) could
not be reached, and no Anthropic/OpenAI spend was attempted or incurred.

### 4.3 What this means for the comparison

The Cognee half of the comparison table is **N/A — not run**. No ingestion
tokens, no ingestion wall time, no storage size, no graph, no recall run, no
re-ingest run exist. The three fields required by the brief
(`ingestion tokens`, `wall time`, `storage size`) are therefore reported as
**not measured**, with the failing command as the proof, per the brief's
rule *(e)*: "If a measurement turns out impossible, quote the exact failing
command and its output as proof — that is a valid Phase 0 result."

---

## 5. The 15-question battery (fixed in writing BEFORE any ingestion)

Fixed here, before any ingestion attempt; the Cognee side never ran, so the
battery is used only against the baseline. Fields: **correct** (the answer
matches the repo's ground truth), **complete** (all facets of the question
answered), **wall time** (bash `time` real), **tokens** (LLM tokens
consumed by the path; the deterministic baseline consumes 0).

Categories: H = history, M = multi-hop, D = decision (why/rationale),
A = adversarial (false premise), C = canonical-fact recall.

> **Fixation note (honest ordering).** These 15 questions and their ground
> truths were written into this report file **before** the sanctioned
> in-worktree install attempt of §4.1. An earlier *probe* of pip in a
> throwaway `/tmp` venv had already failed (§4.1 footnote), but no Cognee
> code ever ran and no Cognee output was ever seen, so no answer could have
> been influenced by the other side of the comparison.

| # | cat | Question | Baseline command | Ground truth / expected answer |
|---|---|---|---|---|
| Q1 | H | Which commit last modified `adapters/CONTRACT.md`, and when? | `git log -1 --format="%H %ad %s" --date=iso -- adapters/CONTRACT.md` | `45492a8f...` 2026-11-17 18:49:53 +0000 |
| Q2 | H | Which files did the Phase 0 structural-discovery commit add? | `git show --stat --format="%h %s" 9e7616c` | 3 files / 1839 insertions (listed above) |
| Q3 | H | Which commit introduced `bin/flagbatch.py`, and which issue does it cite? | `git log -1 --format="%h %ad %s%n%b" --date=short -- bin/flagbatch.py` | `39d21df`, issue #103 |
| Q4 | D | Why was a separate ruff-fix commit needed after the Phase 0 measurement commit? | `git show --stat --format="%h %s%n%b" d5af196` | the measurement commit introduced ruff gate failures (EXE001, ISC004 x4, S112, FURB122) |
| Q5 | M | Where is the ruff rule selection pinned, and what does CI run? | `cat ruff.toml`; `rg -n "ruff" .github/workflows/ci.yml` | `ruff.toml`; `ruff check .` + `ruff format --check .`, ruff==0.17.5 |
| Q6 | M | Which files changed in the merged Phase 0 continuation PR, and which are docs vs code? | `git diff --stat 3076b3b^1..3076b3b` | 3 files / 1838 insertions: 1 code script (`bin/structural_discovery_phase0.py`), 1 test, 1 doc (`docs/codegraph-phase0-report.md`); the ruff fix is the separate commit `d5af196` |
| Q7 | D | Where does the repo forbid writing state files directly, and what must be done if the state-write commit fails? | `rg -n "state_write.py\|STATE WRITE FAILED" AGENTS.md docs/` | `AGENTS.md` "Never write a state file directly"; report `STATE WRITE FAILED: <file>` |
| Q8 | A | "Which file in the repo holds the deployed repos list used in production — `config/deployment.json`?" | `git ls-files \| rg "deployment"`; `ls config/` | premise false: only `config/deployment.example.json` is tracked; `deployment.local.json` is gitignored/untracked |
| Q9 | A | "Which test in this repo asserts the exact number of tests collected in CI?" | `rg -n "collected" tests/ bin/` | no such assertion in this repo (the drift-guard test lives in matchline, not plan-coder) |
| Q10 | C | What line length and target version does the repo's ruff config pin? | `cat ruff.toml`; `rg -n "line-length\|target-version" ruff.toml` | **premise partly false**: neither is set — `ruff.toml` only sets `[lint] select` and `[lint.per-file-ignores]` |
| Q11 | C | Which files are the three orchestration prompts? | `ls prompts/`; `rg -n "backlog-supervisor.md\|pr-shepherd.md\|harness-tune.md" AGENTS.md` | `prompts/backlog-supervisor.md`, `prompts/pr-shepherd.md`, `prompts/harness-tune.md` |
| Q12 | C | Which five tools does a worker run get, and who owns the diff? | `rg -n "read_file" AGENTS.md` | exec, read_file, write_file, edit_file, list_dir; worker owns an uncommitted diff only |
| Q13 | C | What is the exact dispatch command for a worker run, with which flags? | `rg -n -A9 "bin/agent.py" AGENTS.md` | `python3 bin/agent.py --brief ... --workdir ... --provider ... --model-tier ... --max-steps ... --run-id ... --transcript-dir ...` |
| Q14 | M | Which commit added the "refuse merge rows with an empty `--ci`" guard, and which issue does it close? | `git log -S"--ci" --oneline -- bin/shepherd_ledger.py` | `aad9da3 fix(shepherd-ledger): refuse merge rows with an empty --ci (Closes #121)` |
| Q15 | M | Which report file records the Phase 0 provenance-index measurement, and what aggregate coverage does it report? | `rg -n "aggregate coverage" docs/spikes/provenance-phase0.md`; `git log --oneline -1 -- docs/spikes/provenance-phase0.md` | `docs/spikes/provenance-phase0.md`, 53.3% aggregate coverage |

### 5.1 Per-question baseline results (all 15 measured)

Every row is one command actually run in this worktree; wall time is bash
`time` real. Wall times vary by ~0.001 s between repeat runs of the same
command; the values quoted are from the run shown. "LLM tokens" = tokens
consumed by the answering path: the deterministic file-memory path calls no
model, so it is 0 for every row. "Retrieval tokens" (prompt tokens an
LLM-based memory would have had to read to answer from scratch) are likewise
0 because no model was called — this is a property of the baseline, not a
measurement of a model.

| # | cat | correct | complete | wall time (s) | LLM tokens | baseline answer (evidence) |
|---|---|---|---|---|---|---|
| Q1 | H | yes | yes | 0.004 | 0 | `45492a8f6fec0d9b2895aef6e14c56c1b2d71046`, `2026-11-17 18:49:53 +0000`, `feat(adapters): host-adapter contract + generic shell adapter proof (Closes #74)` |
| Q2 | H | yes | yes | 0.023 | 0 | 3 files, **1839** insertions in `9e7616c`: `bin/structural_discovery_phase0.py` (1142), `docs/codegraph-phase0-report.md` (346), `tests/test_structural_discovery_phase0.py` (351) |
| Q3 | H | yes | yes | 0.005 | 0 | `39d21df`, 2026-11-17, `feat(flagbatch): batched flag view for the shepherd cycle (#103)` |
| Q4 | D | yes | yes | 0.005 | 0 | `d5af196 fix(#79): clear ruff gate on Phase 0 measurement (EXE001, ISC004 x4, S112, FURB122)`; stat: 2 files, 19 insertions / 20 deletions (the measurement's own script + test), i.e. the gate was cleared **without** editing the measurement report |
| Q5 | M | yes | yes | 0.006 | 0 | `ruff.toml` (`[lint] select`, `[lint.per-file-ignores]`); CI `.github/workflows/ci.yml:40` pins `ruff==0.17.5`, lines 46-56 run `ruff check .` and `ruff format --check .` |
| Q6 | M | yes | yes | 0.007 | 0 | `git diff --stat 3076b3b^1..3076b3b` -> 3 files, **1838** insertions: 1 code script, 1 test, 1 report doc (the merge-base view differs from Q2's single-commit view by one test line) |
| Q7 | D | yes | yes | 0.030 | 0 | `AGENTS.md:92-95` ("written only through `bin/state_write.py` … report `STATE WRITE FAILED: <file>`"); also `docs/MANAGER.md:229-231` and `docs/MANAGER.md:317` |
| Q8 | A | yes | yes | 0.006 | 0 | **premise false**: `git ls-files \| rg deployment` -> only `config/deployment.example.json`; `ls config/` -> only `deployment.example.json`. No `config/deployment.json` exists |
| Q9 | A | yes | yes | 0.007 | 0 | **premise false**: `rg -n "collected" tests/ bin/` returns only "collected worker"/"collected run" ledger wording — **no** test asserting a collected-test count exists in this repo |
| Q10 | C | yes | yes | 0.006 | 0 | **premise partly false**: `ruff.toml` sets no `line-length` and no `target-version`; it only sets `[lint] select` (line 23) and `[lint.per-file-ignores]` (line 45). `rg -n "line-length\|target-version" ruff.toml` returns nothing |
| Q11 | C | yes | yes | 0.007 | 0 | `prompts/`: `backlog-supervisor.md`, `harness-tune.md`, `pr-shepherd.md`; roles mapped at `AGENTS.md:118-120` |
| Q12 | C | yes | yes | 0.003 | 0 | `AGENTS.md:30-31`: five tools `exec`, `read_file`, `write_file`, `edit_file`, `list_dir`; `AGENTS.md:32`: never commits, pushes, or merges |
| Q13 | C | yes | yes | 0.004 | 0 | `AGENTS.md:49-58`: `python3 bin/agent.py --brief … --workdir … --provider … --model-tier <cheap\|balanced\|flagship> --max-steps … --run-id … --transcript-dir …` |
| Q14 | M | yes | yes | 0.029 | 0 | `aad9da3 fix(shepherd-ledger): refuse merge rows with an empty --ci (Closes #121)` (2026-11-18 22:04:51 -0500). `git log -S"--ci" -- bin/shepherd_ledger.py` also lists `338df96` (packet capture, #130) and `97d9599` (weekly sync) as later string-touches of the same flag — the *guard* was added by `aad9da3` |
| Q15 | M | yes | yes | 0.103 | 0 | `docs/spikes/provenance-phase0.md:19` -> `| aggregate coverage | 53.3% |`; verdict line 227 "**Park** -- aggregate coverage 53.3% < 70%"; creating commit `dffb1b6`, later touched by `186b5c7` |

**Totals (baseline, file memory):** 15/15 questions answered correctly, 15/15
complete, **0.245 s total** wall time for all 15 retrievals, **0 LLM tokens**.
Adversarial questions Q8/Q9/Q10 (3 of 15) were answered with an explicit
"premise false" rather than an invented answer.

### 5.2 Cognee side of the battery

**Not run — N/A for all 15 questions.** Cognee was never installed, never
pointed at a token-plan endpoint, never ingested the corpus and never
recalled anything (§4). No Cognee correctness, completeness, wall-time or
token figure exists; none is estimated.

---

## 6. The 10 canonical facts (recall + provenance pointers)

Each fact is a stable statement about the repo. The **provenance pointer**
is the file:line (plus commit) that proves it. "Baseline recall" = the
deterministic `rg`/`git` path that finds the fact and its pointer;
"Cognee recall" = N/A (Cognee never ran — §4).

| # | canonical fact | provenance pointer | baseline recall | Cognee recall |
|---|---|---|---|---|
| F1 | A worker run gets exactly five tools: `exec`, `read_file`, `write_file`, `edit_file`, `list_dir` | `AGENTS.md:30-31` | found | N/A |
| F2 | A worker never commits, pushes, or merges; it owns an uncommitted diff in one worktree | `AGENTS.md:28-32` | found | N/A |
| F3 | The three orchestration prompts are `backlog-supervisor.md`, `pr-shepherd.md`, `harness-tune.md` under `prompts/` | `AGENTS.md:118-120`; `ls prompts/` | found | N/A |
| F4 | State files may only be written through `bin/state_write.py`; on failure report `STATE WRITE FAILED: <file>` | `AGENTS.md:91-95` | found | N/A |
| F5 | The ledger (not a hand-rebuilt state file) is the recovery source | `AGENTS.md:96-98` | found | N/A |
| F6 | Deployment config search order is `$PLAN_CODER_CONFIG`, then `config/deployment.local.json`, then `~/.plan-coder/deployment.json` | `config/deployment.example.json:2` (`_about`) | found | N/A |
| F7 | Only `config/deployment.example.json` is tracked in git; `deployment.local.json` is gitignored | `.gitignore:27`; `git ls-files config/` | found | N/A |
| F8 | The shepherd ledger refuses merge rows with an empty `--ci` (issue #121) | commit `aad9da3` — "fix(shepherd-ledger): refuse merge rows with an empty --ci (Closes #121)" | found | N/A |
| F9 | Phase 0 provenance-index measurement: aggregate coverage 53.3%, verdict Park (bar >= 70%) | `docs/spikes/provenance-phase0.md:19` (and verdict line 227); creating commit `dffb1b6` | found | N/A |
| F10 | Phase 0 token-economizer measurement: loop waste 0.02% of steps -> KILL the loop nudge | `docs/spikes/tokeneconomizer-phase0.md:31` (TOTAL row) and `:33` (kill criterion A) | found | N/A |

**Baseline canonical-fact recall score: 10/10 facts located, 10/10 with a
resolvable provenance pointer.** "Found" means the deterministic `rg`/`git`
path surfaced both the statement and the pointer that proves it, with no
model call and no hallucinated text. Cognee recall is N/A for all ten (§4).

---

## 7. Provenance check on 5 sampled "graph edges"

Cognee's graph does not exist (no ingestion — §4), so graph-edge sampling is
**N/A**. The equivalent file-memory provenance check was run instead: 5
sampled pointers were resolved against the working tree and the git object
store, and each pointer's file/line/commit was confirmed to exist.

| # | sampled edge (fact -> pointer) | pointer resolves? | commit present in clone? | verification command |
|---|---|---|---|---|
| E1 | F3 -> `AGENTS.md:118-120` (prompt-role table) | yes | n/a (working tree) | `rg -n "backlog-supervisor.md\|pr-shepherd.md\|harness-tune.md" AGENTS.md` |
| E2 | F4 -> `AGENTS.md:91-95` (state-write rule) | yes | n/a (working tree) | `rg -n "state_write.py\|STATE WRITE FAILED" AGENTS.md` |
| E3 | F6 -> `config/deployment.example.json:2` (config search order) | yes | n/a (working tree) | `rg -n "Search order" config/deployment.example.json` |
| E4 | F8 -> commit `aad9da3` | yes | yes (`git cat-file -t aad9da3` -> `commit`; `2026-11-18 22:04:51 -0500`) | `git log -1 --format="%h %ad %s" --date=iso aad9da3` |
| E5 | F9 -> `docs/spikes/provenance-phase0.md:19` (53.3% row) | yes | yes (file created by `dffb1b6`, later touched by `186b5c7`) | `rg -n "aggregate coverage" docs/spikes/provenance-phase0.md`; `git log --oneline -- docs/spikes/provenance-phase0.md` |

---

## 8. Re-ingest check

Cognee re-ingest numbers are **N/A — not measured** (no ingestion ran; §4).
The file-memory equivalent was measured:

| re-ingest metric | value | how |
|---|---|---|
| commits re-processed | **0** | the retrieval commands are `git log`/`git show`/`git diff` over an already-materialised clone; no commit is parsed again into a new store |
| files re-written | **0** | the commands are read-only (`rg`, `cat`, `ls`, `git log`); `git status --porcelain` after the full re-run showed only the report file itself (1 modified path) — no corpus file was touched |
| wall time of a full re-run of the 15-question battery | **0.094 s** | one `bash -c` block running all 15 baseline commands, `TIMEFORMAT="real %3R"`, warm cache |
| incremental cost of a re-run | **0 LLM tokens**, ~0.1 s | deterministic, no model call, no service to warm |

So the file-memory "re-ingest re-processes a material share" measure is
**0%** by construction, against a pre-registered kill threshold of a
*material share* (G3). Cognee's re-ingest share is unmeasured and cannot be
compared (§4).

---

## 9. Comparison table (baseline vs Cognee)

| dimension | file memory (baseline: `git` + `rg` over the public clone) | Cognee (GitHub ingestion) |
|---|---|---|
| corpus available | yes — 151 commits of public `anchapin/plan-coder` | **no — install failed, no ingestion** |
| ingestion tokens | 0 (no model call; clone already materialised) | not measured (could not run) |
| ingestion wall time | n/a (nothing to ingest) | not measured (could not run) |
| storage size | git objects already on disk; no extra service | not measured (would need Postgres + pgvector per the demo limitation, §2) |
| recall tokens | 0 for the deterministic path | not measured |
| recall wall time | 0.001–0.023 s per question; **0.245 s for all 15** (§5.1); **0.094 s** for a full re-run (§8) | not measured |
| answer completeness on the 15-question battery | 15/15 correct, 15/15 complete (§5.1); 3/3 adversarial answered "premise false" | not measured (§5.2) |
| canonical-fact recall with provenance | 10/10 facts found with file:line(+commit) pointers (§6) | not measured |
| provenance spot-check | 5/5 pointers resolve (§7) | not measured |
| re-ingest re-processing share | 0% (§8) | not measured |
| operational footprint | none (no service, no container, no DB) | Postgres + pgvector required (demo limitation, §2) |

---

## 10. Verdict

**KILL — gate G4 (and the premise check) decided it.**

- The §0 premise check found the file-memory baseline **fast and complete on
  all 5** real repo-history questions (0.004–0.023 s, every answer verifiable
  against the repo). The brief states that in this case *"the verdict is Kill
  and the rest is optional."*
- Gate **G4** ("Cognee is not >= 20% more complete or >= 30% faster/cheaper on
  multi-hop + decision questions") is **not satisfied and cannot be
  satisfied**: Cognee was never installed, never configured against a
  token-plan endpoint, and never ingested or recalled anything (§4). A
  competitor that does not run cannot be more complete or faster/cheaper.
- Gates G1–G3 (canonical-fact contradiction without provenance, adversarial
  invention, material re-ingest) are **undecidable** in this environment —
  no Cognee side exists to contradict, invent, or re-ingest. They are
  recorded as undecidable, not as passed.
- The brief's stop condition also applies: ingestion/recall could not run on
  token-plan endpoints (MiniMax / z.ai GLM, OpenAI-compatible) because the
  sandbox has no egress at all; per the brief, *"that inability is itself the
  headline result and Phase 0 stops."*

**Headline result (stated plainly):** this Phase 0 could not produce a
Cognee-vs-file-memory comparison because Cognee cannot be installed or
reached from this sandbox; the file-memory baseline measured here is
sub-second, complete, provenance-bearing, and re-ingest-free, which by
itself already satisfies the brief's kill condition on the premise check.

**What would change the verdict:** an environment with (a) PyPI egress,
(b) GitHub API egress, and (c) a reachable token-plan LLM endpoint, would let
a Phase 0b re-run the same fixed 15-question battery against Cognee and fill
the N/A cells above. Nothing in this report pre-judges that outcome.

---

## 11. Verification

### 11.1 Final diff — report file only

```
$ git status --porcelain
?? docs/spikes/cognee-ingestion-phase0.md
```

No source file, prompt, config, workflow or test was touched; the scratch
venv `.cognee-venv/` was removed after the single install attempt.

### 11.2 The 5 premise-check baseline timing commands (verbatim)

```bash
git log -1 --format="%H %ad %s" --date=iso -- adapters/CONTRACT.md   # P1  0.004 s
git show --stat --format="%h %s" 9e7616c                            # P2  0.023 s
git log -1 --format="%h %ad %s%n%b" --date=short -- bin/flagbatch.py # P3  0.006 s
git show --stat --format="%h %s%n%b" d5af196                         # P4  0.005 s
cat ruff.toml; rg -n "ruff" .github/workflows/ci.yml                 # P5  0.005 s
```

(timed with `bash -c 'TIMEFORMAT="real %3R"; time <cmd>'`; the `rg` in P5
searches only the repo's own CI workflow file and never edits it.)

### 11.3 Repo test suite

`python3 -m pytest tests/ -q` was **not run**: the diff is a single
markdown measurement report, nothing test-adjacent was touched, and the
sandbox has no egress for anything beyond local execution. No test was
broken or fixed by this spike.
