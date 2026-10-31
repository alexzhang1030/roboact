# Execution model

See [docs/exec-model.md](../../docs/exec-model.md).

- Page gone ~10 s → `operator_lost` after the current node.
- Adapter silent ~3 s → `adapter_lost`.
- Codes on the wire. `motion` default. No auto-retry after crash.
