# Brief: fix retry backoff in the HTTP client (issue #123)

## SCOPE — why, and what is allowed

- Goal: the HTTP client's retry logic currently sleeps a fixed 1s between attempts. Make it exponential backoff with jitter, capped at 30s, per the acceptance criteria on issue #123.
- Allowed paths: `src/http/client.py`, `tests/test_http_client.py`.
- Hard stops: do not change the public API of `Client.request`; do not add dependencies.
- Done means: acceptance criteria below pass and the repo test commands are green.

## ROOM — where the work happens

- Worktree: this directory, branch `worker/123-retry-backoff`, based on `origin/main`.
- Rules: no commits, pushes, or merges — leave changes uncommitted. Stay inside this workdir. Never touch `.git`. Never delete, move, or rename this worktree. If the issue and the code contradict each other, report the contradiction in your summary instead of guessing.

## Acceptance criteria

1. Retry delays follow `min(30, 2^n) * jitter(0.5–1.0)` seconds for attempt `n`.
2. Non-retryable status codes (4xx other than 429) still fail immediately.
3. New tests cover the cap and the 429 path; existing tests unchanged and passing.

## Verification commands

```bash
pytest tests/test_http_client.py -q
pytest -q   # full suite, if it runs in reasonable time
```

## Code discipline

- Understand before changing: read the relevant files first; enumerate ALL occurrences of the flaw with grep before fixing any.
- Verify, don't trust: run the verification commands before finishing; fix failures you introduced.
- Stale-test protocol: if a test fails, prove whether it predates your change — `git stash -u`, re-run the failing test, `git stash pop`. A test that passed before your change and fails after is YOUR regression; never hand-wave it as pre-existing.
- Evidence ranking: the project's own test suite beats new tests written to repo conventions, which beat ad-hoc self-written checks.
- If the task turns out to need no change, quote the exact acceptance/repro commands and their output as proof — "already done" without quoted evidence doesn't count.
