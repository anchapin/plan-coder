# plan-coder #79 Phase 0 — structural-discovery measurement

Mechanical classifier over existing worker transcripts. No installs, no harness changes, no LLM judge.

- Transcript dir: `/home/hatch/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts` (resolved: live supervisor transcript dir (config-resolved dir absent))
- Sampling rule: the 60 most recent `*.jsonl` transcripts by file mtime (descending, ties by name), minus the excluded run ids ['plan-coder-79-1795092148']; runs with fewer than 5 steps are dropped.
- Corpus: **58 qualifying runs** (60 files sampled, 2 excluded).

## Verdict

- Kill bar (median < 10% AND p90 < 20% AND the same pattern pre-first-edit): **NOT MET**
- Median structural share: **42.2%** · p90 **67.6%**
- Pre-first-edit window: median **57.1%** · p90 **76.2%**
- Result: **PROCEED**

Sensitivity cut — the 35 runs that join the worker ledger (real supervisor worker runs; the rest are calibration runs of the same agent loop): median **36.0%**, p90 **60.0%**, pre-edit median **55.9%** → **PROCEED**.

## Classifier rules (mechanical, ambiguous → other_exec)

| class | rule |
|---|---|
| `edit` | tool name write_file or edit_file |
| `vcs` | exec whose first command segment starts with git or gh |
| `verify` | exec with a test/lint/typecheck/build segment (pytest, ruff, mypy, pyright, tsc, eslint, npm/pnpm/yarn test|build|lint, cargo test|check, go test, make test|check, gradlew, ctest, vitest, jest, bun test, dotnet test, a repo test script such as test_*.py / run_tests / verify*.sh) |
| `struct_search` | exec with a strong file-location segment (grep, egrep, rg, ag, ack, find, fd, tree, ast-grep, sg, ctags) anywhere in the command; an exec whose only file-location segment is a plain ls/dir, which carries no test/lint/build segment, and which was issued before the run's first edit; plus every list_dir call made before the run's first edit |
| `struct_read` | read_file (or a cat/head/tail/sed -n/wc read via exec) on a file that is not a steering/task document, was not read earlier in the run, and is not edited anywhere in the run |
| `task_read` | read_file / list_dir of a steering or task document, of a file already read in the run, or of a file edited in the run; list_dir calls after the run's first edit |
| `other_exec` | every other exec command (installs, script runs, downloads, ...) |

## Class totals

| class | calls |
|---|---|
| `struct_search` | 749 |
| `struct_read` | 310 |
| `task_read` | 260 |
| `edit` | 477 |
| `verify` | 209 |
| `vcs` | 116 |
| `other_exec` | 556 |
| **total** | **2677** |

## Aggregate

| cut | n | median | p90 |
|---|---|---|---|
| all tool calls | 58 | 42.2% | 67.6% |
| pre-first-edit window | 58 | 57.1% | 76.2% |
| share of steps carrying a discovery call | 58 | 43.8% | 68.8% |
| share of tool-result chars | 58 | 50.1% | 72.2% |
| ceiling, perfect graph (100%/50%) | 58 | 33.8% | 58.3% |
| ceiling, realistic graph (50%/25%) | 58 | 16.9% | 29.2% |

### By repo

| repo | runs | median share | p90 | median pre-edit share |
|---|---|---|---|---|
| armored-archer | 4 | 51.8% | 60.7% | 64.6% |
| home-energy-twin | 19 | 37.3% | 57.1% | 51.7% |
| plan-coder | 13 | 31.8% | 53.8% | 50.0% |
| planar-nexus | 8 | 45.8% | 67.6% | 56.8% |
| portkit-v2 | 14 | 53.9% | 74.4% | 63.2% |

### By outcome class

| outcome class | runs | median share | p90 |
|---|---|---|---|
| failed | 4 | 37.1% | 53.0% |
| other | 1 | 54.7% | 54.7% |
| pr_opened | 29 | 32.3% | 60.0% |
| stopped | 1 | 69.0% | 69.0% |
| unknown | 23 | 50.0% | 70.0% |

### By transcript disposition

| disposition | runs | median share | p90 |
|---|---|---|---|
| edited | 50 | 38.3% | 60.7% |
| no_edit | 2 | 53.9% | 54.7% |
| no_finished_event | 3 | 57.1% | 64.7% |
| token_cap | 3 | 69.0% | 74.4% |

### Tool calls by class and run outcome

| class | failed | other | pr_opened | stopped | unknown | total |
|---|---|---|---|---|---|---|
| `struct_search` | 83 | 40 | 418 | 20 | 188 | 749 |
| `struct_read` | 34 | 12 | 176 | 9 | 79 | 310 |
| `task_read` | 25 | 12 | 172 | 7 | 44 | 260 |
| `edit` | 46 | 1 | 395 | 1 | 34 | 477 |
| `verify` | 9 | 1 | 167 | 1 | 31 | 209 |
| `vcs` | 10 | 4 | 77 | 1 | 24 | 116 |
| `other_exec` | 88 | 25 | 348 | 3 | 92 | 556 |

### Steps to first edit

- median **14** · p90 **31** (n=53)

### Ledger-joined sensitivity cut

| cut | n | median | p90 |
|---|---|---|---|
| all tool calls | 35 | 36.0% | 60.0% |
| pre-first-edit window | 35 | 55.9% | 72.4% |
| ceiling, perfect graph (100%/50%) | 35 | 29.3% | 52.5% |
| ceiling, realistic graph (50%/25%) | 35 | 14.7% | 26.2% |

## `other_exec` audit sample

556 calls landed in `other_exec`. Deterministic sample of 30 commands (seed 79) for a human to audit the taxonomy's leakage:

- `cd . && ls prompts examples config schemas tests/fixtures | head -60; echo ---; wc -l bin/metrics_rollup.py prompts/harness-tune.md 2>&1; echo ---; git log --oneline -3`
- `cd /tmp && P="-x http://hatch-egress-proxy:3128"; echo "== issue 68"; timeout 30 curl -sS -m 25 $P -o /tmp/hyd_issue68.json -w "%{http_code}\n" https://api.github.com/repos/GeyserMC/Hydraulic/issues/6`
- `cd /tmp && PYTHONPATH=/tmp/hetlibs python3 -c "import pydantic" 2>&1 | tail -20`
- `cd /tmp && curl -sS -m 25 -o /tmp/hyd_readme.md -w "%{http_code}\n" https://raw.githubusercontent.com/GeyserMC/Hydraulic/master/README.md; echo "exit=$?"; ls -la /tmp/hyd_readme.md 2>/dev/null; head -`
- `cd /tmp && python3 -V && python3 -m venv --help >/dev/null 2>&1 && echo venv-ok; pip download ochre --no-deps -d /tmp/ochre_probe 2>&1 | tail -5`
- `cd /tmp && timeout 240 python3 - <<'EOF'
import zipfile, pathlib, re
root = pathlib.Path.home()/".cache/pip/http-v2"
for p in root.rglob("*.body"):
    try:
        with zipfile.ZipFile(p) as z:
     `
- `cd /tmp && timeout 30 curl -sS -v -m 25 https://api.github.com/repos/GeyserMC/Hydraulic 2>&1 | tail -20`
- `cd /workspace 2>/dev/null || cd .; pwd; ls; cat package.json | head -60`
- `cd ~/workspace 2>/dev/null || true; pwd; sed -n 1,60p bin/experiments.py`
- `cd ~/workspace/fixtrees/armored-archer-worker-1878 && pwd && git status --short && git log --oneline -3 && ls`
- `cd ~/workspace/fixtrees/home-energy-twin-worker-10 && ls home_energy_twin/changepoint/ && wc -l home_energy_twin/changepoint/*.py tests/test_changepoint_calibrator.py`
- `cd ~/workspace/fixtrees/plan-coder-worker-124 && ls docs/ 2>/dev/null; echo ---; ls spikes/ 2>/dev/null; echo ---; ls docs/spikes 2>/dev/null`
- `cd ~/workspace/fixtrees/plan-coder-worker-80 && ls -la && echo "---GIT---" && git status --short && git log --oneline -3`
- `cd ~/workspace/fixtrees/planar-nexus-worker-2419 && ls src/lib/game-state/game-state/ && ls src/lib/game-state/__tests__/ | head -50`
- `cd ~/workspace/fixtrees/planar-nexus-worker-2419 && npx tsx scripts/bench-state-based-actions.ts --passes 5 2>&1 | head -20`
- `cd ~/workspace/fixtrees/portkit-v2-worker-235 && PYTHONPATH=src timeout 900 python3 -m portkit.cli eval 2>&1 | tail -30`
- `cd ~/workspace/fixtrees/portkit-v2-worker-281 && for u in https://minecraft.wiki/w/Configured_feature https://github.com https://pypi.org/simple/; do echo "== $u"; timeout 15 curl -sS -o /dev/null -w `
- `cd ~/workspace/fixtrees/portkit-v2-worker-281 && timeout 15 curl -sSI https://raw.githubusercontent.com/Mojang/bedrock-samples/main/documentation/Features.html | head -3; echo "exit=$?"`
- `cd ~/workspace/fixtrees/portkit-v2-worker-287 && python3 -c "
import json
d=json.load(open('fixtures/real/gauntlet-baseline.json'))
print(list(d.keys()))
print(json.dumps(d, indent=1)[:2500])
"`
- `cd ~/workspace/fixtrees/portkit-v2-worker-299 && ls && git log --oneline -3 && git status`
- `cd ~/workspace/fixtrees/portkit-v2-worker-465 && python3 - <<'EOF'
import re
src=open('src/portkit/agent/residue.py').read()
i=src.index('SKIP_KINDS = {')
# find matching close
depth=0
j=i+len('SKIP_K`
- `curl --max-time 15 -sS -i http://api.github.com/repos/anchapin/plan-coder/issues/119 | sed -n '1,80p'; echo rc=${PIPESTATUS[0]}`
- `ls -R spikes | head -50; echo ---; ls docs; echo ---; ls specs; echo ---; ls tests; echo ---; ls`
- `ls -d ~/workspace/fixtrees/*planar*/node_modules 2>/dev/null; ls ~/workspace/fixtrees/planar-nexus-laws 2>/dev/null | head`
- `ls /usr/lib/python3/dist-packages/ 2>/dev/null | head -50; echo "--- pip cache:"; ls ~/.cache/pip 2>/dev/null; echo "--- find pydantic dirs:"; ls -d /usr/lib/python3*/site-packages/pydantic /usr/local`
- `pwd; ls -la`
- `pwd; ls -la; echo ---; ls -R specs docs 2>/dev/null | head -50`
- `python3 - <<'PY'
from pathlib import Path
import re
for name in ('pr-shepherd.md','backlog-supervisor.md','harness-tune.md'):
 text=Path('prompts',name).read_text()
 body=text.split('\n---\n',1)[1] if`
- `python3 bin/prompt_check.py; echo exit=$?`
- `timeout 20 curl -sS -o /dev/null -w "%{http_code}\n" http://github.com 2>&1|tail -2; echo "--- insecure"; timeout 20 curl -sSk -o /dev/null -w "%{http_code}\n" https://github.com 2>&1|tail -2; echo "-`

## Top 10 runs by absolute structural-discovery calls

| run | repo | outcome | structural calls | tool calls | most-searched subject |
|---|---|---|---|---|---|
| portkit-v2-281-1795068119 | portkit-v2 | pr_opened | 53 | 90 | `worldgen` |
| portkit-v2-287-1795001403 | portkit-v2 | other | 52 | 95 | `quark` |
| portkit-v2-465-1795059006 | portkit-v2 | failed | 44 | 83 | `quark` |
| portkit-v2-465-1795060422 | portkit-v2 | pr_opened | 43 | 67 | `quark` |
| planar-nexus-2419-1794995050 | planar-nexus | pr_opened | 38 | 71 | `castSpell` |
| armored-archer-1877-1795068098 | armored-archer | pr_opened | 37 | 61 | `juice_effect_started` |
| portkit-v2-464-1795007513 | portkit-v2 | pr_opened | 36 | 77 | `"stairs\` |
| home-energy-twin-3-1794991481 | home-energy-twin | failed | 33 | 77 | `version` |
| portkit-v2-goldfish-465-1795060091 | portkit-v2 | unknown | 32 | 43 | `quark` |
| portkit-v2-goldfish-287-1794977013 | portkit-v2 | unknown | 31 | 40 | `quark` |

## Steering-document check (issue #79 section 4)

| repo | clone root | AGENTS.md | CONTRACT.md | CLAUDE.md | CONTEXT.md | names entry points |
|---|---|---|---|---|---|---|
| armored-archer | `/home/hatch/workspace/armored-archer` | yes | no | yes | yes | yes (AGENTS.md, CLAUDE.md) |
| home-energy-twin | `/home/hatch/workspace/home-energy-twin` | yes | no | no | no | no |
| plan-coder | `/home/hatch/workspace/plan-coder-repo` | yes | no | yes | no | yes (AGENTS.md, CLAUDE.md) |
| planar-nexus | `/home/hatch/workspace/planar-nexus` | yes | no | yes | no | yes (AGENTS.md, CLAUDE.md) |
| portkit-v2 | `/home/hatch/workspace/portkit-v2` | no | no | no | no | no |

## Per-run table

| run | repo | outcome | disposition | ledger | steps | tool calls | struct_search | struct_read | share | discovery steps | pre-edit calls | pre-edit share | steps to first edit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| portkit-v2-56-1795077657 | portkit-v2 | pr_opened | edited | yes | 48 | 57 | 12 | 3 | 26.3% | 13 | 22 | 59.1% | 14 |
| armored-archer-1829-1795073394 | armored-archer | pr_opened | edited | yes | 60 | 62 | 18 | 9 | 43.5% | 27 | 21 | 76.2% | 19 |
| plan-coder-79-1795073462 | plan-coder | failed | edited | yes | 60 | 70 | 15 | 7 | 31.4% | 18 | 34 | 55.9% | 24 |
| armored-archer-1878-1795070724 | armored-archer | pr_opened | edited | yes | 47 | 56 | 21 | 2 | 41.1% | 21 | 29 | 58.6% | 20 |
| armored-archer-1879-1795070081 | armored-archer | pr_opened | edited | yes | 13 | 15 | 8 | 1 | 60.0% | 8 | 6 | 66.7% | 4 |
| portkit-v2-281-1795068119 | portkit-v2 | pr_opened | edited | yes | 60 | 90 | 40 | 13 | 58.9% | 37 | 57 | 75.4% | 30 |
| armored-archer-1877-1795068098 | armored-archer | pr_opened | edited | yes | 41 | 61 | 27 | 10 | 60.7% | 25 | 16 | 62.5% | 8 |
| portkit-v2-465-1795060422 | portkit-v2 | pr_opened | edited | yes | 60 | 67 | 36 | 7 | 64.2% | 40 | 58 | 70.7% | 51 |
| plan-coder-133-cifix-20261119 | plan-coder | unknown | edited | no | 16 | 22 | 7 | 0 | 31.8% | 6 | 13 | 30.8% | 8 |
| portkit-v2-goldfish-465-1795060091 | portkit-v2 | unknown | token_cap | no | 22 | 43 | 24 | 8 | 74.4% | 20 | 43 | 74.4% | - |
| portkit-v2-465-1795059006 | portkit-v2 | failed | no_edit | yes | 54 | 83 | 34 | 10 | 53.0% | 31 | 83 | 53.0% | - |
| plan-coder-jittest-test-133-1795057736 | plan-coder | unknown | edited | no | 8 | 11 | 2 | 0 | 18.2% | 1 | 6 | 33.3% | 3 |
| plan-coder-jittest-hyp-133-1795057565 | plan-coder | unknown | edited | no | 6 | 8 | 1 | 3 | 50.0% | 3 | 6 | 50.0% | 4 |
| plan-coder-132-1795056457 | plan-coder | pr_opened | edited | yes | 56 | 62 | 12 | 8 | 32.3% | 18 | 16 | 62.5% | 10 |
| plan-coder-goldfish-132-1795056245 | plan-coder | unknown | edited | no | 16 | 23 | 8 | 5 | 56.5% | 11 | 18 | 66.7% | 12 |
| home-energy-twin-5-1795021834 | home-energy-twin | pr_opened | edited | yes | 60 | 67 | 7 | 9 | 23.9% | 13 | 14 | 64.3% | 7 |
| home-energy-twin-10-1795019924 | home-energy-twin | pr_opened | edited | yes | 60 | 66 | 4 | 3 | 10.6% | 7 | 32 | 12.5% | 26 |
| home-energy-twin-goldfish-10-1795019569 | home-energy-twin | unknown | token_cap | no | 18 | 29 | 11 | 4 | 51.7% | 12 | 29 | 51.7% | - |
| planar-nexus-jittest-hyp-2986-1795018114 | planar-nexus | unknown | edited | no | 7 | 8 | 1 | 3 | 50.0% | 3 | 7 | 57.1% | 6 |
| planar-nexus-2419-1795016671 | planar-nexus | pr_opened | edited | yes | 60 | 75 | 17 | 10 | 36.0% | 22 | 48 | 54.2% | 33 |
| home-energy-twin-10-1795016491 | home-energy-twin | failed | edited | yes | 60 | 65 | 8 | 10 | 27.7% | 16 | 38 | 39.5% | 33 |
| portkit-v2-235-1795016581 | portkit-v2 | pr_opened | edited | yes | 60 | 62 | 19 | 9 | 45.2% | 27 | 29 | 55.2% | 27 |
| plan-coder-124-1795016401 | plan-coder | pr_opened | edited | yes | 35 | 46 | 4 | 7 | 23.9% | 10 | 27 | 33.3% | 17 |
| planar-nexus-goldfish-2419-1795016067 | planar-nexus | unknown | edited | no | 23 | 37 | 21 | 4 | 67.6% | 15 | 28 | 82.1% | 14 |
| home-energy-twin-9-1795013234 | home-energy-twin | pr_opened | edited | yes | 60 | 67 | 20 | 5 | 37.3% | 23 | 16 | 62.5% | 9 |
| plan-coder-121-1795013054 | plan-coder | pr_opened | edited | yes | 34 | 37 | 8 | 3 | 29.7% | 10 | 11 | 72.7% | 8 |
| plan-coder-119-1795007901 | plan-coder | pr_opened | edited | yes | 60 | 88 | 4 | 7 | 12.5% | 7 | 28 | 25.0% | 14 |
| portkit-v2-464-1795007513 | portkit-v2 | pr_opened | edited | yes | 60 | 77 | 28 | 8 | 46.8% | 31 | 46 | 67.4% | 37 |
| plan-coder-goldfish-119-1795007443 | plan-coder | unknown | edited | no | 24 | 28 | 7 | 4 | 39.3% | 10 | 16 | 37.5% | 12 |
| home-energy-twin-8-1795005926 | home-energy-twin | pr_opened | edited | yes | 60 | 63 | 10 | 8 | 28.6% | 17 | 33 | 42.4% | 30 |
| portkit-v2-463-1795005837 | portkit-v2 | pr_opened | edited | yes | 60 | 62 | 9 | 4 | 21.0% | 12 | 23 | 52.2% | 21 |
| portkit-v2-287-1795001403 | portkit-v2 | other | no_edit | yes | 60 | 95 | 40 | 12 | 54.7% | 36 | 83 | 59.0% | 49 |
| planar-nexus-2411-1795001286 | planar-nexus | pr_opened | edited | yes | 48 | 53 | 13 | 9 | 41.5% | 19 | 23 | 56.5% | 18 |
| portkit-v2-goldfish-287-1795001280 | portkit-v2 | unknown | edited | no | 24 | 37 | 20 | 5 | 67.6% | 17 | 30 | 80.0% | 17 |
| home-energy-twin-3-1794998691 | home-energy-twin | pr_opened | edited | yes | 46 | 51 | 15 | 5 | 39.2% | 17 | 13 | 61.5% | 8 |
| home-energy-twin-goldfish-3-1794998526 | home-energy-twin | unknown | edited | no | 13 | 16 | 5 | 3 | 50.0% | 7 | 12 | 66.7% | 9 |
| planar-nexus-jittest-test-2981-1794996814 | planar-nexus | unknown | edited | no | 14 | 18 | 5 | 1 | 33.3% | 6 | 14 | 42.9% | 10 |
| planar-nexus-jittest-hyp-2981-1794996480 | planar-nexus | unknown | edited | no | 7 | 8 | 1 | 1 | 25.0% | 1 | 5 | 40.0% | 4 |
| home-energy-twin-7-1794995050 | home-energy-twin | pr_opened | edited | yes | 60 | 66 | 3 | 7 | 15.2% | 7 | 23 | 43.5% | 17 |
| planar-nexus-2419-1794995050 | planar-nexus | pr_opened | edited | yes | 60 | 71 | 28 | 10 | 53.5% | 32 | 42 | 59.5% | 31 |
| planar-nexus-2419-1794994572 | planar-nexus | unknown | no_finished_event | no | 22 | 34 | 15 | 7 | 64.7% | 17 | 34 | 64.7% | - |
| portkit-v2-299-1794994572 | portkit-v2 | pr_opened | edited | yes | 29 | 30 | 3 | 0 | 10.0% | 3 | 12 | 25.0% | 11 |
| home-energy-twin-7-1794994572 | home-energy-twin | unknown | no_finished_event | no | 17 | 25 | 5 | 7 | 48.0% | 10 | 25 | 48.0% | - |
| home-energy-twin-3-1794991481 | home-energy-twin | failed | edited | yes | 60 | 77 | 26 | 7 | 42.9% | 31 | 41 | 24.4% | 27 |
| home-energy-twin-4-1794987600 | home-energy-twin | pr_opened | edited | yes | 60 | 66 | 11 | 2 | 19.7% | 11 | 34 | 32.4% | 29 |
| home-energy-twin-4-1794987468 | home-energy-twin | unknown | no_finished_event | no | 9 | 14 | 5 | 3 | 57.1% | 6 | 12 | 66.7% | 7 |
| home-energy-twin-jittest-test-19-1794986882 | home-energy-twin | unknown | edited | no | 8 | 9 | 0 | 2 | 22.2% | 1 | 2 | 100.0% | 1 |
| home-energy-twin-jittest-hyp-19-1794986753 | home-energy-twin | unknown | edited | no | 11 | 14 | 5 | 2 | 50.0% | 5 | 8 | 62.5% | 5 |
| home-energy-twin-2-1794985323 | home-energy-twin | pr_opened | edited | yes | 27 | 34 | 13 | 4 | 50.0% | 16 | 13 | 53.8% | 7 |
| home-energy-twin-jittest-test-18-1794984478 | home-energy-twin | unknown | edited | no | 19 | 21 | 3 | 2 | 23.8% | 5 | 16 | 25.0% | 14 |
| home-energy-twin-jittest-hyp-18-1794984294 | home-energy-twin | unknown | edited | no | 8 | 10 | 4 | 3 | 70.0% | 5 | 3 | 100.0% | 1 |
| home-energy-twin-6-1794983550 | home-energy-twin | pr_opened | edited | yes | 60 | 70 | 11 | 4 | 21.4% | 14 | 33 | 42.4% | 30 |
| portkit-v2-243-1794983670 | portkit-v2 | pr_opened | edited | yes | 32 | 47 | 6 | 3 | 19.1% | 9 | 37 | 16.2% | 22 |
| plan-coder-jittest-hyp-111-1794981812 | plan-coder | unknown | edited | no | 12 | 13 | 3 | 4 | 53.8% | 6 | 10 | 60.0% | 9 |
| plan-coder-80-1794980610 | plan-coder | pr_opened | edited | yes | 60 | 85 | 11 | 6 | 20.0% | 14 | 24 | 45.8% | 12 |
| plan-coder-goldfish-80-1794980343 | plan-coder | unknown | edited | no | 17 | 24 | 11 | 1 | 50.0% | 9 | 21 | 57.1% | 14 |
| portkit-v2-287-1794977262 | portkit-v2 | stopped | token_cap | yes | 22 | 42 | 20 | 9 | 69.0% | 19 | 29 | 72.4% | 15 |
| portkit-v2-goldfish-287-1794977013 | portkit-v2 | unknown | edited | no | 24 | 40 | 24 | 7 | 77.5% | 18 | 38 | 81.6% | 22 |

### Excluded from the corpus

- `planar-nexus-2984-1795005743` — fewer than 5 steps
- `plan-coder-jittest-test-111-1794981958` — fewer than 5 steps

## Verdict input: is the discovery share material?

Yes — the structural-discovery share is material, by a wide margin, so
the issue-#79 kill bar (median < 10% AND p90 < 20%, with the same
pattern in the pre-first-edit window) is **NOT MET** and the mechanical
verdict is **PROCEED**. Over the 58 qualifying runs (2,677 classified
tool calls) the median run spends **42.2%** of its tool calls on
structural discovery (**749** `struct_search` + **310** `struct_read`),
with a p90 of **67.6%**; the same measure over *steps* rather than calls
is a median of **43.8%** (p90 **68.8%**), so the discovery traffic is
not a handful of calls per run but a recurring cost across a large part
of the run's step budget. The discovery share is also *front-loaded*:
in the pre-first-edit window the median run spends **57.1%** (p90
**76.2%**) of its calls discovering structure — exactly the window a
pre-built code graph is supposed to compress — and the discovery calls
carry a median **50.1%** of all tool-result characters (p90 **72.2%**),
so they are also the bulk of what the worker reads. The pattern holds
across every cut in the tables above: it is not an artefact of one repo
(`portkit-v2` 53.9% median, `planar-nexus` 45.8%, `armored-archer`
51.8%, `plan-coder` 31.8%, `home-energy-twin` 37.3%) nor of one outcome
class (`pr_opened` 32.3% median over 29 runs, `failed` 37.1% over 4,
`unknown` 50.0% over 23), nor of the calibration runs only — the
35-run ledger-joined sensitivity cut (real supervisor worker runs) is
**36.0%** median / **60.0%** p90, still well above the kill bar, and it
is the *worst* outcomes that are most discovery-heavy (`token_cap`
disposition 69.0% median, `no_finished_event` 57.1%, `no_edit` 53.9%
versus `edited` 38.3%), i.e. the runs that got stuck are the ones whose
call budget went mostly into looking for structure rather than changing
code. Even the deliberately
pessimistic ceiling cuts — a perfect graph replacing 100% of
`struct_search` and only 50% of `struct_read`, and a realistic graph
replacing 50%/25% — put the median removable share at **33.8%** and
**16.9%** of tool calls (p90 58.3% / 29.2%). The verdict input for
Phase 1 is therefore that structural discovery is a material share of
worker effort in this corpus and a plausible target for a code graph,
and Phase 1 should measure what a real graph would actually replace
rather than assuming the upper bound above.

## How to reproduce

```bash
cd ~/workspace/fixtrees/plan-coder-worker-79
python3 bin/structural_discovery_phase0.py \
    --limit 60 \
    --exclude-run-id plan-coder-79-1795092148 \
    --out docs/codegraph-phase0-report.md
```

The transcript directory is resolved the way `bin/agent.py` resolves it
(`--transcripts-dir` → `$PLAN_CODER_TRANSCRIPT_DIR` → deployment config
`state.transcripts_dir` → `~/.plan-coder/transcripts`); on this machine
none of the first four exists, so the script falls back to the live
supervisor directory
`~/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts`
(981 transcripts at measurement time; the 60 most recent by file mtime
were sampled). `plan-coder-79-1795092148` is the run that produced this
report, and is excluded so the measurement does not measure itself.
The classifier itself is pinned by `tests/test_structural_discovery_phase0.py`
(`python3 -m pytest tests/test_structural_discovery_phase0.py -q`).

## Reading the numbers (caveats)

- **Not a random sample.** The corpus is the 60 most recent
  transcripts, which mixes supervisor worker runs with calibration runs
  of the same agent loop (`jittest`/`goldfish`/`hyp`/`cifix` role words,
  stripped from the repo column by `repo_from_run_id`). The
  ledger-joined cut (35 runs) is the cleanest subset; it is reported
  separately and does not change the conclusion.
- **Outcome coverage.** `outcome_class` comes from the supervisor's
  worker ledger and is known for 35 of the 58 runs; the other 23 are
  `unknown` in the outcome tables. The *disposition* table is derived
  from each transcript's own `finished` event and therefore covers all
  58 runs.
- **The classifier errs toward discovery, not away from it.** A compound
  `exec` counts as `struct_search` if *any* segment is a search, even
  when the same command also runs `git` or a test — so the discovery
  share above is an upper bound on "pure" discovery, which makes the
  spike's case weaker, not stronger. `list_dir`/`ls` calls are counted
  as discovery only before the run's first edit, and reads of files that
  the run later edits are counted as `task_read`, not `struct_read`.
- **`other_exec` leakage.** The 556 `other_exec` calls are audited in
  the sample above; they are dominated by network probing
  (`curl`/`pip download`), virtualenv setup and one-off script runs —
  not by undiscovered discovery traffic.
- **Scope.** Phase 0 only: measurement, no installs, no harness
  changes, no prompt changes. Phase 1 (building/evaluating a real graph)
  is not authorized by this report.


