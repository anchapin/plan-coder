# Evaluator self-tests — Phase 0 (plan-coder #141, **Part A only**)

Status: **Part A measurement complete**; the numbers below were recomputed
mechanically from `docs/spikes/evaluator-self-tests-parta-runs.json` and match
that recount exactly. Parts B and C of issue #141 are explicitly **out of scope
for this run** and were not started.

Evaluator class under test: **the shepherd merge/classification evaluator** —
the pre-existing-vs-new failure classification and clean-base claims, as stated
by the clauses actually written in `prompts/pr-shepherd.md`.

Artefacts (the only three files this run created):

| file | role |
|---|---|
| `docs/spikes/evaluator-self-tests-phase0.md` | this report (the deliverable) |
| `docs/spikes/evaluator-self-tests-parta-fixtures.json` | the self-test corpus (one object per fixture) |
| `docs/spikes/evaluator-self-tests-parta-runs.json` | the raw run results (one object per fixture) |

Headline: **catch rate 96.4% (27/28)**, **false-alarm rate 18.2% (2/11)**,
3 misses listed individually in §5.

---

## 0. Scope and hard stops

* Part A only. No Part B, no Part C.
* No prompt edit, no build, nothing installed or enabled. `prompts/`, `bin/`,
  `tests/`, `config/` and every other pre-existing file are byte-identical to
  the base commit (`556ef87`) — `git diff --name-only` is empty (§ Appendix A).
* No `.github/workflows` touch; no spec files exist in this worktree.
* All fixtures are synthetic / mechanically derived. No incident was invented
  beyond the sources named in each fixture's `source_incident` field (§1).

---

## 1. Corpus provenance — and the limitation that goes with it

**The issue body of #141 is not present in this worktree.** This run was given
a narrowed brief naming the incidents the corpus must come from, but the issue
text itself (its §2, its §4, its "Relation to #94" section) is not in the
worktree, and this environment has no network access — `gh issue view 141
--repo anchapin/plan-coder` fails with
`Post "https://api.github.com/graphql": tls: first record does not look like a TLS handshake`
(quoted in Appendix A). Per the brief's rule ("none may be fetched from
outside it"), nothing was fetched.

Consequence, stated plainly rather than papered over:

| what | status |
|---|---|
| Issue #141's incident list | **unavailable in this worktree** (not fetched, cannot be) |
| Primary verdict records for those incidents | **unavailable** — no such records exist inside this worktree |
| Corpus actually built from | the shepherd classification clauses that *are* in this worktree: `prompts/pr-shepherd.md` step 2a / 2b / 2c and the merge-policy UNSTABLE clause; the worktree's own record of the shepherd decision scenarios and their expected decisions, `tests/fixtures/prompt_specs/pr-shepherd.json` (shipped spec fixture, issue #55/#119 lineage); and the clean-base vocabulary in `bin/verify_stamp.py` (`clean_base_verified` = yes / no / not_claimed) and `docs/OPERATIONS.md` (the stale-test protocol) |

This is a **corpus-provenance limitation**, not a hidden substitution: the
corpus measures the shepherd evaluator against the worktree's own recorded
scenarios, **not** against the issue's incident list. A reader who holds the
issue body can rebuild the corpus from it; what this run establishes is the
*method* (rules unchanged, mechanical recount, every miss listed).

**The measurement itself is not circular in the way it looks.** The corpus is
prose (the fixture text); the rules under test are the shepherd's clauses; the
reference verdicts are the shepherd's own expected decisions. What the
self-test measures is whether a **prose reading** of a failure report
reproduces the decision the shepherd's **structured** rule engine makes — the
fidelity question #141's Part A asks.

---

## 2. The evaluator under test (rules applied unchanged)

The rules are the clauses of `prompts/pr-shepherd.md`, applied unchanged:

* **Step 2a (flaky/infra)** — "If the failure looks flaky/infra (runner died,
  network timeout, rate limit): rerun once via `gh run rerun --failed`."
  → verdict `rerun`.
* **Step 2b (worker branch, mechanical)** — a real test/lint failure on a
  `worker/*` branch whose fix is clearly mechanical → verdict `fix_attempt`.
* **Step 2c (non-worker branch or judgment)** — "If the failure is on a
  non-worker branch or needs judgment: emit `{{operator.flag_label}}`, do not
  attempt a fix." → verdict `flag`.
* **Merge policy, UNSTABLE clause** — "`mergeStateStatus` CLEAN is preferred,
  but UNSTABLE is acceptable when the only failures are **pre-existing
  develop-wide issues already tracked in follow-up issues**" → verdict `merge`
  when every failing check is pre-existing *and* tracked; otherwise `flag`.

The deterministic decision function is the worktree's own shipped rule engine,
`bin/prompt_check.py:decide()`, used **unchanged** — imported, not copied, not
edited. It is gated on the prompt clauses: if a clause is missing from
`prompts/pr-shepherd.md` the engine returns `hold_unstated` and names the
clause instead of guessing. On the live prompt every clause is present, so no
fixture can be answered by a missing rule (no fixture came back
`hold_unstated`).

Because that engine consumes a *structured* PR state, the corpus is prose and
a deterministic **reader** (lexical extraction of the state from the fixture
text) sits in front of it. The reader is the only new code in this run; the
rules it feeds are unchanged. Its full source is in Appendix B, and it was
kept out of the diff (it lived as a scratch file and was deleted; the
Appendix-B source is the reproducible copy).

---

## 3. The corpus

`docs/spikes/evaluator-self-tests-parta-fixtures.json` — one object per
fixture with `id`, `class`, `source_incident`, `fixture_text`,
`known_correct_verdict`. **n = 39** (bar: ≥ 24 — met, no shortfall to
report).

| class | n | what it is |
|---|---|---|
| `seed` | 14 | the incident as its source records it |
| `paraphrase` | 14 | a mechanical rewording of its seed (synonym / clause reordering only; no fact changed) |
| `negation` | 5 | a mechanical polarity flip of its seed (one clause negated; the verdict flips with it) |
| `clean-must-pass` | 6 | a fixture carrying an explicit clean-base comparison and the verdict that comparison implies |

Reference verdicts in the corpus: `flag` 19, `merge` 11, `rerun` 6,
`fix_attempt` 3.

---

## 4. Counts (recomputed mechanically from `runs.json`)

`runs.json` covers **39/39** fixtures, ids identical to the fixture file
(verified by the recount in Appendix A, command 6).

**Family A — "must not merge"** (known-correct verdict ∈ {flag, rerun,
fix_attempt}): the shepherd must block, rerun or fix — a merge would be the
dangerous wrong answer.
**Family B — "must merge"** (known-correct verdict = `merge`): the only
failing check is a pre-existing, tracked develop-wide failure (the UNSTABLE
exemption) — blocking here is a false alarm.

| metric | definition | n | hits | rate |
|---|---|---|---|---|
| **catch rate** | family-A fixtures the evaluator classified *exactly* as the known-correct verdict | **28** | 27 | **96.4%** |
| **false-alarm rate** | family-B fixtures the evaluator did **not** answer `merge` | **11** | 2 | **18.2%** |
| unsafe-merge rate (subset of catch) | family-A fixtures the evaluator answered `merge` | 28 | 0 | **0.0%** |
| overall exact agreement | all fixtures classified exactly as the known-correct verdict | 39 | 36 | **92.3%** |

Exact agreement by class and by reference verdict:

| class | n | exact | | known verdict | n | exact |
|---|---|---|---|---|---|---|
| seed | 14 | 13 (92.9%) | | `flag` | 19 | 19 (100%) |
| paraphrase | 14 | 12 (85.7%) | | `merge` | 11 | 9 (81.8%) |
| negation | 5 | 5 (100%) | | `rerun` | 6 | 5 (83.3%) |
| clean-must-pass | 6 | 6 (100%) | | `fix_attempt` | 3 | 3 (100%) |

Evaluator verdict distribution: `flag` 21, `merge` 9, `rerun` 5,
`fix_attempt` 4.

Note on denominators: **n = 11** for the false-alarm rate is small — the two
misses come from a single root cause (§5), so the 18.2% is one defect counted
twice, not two independent defects. Read it as "the false-alarm path has one
known defect class", not as a stable rate.

---

## 5. Misses (every miss, individually)

Three misses, listed with fixture id, known-correct verdict and what the
evaluator answered.

| # | fixture id | class | known-correct verdict | evaluator verdict | why it missed |
|---|---|---|---|---|---|
| 1 | `pa-s14-unstable-repowide-ruff-drift` | seed | `merge` | `flag` | The fixture names the pre-existing failure as "the repo-wide ruff drift **that predates this branch**". The reader's pre-existing vocabulary recognises `pre-existing` / `pre existing` but not `predates`, so the failure scope falls through to `pr_specific`; the UNSTABLE exemption then does not apply and the rule engine flags. The **rule** was applied correctly to the extracted state — the **reader** extracted the wrong state. |
| 2 | `pa-p14-unstable-repowide-ruff-drift-paraphrase` | paraphrase | `merge` | `flag` | Same root cause as miss 1, carried into the paraphrase ("predates this branch"). |
| 3 | `pa-p01-flaky-runner-died-paraphrase` | paraphrase | `rerun` | `fix_attempt` | The paraphrase reads "is **not** a real defect **but** flaky/infra". The reader's flaky-negation guard sees `not … flaky` within its 40-character window and suppresses the flaky/infra reading; the failure is then read as a real test failure on a `worker/*` branch → `fix_attempt` instead of `rerun`. The guard cannot distinguish "not X **but** Y" from "not X". |

Consequences of the misses, stated explicitly:

* **No unsafe merge.** Zero family-A fixtures were answered `merge`
  (0/28). Every miss is either the conservative direction (blocking a merge
  the policy allows — misses 1 and 2) or a wrong-but-safe triage action
  (fixing instead of rerunning — miss 3).
* Both false alarms share one root cause (misses 1 and 2), so the corpus
  exposes **one** defect class in the reading layer, not two.
* All three misses are **reader-side** (lexical) errors. In every fixture the
  shipped rule engine `bin/prompt_check.py:decide()` decided the extracted
  state exactly as `prompts/pr-shepherd.md` requires. This run therefore
  bounds **reading** fidelity, not **rule** fidelity — see Finding F4.

---

## 6. Per-part verdict — **Part A only**

**Part A: PASS** — against the bar quoted below.

> **The bar** (quoted from this run's brief, which renders the issue's Part A
> bar; the issue body itself is unavailable in this worktree, so this is the
> only quotable form of it): *"the three allowed files exist; fixtures >=24 for
> this evaluator class (if the named incidents + mechanical paraphrase/negation
> variants cannot reach 24, report the shortfall as a finding with the actual
> n, do not pad with unrelated synthetic cases); runs.json covers every
> fixture; the report contains (1) a counts table with catch rate, false-alarm
> rate, and n, (2) every miss listed with fixture id and known-correct
> verdict, (3) a per-part verdict line for Part A only: PASS / VALIDATED / FAIL
> with the issue's Part A bar quoted."*

Bar-by-bar:

| bar item | met? | evidence |
|---|---|---|
| the three allowed files exist | yes | §Appendix A command 7 (`git status --porcelain`) lists exactly the three new files and no modified file |
| fixtures ≥ 24 for this evaluator class | yes | n = 39 (§3); no padding with unrelated synthetic cases |
| runs.json covers every fixture | yes | 39/39, ids identical to the fixture file (Appendix A command 6) |
| counts table with catch rate, false-alarm rate and n | yes | §4 (catch 96.4% n=28; false alarm 18.2% n=11) |
| every miss listed with fixture id and known-correct verdict | yes | §5 (3 misses, each with id, class, known-correct verdict, evaluator verdict, cause) |
| a per-part verdict line for Part A only | yes | this line; Parts B and C were not started |

**Qualification on that PASS.** The bar could only be applied in the form the
brief renders it, because the issue body is not in this worktree (§1). Two
consequences a reader with the issue body must weigh:

1. If the issue's Part A bar additionally sets a **numeric** threshold on
   catch / false-alarm rate, that threshold is unquotable here; the numbers
   for that adjudication are §4 (catch 96.4% on n=28, false alarm 18.2% on
   n=11) and the misses are §5.
2. The corpus incidents are the worktree's own recorded shepherd scenarios,
   not the issue's incident list (§1). A corpus rebuilt from the issue body
   may produce different numbers — the method, not these numbers, is what
   this run establishes.

---

## 7. Relation to #94 — seam note

The issue's own "Relation to #94" section is unavailable in this worktree
(§1), so this note is derived from the worktree's own references to #94
(`bin/verify_stamp.py`: *"Verification stamp at supervisor collection (issue
#95, from the #94 verification spike, Part A — Phase 0 PROCEED 2026-11-17)"*)
and from the class under test.

| | #94 owns | this run (#141 Part A) covers |
|---|---|---|
| object | the **verification stamp** — a collection-time record of what a worker run actually verified (`verify_class` E2E/SUITE/TARGETED/NONE, `clean_base_verified` yes/no/not_claimed, `clean_base_detected` transcript evidence) in a companion append-only ledger | the **shepherd merge/classification evaluator** — the decisions the shepherd's own clauses produce about a failing check |
| decision | **records** whether a clean-base comparison actually ran behind a pre-existing-failure claim; decides nothing about merge eligibility; observe-only, ships disabled behind a config gate | **decides**: pre-existing-and-tracked (exempt → merge) vs new (flag); flaky/infra (rerun) vs real-on-worker (fix attempt) vs non-worker / needs-judgment (flag) |
| direction | evidence producer | fidelity test of the consumer |
| overlap | the `clean_base_verified` vocabulary and the clean-base protocol (`docs/OPERATIONS.md`) are reused here as the `clean-must-pass` fixture vocabulary — that is the shared seam, and it is a *vocabulary* seam, not a schema or code seam | nothing in #141 Part A changes the stamp, its schema, the collector, or the config gate; no file outside the three allowed paths was touched |

In one line: **#94 owns proving that a clean-base comparison happened (and
recording it); #141 Part A owns proving that the shepherd classifies
pre-existing-vs-new failures and clean-base claims correctly when it reads
them.** #141 does not re-implement #94's stamp, and #94 does not measure the
classifier.

---

## 8. Findings and limitations

* **F1 — corpus provenance (the headline limitation).** The issue body of
  #141 is not in this worktree and cannot be fetched (no network; TLS failure
  quoted in Appendix A). The corpus is therefore built from the worktree's
  own shepherd clauses and the shipped shepherd spec fixture. This is a
  provenance deviation, reported rather than hidden; it is the reason the
  Part A PASS in §6 is qualified.
* **F2 — no primary verdict records.** There are no primary verdict records
  inside this worktree for the issue's incidents, and none were fetched. The
  reference verdicts used here come from the prompt clauses and the shipped
  spec fixture's `expected_decision` fields.
* **F3 — the false-alarm denominator is small.** n = 11, and both false alarms
  are the same root cause. The rate is a defect count, not a stable estimate;
  a corpus rebuilt from the issue body should re-measure it.
* **F4 — all three misses are reading-layer misses, not rule misses.** The
  shipped rule engine decided every extracted state exactly as the prompt
  requires. The reader is the component under measurement, and it is *not* a
  shipped product: the real shepherd consumes structured `gh` output, not
  prose. So this measurement bounds the fidelity of a prose→state reader, and
  it does **not** establish that the shepherd's own merge decisions are
  correct in production. A follow-up that wants that must feed the corpus
  through the shepherd's real inputs (`gh pr checks` / status rollup), not
  through prose.
* **F5 — fixture count.** 39 fixtures against a ≥24 bar; no shortfall, and no
  unrelated synthetic padding was used.
* **F6 — verification.** This is a docs-only run: no test suite was executed
  and none was needed (no code changed). The verification commands are the
  two `python3 -m json.tool` validations, the mechanical recount of §4, and
  `git status --porcelain` / `git diff --name-only` showing that no
  pre-existing file was modified (Appendix A). No pre-existing failure was
  encountered, so the stale-test protocol was not invoked.

---

## Appendix A — commands run, with output

Base: `556ef87` (`git log --oneline -1`).

**1 — the issue body is not reachable (provenance evidence)**

```
$ gh issue view 141 --repo anchapin/plan-coder
Post "https://api.github.com/graphql": tls: first record does not look like a TLS handshake
```

**2 — JSON validation of the corpus**

```
$ python3 -m json.tool docs/spikes/evaluator-self-tests-parta-fixtures.json > /dev/null && echo "fixtures json.tool OK"
fixtures json.tool OK
$ python3 -m json.tool docs/spikes/evaluator-self-tests-parta-runs.json > /dev/null && echo "runs json.tool OK"
runs json.tool OK
```

**3 — the self-test run** (the runner is the scratch script whose source is
in Appendix B; it lived at `docs/spikes/parta-eval-scratch.py`, was deleted
after the run, and is not part of the diff)

```
$ python3 docs/spikes/parta-eval-scratch.py
pa-s01-flaky-runner-died                       expected=rerun        got=rerun        OK
pa-s02-flaky-network-timeout                   expected=rerun        got=rerun        OK
pa-s03-flaky-rate-limit                        expected=rerun        got=rerun        OK
pa-s04-nonworker-branch-lint                   expected=flag         got=flag         OK
pa-s05-nonworker-branch-test                   expected=flag         got=flag         OK
pa-s06-needs-judgment-scope                    expected=flag         got=flag         OK
pa-s07-needs-judgment-cause                    expected=flag         got=flag         OK
pa-s08-unstable-preexisting-tracked            expected=merge        got=merge        OK
pa-s09-unstable-new-failure                    expected=flag         got=flag         OK
pa-s10-unstable-preexisting-untracked          expected=flag         got=flag         OK
pa-s11-cleanbase-reproduces                    expected=merge        got=merge        OK
pa-s12-cleanbase-clean                         expected=flag         got=flag         OK
pa-s13-unstable-develop-type-error             expected=merge        got=merge        OK
pa-s14-unstable-repowide-ruff-drift            expected=merge        got=flag         MISS
pa-p01-flaky-runner-died-paraphrase            expected=rerun        got=fix_attempt  MISS
pa-p02-flaky-network-timeout-paraphrase        expected=rerun        got=rerun        OK
pa-p03-flaky-rate-limit-paraphrase             expected=rerun        got=rerun        OK
pa-p04-nonworker-branch-lint-paraphrase        expected=flag         got=flag         OK
pa-p05-nonworker-branch-test-paraphrase        expected=flag         got=flag         OK
pa-p06-needs-judgment-scope-paraphrase         expected=flag         got=flag         OK
pa-p07-needs-judgment-cause-paraphrase         expected=flag         got=flag         OK
pa-p08-unstable-preexisting-tracked-paraphrase expected=merge        got=merge        OK
pa-p09-unstable-new-failure-paraphrase         expected=flag         got=flag         OK
pa-p10-unstable-preexisting-untracked-paraphrase expected=flag        got=flag         OK
pa-p11-cleanbase-reproduces-paraphrase         expected=merge        got=merge        OK
pa-p12-cleanbase-clean-paraphrase              expected=flag         got=flag         OK
pa-p13-unstable-develop-type-error-paraphrase  expected=merge        got=merge        OK
pa-p14-unstable-repowide-ruff-drift-paraphrase expected=merge        got=flag         MISS
pa-n01-not-flaky-real-failure                  expected=fix_attempt  got=fix_attempt  OK
pa-n02-not-preexisting-new-failure             expected=flag         got=flag         OK
pa-n03-cleanbase-does-not-reproduce            expected=flag         got=flag         OK
pa-n04-cleanbase-also-fails                    expected=merge        got=merge        OK
pa-n05-not-rate-limit-real-lint                expected=fix_attempt  got=fix_attempt  OK
pa-c01-clean-base-passes-failure-is-new        expected=flag         got=flag         OK
pa-c02-clean-base-fails-failure-is-preexisting expected=merge        got=merge        OK
pa-c03-clean-base-passes-mechanical-on-worker  expected=fix_attempt  got=fix_attempt  OK
pa-c04-clean-base-passes-nonworker-branch      expected=flag         got=flag         OK
pa-c05-clean-base-fails-only-known-failure     expected=merge        got=merge        OK
pa-c06-clean-base-passes-needs-judgment        expected=flag         got=flag         OK

fixtures=39 misses=3
misses: pa-s14-unstable-repowide-ruff-drift, pa-p01-flaky-runner-died-paraphrase, pa-p14-unstable-repowide-ruff-drift-paraphrase
```

**4 — the mechanical recount** (the numbers in §4 come from this, not from
hand tallying)

```
$ python3 - <<'EOF'
import json
from collections import Counter
fx=json.load(open("docs/spikes/evaluator-self-tests-parta-fixtures.json"))["fixtures"]
rn=json.load(open("docs/spikes/evaluator-self-tests-parta-runs.json"))["runs"]
ids_f=[f["id"] for f in fx]; ids_r=[r["id"] for r in rn]
print("fixtures:",len(fx),"runs:",len(rn),"coverage_ok:",ids_f==ids_r)
from collections import Counter
print("classes:",Counter(f["class"] for f in fx))
print("known verdicts:",Counter(f["known_correct_verdict"] for f in fx))
famA=[f for f in fx if f["known_correct_verdict"]!="merge"]
famB=[f for f in fx if f["known_correct_verdict"]=="merge"]
res={r["id"]:r for r in rn}
catchA=[f for f in famA if res[f["id"]]["evaluator_verdict"]==f["known_correct_verdict"]]
faB=[f for f in famB if res[f["id"]]["evaluator_verdict"]!="merge"]
print("nA:",len(famA),"catch:",len(catchA),"rate: %.1f%%"%(100*len(catchA)/len(famA)))
print("nB:",len(famB),"false alarms:",len(faB),"rate: %.1f%%"%(100*len(faB)/len(famB)))
print("unsafe merges in A:",[f["id"] for f in famA if res[f["id"]]["evaluator_verdict"]=="merge"])
misses=[(f["id"],f["known_correct_verdict"],res[f["id"]]["evaluator_verdict"]) for f in fx if not res[f["id"]]["correct"]]
print("misses:",len(misses))
for m in misses: print("  ",m)
print("overall exact:",sum(1 for f in fx if res[f["id"]]["correct"]),"/",len(fx))
EOF
fixtures: 39 runs: 39 coverage_ok: True
classes: Counter({'seed': 14, 'paraphrase': 14, 'clean-must-pass': 6, 'negation': 5})
known verdicts: Counter({'flag': 19, 'merge': 11, 'rerun': 6, 'fix_attempt': 3})
nA: 28 catch: 27 rate: 96.4%
nB: 11 false alarms: 2 rate: 18.2%
unsafe merges in A: []
misses: 3
   ('pa-s14-unstable-repowide-ruff-drift', 'merge', 'flag')
   ('pa-p01-flaky-runner-died-paraphrase', 'rerun', 'fix_attempt')
   ('pa-p14-unstable-repowide-ruff-drift-paraphrase', 'merge', 'flag')
overall exact: 36 / 39
```

**5 — break-down used in §4**

```
evaluator verdicts: Counter({'flag': 21, 'merge': 9, 'rerun': 5, 'fix_attempt': 4})
by class:
  seed             n=14 exact=13 (92.9%)
  paraphrase       n=14 exact=12 (85.7%)
  negation         n= 5 exact= 5 (100.0%)
  clean-must-pass  n= 6 exact= 6 (100.0%)
by known verdict:
  flag         n=19 exact=19 (100.0%)
  merge        n=11 exact= 9 (81.8%)
  rerun        n= 6 exact= 5 (83.3%)
  fix_attempt  n= 3 exact= 3 (100.0%)
```

**6 — no pre-existing file was touched**

```
$ git status --porcelain
?? docs/spikes/evaluator-self-tests-parta-fixtures.json
?? docs/spikes/evaluator-self-tests-parta-runs.json
?? docs/spikes/evaluator-self-tests-phase0.md
$ git diff --name-only
$ git diff --cached --name-only
```

(both `git diff` outputs are empty: the run added three new files and modified
nothing; the scratch runner was deleted before the run finished).

---

## Appendix B — the reader (the only new code in this run)

Not part of the diff: this is the source of the scratch runner invoked in
Appendix A command 3. Save it outside the repo (e.g. `/tmp/parta-eval.py`),
point `REPO` at the worktree, and run it with `python3` to reproduce
`docs/spikes/evaluator-self-tests-parta-runs.json`.

```python
#!/usr/bin/env python3
"""Part A self-test runner for the shepherd merge/classification evaluator.

Reads the fixture corpus (prose), reads the shepherd's own rules
(prompts/pr-shepherd.md via the shipped rule engine bin/prompt_check.py,
UNCHANGED), decides each fixture, and writes the raw run results.

The only new logic here is the READER: a deterministic lexical extraction of
the PR state from the fixture prose. The rules are the prompt's clauses, and
the decision function is the shipped bin/prompt_check.py:decide().
"""
from __future__ import annotations

import json
import os
import re
import sys

REPO = os.environ.get("PART_A_REPO", "<worktree path>")
sys.path.insert(0, os.path.join(REPO, "bin"))
import prompt_check  # noqa: E402

FIXTURES = os.path.join(REPO, "docs/spikes/evaluator-self-tests-parta-fixtures.json")
RUNS = os.path.join(REPO, "docs/spikes/evaluator-self-tests-parta-runs.json")
PROMPT = os.path.join(REPO, "prompts/pr-shepherd.md")

# --- lexical tables (transcribed from the prompt clauses) --------------------

FLAKY = ["runner died", "runner was lost", "runner lost", "network timeout",
         "timed out", "rate limit", "rate-limit", "flaky", "infra/",
         "infrastructure failure", "network error", "connection reset"]
FLAKY_NEG = re.compile(
    r"\b(not|never|isn't|is not|no longer)\b[^.;]{0,40}"
    r"\b(flaky|infra|infrastructure|rate limit|rate-limit|network timeout|"
    r"timeout|runner died|runner lost)\b", re.I)
JUDGMENT = ["needs judgment", "needs judgement", "requires judgment",
            "requires judgement", "cannot be decided mechanically",
            "needs the operator's judgment", "cause is unclear",
            "no mechanical repair", "no mechanical fix"]
GREEN = ["every other check is green", "all other checks are green",
         "checks are green", "ci is green"]
MECHANICAL = ["failing test", "test failure", "lint failure", "lint error",
              "ruff", "real failure", "assertion", "f401", "failing check"]

WORKFLOWS = [".github/workflows"]
JITTEST = ["jittest", "ji ttest"]
TAMPER = ["test-tamper", "test tamper"]
STALE_SHA = ["head moved since", "sha has moved", "head sha moved",
             "stale check"]
UNVERIFIABLE = ["cannot verify the implementation", "cannot be verified"]
DOMAIN = ["domain judgment is needed"]

NOT_TRACKED = re.compile(r"not\s+(yet\s+)?tracked|untracked|no follow-up issue", re.I)
NOT_PREEXISTING = re.compile(r"not\s+(a\s+)?pre-existing|no pre-existing", re.I)
PREEXISTING = re.compile(r"pre-existing|pre existing", re.I)
NEW = re.compile(r"\bnew\b", re.I)
TRACKED = re.compile(r"tracked|develop-wide|repo-wide|follow-up issue", re.I)


def has(text: str, phrases: list[str]) -> bool:
    low = text.lower()
    return any(p in low for p in phrases)


def read_state(text: str) -> dict:
    """Extract the shepherd's PR state from a prose failure report."""
    low = text.lower()

    # mergeability (step 1)
    if "conflicting" in low or "merge conflicts" in low:
        mergeable = "CONFLICTING"
    else:
        mergeable = "MERGEABLE"

    # CI class (step 2a / 2b / 2c)
    flaky = has(text, FLAKY) and not FLAKY_NEG.search(text)
    if flaky:
        ci = "flaky"
    elif has(text, JUDGMENT):
        ci = "judgment"
    elif has(text, GREEN):
        ci = "green"
    elif has(text, MECHANICAL):
        ci = "mechanical"
    else:
        ci = "green"

    # merge state status (merge policy)
    status = "UNSTABLE" if "unstable" in low else "CLEAN"

    # head branch
    m = re.search(r"head branch\s*(?:is\s*)?`?([A-Za-z0-9_./-]+)`?", text, re.I)
    if m:
        head_branch = m.group(1)
    else:
        m2 = re.search(r"(worker/[A-Za-z0-9_./-]+)", text)
        head_branch = m2.group(1) if m2 else "unknown"

    # failure scope: pre-existing-and-tracked vs new vs untracked vs infra
    if NOT_TRACKED.search(text):
        scope = "untracked"
    elif NOT_PREEXISTING.search(text):
        scope = "pr_specific"
    elif PREEXISTING.search(text) and TRACKED.search(text):
        scope = "pre_existing_tracked"
    elif NEW.search(text):
        scope = "pr_specific"
    elif ci == "flaky":
        scope = "infra"
    else:
        scope = "pr_specific"

    failures = []
    if status == "UNSTABLE" or ci in ("flaky", "mechanical", "judgment"):
        failures = [{"scope": scope, "name": "failing check named in the report"}]

    return {
        "state": "open",
        "mergeable": mergeable,
        "merge_state_status": status,
        "head_branch": head_branch,
        "head_sha_stale": has(text, STALE_SHA),
        "diff_reviewed": True,
        "flag_raised": False,
        "jittest_catch": has(text, JITTEST),
        "test_tamper_flag": has(text, TAMPER),
        "touches_workflows": has(text, WORKFLOWS),
        "domain_judgment": has(text, DOMAIN),
        "unverifiable": has(text, UNVERIFIABLE),
        "ci": ci,
        "ci_failures": failures,
    }


def main() -> int:
    with open(PROMPT, encoding="utf-8") as fh:
        prompt_text = fh.read()
    clauses = prompt_check.present_clauses(prompt_text)
    with open(FIXTURES, encoding="utf-8") as fh:
        doc = json.load(fh)
    runs = []
    for fx in doc["fixtures"]:
        state = read_state(fx["fixture_text"])
        decision, clause, _consulted = prompt_check.decide(state, clauses)
        runs.append({
            "id": fx["id"],
            "evaluator_verdict": decision,
            "correct": decision == fx["known_correct_verdict"],
        })
        print("%-46s expected=%-12s got=%-12s %s" % (
            fx["id"], fx["known_correct_verdict"], decision,
            "OK" if decision == fx["known_correct_verdict"] else "MISS"))
    with open(RUNS, "w", encoding="utf-8") as fh:
        json.dump({"evaluator_class": "shepherd-merge-classification",
                   "rules": "prompts/pr-shepherd.md clauses; decision function "
                            "bin/prompt_check.py:decide() (unchanged)",
                   "runs": runs}, fh, indent=2)
        fh.write("\n")
    misses = [r["id"] for r in runs if not r["correct"]]
    print("\nfixtures=%d misses=%d" % (len(runs), len(misses)))
    print("misses: %s" % (", ".join(misses) if misses else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The reader was **not** tuned after the run: the three misses of §5 are
reported as measured, and the lexical gaps that caused them are named there
rather than patched.
