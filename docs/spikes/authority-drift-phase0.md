# Authority-axis drift classification + coverage receipt — Phase 0 measurement (issue #24)

Phase 0 of the authority-axis spike: **measurement only**. No prompt, config, code or
test change is made by this document, and nothing here authorises enabling anything
in the live config. Phase 1 (shadow adoption of an `authority:` line in harness-tune's
drift sections + a maintained coverage file) is **not** built here — it is gated on a
Phase 0 Proceed **and** Alex's separate go, per issue #24.

Method: the sample is retrospective — historical drift/flag findings whose outcomes
are already known — classified on the pdd-verify authority axis (aligned / spec-right /
code-right / neither-right / unmapped / unverifiable). Sources are GitHub primary
records (issue bodies, closing PRs, repo trees at HEAD) read through the read-only
`gh` CLI, plus this repo's own `bin/contract_check.py` citation extractor.

Classification rule used throughout (stated so the rows can be re-derived):

| class | meaning as applied here |
|---|---|
| `aligned` | a written artifact and the code agree; no drift (the zero-drift control rows) |
| `spec-right` | a **written** artifact (contract, glossary, standing directive, acceptance criteria, decision record) is right; the code or the other doc must change |
| `code-right` | the **live/executable** side (code, CI config, canonical data file, re-measured counts) is right; the stale side must change |
| `neither-right` | no side is canonical on inspection; a human decision is required before either side moves |
| `unmapped` | no written artifact states the intended behaviour for that area — there is no authority to drift *from* |
| `unverifiable` | inspection cannot settle which side is right |

---

## 1. Raw classification table (first deliverable)

29 rows, one per historical finding (or per control/absence row). "Action actually
taken" is the observed outcome on GitHub (closing PR title + merge state, or the
recorded absence of one). "Grouping" names the single authority decision the row
belongs to (`G1`…`G17`, §1.2) and says what the class would have done to the action.

### 1.1 Rows

| # | Source (repo, record) | What the finding was | Class | Action actually taken | Grouping note — would the class have changed / shortened / grouped it? |
|---|---|---|---|---|---|
| R01 | fluxion #4754 → PR #4761 (merged 2026-11-12T03:52) | AGENTS.md L25/L27 and README L200 carry stale prose test counts (8,865/143, "4,158", "~4,400") against canonical `tests/test_inventory.json` (9,266/156, 4,825); `check_test_inventory_drift.py --no-verify` passes because it compares prose to *regenerated* numbers, never to the committed file | `code-right` | Prose counts rewritten to inventory-derived figures (PR #4761, merged) | `G1`. Class would **group** with R02 (one authority decision: "the inventory chain is canonical"); would not have shortened it — the numbers still had to be re-derived |
| R02 | fluxion #4773 → PR #4779 (merged 2026-11-12T15:25) | Committed `tests/test_inventory.json` (9,281/153) and `tests/reference_data/test_inventory_baseline.json` (9,282/155) disagree with each other *and* with AGENTS.md/README (9,290/153) — live AST truth is 4,446/9,290/153 | `code-right` | Inventory + baseline regenerated so the chain agrees (PR #4779, merged) | `G1`. Class would **group** with R01; without the class these read as two separate "number disagreement" findings |
| R03 | fluxion #4757 → PR #4769 (merged 2026-11-12T07:54; PR #4766 closed unmerged) | Seven CLI workflow stubs, two error strings and the docs-gate anchor cite issue #2947, which is CLOSED; the issue itself asks to "decide the tracking home" | `neither-right` | Tracking citations removed/re-anchored in stubs, error text and the docs gate (PR #4769, merged) | `G2`. Class would **group** with R04 (one decision: "where does the unimplemented-CLI work live") and would have flagged the row as *decision-bearing* rather than mechanical — a live/dead read of #2947 alone cannot tell whether to re-home or drop tracking |
| R04 | fluxion #4772 → PR #4778 (merged 2026-11-12T13:52) | The 7 `--bin fluxion` unit tests still assert the old `#2947` error text, so all 7 fail on develop; `#4769`'s verification ran only `--test integration-cli` | `code-right` | Assertions updated to the new wording; residue #2947 mentions swept (PR #4778, merged) | `G2`. Class would **group** with R03 — a second finding from the same authority decision, filed a day later |
| R05 | fluxion #4752 → PR #4760 (merged 2026-11-12T03:25) | `scripts/check_module_size.py` returns `None` for a limits path that no longer exists, so 3 of 11 LIMITS entries are silently skipped; the successor `src/api/security/mod.rs` is 2,320 lines vs the dead entry's 2,199 ceiling | `spec-right` | Gate changed to fail on missing paths; entries re-pointed with the documented ceiling protocol (PR #4760, merged) | `G3`. Class would **group** with R06 (the module-size gate is one artifact with two defects); it also identifies the written acceptance criteria as the authority, which the "gate is red/green" reading does not |
| R06 | fluxion #4753 → PR #4762 (merged 2026-11-12T03:52) | Three large source files (zone_balance 4,421 / pressure_air_loop 4,312 / gauge_zone_solver 4,001) are above the documented ceiling but ungated | `spec-right` | Files brought under gates per the #3574 ceiling protocol (PR #4762, merged) | `G3`. Class would **group** with R05; the protocol citation is what makes it spec-right rather than a taste call |
| R07 | fluxion #4793 → PR #4802 (merged 2026-11-13) | The `#[ignore]` census baseline predates the `tests/all_tests` consolidation, so the nightly delta signal is dead | `code-right` | Baseline regenerated (normalised) and the script refreshed (PR #4802, merged) | `G4`. Class would **group** with R10–R14 (all "ignore/quarantine bookkeeping against closed blockers"); would not have shortened the work — the baseline still had to be regenerated |
| R08 | fluxion #4792 → PR #4804 (merged 2026-11-13T20:31) | Four slow `lib_batch_oracle` population tests run in no CI lane; the QUARANTINE registry cites a nextest profile that does not exist | `neither-right` | A nightly perf lane was added and the quarantine rows migrated (PR #4804, merged) | `G5`. Class would have said "decision": the registry and the CI config disagree and neither is canonical by inspection — the shipped answer (add a lane) was one of at least two available actions |
| R09 | fluxion #4791 → PR #4797 (merged 2026-11-13T02:35) | `BatchOracle` takes the GPU surrogate path with no model loaded, so `evaluate_population(use_surrogates=true)` can hang | `unmapped` | A loaded model is now required for the GPU path (PR #4797, merged) | `G6`. No contract/glossary row states the intended surrogate-path behaviour, so there is no written authority to drift from; the class would have **grouped** it with the other no-artifact rows and flagged "no artifact exists here" |
| R10 | fluxion #4755 → PR #4771 (merged 2026-11-12T09:21) | The Case 195 high-mass test's ignore trigger ("once #3059 lands") has fired; #3059/#3064/#1465/#1462 are all closed | `code-right` | Test re-enabled (PR #4771, merged) | `G4`. Class would **group** with R07/R11–R14: one decision ("re-evaluate ignores whose blocker closed"), six rows |
| R11 | fluxion #4756 → PR #4770 (merged 2026-11-12T08:51) | An empty Case 610 placeholder stub whose blocker (#62) closed 2026-02-15 | `code-right` | Stub deleted (PR #4770, merged) | `G4`. Class would **group** with the other bookkeeping rows; would have shortened the *Alex-facing* part (one decision, six rows) though not the mechanical work |
| R12 | fluxion #4758 → PR #4764 (merged 2026-11-12T04:52) | Three blind-validation tests ignored as "Pending reference CSV" — the CSVs are committed; the recorded reason was stale | `code-right` | Re-ignored with *measured* gaps rather than un-ignored (PR #4764, merged) | `G4`. Class would **group** with R07/R10/R11/R13/R14. Note the class alone would not have produced the shipped action (re-ignore with a measured gap, not un-ignore) — the axis is about authority, not remedy |
| R13 | fluxion #4759 → PR #4768 (merged 2026-11-12T07:54) | The IDF Case 600 strict test is quarantined as ci-broken on #1577, closed 2026-07-14 — the registry's only ci-broken row | `code-right` | Test un-ignored (PR #4768, merged; its title cites #4755, one off) | `G4`. Class would **group** with R07/R10/R11/R12/R14 — the whole cluster is one authority decision, not six |
| R14 | fluxion #4777 → PR #4781 (merged 2026-11-12) | QUARANTINE rows #532/#533: #532's un-ignore criterion ('Resolved or closed') is met; #533's criterion is obsolete | `code-right` | #532 un-ignored, #533's criterion replaced (PR #4781, merged) | `G4`. Class would **group** with the cluster |
| R15 | matchline #1359 → PR #1362 (merged 2026-11-12T04:02) — **the issue's hand-found prose-drift item #1**: AGENTS.md (Verify) and docs/ci.md say "(3752 collected; expected per `.github/workflows/ci.yml`)" while `EXPECTED_TEST_COUNT` is 4114 at wiring time (4143 on develop at filing); CONTRACT.md already frames ci.yml as canonical and AGENTS.md's 3752 as the stale example | `code-right` | Both citations rewritten to the live `EXPECTED_TEST_COUNT`, plus a new drift-guard test that parses the `(NNNN collected` figures | `G7`. Class would **group** with R16/R17 (same canonical figure, three files). This is the issue's own named example: the *finding* was hand-made, and the class would have told the fixer which side wins without reading CONTRACT.md |
| R16 | matchline #1366 → PR #1370 (merged 2026-11-12T05:33) | README "~3,500", QUALITY-SCORE.md "1,744", docs/ifc_import.md "(11 tests)" for a file that collects 51 — canonical 4151; these figures match no guarded format | `code-right` | Figures corrected / removed; the guard's file list extended (PR #1370, merged) | `G7`. Class would **group** with R15/R17: one decision ("ci.yml is the canonical count"), three rows across two repos' files |
| R17 | matchline #1375 → PR #1379 (merged 2026-11-12) | `docs/ifc_import.md:219` ends "`N_CHECKS` stays 48" while the battery has 49 (`validate/__init__.py` = 45 + 4; the repo's own `docs/validation.md:161` says 49); the phrasing escapes the existing count guard | `code-right` | Doc corrected to 49 and every `N_CHECKS` mention guarded (PR #1379, merged) | `G7`. Class would **group** with R15/R16 — the *same* authority question (which count is canonical) in a different file and a different guard |
| R18 | matchline #1371 → PR #1376 (merged 2026-11-12T18:11) | Per-quantity `Unit` is ignored when it is an `IfcConversionBasedUnit` (inch/foot), so imports mis-scale; the project-level conversion unit crashes | `unmapped` | Unit-aware path implemented for project-level and per-quantity units (PR #1376, merged; related PR #1367) | `G8`. No written artifact in the repo states the expected unit-conversion semantics for imports, so there is no authority to drift from — the class says "unmapped" rather than pretending a doc disagreed |
| R19 | staging-studio #1537 → PR #1540 (merged 2026-11-13) | AGENTS.md's test census says 238 `*.test.ts` / 261 vitest files; after #1534 the real numbers are 239 / 262 (the same drift class fixed in #1512, #1521) | `code-right` | AGENTS.md census refreshed to 239 / 262 (PR #1540, merged) | `G9`. Class would **group** with R15–R17 as the same *shape* of finding (count drift), but it is a different repo's decision — so the class groups, it does not merge, unless the decision is reframed as "the CI count is canonical everywhere" |
| R20 | armored-archer #1806 → PR #1811 (merged 2026-11-12T05:34) | AGENTS.md claims 55 singletons (52 + 3 elsewhere) and misplaces two of them; CLAUDE.md says tokens live in `autoloads/design_tokens.gd` (no such file) and lists a deleted `AutoAimManager` — `project.godot` `[autoload]` has **49** entries | `code-right` | Both standing docs realigned to the live registry (PR #1811, merged) | `G10`. Class would **group** with R21/R24 — one decision ("`project.godot` is the registry of record"), three findings in two artifacts |
| R21 | armored-archer #1817 → PR #1822 (merged 2026-11-12T15:11) | `data/autoload-to-test-mapping.json` (25 entries, `last_updated: 2026-03-30`) maps three removed autoloads and mis-paths three suites | `code-right` | Mapping paths corrected (PR #1822, merged) | `G10`. Class would **group** with R20/R24 |
| R22 | armored-archer #1826 → PR #1834 (merged 2026-11-13) | The legacy runner `test/run_all_tests.gd` lists 2 missing files and 1 duplicate and does not fail loudly on unloadable entries | `code-right` | Runner file list reconciled and loader made loud (PR #1834, merged; PR #1838 still open) | `G11`. Class would **group** with R20/R21/R24 in *shape* (a checked-in registry vs the live tree) but it is a distinct authority decision (the runner's file list is not the autoload registry) |
| R23 | armored-archer #1824 → PR #1828 (**open, not merged**); issue still OPEN | Nine UI scripts fetch the nonexistent `/root/DesignTokens` — the design-token palette is silently bypassed for drifted fallback colours; CLAUDE.md is wrong too (it names `autoloads/design_tokens.gd`) | `unmapped` | A fix is proposed (PR #1828) but **not merged**; the finding is still open | `G12`. Both the code and the doc are wrong and no artifact names the right path, so there is no authority — the class says "unmapped" and routes the row to "author an artifact", which is what actually happened (a PR) but was never recorded as such |
| R24 | armored-archer #1825 → PR #1842 (merged 2026-11-13) | `/root/UIAutomation` lookups are dead since the manager was de-autoloaded (#1434) | `code-right` | Consumers routed through the static API (PR #1842, merged) | `G10`. Class would **group** with R20/R21 — the de-autoload decision (#1434) is the same authority decision as the registry drift |
| R25 | matchline `docs/roof-simplify-wiring-decision.md` vs Alex's 2026-10-25 standing directive — **the issue's hand-found prose-drift item #2**; recorded in matchline CONTRACT.md ("this supersedes the opt-in Option C in docs/roof-simplify-wiring-decision.md, which is stale on this point"), while the doc at HEAD still reads "**Verdict: Option C** — wire the roof simplifier into the production path behind an opt-in flag now" and PR #1297 (merged 2026-10-26) wired it on by default | `spec-right` | **No action taken** — no issue was ever filed for this row; the doc is still stale at HEAD (verified in this run) | `G13`. Class would have **shortened** it (one line: "the directive is right, the doc must change") and would have made it *visible*: this is the only doc-vs-directive disagreement in the sample that no finder surfaced and no filing process tracked |
| R26 | fluxion `CONTRACT.md` at HEAD — control row | 14 extracted citations, 12 checkable (2 are external: an operator standing directive and a GitHub issue); all 12 checkable targets resolve on HEAD (`SCORECARD.md`, `tests/test_inventory.json`, `scripts/check_test_inventory_drift.py`, `src/sim/assembly.rs`, `src/sim/multi_node_thermal.rs`, `.github/ci-gates.yaml`, `.github/workflows/*.yml`, `fluxion-fluid/`, `RULES.md` + its `Must-Always`/`Must-Never` sections, `AGENTS.md`) | `aligned` | None — zero-drift baseline at wiring time | `G14`. Control row: it exists to show the axis returns `aligned` where there is no drift, and to show that a *binary* live/dead read (all 12 live) already settled it |
| R27 | matchline `CONTRACT.md` at HEAD — control row | 14 extracted citations, 13 checkable (1 external); all 13 resolve on HEAD (`.github/workflows/ci.yml`, `validate/`, `*.py`, `scripts/`, `detector/`, `tests/`, `conftest.py`, `cli.py:main`, `jesse.py`, `geometry/geometry_simplify.py`, `docs/geometry_simplify.md`, `ci.yml`, `AGENTS.md`) | `aligned` | None — zero-drift baseline at wiring time | `G15`. Control row, same role as R26 |
| R28 | fluxion glossary — `UBIQUITOUS_LANGUAGE.md` exists **only in open PR #4763** (head `docs/ubiquitous-language` → base `develop`, not merged); 57 bolded core-term definitions; the file is absent from HEAD | `unmapped` | None at HEAD — the artifact has never landed; no checker has a subject | `G16`. Absence row: the issue assumed a live glossary artifact; there is none in the checked-out repo, so no glossary drift can be detected — `unmapped`, not `aligned` |
| R29 | matchline glossary — `UBIQUITOUS_LANGUAGE.md` exists **only in open PR #1369** (head `docs/ubiquitous-language` → base `develop`, not merged); 43 core-term definitions; absent from HEAD | `unmapped` | None at HEAD — the artifact has never landed | `G17`. Same as R28; the issue's "matchline 13/13 live" baseline is a *contract-citation* baseline (R27), not a glossary baseline — the glossary baseline in the issue brief does not exist as a checked-out artifact |

### 1.2 Authority decisions (the grouping the axis buys)

Rows that share one authority decision count as one decision (issue #24 §2).

| group | authority decision | rows | row count |
|---|---|---|---|
| G1 | The committed test-inventory chain is canonical; every other count is derived from it | R01, R02 | 2 |
| G2 | Where the unimplemented-CLI work lives (tracking home), after #2947 closed | R03, R04 | 2 |
| G3 | The module-size gate's semantics (missing path = failure) and its coverage of the three largest files | R05, R06 | 2 |
| G4 | Ignores/quarantines whose blocker has closed must be re-evaluated | R07, R10, R11, R12, R13, R14 | 6 |
| G5 | Whether a CI lane exists for the quarantined population cohort | R08 | 1 |
| G6 | The BatchOracle surrogate-path precondition (no written artifact exists) | R09 | 1 |
| G7 | `EXPECTED_TEST_COUNT` in ci.yml is the canonical test count; prose must be derived from it | R15, R16, R17 | 3 |
| G8 | IFC import unit-conversion semantics (no written artifact exists) | R18 | 1 |
| G9 | staging-studio's AGENTS.md test census is derived from the live count | R19 | 1 |
| G10 | `project.godot` `[autoload]` is the registry of record | R20, R21, R24 | 3 |
| G11 | The legacy runner's file list is derived from the live test tree | R22 | 1 |
| G12 | The design-token path (neither code nor CLAUDE.md is right) | R23 | 1 |
| G13 | The 2026-10-25 standing directive beats `docs/roof-simplify-wiring-decision.md` | R25 | 1 |
| G14 | fluxion CONTRACT.md's 12 checkable citations are live | R26 | 1 |
| G15 | matchline CONTRACT.md's 13 checkable citations are live | R27 | 1 |
| G16 | fluxion has no glossary artifact at HEAD | R28 | 1 |
| G17 | matchline has no glossary artifact at HEAD | R29 | 1 |
| | **17 decisions over 29 rows** | | **29** |

---

## 2. Coverage table — plan-coder's checking surface, 8 supervised repos × 3 artifact kinds

Artifact presence was verified per repo from the GitHub tree API (`gh api
repos/anchapin/<repo>/git/trees/HEAD?recursive=1`), not from memory; the plan-coder
side was verified from this worktree's `bin/` and `prompts/` and from the public
plan-coder tree at HEAD.

Legend: **covered** = a checker in this repo reads this artifact and its output is
live/dead per row; **unmapped** = an artifact exists (or should) with no checker;
**excluded-with-reason** = a recorded, deliberate exclusion; **limitation** = what the
covered check does *not* catch.

| repo | artifact kind | covered (paths) | unmapped | excluded-with-reason | limitation |
|---|---|---|---|---|---|
| fluxion | CONTRACT.md | `bin/contract_check.py` → `CONTRACT.md` (14 citations: 12 checkable + 2 external; 12/12 live at HEAD, R26) | — | none recorded | citations checked, **prose drift not caught** — a stale *number* inside AGENTS.md is not a citation and passes the check (R01/R02 are exactly this) |
| fluxion | glossary core terms | none | `UBIQUITOUS_LANGUAGE.md` — 57 core terms, present **only in open PR #4763** (branch `docs/ubiquitous-language`), absent from HEAD (R28) | none recorded | the artifact is not in the repo; no glossary checker exists in this repo, so there is nothing to run |
| fluxion | flagcheck classes | shepherd step 4c (8 classes) as described in `docs/task-register.md` §Task 4 | per-class outcomes for fluxion PRs | none recorded | **implementation not present in the public plan-coder repo** — no `bin/flagcheck.py` at HEAD (contradicts issue #24's premise) |
| matchline | CONTRACT.md | `bin/contract_check.py` → `CONTRACT.md` (14 citations: 13 checkable + 1 external; 13/13 live at HEAD, R27) | — | none recorded | citations checked, prose drift not caught (R15/R16/R17 are this class) |
| matchline | glossary core terms | none | `UBIQUITOUS_LANGUAGE.md` — 43 core terms, **only in open PR #1369**, absent from HEAD (R29) | none recorded | no glossary checker exists; CONTRACT.md itself still carries the two stale prose claims (R15, R25) |
| matchline | flagcheck classes | shepherd step 4c (8 classes) | per-class outcomes for matchline PRs | none recorded | implementation absent from the public repo (as fluxion) |
| staging-studio | CONTRACT.md | none | no `CONTRACT.md` in the tree | none recorded | nothing to check; the repo's prose census (R19) drifts unguarded |
| staging-studio | glossary core terms | none | `CONTEXT.md` present in the tree; no checker reads it | none recorded | an existing vocabulary artifact with no drift reader |
| staging-studio | flagcheck classes | shepherd step 4c (8 classes) | per-class outcomes | none recorded | implementation absent from the public repo |
| planar-nexus | CONTRACT.md | none | no `CONTRACT.md` in the tree | none recorded | — |
| planar-nexus | glossary core terms | none | no glossary artifact; `.planning/**/CONTEXT.md` files exist (planning notes, not a vocabulary) | none recorded | — |
| planar-nexus | flagcheck classes | shepherd step 4c (8 classes) | per-class outcomes | none recorded | implementation absent from the public repo |
| armored-archer | CONTRACT.md | none | no `CONTRACT.md` in the tree | none recorded | — |
| armored-archer | glossary core terms | none | `docs/GLOSSARY.md` present (a reconciled game vocabulary — reconciled with `CONTEXT.md` in #1103) — no checker reads it | none recorded | **surprise**: a real, previously-reconciled glossary that no checker covers |
| armored-archer | flagcheck classes | shepherd step 4c (8 classes) | per-class outcomes | none recorded | implementation absent from the public repo |
| building-ai-pocs | CONTRACT.md | none | no `CONTRACT.md` | none recorded | — |
| building-ai-pocs | glossary core terms | none | no glossary artifact | none recorded | — |
| building-ai-pocs | flagcheck classes | shepherd step 4c (8 classes) | per-class outcomes | none recorded | implementation absent from the public repo |
| portkit-v2 | CONTRACT.md | none | no `CONTRACT.md` | none recorded | — |
| portkit-v2 | glossary core terms | none | no glossary artifact | none recorded | — |
| portkit-v2 | flagcheck classes | shepherd step 4c (8 classes) | per-class outcomes | none recorded | implementation absent from the public repo |
| plan-coder | CONTRACT.md | `bin/contract_check.py` exists (checker code) but **has no repo of its own to check** — `plan-coder/CONTRACT.md` is absent from the tree | the repo has no contract for itself | none recorded | a checker with no subject in its own repo |
| plan-coder | glossary core terms | none | no glossary artifact | none recorded | — |
| plan-coder | flagcheck classes | described in `docs/task-register.md` §Task 4 (8 classes: `ci-none`, `ci-reds`, `ci-unclassified`, `workflow-kind`, `security-scan`, `conflict-stale`, `physics-gate`, `held-green`; "ships disabled") | the class→repo mapping (which classes apply to which repo) is recorded nowhere | none recorded | **contradiction with the issue brief**: issue #24 states the classes live in `bin/flagcheck.py`; no such file exists in the public plan-coder repo at HEAD (or on any of its 18 branches) |

### 2.1 What the coverage table itself shows

- **24 cells: 10 covered, 14 unmapped, 0 excluded-with-reason.** The 10 covered
  cells are the 2 contract cells (fluxion, matchline — the only two repos with a
  `CONTRACT.md` at HEAD) and the 8 flagcheck cells, whose *implementation* is absent
  from the public repo (§2, row "flagcheck classes"). There is not one recorded
  deliberate exclusion anywhere in plan-coder — which is exactly the state the issue
  predicted ("absence is indistinguishable from oversight"). The
  "excluded-with-reason" column is empty because *no coverage artifact exists to
  record exclusions in*.
- The table was **not** fillable in under an hour from existing records: it needed
  tree listings for 8 repos, a branch enumeration of `docs/ubiquitous-language` in
  two of them, a check of 18 plan-coder branches for `bin/flagcheck.py`, and 24
  citation-resolution checks for the two control rows.
- Three findings that are *surprises* under the issue's definition (not known,
  deliberate exclusions): (a) **both** glossary artifacts exist only in unmerged PRs,
  so the "glossary core terms" the brief treats as a live checking surface has no
  checked-out subject; (b) the flagcheck implementation the brief names is absent
  from the public repo, so the third artifact kind is documented but not present;
  (c) `armored-archer/docs/GLOSSARY.md` and `staging-studio/CONTEXT.md` are
  vocabularies no checker reads.
- The issue's "fluxion 12/12 live, matchline 13/13 live" baselines are **contract
  citation** baselines, and they reproduce exactly (R26, R27) when the repo's own
  `bin/contract_check.py` extractor is run over the HEAD contracts: 14 citations each,
  12 checkable for fluxion (2 external) and 13 checkable for matchline (1 external),
  all resolving. The glossary baseline (43/57 terms) does not exist at HEAD.

---

## 3. Verdict against the issue's Kill / Proceed bars

Sample: **N = 29 rows** (27 findings + 2 control/absence rows). Class counts:

| class | rows | share of N |
|---|---|---|
| `code-right` | 17 | 58.6% |
| `spec-right` | 3 | 10.3% |
| `neither-right` | 2 | 6.9% |
| `unmapped` | 5 | 17.2% |
| `aligned` (controls) | 2 | 6.9% |
| `unverifiable` | 0 | 0.0% |

**Kill bar 1 — ≥90% of rows code-right with an action already obvious from the binary
live/dead output: NOT met.** 17/29 = 58.6% ≪ 90%. Moreover only 2 of the 29 rows
(R26, R27) came from a binary live/dead output at all; none of the 17 code-right rows
had an action that the binary output made obvious (they needed prose/count
comparison, a registry read, or a judgment about which side is canonical).

**Kill bar 2 — grouping collapses nothing, rows-per-decision ≈ 1.0: NOT met.**
29 rows / 17 authority decisions = **1.71** rows per decision. Under a deliberately
conservative grouping that gives every distinct artifact its own decision
(G3 split, G4 split into per-registry decisions), the floor is 29/22 = **1.32**.
Neither is ≈ 1.0.

**Kill bar 3 — coverage table fillable in under an hour from existing records AND
surfaces zero surprising unmapped areas: NOT met.** Both conjuncts fail: the table
needed branch enumeration and 24 citation checks (§2.1), and it surfaces three
surprises (unmerged glossary artifacts, missing flagcheck implementation, uncovered
`GLOSSARY.md`/`CONTEXT.md` vocabularies).

**Proceed bar 1 — ≥25% of rows in neither-right / unmapped / unverifiable: NOT met
(marginally).** (2 + 5 + 0) / 29 = 7/29 = **24.1%** < 25%. Sensitivity: if the two
absence rows R28/R29 are counted as coverage-table facts rather than sample rows,
5/27 = 18.5%.

**Proceed bar 2 — grouping reduces Alex-facing decisions by ≥30% against the per-item
baseline: MET.** Baseline = one decision per row = 29. Grouped = 17 decisions.
Reduction = (29 − 17) / 29 = **41.4%** ≥ 30%. The biggest single group is G4 (six
ignore/quarantine rows → one decision); G7 turns three count-drift rows in two repos
into one. Conservative floor (22 decisions) = 24.1% < 30%, so the bar fires under
the issue's own definition ("rows sharing one authority decision count as one
decision") with the natural grouping, and does not fire at the most pessimistic
grouping.

**Proceed bar 3 — the coverage table surfaces ≥1 genuinely surprising unmapped area:
MET.** Three, listed in §2.1: the two `UBIQUITOUS_LANGUAGE.md` artifacts that exist
only in unmerged PRs #4763/#1369; the absent `bin/flagcheck.py` that the issue brief
names as existing; and the two uncovered vocabularies (`armored-archer/docs/GLOSSARY.md`,
`staging-studio/CONTEXT.md`). None of these is a known deliberate exclusion — there
are no recorded exclusions at all.

### Verdict: **PROCEED**

Bar fired: **Proceed bar 3** (coverage table surfaces surprising unmapped areas),
with **Proceed bar 2** also met at 41.4% decision reduction. No Kill bar fired
(58.6% ≪ 90%; 1.71 ≫ 1.0 rows/decision; the coverage table was neither quick nor
surprise-free). Proceed bar 1 did not fire (24.1% < 25%) — the sample is *not*
dominated by undecidable rows, which is itself informative: the axis is mostly
resolving *which side wins*, not escalating *who decides*.

This is a measurement result only. Per issue #24's hard stops, Phase 1 (the
`authority:` line in harness-tune's drift sections and a maintained coverage file) is
**not** built here and remains gated on Alex's separate go; no prompt, config,
checker, merge policy or flagcheck class is touched by this document.

---

## 4. Claims

Typed per repo convention: **[observed]** = read directly from a primary record in
this run; **[inference]** = a reading that goes beyond the record but is supported by
it; **[unsupported]** = asserted without a reachable record (and therefore not used
to drive any conclusion above).

- [observed] The raw table has 29 rows; 17 `code-right`, 3 `spec-right`, 2
  `neither-right`, 5 `unmapped`, 2 `aligned`, 0 `unverifiable`.
- [observed] fluxion `CONTRACT.md` at HEAD yields 14 extracted citations, 12 of them
  checkable, all resolving (R26); matchline `CONTRACT.md` yields 14 citations, 13
  checkable, all resolving (R27).
- [observed] `UBIQUITOUS_LANGUAGE.md` exists in fluxion only in open PR #4763 and in
  matchline only in open PR #1369 (both head `docs/ubiquitous-language`, base
  `develop`, unmerged); neither repo's HEAD tree contains the file.
- [observed] `bin/flagcheck.py` does not exist in the plan-coder repo at HEAD nor on
  any of its 18 branches; the 8 classes appear only as prose in
  `docs/task-register.md` §Task 4 — this contradicts the issue brief's premise.
- [observed] `prompts/harness-tune.md` in this worktree contains no occurrence of
  "contract", "glossary" or "drift" — the harness-tune drift sections the issue
  describes are not present in this repo's prompt template at HEAD.
- [observed] `docs/roof-simplify-wiring-decision.md` at matchline HEAD still reads
  "**Verdict: Option C** — wire the roof simplifier into the production path behind
  an opt-in flag now", while matchline `CONTRACT.md` states that Alex's 2026-10-25
  standing directive "supersedes the opt-in Option C ... which is stale on this
  point" and PR #1297 (merged 2026-10-26) wired it on by default (R25).
- [observed] Every action in §1.1 is a merged PR except R23 (PR #1828 open, issue
  #1824 open) and R25 (no issue filed at all).
- [observed] PR #4768's title cites #4755 although the finding it closes (#4759) is
  the IDF Case 600 row; the Case 195 row (#4755) is closed by PR #4771.
- [inference] The R07/R10–R14 cluster is a single authority decision ("re-evaluate
  ignores whose blocker closed"): six separately-filed issues, each with its own
  artifact, all resolve to the same rule.
- [inference] The axis would have *shortened* R25 to one line and made it visible:
  it is the only doc-vs-directive disagreement in the sample that neither a checker
  nor a filing pass surfaced.
- [inference] The Proceed bar 2 result (41.4% decision reduction) is robust to the
  R12 wording and to R19's cross-repo grouping, but would fall to 24.1% under the
  fully conservative grouping — the verdict's Proceed bar 2 therefore depends on
  accepting the issue's "one authority decision" definition, which is why bar 3 is
  the load-bearing bar.
- [unsupported] That the two hand-found prose-drift items were found by a specific
  human pass on a specific date — the issue asserts the hand-finding; this run only
  confirms the *state* of both artifacts (R15 closed, R25 still stale).
- [unsupported] The number of harness-tune drift reads performed since 2026-11-11,
  and their contents — no record of those reads is reachable from this worktree
  (§5). No row was invented to stand in for them.
- [unsupported] Any statement about `retrofit-copilot`, which appears in the
  file-size spike's inventory but is not among the 8 supervised repos the issue
  names; it was left out of the coverage table and is recorded as a limitation.

---

## 5. Reproduction, and the sources that were not reachable

Commands used (read-only `gh`; the proxy variables from `/etc/environment` are
required in this cell):

```
gh issue list --repo anchapin/<repo> --state all --search "created:>=2026-11-10" \
   --json number,title,state,createdAt,closedAt,labels,author
gh api "repos/anchapin/<repo>/git/trees/HEAD?recursive=1" --jq '.tree[].path'
gh api "repos/anchapin/<repo>/contents/<path>?ref=docs/ubiquitous-language" --jq .content
gh api "repos/anchapin/<repo>/issues/<n>/timeline" --jq '[.[] | select(.event=="cross-referenced")]'
gh pr list --repo anchapin/<repo> --state all --search "created:2026-11-11..2026-11-14" \
   --json number,title,mergedAt,state
python3 -c "import importlib.util; …"   # bin/contract_check.py extract_citations over the two CONTRACT.md files
```

**Sources that could not be reached from this worktree** (recorded as limitations, not
papered over):

1. **harness-tune drift reads since 2026-11-11 (contract + glossary sections).**
   The drift-read output records live in the plan-coder runtime state directory
   (`{{state.dir}}`, referenced by `prompts/harness-tune.md`), which is outside the
   worktree; `config/deployment.local.json` is operator-private and gitignored and
   was neither read nor edited. Failing commands:
   `grep -n -i "drift\|contract\|glossary" prompts/harness-tune.md` → *no matches*
   (the template at HEAD has no drift sections), and
   `gh api repos/anchapin/plan-coder/git/trees/HEAD?recursive=1 | grep -i glossary`
   → *no glossary checker in the public repo*. **Consequence:** the sample had to be
   assembled from the doc-vs-code/spec-vs-code findings the factory filed across the
   8 supervised repos between 2026-11-11 and 2026-11-13 — the same *kind* of finding
   the drift read surfaces, but not the drift read itself. Rows were not invented to
   stand in for the missing reads.
2. **The glossary checker.** `bin/glossary_check.py` does not exist in this repo
   (`gh api repos/anchapin/plan-coder/git/trees/HEAD?recursive=1` → only
   `bin/contract_check.py` among the two). The issue's "glossary_check emits
   live/dead per core-term row" could not be exercised; the glossary rows R28/R29
   are therefore absences, not drift findings.
3. **The flagcheck implementation.** `bin/flagcheck.py` is absent from the public
   plan-coder repo at HEAD and on all 18 branches (`gh api repos/anchapin/plan-coder/branches`).
   The 8 classes are documented in `docs/task-register.md`; their per-repo
   application could not be observed, only their documented existence.
4. **The shepherd's own flag records** (`flagcheck_ledger.jsonl`,
   `flag_resolutions.json`) live in the same out-of-worktree state directory; the
   "shepherd flags that turned on a doc-vs-code disagreement" source was therefore
   reconstructed from the *public* PR/issue record (the issues the factory filed and
   the PRs that closed them), not from the shepherd ledger.
5. **The fluxion `CONTRACT.md` at HEAD vs the branch copy** are citation-identical
   (same 14 extractions); the fluxion `UBIQUITOUS_LANGUAGE.md` exists only in
   PR #4763. Nothing in this run reads the operator's own records.

Nothing in this document was produced by editing a code, prompt, config or test file.
