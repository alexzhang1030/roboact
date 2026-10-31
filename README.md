<img src="assets/logo.svg" alt="Roboact" width="96">

# Roboact

Offline, zero-build robot flow canvas. Wire the atomic actions an adapter exposes into a loopable flow, then debug with breakpoints, single-step, human confirm, and a run log. The canvas orchestrates an embodied agent (a robot), not a chat LLM.

- Backend: Python asyncio. Run records default to SQLite (`Store`; tests may use the in-memory implementation).
- Frontend: Vue 3 + Vue Flow 1.48.2, **zero build** (native ES modules + import map).
- Adapter: one interface in front of an SDK or ROS 2. v1 ships `FakeAdapter` only (`target=sim`).
- **Offline**: frontend, icons, and fonts live in the repo; Python deps are local wheels. Latin UI and digits use Inter; code, parameter values, times, and step counts use JetBrains Mono (both in `roboact/static/fonts/`, SIL OFL 1.1). CJK falls back to system PingFang SC / Microsoft YaHei / Noto Sans CJK SC — not bundled.

Roboact is licensed under the **MIT** License (`LICENSE`, Copyright (c) 2026 alexzhang1030). Third-party texts stay under `vendor/licenses/`. This repo is not published to PyPI and has no publish config.

Specs in this tree: [PRD v1.4.15](docs/prd.md), [exec-model v0.2.3](docs/exec-model.md) (token §0.2 / A39 ignored — auth deferred), [system-design](docs/system-design.md), [UI spec](docs/ui-spec.md) (**§17 fonts/themes, §18 language**), [Rams design sheets](docs/design/), [diagrams](docs/diagrams/). History: `docs/prd-v1.md`, `docs/exec-model-v0.md`, `docs/ui-spec-v0.md`, `docs/system-design-v0.md`. Decision records are PCR in [`.agents/docs/`](.agents/docs/).

## Install and run

Needs **CPython 3.12**. The robot controller (Jetson Thor, aarch64) runs ROS 2 Jazzy on Ubuntu 24.04; system Python is 3.12 ([REP 2000](https://www.ros.org/reps/rep-2000.html)). `rclpy` is **not** a dependency — it comes from apt with ROS. v1 is `FakeAdapter` only; the core package must never import `rclpy`.

```bash
uv sync --frozen
uv run python -m roboact
```

Open <http://127.0.0.1:7465>. No Node, no `npm`, no bundle step.

On the controller, create the venv from the ROS system Python so a later ROS adapter can see `rclpy`:

```bash
uv venv --python /usr/bin/python3 --system-site-packages
uv sync --frozen
```

Laptop / CI without ROS: ordinary `uv sync --frozen` is enough. Offline laptop with the vendored wheels:

```bash
uv sync --frozen --find-links vendor/wheels
# or: pip install --no-index --find-links vendor/wheels -r requirements.txt
```

```bash
uv run python -m roboact --port 7465 --db data/roboact.db
```

Bind is `127.0.0.1` only. There is no `--host` flag. There is no access token and no login screen. A39 is **deferred (Alex 2026-10-31)** — see [.agents/docs/auth-and-network.md](.agents/docs/auth-and-network.md).

To use the UI from another computer, SSH port-forward. Do not bind a LAN address:

```bash
ssh -L 17465:127.0.0.1:17465 <controller>
```

Then start the server with `--port 17465` (or forward the default `7465` the same way).

HTTP requests whose `Host` is not `127.0.0.1`, `localhost`, or `[::1]` (with a port) are rejected. WebSocket and SSE handshakes require `Origin` to be the page’s own origin. Command endpoints accept only `Content-Type: application/json`.

Defaults: step limit **1000**, node timeout 60 s (absolute deadline given to the adapter; Python also enforces it), adapter heartbeat 3 s, page-disconnect pause 10 s, SSE retry 2 s, SQLite `busy_timeout` 5000 ms. Those values live on the server. The browser stores only the seven display prefs (`roboact.lang`, `roboact.theme`, `roboact.motion`, `roboact.viewport.<flow>`, `roboact.panels`, `roboact.grid`, `roboact.logFilter`).

## How to use it

1. First paint is an infinite canvas. The left palette lists adapter actions grouped by adapter, plus Start, Output, Condition, Merge, Human confirm, Constant. There is no loop node; loops are a back-edge plus a condition.
2. The top bar shows the flow name and adapter name. A red **REAL** tag appears only when `target=real`. There is no sim/real toggle.
3. Ports of different types refuse to connect. Parameters sit on the right. A breakpoint before a node is the left rim; after is the right rim.
4. The three control slots stay put: **Run / Pause / Continue**, **Step**, **Stop**. While a run is going the first slot is Pause and Stop is enabled. After Pause the slot reads **Pausing…** until the server says `paused`, then **Continue**. **F5** run/continue, **F6** pause, **F10** step, **Shift+F5** stop. Stop is not e-stop. The far-right control is appearance (half-filled circle): follow system (default), light, dark, stored as `roboact.theme`. Language sits next to it (`roboact.lang`: follow browser / English / 中文). First load follows `navigator.languages` (first `zh*` → Chinese, else English) and `prefers-color-scheme`. Manual picks persist; a second tab follows via the `storage` event. All UI chrome goes through `web/i18n/en.js` and `web/i18n/zh-CN.js`. Operator-typed node and flow text is never translated.
5. A running node shows **Running · 40%** or the latest feedback; otherwise **Running · 12 s**. Images appear only on the right Output tab, never as canvas thumbnails. The log stores size / time / bytes.
6. The adapter action-list version is stored on the flow. A mismatch blocks Run; a missing action is marked **Action no longer exists**. Actions that omit required descriptor fields, or that declare `side_effect=write`, are not turned into nodes. The panel lists what was rejected. `expiry=adapter_only` shows **Expiry is adapter-only**.
7. Stop on a non-cancellable blocking action waits for it to finish; the log records `not cancelled`. Cancel rejected: the node keeps running and shows **Cancel rejected**.
8. After a service restart the run stays paused. A `motion` node that was in flight is **Interrupted · result unknown** and does **not** rerun by itself. Read the robot back, then **Redo this step** or **Mark done**. Test `write` nodes retry with the same idempotency key.
9. Adapter silent for 3 s → **Paused · robot controller lost** (red; Continue waits for the adapter). The adapter base cancels in-flight goals when its own heartbeat dies. Page SSE gone for 10 s → **Paused · page disconnected** (amber; Continue after reconnect; the current node is not cancelled).

Side effects: `none | read | motion | write`. `motion` moves the robot (PRD v1.4.4 / exec-model v0.2.1; default if omitted). `write` is only for handwritten test nodes (`append_note` / `demo_notes.db`), not the product palette. An action may set optional `precondition_wait_s`: the adapter re-reads `read_state` for that window and sends the goal as soon as the condition holds; if it never holds the step is `precondition_failed` and no goal is sent. One run record per step either way.

Storage, SSE, the HTTP API, and exported flows carry **codes only**. The UI maps codes to text for the active locale. Adapter action titles use `labels[locale]`, then `name`, then `id` — never a blank title. Units and operator-typed names, notes, and values are not translated. A missing Chinese key falls back to English, never to the key name.

## Tests

```bash
uv sync --frozen
uv run python -m playwright install chromium   # if this machine has no Chrome
scripts/check.sh
```

`scripts/check.sh` is the gate: `uv sync --locked`, `ruff check`, `ruff format --check`, `pyright`, `pytest`, private-IP scan over exactly `git ls-files`, then gitleaks. CI runs the same order on x86_64 (`uv sync --locked`) and a second `ubuntu-24.04-arm` job (`uv sync --frozen` + pytest).

CI also recomputes the sha256 of every file in `vendor-manifest.json` and requires an SPDX `license` plus the text under `vendor/licenses/`. `127.0.0.1` and `0.0.0.0` are allowed in the private-IP scan; other RFC1918 addresses are not. Enable the hook with `git config core.hooksPath .githooks` (it runs `scripts/pre-push.sh`).

`append_note` / `demo_notes.db` stay off the product path. They exist so executor / idempotency tests (A18, A20) have a write side effect. Product writes go through FakeAdapter memory.

## Acceptance

Only items exercised **in a browser against the real backend** are marked pass. Server-queue or `SseHub` measurements alone do not count.

| Item | Status | Evidence |
|---|---|---|
| A4 / PRD 19.1 push merge | **pass** | Chromium, 500 nodes, real SSE: `n=117 duration_s=12.449 batches_per_sec=9.398` (≤ 10). Canvas matched backend. |
| A5 / PRD 19.2 pause latency | **pass** | Chromium rAF over 5 runs: `min=2 median=3.0 max=4` (all ≤ 20). samples=`[2, 3, 3, 4, 4]`. |
| A6 reconnect + page lost | **部分通过** | Real TCP-proxy half-open → `operator_lost`, no new motion node (`nodes=['start', 'go']`). Pause was `11.251s` (need ≤ 10 s). Reconnect cut `1.604s`, event ids did not go backwards. |
| PRD 19 canvas performance | **部分通过** | CI records only; formal check on Alex's computer. |
| PRD 19.2 stop without waiting for the window | **pass** | Same as A5 (rAF min/median/max all ≤ 20). |
| PRD 19.4 wait until stable | **pass** | `test_a38_wait_then_send_when_stable`, `test_a38_deadline_precondition_failed_no_goal`, `test_a38_no_wait_fails_on_first_read`. |
| PRD 19.5 light and dark | **pass** | `test_prd195_prefers_dark_then_persists_light`; grayscale six states; `test_prd195_offline_fonts_both_themes` (no remote font request). Contrast: `test_section_17_4_table_values`. |
| Locale + settings | **pass** | Catalog key parity; missing zh key falls back to English. Navigator en / zh / fr. 19.7 persist / bad `localStorage` / two-tab sync. `test_prd198_codes_survive_language_switch`. |
| A40 / Host Origin | **pass** | One reject + one allow each for Host, Origin, Content-Type (`test_a40_*`). Rejects start no run. |
| PRD 19.10 toolchain | **pass** | Python 3.12; no `rclpy` in `pyproject.toml`; `scripts/check.sh` + CI `check` / `test-aarch64`. |
| A39 authentication | **deferred (Alex 2026-10-31)** | No tokens. See [.agents/docs/auth-and-network.md](.agents/docs/auth-and-network.md). |

## Before a real robot (R1–R8, N/A in v1)

v1 uses `FakeAdapter` (`target=sim`). These must pass before a real robot. Source: [exec-model](docs/exec-model.md) §13.1.

| Id | What | v1 | Pass when |
|---|---|---|---|
| R1 | Spot-class E-Stop endpoint `timeout` = 9 s, stop check-in | N/A | After 9 s enter SETTLE_THEN_CUT (sit); vendor citation required |
| R2 | Topic velocity control stops publishing (ros2_controllers class) | N/A | Default `cmd_vel_timeout` 0.5 s then zero velocity; quadruped uses its own citation |
| R3 | Reset after hardware e-stop | N/A | Canvas run stays paused, does not auto-continue (ISO 13850 4.1.1.2) |
| R4 | What the dog does after command stream stops | N/A | Matches vendor docs; citation required before a real robot |
| R5 | What the dog does after controller power loss; hardware e-stop still works | N/A | Same |
| R6 | Gripper still holds after power loss | N/A | `cancel.after` and `read_state` from the vendor text |
| R7 | “Settled” is decidable | N/A | Blocking actions return only after the robot is stable, **or** `read_state` has a settled field; both need a vendor citation. Without that, no real robot and no `precondition_wait_s` |
| R8 | Repeat A6 on a real TCP cut | N/A | On the real-robot network, half-open cut (no FIN/RST) must produce `operator_lost` in about 10 s. Without that pause, `motion` keeps going with nobody watching |

## Layout

```
LICENSE                 MIT (Copyright (c) 2026 alexzhang1030)
AGENTS.md               Project rules + PCR block
.agents/docs/           Project Context Records (map in README.md)
roboact/                Python package (adapter, nodes, graph, executor, Store, HTTP+SSE)
roboact/static/fonts/   Inter + JetBrains Mono woff2 and OFL files (ship with the package)
web/                    Static frontend, served by the backend
web/i18n/               en.js and zh-CN.js catalogs (ES modules, no build)
web/vendor/             Vue / Vue Flow / icons (unmodified upstream files)
assets/logo.svg         Brand mark (favicon is the same drawing)
docs/prd.md             PRD v1.4.15 (current product doc in this tree; token rows ignored)
docs/system-design.md
docs/ui-spec.md         §17 fonts and themes, §18 language
docs/design/            Rams light + `-dark` sheets
docs/design/contrast.py WCAG helpers used by contrast tests
docs/diagrams/          Deploy / sequence / state sources + PNG
pyproject.toml          uv + ruff + pyright
.python-version         3.12
scripts/check.sh        uv sync --locked, ruff, format, pyright, pytest, private-IP, gitleaks
.githooks/pre-push      Private-IP scan + gitleaks
vendor/wheels/          Runtime Python wheels
vendor/licenses/        Third-party licence texts
vendor-manifest.json
scripts/check_manifest.py
scripts/check_private_ip.py
scripts/pre-push.sh
tests/
```
