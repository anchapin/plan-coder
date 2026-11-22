# PyPI `.pth` Supply-Chain Audit — plan-coder (issue #153)

- **Scope:** this repository only (`plan-coder-repo-worker-153`, branch `worker/153-pth-audit`,
  base `origin/main` @ `8e82bdd26f1e71a6cc5ef63b16f67badbec4aba0`). The cross-repo sweep is the
  operator's job; worker isolation forbids reading other repositories' trees.
- **Mode:** READ-ONLY audit. No dependency, workflow, cache, `bin/`, `prompts/`, `config/`, or
  `tests/` change was made. The only file created is this report.
- **Hard-stop check (escalation):** NOT triggered. Neither `agent-framework` / `agent_framework`
  (nor versions `1.26.1` / `1.26.2`) nor any unexplained `.pth` hook exists in this repository.
  Per the brief, the report therefore continues with the per-area verdicts below instead of
  stopping at the top.

## Summary of verdicts

| # | Area | Verdict |
|---|------|---------|
| 1 | Manifest / lockfile check for `agent-framework` / `agent_framework` and `1.26.1` / `1.26.2` | **CLEAN** |
| 2 | `.pth` files in the repo tree and committed fixtures | **CLEAN** |
| 3 | CI workflow: unpinned Python dependency resolution / cache-restored environments | **FINDING (low severity, report-only)** |

---

## Area 1 — Manifest and lockfile check

**Verdict: CLEAN.**

This repository ships **no Python dependency manifest and no lockfile at all** (no
`pyproject.toml`, no `requirements*.txt`, no `poetry.lock`, no `Pipfile*`, no `setup.py` /
`setup.cfg`, no `*.lock`). Consequently there is no place where `agent-framework` /
`agent_framework` could be declared, and no lockfile that could pin or fail to pin versions
`1.26.1` / `1.26.2`. Every textual occurrence of the package name was searched for across the
whole tree (tracked files, untracked files, all file types including binaries) and in the full
git history: **zero hits**.

The repo is intentionally stdlib-only at runtime (see `README.md` line 52: *"Prerequisites —
`git`, Python 3.9+ (stdlib only; nothing to `pip install`)"*). The only third-party imports
found are confined to a spike fixture/tooling and are not part of the shipped package:

```
$ grep -rhoE '^\s*(import|from)\s+[A-Za-z0-9_\.]+' --include='*.py' . | sed -E 's/^\s*//; s/(import|from)\s+//' | cut -d. -f1 | sort -u
... (stdlib names: argparse, ast, csv, dataclasses, hashlib, json, pathlib, re, sqlite3, subprocess, ...)

$ grep -rln --include='*.py' -E '^\s*(import|from)\s+(lxml|yaml)' .
./spikes/astgrep-matchline/fixtures/clean/c3_xml_entity_expansion.py
./spikes/astgrep-matchline/fixtures/violation/v3_xml_entity_expansion.py
./spikes/astgrep-matchline/run_scan.py
```

### Commands run (Area 1)

```
$ find . -path ./.git -prune -o -type f \( -name 'pyproject.toml' -o -name 'requirements*.txt' \
      -o -name '*.lock' -o -name 'Pipfile*' -o -name 'poetry.lock' -o -name 'setup.py' \
      -o -name 'setup.cfg' -o -name '*.pth' \) -print
(no output)

$ git ls-files | grep -Ei 'pyproject|requirements|\.lock$|Pipfile|setup\.(py|cfg)'
(no output)

$ grep -rIn --exclude-dir=.git -i -E 'agent[-_]framework' . | wc -l
0

$ grep -ria --exclude-dir=.git -E 'agent[-_]framework' . | wc -l
0

$ grep -rIn --exclude-dir=.git -E '1\.26\.[12]' . | head -20
(no output)

$ git log --all -S'agent-framework' --oneline
(no output)

$ git log --all -S'agent_framework' --oneline
(no output)
```

Interpretation: absence of a manifest is itself the relevant result — there is no dependency
surface in this repo through which the `agent-framework` PyPI release (1.26.1 / 1.26.2) could
enter a build or install of plan-coder.

---

## Area 2 — `.pth` files in the tree and committed fixtures

**Verdict: CLEAN.**

A `.pth` file is an executable-on-import hook: `site` executes any line in it starting with
`import` when the interpreter starts. Any such file committed into this repo — or into a
committed fixture that a test copies into a virtualenv — would be a code-execution vector. There
are **none**: no `.pth` file exists anywhere in the working tree (including untracked and hidden
paths), none was ever committed in any branch of the git history, and the committed test fixtures
contain only JSON / JSONL / CSV / Markdown data files.

```
$ find . -path ./.git -prune -o -name '*.pth' -print
(no output)

$ git ls-files '*.pth'
(no output)

$ git log --all --diff-filter=A --name-only --pretty=format: | sort -u | grep -i '\.pth$'
(no output — no .pth file has ever been added in this repository's history)

$ grep -rIn --exclude-dir=.git -E '\.pth' .
(no output — the string ".pth" does not appear anywhere in the tree)

$ find tests/fixtures -type f
tests/fixtures/dashboard_live/shepherd.json
tests/fixtures/dashboard_live/workers.json
tests/fixtures/dashboard_state/shepherd.json
tests/fixtures/dashboard_state/shepherd_ledger.jsonl
tests/fixtures/dashboard_state/worker_ledger.jsonl
tests/fixtures/dashboard_state/workers.json
tests/fixtures/dashboard_v2_state/experiments.jsonl
tests/fixtures/dashboard_v2_state/metrics_rollup.jsonl
tests/fixtures/dashboard_v2_state/shepherd.json
tests/fixtures/dashboard_v2_state/shepherd_ledger.jsonl
tests/fixtures/dashboard_v2_state/worker_ledger.jsonl
tests/fixtures/dashboard_v2_state/workers.json
tests/fixtures/metrics_rollup_state/shepherd_ledger.jsonl
tests/fixtures/metrics_rollup_state/worker_ledger.jsonl
tests/fixtures/prompt_specs/backlog-supervisor.json
tests/fixtures/prompt_specs/harness-tune.json
tests/fixtures/prompt_specs/pr-shepherd-regression.json
tests/fixtures/prompt_specs/pr-shepherd.json
tests/fixtures/prompts/pr-shepherd-known-failure-clause-removed.md
tests/fixtures/verification_phase0_part_c_core.csv
tests/fixtures/verification_phase0_pr_files.json
tests/fixtures/verification_phase0_runs.csv

$ git status --porcelain
(no output — no untracked files either)
```

Supplementary (out-of-repo, environment of the audit runner only, informational — the active
interpreter has no `agent_framework` installed and the only `.pth` files in site-packages are
stock distribution/editable-install ones, none related to `agent-framework`):

```
$ python3 -m pip list | grep -i -E 'agent|framework'
(nothing matching agent/framework)

$ python3 -c "<enumerate site-packages>/*.pth"
/usr/lib/python3/dist-packages/distutils-precedence.pth      (stock setuptools shim)
/usr/lib/python3/dist-packages/matplotlib-3.6.3-nspkg.pth     (stock matplotlib namespace shim)
/home/hatch/.local/lib/python3.12/site-packages/__editable__.wisard_bem-0.1.0.pth  (editable install of another project)
```

---

## Area 3 — CI workflow: unpinned resolution and cache-restored environments

**Verdict: FINDING (low severity, report-only — no workflow edits made, per the brief).**

Only one workflow file exists (`.github/workflows/ci.yml`); there are no other CI definitions
(no Travis/Circle/GitLab/Jenkins/Makefile/tox/nox/pre-commit files).

Positive (cache side): **no cache restore path exists in this workflow.** There is no
`actions/cache` step, and `actions/setup-python@v5` is used *without* the `cache:` input, so no
pip wheel cache or restored virtualenv can carry a package across runs. The runner therefore
always resolves its tooling from the network on a fresh environment.

Negative (pinning side): two things are resolved without a pin.

```
$ grep -n -E 'cache|uses:|pip install|python-version' .github/workflows/ci.yml
28:        uses: actions/checkout@v4
31:        uses: actions/setup-python@v5
33:          python-version: "3.12"
39:          python3 -m pip install --upgrade pip
40:          python3 -m pip install "pytest==9.1.1" "ruff==0.17.5"

$ git ls-files .github
.github/workflows/ci.yml
```

Findings, in the order of how a compromised package could actually be introduced:

1. **Unpinned `pip` self-upgrade — `python3 -m pip install --upgrade pip` (line 39).** This
   resolves the `pip` distribution from PyPI with *no* version constraint, so CI installs
   whatever `pip` release is current at run time. `pip` is a code-execution package that runs
   setup/build hooks during installation; an unpinned "latest" resolution is the one place in
   this workflow where the installed artifact is not determined by a committed constraint.
   Recommendation (not applied — out of allowed paths): pin it, e.g.
   `python3 -m pip install "pip==<known-good>"`, or drop the step entirely.
2. **Floating interpreter patch level — `python-version: "3.12"` (line 33).** Resolves to the
   newest 3.12.x published by `actions/setup-python`, i.e. the interpreter build is not pinned.
   This is a toolchain pin, not a package pin, but it means the CI environment is not
   byte-reproducible across runs.
3. **Actions referenced by mutable tag, not by commit SHA — `actions/checkout@v4`,
   `actions/setup-python@v5` (lines 28, 31).** A tag can be re-pointed by the upstream owner;
   SHA pinning (`@<40-hex-sha>`) would remove that supply-chain surface. Same class of issue as
   (1), one step further from the repo's control.
4. **Good:** the actual test/lint tooling *is* pinned to exact versions —
   `python3 -m pip install "pytest==9.1.1" "ruff==0.17.5"` (line 40) — so no transitive
   float is introduced there for the top-level packages, and `pip`'s own resolver picks their
   transitive dependencies unpinned (there is no lockfile and no `--require-hashes`), which is
   the standard residual risk for a repo with no manifest.

Two non-CI, documentation-only pip invocations were also noticed and are **not** CI resolution
paths: `bin/ast_grep_phase0_scan.py` (lines 488, 493, 775) and `docs/spikes/ast-grep-matchline-phase0.md`
describe an operator-created scratch venv with `pip install ast-grep-cli` (unpinned) for a
one-off scan; `docs/spikes/cognee-ingestion-phase0.md` and `docs/spikes/headroom-phase1.md` do
the same for `cognee` / `headroom`. These are prose/spike artifacts executed by a human, not by
CI, and they never run in the workflow.

---

## Constraints honoured

- No dependency changes, no workflow changes, no cache purges.
- No edits to `bin/`, `prompts/`, `config/` (including `config/deployment.local.json`, which
  does not exist in this clone — only `config/deployment.example.json` is tracked), or `tests/`.
- `.github/workflows/` was read only; it is unmodified.
- Only `docs/pth-audit-153.md` was created.

## Verification

Documentation-only change; the repo test suite was run anyway to confirm nothing regressed:

```
$ python3 -m pytest tests/ -q
...ss................................................................... [ 11%]
...
648 passed, 2 skipped, 62 subtests passed in 52.15s
```

`ruff check .` was not re-run because no Python source was touched (no lint surface changed).
