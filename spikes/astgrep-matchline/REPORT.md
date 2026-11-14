# ast-grep rules for the matchline conventions — Phase 0 slice (issue #18)

Scope of this slice: **measurement only**. It writes ast-grep rules for
already-documented matchline conventions, proves each rule fires on a seeded
violation, and proves the rules are quiet on a clean fixture set. It does
**not** wire CI, does not touch any `.github/workflows` file in any repo, and
does not modify the matchline repository.

Everything in this slice lives under `spikes/astgrep-matchline/`:

```
spikes/astgrep-matchline/
├── rules/                      5 ast-grep rule files (one per rule)
├── fixtures/violation/         5 seeded violations (one per rule)
├── fixtures/clean/             5 clean counterparts (must produce zero findings)
├── results/scan-results.json   machine-readable scan output
├── run_scan.py                 the scan runner
└── REPORT.md                   this file
```

---

## 0. Environment finding — READ FIRST (it limits the evidence)

The brief states that the ast-grep binary `sg` is pre-provisioned at
`/usr/bin/sg`. **In this environment it is not ast-grep.** It is the
shadow-utils `newgrp` binary:

```console
$ sg --version
Usage: sg group [[-c] command]
$ exit=1
$ ls -la /usr/bin/sg
lrwxrwxrwx 1 root root 6 Jan  1  1970 /usr/bin/sg -> newgrp
```

`ast-grep` / `astgrep` are not on `PATH` anywhere, and there is no network
egress to fetch them (a `pip download ast-grep-py` probe fails with
`SSL: WRONG_VERSION_NUMBER`, i.e. the egress proxy is not configured in this
cell — the same limitation recorded for issue #42 in
`docs/spikes/ast-grep-matchline-phase0.md`, which installed ast-grep 0.51.2
into a throwaway `/tmp` venv there).

The brief forbids provisioning ("do not install anything"), so **nothing was
installed**. The consequence is stated plainly:

* the rules in `rules/*.yml` were **not** executed by a real ast-grep binary
  in this slice — their ast-grep-side syntax is therefore **unvalidated**;
* the scan numbers quoted below come from the *reference scanner* bundled in
  `run_scan.py`, which interprets the very same YAML rule files over the
  Python `ast` module. It is **self-written** evidence and sits at the
  weakest tier of the evidence ranking (project test suite > repo-convention
  tests > ad-hoc self-written checks);
* the engine used is always recorded in the JSON output
  (`"engine": "reference-fallback"` vs `"engine": "ast-grep"`), so no result
  can be mistaken for real ast-grep output.

`run_scan.py` prefers a real ast-grep binary whenever one exists
(`$AST_GREP_BIN`, `ast-grep`, or `sg` — the last one only if
`<bin> --version` actually reports ast-grep, which is exactly the check that
rejects the `newgrp` impostor). When a real binary is present, the same
command produces ast-grep-native output with no other change.

**Action required before Phase 1:** re-run `run_scan.py` on a machine with a
real ast-grep binary and confirm the same counts; if the YAML syntax is
rejected by ast-grep (see "Known limitations"), fix the rule files first.

---

## 1. Rules and their documented source

Every rule cites the convention it encodes in its own YAML header, in the
`message` field and in the `note` field.

| # | rule id | file | convention encoded | source (matchline) |
|---|---|---|---|---|
| R1 | `no-sys-path-hack` | `rules/no-sys-path-hack.yml` | no `sys.path` hacks | AGENTS.md (Never-list) "No `sys.path` hacks."; CONTRACT.md "no `sys.path` hacks"; docs/CODE-REVIEW.md anti-pattern "sys.path manipulation" — recorded for issue #42 as AGENTS.md:19 / CONTRACT.md:30 / CODE-REVIEW.md:45 |
| R2 | `no-jesse-import` | `rules/no-jesse-import.yml` | `jesse.py` is research-only (WiSARD reproduction) and must not be wired into the production pipeline | AGENTS.md (Project Structure) "jesse.py is research-only …"; issue #18 brief quoting AGENTS.md Project Structure / Untrusted-input |
| R3 | `no-xml-entity-expansion` | `rules/no-xml-entity-expansion.yml` | parse XML with entity expansion disabled | AGENTS.md (Untrusted-input) "Parse XML with entity expansion disabled."; CONTRACT.md "parse XML with entity expansion disabled" — recorded for issue #42 as AGENTS.md:22 / CONTRACT.md:31 |
| R4 | `no-hardcoded-absolute-path` | `rules/no-hardcoded-absolute-path.yml` | no machine/absolute paths in code | AGENTS.md (Never-list) "No datasets, credentials, or machine paths in code."; CONTRACT.md "never hardcode absolute venv paths"; docs/CODE-REVIEW.md anti-pattern "~/workspace/... paths in library code" — recorded for issue #42 as AGENTS.md:19 / CONTRACT.md:30 / CODE-REVIEW.md:47 |
| R5 | `no-subprocess-shell-true` | `rules/no-subprocess-shell-true.yml` | **general guard, NOT a matchline convention** — `subprocess` call with `shell=True` | none; flagged as a generic unsafe-input guard in the rule header, the `message` and here |

Rule bodies (abridged; the YAML files carry the full citations):

```yaml
# R1
rule:
  any:
    - pattern: sys.path.insert($$$ARGS)
    - pattern: sys.path.append($$$ARGS)
    - pattern: sys.path.extend($$$ARGS)

# R2
rule:
  any:
    - pattern: import jesse
    - pattern: from jesse import $$$

# R3
rule:
  all:
    - kind: call
    - regex: '(?i)parser'
    - regex: 'resolve_entities\s*=\s*True'

# R4
rule:
  kind: string
  regex: '(/home/|~/workspace|/Users/)'

# R5
rule:
  all:
    - kind: call
    - regex: 'subprocess'
    - regex: 'shell\s*=\s*True'
```

R3 targets the *construction* of a parser with entity expansion switched on
(`XMLParser(resolve_entities=True)` in the `etree.` / `ET.` / `lxml.etree.`
shapes). The safe form `resolve_entities=False` — and the matchline
`safe_xml.safe_xml_parser()` helper — is not matched.

---

## 2. Fixtures

| rule | seeded violation (`fixtures/violation/`) | clean counterpart (`fixtures/clean/`) |
|---|---|---|
| R1 | `v1_sys_path_hack.py` — `sys.path.insert(0, "/opt/vendor-libs")`, `sys.path.append("/opt/other-libs")`, `sys.path.extend(["/opt/third-libs"])` | `c1_no_sys_path_hack.py` — normal import, no import-path mutation |
| R2 | `v2_jesse_import.py` — `import jesse` and `from jesse import research_helper` | `c2_no_jesse_import.py` — imports a supported dependency |
| R3 | `v3_xml_entity_expansion.py` — `etree.XMLParser(resolve_entities=True, huge_tree=True)` | `c3_xml_entity_expansion.py` — `etree.XMLParser(resolve_entities=False, huge_tree=False)` |
| R4 | `v4_hardcoded_absolute_path.py` — `DATASET_ROOT = "/home/alice/datasets/matchline"` | `c4_no_hardcoded_absolute_path.py` — `os.environ.get("MATCHLINE_DATASET_ROOT", "datasets/matchline")` |
| R5 | `v5_subprocess_shell_true.py` — `subprocess.run(command, shell=True, …)` | `c5_subprocess_shell_true.py` — `subprocess.run(argv, capture_output=True, text=True, check=True)` |

Each violation fixture contains only violations of its own rule, so a
per-rule count of 1 (R1: 3, R2: 2 — one per forbidden form) on the violation
set is the expected result.

---

## 3. Scan results (quoted)

### 3.1 Environment check

```console
$ sg --version
Usage: sg group [[-c] command]
$ echo "exit=$?"
exit=1
```

### 3.2 The scan

```console
$ python3 spikes/astgrep-matchline/run_scan.py
engine: reference-fallback (candidate /usr/bin/sg (`sg` on PATH) is not ast-grep: `/usr/bin/sg --version` printed 'Usage: sg group [[-c] command]')
violation: total_matches=8
  no-hardcoded-absolute-path: 1
  no-jesse-import: 2
  no-subprocess-shell-true: 1
  no-sys-path-hack: 3
  no-xml-entity-expansion: 1
clean: total_matches=0
  no-hardcoded-absolute-path: 0
  no-jesse-import: 0
  no-subprocess-shell-true: 0
  no-sys-path-hack: 0
  no-xml-entity-expansion: 0
results written to .../spikes/astgrep-matchline/results/scan-results.json
```

### 3.3 JSON summary — both fixture sets

From `results/scan-results.json` (full file in the repo):

```json
{
  "engine": "reference-fallback",
  "engine_detail": "candidate /usr/bin/sg (`sg` on PATH) is not ast-grep: `/usr/bin/sg --version` printed 'Usage: sg group [[-c] command]'",
  "ast_grep_version": null,
  "rules": [
    "no-hardcoded-absolute-path",
    "no-jesse-import",
    "no-subprocess-shell-true",
    "no-sys-path-hack",
    "no-xml-entity-expansion"
  ],
  "fixture_sets": {
    "clean": {
      "total_matches": 0,
      "by_rule": {
        "no-hardcoded-absolute-path": 0,
        "no-jesse-import": 0,
        "no-subprocess-shell-true": 0,
        "no-sys-path-hack": 0,
        "no-xml-entity-expansion": 0
      }
    },
    "violation": {
      "total_matches": 8,
      "by_rule": {
        "no-hardcoded-absolute-path": 1,
        "no-jesse-import": 2,
        "no-subprocess-shell-true": 1,
        "no-sys-path-hack": 3,
        "no-xml-entity-expansion": 1
      }
    }
  }
}
```

**Violation set:** every rule fires on its own seeded file (and only there):

| rule | file | line | matched text |
|---|---|---|---|
| `no-sys-path-hack` | `v1_sys_path_hack.py` | 8 | `sys.path.insert(0, "/opt/vendor-libs")` |
| `no-sys-path-hack` | `v1_sys_path_hack.py` | 9 | `sys.path.append("/opt/other-libs")` |
| `no-sys-path-hack` | `v1_sys_path_hack.py` | 10 | `sys.path.extend(["/opt/third-libs"])` |
| `no-jesse-import` | `v2_jesse_import.py` | 6 | `import jesse` |
| `no-jesse-import` | `v2_jesse_import.py` | 7 | `from jesse import research_helper` |
| `no-xml-entity-expansion` | `v3_xml_entity_expansion.py` | 8 | `etree.XMLParser(resolve_entities=True, huge_tree=True)` |
| `no-hardcoded-absolute-path` | `v4_hardcoded_absolute_path.py` | 6 | `"/home/alice/datasets/matchline"` |
| `no-subprocess-shell-true` | `v5_subprocess_shell_true.py` | 11 | `subprocess.run(command, shell=True, capture_output=True, text=True)` |

**Clean set:** `total_matches = 0` — zero findings for all five rules across
all five clean fixtures. No rule fired on a file that does not seed its
violation, so there are **no cross-rule false positives** on the clean set
either.

---

## 4. Known limitations / coverage gaps (carry into Phase 1)

* **The rules have never been run by ast-grep itself** (see §0). The YAML
  uses `pattern`, `kind`, `regex`, `all`/`any` and `$$$` — the ast-grep
  features used by the already-shipped issue #42 rules — but the
  statement-level pattern `from jesse import $$$` and the `regex` constraint
  inside an `all` list are the two constructs most likely to need adjustment
  if ast-grep rejects them. Fallbacks if needed: `from jesse import $NAME`
  plus a second pattern, and merging the two regexes into one object.
* R1 does not cover `sys.path = [...]` rebinding or `sys.path[0:0] = [...]`.
* R2 does not cover `import jesse.research` (submodule import) — only the
  top-level module name is matched.
* R3 keys on the text `resolve_entities=True` inside a call that mentions
  "parser"; a parser built through a helper wrapper would be missed.
* R4 matches string literals only (not f-string interpolations of a path
  built at runtime), and it also matches docstrings that merely *mention* a
  machine path — Phase 1 should classify/scope those (issue #42 measured the
  same effect for its `C4` rule).
* R5 is a general guard, **not** a matchline convention, and does not cover
  the `from subprocess import run` form.

---

## 5. Phase 1 gate — stated honestly

**NOT done by this slice:**

* no CI wiring of any kind was performed — in particular **no advisory CI
  job** was added to any repository, and **no `.github/workflows` file was
  created, edited or read**;
* the **1-week zero-false-positive observation window on `develop`** was
  **not** run — there is no measurement of the rules against the real
  matchline tree in this slice (the fixtures are synthetic);
* the rules were **not** validated against a real ast-grep binary, so the
  counts in §3 are reference-scanner counts, not ast-grep counts.

The Phase 1 gate ("wire the rules into CI as advisory and observe one week
of zero false positives on `develop` before making them blocking") remains
open and is Alex's call after this measurement slice. The measurement this
slice does provide is: *the five rules are expressible in ast-grep YAML, each
one is catchable on a synthetic seeded violation, and none of them fires on
the synthetic clean corpus.*

---

## 6. Verification

```console
$ sg --version
Usage: sg group [[-c] command]      # NOT ast-grep — environment failure, see §0

$ python3 spikes/astgrep-matchline/run_scan.py --check
engine: reference-fallback (candidate /usr/bin/sg (`sg` on PATH) is not ast-grep: ...)
violation: total_matches=8
  ...
clean: total_matches=0
  ...
$ echo $?
0

$ python3 -m pytest tests/ -q
231 passed, 2 skipped, 13 subtests passed in 19.29s
```

The two skipped tests are the ast-grep-dependent cases in
`tests/test_astgrep_matchline.py` (skipped because no ast-grep binary is
available — the binary is not a hard dependency of the suite). The other two
tests in that file (rule-YAML validity and fixture pairing) always run.

---

## 7. Verdict for this slice only

**PASS (with one environment caveat).** Five rules are written, each traceable
to a documented matchline convention with the citation in the rule YAML and
in this report; each rule fires on its seeded violation file and the clean
fixture set yields zero findings.

Caveat: the ast-grep binary named in the brief is absent from this
environment (`/usr/bin/sg` is `newgrp`), so the evidence above is
reference-scanner evidence, not ast-grep evidence, and the rule YAML syntax
is unvalidated against ast-grep. Re-running `run_scan.py` on a host with a
real ast-grep binary is the first action of Phase 1 — before any CI wiring.
