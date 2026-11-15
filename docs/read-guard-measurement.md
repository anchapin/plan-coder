# Phase-0 read-guard measurement (issue #56)

## Sample

- Derived per-run table: `docs/spikes/headroom-phase0-runs.json`
- Real worker runs: 484 (18329 steps) from the corpus of 801 transcript files
- Window: 2026-10-31T06:47:05.107410+00:00 .. 2026-11-14T06:47:05.107410+00:00
- The corpus directory (`/home/hatch/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts`) is OUTSIDE this checkout and `transcripts/` is gitignored, so the raw per-call transcripts are not readable here; the committed derived table is the sample this measurement reads.

## Table 1 - read volume per run (real data)

| metric | chars | tokens (chars/4) |
|---|---|---|
| median | 1628 | 407 |
| mean | 2514 | 628 |
| p90 | 7228 | 1807 |
| max | 17230 | 4308 |
| total | 1216771 | 304193 |

## Table 2 - the >40 KB question

| count | value |
|---|---|
| runs whose whole read output exceeded 40 KB | 0 of 484 |
| runs with no read_file call at all | 164 of 484 |
| runs whose read output exceeded one saturated read (12,000 chars) | 12 of 484 |
| saturated reads, upper bound (whole corpus) | 332 |

## Table 3 - cost of the guard's target event

| metric | value |
|---|---|
| tokens per saturated read (12,000 chars) | 3000 |
| median read tokens per run | 407 |
| median read share of the median run's context | 0.777% |
| heaviest run's read tokens | 4308 |
| heaviest run's read share of context | 8.228% |
| saturated reads per run, upper bound | 0.69 |

## Verdict

**DROP** - no run in the sample exceeded the 40 KB guard on read output; the harness already caps a single read's tool result at 12,000 characters, so the event the guard would catch never reaches the model's context.
