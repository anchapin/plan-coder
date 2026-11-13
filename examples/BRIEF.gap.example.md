# Brief: apply the per-axis drag coefficient in the damping solver (issue #1301)

## SCOPE — why, and what is allowed

- Goal: the damping solver applies the drag coefficient to the horizontal axis only; the vertical axis must use its own coefficient.
- Allowed paths: `src/sim/damping.py`, `tests/test_damping.py`.
- Hard stops: no new dependencies; no change to the public `DampingSolver` signature.
- Done means: the acceptance criteria below pass and the repo test commands are green.

## ROOM — where the work happens

- Worktree: this directory, branch `worker/1301-per-axis-drag`, based on `origin/main`.
- Rules: no commits, pushes, or merges — leave changes uncommitted. Stay inside this workdir. Never touch `.git`. Never delete, move, or rename this worktree. If the issue and the code contradict each other, report the contradiction in your summary instead of guessing.

## PROOF — what evidence to leave behind

`PROOF: type=numeric; items=before/after settling time for the case drop_2m and for the case drop_5m, each quoted as seconds to two decimals, with the command that produced the numbers.`

- Type: `numeric` — the PR body needs before/after headline numbers with the case each number comes from.
- The worker leaves the material (the numbers and the producing command); the supervisor assembles the PR's `## Proof` section from it at PR-creation time.
- If an item above cannot be produced — say the baseline run was never captured — say so in the report with the reason; the supervisor records it as an explicit `proof gap:` line, it is never silently dropped.

## Acceptance criteria

1. The vertical axis uses the per-axis drag coefficient; the horizontal axis is unchanged.
2. The settling time for `drop_2m` improves; `drop_5m` behaves as specified.
3. New tests cover both axes; existing tests unchanged and passing.

## Verification commands

```bash
pytest tests/test_damping.py -q
```

## Code discipline

- Understand before changing: read the relevant files first; enumerate ALL occurrences of the flaw with grep before fixing any.
- Verify, don't trust: run the verification commands before finishing; fix failures you introduced.
- Stale-test protocol: if a test fails, prove whether it predates your change — `git stash -u`, re-run the failing test, `git stash pop`. A test that passed before your change and fails after is YOUR regression; never hand-wave it as pre-existing.
- Evidence ranking: the project's own test suite beats new tests written to repo conventions, which beat ad-hoc self-written checks.
- If the task needs no change, quote the exact acceptance/repro commands and their output as proof — "already done" without quoted evidence doesn't count.
