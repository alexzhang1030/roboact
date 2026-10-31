# Authentication deferred

**Decided:** Skip authentication for v1. Do not implement access tokens, `Authorization` headers, `#token=` links, a token-gate screen, or any `sessionStorage` token. Ignore exec-model v0.2.2 §0.2 token rules, PRD token rows, and acceptance A39. A39 is **deferred (Alex 2026-10-31)**, not a pass.

Default bind remains `127.0.0.1`. `--host` on a non-loopback address prints a warning that there is no authentication and anyone on that network can control the robot; it does not exit.

**Why:** Alex asked to skip login until the bind and remote-use question is settled. A half-implemented token would look like a security feature and would not be one.

**Who:** Alex

**Date:** 2026-10-31

**Spec:** [PRD §5 token row / A39](../prd.md) (ignored), [exec-model §0.2](../exec-model.md) (ignored)
