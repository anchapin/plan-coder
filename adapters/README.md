# Host adapters

This directory holds plan-coder's **host adapters**: the thin plumbing
that lets the same manager run on different agent hosts.

The normative document is [`CONTRACT.md`](CONTRACT.md) — what a host must
provide, and what an adapter may and may not do. This file is the
orientation: how the tree is laid out, how to run the generic adapter,
and how three specific hosts map onto the contract (**sketches only** —
none of these adapters is built in this phase).

> **Adapters differ in scheduling and approval plumbing ONLY.** Merge
> policy, brief discipline, verification gates and hard stops live in
> [`docs/OPERATIONS.md`](../docs/OPERATIONS.md),
> [`docs/MANAGER.md`](../docs/MANAGER.md) and
> [`prompts/`](../prompts/) — identical for every host. An adapter that
> restates a rule is a defect, not a variation.

## Layout

```
adapters/
  CONTRACT.md          the host-adapter contract (normative)
  README.md            this file
  generic/
    supervisor.sh      thin wrapper: one supervisor cycle
    shepherd.sh       thin wrapper: one shepherd pass
```

## The generic adapter

`adapters/generic/` is the reference implementation: two POSIX-shell
wrappers that resolve the manager command and the rendered prompt from the
environment, run the manager command, and exit with its status. They
contain no policy text, no deployment values and no credentials.

### Configuration

| Variable | Required | Meaning |
|---|---|---|
| `PLAN_CODER_MANAGER_CMD` | yes | The command that runs the plan-coder manager. |
| `PLAN_CODER_SUPERVISOR_PROMPT` | no | Rendered supervisor prompt path (final argument of the manager command). Empty value = CLI mode. Unset = use `local/backlog-supervisor.rendered.md` if it exists. |
| `PLAN_CODER_SHEPHERD_PROMPT` | no | Rendered shepherd prompt path. Empty value = CLI mode. Unset = use `local/pr-shepherd.rendered.md` if it exists. |

Missing configuration is a **loud failure**: the wrapper prints a
message naming the variable and exits `2`. A failing manager command's
status is propagated unchanged.

### Running it

Render the prompts first if you want prompt mode — the rendered bodies
are the output of `python3 bin/install_crons.py` (gitignored, never
edited by hand):

```bash
# Prompt mode: the host's own CLI takes the rendered prompt path.
export PLAN_CODER_MANAGER_CMD="<host cli> --prompt"
export PLAN_CODER_SUPERVISOR_PROMPT=local/backlog-supervisor.rendered.md
adapters/generic/supervisor.sh

# CLI mode: the read-only manager CLI answers on its own.
export PLAN_CODER_MANAGER_CMD="python3 bin/plan_coder.py next-actions --json"
export PLAN_CODER_SUPERVISOR_PROMPT=
adapters/generic/supervisor.sh
```

The shepherd wrapper is the same, with
`PLAN_CODER_SHEPHERD_PROMPT` and a `shepherd-check` / `next-actions`
manager command.

### Proof

`tests/test_generic_adapters.py` is the stub-driven smoke test: a stub
"manager" records the arguments it was called with and exits with a
chosen status. It pins down the invocation, the prompt argument (and its
absence in CLI mode), the loud failure on missing configuration, the
propagation of a failing manager command, and the hygiene of the adapter
files (no secret shapes, no operator-specific paths).

```bash
python3 -m pytest tests/test_generic_adapters.py -q
```

The end-to-end live-cycle gate (a supervisor cycle and a shepherd cycle
run by a fresh manager on a test deployment) is **not** part of this
phase — it is the operator's step after this change lands.

---

## Host mapping sketches (docs only — no adapter is built)

Each sketch answers the contract's four questions for one host: how the
host invokes a cycle, where next-actions and escalations go, how an
approval comes back, and how the next cycle is triggered.

### Muse (reference host)

The host the system was built against: a cron-style orchestrator that
runs a saved prompt body on an interval and delivers the run's output to
a chat channel, with the human replying in the same channel.

| Contract requirement | Mapping |
|---|---|
| Periodic invoker | One cron entry per role. The saved body is the **rendered** prompt (`bin/install_crons.py` renders the templates and writes the manifest; the body the host saves must be byte-identical to the rendered file). Cadence and title come from the deployment config's `crons` section — the adapter never carries them. |
| Delivery to the human | The orchestrator's delivery channel for the run's output: the next-actions list and any escalation land in the channel the deployment config names. |
| Approval back | A reply in that channel. The next cycle reads the channel and treats a labelled approval as input — the content and the rules for what counts as an approval are policy (`docs/OPERATIONS.md`), not adapter behaviour. |
| Next-cycle trigger | **Heartbeat-only**: cron entries fire on time; no event triggers a cycle. The adapter says so explicitly, so nobody waits for an event that never comes. |
| Secret injection | The orchestrator injects the provider keys into the run's environment from the host's secret store; the adapter never sees or writes them. |
| Adapter shape | The generic wrappers, with `PLAN_CODER_MANAGER_CMD` pointing at the orchestrator's own "run this prompt" entry point. No Muse-specific code is needed for Phase 0. |

### Claude Code

A session-shaped host: work happens in a session, the human is in the
loop in the same session, and the model is invoked through the host's
own CLI.

| Contract requirement | Mapping |
|---|---|
| Periodic invoker | The host's own scheduler or an external timer calls the adapter's entry point. A headless invocation (no interactive human) is the normal case for the supervisor cycle; the shepherd cycle may run in a session. |
| Delivery to the human | The session's own output channel — the next-actions list and escalations are printed to the session, and the session's transcript is the durable record. |
| Approval back | An in-session decision (the human answers in the session) or a file/label-based decision the next session reads. Which one applies is a deployment choice; the round trip must survive the session ending. |
| Next-cycle trigger | Event-driven where the host supports it (a new PR comment can start a shepherd pass); otherwise **heartbeat-only**, stated explicitly. |
| Secret injection | The provider key is exported into the session's environment by the host's own configuration; it is never written into a prompt, a brief or a log line. |
| Adapter shape | The generic wrappers, with `PLAN_CODER_MANAGER_CMD` pointing at the host CLI in prompt mode. |

### OpenClaw / Hermes

Another host family with its own trigger relay: it can be woken by an
event rather than only by a timer, and it delivers to the human through
its own channel.

| Contract requirement | Mapping |
|---|---|
| Periodic invoker | The host's timer for the ~2h supervisor cadence and the ~30m shepherd cadence (values from the deployment config). |
| Delivery to the human | The host's channel for the next-actions list and escalations. |
| Approval back | The host's reply path, surfaced to the next cycle. As with every host, the *meaning* of an approval is policy. |
| Next-cycle trigger | Event-triggered: the host can wake a cycle when the tracked PRs change. The adapter must say which events it wires, so the operator knows what will and will not start a cycle. |
| Secret injection | Host-side secret store → environment of the invoked process. |
| Adapter shape | The generic wrappers again, plus (later) a small host-specific mapping file — the mapping is data about the host's mechanics, not policy. |

---

## Deferred (not in this phase)

- **Building** the Claude Code, OpenClaw/Hermes or any other
  host-specific adapter. This phase ships the contract and the generic
  shell adapter as the proof; the sketches above are documentation.
- **The MCP server** (Layer 4).
- **The end-to-end live-cycle gate** — a supervisor cycle plus a
  shepherd cycle run by a fresh manager on a test deployment. The
  operator runs that after this change lands; the proof here is the
  stub-driven smoke test.

## See also

- [`adapters/CONTRACT.md`](CONTRACT.md) — the contract.
- [`docs/OPERATIONS.md`](../docs/OPERATIONS.md) — the operating policy
  (the rules an adapter must not fork).
- [`docs/MANAGER.md`](../docs/MANAGER.md) — the host-neutral manager
  checklist.
- [`AGENTS.md`](../AGENTS.md) — roles, run lifecycle, never-dos.
