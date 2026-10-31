# Auth and network

**Current conclusion.** Auth deferred; loopback only; SSH forwarding for remote use. A39 is **deferred (Alex 2026-10-31)**. There is no warning-and-continue path for a LAN bind.

Bind only `127.0.0.1`, `::1`, or `localhost`. `--host` with any other address exits with an error. To open the UI from another computer:

```
ssh -L 17465:127.0.0.1:17465 <controller>
```

No access tokens. No `Authorization` header. No `#token=` link. No `sessionStorage` token. No token-gate screen (`r-token`). Ignore exec-model v0.2.2 §0.2 token rules and PRD token rows until Alex reopens authentication.

## Why

Alex asked to skip login. A half-implemented token would look like a security feature and would not be one. Binding a non-loopback address without auth would let anyone on that network move the robot. SSH port forwarding keeps the bind on loopback and does not need a token.

## Rejected

- Warning-and-continue on `--host 0.0.0.0` (revoked 2026-10-31).
- Implementing A39 / `r-token` / catalog keys for a token screen in v1.
- Cookie or `localStorage` tokens (browser would attach them to cross-site requests).

## A40 (not login)

Other pages in the same browser can still POST to `127.0.0.1` (simple form POST skips CORS preflight; DNS rebinding can spoof Host). Without these checks a malicious tab can move the robot.

- HTTP `Host` must be `127.0.0.1`, `localhost`, or `[::1]` (port may differ from the bind so SSH forwarding works). Else 403 `bad_host`.
- WebSocket and SSE handshakes require `Origin` to be the page’s own origin. Else 403 `bad_origin`.
- Command methods under `/api/` require `Content-Type: application/json`. Else 415 `bad_content_type`.

`curl` with `Host: evil.example`, `Content-Type: text/plain`, or a WebSocket handshake with a foreign Origin must all be rejected, and FakeAdapter must not send a goal.

## Who / date / spec

Alex + Fadell + Thompson, 2026-10-31.

[PRD §7 browser / token rows](../../docs/prd.md) (token rows ignored), [exec-model §0.2 / A40](../../docs/exec-model.md) (A39 deferred), [UI spec §18 r-token note](../../docs/ui-spec.md).
