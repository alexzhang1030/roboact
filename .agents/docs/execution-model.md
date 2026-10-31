# Execution model

See [docs/exec-model.md](../../docs/exec-model.md).

- Page gone ~10 s → `operator_lost` after the current node. The 10 s clock is `last_ui_at` (ui-beat + SSE subscribe), checked at node boundaries. The in-flight node is not cancelled, so a fixture whose remaining motion after the TCP cut is 11.2 s (`28 × 0.4 s` in the old A6 half-open setup) pauses at ~11.25 s. Remaining motion after the cut must be ≤ 10 s, and the node must still outlast `last_ui_at + 10 s` so the next node does not start.
- Adapter silent ~3 s → `adapter_lost`.
- Codes on the wire. `motion` default. No auto-retry after crash.
