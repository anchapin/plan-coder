# Phase 0 TokenEconomizer measurement (issue #28)

Measurement only: no loop nudge, no fact lifecycle, no prompt or
config change, no change to the 200k/750k token caps.

## Parameters

| parameter | value |
|---|---|
| transcripts | /home/hatch/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts |
| corpus files | 790 |
| min runs for an active repo | 5 |
| min distinct tasks for a recurrence candidate | 3 |
| loop-waste kill threshold | < 5.0% of steps |
| fact kill thresholds | < 3 promotable per active repo, or >= 80.0% already covered |

## Table A -- loop waste (digest detector)

| repo | runs | runs affected | total steps | waste steps | waste % of steps | waste % of tokens-out | discounted reruns |
|---|---|---|---|---|---|---|---|
| armored-archer | 48 | 0 | 2186 | 0 | 0.00% | 0.00% | 0 |
| building-ai-pocs | 44 | 0 | 1573 | 0 | 0.00% | 0.00% | 0 |
| fluxion | 260 | 2 | 11427 | 2 | 0.02% | 0.00% | 2 |
| matchline | 108 | 1 | 4698 | 1 | 0.02% | 0.00% | 2 |
| plan-coder | 36 | 0 | 1200 | 0 | 0.00% | 0.00% | 0 |
| planar-nexus | 126 | 1 | 5774 | 1 | 0.02% | 0.01% | 0 |
| portkit-v2 | 35 | 0 | 1358 | 0 | 0.00% | 0.00% | 0 |
| retrofit-copilot | 13 | 0 | 410 | 0 | 0.00% | 0.00% | 0 |
| staging-studio | 95 | 1 | 3951 | 2 | 0.05% | 0.01% | 0 |
| unattributed | 25 | 1 | 441 | 1 | 0.23% | 0.05% | 0 |
| **TOTAL** | 790 | 6 | 33018 | 7 | 0.02% | 0.00% | 4 |

**Kill criterion A:** true loop waste = 0.02% of steps (threshold 5.0%).  Verdict: **KILL** -- kill the loop nudge.

### Sensitivity: same detector, command-only digest

| repo | total steps | waste steps (command digest) | waste % of steps |
|---|---|---|---|
| armored-archer | 2186 | 0 | 0.00% |
| building-ai-pocs | 1573 | 0 | 0.00% |
| fluxion | 11427 | 2 | 0.02% |
| matchline | 4698 | 1 | 0.02% |
| plan-coder | 1200 | 0 | 0.00% |
| planar-nexus | 5774 | 6 | 0.10% |
| portkit-v2 | 1358 | 0 | 0.00% |
| retrofit-copilot | 410 | 0 | 0.00% |
| staging-studio | 3951 | 8 | 0.20% |
| unattributed | 441 | 1 | 0.23% |
| **TOTAL** | 33018 | 18 | 0.05% |

The looser digest (same command, any failure signature) also lands at 0.05% of steps, so the KILL verdict is not an artefact of the strict signature match.

## Table B -- micro-fact recurrence

| repo | runs | active | recurrence candidates (>=3 tasks) | fact-bearing candidates | already covered | promotable |
|---|---|---|---|---|---|---|
| armored-archer | 48 | yes | 4 | 2 | 0 | 2 |
| building-ai-pocs | 44 | yes | 2 | 1 | 1 | 0 |
| fluxion | 260 | yes | 6 | 4 | 1 | 3 |
| matchline | 108 | yes | 6 | 3 | 1 | 2 |
| plan-coder | 36 | yes | 2 | 2 | 0 | 2 |
| planar-nexus | 126 | yes | 7 | 3 | 2 | 1 |
| portkit-v2 | 35 | yes | 3 | 1 | 0 | 1 |
| staging-studio | 95 | yes | 6 | 3 | 2 | 1 |
| **TOTAL** | 752 | | 36 | 19 | 7 | 12 |

### Candidates (raw)

| repo | failure signature | distinct tasks | runs | coverage | terms hit | example command |
|---|---|---|---|---|---|---|
| armored-archer | exit=<n> | 13 | 13 | no-fact-content (0.00) | 0/0 | git stash |
| armored-archer | note: If you believe this is a mistake, please contact your Python installation or OS distribution provider. You can ove | 4 | 4 | partially-covered (0.44) | 7/16 | pip3 install gdtoolkit 2>&1 | tail -3; which gdlint |
| armored-archer | TIMEOUT | 3 | 3 | no-fact-content (0.00) | 0/0 | cd backend && npm install 2>&1 | tail -30 |
| armored-archer | WARNING: Retrying (Retry(total=<n>, connect=None, read=None, redirect=None, status=None)) after connection broken by 'SS | 3 | 3 | partially-covered (0.59) | 10/17 | cd /tmp && timeout 120 pip install --quiet --break-system-packages gdtoolkit 2>& |
| building-ai-pocs | TIMEOUT | 7 | 12 | no-fact-content (0.00) | 0/0 | cd <WORKTREE> && sleep 120; cat retrofit-copilot/runs/trial.log; ls retrofit-cop |
| building-ai-pocs | /bin/sh: <n>: time: not found | 3 | 3 | covered (1.00) | 1/1 |  |
| fluxion | TIMEOUT | 50 | 63 | no-fact-content (0.00) | 0/0 | cd <WORKTREE> && export PATH="<PATH>:$PATH" && cargo build -p fluxion --lib 2>&1 |
| fluxion | exit=<n> | 35 | 37 | no-fact-content (0.00) | 0/0 | cd <WORKTREE> && ls target/ 2>/dev/null && ls target/debug/fluxion 2>/dev/null & |
| fluxion | OverflowError: timeout is too large | 7 | 9 | partially-covered (0.50) | 1/2 | cd <WORKTREE>; export PATH="<PATH>:$PATH"; cargo test --lib cli::topology 2>&1 | |
| fluxion | FileNotFoundError: [Errno <n>] No such file or directory: '<path>' | 5 | 5 | partially-covered (0.60) | 3/5 | echo hello |
| fluxion | ERROR: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...] | python -m pytest: error: unrecognized argum | 3 | 4 | partially-covered (0.38) | 6/16 | pwd && python3 -m pytest scripts/ci/test_docker_rest_defaults.py -v 2>&1 |
| fluxion | ls: cannot access 'WORKER_REPORT.md': No such file or directory | 3 | 3 | covered (0.75) | 3/4 | cd <WORKTREE> && grep -rn "pub mod sim\|pub mod physics\|pub mod analysis" src/l |
| matchline | TIMEOUT | 18 | 19 | no-fact-content (0.00) | 0/0 |  |
| matchline | exit=<n> | 14 | 17 | no-fact-content (0.00) | 0/0 | git check-ignore -v docs/plans/index.md docs/design-docs/index.md |
| matchline | /bin/sh: <n>: uv: not found | 7 | 7 | no-fact-content (0.00) | 0/0 | cd <WORKTREE> && (uv run --no-sync ruff format scripts/make_synthetic_schedule_w |
| matchline | /bin/sh: <n>: python: not found | 6 | 6 | covered (1.00) | 1/1 | cd <WORKTREE> && python -V && python -c "import pytest, shapely; print('ok')" && |
| matchline | FileNotFoundError: [Errno <n>] No such file or directory: '<path>' | 3 | 3 | partially-covered (0.60) | 3/5 | echo hello |
| matchline | virtualenv | wisard-bem | 3 | 3 | not-covered (0.00) | 0/2 | command -v uv; ls <PATH> 2>/dev/null | head; command -v ruff; pip list 2>/dev/nu |
| plan-coder | UnicodeDecodeError: 'utf-<n>' codec can't decode byte 0xe2 in position <n>: invalid continuation byte | 5 | 5 | not-covered (0.33) | 2/6 | cd <WORKTREE> && grep -rn "7-proof" prompts/backlog-supervisor.md | head -3 | cu |
| plan-coder | curl: (<n>) OpenSSL/<n>.<n>.<n>: error:0A00010B:SSL routines::wrong version number | 3 | 3 | not-covered (0.29) | 2/7 | cd <WORKTREE> && curl -sS -m 20 -o /dev/null -w "%{http_code}\n" https://api.git |
| planar-nexus | TIMEOUT | 15 | 20 | no-fact-content (0.00) | 0/0 | npm install 2>&1 | tail -20 |
| planar-nexus | exit=<n> | 11 | 15 | no-fact-content (0.00) | 0/0 | grep -rn "15108\|15101" --include="*.md" --include="*.mjs" --include="*.js" --in |
| planar-nexus | /bin/sh: <n>: Syntax error: "(" unexpected | 6 | 6 | not-covered (0.00) | 0/1 | cd <WORKTREE> && diff <(md5sum package-lock.json | cut -d' ' -f1) <(md5sum <WORK |
| planar-nexus | ls: cannot access '<path>': No such file or directory | 3 | 3 | covered (0.75) | 3/4 | df -h | head -20; echo ===; ls <WORKTREE> | head -5; ls -d <WORKTREE>/node_modul |
| planar-nexus | const err = new Error(message); | Error: Cannot find module 'zod' | 3 | 3 | covered (1.00) | 3/3 | date -Iseconds; npx --no-install tsx scripts/game-state-update-bench.ts 10 |
| planar-nexus | --- | 3 | 3 | no-fact-content (0.00) | 0/0 | grep -rn "update-bench\|updateBench" --include=* -l . --exclude-dir=node_modules |
| planar-nexus | <n> | 3 | 3 | no-fact-content (0.00) | 0/0 | curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9002/multiplayer --max |
| portkit-v2 | ModuleNotFoundError: No module named 'portkit' | 6 | 7 | not-covered (0.33) | 1/3 | cd <WORKTREE> && ls fixtures | head -40 && python3 -c "import portkit, sys; prin |
| portkit-v2 | TIMEOUT | 3 | 3 | no-fact-content (0.00) | 0/0 | cd <WORKTREE> && python3 -m pytest tests/ -q -x 2>&1 | tail -20 |
| portkit-v2 | exit=<n> | 3 | 3 | no-fact-content (0.00) | 0/0 | ls <PATH> 2>/dev/null && du -sh <PATH>* 2>/dev/null | head |
| staging-studio | exit=<n> | 12 | 12 | no-fact-content (0.00) | 0/0 | rg -n "\.room\b" src/ | grep -i vision |
| staging-studio | TIMEOUT | 10 | 10 | no-fact-content (0.00) | 0/0 | npx prisma validate 2>&1 |
| staging-studio | <n> │ import { defineConfig } from "vitest/config"; | │ ───────┬──...[truncated] | 6 | 7 | covered (0.80) | 4/5 | npx vitest run tests/lib/sliding-window-ratelimit.test.ts 2>&1 |
| staging-studio | ls: cannot access 'node_modules/.bin': No such file or directory | 5 | 5 | covered (0.75) | 3/4 | cd <WORKTREE> && ls node_modules/.bin | grep -E "vitest|tsc" && ./node_modules/. |
| staging-studio | <n> | 3 | 3 | no-fact-content (0.00) | 0/0 | grep -c "VisionLabel" node_modules/.prisma/client/default.d.ts |
| staging-studio | /bin/sh: <n>: Syntax error: "(" unexpected | 3 | 3 | not-covered (0.00) | 0/1 | cd <WORKTREE> && git stash -u >/dev/null && timeout 600 npx tsc --noEmit > /tmp/ |

**Kill criterion B:** 12 promotable facts over 8 active repos; 36.8% of the 19 fact-bearing candidates already covered.  Verdict: **KILL** -- active repos with fewer than 3 promotable facts: armored-archer, building-ai-pocs, matchline, plan-coder, planar-nexus, portkit-v2, staging-studio.

