# Ledger discipline

The worker ledger is append-only: every row is a new line, fsync'd
before the merge decision is recorded, and no row is ever rewritten.

The shepherd ledger is append-only for the same reason: a rewrite would
destroy the audit trail that the weekly harness-tune pass reads.

The ledger lives beside the transcripts in the state dir; the ledger
path is always given on the command line, never guessed.
