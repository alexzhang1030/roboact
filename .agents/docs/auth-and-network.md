# Auth and network

**Current conclusion.** No authentication. A39 is **deferred (Alex 2026-10-31)**. Default bind is `127.0.0.1`. `--host` on a non-loopback address **warns** that there is no authentication and anyone on that network can control the robot; it does not exit.

## A39 deferred

Do not implement access tokens. Ignore exec-model v0.2.2 §0.2 token rules, PRD token rows, and the `r-token` design sheets. There is no `ROBOACT_TOKEN`, no `--token`, no `sessionStorage` token, no token-gate screen.

When Alex reopens authentication, write the new rule here and then implement.

## A40 (not login)

- HTTP `Host` must be `127.0.0.1`, `localhost`, or `[::1]`, with the bind port when a port is present. Else 403 `bad_host`.
- WebSocket and SSE handshakes require `Origin` to be the page’s own origin. Else 403 `bad_origin`.
- Command methods under `/api/` require `Content-Type: application/json`. Else 415 `bad_content_type`.
