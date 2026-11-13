# Brief: fix the one-frame default accent on the settings page (issue #412)

## SCOPE — why, and what is allowed

- Goal: the settings page paints the default blue accent for one frame before applying the saved colour. The saved accent must be applied on the first paint.
- Allowed paths: `src/ui/settings/AccentPicker.tsx`, `src/ui/settings/__tests__/AccentPicker.test.tsx`.
- Hard stops: no new dependencies; no change to the saved-settings storage format; no change to the picker layout.
- Done means: the acceptance criteria below pass and the repo test commands are green.

## ROOM — where the work happens

- Worktree: this directory, branch `worker/412-accent-first-paint`, based on `origin/main`.
- Rules: no commits, pushes, or merges — leave changes uncommitted. Stay inside this workdir. Never touch `.git`. Never delete, move, or rename this worktree. If the issue and the code contradict each other, report the contradiction in your summary instead of guessing.

## PROOF — what evidence to leave behind

`PROOF: type=visual; items=before screenshot of the settings page with the default blue accent, after screenshot of the same page with the saved accent (#c2410c); both PNGs committed under artifacts/412/ in the PR branch, the after one taken from the fixed code.`

- Type: `visual` — the PR body needs before/after material (the screenshot paths, or an explicit gap if the environment could not render the page).
- The worker leaves the material (the screenshot paths and what each one shows); the supervisor assembles the PR's `## Proof` section from it at PR-creation time.
- If an item above cannot be produced — no display, no browser — say so in the report with the reason; the supervisor records it as an explicit `proof gap:` line, it is never silently dropped.

## Acceptance criteria

1. The picker renders the saved accent colour on the first paint (no frame with the default blue).
2. A regression test asserts the first painted colour is the saved accent.
3. The existing settings tests still pass unchanged.

## Verification commands

```bash
npm test -- AccentPicker
npm test
```

## Code discipline

- Understand before changing: read the relevant files first; enumerate ALL occurrences of the flaw with grep before fixing any.
- Verify, don't trust: run the verification commands before finishing; fix failures you introduced.
- Stale-test protocol: if a test fails, prove whether it predates your change — `git stash -u`, re-run the failing test, `git stash pop`. A test that passed before your change and fails after is YOUR regression; never hand-wave it as pre-existing.
- Evidence ranking: the project's own test suite beats new tests written to repo conventions, which beat ad-hoc self-written checks.
- If the task needs no change, quote the exact acceptance/repro commands and their output as proof — "already done" without quoted evidence doesn't count.
