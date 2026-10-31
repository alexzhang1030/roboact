# Execution model

**Current conclusion.** Product docs in this tree: [exec-model v0.2.3](../../docs/exec-model.md), [PRD v1.4.10](../../docs/prd.md). Later Alex notes (loopback only, no `--host`, no auth, codes-only, fonts, toolchain) override those docs where they conflict.

There is no wait node. Optional `precondition_wait_s` on an action lets the adapter re-read `read_state` and send the goal only when the condition holds; otherwise the step is `precondition_failed` and no goal is sent. People waiting uses Human confirm.

## Codes on the wire

Executor, adapter, and API messages are codes: `timeout`, `precondition_failed`, `cancelled`, `crash`, `write_timeout`, `motion_timeout`, `cancel_rejected`, `no_result`. The UI maps them. Do not put Chinese (or English display copy) in `word` / `message` fields that get stored or pushed.

## Safety defaults (server)

- Page SSE gone ~10 s → pause `operator_lost`. Current node is not cancelled.
- Adapter silent ~3 s → pause `adapter_lost`. Continue stays disabled until the adapter is back. The adapter base cancels in-flight goals when *its* heartbeat dies.
- Node `timeout_s` is an absolute deadline given to the adapter; Python also enforces it. `motion` timeout → `interrupted_unknown` + read-back; no auto-retry.
- Optional `precondition_wait_s`: adapter re-reads `read_state` and sends only when the condition holds; else `precondition_failed`, no goal. One run record per step.
- `side_effect` default is `motion` when omitted. `write` is test-only (`append_note`).
- Stop is hold-position, motors powered. Not e-stop. No e-stop control on the canvas.
- After process crash / restart: run stays paused (`restart`); in-flight `motion` is `interrupted_unknown` and waits for Redo / Mark done.

R1–R8 stay N/A until a real robot. FakeAdapter must not move when A40 rejects a request.

Canvas library: Vue Flow 1.48.2, zero-build native ES modules. React Flow is the fallback. LiteGraph is out.

## Why

Pause and breakpoints sit between nodes. A wait node that became true, then paused, would be stale by the time the next action fired. Putting the check in the action, immediately before the goal, leaves no gap. Codes survive a language switch and a crash.

## Rejected

- A seventh control node for “wait until stable”.
- Auto-retry of `motion` after crash.
- Storing display strings in run records.

## Who / date / spec

Tedrake (wait rule), Thompson (exec-model v0.2.1 §2.5.5, A38; v0.2.3 A40), Fadell (PRD), Alex (zero-build / offline), Steve (no bundler), 2026-10-31.

[PRD §5.2](../../docs/prd.md), [exec-model §2.5.5 / §0.2](../../docs/exec-model.md), [flow-lib eval](../../docs/flow-lib-eval-2026-10-31.md).
