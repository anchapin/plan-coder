# Stalemap — Phase 0 measurement report (issue #69, NARROWED slice: planar-nexus dependency-change census)

- **Issue:** plan-coder #69 — *[spike] stalemap-mined post-cutoff dependency-change block for TS repos — Phase 0 measurement first* (filed 2026-11-17, state `open`).
- **Scope of this slice:** **Phase 0, MEASUREMENT ONLY, one repo** — the **dependency-change census for
  `planar-nexus` only**. The other half of #69's Phase 0 (the transcript incident scan) is **DEFERRED**
  (see §8).
- **Write scope:** exactly one new file in this worktree — `docs/stalemap-phase0-report.md`. No install,
  no build, no `AGENTS.md` / prompt / config edit, no commit, no push.
- **Inputs read (read-only):** `~/workspace/planar-nexus/package.json`, `~/workspace/planar-nexus/package-lock.json`,
  read-only `grep` of `~/workspace/planar-nexus/src/`, npm registry metadata + GitHub release notes over the
  network.
- **Status:** report delivered. Headline: **104 direct deps censused → 2 major-line candidates →
  2 deep-checked → 0 qualifying in-use post-cutoff API changes** (§5, §7).

---

## 1. Proxy statement (what "post-cutoff" means here)

The issue's proxy is used verbatim, and **no model's actual knowledge cutoff is asserted anywhere in this
report**:

> A change is "post-cutoff" if **the first release containing it shipped after 2025-01-01**.

Consequences of using this proxy, stated up front:

- "Post-cutoff" is a property of the **release that first shipped the change** — not of the change's
  authorship date, and not of any model's training-data boundary.
- A change authored before 2025-01-01 but first shipped in a release published after 2025-01-01 **counts**
  as post-cutoff under this proxy.
- A change first shipped on or before 2025-01-01 **does not** count, even if the repo would still be broken
  by it.
- **No claim is made about the training-data boundary of MiniMax / z.ai / Claude / any other model.** The
  issue itself notes those vendors do not publish a models.dev-style cutoff for the tiers in use; the
  2025-01-01 date is a *defensible stand-in*, not a fact about any model.

A second, orthogonal date rule comes from the issue's census definition and is also applied here:
the issue counts changes "between the pinned version and **the newest stable ≥ 30 days old**". This slice
measured that window against the measurement clock: **now = 2026-11-28T12:58Z**, so the 30-day floor is
**2026-10-29T12:58Z**; a release published **at or before** that instant is at least 30 days old (eligible to
end the window), one published after it is too fresh and therefore excluded (§4.3).

---

## 2. Methods

1. **Census (no network).** `~/workspace/planar-nexus/package.json` and `package-lock.json` (lockfile v3)
   were parsed read-only. A dep is **direct** if it appears in `dependencies` / `devDependencies` /
   `peerDependencies` / `optionalDependencies` of `package.json`. The **pinned version** used everywhere in
   this report is the exact version resolved for that dep in `package-lock.json`
   (`packages["node_modules/<dep>"].version`), not the caret range in `package.json`. Both are shown in
   Appendix A.
2. **Candidate selection (one batched registry script, no per-dep crawling).** One npm-registry metadata
   request per *distinct registry package* (`GET https://registry.npmjs.org/<pkg>`, full packument, so the
   per-version publish dates in `time` and the `exports` map are available). Alias deps were collapsed
   first (`typescript` → `npm:@typescript/typescript6@^6.0.2`, `@typescript/native` →
   `npm:typescript@^7.0.2`), so 104 direct deps map to **103 distinct registry packages**. A dep is a
   **candidate** iff a **stable** release exists in a **strictly higher major line** than its pinned major
   (prereleases excluded from candidacy; §4.2 reports them separately).
3. **Deep-check (≤ 10 candidates).** For each candidate, the breaking change was identified from the
   upstream **release notes** (`api.github.com/repos/<owner>/<repo>/releases/tags/<tag>`) and the
   **shipped-`.d.ts`/package-metadata difference** between the pinned version and the new-major release
   (`api.github.com/repos/<owner>/<repo>/compare/<pinned>...<new>` plus the two versions' `exports` /
   `types` / `peerDependencies` fields from the cached packuments). Release dates come from the registry
   packument `time` map (authoritative publish time).
4. **In-use test (the issue's gate (b)).** A change **qualifies only if the changed public symbol is
   imported/used under `~/workspace/planar-nexus/src/`** — grep evidence with a file path is recorded in
   §5. A changed API nobody imports does not count. Grep scope for this slice is `src/` only.
5. **Caps honoured:** ≤ 10 deps deep-checked (2 used, because only 2 candidates exist); harvesting stopped
   before the step-35 cutoff; the transcript half was not attempted.

### 2.1 Registry-request accounting (honest disclosure — one deviation)

| Pass | Distinct registry endpoints | HTTP requests | Note |
|---|---|---|---|
| 1 | 103 | 103 | Script crashed **after** download in the summarise step (a `repository` field that was a *string* in some packuments); nothing was persisted, so this pass's data was lost. |
| 2 | 103 (same endpoints) | 103 | Fixed script, each packument written to `/tmp/packuments/<pkg>.json` **before** parsing. |
| Deep-check | 0 | 0 | No further registry traffic: breaking-change evidence came from GitHub release notes + `compare` diffs, and from the already-cached packuments. |
| **Total** | **103 distinct** | **206** | The brief's cap was 110 registry requests. |

**Deviation:** the 206 HTTP requests exceed the brief's 110-request cap. The cause is a script bug
(pass 1's in-memory results were lost before persistence), **not** extra scope: only **103 distinct registry
endpoints** were touched (≤ 110), each of the 104 direct deps was resolved exactly once in the surviving
pass, and no package endpoint was queried for per-version or per-file detail. Recorded here rather than
hidden.

### 2.2 Environment note

Direct TLS to the internet fails in this cell (`SSL: wrong version number`); the ambient egress proxy
(`HTTP(S)_PROXY=http://hatch-egress-proxy:3128`, CA bundle `/run/hatch/egress-tls/ca-bundle.pem`) must be
used **explicitly** (`curl -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem …`,
or `urllib` with an equivalent `ProxyHandler` + `HTTPSHandler(context=…)`). `gh` fails without the proxy
settings; `curl`/`urllib` with the explicit proxy work for both `registry.npmjs.org` and `api.github.com`.

---

## 3. Dependency census (planar-nexus direct deps)

| Metric | Value |
|---|---|
| **Direct deps censused** | **104** (56 `dependencies` + 48 `devDependencies`) |
| Distinct registry packages behind them | 103 (2 alias deps collapse onto other names) |
| Deps whose pinned version is the newest release of its own major line | 73 |
| Deps with a newer release on the *same* major line (not deep-checked — see §10.2) | 31 |
| Deps with a newer **stable major line** above the pinned major (= candidates) | **2** |
| Deps with a newer major line in *prerelease only* (excluded from candidacy) | 10 (see §4.2) |

The full 104-dep list with pinned versions is in **Appendix A**. (Note: the issue text says planar-nexus has
"103 direct deps"; the manifest as measured today yields **104** — one dep was added since 2026-11-17. The
census uses the manifest as it stands.)

---

## 4. Candidate selection

### 4.1 Candidates (stable newer major line) — the complete set

| dep | section | pinned (lock) | pinned major | newest stable major | first release of that major | published | newest release |
|---|---|---|---|---|---|---|---|
| `@hookform/resolvers` | dep | `6.0.0` | 6 | **7** | `7.0.0` | 2026-10-31 | `7.0.9` (2026-11-24) |
| `@testing-library/jest-dom` | dev | `7.0.1` | 7 | **8** | `8.0.0` | 2026-11-19 | `8.0.0` (2026-11-19) |

Every other direct dep is already on the newest stable release of its own major line, or its pinned major is
the newest stable major that exists for that package.

### 4.2 Prerelease-only higher majors (excluded from candidacy, reported for completeness)

`@radix-ui/react-select` 3.0.0-rc.*, `@tauri-apps/plugin-process` 3.0.0-alpha.*,
`@tauri-apps/plugin-updater` 3.0.0-alpha.*, `@tauri-apps/cli` 3.0.0-alpha.*, `date-fns` 5.0.0-alpha.0,
`embla-carousel-react` 9.0.0-rc*, `fast-check` 5.0.0-beta, `prettier` 4.0.0-alpha.*,
`react-hook-form` 8.0.0-alpha.*, `ts-node` 11.0.0-beta.1 — 10 deps. None of these is a stable major line,
so none is a candidate under the brief's rule; their breaking changes, if any, are unreleased-to-consumers
and out of scope for this slice.

### 4.3 The issue's own "newest stable ≥ 30 days old" window, applied

With now = 2026-11-28T12:58Z, the 30-day floor is **2026-10-29T12:58Z**; a release counts as "≥ 30 days
old" only if it was published at or before that instant.

| candidate | pinned (published) | newest stable ≥ 30 days old | the issue's window | candidate under the brief's rule? |
|---|---|---|---|---|
| `@hookform/resolvers` | 6.0.0 (2026-10-19) | **6.0.2** (2026-10-28) — every 7.0.x is 4–28 days old | `6.0.0 → 6.0.2` (3 versions, **no** major bump) | yes |
| `@testing-library/jest-dom` | 7.0.1 (2026-08-09) | **7.0.1** (2026-08-09) — `8.0.0` is 9 days old | `7.0.1 → 7.0.1` (empty: the pinned release *is* the newest eligible one) | yes |

The brief's candidate rule (newer major line) is therefore **wider** than the issue's window rule here:
under the issue's literal wording neither candidate's major-bump release is countable yet (`@hookform/resolvers`
7.0.0 is 28 days old; `@testing-library/jest-dom` 8.0.0 is 9 days old). The brief's rule is the one this
slice was told to apply, so both major-bump releases are deep-checked and reported (§5).

The two releases that *are* inside the issue's window for the resolvers candidate were checked too (release
notes): `6.0.1` (2026-10-27) — *"drop @typeschema/main from peerDependencies"* (a bug-fix release);
`6.0.2` (2026-10-28) — *"typebox: preserve escaped field names in validation errors"* (bug fix). Neither is
a removed/renamed export or a breaking signature change, and the package is not imported by `src/` at all
(§5), so neither can qualify.

The two rules disagree about **which releases are countable**; they agree on the **count**: 0 qualifying for
planar-nexus either way.

---

## 5. Qualifying changes table (deep-checked: 2 of 2 candidates)

| dep | pinned version | changing release + date | changed public symbol / API | is the changed symbol used in `src/`? (grep evidence) | qualifying? |
|---|---|---|---|---|---|
| `@testing-library/jest-dom` | `7.0.1` (2026-08-09) | **`8.0.0`, published 2026-11-19** (release notes: *"Support for vitest versions lower than 5 has been removed. Consumers must upgrade to a supported version."*) | The **`vitest` module augmentation** in `types/vitest.d.ts`: `interface Assertion<T = any> extends TestingLibraryMatchers<any, T> {}` → `interface Assertion<R, T> extends TestingLibraryMatchers<T, R> {}` (generic order swapped = breaking type signature), plus peer range `vitest: ">= 0.32"` → `">=5 <6"`. The `./jest-globals` and `./matchers` entry points and `types/matchers.d.ts` are **unchanged** between 7.0.1 and 8.0.0. | **No.** `grep -rn "from ['\"]vitest['\"]" src/` → 0 hits; the only `vitest` string in `src/` is the word "vitest-only" in a comment at `src/ai/tools/__tests__/card-search.test.ts:2`. No `src/` file imports `@testing-library/jest-dom/vitest`. The 29 `src/` files that import `@testing-library/jest-dom/jest-globals` (e.g. `src/app/(app)/deck-builder/_components/__tests__/deck-list.test.tsx`), the 3 that import the bare `@testing-library/jest-dom` root entry (`src/components/__tests__/draft-pool-view.test.tsx:21`, `src/components/__tests__/game-chat.test.tsx:20`, `src/components/__tests__/p2p-status-banner.test.tsx:4`) and the hand-written ambient matcher list in `src/test-utils/jest-dom.d.ts` touch only the **unchanged** jest-globals/matcher surface. | **NO** |
| `@hookform/resolvers` | `6.0.0` (2026-10-19) | **`7.0.0`, published 2026-10-31** (release notes: *"the typebox resolver now requires `typebox` ^1.0.0 as peer dependency instead of `@sinclair/typebox` … `TypeCompiler.Compile` is replaced by `Compile` from `typebox/compile`"*) | The **`typebox` resolver surface**: peer `@sinclair/typebox` dropped / `typebox: ^1.0.0` added; `TypeCompiler.Compile` → `Compile` (`typebox/compile`); resolver error `type` values change from numeric `ValueErrorType` codes to keyword names (e.g. `minLength`) and messages change to TypeBox 1.x wording. | **No.** `grep -rn "hookform" src/` → **0 hits** (0 files); `grep -rn "zodResolver" src/` → 0 hits; `grep -rn "resolvers" src/` → 2 hits, both unrelated prose in `src/lib/game-state/spell-casting/choices.ts:32` and `src/lib/game-state/effect-resolution.ts:79`. `react-hook-form` itself is imported once in `src/`, but **not** the resolvers package. | **NO** |

**Qualifying-change count for planar-nexus: 0.** Both deep-checked candidates contain a genuine,
post-2025-01-01 breaking public-API change (both releases shipped in 2026), but in neither case does the
repo's own `src/` import the changed symbol — which is exactly the issue's gate (b): *"the repo's own source
imports that API (grep `src/` for the changed symbols; a changed API nobody imports does not count)"*.

Deep-checked deps: **2** (the cap is 10; the candidate set is exhausted — there is no third candidate to
prioritise). "Most-used" was therefore never a binding constraint: both candidates were checked in full.

Supporting detail for the two `src/` usages that were checked:

```
$ grep -rn "@testing-library/jest-dom" ~/workspace/planar-nexus/src/ | wc -l
34                       # 29 x "@testing-library/jest-dom/jest-globals" + 3 x bare root import + 2 comment lines
$ grep -rln "@testing-library/jest-dom/jest-globals" ~/workspace/planar-nexus/src/ | wc -l
29
$ grep -rn "toBeInTheDocument\|toBeDisabled\|toHaveTextContent" ~/workspace/planar-nexus/src/ | wc -l
545                       # matcher call sites (Jest runtime matchers; v8.0.0 does not touch these matchers)
$ grep -rn "hookform" ~/workspace/planar-nexus/src/ | wc -l
0
```

---

## 6. Sensitivity note (2024-06-01 proxy)

Re-running the census with the alternative proxy *"the first release containing it shipped after
**2024-06-01**"*, using only the release dates already collected (no extra harvesting): **the count does not
change — it stays 0.** Both candidate breaking changes (`@hookform/resolvers` 7.0.0 @ 2026-10-31 and
`@testing-library/jest-dom` 8.0.0 @ 2026-11-19) are post-2024-06-01 *and* post-2025-01-01, so the date
proxy is not what excludes them — the **in-use test** does (neither changed symbol is imported under `src/`).
Symmetrically, moving the proxy *later* (e.g. 2026-01-01) would not change the count either, since both
releases post-date that as well. The date proxy is therefore **not the binding constraint** for this slice.
What *would* move the number is the width of the release window, not its date: **100 of the 104 pinned
releases were themselves published after 2024-06-01** (the 4 exceptions are `@types/lru-cache` 7.10.10
(2022-05-13), `tailwindcss-animate` 1.0.7 (2023-08-28), `ts-node` 10.9.2 (2023-12-08) and `clsx` 2.1.1
(2024-04-23); the newest pinned release is 2026-11-16, `gdp-wired` 0.50.2), so under a 2024-06-01 proxy
essentially the whole dependency baseline is itself "post-cutoff", and any breaking change sitting in a
*same-major* release window (§10.2, 31 deps) would count as post-cutoff as well. Under that proxy the true
planar-nexus figure is bounded below by 0 and above by what a full same-major window sweep would find — which
this slice did not perform.

---

## 7. Census verdict

**planar-nexus-only qualifying count (this slice): 0.**

Census arithmetic: **104 direct deps censused → 103 distinct registry packages → 2 candidates (stable newer
major line) → 2 deep-checked (cap 10) → 0 qualifying in-use post-cutoff API changes.** Both candidates
carry a real, post-2025-01-01 breaking public-API change (`@hookform/resolvers` 7.0.0, 2026-10-31;
`@testing-library/jest-dom` 8.0.0, 2026-11-19), and in both cases the repo's `src/` does not import the
changed symbol, so neither clears the issue's in-use gate.

**This slice does not close the issue's two-repo verdict.** The issue's decision bars are written for
**two repos** — `staging-studio` (37 direct deps) **and** `planar-nexus` (103 direct deps per the issue;
104 as measured today):

- **Proceed to Phase 1** if the census finds **≥ 8 qualifying in-use API changes across the two repos**, *or*
  the incident scan finds **≥ 3 stale-API incidents** with `run_id` evidence.
- **Measured Kill** if the census finds **< 3 qualifying changes** *and* incidents are **0–1** on an n of
  **≥ 20 TS runs**. If TS run n < 20, the incident half is *"unmeasured — thin sample"* and the verdict
  rests on the census alone.

This slice contributes **one repo's census half** and **zero** of the incident half (deferred, §8). A
planar-nexus-only figure of 0 therefore **cannot** be read against those bars on its own: with
staging-studio uncounted and the incident scan unmeasured, the two-repo total is **not** determined by this
report, and the Proceed/Kill comparison is **not** made here. The only thing this slice establishes is that
**planar-nexus's contribution cannot by itself reach the Proceed bar's ≥ 8 threshold** (it contributes 0),
and that planar-nexus's contribution to the Kill bar's "< 3" test is **0** — i.e. if staging-studio's
census also came back at 0–2 and the incident scan returned 0–1 incidents on n ≥ 20 TS runs, the two-repo
verdict would be **Measured Kill**; if staging-studio alone reached ≥ 8, the verdict would be **Proceed**.
Neither conclusion is asserted here. **No model spend, no tool installed, no `AGENTS.md` block written, no
build run, no prompt/config change** — measurement only, as #69 requires.

---

## 8. Deferred half of #69 Phase 0 — transcript incident scan

**DEFERRED — not attempted in this slice.** The brief for this attempt splits #69's Phase 0 into two halves
and defers the transcript incident scan to a later slice. **No transcript, ledger, or harness-state corpus
was read, scanned, summarised or counted** in producing this report; no transcript directory was opened, no
`run_id` was enumerated, and no incident rate, denominator, or token estimate appears anywhere above.

Consequences for the issue's bars, stated so nothing here is over-read:

- The incident half of the "Proceed" bar (**≥ 3 stale-API incidents with `run_id` evidence**) is
  **unmeasured** in this slice.
- The incident half of the "Kill" bar (**0–1 incidents on n ≥ 20 TS runs**) is **unmeasured**; whether the
  TS-run sample is thin (< 20) is also **not** determined here.
- Nothing in §1–§7 should be read as a finding — or a non-finding — about transcript-level incidents.

---

## 9. Exact commands run

All network calls used the ambient egress proxy explicitly (§2.2). `$ PN` = `~/workspace/planar-nexus`.

```bash
# 0. context
pwd; git status --porcelain; git log --oneline -3
ls -l $PN/package.json $PN/package-lock.json; ls $PN/src

# 1. dependency census (read-only, no network) -> /tmp/deps.json (104 rows)
python3 - <<'PY'
import json
PN = "/home/hatch/workspace/planar-nexus"
pkg  = json.load(open(f"{PN}/package.json"))
lock = json.load(open(f"{PN}/package-lock.json"))
resolved = {}
for path, meta in lock.get("packages", {}).items():
    if path.startswith("node_modules/") and isinstance(meta, dict) and "version" in meta:
        resolved.setdefault(path[len("node_modules/"):], meta["version"])
rows = [{"name": n, "section": s, "range": r, "locked": resolved.get(n)}
        for s in ("dependencies","devDependencies","peerDependencies","optionalDependencies")
        for n, r in pkg.get(s, {}).items()]
json.dump(rows, open("/tmp/deps.json","w"), indent=1)
print("total direct deps:", len(rows))
PY
# -> total direct deps: 104   (prod 56 / dev 48; lockfileVersion 3; 0 deps missing from the lockfile)

# 2. batched registry metadata harvest (1 request per distinct package; /tmp/batch_registry.py)
python3 /tmp/batch_registry.py     # pass 1: 103 fetches, crashed in summarise (data lost)
python3 /tmp/batch_registry.py     # pass 2: 103 fetches, cached to /tmp/packuments/*.json
# stderr: "fetched 103 packages; errors: 0 []"
# equivalent single-package probe used to validate the proxy recipe:
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/json" -o /tmp/react.json -w "http:%{http_code} size:%{size_download}\n" \
     https://registry.npmjs.org/react
# -> http:200 size:7170415   (full packument: dist-tags + versions + time)

# 3. candidate selection (local, from cached packuments)
python3 - <<'PY'   # major(pinned) vs max stable major; alias collapse via registry.json["aliases"]
# -> CANDIDATES (stable newer major): 2
#    @hookform/resolvers 6.0.0 -> major 7 (first 7.0.0 @ 2026-10-31)
#    @testing-library/jest-dom 7.0.1 -> major 8 (first 8.0.0 @ 2026-11-19)
# -> deps with a newer release on the SAME major line: 31 ; on the newest release of their major: 73
# -> deps with ANY higher major incl. prereleases: 12 (10 prerelease-only)

# 4. deep-check evidence (release notes + shipped-.d.ts / package-metadata diffs)
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/testing-library/jest-dom/releases/tags/v8.0.0"
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/testing-library/jest-dom/releases/tags/v7.0.0"
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/react-hook-form/resolvers/releases/tags/v7.0.0"
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/react-hook-form/resolvers/releases/tags/v6.0.1"   # in-window release
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/react-hook-form/resolvers/releases/tags/v6.0.2"   # in-window release
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/testing-library/jest-dom/compare/v7.0.1...v8.0.0"   # 5 files changed
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/react-hook-form/resolvers/compare/v6.0.2...v7.0.0" # 15 files changed
# + the issue text itself (for the bars, read-only):
curl -s -x http://hatch-egress-proxy:3128 --cacert /run/hatch/egress-tls/ca-bundle.pem \
     -H "Accept: application/vnd.github+json" \
     "https://api.github.com/repos/anchapin/plan-coder/issues/69"

# 5. in-use test (read-only grep of $PN/src/)
grep -rn "@hookform/resolvers" $PN/src/            # 0 hits
grep -rn "hookform" $PN/src/                      # 0 hits
grep -rn "zodResolver" $PN/src/                   # 0 hits
grep -rn "resolvers" $PN/src/                     # 2 hits, both unrelated prose
grep -rn "@testing-library/jest-dom" $PN/src/ | wc -l      # 34 (29 x jest-globals, 3 x bare root, 2 comment lines)
grep -rn "toBeInTheDocument\|toBeDisabled\|toHaveTextContent" $PN/src/ | wc -l   # 545
grep -rn "from ['\"]vitest['\"]" $PN/src/         # 0 hits
grep -rn "vitest" $PN/src/                        # 1 hit: src/ai/tools/__tests__/card-search.test.ts:2 (comment)
grep -rln "@testing-library/jest-dom/jest-globals" $PN/src/ | wc -l   # 29 files; ambient matcher list: src/test-utils/jest-dom.d.ts

# 6. clock / 30-day floor
date -u "+now=%Y-%m-%dT%H:%M:%SZ"   # now=2026-11-28T12:58:46Z  -> 30-day floor 2026-10-29T12:58:46Z
```

Scratch artefacts (never in the diff): `/tmp/deps.json`, `/tmp/registry.json`, `/tmp/candidates.json`,
`/tmp/packuments/*.json` (103 packuments), `/tmp/batch_registry.py`, `/tmp/jestdom_compare.json`,
`/tmp/resolvers_compare.json`, `/tmp/issue69.json`.

---

## 10. Deviations, contradictions and limitations (read before quoting §7)

1. **Registry-request cap exceeded (206 HTTP requests / 103 distinct endpoints) —** cause and remedy in §2.1.
   The distinct-endpoint count (103) is inside the cap; the request count is not, because pass 1's results
   were lost to a script bug. Disclosed rather than hidden.
2. **The brief's candidate rule is narrower than the issue's census rule.** The brief selects candidates by
   *"a newer MAJOR version line exists above the pinned major"*; the issue's census covers **all** releases
   "between the pinned version and the newest stable ≥ 30 days old", including **same-major** releases.
   This slice deep-checked only the 2 major-line candidates. **31 direct deps have newer releases on their
   own major line** (e.g. `eslint` 10.15.0 → 10.16.0, `recharts` 3.10.1 → 3.11.2,
   `dompurify` 3.4.18 → 3.5.0, `ai` 7.0.173 → 7.0.184, `lucide-react` 1.73.0 → 1.83.0) and were **not**
   deep-checked. Under semver those should be non-breaking, but
   the issue's rule does not assume that. **Therefore the 0 in §7 is a lower bound for planar-nexus under the
   issue's full rule, not a complete census** — a full sweep of those 31 windows is the obvious next slice.
3. **Rule disagreement on the window, not on the count.** Under the issue's literal "newest stable ≥ 30 days
   old" window (floor 2026-10-29T12:58Z) **neither** candidate's major-bump release is countable yet:
   `@hookform/resolvers` 7.0.0 is 28 days old and `@testing-library/jest-dom` 8.0.0 is 9 days old; the
   jest-dom window is in fact empty (the pinned 7.0.1 *is* the newest eligible release) and the resolvers
   window is `6.0.0 → 6.0.2` (bug fixes only, §4.3). This slice followed the brief's wider rule and
   deep-checked both major bumps anyway. Either way the count is 0 — the candidates fail the **in-use**
   gate, not the date rule.
4. **Dep-count drift:** the issue says planar-nexus has 103 direct deps (measured 2026-11-17); the manifest
   yields **104** today. Census uses today's manifest.
5. **Release-note provenance:** the breaking-change text comes from the upstream GitHub release bodies and
   the `compare` diffs, with publish dates taken from the npm registry packument `time` map. Both sources
   are consistent for the two releases quoted.
6. **Grep scope:** the in-use test covers `~/workspace/planar-nexus/src/` only, per this slice's scope.
   `@hookform/resolvers` is declared as a **production** dependency but has **zero** `src/` references; this
   report does not claim it is unused repo-wide (that would require a grep outside the allowed scope), only
   that it is not imported by `src/`.
7. **No build, no install, no typecheck** was run: the census reasons from manifests, release notes and
   shipped type declarations, not from a compile. Nothing here asserts that the repo currently builds or
   breaks.

---

## Appendix A — full direct-dep census (104 deps, pinned = `package-lock.json` resolution)

**Production dependencies (56)**

```
@ai-sdk/anthropic@4.0.105               @ai-sdk/google@4.0.121                  @ai-sdk/openai@4.0.118
@ai-sdk/react@4.0.177                   @hookform/resolvers@6.0.0               @huggingface/transformers@4.3.2
@orama/orama@3.1.18                     @orama/plugin-data-persistence@3.1.18   @radix-ui/react-accordion@1.3.1
@radix-ui/react-alert-dialog@1.2.1      @radix-ui/react-avatar@1.3.1            @radix-ui/react-checkbox@1.4.1
@radix-ui/react-collapsible@1.2.1       @radix-ui/react-dialog@1.3.1            @radix-ui/react-dropdown-menu@2.2.1
@radix-ui/react-label@2.1.17            @radix-ui/react-menubar@1.2.1           @radix-ui/react-popover@1.3.1
@radix-ui/react-progress@1.2.1          @radix-ui/react-radio-group@1.5.1       @radix-ui/react-scroll-area@1.4.0
@radix-ui/react-select@2.4.1            @radix-ui/react-separator@1.2.0         @radix-ui/react-slider@1.6.1
@radix-ui/react-slot@1.5.0              @radix-ui/react-switch@1.4.1            @radix-ui/react-tabs@1.2.1
@radix-ui/react-toast@1.3.1             @radix-ui/react-tooltip@1.4.1          @tanstack/react-virtual@3.14.26
@tauri-apps/plugin-process@2.4.0        @tauri-apps/plugin-updater@2.14.1       ai@7.0.173
class-variance-authority@0.7.1          clsx@2.1.1                              comlink@4.4.2
date-fns@4.4.0                          dexie@4.4.7                             dompurify@3.4.18
dotenv@18.0.10                          embla-carousel-react@8.6.0              lru-cache@11.5.3
lucide-react@1.73.0                     next@16.4.6                             next-intl@4.15.1
onnxruntime-web@1.31.1                  qrcode@1.5.4                            react@19.3.1
react-day-picker@10.0.2                 react-dom@19.3.1                        react-hook-form@7.92.0
recharts@3.10.1                         tailwind-merge@3.7.0                    tailwindcss-animate@1.0.7
use-debounce@10.1.1                     zod@4.6.7
```

**Dev dependencies (48)**

```
@commitlint/cli@21.2.4                  @commitlint/config-conventional@21.2.4  @eslint/js@10.0.1
@jest/globals@30.5.2                    @playwright/test@1.65.1                 @stryker-mutator/core@10.1.1
@stryker-mutator/jest-runner@10.1.1     @tailwindcss/postcss@4.3.5              @tauri-apps/cli@2.12.5
@testing-library/dom@10.4.2             @testing-library/jest-dom@7.0.1         @testing-library/react@16.3.3
@testing-library/user-event@14.6.12     @types/dompurify@3.2.0                  @types/jest@30.0.0
@types/lru-cache@7.10.10                @types/node@26.6.6                      @types/qrcode@1.5.6
@types/react@19.3.0                     @types/react-dom@19.3.0                 @typescript-eslint/eslint-plugin@8.73.0
@typescript-eslint/parser@8.73.0        @typescript/native@7.0.2                @typescript/typescript6@6.0.2
@vitest/web-worker@5.1.0                eslint@10.15.0                          eslint-config-next@16.4.6
eslint-plugin-react@7.37.5              eslint-plugin-react-hooks@7.1.1         expect@30.5.2
fake-indexeddb@6.2.8                    fast-check@4.10.2                       gdp-wired@0.50.2
husky@9.1.7                             jest@30.5.2                             jest-environment-jsdom@30.5.2
lint-staged@17.6.1                      msw@3.0.6                               onnxruntime-node@1.31.1
postcss@8.5.33                          prettier@3.10.1                         tailwindcss@4.3.5
ts-jest@29.4.15                         ts-node@10.9.2                          tsx@4.23.15
typescript@6.0.2                        typescript-eslint@8.73.0                vitest@5.1.0
```

**Alias mapping (npm `:` aliases — resolved against the aliased package, not the alias name):**

| dep name in `package.json` | `package.json` range | resolves to registry package | pinned |
|---|---|---|---|
| `typescript` | `npm:@typescript/typescript6@^6.0.2` | `@typescript/typescript6` | `6.0.2` |
| `@typescript/native` | `npm:typescript@^7.0.2` | `typescript` | `7.0.2` |

(These two collapses are why 104 direct deps map to 103 distinct registry packages.)
