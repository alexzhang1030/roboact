# Settings split

**Current conclusion.** Only display preferences live in the browser. Execution and safety stay on the server.

## Browser (`localStorage`, prefix `roboact.`)

| Key | Values | Default |
|---|---|---|
| `roboact.theme` | `system` \| `light` \| `dark` | `system` (resolved from `prefers-color-scheme`) |
| `roboact.lang` | `system` \| `en` \| `zh-CN` | `system` (resolved by `detectBrowserLang`) |
| `roboact.motion` | `system` \| `reduce` \| `full` | `system` (resolved from `prefers-reduced-motion`) |
| `roboact.leftOpen` | `0` \| `1` | open |
| `roboact.rightW` | panel width px | CSS default |
| `roboact.grid` | minimap / grid pref | on |
| `roboact.lastFlow` | last flow id | none |
| `roboact.canvas` | zoom / position | Vue Flow default |

Clearing site storage resets those to browser defaults. It does **not** change `timeout_s`, `precondition_wait_s`, step limit, adapter heartbeat, page-disconnect deadline, expiry, or the flow on disk.

`sessionStorage` is not used for an access token.

## Server

10 s page-disconnect pause, 3 s adapter silence, node `timeout_s`, `precondition_wait_s`, expiry, adapter identity, flow graph, step limit. Those are not mirrored into `localStorage`.
