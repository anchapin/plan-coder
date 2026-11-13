# Sample PR body — visual repo (`gh pr create --title "..." --body-file PR_BODY.md`)

Repo: `you/atlas-ui` · Issue #412 · PR: https://github.com/you/atlas-ui/pull/417

Fixes #412 — the settings page painted the default blue accent for one frame before applying the saved colour.

Provider/tier: minmax/MiniMax-M2.7 (balanced) · tokens: 812,340 in / 40,211 out

## Proof

Claim: the settings page now applies the saved accent colour on first paint, not after a frame of default blue.

Files changed (git facts, from the machine manifest — base_commit `91f0c2a`, head_commit `3d5ae17`):

| file | +/− |
|---|---|
| `src/ui/settings/AccentPicker.tsx` | +48 −12 |
| `src/ui/settings/__tests__/AccentPicker.test.tsx` | +31 −4 |

diffstat: 2 files changed, 79 insertions(+), 16 deletions(-)

Verification (re-run by the manager in the worktree, not taken from the worker's report):

- `npm test -- AccentPicker` → PASS (`Tests: 1 passed, 1 total`)

Gate triggers: none.

Before/after (visual repo — the committed screenshots):

- before: `artifacts/412/accent-before.png` — picker renders the default blue accent
- after: `artifacts/412/accent-after.png` — picker renders the saved accent (#c2410c)
