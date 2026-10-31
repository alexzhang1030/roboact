# No wait node; `precondition_wait_s` instead

**Decided:** There is no seventh control node for “wait until stable”. Blocking actions wait inside the controller (`return_mode = blocking`). Optional `precondition_wait_s` on an action lets the adapter re-read `read_state` and send the goal only when the condition holds; otherwise the step is `precondition_failed` and no goal is sent. People waiting uses Human confirm. Long waits (battery, minutes) are out of v1.

**Why:** Pause and breakpoints sit between nodes. A wait node that became true, then paused, would be stale by the time the next action fired. Putting the check in the action, immediately before the goal, leaves no gap. Control nodes also do not call the adapter; a wait node would break that boundary.

**Who:** Tedrake (the rule), Thompson (exec-model v0.2.1 §2.5.5, A38), Fadell (PRD v1.4.3)

**Date:** 2026-10-31

**Spec:** [PRD §5.2](../prd.md), [exec-model §2.5.5](../exec-model.md)
