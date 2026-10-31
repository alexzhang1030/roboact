# Bind

Listen on `127.0.0.1` only. No `--host`. No auth. Remote use: SSH port-forward.

Reject `Host` outside `127.0.0.1` / `localhost` / `[::1]`. SSE/WebSocket `Origin` must be this page. Commands need `Content-Type: application/json`.
