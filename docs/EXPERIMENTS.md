# The experiment registry — format and tooling

**Status:** implemented (issue #132, docs/METRICS.md §8 Phase 2).
Date: 2026-11-14 · Owner: the harness-tuning pass (the registry's writer is
`bin/experiments.py`; the human is still the gate).

This document describes the registry file `experiments.jsonl` (the state
dir), the schema its rows follow (docs/METRICS.md §6), and the CLI that
creates, appends to, lists and validates it. It is the format reference
for the optimization loop; the prompt change that makes the
harness-tune pass open and close experiments is **not** in this
repository yet — it is proposed in
`docs/proposals/harness-tune-experiment-registry.md` and waits for
Alex's review.

---

## 1. Where the file lives

`<state dir>/experiments.jsonl` — one JSON object per line, one line per
experiment. The state dir is the same one the ledgers live in (the
deployment config's `state.dir`; see `bin/trace_summary.py` for the
search order).

The registry is append-mostly: `add` appends a row, `close` rewrites
one row in place (atomically, via a temp file and a rename). The tool is
the only writer — a hand-edited registry is a registry whose schema
nobody checked.

An absent registry is not an error: it means no experiment has been
opened yet. The dashboard renders the "no experiments yet" note in that
case, and the tune pass notes it and moves on.

## 2. The row schema (docs/METRICS.md §6)

| Field | Type | Meaning |
|---|---|---|
| `id` | string | the experiment's id (`"001"`, `"002"`, …); unique in the file |
| `date` | string | the day the experiment was opened, `YYYY-MM-DD` |
| `hypothesis` | string | one sentence: the primary metric, the expected direction and the expected size |
| `change` | string | what was edited (prompt file, routing rule, config knob) with the commit/PR link |
| `window` | object | `{"start": "YYYY-MM-DD", "min_sample": N, "min_weeks": N}` — the measurement window and its minimum sample (section 6: ≥20 runs **or** ≥2 weeks, whichever is later) |
| `primary_metric` | string | exactly **one** metric the experiment is judged on |
| `guardrails` | list of strings | the metrics that must not regress (from the §4 table); never empty |
| `verdict` | string | `keep` \| `revert` \| `inconclusive` \| `in-progress` (`pending` is accepted as a synonym of `in-progress` and normalized to it on write) |
| `verdict_evidence` | string, object or list | the stratified numbers behind the verdict (overall **and** per repo / tier / provider) |
| `name` | string (optional) | a short human label; the dashboard's Panel 5 prefers it over `id` |
| `verdict_date` | string (optional) | the day the verdict was recorded; written by `close` |

Rules the schema encodes (section 6):

- one primary metric per experiment; the guardrails come from §4;
- a verdict compares the window against the **immediately preceding
  equal-length window**, **stratified** — an aggregate win with a
  per-repo loss is `inconclusive` until it is explained;
- the existing evidence bar (≥2 distinct runs behind any prompt edit)
  still applies; the registry adds the *outcome* bar on top;
- structural changes (new gates, slot counts, merge policy) stay the
  human's decision — the loop may propose them with evidence, never
  apply them. The registry records what was applied, so a proposal that
  was never applied never gets a row.

## 3. The CLI

```
python3 bin/experiments.py init   [--state-dir DIR] [--registry PATH] [--force]
python3 bin/experiments.py add    --id ID --hypothesis TEXT --change TEXT
                                 --window-start YYYY-MM-DD --primary-metric METRIC
                                 --guardrail "GUARDRAIL" [--guardrail ...]
                                 [--verdict in-progress] [--verdict-evidence TEXT]
                                 [--date YYYY-MM-DD] [--name NAME]
                                 [--min-sample 20] [--min-weeks 2]
python3 bin/experiments.py close  --id ID --verdict keep|revert|inconclusive
                                 --verdict-evidence TEXT [--window-start YYYY-MM-DD]
python3 bin/experiments.py list   [--verdict V] [--json]
python3 bin/experiments.py validate [--json]
```

- `--registry` overrides the path; otherwise the registry is
  `<state dir>/experiments.jsonl`, where the state dir comes from
  `--state-dir` or from the deployment config.
- `--json` prints machine-readable output on every subcommand (the
  sibling tools' convention).
- Exit codes: `0` success; `1` the registry failed validation or a row
  was rejected (a schema violation, a duplicate id); `2` a file could
  not be read or written (no registry, no state dir).

Examples:

```bash
# open the registry in a state dir
python3 bin/experiments.py init --state-dir ~/.plan-coder/state

# open an experiment (before the change is applied)
python3 bin/experiments.py add --state-dir ~/.plan-coder/state \
  --id 003 \
  --hypothesis "brief template clarification cuts failed:brief-* runs by 30% within 2 weeks" \
  --change "prompts/backlog-supervisor.md - brief template clarification (PR #123)" \
  --window-start 2026-11-21 \
  --primary-metric "failed:brief-* runs per week" \
  --guardrail "PR-opened rate" --guardrail "tokens_out per merged PR (median)" \
  --verdict in-progress

# close it when the window has closed and the numbers are in
python3 bin/experiments.py close --state-dir ~/.plan-coder/state \
  --id 003 --verdict keep \
  --verdict-evidence "failed:brief-* 14 -> 9 per week (n=31 vs n=28 runs), PR-opened rate 75.6% -> 74.8% (guardrail held), tokens/merged PR 29.7k -> 28.9k"

# read it
python3 bin/experiments.py list --state-dir ~/.plan-coder/state
python3 bin/experiments.py validate --state-dir ~/.plan-coder/state
```

The tool never changes targets, slot counts, merge policy, or any
prompt. It is a ledger with a schema check attached.

## 4. Compatibility with the dashboard

`bin/dashboard.py` (Panel 5) already reads this file and is **not**
changed by this work:

- the timestamp falls back from `ts` to `date` — a row written by this
  tool has `date`, so it renders;
- the label falls back from `name` to `id` — a row with the optional
  `name` renders the human label, a row without it renders the id;
- the change note comes from `change` — the field this schema requires.

`tests/test_experiments.py` proves this: it copies the seeded example
registry into a fixture state dir and asserts `build_experiments()`
reads both rows (count, label, timestamp, change note).

## 5. The seeded example registry

`examples/experiments.example.jsonl` — the two experiments issue #132
names, written by the tool itself (not hand-written):

1. **001 — Context Mode (#53)**, the retroactive first entry: the
   section 6 worked example. Primary metric
   `tokens_out per merged PR (median)`, guardrails `PR-opened rate` and
   `grade median`, verdict `in-progress` — the live A/B Alex already
   gated has not closed its window, so there are no verdict numbers yet.
2. **002 — p90 tail: exploratory read cap**, the second entry, in the
   explicit `in-progress` state: the change is not published yet, so the
   measurement window has not started. Primary metric
   `tokens_out per run (p90)`, guardrails `PR-opened rate` and
   `tokens_out per merged PR (median)`.

Both rows validate against the section 6 schema, and both are readable
by the existing dashboard reader.

One caveat on the retroactive row: the merge date of PR #53 is not
recorded anywhere in this repository, so 001's `window.start` is the
day the registry was opened (2026-11-14). When the real merge date is
known, correct the row (`bin/experiments.py close --id 001 --window-start
<merge day> ...` rewrites the row) — the retroactive entry is only
honest with the window it actually measures.

## 6. What is deliberately absent

- No auto-tuning: nothing in this tooling changes a target, a slot
  count or the merge policy, and nothing applies a change to the
  harness.
- No live prompt edit: the harness-tune prompt change is a proposal
  document, not an applied change (see
  `docs/proposals/harness-tune-experiment-registry.md`).
- No write to the operator's private state dir from the repository:
  the example registry is a fixture; the live registry is created by
  running the tool against the live state dir.
