# Example playbook store (seed, plan-coder #184)

These are the 4 fluxion entries drafted in the Phase 0 report for
issue #184 (E1, E2, E4, E5; E3 was excluded as already covered by
fluxion's CONTRACT.md), with lesson and evidence text used verbatim
from that report.

They are **examples, not live state**. The runtime store lives
outside every target repo and is resolved by `bin/playbook.py` from
the deployment config (see that file's docstring). To seed a runtime
store, copy this directory's `fluxion/` folder into the resolved
store directory:

```bash
STORE=$(python3 -c "import sys; sys.path.insert(0,'bin'); import playbook; print(playbook.default_store_path())")
mkdir -p "$STORE"
cp -r examples/playbooks/fluxion "$STORE"/
python3 bin/playbook.py --store "$STORE" list
python3 bin/playbook.py --store "$STORE" match --repo fluxion --keywords cargo,build
python3 bin/playbook.py --store "$STORE" lifecycle
```

New lessons never land here or directly in the runtime store as
active entries: `bin/playbook.py propose …` stages them as pending
for human review, and the lifecycle report archives (never deletes)
entries past 30 days without use.
