# System design

One Python process serves the static UI and the HTTP/SSE API on `127.0.0.1`. The executor runs one node at a time. The adapter talks to the controller (v1: in-process `FakeAdapter`).

```
browser  --HTTP/SSE-->  roboact server  --Adapter-->  controller
                         executor + SQLite
```

Pause and breakpoints apply **between** nodes. In-flight motion is not cancelled on page loss. See [exec-model.md](exec-model.md) for disconnect and limits.

Bind is loopback only. Remote use is SSH port-forward. Host / Origin / Content-Type checks are on.
