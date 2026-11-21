# nomore-mined — Phase 0 measurement report (issue #70)

**Scope:** Phase 0, MEASUREMENT ONLY. No code, no prompt, no config change.
Zero model spend. One new file: `docs/nomore-phase0-report.md`.

**Status:** report delivered. Verdict is **Kill** on the evidence reachable from
this worktree — see §7 for the full reading and the contradiction in the
issue's own decision bars.

---

## 1. Headline finding (read this first)

**The corpus the issue asks for — supervisor briefs, shepherd intervention
messages and harness-tune directives recoverable from transcripts/ledgers — is
NOT reachable from this worktree.** The live harness state directory
(`{{state.dir}}`, default `~/.plan-coder/state`, holding `worker_ledger.jsonl`,
`shepherd_ledger.jsonl` and `transcripts/`) lives outside the repository and is
operator-private. `config/deployment.local.json` is gitignored and not present
here either. Nothing under `transcripts/` is committed (`.gitignore` lists
`transcripts/`, `worker_ledger.jsonl`, `shepherd.json`, `workers.json`).

Therefore this report does **not** publish a census of live briefs/messages.
It publishes two censuses over the corpus that *is* reachable inside this
repo, and it states the limitation as the primary result rather than inventing
live-corpus numbers:

| # | Reachable census | What it measures | Window | n |
|---|---|---|---|---|
| **A** | Working-tree instruction corpus (repo @ `46fd2c6`) | repeated instruction text across every instruction-bearing file in the repo | snapshot @ `46fd2c6` (2026-11-18) | 16 files, **438 instruction spans** |
| **B** | Git history of the three **live** prompt templates | repeated *corrections*: an instruction dropped from a live prompt body and later re-stated | **2026-10-24 → 2026-11-18** (26 days, 30 commits, 45 file-revisions) | 253 introduction events |

Census B is the closest reachable proxy for the live corpus the issue names:
the three prompt templates are `Weekly sync from live harness` artefacts, so
their git history is a real, timestamped trace of the live supervisor /
shepherd / harness-tune instruction bodies as the operator changed them.

**Headline numbers.**

- Census A: 438 spans → 400 clusters → **8 clusters with count ≥ 3**
  (**1 class (U)**, **7 class (C)**).
- Census B: 253 instruction-introduction events over 26 days of live prompt
  history → **7 re-statement events**, **0 instructions re-stated ≥ 3 times**,
  2 clusters with count ≥ 3 (both class (C), both template boilerplate).
- Sampled precision of the clustering rule: **13/14 = 92.9 %** (§6).
- Verdict: **Kill** (§7).

---

## 2. Method

### 2.1 Corpus definition (census A)

Instruction-bearing files reachable in the repo, grouped by the surface they
feed:

| Surface | Files | Spans |
|---|---|---:|
| supervisor directives | `prompts/backlog-supervisor.md` | 118 |
| shepherd messages | `prompts/pr-shepherd.md` | 37 |
| harness-tune directives | `prompts/harness-tune.md` | 36 |
| worker briefs | `examples/BRIEF.example.md`, `examples/BRIEF.gap.example.md`, `examples/BRIEF.visual.example.md` | 22 |
| worker system prompt | `bin/agent.py` (`SYSTEM_PROMPT`, `MIDRUN_GATE_PROMPT`, `CODE_MAP_PROMPT`) | 14 |
| hypothesis brief | `bin/jittest.py` | 21 |
| manager docs | `AGENTS.md`, `CLAUDE.md`, `SKILL.md`, `docs/MANAGER.md`, `docs/OPERATIONS.md` | 139 |
| clause registry | `bin/prompt_check.py`, `bin/prompt_check_supervisor.py`, `bin/prompt_check_harness_tune.py` | 51 |
| **total** | **16 files** | **438** |

### 2.2 Instruction-shaped span extraction

A span is an *instruction* if it is 25–700 characters and either

1. its first token is an imperative verb from a fixed 70-word list
   (`never`, `do not`, `run`, `read`, `write`, `keep`, `must`, `only`, …), or
2. it contains a directive marker (`never`, `do not`, `must `, `always`,
   `only `, `shall `, `is required`, `not allowed`, `forbidden`).

Spans are sentence-level: each source line is split on `. ! ? : ;` followed by
an opening capital/backtick/quote, so a multi-clause paragraph yields one span
per clause.

### 2.3 Normalisation (printed rule)

Applied in order, then lower-cased and whitespace-collapsed:

1. backticks removed;
2. `{{token}}` templates → `{{token}}`;
3. file paths (`bin/x.py`, `docs/y.md`, …) → `<path>`;
4. `issue 12` / `PR #34` / `repo #7` → `issue <num>` / `pr <num>` / `repo <num>`;
5. `#12` → `#<num>`;
6. every remaining integer → `<num>`;
7. non `[a-z0-9_<>#.-]` → space; whitespace collapsed; leading/trailing
   `. ; : -` stripped.

### 2.4 Clustering rule (printed rule)

> Two spans join the same cluster when their normalised forms are **identical**,
> **or** when their token sets have **Jaccard overlap ≥ 0.8**.
> Token set = the normalised string split on every non-`[a-z0-9_<>#]` character.
> Clustering is single-link with a post-merge pass over cluster pairs.

Applied to 438 spans this yields 400 clusters; 8 have count ≥ 3.

### 2.5 (U)/(C) classification rule (printed rule)

The **codified baseline** is exactly the issue's list:
`bin/agent.py` `SYSTEM_PROMPT`, `prompts/backlog-supervisor.md`,
`prompts/pr-shepherd.md`, `prompts/harness-tune.md` (205 normalised baseline
spans at `HEAD`).

> A cluster is **(C) codified-but-still-repeated** if any of its spans matches
> a baseline span by exact normalised equality, or by token-set Jaccard ≥ 0.8,
> or by token containment ≥ 0.7 (baseline tokens ⊇ span tokens).
> A cluster is **(U) uncodified repeat** if no baseline span matches by any of
> those three tests.

Containment ≥ 0.7 is included because it catches a rule that is *re-worded*
rather than copied; the mechanical test is deliberately mechanical, and §6
records where it disagrees with a human reading.

### 2.6 Census B (history) definitions

For each of the three live prompt templates, `git log --reverse` over the file
gives the ordered revision series (2026-10-24 → 2026-11-18). For every
revision the instruction spans are extracted and normalised (§2.2–2.3). A span
entering the normalised set at revision *n* while absent at *n-1* is an
**introduction event**; if that same normalised span had been present at some
earlier revision, the event is a **re-statement** — i.e. the operator had to
say it again. Re-statements are the "repeated correction" signal.

---

## 3. Census A — clusters with count ≥ 3 (working tree)

n = 438 spans, 16 files, snapshot @ `46fd2c6`. All 8 clusters, with sources.

| ID | n | class | normalised instruction (excerpt) | sources |
|---|---:|---|---|---|
| R01 | 4 | C | `a test that passed before your change and fails after is your regression never hand-wave it as pre-existing` | `examples/BRIEF.example.md:48`, `examples/BRIEF.gap.example.md:39`, `examples/BRIEF.visual.example.md:40`, `prompts/backlog-supervisor.md:24` |
| R02 | 3 | C | `never touch branches that are not worker` | `bin/prompt_check.py:126`, `docs/MANAGER.md:287`, `prompts/pr-shepherd.md:12` |
| R03 | 3 | C | `do not create issues yourself` | `bin/prompt_check_supervisor.py:81`, `bin/prompt_check_supervisor.py:239`, `prompts/backlog-supervisor.md:22` |
| R04 | 3 | C | `every brief opens with two header blocks that never merge scope` | `bin/prompt_check_supervisor.py:127`, `bin/prompt_check_supervisor.py:240`, `prompts/backlog-supervisor.md:24` |
| R05 | 3 | C | `never delete move or rename this worktree` | `examples/BRIEF.example.md:13`, `examples/BRIEF.gap.example.md:13`, `examples/BRIEF.visual.example.md:13` |
| R06 | 3 | **U** | `if an item above cannot be produced say so in the report the supervisor records it as an explicit proof gap line it is never silently dropped` | `examples/BRIEF.example.md:21`, `examples/BRIEF.gap.example.md:21`, `examples/BRIEF.visual.example.md:21` |
| R07 | 3 | C | `verify don t trust run the verification commands before finishing fix failures you introduced` | `examples/BRIEF.example.md:47`, `examples/BRIEF.gap.example.md:38`, `examples/BRIEF.visual.example.md:39` |
| R08 | 3 | C | `<path> substitutes the deployment config s {{token}} and <path> installs the rendered body into the scheduler edit here or in the config never in the scheduler directly` | `prompts/backlog-supervisor.md:3`, `prompts/harness-tune.md:3`, `prompts/pr-shepherd.md:3` |

Class reasoning per cluster (from the mechanical rule of §2.5):

| ID | class reason | human read |
|---|---|---|
| R01 | exact normalised match with baseline span in `prompts/backlog-supervisor.md` | agrees (C) |
| R02 | Jaccard ≥ 0.8 vs `prompts/pr-shepherd.md` | agrees (C) |
| R03 | exact normalised match with baseline span in `prompts/backlog-supervisor.md` | agrees (C) |
| R04 | exact normalised match with baseline span in `prompts/backlog-supervisor.md` | agrees (C) |
| R05 | containment ≥ 0.7 vs `prompts/backlog-supervisor.md` | agrees (C) — the prompt carries the same rule re-worded |
| R06 | **no baseline span matches** (exact / Jaccard ≥ 0.8 / containment ≥ 0.7) | **disagrees** — see below |
| R07 | containment ≥ 0.7 vs `prompts/backlog-supervisor.md` | agrees (C) |
| R08 | exact normalised match with baseline span in all three prompts | agrees (C), but see caveat |

**R06 is a mechanical false negative of the (C) test.** The rule "if an item
above cannot be produced, say so in the report … never silently dropped" is
codified in the baseline at `prompts/backlog-supervisor.md:44` in re-worded
form ("If any proof item the brief's `PROOF:` block required is missing, the
section says so explicitly as `proof gap: <what is missing>` — never silently
omit it"). The best token containment of R06 against that baseline span is
**0.458** — below the 0.7 gate — so the mechanical rule labels it (U). A human
reader says the rule *is* codified. Consequence: the true (U) count is
**0, not 1**; the mechanical count is 1. Both are reported; §7 uses the
conservative reading.

**R08 caveat.** R08 is a pure documentation header ("`bin/render_prompts.py`
substitutes … edit here … never in the scheduler directly"). It is a genuine
near-duplicate string across the three prompt files, so it is a true cluster,
but it is **template boilerplate, not an instruction to a model at runtime** —
it consumes no per-cycle model tokens in any meaningful sense. It should not
be counted as an actionable (C) rule. Actionable class-(C) clusters: **6**
(R01–R05, R07).

### 3.1 Second-tier clusters (count = 2)

The census reports clusters with count ≥ 3. For the false-positive audit
(§6) the six largest count = 2 clusters were also read by hand:

| ID | n | class | excerpt | sources |
|---|---:|---|---|---|
| X01 | 2 | C | `write the full new content to a staging file beside it <file> .new … state_write.py commit …` | `prompts/backlog-supervisor.md:16`, `prompts/pr-shepherd.md:29` |
| X02 | 2 | C | `emit {{token}} with the script s one-line reason and its evidence lines and leave the pr for {{token}}` | `prompts/pr-shepherd.md:14`, `prompts/pr-shepherd.md:15` |
| X03 | 2 | C | `name a deliverable only when the issue genuinely requires that specific artefact …` | `examples/BRIEF.example.md:29`, `prompts/backlog-supervisor.md:24` |
| X04 | 2 | C | `a missing goldfish ledger means the spike has not logged a review yet …` | `prompts/harness-tune.md:12`, `prompts/harness-tune.md:13` |
| X05 | 2 | C | `verify don t trust run the brief s test commands before finishing fix failures you introduced` | `docs/OPERATIONS.md:25`, `prompts/backlog-supervisor.md:24` |
| X06 | 2 | C | `the run lifecycle the never-dos where each operation lives how to see` | `CLAUDE.md:6`, `SKILL.md:15` |

X02 is a **verbatim duplicate inside one live prompt body**
(`prompts/pr-shepherd.md:14` and `:15` are the same sentence, character for
character) — the only true intra-context duplication found in the three live
prompts. X04 pairs two *different* rules (goldfish ledger vs JiTTest ledger)
that merely share a template — a clustering false positive (§6).

---

## 4. Census B — repeated corrections in the live prompt history

Window **2026-10-24 → 2026-11-18** (26 days). 30 commits touching
`prompts/`; file revisions: `backlog-supervisor.md` 18, `pr-shepherd.md` 14,
`harness-tune.md` 13 (45 file-revisions total).

| metric | value |
|---|---:|
| instruction-introduction events | 253 |
| **re-statement events** (span absent at *n-1*, present at *n*, and present at some earlier revision) | **7** |
| distinct instructions re-stated ≥ 3 times | **0** |
| clusters with count ≥ 3 | 2 (both class (C)) |
| clusters with ≥ 3 *re-statements* | 0 |

The 7 re-statement events, all class (C) (already codified in the baseline):

| date | file | re-stated instruction (normalised excerpt) |
|---|---|---|
| 2026-10-25 | `prompts/backlog-supervisor.md` | `one worktree per worker never two git-writing workers in one tree` |
| 2026-11-13 | `prompts/pr-shepherd.md` | `emit {{token}} with the script s one-line reason and its evidence lines and leave the pr for {{token}}` |
| 2026-11-13 | `prompts/pr-shepherd.md` | `exit <num> means the pr modifies or deletes existing test files without brief authorization …` |
| 2026-11-13 | `prompts/pr-shepherd.md` | `leave it for {{token}} with the flag reason and evidence and never clear the flag yourself` |
| 2026-11-13 | `prompts/pr-shepherd.md` | `new test files added by the pr are never a flag` |
| 2026-11-18 | `prompts/pr-shepherd.md` | `exit <num> means no pre-existing judging file was modified routine ratchet inventory touches …` |
| 2026-11-18 | `prompts/pr-shepherd.md` | `exit <num> means the pr modifies or deletes a pre-existing judging file a gate check script a lint config …` |

All 7 cluster on the test-tamper / judging-file flag rules added in issues
#15 and #96 — i.e. they are *re-wordings introduced by the feature commits
themselves*, not an operator re-stating a correction that the harness had
forgotten. The 2 clusters with count ≥ 3 in census B are the "write a precise
brief to …" supervisor step and the R08 prompt header; neither is a repeated
correction.

**Reading:** across 26 days of live-synced prompt history the live harness
re-stated a given instruction **7 times total**, and **never** the same
instruction three times. There is no repeated-correction churn in the live
*prompt* layer over this window. The repetition nomore-mined would mine lives
in the *brief layer* (what the supervisor writes into each BRIEF.md) and in
the *shepherd message layer* — which are not in this repo.

---

## 5. Waste estimate for class (U) — method and result

### 5.1 Method (stated in full, so it can be re-run)

1. **Unit of waste.** The repeated instruction's *character length* is taken
   from its shortest cluster member; tokens = characters ÷ 3.7 (English prose
   average for model-facing text).
2. **Copies per delivery.** Class-(U) rules are, by definition, absent from
   the codified baseline — so they can only live in the per-brief text. Each
   brief carries **1** copy.
3. **Delivery rate.** From `config/deployment.example.json` (the committed
   example; `deployment.local.json` is operator-private and was not read):
   supervisor `interval_hours = 2` → **12 cycles/day**;
   `max_parallel = 6` → **≤ 6 briefs/cycle** → **≤ 72 briefs/day**.
   Shepherd `interval_minutes = 30` → 48 cycles/day. Harness-tune: weekly.
4. **Window.** The census-B window, 26 days.
5. **Counterfactual.** If the rule were codified once in the baseline
   (`prompts/backlog-supervisor.md`) and the brief referenced it by one line
   (~59 chars ≈ 16 tokens) instead of restating it, the per-brief cost drops
   from 40.3 to 16.0 tokens.
6. **Upper bound caveat.** 72 briefs/day is the configured *ceiling*
   (`max_parallel`), not an observed rate; there is no ledger in this worktree
   to measure the real rate. The figure is therefore an upper bound on the
   waste, and is reported as such.

### 5.2 Result

Class (U) clusters with count ≥ 3: **1** (R06). (By the human read in §3,
R06 is in fact already codified in re-worded form, so the *actionable* (U)
count is **0** and the (U) waste is **0 tokens**.)

Mechanical estimate for R06 as if it were genuinely uncodified:

| term | value |
|---|---:|
| instruction length (shortest member) | 149 chars |
| tokens per copy | 40.3 |
| briefs/day (ceiling) | 72 |
| waste/day | 2 899 tokens |
| waste over 26-day window | **75 386 tokens** |
| saving if codified once and referenced | 24.3 tokens/brief → **45 535 tokens / 26 days** |

Supplementary (not class (U), reported for completeness): the one true
intra-context duplication, X02 in `prompts/pr-shepherd.md:14-15` (126 chars
verbatim twice), costs **34.1 redundant tokens per shepherd delivery** =
1 635 tokens/day = **42 499 tokens over the 26-day window**. This is a
one-line deletion in a prompt body — a prompt edit, which this Phase 0 does
**not** perform (scope: measurement only).

**Total measured waste of repeated instruction text in the reachable corpus:
≈ 118 k tokens / 26 days** = 75.4 k (class-(U) R06, mechanical view) + 42.5 k
(the shepherd intra-context duplicate X02). The *avoidable* part of the (U)
figure is 45.5 k (the difference between restating the rule and referencing
it); under the human read of §3 the (U) figure is 0 and the avoidable total is
42.5 k. Context for scale: one burned worker run in this repo's own
supervisor prompt is recorded as "~12M tokens".

---

## 6. False-positive audit

**Method.** Hand-read (read the actual source text, not the normalised form)
every cluster with count ≥ 3 — 8 clusters — plus the six largest count = 2
clusters, for **14 audited clusters**. A cluster is *genuine* when its members
state **the same instruction** (same rule, re-worded or copied) and the
cluster is not an artefact of parallel wording.

| ID | n | verdict | why |
|---|---:|---|---|
| R01 | 4 | genuine | identical sentence in 3 brief templates + the supervisor prompt's Code-discipline section |
| R02 | 3 | genuine | same rule ("never touch non-`worker/*` branches") in the clause registry, `docs/MANAGER.md` and the shepherd prompt |
| R03 | 3 | genuine | identical sentence; the two registry hits are the same literal string stored twice in `bin/prompt_check_supervisor.py` |
| R04 | 3 | genuine | identical sentence, same duplication shape as R03 |
| R05 | 3 | genuine | identical sentence in all three brief templates |
| R06 | 3 | genuine (cluster) / **mis-classed** | genuine cluster (same rule), but the rule *is* codified in re-worded form → mechanical (U) is wrong |
| R07 | 3 | genuine | identical sentence in all three brief templates |
| R08 | 3 | genuine | byte-identical header in all three prompts — genuine duplication, but boilerplate, not a runtime instruction |
| X01 | 2 | genuine | same atomic state-write rule, two files, one file-name difference |
| X02 | 2 | genuine | byte-identical sentence twice inside one prompt body |
| X03 | 2 | genuine | identical sentence in brief template + supervisor prompt |
| X04 | 2 | **false positive** | goldfish-ledger rule vs JiTTest-ledger rule — *different* rules, parallel wording; token overlap 0.83 |
| X05 | 2 | genuine | same rule in `docs/OPERATIONS.md` and the supervisor prompt |
| X06 | 2 | genuine | same pointer sentence in `CLAUDE.md` and `SKILL.md` (near-duplicate doc prose) |

**Sampled precision = 13 genuine / 14 audited = 92.9 %** (cluster-level
precision: is the cluster a real repeated instruction?).

Restricted to the reported set (count ≥ 3): **8/8 = 100 %**. If R08 is
excluded as non-actionable boilerplate: 7/8 = 87.5 %.

**Class-level precision (the (U)/(C) rule)**: 13/14 clusters correctly classed;
**1 error (R06)**, a false *(U)* — the rule is codified in re-worded form.
So the mechanical (U)/(C) split is ~93 % precise on this sample, and the
mechanical (U) count of 1 should be read as **0**.

---

## 7. Verdict against the issue's decision bars

The issue's bars, quoted:

> Proceed: ≥ 5 genuine (U) rules at ≥ 70 % sampled precision, **or** ≥ 3 (C)
> rules. Kill: genuine (U) < 3 **or** precision < 50 %.

**Measured (census A, the only census of briefs/messages/directives reachable
from this worktree):**

| bar | measured | met? |
|---|---|---|
| ≥ 5 genuine (U) rules | **0** (mechanical 1, human 0) | no |
| ≥ 3 (C) rules | 7 mechanically, **6 actionable** (R08 is boilerplate) | yes, mechanically |
| sampled precision ≥ 70 % | 92.9 % | yes |
| genuine (U) < 3 → Kill | **0 < 3 → Kill** | **Kill fires** |
| precision < 50 % → Kill | 92.9 % | no |

### The bars are mutually contradictory on this data — reported, not resolved by guessing

With (U) = 0 and (C) = 6–7, the **Kill** condition ("genuine (U) < 3") and the
**Proceed** condition ("or ≥ 3 (C) rules") fire simultaneously. The issue
text as quoted does not say which wins. I take the following reading and state
it explicitly:

1. **The (C) branch of Proceed does not transfer.** The (C) clusters measured
   here are *static* duplication between files in one repository (a brief
   template vs. the supervisor prompt; a clause registry vs. the prompt; the
   same header in three prompt files). nomore-mined's (C) class is meant to
   capture a rule that is codified *and yet re-stated at runtime in successive
   briefs/messages* — a per-cycle token cost. Static in-repo duplication is
   not that: R01/R05/R07 cost nothing extra at runtime, because a given
   brief is read once and the prompt is read once by a different actor.
2. **The (U) branch is the actionable one and it is empty.** The uncodified
   repeat count is 0 by human read, 1 by mechanical rule, against a bar of 5.
3. **The corpus the bars were written against was not available.** §1.

**Verdict: KILL** — do not proceed to a nomore-mined implementation on the
evidence reachable from this worktree. The (U) bar (≥ 5) fails by the full
margin (0 vs 5), and the (C) branch that would otherwise trigger Proceed is
satisfied only by static in-repo duplication that carries no runtime cost and
therefore no motivation for the tool.

**What would change this verdict.** The measurement is one command away from
the real corpus, and the census script is deterministic and zero-cost. Against
`{{state.dir}}/worker_ledger.jsonl`, `{{state.dir}}/shepherd_ledger.jsonl`
and `{{state.dir}}/transcripts/*.jsonl` the same extractor would yield the
(U)/(C) split the issue's bars were calibrated for, over briefs, shepherd
intervention messages and harness-tune directives as actually delivered.
That run needs the live state directory, which this worktree does not have.
Two secondary findings are already actionable and are out of scope for this
measurement-only phase: the verbatim duplicate at `prompts/pr-shepherd.md:14-15`
(§3.1, ~42.5 k tokens/26 days) and the three identical brief-template
clauses (R01/R05/R07) that could be referenced from one place.

---

## 8. Reproduction

All commands are deterministic, offline and zero-model-spend.

```bash
# census A — working-tree corpus, spans → clusters → (U)/(C) split
python3 /tmp/nomore/final_census.py .            # prints JSON: spans=438, clusters_ge3=8, u=1, c=7

# census B — repeated corrections in the live prompt history
python3 /tmp/nomore/hist_final.py .               # prints JSON: events=253, re_statement_events=7, u=0, c=2

# repo test suite (unchanged by this phase; no code was touched)
python3 -m pytest tests/ -q
# 567 passed, 2 skipped, 62 subtests passed in 42.06s
```

The census scripts live under `/tmp` and are deliberately **not** part of the
diff, per the repo's own scratch-file convention (`AGENTS.md` §(f):
"helper scripts live under /tmp … and must never appear in the final diff").

---

## 9. Limitations

1. **The live corpus is absent.** No transcripts, no ledgers, no
   `deployment.local.json`. All counts are over the in-repo corpus of §2.1.
   Census A is a *proxy*, not the target measurement.
2. **Census B measures the prompt layer only.** The live prompt templates are
   the corpus the issue calls "supervisor briefs / shepherd messages /
   harness-tune directives" only in template form; the *rendered, per-cycle*
   bodies and the supervisor-written `BRIEF.md` files are not in the repo.
3. **Clustering thresholds are choices.** Jaccard ≥ 0.8 and containment ≥ 0.7
   were fixed before reading results and are printed in §2.4/§2.5; one
   hand-read disagreement (R06) is recorded rather than absorbed by tuning.
4. **Waste figures are upper bounds** (§5.1, step 6) — no ledger to measure
   the real brief rate.
5. **Token conversion is a constant** (3.7 chars/token), not a tokenizer.
6. `ruff` is not installed in this environment, so the structured lint
   diagnostics command (`ruff check --output-format=json`) could not be run;
   no source file was modified, so lint state is unchanged from `46fd2c6`.
