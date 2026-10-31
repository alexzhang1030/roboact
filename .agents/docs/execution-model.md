# Execution model

See [docs/exec-model.md](../../docs/exec-model.md).

- Page gone ~10 s → `operator_lost` after the current node. The 10 s clock is `last_ui_at` (ui-beat + SSE subscribe), checked at node boundaries. The in-flight node is not cancelled, so a fixture whose remaining motion after the TCP cut is 11.2 s (`28 × 0.4 s` in the old A6 half-open setup) pauses at ~11.25 s. Remaining motion after the cut must be ≤ 10 s, and the node must still outlast `last_ui_at + 10 s` so the next node does not start.
- A5 pause latency is the first frame after the `paused` SSE event (acceptance 19.2), not click → label. Click includes HTTP, the in-flight node, and queued `nodes` batches from a 500-node run; that is why a click-frame count can hit 35–54 under CI load while event→frame stays 1.
- Adapter silent ~3 s → `adapter_lost`.
- Codes on the wire. `motion` default. No auto-retry after crash.
