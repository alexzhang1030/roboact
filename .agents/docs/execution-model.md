# Execution model

**Current conclusion.** The product doc in this tree is exec-model v0.2.1 / PRD v1.4.4. Later Alex notes (loopback, no auth, codes-only, fonts) override those docs where they conflict. Do not invent missing PRD §18 / 19.6–19.9 prose; implement the stated rules and keep the tests.

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
