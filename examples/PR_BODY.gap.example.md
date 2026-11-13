# Sample PR body — a brief-required proof item is missing (worked gap example)

Repo: `you/repo-a` · Issue #1301 · PR: https://github.com/you/repo-a/pull/1307

Fixes #1301 — the damping solver ignored the new per-axis drag coefficient.

Provider/tier: zai/glm-4.6 (balanced) · tokens: 502,118 in / 22,905 out

## Proof

Claim: the damping solver applies the per-axis drag coefficient to the vertical axis as well.

Files changed (git facts, from the machine manifest — base_commit `5b8f001`, head_commit `11ab0c4`):

| file | +/− |
|---|---|
| `src/sim/damping.py` | +44 −9 |

diffstat: 1 file changed, 44 insertions(+), 9 deletions(-)

Verification (re-run by the manager in the worktree):

- `pytest tests/test_damping.py -q` → PASS (`11 passed in 1.83s`)

Gate triggers: none.

Headline numbers (before → after, with the case each number comes from):

| case | before | after |
|---|---|---|
| vertical-axis settling time, case `drop_2m` | 4.81 s | 2.02 s |
| horizontal-axis settling time, case `drop_2m` | 4.79 s | 4.81 s (unchanged, as intended) |

proof gap: before numbers for the case `drop_5m` (not available: no baseline run was captured in the worktree)

The brief's `PROOF:` block required before/after numbers for the cases `drop_2m` and `drop_5m`; only `drop_2m` could be produced, and the gap is stated here rather than silently dropped. The gap is recorded on the receipt (`proof: gaps`) and reported by `bin/receipt.py summary`. It is not a merge gate: the PR still opens and parks for the operator.
