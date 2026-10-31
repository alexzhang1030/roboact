# Settings split

**Decided:** Display preferences live in the browser. Execution and safety stay on the server.

Seven UI keys, all `roboact.*`. Missing or bad values fall back as in the PRD table. A second tab follows via the `storage` event. Follow-browser for language deletes `roboact.lang` (stored values are only `en` / `zh-CN`). Theme and motion may store `system`.

| Key | Values | Default |
|---|---|---|
| `roboact.lang` | `en` \| `zh-CN` | `navigator.languages` (`zh*` → Chinese, else English) |
| `roboact.theme` | `system` \| `light` \| `dark` | `prefers-color-scheme` |
| `roboact.motion` | `system` \| `reduce` \| `full` | `prefers-reduced-motion` (off → full) |
| `roboact.viewport.<flow id>` | `{x,y,zoom}` | fit view |
| `roboact.panels` | `{leftOpen, rightW}` | spec widths |
| `roboact.grid` | `0` \| `1` | on |
| `roboact.logFilter` | `all` \| `error` | all |

Server only: 10 s page-disconnect, 3 s adapter silence, `timeout_s`, `precondition_wait_s`, expiry, adapter, flow, step limit, `--host`. Those must not appear in `localStorage`.

No access-token key. Authentication is deferred (see [auth-deferred.md](auth-deferred.md)).

**Why:** Two tabs must not invent two pause rules for one run. Theme, language, and motion are per-operator.

**Who:** Alex, Steve, Thompson

**Date:** 2026-10-31

**Spec:** [PRD v1.4.8 settings table](../prd.md), [PRD 19.6 / 19.7](../prd.md), [UI spec §18.1](../ui-spec.md)
