# Auth and network

**Current conclusion.** Bind loopback only. No authentication. A39 is deferred (Alex 2026-10-31). A40 is an acceptance check (Host / Origin / Content-Type).

## Bind

`--host` accepts only `127.0.0.1`, `::1`, and `localhost`. Anything else (`0.0.0.0`, a LAN IP) **exits**. There is no warning-and-continue path.

Remote use is SSH port forwarding, not opening the bind:

```bash
ssh -L 17465:127.0.0.1:17465 <controller>
```

## A40

- HTTP `Host` must be `127.0.0.1`, `localhost`, or `[::1]`, with the bind port when a port is present. Else 403 `bad_host`.
- WebSocket and SSE handshakes require `Origin` to be the page’s own origin. Else 403 `bad_origin`.
- Command methods (`POST` `PUT` `PATCH` `DELETE`) under `/api/` require `Content-Type: application/json`. Else 415 `bad_content_type`.

A rejected request must not move the fake robot. Tests: `tests/test_a40_host_origin.py`.

## A39 deferred

Do not implement access tokens. Ignore exec-model token rules, PRD token rows, and the `r-token` design sheets. There is no `ROBOACT_TOKEN`, no `--token`, no `sessionStorage` token, no token-gate screen. `docs/ui-spec.md` §16.z still mentions a 401 token field; that line is stale relative to this record.

When Alex reopens authentication, write the new rule here and then implement.
