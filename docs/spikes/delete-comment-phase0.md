# /delete-comment Phase 0 — census + classification measurement (#142)

Status: Phase 0 measurement complete, **adjudication pending**. No comment was
deleted or edited; no issue was filed; no lint rule was built. Phase 1 remains
gated on a Phase 0 verdict per part plus Alex's separate tap.

Provenance note: the worker run (balanced, MiniMax-M2.7, 60/60 steps) built the
extractor, the classification ledger, and the tests, then hit the step cap
before writing this report. This report was authored by the supervisor from the
worker's committed ledger (`docs/spikes/delete-comment-phase0-runs.json`) after
independently re-running the extractor and the tests; every number below was
reproduced by the supervisor, not copied from worker prose.

## Method (and one deviation from the issue text)

- Tool: `bin/delete_comment_phase0.py` — a deterministic, rule-table classifier
  (`method: "table"` in every ledger record). The issue's Step 1 imagined one
  cheap-tier agent run per file (46 runs); this run instead classified with a
  deterministic rule table in a single worker run. That is a deliberate
  measurement trade: the counts are exactly reproducible, but the classification
  is only as good as the rule table, which is precisely what Step 2 adjudication
  exists to judge. The PASS bars below are therefore reported as pending, not
  passed.
- Block definition: a maximal run of consecutive comment-only (`#`) lines.
  Excluded: docstrings, trailing/inline comments, the shebang line, and the
  PEP 263 encoding declaration. Applied consistently by the extractor.
- Every one of the 328 ledger records carries a verbatim evidence quote
  (supervisor-verified: 0 records with an empty evidence field).

## Census (ratchet-baseline candidate)

Base: origin/main @ 2ea258e03678f744f5158edd84ce1814af84fd41.
Population: `bin/*.py` — 45 files scanned, 36 with blocks.

| Category | Blocks (n=328) |
|---|---|
| Tautology | 195 |
| Excuse | 4 |
| Stale | 0 |
| Decision Record | 35 |
| Keep — Provenance/Invariant | 94 |

Self-reference note: the census includes the Phase 0 tool's own file
(`bin/delete_comment_phase0.py`, 11 blocks). Excluding it, the pre-existing
tree is 317 blocks in 44 files (Tautology 191, Keep 87, Decision Record 35,
Excuse 4, Stale 0). The issue's drafting census counted 375 blocks in 46 files
at an older HEAD; the difference is the block definition above (comment-only
lines; docstrings and inline comments excluded) plus tree drift since drafting.

Reproducibility (Part C evidence): the supervisor ran the extractor twice;
both runs produced identical counts — 328 blocks, identical per-category
counts. `tests/test_delete_comment_phase0.py` (16 tests, all passing) pins the
extractor behaviour including determinism.

## Excuse clusters (Step 3 dry-run — no issues filed)

Raw excuse blocks: 4. Clusters: 3. Flood ratio (raw / clusters): 1.33.

- `accepted-approximation` — bin/dashboard.py:77
- `heuristic-not-proof` — bin/provenance_phase0.py:633, bin/provenance_phase0.py:83
- `measurement-lower-bound` — bin/headroom_phase0.py:101

With a raw excuse count of 4 (< 10), clustering is moot but harmless per the
issue's Part B wording; the flood risk this spike worried about does not exist
in plan-coder's own bin/ at this base.

## Cost (Step 4)

- This run: ~24,590 estimated tokens (11,470 to read the census's 791 comment
  lines + 13,120 adjudication estimate at 40 tokens/block × 328). The worker
  run itself burned 45,776 tokens-out (MiniMax-M2.7, balanced).
- Repeatability projection (staging-studio `src/components`, 77 files, 2,643
  comment lines, per the issue): ~1,096 projected blocks at ~75 tokens/block —
  full figures in the ledger's `projection` object. Well under the Part C bar
  of ≤2% of one supervisor cycle's token budget (750k).

## Per-part status against the §4 PASS bars

- **Part A (classification quality): PENDING ADJUDICATION.** Agreement ≥85%,
  zero provenance false-deletes, and false-Keep ≤25% can only be judged by
  Step 2 adjudication (all Keep blocks, all Excuse blocks, 25% sample of the
  rest) against the current code. That adjudication is the main agent's / Alex's
  step and has not happened yet. Nothing in this report claims Part A passes.
- **Part B (excuse clustering): flood ratio measured (4 raw → 3 clusters);
  precision PENDING ADJUDICATION** (excuse precision ≥80% is an adjudication
  judgement). The small-n clause applies: raw excuse count < 10.
- **Part C (baseline + cost): PASS on the evidence available.** Extraction is
  mechanically reproducible (two identical supervisor runs + determinism tests)
  and the extrapolated repeat-audit cost is ≤2% of a cycle budget by a wide
  margin.

## Verification (supervisor re-runs, 2026-11-21)

- `python3 bin/delete_comment_phase0.py --json` ×2 → 328 blocks, identical
  category counts both runs.
- `python3 -m pytest tests/test_delete_comment_phase0.py -q` → 16 passed.
- `python3 -m pytest tests/ -q` (full suite, supervisor re-run) → 567 passed, 2 skipped, 62 subtests passed.
- Ruff: not re-run by the supervisor (no JSON ruff output available in this
  environment); CI is the lint evidence.
