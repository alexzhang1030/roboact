# Settings split

**Current conclusion.** Only display preferences live in the browser. Execution and safety stay on the server. Seven `roboact.*` keys (PRD v1.4.8).

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

10 s page-disconnect pause, 3 s adapter silence, node `timeout_s`, `precondition_wait_s`, expiry, adapter identity, flow graph, step limit, `--host`. Those are not mirrored into `localStorage`.
