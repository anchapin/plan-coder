# Prompt self-testing — Phase 0 spike (issue #55)

**Phase 0 only: measurement + a deterministic seed of prompt self-testing for
ONE prompt (`prompts/pr-shepherd.md`).**  The live prompt's behaviour does not
change in this phase — the prompt file is read-only input here.  The deliberate
regression introduced for the keep/revert demonstration lives **only** in a
fixture copy under `tests/fixtures/prompts/`.

A prompt edit in plan-coder is today validated by render checks and live
cycles; `bin/` code has a regression suite, prompts have none.  Phase 0 asks a
narrow question: *can a prompt get a deterministic, offline score that catches
a broken prompt edit the way the test suite catches a broken module?*

**Answer: yes, at zero LLM cost.**  The seed below scores the shepherd prompt
100/100 as it stands; a copy with one merge-policy sentence removed scores
86.7/100 and the failing specs name the removed clause.

---

## 1. What was built

| artifact | role |
|---|---|
| `bin/prompt_check.py` | the runner: static checks + behavioural specs, one score, `--json`, exit 0/1 |
| `tests/fixtures/prompt_specs/pr-shepherd.json` | 10 behavioural specs (9 deterministic + 1 model-judgement, excluded) |
| `tests/fixtures/prompt_specs/pr-shepherd-regression.json` | the deliberate-regression recipe (which clause is removed, which specs must fail) |
| `tests/fixtures/prompts/pr-shepherd-known-failure-clause-removed.md` | the fixture COPY of the prompt with the known-failure exemption deleted |
| `tests/test_prompt_check.py` | 17 tests: live prompt green, regression caught, each static check fires, judgement spec excluded, runner never writes to the prompt |

Nothing outside `bin/`, `tests/` and `docs/` was touched.  `prompts/` is
byte-identical to the base commit (verified by `git status`: no modified
files under `prompts/`).

### How the score works

* 4 static checks carry 40 points; the 9 deterministic behavioural specs carry
  60 points; the score is `100 * (earned / 100)`, one number.
* Every rule in the rule engine is **gated on the clause of the prompt that
  states it**.  Delete the clause and the scenario is no longer decidable the
  way the prompt says it is: the engine returns `hold_unstated` and the spec
  fails *naming the clause*.  That is what makes the demo below work.
* A spec that would need a model judgement is marked `deterministic: false`,
  is reported as `SKIP`, and is **excluded from the score** — not faked into
  determinism.  The shipped example is `diff_scope_judgement` ("is a 400-line
  refactor a well-scoped change with clear intent?").

---

## 2. Static checks (4, all green on the live prompt)

| check | what it asserts | the failure it prevents |
|---|---|---|
| `required_sections` | `MERGE POLICY`, `NEVER merge`, `gh pr merge --merge`, `MERGE CONFLICTS`, `TRIGGER REQUESTED`, `{{operator.flag_label}}` are present in the body | an edit that quietly drops a merge-policy section |
| `tokens_resolved` | every `{{token}}` in the body resolves against a synthetic config; no unresolved token survives | a broken template (the renderer aborts on unresolved tokens, but only for the tokens the *config* happens to carry) |
| `bin_paths_exist` | every `bin/*.py` path the prompt names — `{{skill.dir}}/bin/x.py` **and** bare `bin/x.py`, in body *and* header — exists in `bin/` | the **2026-11-08 stale-path incident**: a prompt naming a renamed `bin/` path |
| `length_budget` | body length in [3000, 20000] chars, 4–12 numbered steps, no step over 8000 chars | a prompt that has grown past what the shepherd can follow |

The `bin_paths_exist` check is the motivating one: it is a stale-reference
check over the prompt's own text, exactly like a link checker, and it is the
failure mode the Nov 8 incident produced.

---

## 3. Behavioural specs (10 scenarios, 9 deterministic)

Each spec is a small input-state snippet (the PR as the shepherd sees it) and
the decision the prompt's own rules imply.

| spec | input state | expected decision |
|---|---|---|
| `merge_green_worker_branch` | green PR-specific CI, `worker/*` head, fresh SHA, diff reviewed, no flags | `merge` |
| `merge_blocked_non_worker_branch` | green CI, head branch `main` | `flag` (never merge) |
| `merge_blocked_stale_sha` | head moved since the CI check | `hold_stale_sha` (re-triage next cycle) |
| `merge_unstable_known_failure_exempted` | `mergeStateStatus` UNSTABLE, only pre-existing develop-wide failures already tracked | `merge` |
| `merge_blocked_unstable_new_failure` | `mergeStateStatus` UNSTABLE with a new PR-specific failure | `flag` |
| `merge_blocked_workflow_touch` | PR touches `.github/workflows` | `flag` (workflow-touch hard stop) |
| `merge_blocked_jittest_catch` | a catching JiTTest survived against the PR | `flag` |
| `flaky_ci_rerun_once` | runner died / timeout / rate limit | `rerun` (once) |
| `conflicting_pr_skips_ci_fix` | `mergeable` CONFLICTING | `merge_conflicts` (skip CI fixing) |
| `diff_scope_judgement` | "is this diff well-scoped?" | **excluded** — model judgement, `SKIP`, not scored |

Coverage of the prompt's decision rules: merge bar (5 specs), hard stops
(2), known-failure exemption (2), workflow-touch flag (1), CI triage (2).

---

## 4. Keep/revert demonstration (on a COPY only)

The regression: the known-failure exemption sentence is deleted from a **copy**
of the prompt —

> ~~`mergeStateStatus` CLEAN is preferred, but UNSTABLE is acceptable when the
> only failures are pre-existing develop-wide issues already tracked in
> follow-up issues ({{merge_policy.known_failures}}) — these must not block
> merges.~~

The live prompt in `prompts/` is untouched.

**Before (intact live prompt):**

```
$ python3 bin/prompt_check.py
prompt: prompts/pr-shepherd.md
static checks (4/4 passed):
  PASS required_sections -- all required phrases present
  PASS tokens_resolved -- every token renders (body 9617 chars)
  PASS bin_paths_exist -- 5 bin path(s) referenced, all exist
  PASS length_budget -- body 9991 chars, 5 steps, longest step 5525 chars
behavioural specs (9/9 deterministic passed, 1 excluded):
  PASS merge_green_worker_branch -> merge (expected merge)
  PASS merge_blocked_non_worker_branch -> flag (expected flag)
  PASS merge_blocked_stale_sha -> hold_stale_sha (expected hold_stale_sha)
  PASS merge_unstable_known_failure_exempted -> merge (expected merge)
  PASS merge_blocked_unstable_new_failure -> flag (expected flag)
  PASS merge_blocked_workflow_touch -> flag (expected flag)
  PASS merge_blocked_jittest_catch -> flag (expected flag)
  PASS flaky_ci_rerun_once -> rerun (expected rerun)
  PASS conflicting_pr_skips_ci_fix -> merge_conflicts (expected merge_conflicts)
  SKIP diff_scope_judgement -- Is a 400-line refactor 'a well-scoped change with clear intent'? The prompt sets the bar in prose; no mechanical assertion can decide it. (excluded: needs a model judgement (diff scope review) -- excluded from the deterministic score, not faked)
score: 100.0/100
result: PASS
$ echo $?
0
```

**After (the fixture copy with the clause removed):**

```
$ python3 bin/prompt_check.py --prompt tests/fixtures/prompts/pr-shepherd-known-failure-clause-removed.md
prompt: tests/fixtures/prompts/pr-shepherd-known-failure-clause-removed.md
static checks (4/4 passed):
  PASS required_sections -- all required phrases present
  PASS tokens_resolved -- every token renders (body 9407 chars)
  PASS bin_paths_exist -- 5 bin path(s) referenced, all exist
  PASS length_budget -- body 9764 chars, 5 steps, longest step 5298 chars
behavioural specs (7/9 deterministic passed, 1 excluded):
  PASS merge_green_worker_branch -> merge (expected merge)
  PASS merge_blocked_non_worker_branch -> flag (expected flag)
  PASS merge_blocked_stale_sha -> hold_stale_sha (expected hold_stale_sha)
  FAIL merge_unstable_known_failure_exempted -> hold_unstated (expected merge)  [expected merge, rule engine gave hold_unstated (clause missing: known_failure_exemption)]
  FAIL merge_blocked_unstable_new_failure -> hold_unstated (expected flag)  [expected flag, rule engine gave hold_unstated (clause missing: known_failure_exemption)]
  PASS merge_blocked_workflow_touch -> flag (expected flag)
  PASS merge_blocked_jittest_catch -> flag (expected flag)
  PASS flaky_ci_rerun_once -> rerun (expected rerun)
  PASS conflicting_pr_skips_ci_fix -> merge_conflicts (expected merge_conflicts)
  SKIP diff_scope_judgement -- Is a 400-line refactor 'a well-scoped change with clear intent'? The prompt sets the bar in prose; no mechanical assertion can decide it. (excluded: needs a model judgement (diff scope review) -- excluded from the deterministic score, not faked)
score: 86.7/100
result: FAIL
$ echo $?
1
```

**Revert (score the live prompt again):**

```
$ python3 bin/prompt_check.py
... (tail of the output) ...
score: 100.0/100
result: PASS
$ echo $?
0
```

| state | score | exit | failing specs |
|---|---|---|---|
| intact live prompt | **100.0 / 100** | 0 | — |
| fixture copy, known-failure clause removed | **86.7 / 100** | 1 | `merge_unstable_known_failure_exempted`, `merge_blocked_unstable_new_failure` (both name `known_failure_exemption`) |
| reverted (live prompt again) | **100.0 / 100** | 0 | — |

The same result is reachable without touching any file, via the runner's
mutation switch (which builds the mutated copy in a temp dir and deletes it):

```
$ python3 bin/prompt_check.py --remove-clause known_failure_exemption
prompt: prompts/pr-shepherd.md
mutation: clause 'known_failure_exemption' removed from a temporary copy
... (identical spec lines to the fixture-copy run above) ...
score: 86.7/100
result: FAIL
```

The 13.3-point drop is exactly two of the nine deterministic specs (2 × 60/9)
— the two that depend on the removed clause.  The static checks stay green,
which is the point of having both layers: **the static layer cannot see a
missing rule, the behavioural layer can.**

---

## 5. Cost per full run

| item | cost |
|---|---|
| LLM tokens per run | **0** — the runner never calls a model; every assertion is a string/JSON/regex check over the prompt text |
| wall time per run | **≈ 0.09 s** (median of 5, measured on this worktree: 0.075–0.089 s) |
| network / credentials | none — hermetic, works on a public clone |
| cost of the new tests in the repo suite | +17 tests, ≈ 1.8 s (`python3 -m pytest tests/test_prompt_check.py -q` → `17 passed in 1.79s`) |
| full repo suite | `python3 -m pytest tests/ -q` → `248 passed, 2 skipped, 13 subtests passed in 44.76s` |

Cost is therefore **effectively free**: a fraction of a second of CPU and zero
tokens.  A score check is cheap enough to run on every prompt edit, which is
what makes it usable as a gate.

---

## 6. Proceed / Kill

**Recommendation: PROCEED to Phase 1 — for the two other prompts and for
wiring a score check into `harness-tune` — with the caveats below.**  The
decision stays with the operator; Phase 0 only supplies the evidence.

*Why proceed:*

1. The seed works on the hardest of the three prompts (the shepherd prompt
   carries the merge policy, the hard stops and the known-failure exemption)
   and scores it 100/100 with no false positives.
2. The failure mode the issue cares about is caught: a removed merge-policy
   clause costs 13.3 points, the runner exits non-zero, and the failing spec
   names the clause.  The same machinery covers the 2026-11-08 stale-`bin/`
   path incident through the `bin_paths_exist` check.
3. The cost is zero tokens and ~0.09 s, so it can gate every prompt edit
   rather than run occasionally.
4. The scoring is explainable: every point maps to a named clause of the
   prompt, so a failing score says *which sentence* is missing, not just
   "the prompt changed".

*What Phase 1 still has to solve (and must not be assumed away):*

* **Clause catalogues are per-prompt.**  `CLAUSES` in `bin/prompt_check.py`
  is written for `pr-shepherd.md`.  `prompts/backlog-supervisor.md` (46 kB,
  issue selection, brief writing, worktree isolation) and
  `prompts/harness-tune.md` (15 kB, the weekly pass) need their own clause
  catalogues, their own spec fixtures and their own required-phrase lists —
  roughly the same amount of work again per prompt, not a copy-paste.
* **The specs are the prompt author's, not the runner's.**  A spec is only
  as good as the rule it encodes; when the prompt's policy legitimately
  changes, the spec must change with it, or the runner will report a
  regression that is actually a decision.
* **Judgement specs stay judgement specs.**  The `diff_scope_judgement` class
  of question (does this diff look right?) cannot be made deterministic
  without an LLM judge, which Phase 0 deliberately does not introduce.  The
  excluded-spec mechanism is the honest home for it.
* **Wiring into `harness-tune` is a policy change, not a code change.**  The
  harness-tune prompt edits other prompts; making it consult a score would
  change what the harness is allowed to do autonomously.  That is the
  operator's call — Phase 0 does not touch `prompts/harness-tune.md`, does not
  touch `bin/harness-tune`-side code, and does not enable anything.

*Kill criteria for Phase 1 (any one of these and the spike should stop):*
the other two prompts cannot be given a stable clause catalogue within a
comparable effort; the score starts flagging deliberate policy changes as
regressions and needs per-run waivers to stay useful; or the runner's
maintenance cost (a clause catalogue per prompt that must track every policy
edit) turns out to exceed the value of catching prompt regressions.

---

## 7. What this phase did NOT do

* No prompt behaviour change: `prompts/pr-shepherd.md` is byte-identical to
  the base commit, as are the other two prompts.
* No rendered cron body was touched; no template was edited.
* No harness-tune integration, no config change, no dependency installed.
* No external eval service, no LLM-eval framework: the runner is stdlib-only
  and deterministic.
