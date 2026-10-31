# Storage and API carry codes only

**Decided:** SQLite run records, exported flows, HTTP, and SSE carry status and error **codes** (`interrupted_unknown`, `waiting_precondition`, `precondition_failed`, `operator_lost`, `adapter_lost`, …). They never store display strings. The UI maps a code to the active locale.

**Why:** After a crash, the executor must know whether to read the robot back and wait for Redo / Mark done. A Chinese or English sentence in the record cannot be compared. Switching language on an old run must relabel the same code.

**Who:** Thompson (storage), Steve and Tedrake (labels vs name vs id for adapter titles)

**Date:** 2026-10-31

**Spec:** [PRD §5 language](../prd.md), [PRD 19.6](../prd.md), [exec-model](../exec-model.md)
