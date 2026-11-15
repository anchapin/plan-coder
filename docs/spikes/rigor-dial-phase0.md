# Phase 0 `modes.rigor` dial — design + measurement (issue #54)

Measurement and design only: no runtime behaviour, no prompt text, no
rendered cron body and no live config changed.  This report decides
whether the issue #54 front-door dial ("one setting over the existing
brief/review/QA knobs") is worth building in Phase 1.

Every knob named below was **verified to exist** in this tree before it
was mapped; the file and line are cited.  Knobs the issue named that do
**not** exist as knobs are listed as such (Section 3) rather than
invented.

## Parameters

| parameter | value |
|---|---|
| scope | measurement + design only; no behaviour change |
| knobs verified in | `config/deployment.example.json`, `prompts/backlog-supervisor.md`, `prompts/pr-shepherd.md`, `bin/goldfish.py`, `bin/jittest.py`, `bin/failover.py`, `bin/ledger_check.py`, `bin/agent.py`, `bin/render_prompts.py` |
| config file read | `config/deployment.example.json` (the committed EXAMPLE only; `config/deployment.local.json` is operator-private and gitignored — never read, never touched) |
| levels proposed | `minimal`, `standard`, `full` |
| default level | `minimal` everywhere (per the issue) |
| cost-estimate corpus | `tests/fixtures/dashboard_state/worker_ledger.jsonl` (2 worker rows, one 2026-01-05 cycle) + `tests/fixtures/dashboard_state/shepherd_ledger.jsonl` (5 shepherd rows, same window) |
| per-step tokens_out model | derived from those 2 rows only (n=1 per tier) — see Section 7 |
| live ledgers | not available in this worktree; no number below is invented |
| test suite | `python3 -m pytest tests/ -q` → `282 passed, 2 skipped, 13 subtests passed in 21.17s` (baseline, before and after this report) |

---

## 1. What exists today — the knob inventory (verified)

All knobs live under `workers.*`, `supervisor.*`, `shepherd.*` in the
deployment config.  **Every one of them is global** — there is no
per-repo and no per-path carrier for any knob (Section 5 explains why
that matters).

| knob | defined at | current value | read at | what it actually controls |
|---|---|---|---|---|
| `workers.max_steps` | `config/deployment.example.json:42` | 60 | `bin/agent.py:828` (step loop), `prompts/backlog-supervisor.md:38` (`--max-steps`) | hard cap on worker steps per run |
| `workers.max_tokens_out` | `config/deployment.example.json:47` | 0 (= off) | `bin/agent.py:732-744`, `bin/agent.py:842` | per-run completion-token burn guard |
| `workers.cycle_tokens_out_budget` | `config/deployment.example.json:48` | 0 (= off) | `prompts/backlog-supervisor.md:21` (step 1) | per-cycle completion-token burn guard |
| `workers.failover.failure_threshold` | `config/deployment.example.json:51` | 2 | `bin/failover.py:237-239` | graded failures before a re-dispatch at a higher tier |
| `workers.failover.flag_after` | `config/deployment.example.json:52` | 3 | `bin/failover.py:240-241` | graded failures before the issue is flagged for the operator |
| `workers.failover.tier_ladder` | `config/deployment.example.json:53` | `["cheap","balanced","flagship"]` | `bin/failover.py:242` | the tier escalation ladder (the **only** tier knob that exists) |
| `workers.goldfish.enabled` | `config/deployment.example.json:61` | **false** | `bin/goldfish.py:129-130` | ships disabled; enabling is an operator decision (config `_about`, line 60) |
| `workers.goldfish.max_per_cycle` | `config/deployment.example.json:62` | 2 | `prompts/backlog-supervisor.md:25` (step 4b) | brief reviews per supervisor cycle |
| `workers.goldfish.max_tokens_out` | `config/deployment.example.json:63` | 30000 | `bin/goldfish.py:83` | per-review token cap |
| `workers.goldfish.max_steps` | `config/deployment.example.json:64` | 25 | `bin/goldfish.py:84` | steps per review |
| `workers.goldfish.tier` | `config/deployment.example.json:65` | `cheap` | `bin/goldfish.py:85` | tier the review runs on |
| `workers.goldfish.max_scope_files` | `config/deployment.example.json:66` | 5 | `bin/goldfish.py:150` (trigger `scope`) | scope-size trigger threshold for the brief review |
| `workers.jittest.enabled` | `config/deployment.example.json:70` | **false** | `bin/jittest.py:198-199` | ships disabled; operator decision (config `_about`, line 69) |
| `workers.jittest.max_per_cycle` | `config/deployment.example.json:71` | 2 | `bin/jittest.py:255` | catching checks per cycle |
| `workers.jittest.hypothesis_tier` | `config/deployment.example.json:72` | `balanced` | `prompts/backlog-supervisor.md:44` (step 7b) | tier of the hypothesis run |
| `workers.jittest.test_tier` | `config/deployment.example.json:73` | `cheap` | `prompts/backlog-supervisor.md:44` | tier of the test run |
| `workers.jittest.max_tokens_out_hypothesis` | `config/deployment.example.json:74` | 30000 | `prompts/backlog-supervisor.md:44` | hard cap on the hypothesis run |
| `workers.jittest.max_tokens_out_test` | `config/deployment.example.json:75` | 30000 | `prompts/backlog-supervisor.md:44` | hard cap on the test run |
| `workers.jittest.max_steps` | `config/deployment.example.json:76` | 25 | `prompts/backlog-supervisor.md:44` | steps per JiT run |
| `workers.jittest.max_files` | `config/deployment.example.json:77` | 5 | `bin/jittest.py:247` | diff-size trigger (file count) |
| `workers.jittest.max_diff_lines` | `config/deployment.example.json:78` | 300 | `bin/jittest.py:248` | diff-size trigger (line count) |
| `workers.jittest.max_three_state_runs` | `config/deployment.example.json:79` | 3 | `bin/jittest.py:872-878` | the three-state gate's verify-loop budget (the gate **refuses** below 3) |
| `supervisor.max_parallel` | `config/deployment.example.json:33` | 6 | `prompts/backlog-supervisor.md:7`, `:21` (step 1) | workers in flight per cycle |
| `supervisor.collect_deadline_hours` | `config/deployment.example.json:34` | 2.5 | `prompts/backlog-supervisor.md:40` (Phase 2 header) | how long the cycle waits for stragglers |
| `supervisor.interval_hours` | `config/deployment.example.json:35` | 2 | `prompts/backlog-supervisor.md:7` | cycle cadence |
| `shepherd.max_fix_attempts` | `config/deployment.example.json:155` | 2 | `prompts/pr-shepherd.md:12` (step 2b) | CI repair attempts per PR before the operator flag |
| `repos[].issues_per_cycle` | `config/deployment.example.json:20`, `:29` | 2 / 1 | `bin/render_prompts.py:61-83` | how many issues a repo contributes per cycle |

Path-sensitivity that already exists (hard-coded, **not** knobs):

* goldfish hard-stop path markers — `bin/goldfish.py:109-110`
  (`.github/workflows/`, `migrat`, `/physics`, `physics/`, `simulation output`).
* jittest path class exemptions (docs-only / test-only diffs) —
  `bin/jittest.py:233-236` (`_is_doc_path`, `bin/jittest.py:218-221`).

## 2. The knobs that are *already* per-repo-shaped

`repos[]` entries carry exactly: `name`, `clone`, `slug`, `base`,
`issues_per_cycle`, `skip_note`, `brief_extra`
(`config/deployment.example.json:16-30`; rendered at
`bin/render_prompts.py:61-83`).  **No `repos[]` entry can carry a
worker/QA knob today.**  `brief_extra` is free text appended to every
brief of that repo (`bin/render_prompts.py:76-78`) — it is the only
per-repo injection point that exists, and it is prompt text, not a knob.

## 3. Verifications that FAILED — knobs the issue named that do not exist

The issue's candidate knob list was checked against the tree.  Three of
the five named knobs have **no config knob** to map:

| issue's candidate knob | verdict | where it actually lives |
|---|---|---|
| Goldfish scope threshold | **exists** | `workers.goldfish.max_scope_files` — `config/deployment.example.json:66`, used `bin/goldfish.py:150` |
| JiT trigger strictness | **exists** | `workers.jittest.max_files` / `max_diff_lines` — `config/deployment.example.json:77-78`, used `bin/jittest.py:247-248` |
| proof-packet depth | **does not exist as a knob** | the packet depth constants are hard-coded in code: `MAX_CONTRACT_FILES = 4`, `MAX_CONTRACT_CHARS = 6000`, `MAX_NAMED_FILES = 3` (`bin/jittest.py:177-179`, used at `bin/jittest.py:420-421`, `:470-473`, `:1365`).  The goldfish packet builder (`bin/goldfish.py:169-224`) has **no** depth knob at all — it always embeds the whole issue text and the whole brief.  The brief's `PROOF:` block is prompt text (`prompts/backlog-supervisor.md:24`, section 7-proof at `:43`). |
| brief acceptance-criteria requirement | **does not exist as a knob** | it is prompt text (`prompts/backlog-supervisor.md:24`: the brief "must" carry task, acceptance criteria, verification commands).  The only config-side artefact is goldfish's `--has-criteria` **input** (`bin/goldfish.py:138`, trigger `no-criteria` at `bin/goldfish.py:152-153`) — an input, not a knob. |
| supervisor re-run depth | **does not exist under that name** | the nearest real knobs are: re-dispatch depth = `workers.failover.failure_threshold` / `flag_after` (`config/deployment.example.json:51-52`, `bin/failover.py:237-241`); CI repair depth = `shepherd.max_fix_attempts` (`config/deployment.example.json:155`, `prompts/pr-shepherd.md:12`); parallelism = `supervisor.max_parallel` (`config/deployment.example.json:33`).  The retry guard's threshold is **hard-coded**: `min_failures: int = 2` (`bin/ledger_check.py:32`, block at `bin/ledger_check.py:55`) with no config carrier. |

Two further facts that matter for any dial:

* There is no knob for the dispatch tier.  "cheap = small fully-specified jobs, balanced = ordinary issues (default), flagship = tricky work" is prompt judgment (`prompts/backlog-supervisor.md:9`).  The only tier knob is the *escalation* ladder `workers.failover.tier_ladder` (`config/deployment.example.json:53`).
* **The two QA spikes ship disabled** (`workers.goldfish.enabled: false` at `config/deployment.example.json:61`; `workers.jittest.enabled: false` at `config/deployment.example.json:70`), each with an `_about` that says enabling is "a separate operator decision" (lines 60 and 69).  A dial that maps onto them changes behaviour only for an operator who has already turned them on.

## 4. The proposed `rigor` config schema

Proposed (design only — **nothing in this tree reads it**; the config
loader is permissive, `bin/trace_summary.py:36-54` ignores unknown
top-level keys, so a `modes` section in `deployment.local.json` would be
silently inert today):

```json
{
  "modes": {
    "rigor": {
      "_about": "One front-door dial over the existing brief/review/QA knobs (issue #54). Level names only; the level -> knob-value table lives in code (bin/rigor.py, Phase 1). 'minimal' is the default everywhere. Most specific wins: path override > repo level > global default.",
      "default": "minimal",
      "repos": {
        "plan-coder": {
          "level": "standard",
          "paths": {
            "bin/": "standard",
            "prompts/": "standard",
            "docs/": "minimal",
            "docs/spikes/": "minimal"
          }
        },
        "content-repo": {
          "level": "minimal"
        }
      }
    }
  }
}
```

Resolution rules (proposed):

1. `modes.rigor.default` — the global default level; **`minimal`** everywhere unless the operator says otherwise (issue #54's requirement).
2. `modes.rigor.repos.<name>.level` — per-repo default; the repo name matches `repos[].name` (`config/deployment.example.json:17`).
3. `modes.rigor.repos.<name>.paths.<prefix>` — optional per-path override, longest-prefix wins, matched against the **brief's SCOPE paths** the supervisor already computes (the same list it passes to `goldfish.py check --scope-paths`, `prompts/backlog-supervisor.md:25`).
4. Optional `modes.rigor.levels.<level>` — explicit per-level knob overrides for an operator who wants to diverge from the built-in table.  Absent by default; the built-in table below is the contract.

Level → knob-value table (the built-in mapping, Section 5).  Values are
**the knob's current value at `standard`** where the knob already has a
sane reference value, so `standard` = today's behaviour for every knob
except the two QA spikes, which stay as configured.

## 5. The knob mapping per level (EXISTING knobs only)

Legend: **changes** = the value differs between at least two levels.
`n/a (disabled)` = the spike is off at that level, so the knob's value
is inert.

| knob (existing) | minimal | standard | full | changes? |
|---|---|---|---|---|
| `workers.max_steps` (`:42`) | 30 | 60 | 100 | **yes** |
| `workers.failover.failure_threshold` (`:51`) | 1 | 2 | 3 | **yes** |
| `workers.failover.flag_after` (`:52`) | 2 | 3 | 5 | **yes** |
| `workers.failover.tier_ladder` (`:53`) | `["cheap","balanced"]` | `["cheap","balanced","flagship"]` | `["cheap","balanced","flagship"]` | **yes** (minimal vs standard) |
| `workers.goldfish.enabled` (`:61`) | false | true | true | **yes** |
| `workers.goldfish.max_scope_files` (`:66`) | n/a (disabled) | 5 | 10 | **yes** |
| `workers.goldfish.max_per_cycle` (`:62`) | n/a (disabled) | 2 | 4 | **yes** |
| `workers.goldfish.max_steps` (`:64`) | n/a (disabled) | 25 | 40 | **yes** |
| `workers.goldfish.tier` (`:65`) | n/a (disabled) | cheap | balanced | **yes** |
| `workers.jittest.enabled` (`:70`) | false | true | true | **yes** |
| `workers.jittest.max_files` (`:77`) | n/a (disabled) | 5 | 10 | **yes** |
| `workers.jittest.max_diff_lines` (`:78`) | n/a (disabled) | 300 | 800 | **yes** |
| `workers.jittest.max_three_state_runs` (`:79`) | n/a (disabled) | 3 | 5 | **yes** (never < 3: `bin/jittest.py:872-878` refuses the gate) |
| `workers.jittest.max_per_cycle` (`:71`) | n/a (disabled) | 2 | 4 | **yes** |
| `workers.jittest.hypothesis_tier` (`:72`) | n/a (disabled) | balanced | flagship | **yes** |
| `workers.jittest.test_tier` (`:73`) | n/a (disabled) | cheap | balanced | **yes** |
| `supervisor.max_parallel` (`:33`) | 2 | 6 | 10 | **yes** |
| `supervisor.collect_deadline_hours` (`:34`) | 1.5 | 2.5 | 4 | **yes** |
| `shepherd.max_fix_attempts` (`:155`) | 0 | 2 | 3 | **yes** |
| `workers.max_tokens_out` (`:47`) | *excluded* | *excluded* | *excluded* | no — burn guard, not a rigor knob (config `_about`: "pathological-burn guards") |
| `workers.cycle_tokens_out_budget` (`:48`) | *excluded* | *excluded* | *excluded* | no — same reason |
| `workers.goldfish.max_tokens_out` (`:63`) | n/a | 30000 | 60000 | **yes** (full) |
| `workers.jittest.max_tokens_out_hypothesis` / `_test` (`:74-75`) | n/a | 30000 / 30000 | 60000 / 60000 | **yes** (full) |

**The mapping is not pure relabelling**: all 21 mappable knobs take a
different value at `minimal` than at `standard`, and 19 of them differ
again at `full`.  The issue's automatic-Kill trigger ("pure relabelling,
no knob actually changing value per level") therefore does **not** fire
on that ground.

Two knobs that a front door would want but that **cannot** be mapped:

* *dispatch tier ceiling* — no knob exists (`prompts/backlog-supervisor.md:9` is judgment text).  A dial can bound the **escalation** ladder only.
* *per-path override of any kind* — no knob is keyed by path; the only path-aware logic is the hard-coded marker lists (`bin/goldfish.py:109-110`, `bin/jittest.py:233-236`).  A per-path override therefore needs a **new resolver**, not a knob (Section 8).

## 6. Worked mapping — two repos of different character

### 6.1 `plan-coder` itself — `standard` for the merge/seal paths, `minimal` for docs

Effective level per path (most-specific-wins over
`modes.rigor.repos["plan-coder"].paths`):

| path | effective level | why (this repo's own character) |
|---|---|---|
| `bin/`, `prompts/`, `config/` | `standard` | these are the merge/seal paths: `bin/agent.py` runs the workers, `prompts/backlog-supervisor.md` assembles the PR, `config/deployment.example.json` is the operator's live contract.  A cheap-tier 30-step worker that truncates a refactor here loses the PR. |
| `docs/`, `docs/spikes/`, `examples/` | `minimal` | prose and sample files: the file-level work is small, self-contained and reviewable; the QA spikes' value (catching a seam/ordering bug) is near zero on a markdown edit. |

Knob values that actually change between the two paths of the same repo:

| knob | `bin/`+`prompts/` (standard) | `docs/` (minimal) | delta |
|---|---|---|---|
| `workers.max_steps` | 60 | 30 | worker step cap halves |
| `workers.failover.failure_threshold` | 2 | 1 | one re-dispatch instead of two |
| `workers.failover.flag_after` | 3 | 2 | flag the operator sooner |
| `workers.failover.tier_ladder` | `["cheap","balanced","flagship"]` | `["cheap","balanced"]` | no flagship escalation on docs |
| `workers.goldfish.enabled` | true | false | **no brief review on docs briefs** |
| `workers.goldfish.max_scope_files` | 5 | n/a | — |
| `workers.goldfish.max_per_cycle` | 2 | n/a | — |
| `workers.jittest.enabled` | true | false | **no catching check on docs diffs** |
| `workers.jittest.max_files` | 5 | n/a | — |
| `workers.jittest.max_diff_lines` | 300 | n/a | — |
| `workers.jittest.max_three_state_runs` | 3 | n/a | — |
| `supervisor.max_parallel` | 6 | 2 | fewer workers in flight |
| `supervisor.collect_deadline_hours` | 2.5 | 1.5 | shorter wait |
| `shepherd.max_fix_attempts` | 2 | 0 | **no CI repair for docs PRs** |

Note the two "sharp" edges this repo's own code already encodes, which
the dial must respect rather than override:

* jittest exempts docs-only and test-only diffs anyway (`bin/jittest.py:233-236`) — so `jittest.enabled=false` on `docs/` changes little; the real saving on docs is the **goldfish** review and the step cap.
* goldfish's hard-stop markers (`bin/goldfish.py:109-110`) include `.github/workflows/` and `migrat` — a `minimal` path that touched a workflow would still be caught by the *existing* hard-stop trigger, because the marker list is not a knob and the dial does not (and must not) touch it.

### 6.2 A content repo — `minimal` throughout

`modes.rigor.repos["content-repo"].level = "minimal"`, no path
overrides.  Every knob takes its minimal value for every path in the
repo:

| knob | content-repo (minimal, all paths) |
|---|---|
| `workers.max_steps` | 30 |
| `workers.failover.failure_threshold` / `flag_after` | 1 / 2 |
| `workers.failover.tier_ladder` | `["cheap","balanced"]` |
| `workers.goldfish.*` | disabled (no brief review at all) |
| `workers.jittest.*` | disabled (no catching check at all) |
| `supervisor.max_parallel` | 2 |
| `supervisor.collect_deadline_hours` | 1.5 |
| `shepherd.max_fix_attempts` | 0 |

A content repo's diffs are prose, so the two QA spikes would have been
no-ops anyway (jittest: docs-only exemption, `bin/jittest.py:233-236`);
the dial's effect there is the step cap, the cheap-tier ladder, the
shorter cycle deadline and no CI repair attempts.

## 7. Cost estimate from the worker ledger

### 7.1 Data used (nothing invented)

The only worker-ledger data inside this repo is the test fixture
`tests/fixtures/dashboard_state/worker_ledger.jsonl` — **two** collected
worker rows forming one 2026-01-05 cycle — plus
`tests/fixtures/dashboard_state/shepherd_ledger.jsonl` (five shepherd
rows in the same window).  The live ledgers referenced by the other
Phase 0 reports (`/home/hatch/workspace/.../worker_ledger.jsonl`) are
**not present in this worktree**, so no live corpus is used and no
number is extrapolated beyond these two rows.

| row | repo/issue | tier | steps | tokens_in | tokens_out | outcome | grade |
|---|---|---|---|---|---|---|---|
| 1 | repo-a#123 | balanced | 34/60 | 812,340 | 40,211 | `pr_opened` (PR 456) | 82 |
| 2 | repo-b#78 | cheap | 60/60 | 402,118 | 18,930 | `failed:empty-diff` | 41 |

Shepherd activity in the same window (the cycle's downstream cost):
PR 456 → `rerun` (flaky CI) → `merged`; PR 789 → `fix_attempt`
(formatting) → `merged_by_shepherd`; PR 321 → `needs_operator`.

### 7.2 The model (stated, so it can be checked)

* `tokens_in` is the prompt/context cost of the run.  It does not depend on the rigor level (same brief, same repo, same context), so it is **identical in both arms**.
* `tokens_out` per run ≈ *steps spent* × *per-step rate of the run's tier*.  The per-step rates are derived from the two rows above, n=1 per tier: cheap `18,930/60 = 315.5`, balanced `40,211/34 = 1,182.7` tokens/step.  **This is the weakest number in the report**: with one row per tier, the rate ratio (3.7×) is not statistically meaningful; it is used only to order the arms, and the sensitivity below is stated.
* A run whose steps exceed the level's `workers.max_steps` is truncated at the cap and **produces no PR** (the run dies on the step budget, `bin/agent.py:826-828`).
* QA spikes at `standard`/`full` add their **caps** (upper bound, since the cycle's trigger count is unknown): goldfish `max_per_cycle 2 × max_tokens_out 30,000 = 60,000`; jittest `max_per_cycle 2 × (30,000 + 30,000) = 120,000` → `≤ 180,000` tokens_out per cycle.
* Time: the ledger `ts` is the run's **END** time, not its dispatch time (`docs/spikes/trajectory-coupling-phase0.md:24`).  The two rows' `ts` are 162.7 min apart; the prompt launches workers staggered ~2 min apart (`prompts/backlog-supervisor.md:38`), so the observed cycle wall-clock ≈ 160 min, i.e. ≈ **2.71 min/step** on the 60-step cheap run (n=1).

### 7.3 The arms

| arm | worker tokens_out | QA tokens_out | tokens_in | **total tokens** | steps spent | cycle wall-clock (est.) |
|---|---|---|---|---|---|---|
| `standard` (as-run tiers, QA spikes **on**) | 59,141 | ≤180,000 | 1,214,458 | **≤ 1,453,599** | 94 | ≈ 163 min + QA |
| `standard` (QA spikes **off** = today's reference deployment) | 59,141 | 0 | 1,214,458 | **1,273,599** | 94 | ≈ 163 min |
| `minimal` (all cheap, 30-step cap, QA off) | 18,930 | 0 | 1,214,458 | **1,233,388** | 60 | ≈ 81 min |
| `full` (flagship proxy = balanced rate, 100-step cap, QA on) | 111,174 | ≤180,000 | 1,214,458 | **≤ 1,505,632** | 94 | ≈ 163 min + QA |

Deltas:

* `minimal` vs `standard` (QA on): **−15.1% total tokens**; worker tokens_out alone **−68.0%** (59,141 → 18,930).
* `minimal` vs `standard` (QA off, today's deployment): **−3.2% total tokens** — the level dial barely moves the *total* token number, because `tokens_in` (context) is 95%+ of it and the dial cannot touch it.
* `standard` QA depth adds **≤14.1%** total tokens over the same cycle, and up to ~136 min (goldfish) + ~271 min (jittest) of serial wall-clock at 2.71 min/step — which **exceeds** `supervisor.collect_deadline_hours = 2.5` (150 min) on its own.  That is a real argument *for* a dial (depth must fit the cycle), and also a real argument *against* turning the spikes on by default.
* Sensitivity: if the cheap/balanced per-step rate ratio is wrong by 2× either way, `minimal`'s worker tokens_out moves to between ~9,465 and ~37,860 — i.e. the *ordering* (`minimal` < `standard`) is robust, the *magnitude* is not.

### 7.4 Does the delta buy anything measurable?

**No — on this corpus it buys a measurable LOSS.**

* Row 1 (repo-a#123) needed **34 steps** and opened a PR with grade 82.  Under `minimal` (30-step cap, cheap tier) it is **truncated at 30 steps and opens nothing**.  The 68% worker-token saving is bought by deleting the cycle's only successful PR.
* Row 2 (repo-b#78) already failed at 60/60 steps with `failed:empty-diff` (grade 41) — `minimal` truncates it at 30 steps and it still fails, so the saving is pure.
* PR 456 was merged after **one** flaky CI rerun (`shepherd_ledger.jsonl` row 1) and PR 789 was merged after **one** `fix_attempt` (row 3).  `minimal` sets `shepherd.max_fix_attempts = 0`, so the fix attempt that merged PR 789 would not happen and PR 789 would be flagged for the operator instead.
* Cycle outcome under the two arms, from the fixture's own rows: `standard` → 2 PRs opened (1 merged, 1 fix-attempted and merged), grades 82 and 41; `minimal` → **0 PRs**, 2 truncated/failed runs.

So the measurable delta of `minimal` vs `standard` on this corpus is: **−68% worker tokens_out, −50% wall-clock, −2 PRs.**  The only measurable benefit is *not spending tokens on a run that was going to fail anyway* (row 2) — and that benefit is already available today from `bin/ledger_check.py`'s retry guard, without a dial.

## 8. Verdict

### **KILL** — the `modes.rigor` front door, as specified in issue #54, is not worth building in Phase 1.

Not because the mapping is empty (it is not — all 21 mappable knobs change value per level, Section 5) but for four reasons that the evidence above
establishes:

1. **No carrier for the two features the issue asks for.**  The dial is specified as per-repo + per-path.  Every knob in this tree is global (`Section 1-2`): `repos[]` entries carry no knob (`bin/render_prompts.py:61-83`), and no knob is keyed by path.  A per-path override therefore needs a **new resolver** (a config-keyed path→level map consulted at brief/dispatch time by the supervisor, which is a prompt-text change and a code change) — that is a mechanism, not a knob mapping, and it is exactly the kind of runtime behaviour Phase 0 must not introduce.
2. **Two of the five named knobs do not exist.**  Proof-packet depth is hard-coded in `bin/jittest.py:177-179`; the brief acceptance-criteria requirement is prompt text (`prompts/backlog-supervisor.md:24`); the only config-side artefact there is goldfish's `--has-criteria` *input* (`bin/goldfish.py:138`).  Mapping a dial onto them means either editing prompts (out of scope) or adding new knobs (which is not "over EXISTING knobs").
3. **The QA depth that would differentiate the levels is inert in the reference deployment.**  `workers.goldfish.enabled` and `workers.jittest.enabled` are both `false` (`config/deployment.example.json:61`, `:70`), each with an `_about` reserving the enable decision to the operator.  `standard` and `full` are therefore *identical* in the shipped config until an operator flips them — the dial's headline levels do not exist for a stock deployment.
4. **The cost delta buys nothing measurable; it loses PRs.**  On the only in-repo corpus (2 ledger rows), `minimal` saves 68% of worker tokens_out and 50% of wall-clock and **loses both PRs** of the cycle (Section 7.4).  The useful part of the saving (not re-running a hopeless issue) is already implemented by `bin/ledger_check.py:32,55` and by the failover thresholds the dial would merely re-label.

### What would flip the verdict to Proceed

The verdict is a statement about *this* corpus and *this* tree, so it is falsifiable:

* Enable `workers.goldfish.enabled` and `workers.jittest.enabled` in a live deployment (operator decision, `config/deployment.example.json:60,69`), then run **≥30 cycles per arm** with the level recorded on the ledger row, and compare PR-opened rate, median `grade`, and shepherd fix attempts.  If `standard` shows a ≥10 pt higher PR-opened rate or ≥1 pt higher median grade than `minimal` at ≤1.3× the total tokens, the dial earns its place — and the level must then be recorded per run (a ledger schema change, `docs/ARCHITECTURE.md:47`) for the comparison to be possible at all.
* Build the per-path resolver first (as its own spike) and show it resolves a path→level map for the knobs that exist; only then is a "one dial" over them meaningful.

### Recommended narrower alternative (Phase 1b, if the operator still wants a dial)

Drop the per-path dimension and the QA knobs; keep **per-repo** only, over knobs that are global, enabled by default, and per-run scoped:

| knob | minimal | standard | full |
|---|---|---|---|
| `workers.max_steps` | 30 | 60 | 100 |
| `workers.failover.failure_threshold` / `flag_after` | 1 / 2 | 2 / 3 | 3 / 5 |
| `workers.failover.tier_ladder` | `["cheap","balanced"]` | `["cheap","balanced","flagship"]` | same |
| `shepherd.max_fix_attempts` | 0 | 2 | 3 |
| `supervisor.max_parallel` | 2 | 6 | 10 |

That subset is real, measurable, and implementable as a config-driven
resolver with **no prompt edit** — but it is a *per-repo cost dial*, not
the issue's "one front door over the brief/review/QA knobs", and it must
still be justified by the ≥30-cycles-per-arm measurement above.

## 9. Scope of this change and test evidence

* Files changed: **this report only** — `docs/spikes/rigor-dial-phase0.md`.
  `config/deployment.example.json` was **deliberately not touched**: no
  runtime code reads a `modes` section, the config loader
  (`bin/trace_summary.py:36-54`) ignores unknown keys, and shipping an
  inert knob in the operator's template would be misleading.  The schema
  lives in Section 4 of this report instead.
* Nothing under `bin/`, `prompts/`, `tests/` or
  `config/deployment.local.json` was read-modified or written; no prompt
  template, cron body or runtime behaviour changed.
* Test suite (run on the tree with this report added):

```
$ python3 -m pytest tests/ -q
........................................................................ [ 48%]
........................................................................ [ 73%]
.................................................................        [ 96%]
.........                                                                [100%]
282 passed, 2 skipped, 13 subtests passed in 21.17s
```

  The same tail line was produced before the report was added (baseline)
  and after, so the tree is green and this report introduces no
  regression.
