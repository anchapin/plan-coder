# Shepherd actions

The shepherd appends one row per action to its ledger. The action
vocabulary includes needs_operator, merged_by_shepherd, rerun and
closed_unmerged.

The needs_operator action puts a question in front of the operator; it
never blocks a merge on its own.
