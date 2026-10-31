# Settings split

**Decided:** Display preferences live in the browser. Execution and safety stay on the server.

Browser, first from the platform then `localStorage` once the operator chooses, each with a follow-browser reset: theme (`prefers-color-scheme`), language (`navigator.languages`), reduced motion (`prefers-reduced-motion`). Keys: `roboact.theme`, `roboact.lang`, `roboact.motion`.

Browser only, no platform default: panel widths and collapse, grid / minimap, last flow id, canvas pan and zoom (`roboact.leftOpen`, `roboact.rightW`, `roboact.grid`, `roboact.lastFlow`, `roboact.canvas`).

Server only, shared by every tab, written into the run: 10 s page-disconnect pause, 3 s adapter silence, step limit, timeouts, `--host`. A second tab that changes a pause deadline must see the server value; that key must not appear in `localStorage`.

Until Thompson answers, a non-loopback access token (if one exists) is `sessionStorage` only, key `roboact.token`. Do not persist it. Every `localStorage` / `sessionStorage` key uses the `roboact.` prefix; helpers refuse any other key.

A bad or unknown stored value is dropped and the browser default is used. No error toast.

**Why:** Two tabs must not invent two pause rules for one run. Theme, language, and motion are per-operator and belong next to the page.

**Who:** Alex (browser first, then storage), Steve, Thompson (server-side safety)

**Date:** 2026-10-31

**Spec:** [PRD §5 settings table](../prd.md), [PRD 19.6 / 19.7](../prd.md), [UI spec §17 / §18](../ui-spec.md)
