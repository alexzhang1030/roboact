# Settings split

**Current conclusion.** Only display preferences live in the browser. Execution and safety stay on the server. Seven `roboact.*` keys.

| Key | Values | Default |
|---|---|---|
| `roboact.lang` | `en` \| `zh-CN` | `detectBrowserLang` (follow-browser deletes the key) |
| `roboact.theme` | `system` \| `light` \| `dark` | `prefers-color-scheme` |
| `roboact.motion` | `system` \| `reduce` \| `full` | `prefers-reduced-motion` |
| `roboact.viewport.<flow id>` | `{x,y,zoom}` | fit view |
| `roboact.panels` | `{leftOpen, rightW}` | spec widths |
| `roboact.grid` | `0` \| `1` | on |
| `roboact.logFilter` | `all` \| `error` | all |

No token key. Helpers refuse any key that does not start with `roboact.`.

## Server

10 s page-disconnect pause, 3 s adapter silence, node `timeout_s`, `precondition_wait_s`, expiry, adapter identity, flow graph, step limit, bind address. Those are not mirrored into `localStorage`. Bind is loopback only (see [auth-and-network.md](auth-and-network.md)).

## Why

Two tabs must not invent two pause rules for one run. Theme, language, and motion are per-operator. The page can close while a run keeps going; the executor cannot read a browser value.

## Rejected

- Storing timeouts or adapter identity in `localStorage`.
- A token key in the browser catalog.

## Who / date / spec

Alex, Steve, Thompson, 2026-10-31. [PRD settings table](../../docs/prd.md), [PRD 19.6 / 19.7](../../docs/prd.md), [UI spec §18.1](../../docs/ui-spec.md).
