# Reader-gate / multi-agent baseline — Phase 0 report (#152)

**Phase 0 is measurement only.** No gate was built, no prompt was
changed, no harness behaviour was changed, no config was changed, and
no ledger format was changed. Phase 1 (the reader gate itself) is a
separate tap and is gated on the Phase 0 verdicts below.

The tool is `bin/reader_gate_phase0.py`; it has two subcommands:

```bash
# Part A -- the weakest-link reader gate census
python3 bin/reader_gate_phase0.py reader \
    --ledger <worker-or-shepherd-ledger.jsonl> \
    --premises <premises.jsonl> \
    --sources-root <dir the cited source paths resolve against>

# Part B -- the compute-matched multi-agent baseline census
python3 bin/reader_gate_phase0.py multiagent \
    --ledger <worker-ledger.jsonl>
```

Both subcommands print markdown tables by default and a JSON payload
with `--json`. The ledger path is always an argument: no operator state
path is hard-coded anywhere in the tool.

## Data availability (read this first)

| input | what this census ran on | where the real input lives |
|---|---|---|
| worker / shepherd ledger | **committed fixture** `tests/fixtures/reader_gate/worker_ledger.jsonl` (32 synthetic rows) | `<state dir>/worker_ledger.jsonl` — outside this checkout, not read by this run |
| premises (acted-on claims + cited sources) | **committed fixture** `tests/fixtures/reader_gate/premises.jsonl` (32 synthetic claims) | produced by the operator from the real summaries; not committed here |
| cited sources | **committed fixtures** `tests/fixtures/reader_gate/sources/` (6 small files) | the repositories the summaries cite |
| multi-agent dispatch ledger | **committed fixture** `tests/fixtures/reader_gate/multiagent_ledger.jsonl` (27 synthetic rows) | `<state dir>/worker_ledger.jsonl` — outside this checkout |

The operator state dir (ledgers, transcripts) lives outside this
worktree and was **not** read for this report. Every number below is
therefore computed on synthetic fixtures: it exercises the tool and
its arithmetic, and it is *not* evidence about the real corpus. The
operative verdict for issue #152 is the one the operator gets from the
commands below, run against the real state dir:

```bash
# Part A against the real ledgers
python3 bin/reader_gate_phase0.py reader \
    --ledger <state dir>/worker_ledger.jsonl \
    --premises <premises file built from the acted-on summaries> \
    --sources-root <repo checkout the summaries cite> \
    | tee /tmp/reader_gate_part_a.txt

# Part B against the real worker ledger
python3 bin/reader_gate_phase0.py multiagent \
    --ledger <state dir>/worker_ledger.jsonl \
    | tee /tmp/reader_gate_part_b.txt
```

(The exact state-dir path is deployment-specific and comes from
`config/deployment.local.json`; this repository never hard-codes it,
and `config/deployment.local.json` was not touched by this change.)

---

## Part A — weakest-link reader gate

Method (as implemented in `bin/reader_gate_phase0.py`): every premise
is an acted-on summary claim with its cited sources. Each cited source
is **re-read from disk** and the claim is scored `accurate`,
`incomplete` or `corrupted` — the weakest cited source decides the
verdict:

| source re-read result | meaning | premise verdict it forces |
|---|---|---|
| supported | the quoted text is in the source | — |
| contradicted | a `contradicts` string is in the source: the source says the opposite | corrupted |
| unsupported | the source was readable but the quoted text is absent | incomplete |
| unanchored | the citation carries no quote, so the re-read cannot check it | incomplete |
| unreadable | the cited file could not be read | incomplete |

`divergence = (incomplete + corrupted) / acted-on premises`, per reader
tier (the tier of the ledger row the premise joins to).

### Table A1 — divergence by reader tier

| tier | acted-on premises | accurate | incomplete | corrupted | divergence | re-read tokens |
|---|---|---|---|---|---|---|
| cheap | 12 | 10 | 1 | 1 | 16.67% | 948 |
| balanced | 12 | 11 | 1 | 0 | 8.33% | 892 |
| flagship | 6 | 6 | 0 | 0 | 0.0% | 499 |
| unknown | 1 | 1 | 0 | 0 | 0.0% | 106 |
| **all** | 31 | 28 | 2 | 1 | 9.68% | 2445 |

### Table A2 — re-read cost

| metric | value |
|---|---|
| premises scored | 32 |
| acted-on premises | 31 |
| premises not joined to a ledger row | 1 |
| re-read characters | 9746 |
| re-read tokens (chars/4) | 2445 |
| ledger tokens_out of the same runs | 485100 |
| re-read tokens as % of those runs' tokens_out | 0.5% |

### Table A3 — corrupted premises that reached a decision

| premise | tier | claim | sources |
|---|---|---|---|
| p-cheap-12 | cheap | the worker ledger is never rewritten | sources/legacy_notes.md |

### Part A gate

Gate arithmetic, straight from issue #152:

- **PROCEED** — divergence ≥ 5% on acted-on premises, **or** any
  corrupted premise reached a merge decision;
- **KILL** — divergence < 2% **and** zero corrupted premises reached a
  merge decision;
- **HOLD** — anything in between (2% ≤ divergence < 5% and no corrupted
  premise reached a merge decision).

On the fixture census: divergence = **9.68%** (≥ 5%) and **1**
corrupted premise reached a merge decision. Both PROCEED clauses fire.

**Part A gate: PROCEED** (on committed fixtures; the operative verdict
is the operator's run against the real ledgers).

The fixture numbers are also what the cheapest possible reader gate
would have to beat: a full re-read of every cited source cost 2445
tokens here, i.e. 0.5% of the tokens those same runs spent producing
their summaries. That is the cost side of the trade the reader gate
would make; it says nothing about the real corpus until the operator
run lands.

---

## Part B — compute-matched multi-agent baseline

Method: every ledger row is one dispatch and is classified into exactly
one class by documented, deterministic heuristics (first match wins):

| class | heuristic |
|---|---|
| shared-evidence-communication | the dispatch records evidence/findings shared between agents (`shared_evidence`, `evidence_shared`, `shared_findings`, `communication`, `agent_messages`, `cross_agent_notes`) |
| external-verification | the dispatch verifies against an external source (`external_source`, `verified_against`, `verification_source`, `external_verification`) |
| model-diversity | the dispatch used more than one provider or model tier (`providers_used` with >1 entry, `tiers_used` with >1 entry, `model_diversity`) |
| partitioned-evidence | everything else — one agent (or several) each on its own slice of the evidence, nothing shared |

The comparator for the shared-evidence class is the nearest comparable
**single-agent** dispatch: single-agent rows in the same repo when
there are any (here: `same_repo`), otherwise single-agent rows from
any repo.

### Table B1 — dispatches by class

| class | dispatches | successes | failures | ungraded | success rate | mean tokens_out |
|---|---|---|---|---|---|---|
| shared-evidence-communication | 6 | 5 | 1 | 0 | 83.33% | 13216.7 |
| external-verification | 4 | 3 | 1 | 0 | 75.0% | 16125.0 |
| model-diversity | 4 | 2 | 2 | 0 | 50.0% | 20875.0 |
| partitioned-evidence | 13 | 8 | 5 | 0 | 61.54% | 16015.4 |

### Table B2 — shared evidence vs nearest comparator

| group | dispatches | successes | failures | ungraded | success rate | mean tokens_out |
|---|---|---|---|---|---|---|
| shared-evidence | 6 | 5 | 1 | 0 | 83.33% | 13216.7 |
| single-agent comparator (same_repo) | 10 | 6 | 4 | 0 | 60.0% | 15350.0 |

### Part B gate

Gate arithmetic, straight from issue #152: the shared-evidence class
must show a **measurable win** over the comparators — a strictly
higher success rate **at no more tokens_out per dispatch** (the
comparison is compute-matched). No shared-evidence dispatches or no
comparator is HOLD (not measurable); otherwise, no win is KILL.

On the fixture census: shared-evidence success rate **83.33%** vs
comparator **60.0%**, at **13216.7** mean tokens_out vs **15350.0** —
a higher success rate at *lower* compute.

**Part B gate: PROCEED** (on committed fixtures; the operative verdict
is the operator's run against the real worker ledger).

Caveat on the fixture census: the real ledger carries no field that
names an agent topology, so the Part B classes are matched on the
optional marker fields listed in the table above. On the real ledger
those markers are mostly absent, and every dispatch then falls into
`partitioned-evidence`; the operator run should record the missing
topology markers (or a dispatch-level note) before the Part B verdict
is treated as meaningful. That is a Phase 0 data-collection
observation, not a harness change.

---

## What this Phase 0 did NOT do

- No reader gate, no reader tier, no re-read step in the harness.
- No prompt change, and no change to any rendered cron body.
- No change to `config/deployment.local.json`, to `prompts/`, to
  `.github/workflows/`, or to any harness behaviour.
- No change to any ledger format; the census only *reads* ledgers.

## Verification

- `python3 -m pytest tests/ -q` — full suite (see `WORKER_REPORT.md`
  for the result line).
- `python3 bin/reader_gate_phase0.py --help` — shows both subcommands.
- `python3 -m pytest tests/test_reader_gate_phase0.py -q` — the new
  tests for the two classifiers, the two scorers, and the gate
  arithmetic.
