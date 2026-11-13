# Sample PR body — numeric repo (`gh pr create --title "..." --body-file PR_BODY.md`)

Repo: `you/fluxion` · Issue #1234 · PR: https://github.com/you/fluxion/pull/1240

Fixes #1234 — the retry client slept a fixed 1s between attempts, so a flaky backend turned one slow call into a 60s request.

Provider/tier: zai/glm-4.6 (cheap) · tokens: 431,002 in / 18,774 out

## Proof

Claim: retry delays are now exponential with jitter, capped at 30s, and the 429 path still retries.

Files changed (git facts, from the machine manifest — base_commit `5b8f001`, head_commit `c07d9ae`):

| file | +/− |
|---|---|
| `src/http/client.py` | +62 −18 |
| `tests/test_http_client.py` | +94 −3 |

diffstat: 2 files changed, 156 insertions(+), 21 deletions(-)

Verification (re-run by the manager in the worktree):

- `pytest tests/test_http_client.py -q` → PASS (`6 passed in 0.42s`)

Gate triggers: none.

Headline numbers (before → after, with the case each number comes from):

| case | before | after |
|---|---|---|
| attempt 1 → 2 delay, 429 case | 1.00 s (fixed) | 2.31 s (2^1 × jitter 0.5–1.0) |
| attempt 5 → 6 delay, 429 case | 1.00 s (fixed) | 30.00 s (cap, jitter 1.0) |
| total wait over 6 attempts, 429 case | 6.00 s | 46.71 s |
| non-retryable 404, first attempt | 1.00 s (slept, then failed) | 0.00 s (fails immediately) |

Numbers produced by `pytest tests/test_http_client.py -q --benchmark` in the worker's tree and re-checked by the manager; source: `artifacts/1234/backoff-numbers.txt`.
