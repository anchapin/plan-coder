# Warm verifier — Phase 0 cold-start census (issue #154)

**Status: Phase 0 measurement only.** This document and the tool it describes
(`bin/warm_verifier_census.py`) *measure* cold start. They do not build a pooled
verifier, a verifier server, an MCP wrapper, a prompt, a config change, or any
harness behaviour. Phase 1 (any pooled-verifier design or implementation) is
gated on **both** the census gate below **and** a separate owner approval.

**The live numbers are not in this document.** Section 6 is a PENDING section for
the operator to fill in from one command. Nothing here is a live measurement.

---

## 1. The question (issue #154, quoted)

> For recent verification-heavy spike and worker runs, split wall-clock into cold
> start (environment / solver / service startup), actual verification work, and
> teardown; report cold-start share per verifier type and runs/week; name the one
> verifier type with the highest aggregate cold-start cost.

**Gate (issue #154, quoted):**

> Proceed to a Phase 1 design only if cold start is >= 25% of aggregate
> verification wall-clock for the top candidate AND the candidate is invoked
> >= 10 times/week.

**Kill clause (issue #154, quoted):**

> Kill clause: if cold start is < 10% everywhere, the census itself is the
> deliverable and the line closes.

The three thresholds are code constants in `bin/warm_verifier_census.py`
(`GATE_COLD_START_SHARE_PCT = 25.0`, `GATE_RUNS_PER_WEEK = 10.0`,
`KILL_COLD_START_SHARE_PCT = 10.0`) and are pinned by
`tests/test_warm_verifier_census.py` for all three verdicts
(`proceed` / `kill` / `below_gate`).

---

## 2. Method

### 2.1 Input

One transcript per worker/spike run, in the format `bin/agent.py` writes:
a `started` event, one JSONL record per step (`ts`, `duration_ms`, `tool_calls`
with `{"name": "exec", "args": "{\"command\": ...}"}`), and a `finished` event.
The tool reads the directory read-only and writes nothing. Zero LLM tokens: all
classification is deterministic pattern matching over the recorded shell
commands.

### 2.2 Window

`--since` / `--until` (ISO-8601) bound the window; a run is included when its
start timestamp falls inside it. When the flags are omitted the window defaults
to the corpus extent (earliest run start .. latest run end) and the JSON says
`"source": "corpus"`. `runs_per_week` = runs in the window / window length in
weeks (a window shorter than one day is counted as one day).

### 2.3 Buckets (per run)

| bucket | meaning |
|---|---|
| `cold_start` | environment / solver / service startup — the part a warm verifier would amortise |
| `verification` | the actual verification work (test suites, solvers, checkers) |
| `teardown` | stopping services and cleaning up |
| `other` | everything else (edits, reading, chat-only steps, commands matching no pattern) |

A step that matches nothing is `other`. It is **never** silently folded into
cold start.

### 2.4 Step classification (deterministic, fixed precedence)

1. a step whose commands match a **cold-start** pattern → `cold_start`
2. else a step whose commands match a **verification** pattern → `verification`
3. else a step whose commands match a **teardown** pattern → `teardown`
4. else → `other`

Step duration is `agent.py`'s `duration_ms` (the chat round plus the tool
execution), falling back to the gap since the previous record when a transcript
carries no `duration_ms`. Run wall-clock is the span from the `started` event to
the `finished` event.

### 2.5 The cold-start interval

Cold start carries the issue's *"time from run start to the first non-setup
action"* interval **plus** the exec steps matching explicit setup patterns.
Because that interval already contains the leading setup steps, only setup steps
that happen *after* the first non-setup action are added on top of it — the two
terms never double count. Both parts are reported separately per run
(`cold_start_gap_seconds`, `cold_start_setup_seconds`).

### 2.6 Cold-start patterns (named constants, one-line rationale each)

Every pattern below is a named constant in the tool with a one-line rationale
comment; the same rationales are emitted in the tool's own JSON output under
`definitions`, so the operator can audit what counted as cold start.

| pattern | rationale |
|---|---|
| `python_virtualenv` | creating a python virtualenv is environment startup |
| `pip_install` | installing python dependencies is environment startup |
| `node_dependency_install` | installing node dependencies is environment startup |
| `toolchain_install` | installing a toolchain or compiler is toolchain startup |
| `cargo_build` | compiling or fetching dependencies is toolchain startup, not a check |
| `project_build` | building the project under test is toolchain startup |
| `service_boot` | booting a service (docker, systemd, a server) is service startup |
| `solver_startup` | starting a solver server is solver startup |
| `simulation_preprocess` | preprocessing a simulation model or weather file is solver startup |

### 2.7 Verification patterns, per verifier type

| verifier type | patterns | rationale |
|---|---|---|
| `pytest` | `pytest_suite`, `unittest_suite`, `tox_nox` | a pytest / unittest suite is the python verification |
| `cargo` | `cargo_check`, `cargo_test` | cargo check / test / clippy is the rust verification |
| `energyplus_openstudio` | `energyplus_run`, `openstudio_run` | an EnergyPlus / OpenStudio run is the building-simulation verification |
| `formal_property` | `lean`, `coq_isabelle`, `smt_solver`, `property_tester` | a proof check, an SMT run or a property tester is the formal verification |

### 2.8 Teardown patterns

| pattern | rationale |
|---|---|
| `service_stop` | stopping a service is teardown |
| `cleanup` | removing scratch or build artifacts is teardown |

### 2.9 Aggregation and attribution

Per verifier type: `runs`, `runs_per_week`, `wall_clock_seconds`,
`cold_start_seconds`, `cold_start_share_pct` (cold start / wall-clock),
`verification_seconds`, `teardown_seconds`, `other_seconds`.

Attribution rules (stated so the numbers can be re-derived):

* A run is a run of every verifier type whose marker appears in any of its
  recorded commands — including setup commands, so a run that only does
  `cargo build` is still a cargo run and its build time is the cold start of
  interest.
* A run's cold start is attributed to every verifier type seen in that run.
* `top_candidate` is the verifier type with the **highest aggregate cold-start
  cost** (total cold-start seconds); ties break on higher share, then name.
* The gate is evaluated on that candidate: `proceed` when its cold-start share
  is `>= 25%` **and** it runs `>= 10`/week; `kill` when the cold-start share is
  `< 10%` for **every** verifier type; `below_gate` otherwise.

### 2.10 Known limits (read before believing a live number)

* A step's duration includes the agent's LLM turn, so the LLM turn of a setup
  step is charged to cold start. The share is therefore an **upper bound** on
  the environment-startup share, and a **lower bound** on the share that a
  warm verifier could remove if the setup step is entirely setup.
* Generic non-python test runners (`make test`, `ctest`, `npm test`, `go test`)
  belong to no verifier type in Phase 0 and land in `other`.
* A verifier type mentioned only inside a setup command (e.g.
  `pip install pytest`) still marks the run as that type.
* `unattributed_seconds` per run is the slack between the run wall-clock and the
  four buckets (idle time between steps).

---

## 3. The tool

```
python3 bin/warm_verifier_census.py census --transcripts-dir DIR \
    [--since ISO] [--until ISO] [--json]
```

* exit `0` on success;
* exit `2` when the transcripts directory is missing or unreadable, or when
  `--since` / `--until` cannot be parsed;
* `--json` prints the machine output: per-run rows (`run_id`, `file`,
  `verifier_types`, the four buckets in seconds), the aggregate table, the gate
  evaluation (`top_candidate`, its cold-start share, its runs/week, `verdict`)
  and the `definitions` audit trail. Without `--json` the same numbers are
  printed as a short text table.

Repo note: the tool carries no standalone `#` comment blocks on purpose.
`bin/delete_comment_phase0.py` requires every comment block in `bin/*.py` to be
listed in its hand-written classification table, and this brief does not allow
editing that script; the rationales therefore live in the pattern tables (as
data, one line each) and in docstrings.

---

## 4. Fixture run (SYNTHETIC — NOT live data)

The fixtures below are synthetic transcripts written by
`tests/test_warm_verifier_census.py` in the format `agent.py` emits, in a
temporary directory. **No live transcript was read.** The numbers in this
section are a check of the tool's arithmetic, not a measurement of the fleet.

Fixture corpus (one week window `2026-01-05T00:00:00Z .. 2026-01-12T00:00:00Z`):

| fixture | steps (command, duration) | expected shape |
|---|---|---|
| `run-setup-heavy.jsonl` | `python3 -m venv .venv && pip install -r requirements.txt` (60 s), `pytest -q tests/` (40 s) | cold start dominated |
| `run-no-setup.jsonl` | `pytest -q tests/` (40 s), `pytest -q tests/` (30 s) | no cold start |
| `run-unclassified.jsonl` | `pytest -q tests/` (40 s), `echo hello world` (10 s) | one step in `other` |

Command used (run from the repo root):

```
python3 bin/warm_verifier_census.py census --transcripts-dir /tmp/wvc-fixtures \
    --since 2026-01-05T00:00:00+00:00 --until 2026-01-12T00:00:00+00:00 --json
```

Actual output (exit code `0`), text rendering:

```
warm-verifier cold-start census (issue #154, Phase 0)
window: 2026-01-05T00:00:00+00:00 .. 2026-01-12T00:00:00+00:00 (1.0 weeks, source=flags)
runs: 3 (corpus 3), wall-clock 220.0s, cold start 60.0s (27.273%)

| verifier type | runs | runs/week | wall-clock s | cold start s | cold start % |
|---|---|---|---|---|---|
| pytest | 3 | 3.0 | 220.0 | 60.0 | 27.273 |

gate: below_gate (top candidate: pytest)
reason: top candidate pytest runs 3.0/week (< 10.0)
```

Actual output (exit code `0`), `--json` (window / totals / runs / aggregate / gate):

```json
{
  "window": {
    "since": "2026-01-05T00:00:00+00:00",
    "until": "2026-01-12T00:00:00+00:00",
    "weeks": 1.0,
    "source": "flags"
  },
  "totals": {
    "runs": 3,
    "runs_in_corpus": 3,
    "wall_clock_seconds": 220.0,
    "cold_start_seconds": 60.0,
    "verification_seconds": 150.0,
    "teardown_seconds": 0.0,
    "other_seconds": 10.0,
    "unattributed_seconds": 0.0,
    "cold_start_share_pct": 27.273
  },
  "runs": [
    {
      "run_id": "run-no-setup",
      "file": "run-no-setup.jsonl",
      "verifier_types": ["pytest"],
      "wall_clock_seconds": 70.0,
      "cold_start_seconds": 0.0,
      "verification_seconds": 70.0,
      "teardown_seconds": 0.0,
      "other_seconds": 0.0,
      "attributed_seconds": 70.0,
      "unattributed_seconds": 0.0,
      "steps": 2,
      "cold_start_gap_seconds": 0.0,
      "cold_start_setup_seconds": 0.0
    },
    {
      "run_id": "run-setup-heavy",
      "file": "run-setup-heavy.jsonl",
      "verifier_types": ["pytest"],
      "wall_clock_seconds": 100.0,
      "cold_start_seconds": 60.0,
      "verification_seconds": 40.0,
      "teardown_seconds": 0.0,
      "other_seconds": 0.0,
      "attributed_seconds": 100.0,
      "unattributed_seconds": 0.0,
      "steps": 2,
      "cold_start_gap_seconds": 60.0,
      "cold_start_setup_seconds": 0.0
    },
    {
      "run_id": "run-unclassified",
      "file": "run-unclassified.jsonl",
      "verifier_types": ["pytest"],
      "wall_clock_seconds": 50.0,
      "cold_start_seconds": 0.0,
      "verification_seconds": 40.0,
      "teardown_seconds": 0.0,
      "other_seconds": 10.0,
      "attributed_seconds": 50.0,
      "unattributed_seconds": 0.0,
      "steps": 2,
      "cold_start_gap_seconds": 0.0,
      "cold_start_setup_seconds": 0.0
    }
  ],
  "aggregate": [
    {
      "verifier_type": "pytest",
      "runs": 3,
      "runs_per_week": 3.0,
      "wall_clock_seconds": 220.0,
      "cold_start_seconds": 60.0,
      "cold_start_share_pct": 27.273,
      "verification_seconds": 150.0,
      "teardown_seconds": 0.0,
      "other_seconds": 10.0
    }
  ],
  "gate": {
    "verdict": "below_gate",
    "top_candidate": "pytest",
    "top_candidate_cold_start_share_pct": 27.273,
    "top_candidate_runs_per_week": 3.0,
    "top_candidate_cold_start_seconds": 60.0,
    "max_cold_start_share_pct": 27.273,
    "thresholds": {
      "proceed_cold_start_share_pct": 25.0,
      "proceed_runs_per_week": 10.0,
      "kill_below_share_pct": 10.0
    },
    "reason": "top candidate pytest runs 3.0/week (< 10.0)"
  }
}
```

Reading of the fixture run (arithmetic only): the setup-dominated run puts its
60 s setup step into the cold-start bucket through the "run start → first
non-setup action" interval (`cold_start_gap_seconds` 60 s, `cold_start_setup_seconds`
0 s — counted once); the no-setup run has `cold_start_seconds` 0; the
unclassifiable `echo` step lands in `other` (10 s) and is not counted as cold
start. The gate lands on `below_gate` because the share (27.273 %) clears 25 %
but 3 runs in one week is fewer than 10/week.

The three other gate outcomes are pinned in the test file from separate
fixture corpora: `proceed` (10 runs in a one-week window, 60 % cold start),
`kill` (5 % cold start for every type) and `below_gate` (60 % cold start but
2 runs/week).

---

## 5. Operator command for the live census

```
python3 bin/warm_verifier_census.py census --transcripts-dir <state-dir>/transcripts --json
```

With an explicit window (recommended: the gate's "runs/week" needs a stated
window):

```
python3 bin/warm_verifier_census.py census \
    --transcripts-dir <state-dir>/transcripts \
    --since <ISO-8601> --until <ISO-8601> --json
```

The tool exits `2` if the transcripts directory is missing or unreadable. The
supervisor runs this at collection time against the live state directory; the
Phase 0 worker never reads live state (it lives outside this repository).

---

## 6. PENDING — live census (operator-run)

**This section is deliberately empty of numbers.** The cells below are filled by
the operator from the command in section 5. They are not estimates, not
extrapolations from the fixtures, and not from any other issue's numbers.

| cell | value |
|---|---|
| live window used | PENDING (operator-run) |
| runs in window | PENDING (operator-run) |
| aggregate wall-clock (s) | PENDING (operator-run) |
| aggregate cold start (s) | PENDING (operator-run) |
| aggregate cold-start share | PENDING (operator-run) |
| per-type table (see JSON `aggregate`) | PENDING (operator-run) |
| **top candidate** (highest aggregate cold-start cost) | PENDING (operator-run) |
| top candidate cold-start share | PENDING (operator-run) |
| top candidate runs/week | PENDING (operator-run) |
| max cold-start share across types | PENDING (operator-run) |
| **gate verdict** (`proceed` / `kill` / `below_gate`) | PENDING (operator-run) |

Decision rule to apply to the filled table, verbatim from the issue:

* `proceed` — top candidate cold start `>= 25%` of its wall-clock **and**
  `>= 10` invocations per week;
* `kill` — cold start `< 10%` for every verifier type; the census is then the
  deliverable and the line closes;
* `below_gate` — anything else.

**Phase 1 remains blocked** until this table is filled by a real run *and* the
owner approves a Phase 1 design separately. A `proceed` verdict from the tool is
a necessary, not a sufficient, condition for a Phase 1 design; a `kill` verdict
closes the line with this census as the deliverable.

---

## 6b. Supervisor addendum — live census run at collection (2026-11-22)

Added by the collecting supervisor (not the Phase 0 worker): the command in
section 5 was run against the live transcripts directory at collection time.

| cell | value |
|---|---|
| live window used | 2026-09-29T00:05:08Z to 2026-11-22T23:44:44Z (7.8551 weeks, full corpus) |
| runs in window | 1065 |
| aggregate wall-clock (s) | 618545.981 |
| aggregate cold start (s) | 41561.672 |
| aggregate cold-start share | 6.719% |
| **top candidate** | cargo (171 runs, 22588.309 cold-start s, 61890.321 verification s) |
| top candidate cold-start share | 12.589% |
| top candidate runs/week | 21.769 |
| other types | pytest 379 runs, 1.65% share, 48.249/wk; formal_property 34 runs, 5.406%, 4.328/wk; energyplus_openstudio 33 runs, 1.621%, 4.201/wk |
| **gate verdict** | **below_gate** — top candidate cargo cold start is 12.589% (< 25.0% Proceed bar); not a Kill either (cargo is above the 10% everywhere line) |

Denominator caveat the operator should weigh before acting on the verdict:
the tool's share denominator is total run wall-clock, which includes agent
turn time (the `other` bucket is 446749.421s of the 618545.981s total). Read
against cold-start + verification time only, cargo's share is
22588.309 / (22588.309 + 61890.321) = 26.7%, which would cross the Proceed
bar. The issue's gate says "25% of aggregate verification wall-clock"; which
denominator that phrase means is an operator decision, and this addendum
records both readings rather than picking one silently. No Phase 1 work is
authorized by either reading without the owner's separate approval.

## 7. Verification

```
$ python3 -m pytest tests/test_warm_verifier_census.py -q
................                                                     [100%]
16 passed in 0.38s
```

```
$ python3 -m pytest tests/ -q
664 passed, 2 skipped, 62 subtests passed in 48.66s
```

`ruff` is not installed in this environment (`ruff: not found`,
`python3 -m ruff: No module named ruff`), so the structured-lint pass was
replaced by the pytest run above, as the brief allows. The new files follow the
repo's ruff configuration by inspection: explicit imports in isort order, no
blanket `except`, no `try/except/continue`, `subprocess.run(..., check=False)`
in the test, and the shebang file is executable (`chmod +x`).

### Files added by this Phase 0

* `bin/warm_verifier_census.py` — the census tool (read-only, deterministic).
* `tests/test_warm_verifier_census.py` — synthetic-fixture tests pinning the
  buckets, the window filter and the gate arithmetic for all three outcomes.
* `docs/spikes/warm-verifier-phase0.md` — this report.

No other file is touched: no `.github/workflows/`, no
`config/deployment.local.json`, no prompt, no change to `bin/agent.py` or to any
other existing `bin/` script.
