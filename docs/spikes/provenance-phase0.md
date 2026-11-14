# Phase 0 provenance-index measurement (issue #29, Part A)

Measurement only: no harness change, no config change, no prompt
change, no dependency, no LLM call.  This report decides whether
the **provenance index** of issue #29 is worth building (Phase 1).

## Parameters

| parameter | value |
|---|---|
| ledger rows read | 574 |
| sample | most recent 100 `pr_opened` ledger rows with a transcript |
| runs measured (n) | 100 |
| runs with a changed-file list | 100 |
| runs with a gh error | 0 |
| changed files total | 572 |
| attributed changed files total | 305 |
| edit/write calls seen | 1154 |
| aggregate coverage | 53.3% |
| per-PR median coverage | 100.0 |
| per-PR mean coverage | 72.3 |
| precision spot-check | 20/20 |
| unattributed subsample | 20 files, 13/20 explainable |
| reason-excerpt yield | 82.0% |

**Definitions.**

* *changed files* -- the PR file list from `gh pr view <pr> --repo
  <slug> --json files` (repo slug and PR number parsed from the
  ledger row's `pr_url`).
* *attributed* -- a changed file whose path is named by an
  `edit_file` / `write_file` call in that run's transcript.  Worker
  paths are normalised to repo-relative paths (worktree prefix and
  `./` stripped) before matching.  Files that no edit/write call
  names are `unattributed`; nothing is inferred.
* *coverage* -- attributed changed files / all changed files, both
  aggregated over the sample and computed per PR (median over the
  runs that have at least one changed file).
* *precision* -- of 20 sampled attributed (file, step) pairs, the
  share where the transcript's raw tool-call arguments for that
  step really do name that file (the file is a PR changed file, the
  step has an `edit_file` / `write_file` call, and the raw args
  string contains the path).
* *reason excerpt* -- the step's `assistant_text` (harness-truncated
  to 400 characters) of the **first** editing step of an attributed
  file; non-empty means the index would carry a stated reason.

## Table 1 -- per-PR raw table (the sample)

The same rows are machine-readable in
`provenance-phase0-runs.csv` / `provenance-phase0-runs.json`.

| run_id | repo | pr | changed | attributed | unattributed | coverage | edit/write calls | attributed w/ reason | reason yield |
|---|---|---|---|---|---|---|---|---|---|
| plan-coder-5-1794639685 | anchapin/plan-coder | 48 | 11 | 4 | 7 | 36.4% | 24 | 1 | 25% |
| plan-coder-22-1794638490 | anchapin/plan-coder | 47 | 6 | 2 | 4 | 33.3% | 18 | 2 | 100% |
| building-ai-pocs-50-1794636900 | anchapin/building-ai-pocs | 61 | 22 | 7 | 15 | 31.8% | 20 | 6 | 86% |
| plan-coder-21-1794636400 | anchapin/plan-coder | 46 | 3 | 2 | 1 | 66.7% | 15 | 2 | 100% |
| plan-coder-28-1794634175 | anchapin/plan-coder | 44 | 2 | 1 | 1 | 50.0% | 17 | 1 | 100% |
| building-ai-pocs-54-1794630817 | anchapin/building-ai-pocs | 60 | 5 | 1 | 4 | 20.0% | 4 | 1 | 100% |
| plan-coder-42-1794630817 | anchapin/plan-coder | 43 | 6 | 2 | 4 | 33.3% | 14 | 2 | 100% |
| retrofit-copilot-7-1794630817 | anchapin/retrofit-copilot | 21 | 4 | 4 | 0 | 100.0% | 19 | 1 | 25% |
| portkit-v2-442-1794630817 | anchapin/portkit-v2 | 443 | 5 | 4 | 1 | 80.0% | 5 | 2 | 50% |
| building-ai-pocs-57-1794627254 | anchapin/building-ai-pocs | 59 | 61 | 6 | 55 | 9.8% | 19 | 6 | 100% |
| retrofit-copilot-6-1794627147 | anchapin/retrofit-copilot | 20 | 4 | 4 | 0 | 100.0% | 14 | 4 | 100% |
| retrofit-copilot-5-1794626178 | anchapin/retrofit-copilot | 19 | 2 | 2 | 0 | 100.0% | 11 | 2 | 100% |
| building-ai-pocs-56-1794626301 | anchapin/building-ai-pocs | 58 | 7 | 2 | 5 | 28.6% | 2 | 1 | 50% |
| planar-nexus-2965-1794623737 | anchapin/planar-nexus | 2971 | 7 | 5 | 2 | 71.4% | 8 | 4 | 80% |
| plan-coder-24-1794623737 | anchapin/plan-coder | 41 | 1 | 1 | 0 | 100.0% | 4 | 1 | 100% |
| retrofit-copilot-13-1794623737 | anchapin/retrofit-copilot | 18 | 1 | 1 | 0 | 100.0% | 3 | 1 | 100% |
| plan-coder-38-1794621184 | anchapin/plan-coder | 40 | 3 | 1 | 2 | 33.3% | 27 | 1 | 100% |
| portkit-v2-438-1794621064 | anchapin/portkit-v2 | 441 | 4 | 4 | 0 | 100.0% | 14 | 4 | 100% |
| retrofit-copilot-10-1794621260 | anchapin/retrofit-copilot | 17 | 2 | 2 | 0 | 100.0% | 8 | 2 | 100% |
| armored-archer-1848-1794620942 | anchapin/armored-archer | 1856 | 3 | 3 | 0 | 100.0% | 7 | 3 | 100% |
| matchline-1396-1794618447 | anchapin/matchline | 1401 | 2 | 1 | 1 | 50.0% | 7 | 1 | 100% |
| portkit-v2-439-1794618750 | anchapin/portkit-v2 | 440 | 4 | 4 | 0 | 100.0% | 26 | 4 | 100% |
| plan-coder-34-1794618462 | anchapin/plan-coder | 39 | 2 | 2 | 0 | 100.0% | 8 | 1 | 50% |
| retrofit-copilot-12-1794618467 | anchapin/retrofit-copilot | 16 | 4 | 4 | 0 | 100.0% | 13 | 4 | 100% |
| staging-studio-1546-1794618442 | anchapin/staging-studio | 1550 | 2 | 2 | 0 | 100.0% | 7 | 2 | 100% |
| armored-archer-1850-1794618457 | anchapin/armored-archer | 1855 | 3 | 3 | 0 | 100.0% | 14 | 3 | 100% |
| matchline-1398-1794613424 | anchapin/matchline | 1400 | 7 | 1 | 6 | 14.3% | 1 | 1 | 100% |
| planar-nexus-2967-1794616245 | anchapin/planar-nexus | 2969 | 3 | 0 | 3 | 0.0% | 1 | 0 | n/a |
| staging-studio-1547-1794613663 | anchapin/staging-studio | 1549 | 8 | 1 | 7 | 12.5% | 6 | 1 | 100% |
| plan-coder-35-1794613424 | anchapin/plan-coder | 37 | 1 | 1 | 0 | 100.0% | 12 | 1 | 100% |
| armored-archer-1849-1794613424 | anchapin/armored-archer | 1854 | 4 | 4 | 0 | 100.0% | 8 | 2 | 50% |
| retrofit-copilot-11-1794613424 | anchapin/retrofit-copilot | 15 | 4 | 1 | 3 | 25.0% | 2 | 1 | 100% |
| building-ai-pocs-53-1794612128 | anchapin/building-ai-pocs | 55 | 9 | 6 | 3 | 66.7% | 19 | 5 | 83% |
| staging-studio-1545-1794610888 | anchapin/staging-studio | 1548 | 2 | 2 | 0 | 100.0% | 6 | 2 | 100% |
| planar-nexus-2966-1794611000 | anchapin/planar-nexus | 2968 | 3 | 0 | 3 | 0.0% | 1 | 0 | n/a |
| armored-archer-1851-1794611064 | anchapin/armored-archer | 1853 | 2 | 2 | 0 | 100.0% | 7 | 2 | 100% |
| plan-coder-23-1794610079 | anchapin/plan-coder | 36 | 2 | 1 | 1 | 50.0% | 25 | 1 | 100% |
| retrofit-copilot-4-1794609829 | anchapin/retrofit-copilot | 14 | 2 | 2 | 0 | 100.0% | 13 | 2 | 100% |
| building-ai-pocs-47-1794606764 | anchapin/building-ai-pocs | 52 | 8 | 1 | 7 | 12.5% | 3 | 1 | 100% |
| plan-coder-27-1794606223 | anchapin/plan-coder | 33 | 4 | 3 | 1 | 75.0% | 7 | 2 | 67% |
| retrofit-copilot-2-1794606094 | anchapin/retrofit-copilot | 9 | 17 | 3 | 14 | 17.6% | 7 | 3 | 100% |
| planar-nexus-2963-1794604067 | anchapin/planar-nexus | 2964 | 9 | 8 | 1 | 88.9% | 25 | 8 | 100% |
| staging-studio-1543-1794603412 | anchapin/staging-studio | 1544 | 6 | 6 | 0 | 100.0% | 13 | 6 | 100% |
| plan-coder-16-1794603464 | anchapin/plan-coder | 32 | 2 | 2 | 0 | 100.0% | 10 | 1 | 50% |
| retrofit-copilot-3-1794603432 | anchapin/retrofit-copilot | 8 | 4 | 4 | 0 | 100.0% | 20 | 4 | 100% |
| portkit-v2-436-1794600861 | anchapin/portkit-v2 | 437 | 3 | 2 | 1 | 66.7% | 6 | 1 | 50% |
| plan-coder-25-1794600861 | anchapin/plan-coder | 31 | 3 | 3 | 0 | 100.0% | 11 | 3 | 100% |
| building-ai-pocs-45-1794600861 | anchapin/building-ai-pocs | 49 | 2 | 2 | 0 | 100.0% | 4 | 2 | 100% |
| plan-coder-17-1794598324 | anchapin/plan-coder | 20 | 5 | 5 | 0 | 100.0% | 11 | 4 | 80% |
| portkit-v2-434-1794595360 | anchapin/portkit-v2 | 435 | 3 | 2 | 1 | 66.7% | 8 | 2 | 100% |
| plan-coder-15-1794595360 | anchapin/plan-coder | 19 | 4 | 4 | 0 | 100.0% | 24 | 4 | 100% |
| building-ai-pocs-43-1794595360 | anchapin/building-ai-pocs | 44 | 10 | 6 | 4 | 60.0% | 22 | 6 | 100% |
| plan-coder-10-1794588962 | anchapin/plan-coder | 14 | 1 | 1 | 0 | 100.0% | 5 | 1 | 100% |
| building-ai-pocs-41-1794584730 | anchapin/building-ai-pocs | 42 | 3 | 3 | 0 | 100.0% | 15 | 3 | 100% |
| building-ai-pocs-37-1794581886 | anchapin/building-ai-pocs | 40 | 27 | 7 | 20 | 25.9% | 22 | 6 | 86% |
| portkit-v2-302-1794577257 | anchapin/portkit-v2 | 433 | 2 | 2 | 0 | 100.0% | 14 | 2 | 100% |
| portkit-v2-296-1794573369 | anchapin/portkit-v2 | 432 | 4 | 3 | 1 | 75.0% | 14 | 2 | 67% |
| planar-nexus-2958-1794573255 | anchapin/planar-nexus | 2961 | 5 | 5 | 0 | 100.0% | 21 | 5 | 100% |
| armored-archer-1831-1794573188 | anchapin/armored-archer | 1844 | 2 | 2 | 0 | 100.0% | 14 | 2 | 100% |
| plan-coder-5-1794559850 | anchapin/plan-coder | 12 | 2 | 2 | 0 | 100.0% | 3 | 1 | 50% |
| portkit-v2-335-1794557040 | anchapin/portkit-v2 | 431 | 5 | 4 | 1 | 80.0% | 18 | 4 | 100% |
| plan-coder-2-1794557301 | anchapin/plan-coder | 11 | 11 | 4 | 7 | 36.4% | 6 | 4 | 100% |
| portkit-v2-299-1794554800 | anchapin/portkit-v2 | 430 | 2 | 2 | 0 | 100.0% | 16 | 2 | 100% |
| armored-archer-1832-1794554800 | anchapin/armored-archer | 1843 | 3 | 3 | 0 | 100.0% | 15 | 3 | 100% |
| planar-nexus-2948-1794550549 | anchapin/planar-nexus | 2959 | 2 | 2 | 0 | 100.0% | 7 | 2 | 100% |
| matchline-1384-1794550393 | anchapin/matchline | 1391 | 8 | 3 | 5 | 37.5% | 13 | 2 | 67% |
| armored-archer-1825-1794550486 | anchapin/armored-archer | 1842 | 3 | 3 | 0 | 100.0% | 16 | 2 | 67% |
| portkit-v2-302-1794545640 | anchapin/portkit-v2 | 429 | 10 | 1 | 9 | 10.0% | 13 | 1 | 100% |
| planar-nexus-2949-1794545620 | anchapin/planar-nexus | 2957 | 7 | 2 | 5 | 28.6% | 6 | 2 | 100% |
| plan-coder-8-1794545650 | anchapin/plan-coder | 9 | 10 | 6 | 4 | 60.0% | 12 | 5 | 83% |
| armored-archer-1830-1794545630 | anchapin/armored-archer | 1841 | 3 | 3 | 0 | 100.0% | 11 | 3 | 100% |
| fluxion-4789-1794541401 | anchapin/fluxion | 4803 | 10 | 3 | 7 | 30.0% | 9 | 3 | 100% |
| plan-coder-1-1794541471 | anchapin/plan-coder | 7 | 7 | 7 | 0 | 100.0% | 35 | 7 | 100% |
| planar-nexus-2950-1794541431 | anchapin/planar-nexus | 2955 | 3 | 3 | 0 | 100.0% | 13 | 3 | 100% |
| armored-archer-1823-1794541441 | anchapin/armored-archer | 1840 | 5 | 5 | 0 | 100.0% | 16 | 3 | 60% |
| portkit-v2-340-1794541461 | anchapin/portkit-v2 | 428 | 2 | 2 | 0 | 100.0% | 6 | 2 | 100% |
| fluxion-4793-1794541298 | anchapin/fluxion | 4802 | 3 | 2 | 1 | 66.7% | 8 | 2 | 100% |
| staging-studio-1537-1794541411 | anchapin/staging-studio | 1540 | 1 | 1 | 0 | 100.0% | 3 | 1 | 100% |
| fluxion-4794-1794538320 | anchapin/fluxion | 4799 | 6 | 1 | 5 | 16.7% | 11 | 1 | 100% |
| matchline-1383-1794537997 | anchapin/matchline | 1388 | 12 | 2 | 10 | 16.7% | 4 | 2 | 100% |
| planar-nexus-2951-1794538014 | anchapin/planar-nexus | 2954 | 6 | 3 | 3 | 50.0% | 16 | 3 | 100% |
| staging-studio-1536-1794537980 | anchapin/staging-studio | 1539 | 3 | 3 | 0 | 100.0% | 14 | 3 | 100% |
| armored-archer-1827-1794538386 | anchapin/armored-archer | 1839 | 32 | 32 | 0 | 100.0% | 33 | 8 | 25% |
| plan-coder-3-1794538032 | anchapin/plan-coder | 6 | 3 | 3 | 0 | 100.0% | 12 | 2 | 67% |
| portkit-v2-240-1794538029 | anchapin/portkit-v2 | 427 | 3 | 3 | 0 | 100.0% | 12 | 2 | 67% |
| building-ai-pocs-33-1794538025 | anchapin/building-ai-pocs | 36 | 2 | 2 | 0 | 100.0% | 3 | 2 | 100% |
| fluxion-4791-1794532182 | anchapin/fluxion | 4797 | 6 | 1 | 5 | 16.7% | 2 | 1 | 100% |
| planar-nexus-2952-1794532182 | anchapin/planar-nexus | 2953 | 1 | 1 | 0 | 100.0% | 7 | 1 | 100% |
| portkit-v2-274-1794532182 | anchapin/portkit-v2 | 426 | 3 | 3 | 0 | 100.0% | 11 | 2 | 67% |
| staging-studio-1535-1794532182 | anchapin/staging-studio | 1538 | 2 | 2 | 0 | 100.0% | 4 | 2 | 100% |
| fluxion-4790-1794532182 | anchapin/fluxion | 4796 | 4 | 4 | 0 | 100.0% | 16 | 4 | 100% |
| building-ai-pocs-32-1794532182 | anchapin/building-ai-pocs | 35 | 2 | 2 | 0 | 100.0% | 4 | 2 | 100% |
| armored-archer-1826-1794532182 | anchapin/armored-archer | 1834 | 1 | 1 | 0 | 100.0% | 8 | 1 | 100% |
| portkit-v2-302-1794530652 | anchapin/portkit-v2 | 425 | 3 | 3 | 0 | 100.0% | 18 | 3 | 100% |
| portkit-v2-298-1794529160 | anchapin/portkit-v2 | 424 | 6 | 5 | 1 | 83.3% | 19 | 5 | 100% |
| fluxion-4774-1794522000 | anchapin/fluxion | 4787 | 9 | 2 | 7 | 22.2% | 16 | 2 | 100% |
| fluxion-4776-1794521700 | anchapin/fluxion | 4788 | 2 | 1 | 1 | 50.0% | 4 | 1 | 100% |
| portkit-v2-348-1794521800 | anchapin/portkit-v2 | 423 | 2 | 2 | 0 | 100.0% | 4 | 2 | 100% |
| planar-nexus-2946e-1794519638 | anchapin/planar-nexus | 2947 | 5 | 1 | 4 | 20.0% | 4 | 1 | 100% |
| fluxion-4775c-1794516171 | anchapin/fluxion | 4785 | 15 | 1 | 14 | 6.7% | 1 | 0 | 0% |

## Table 2 -- unattributed files, hand-checked subsample (20)

One row per sampled unattributed file.  The label is the apparent
cause, checked against the quoted evidence; `unknown` is used where
the record does not support a cause.

| run_id | pr | file | change | label | evidence |
|---|---|---|---|---|---|
| plan-coder-5-1794639685 | 48 | `.gitignore` | MODIFIED | unknown | no edit/write call, no shell write command and no PR metadata explains it |
| plan-coder-22-1794638490 | 47 | `docs/spikes/headroom-phase0-runs.csv` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| building-ai-pocs-50-1794636900 | 61 | `code-mode-mcp/README.md` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| plan-coder-21-1794636400 | 46 | `tests/test_ponytail_phase0_landed.py` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| plan-coder-28-1794634175 | 44 | `docs/spikes/tokeneconomizer-phase0.md` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| building-ai-pocs-54-1794630817 | 60 | `xias-ui-gates/REPORT.md` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| plan-coder-42-1794630817 | 43 | `docs/spikes/ast-grep-matchline-phase0.md` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| portkit-v2-442-1794630817 | 443 | `tests/test_damagetypes.py` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| building-ai-pocs-57-1794627254 | 59 | `media-qa-gate/README.md` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| building-ai-pocs-56-1794626301 | 58 | `check-answerable/README.md` | ADDED | worker_shell_edit | exec step writes the file: cd ~/workspace/fixtrees/building-ai-pocs-worker-56 && python3 - <<'EOF' import re for p,old,new in [("check-answerable/README.md","## 4. Claims","## Claims"),(" |
| planar-nexus-2965-1794623737 | 2971 | `docs/TEST_VIDEO_FIXTURES.md` | MODIFIED | unknown | no edit/write call, no shell write command and no PR metadata explains it |
| plan-coder-38-1794621184 | 40 | `docs/spikes/trajectory-coupling-phase0.md` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| matchline-1396-1794618447 | 1401 | `tests/test_bem_multiring.py` | MODIFIED | unknown | no edit/write call, no shell write command and no PR metadata explains it |
| matchline-1398-1794613424 | 1400 | `.github/workflows/ci.yml` | MODIFIED | unknown | no edit/write call, no shell write command and no PR metadata explains it |
| planar-nexus-2967-1794616245 | 2969 | `src/lib/__tests__/validation-service.test.ts` | MODIFIED | unknown | no edit/write call, no shell write command and no PR metadata explains it |
| staging-studio-1547-1794613663 | 1549 | `src/lib/aesthetic-chips.ts` | DELETED | worker_shell_edit | exec step writes the file: cd ~/workspace/fixtrees/staging-studio-worker-1547 && sed -n '10,20p' src/app/api/setup/route.ts && git rm -q src/lib/cache.ts src/lib/rate-limit.ts src/lib/aes |
| retrofit-copilot-11-1794613424 | 15 | `src/results.py` | MODIFIED | unknown | no edit/write call, no shell write command and no PR metadata explains it |
| building-ai-pocs-53-1794612128 | 55 | `generative-ui-diagnostic-lens/real_runs/PROVENANCE.md` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |
| planar-nexus-2966-1794611000 | 2968 | `src/lib/game-state/__tests__/oracle-text-parser-branches.test.ts` | MODIFIED | unknown | no edit/write call, no shell write command and no PR metadata explains it |
| plan-coder-23-1794610079 | 36 | `docs/spikes/filesize-ratchet-phase0.md` | ADDED | created_outside_worker_edits | PR reports the file as added; no edit/write call and no shell write command in the transcript names it |

## Table 3 -- precision spot-check of attributed (file, step) pairs

| run_id | file | step | tool | args name the file | file is in PR |
|---|---|---|---|---|---|
| plan-coder-5-1794639685 | `README.md` | 34 | edit_file | yes | yes |
| plan-coder-22-1794638490 | `bin/headroom_phase0.py` | 15 | write_file | yes | yes |
| building-ai-pocs-50-1794636900 | `code-mode-mcp/arms/code_mode.py` | 24 | edit_file | yes | yes |
| plan-coder-21-1794636400 | `bin/ponytail_phase0.py` | 16 | write_file | yes | yes |
| plan-coder-28-1794634175 | `bin/tokeneconomizer_phase0.py` | 27 | write_file | yes | yes |
| building-ai-pocs-54-1794630817 | `xias-ui-gates/gates.spec.ts` | 26 | write_file | yes | yes |
| plan-coder-42-1794630817 | `bin/ast_grep_phase0_scan.py` | 46 | edit_file | yes | yes |
| retrofit-copilot-7-1794630817 | `src/ecms.py` | 23 | edit_file | yes | yes |
| portkit-v2-442-1794630817 | `src/portkit/agent/residue.py` | 21 | edit_file | yes | yes |
| building-ai-pocs-57-1794627254 | `media-qa-gate/REPORT.md` | 53 | edit_file | yes | yes |
| retrofit-copilot-6-1794627147 | `src/run_ecms.py` | 15 | edit_file | yes | yes |
| retrofit-copilot-5-1794626178 | `src/idf_params.py` | 12 | edit_file | yes | yes |
| building-ai-pocs-56-1794626301 | `check-answerable/frozen_questions.json` | 8 | write_file | yes | yes |
| planar-nexus-2965-1794623737 | `src/lib/game-state/__tests__/keyword-explore.test.ts` | 49 | edit_file | yes | yes |
| plan-coder-24-1794623737 | `docs/spikes/authority-drift-phase0.md` | 42 | write_file | yes | yes |
| retrofit-copilot-13-1794623737 | `README.md` | 5 | edit_file | yes | yes |
| plan-coder-38-1794621184 | `bin/trajectory_coupling_phase0.py` | 13 | write_file | yes | yes |
| portkit-v2-438-1794621064 | `src/portkit/adapters/create/armor.py` | 38 | edit_file | yes | yes |
| retrofit-copilot-10-1794621260 | `src/run_poc.py` | 6 | edit_file | yes | yes |
| armored-archer-1848-1794620942 | `data/gut-baseline.json` | 44 | edit_file | yes | yes |

## Table 4 -- reason excerpts (verbatim `assistant_text`)

| run_id | file | step | assistant_text (verbatim) |
|---|---|---|---|
| plan-coder-22-1794638490 | `bin/headroom_phase0.py` | 15 | <think>Good style reference. Now write the script. I'll write it now (first scoped edit).  Design details for the report:  Sections: 1. Title + scope statement 2. Parameters table (corpus dir, window start/end, days, as-of, chars/token, kill thresholds) 3. Corpus & window table (files total, in window, excluded, runs parsed, steps) 4. Definitions (how each field is computed) 5. Table 1 — per-run r...[truncated] |
| building-ai-pocs-50-1794636900 | `code-mode-mcp/arms/code_mode.py` | 24 | <think>I referenced `cm_arm.run_one(...)` which doesn't exist. I need to add a method to CodeModeArm that runs a single program in a fresh pool (for the aggregate variant). Let me restructure CodeModeArm:  ```python def run(self, programs: dict) -> list:     with Monty() as pool:         for task_id, program in programs.items():             self.records.append(self._run_one(pool, task_id, program)...[truncated] |
| plan-coder-21-1794636400 | `bin/ponytail_phase0.py` | 16 | <think>ruff is not installed as a python module; check `ruff` binary — `which ruff` returned nothing (the output shows nothing between --- and ls). So ruff is not available → the verification note says "if ruff is configured for this repo; say so if not". The repo has ruff.toml (configured) but ruff isn't installed. I'll try `ruff --version` and if absent, note it and self-check style manually (li...[truncated] |
| plan-coder-28-1794634175 | `bin/tokeneconomizer_phase0.py` | 27 | <think>The AGENTS.md/CONTRACT.md content is only available via transcripts for a few repos, and other repos aren't reachable in the corpus (they're not on this machine — the worktrees are ~/workspace/fixtrees/... which exists? We can't read outside worktree except transcripts. The brief says cross-check against "current briefs, prompts, repo AGENTS.md / CONTRACT.md and agent.py env facts". But we ...[truncated] |
| building-ai-pocs-54-1794630817 | `xias-ui-gates/gates.spec.ts` | 26 | <think>Now write the gate spec. Put canonical source at xias-ui-gates/gates.spec.ts and copy into clone at tests/e2e/specs/xias-ui-gates.spec.ts.  Spec design:  ```ts import { test, expect } from "@playwright/test"; import { PrismaClient } from "@prisma/client"; import { PrismaPg } from "@prisma/adapter-pg"; import path from "node:path"; import { E2E_USER_ID, E2E_REHEARSAL_PROJECT_ID, DATABASE_URL...[truncated] |
| plan-coder-42-1794630817 | `bin/ast_grep_phase0_scan.py` | 46 | <think>Note: the report prose must include C5 evidence and per-convention discussion, plus the classification vocabulary, and the "conventions" section. My generated report lacks C5 section and convention descriptions. I'll add a prose section (section 3.5?) about C5 and per-rule scoping definitions. Let me add a "Conventions and scoping" section before the raw table, and a "C5" section after.  Al...[truncated] |
| portkit-v2-442-1794630817 | `src/portkit/agent/residue.py` | 21 | Now the SKIP_KINDS entry: |
| building-ai-pocs-57-1794627254 | `media-qa-gate/REPORT.md` | 53 | <think>The heading must be exactly "## Claims". Rename "## 9. Claims" → "## Claims". Also check VERDICT_HEADING_RE — my verdict section is "## 8. Slice verdict against the gate" — does that count as a verdict heading? If the [unsupported] bullets are in the Claims section only, and the claims section heading is "## Claims" (not a verdict heading), fine. But rule 4 scans all lines for [unsupported]...[truncated] |
| retrofit-copilot-6-1794627147 | `src/run_ecms.py` | 15 | <think>Note "resolved against the EnergyPlus install's ExampleFiles/WeatherData directories using the same mechanism the constants use today (install / "ExampleFiles" / name)". So absolute paths? The spec says explicit values resolve against those dirs. Keep it simple: resolve name against dirs. But should an absolute path be allowed? The spec says "explicit values resolve against the EnergyPlus i...[truncated] |
| retrofit-copilot-5-1794626178 | `src/idf_params.py` | 12 | <think>Now the design. `ecms.py` imports `idf_params` as P. So `idf_params` cannot import `ecms` at module level (circular import). But `idf_params` needs the construction-selection approach from ecms.py's outdoor-facing opaque envelope selection.  Options: 1. Move the selection helper into `idf_params.py` and have `ecms.py` reuse it (small reuse refactor). But hard stop says: "if E1's selection a...[truncated] |

## Verdict

**Park** -- aggregate coverage 53.3% < 70%; causes explainable 13/20

* aggregate coverage 53.3% (bar: >= 70%)
* per-PR median coverage 100.0% (bar: >= 75%)
* precision 20/20 (bar: 20/20)
* unattributed causes explainable 13/20 (bar: >= 16/20)
* reason-excerpt yield 82.0%

