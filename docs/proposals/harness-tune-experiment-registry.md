# Proposal — harness-tune opens and closes experiments (METRICS.md §6)

**Status: PROPOSAL — NOT APPLIED.** This document is a proposal for
Alex's review. `prompts/harness-tune.md` is **unchanged** in this
branch: the diff below is delivered here and stops here. The prompt
change is a live-prompt change, and live prompt changes are the
human's tap (the same rule the harness-tune pass itself follows for
`agent.py` and the cron templates).

Issue: #132 (docs/METRICS.md §8, Phase 2)
Date: 2026-11-14
Tooling this depends on: `bin/experiments.py` (added in this branch,
see `docs/EXPERIMENTS.md`).

---

## 1. What is being proposed

The weekly harness-tune pass already applies harness edits under an
evidence gate (≥2 distinct runs behind any prompt edit). What it does
not have is a *record* of what it tried and what came of it, so the
edits are anecdotal: the next pass cannot tell "we already tried this"
from "we never tried this", and nobody can say whether a change helped.

docs/METRICS.md §6 defines the registry (`experiments.jsonl`, one row
per change) and the rules the pass must follow when it opens and closes
an experiment:

- one primary metric per experiment, guardrails from the §4 table;
- a verdict compares the window against the immediately preceding
  equal-length window, **stratified** — an aggregate win with a
  per-repo loss is `inconclusive` until it is explained;
- the ≥2-run evidence bar stays; the registry adds the *outcome* bar on
  top;
- structural changes (new gates, slot counts, merge policy) stay the
  human's decision — the loop proposes, never applies;
- the registry is rendered on the dashboard (Panel 5), so the human
  sees what was tried, not just what the numbers did.

The proposal is three insertions into `prompts/harness-tune.md`:

1. **the corpus** gains one bullet naming the registry file, its row
   shape, and the rule that a missing registry is not an error;
2. **a new step 4b** — open or close an experiment — that tells the
   pass to open a row *before* it applies an edit, to close it when the
   window has closed and the stratified numbers are in, and to keep the
   section 6 rules (one primary metric, stratified verdicts, structural
   changes stay the human's);
3. **the final message** gains the monthly one-page metrics review rule
   (D6).

The step is numbered "4b" on purpose: it sits between step 4
(propose edits) and step 5 (apply edits) — an experiment must be open
before the edit is applied — and inserting it as "4b" keeps steps 5–9
(and every cross-reference to them) unchanged.

## 2. The diff (ready to apply)

Apply from the repository root with `git apply -p1` (or `patch -p1`):

```diff
--- a/prompts/harness-tune.md
+++ b/prompts/harness-tune.md
@@ -13,6 +13,7 @@
 - JiTTest ledger: {{state.dir}}/jittest_ledger.jsonl (one JSON object per completed catching check: ts, repo, issue, pr, trigger, hypotheses {produced, kept, discarded_no_evidence, discarded_unverifiable, coverage_complete}, tests_generated, three_state_passed, three_state_class, killed_stage_a {rule: count}, judge {catch, intended, unclear, discarded_no_evidence}, survived, tokens_out, operator_disposition, label_adjudication {ground_truth, adjudicated_by, note}, outcome) — the catching-test spike's loss signal, aggregated in step 1 alongside the worker ledger. The ledger is append-only and a disposition arrives as a later row for the same PR, so step 1 collapses it to the latest row per repo + PR before computing rates. A missing JiTTest ledger means the spike has not logged a check yet; note it and move on, do not treat it as an error.
 - Harness-tune pass ledger: {{state.dir}}/harness_tune_passes.jsonl (one JSON object per completed tune pass: ts, tokens_in, tokens_out, wall_s, source) — the cost of the tune pass ITSELF, which no other ledger records. It is kept apart from the worker and spike ledgers on purpose: those feed the overhead denominators and the loss-signal aggregation, so a tune row in them would perturb the measurement this pass makes. The `source` field names where the numbers came from (the scheduler's cron run record, the pass's own transcript, or "not_available" when neither was reachable) — quote the source whenever you quote the numbers.
 - Transcripts: {{state.dir}}/transcripts/*.jsonl (one JSON line per worker step; fields truncated).
+- Experiment registry: {{state.dir}}/experiments.jsonl (one JSON object per experiment: id, date, hypothesis, change, window {start, min_sample, min_weeks}, primary_metric, guardrails[], verdict, verdict_evidence) — the optimization loop's record of what was tried and what came of it (docs/METRICS.md section 6). A missing registry means no experiment has been opened yet: note it and move on, do not treat it as an error. The registry is written with `python3 {{skill.dir}}/bin/experiments.py` — never hand-edit the file.
 - The weights (what you may edit): {{skill.dir}}/SKILL.md, {{skill.dir}}/bin/agent.py (SYSTEM_PROMPT, tooling, logging), and the cron prompt templates in {{public_repo.local_clone}}/prompts/ (issue selection, tier routing, brief guidance live in the supervisor template). Since the 2026-10-24 unification, bin/agent.py and bin/quick_check.py are symlinks into the public repo clone ({{public_repo.local_clone}}/bin/) — the live harness runs the repo's code, so edits there are public-repo changes, published in step 7. The cron bodies are GENERATED from the repo templates plus config/deployment.local.json: never edit a cron body directly; edit the template (or the config) and reinstall in step 7. Because workers execute this exact code, any edit to agent.py must be compile-clean and behavior-preserving unless the evidence demands otherwise: a broken agent.py published on Sunday breaks every worker launch after it.
 - External ideas brief: {{state.dir}}/{{tune.ideas_brief_file}} (external-systems extraction, 2026-10-24). Candidate harness ideas with our current baseline, known gaps, and a per-item class; read it during step 4.
 
@@ -21,6 +22,7 @@
 2. For the top 2-3 failure classes, pull the relevant transcript excerpts: grep the transcript files for abort/error lines and read the surrounding steps. Read excerpts, never whole files. Skim 1-2 successful runs for contrast.
 3. Refresh merged status: for ledger rows with pr_url from the last 14 days, run `gh pr view <url> --json state,mergedAt` and include merge rate in your report (don't rewrite the ledger).
 4. Propose concrete edits, each backed by evidence from at least 2 distinct worker runs (the backpass evidence gate — cite the run_ids). Candidate surfaces: brief-writing guidance (what did the briefs behind failures have in common?), SYSTEM_PROMPT rules, tier-routing defaults, issue-selection heuristics, exec timeouts, per-repo verification commands. Also evaluate the ideas brief's candidates marked [small-edit candidate] the same way — they need the same 2+ run evidence; candidates marked {{tune.flag_label}} are structural: include them in your report for his decision, never apply them. When a brief item is applied, rejected on evidence, or flagged, annotate the brief file with the date and outcome so it is not re-proposed.
+4b. Open or close an experiment (docs/METRICS.md section 6): every harness edit you apply in step 5 is an experiment, and the registry is the record of it. Run `python3 {{skill.dir}}/bin/experiments.py list --state-dir {{state.dir}}` to see the open experiments. If the edit you are about to apply is not covered by an open experiment, open one BEFORE you apply it: `python3 {{skill.dir}}/bin/experiments.py add --state-dir {{state.dir}} --id <next id> --hypothesis "<the primary metric, the expected direction and size>" --change "<what you edited, with the commit/PR link>" --window-start <YYYY-MM-DD> --primary-metric "<exactly one metric>" --guardrail "<a metric that must not regress>" --guardrail "<another guardrail>" --verdict in-progress`. One primary metric per experiment; the guardrails come from the section 4 table. When an experiment's window has closed (at least its min_sample runs AND at least its min_weeks, whichever is later) and the stratified numbers are in hand, close it: `python3 {{skill.dir}}/bin/experiments.py close --state-dir {{state.dir}} --id <id> --verdict keep|revert|inconclusive --verdict-evidence "<the stratified numbers: overall and per repo/tier/provider>"`. A verdict compares the window against the immediately preceding equal-length window, stratified — an aggregate win with a per-repo loss is inconclusive until you can explain it. The registry is the tool's file: never hand-edit it. Structural changes (new gates, slot counts, merge policy) stay {{operator.name}}'s decision — propose them with evidence, never apply them, and do not open an experiment row for a change you did not apply.
 5. Apply small, safe edits directly: wording clarifications, checklist items, tier-routing tweaks, brief template additions. Cron-prompt edits go in the repo templates under {{public_repo.local_clone}}/prompts/ (tokenized — deployment specifics belong in config/deployment.local.json, never hard-coded in a template), and take effect when step 7 reinstalls the cron bodies. After editing agent.py, run `python3 -m py_compile` on it and the repo test suite (`cd {{public_repo.local_clone}} && python3 -m unittest discover -s tests`) — a red suite means the edit does not ship: fix it, or revert the edit and flag it. The publish gate in step 7 runs the suite again and aborts on failure. Do NOT restructure the harness, change provider wiring or auth, or alter the supervisor's parallelism and scheduling — flag those for {{operator.name}} instead.
    Log every edit bundle as an EDD experiment, ONE INVOCATION PER BUNDLE (not one per pass): right after a bundle is applied, save that bundle's diff to its own file (e.g. `git -C {{public_repo.local_clone}} diff > /tmp/bundle-<n>.diff`) and run `python3 {{skill.dir}}/bin/edd.py experiment --summary "<one line: what this bundle changed>" --evidence <run_id,run_id> --attribution split --bundle-id <template-or-file>#<n> --bundle-diff /tmp/bundle-<n>.diff`. The per-bundle diff hash is what separates two bundles that touch the SAME template in one pass — without it both rows carry the same fingerprint and their effects can never be told apart. The experiment row's timestamp is the cut a later `bin/edd.py regress --cut <ts>` runs at, and the row's discriminator says which bundle that cut belongs to; `python3 {{skill.dir}}/bin/edd.py experiments --state-dir {{state.dir}}` lists every bundle with its cut and its discriminator. If `bin/edd.py` exits 2 (EDD disabled) or the append fails, note it in your report and carry on: EDD is a record, never a gate — it never changes what you merge, dispatch, or edit.
 6. Check the public repo for new issues: run `gh issue list --repo {{public_repo.slug}} --state open --limit 50 --json number,title,author,createdAt,labels` and compare against {{state.dir}}/public_repo_issues.json — an issue whose number is not in that file's `seen` list is NEW. Triage each new issue (classify: bug / feature request / docs / question / spam-or-abuse), choose a disposition, and record every new issue with its disposition in that registry file before you finish:
@@ -35,4 +37,4 @@
 8. Record this pass's own token cost before you write your final message: run `python3 {{skill.dir}}/bin/tune_pass_cost.py append --state-dir {{state.dir}} --cron-run <the scheduler's cron run record for this pass, if you have one> --transcript <this pass's own transcript, if there is one> --wall-s <seconds this pass took>`. It appends ONE row to {{state.dir}}/harness_tune_passes.jsonl — {ts, tokens_in, tokens_out, wall_s, source} — the pass-cost ledger described in the corpus above. The tokens come from the first source that exists: the scheduler's cron run record for this pass, else this pass's transcript usage summed the way bin/jittest.py sums transcript usage; when neither source is reachable the row records tokens_in/tokens_out as the string "not_available" and its source says exactly that — that is a valid record, not a failure, and you report it as such (with the wall time you do have). A failed append is noted in your report, never a reason to abandon the pass.
 9. {{tune.log_step}}
 
-Final message (this reaches {{operator.name}} — keep it short): week stats (runs, PRs opened, merged, median tokens per PR, top failure mode), the attention readout from step 1 (attention per correctly finished task, or its labelled "proxy, censored" figure, with its n, and the share of attention on tasks that did not finish correctly), the previous pass's token cost with its source (from {{state.dir}}/harness_tune_passes.jsonl), this pass's own recorded token cost and source, each harness change with the 2+ runs of evidence behind it, anything flagged for his decision, the public repo's new issues with their dispositions (incorporated, with the commit, / queued / flagged), any cron bodies reinstalled in step 7, and the public repo publish result (commit sha, "no changes", or the abort reason flagged for him). If nothing warranted a change, one line with the stats.
+Final message (this reaches {{operator.name}} — keep it short): week stats (runs, PRs opened, merged, median tokens per PR, top failure mode), the attention readout from step 1 (attention per correctly finished task, or its labelled "proxy, censored" figure, with its n, and the share of attention on tasks that did not finish correctly), the previous pass's token cost with its source (from {{state.dir}}/harness_tune_passes.jsonl), this pass's own recorded token cost and source, each harness change with the 2+ runs of evidence behind it, anything flagged for his decision, the public repo's new issues with their dispositions (incorporated, with the commit, / queued / flagged), any cron bodies reinstalled in step 7, and the public repo publish result (commit sha, "no changes", or the abort reason flagged for him). If nothing warranted a change, one line with the stats. Monthly metrics review (D6): once a month, put a one-page metrics review in chat — but ONLY when it carries a decision: an experiment verdict, a target lock or target change, or a guardrail breach. Otherwise say nothing about it: every other metric stays on the dashboard, on demand (the monitoring contract is quiet unless a decision is needed).
```

The diff is a template diff: every `{{token}}` stays a token (no
rendered cron body is quoted anywhere), and it is computed against the
current `prompts/harness-tune.md` at commit 5efd283.

## 3. The monthly one-page metrics review (D6)

§8 Phase 2 asks for a monthly one-page metrics review that "lands in
chat **only when it carries a decision** (D6, §10): an experiment
verdict, a target lock/change, or a guardrail breach. Otherwise
silence; everything else stays dashboard-on-demand (monitoring
contract: quiet unless a decision is needed)."

The third hunk above encodes exactly that, in the prompt's own words
and next to the final message the pass already writes:

- **chat, once a month**, a one-page metrics review — but only when it
  carries a decision:
  - an experiment verdict (`keep` / `revert` / `inconclusive`),
  - a target lock or a target change (the §4 targets),
  - a guardrail breach (a metric that must not regress, regressed);
- **otherwise: silence.** The review is not a periodic status report.
  Every other number stays on the dashboard, on demand. The weekly
  pass's own final message keeps its existing shape (week stats, the
  attention readout, the token costs, the changes with their evidence,
  the flags) — that message is the tuning record, not the monthly
  review.

The reason is the monitoring contract: a channel that speaks every
month with nothing to decide trains the human to skip it. A message
that only ever appears when a decision is needed is one they read.

## 4. What this proposal does NOT do

- It does not apply itself. `prompts/harness-tune.md` is unchanged in
  this branch; applying the diff is a separate, explicit act.
- It does not change any target, slot count, merge policy or
  harness behaviour. The pass still applies only small, safe edits
  under the existing evidence gate, and structural changes are still
  flagged for the human.
- It does not introduce auto-tuning. The registry records; it never
  decides. The verdict vocabulary is `keep` / `revert` /
  `inconclusive` / `in-progress`, and a `revert` is a recommendation
  that the human acts on.
- It does not touch the operator's private config
  (`config/deployment.local.json`) and does not quote a rendered cron
  body.

## 5. Open questions for Alex

1. Should the pass close an experiment's verdict itself, or propose
   the verdict and leave the row open until the human confirms? The
   proposal above lets the pass close the row (the tool's `close`
   command only records numbers it is given); the stricter reading is
   that `keep` / `revert` is the human's call and the pass may only
   record `inconclusive`.
2. Should a `revert` verdict trigger the pass to revert its own edit
   in the same pass, or should the revert be a proposal like any other
   structural change? The proposal above says neither — a revert is
   reported, not applied, unless the human asks.
3. Is "4b" the right numbering, or should the step be renumbered
   (5 → 6, …) so the pass reads sequentially?

## 6. How to review it

```bash
# apply the diff (extract the ```diff block from this file first)
git apply -p1 harness-tune-experiment-registry.diff

# check it against the current prompt without applying it
patch --dry-run -p1 < harness-tune-experiment-registry.diff

# the tooling the proposal depends on
python3 bin/experiments.py --help
python3 bin/experiments.py list --registry examples/experiments.example.jsonl
python3 bin/experiments.py validate --registry examples/experiments.example.jsonl
```
