# Execution model

See [docs/exec-model.md](../../docs/exec-model.md).

- Page gone ~10 s → cancel the current action through the adapter if its `cancel` says cancellable, then pause `operator_lost`. A non-cancellable or refused cancel runs to end, then pause; refused cancel shows `status.cancel_rejected`. The 10 s clock is `last_ui_at` (ui-beat + SSE subscribe), checked while a node is executing, not only between nodes. Worst case is 10 s plus the remaining time of one non-cancellable action (PRD 14). Shortening a test fixture so the old between-node check meets 10 s does not close A6.
- A5 pause latency is the server write of paused state → first browser frame after the `paused` SSE event (acceptance 19.2). Not from the click, not from the browser receiving the event.
- Adapter silent ~3 s → `adapter_lost`.
- Codes on the wire. `motion` default. No auto-retry after crash.
- Non-cancellable `motion` with `timeout_s` > 10 s blocks Run (checked before every run).
- An in-flight `operator_lost` pause includes `lost_outcome` (`cancelled`, `ran_to_end`, or `cancel_rejected`). `ran_to_end` also includes `lost_extra_s`: seconds from the moment the page was judged lost until that action finished. The node stays `stopped`, `done`, or `cancel_rejected` for those three outcomes. A pause between nodes has no `lost_outcome`.

