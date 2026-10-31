# Execution model

How a run is scheduled. Product copy lives in [prd.md](prd.md).

**Wire format is codes** (`timeout`, `operator_lost`, `adapter_lost`, `interrupted_unknown`, …). The UI maps them. Do not store display strings.

## Disconnect

- No page beat for ~10 s → cancel the current action if its `cancel` says cancellable, then pause `operator_lost`. A non-cancellable or refused cancel runs to end, then pause. Do not start the next.
- Adapter ping fails for ~3 s → pause `adapter_lost`. Continue is disabled until the adapter is back.
- Half-open TCP still accepts SSE writes; liveness is `last_ui_at` (ui-beat + SSE subscribe), not subscriber count.

## Limits

- Step limit default 1000. Node `timeout_s` is an absolute deadline for the adapter; Python also enforces it.
- `motion` timeout → `interrupted_unknown` + read-back; no auto-retry.
- Optional `precondition_wait_s`: adapter re-reads `read_state` and sends only when the check holds; else `precondition_failed`, no goal.
- `side_effect` default is `motion`. `write` is test-only.
- Stop = hold position, motors powered. Not e-stop.
- After process crash: run paused (`restart`). In-flight `motion` stays `interrupted_unknown` until Redo / Mark done.

v1 is `FakeAdapter`. R1–R8 (real robot) are N/A until a vendor adapter exists.
