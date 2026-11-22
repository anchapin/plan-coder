# Same-signature failure escalation — Phase 0 report (#148)

Phase 0 is measurement only. This report covers the measurement tool
(`bin/samesig_phase0.py`, reusing `failure_signature()` from
`bin/tokeneconomizer_phase0.py`) and its validation on synthetic fixtures.
**Real-corpus numbers are pending**: the real transcripts directory lives
outside the worker sandbox, so the worker did not read it. The coordinator
run that produces the real Phase 0 numbers is:

```bash
python3 bin/samesig_phase0.py \
  --transcripts ~/workspace/goals/token-plan-coding-manager-setup/hidden_files/transcripts
# add --json for the machine-readable payload
```

No prompt change, worker-behaviour change, or enablement is proposed here;
Phase 1 is a separate tap (issue #148, Gates section).

## What the tool measures

For each transcript in the corpus it groups failing tool results by their
normalised failure signature (the tokeneconomizer signature, imported, not
reimplemented) and reports:

- **Q1** — how often the same signature occurs 2+ times within one run
  (runs with a recurrence, recurring groups, occurrence histogram).
- **Q2** — for runs with a recurrence: failing attempts and tokens spent
  after the second occurrence, and whether the run eventually succeeded.
- **Q3** — false-positive shape per recurring group: `symptom_loop`
  (same command, same failure, no relevant edit), `progress_after_edit`
  (a relevant file was edited between occurrences — a re-test),
  `env_noise` (transient environment signature), `benign_other`
  (different commands collapsed onto the same normalised text).

## Validation on synthetic fixtures

Six synthetic transcripts with known ground truth (a 3x symptom loop that
then succeeds, a 2x environment-noise repeat, a re-test after an edit, a
different-commands/same-text pair, a no-failure run, and a 2x symptom loop
that never succeeds) were generated and measured. Supervisor re-run of the
worker's script against that corpus, 2026-11-22:

| question | measured | ground truth |
|---|---|---|
| runs scanned | 6 | 6 |
| runs with >=1 recurrence | 5 (83.3%) | 5 |
| recurring signature groups | 5 | 5 |
| occurrence histogram | 2 occ -> 4 groups, 3 occ -> 1 group | matches |
| attempts after 2nd (total) | 2 | 2 (the 3x loop run) |
| tokens after 2nd (total) | 880 | 880 (same run) |
| runs that eventually succeeded | 4 of 5 | 4 of 5 |
| Q3 classes | symptom_loop 2, progress_after_edit 1, env_noise 1, benign_other 1 | matches construction |

Unit tests: `python3 -m pytest tests/test_samesig_phase0.py -q` ->
23 passed (includes a test that the signature function IS the
tokeneconomizer one, imported by identity).

## Kill criteria — status

Not evaluable until the coordinator run on the real corpus lands: whether
recurrence is rare, whether recurring runs almost always self-recover
quickly, and whether normalisation is too coarse are properties of the
real data, not of the synthetic fixtures. The Q3 class split exists
precisely so the real run can answer the third one.
