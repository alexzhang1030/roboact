# UI spec v1: flow canvas (Vue Flow 1.48.2)

Strings are `key`s in `web/i18n/en.js`; English is quoted for top-bar, banner and crowding strings.

## 1. Layout

- Top bar, 44px: flow name, version, adapter name (`--muted`), real tag; run controls, status word; steps, connection, language, theme.
- Left panel, 220px, collapses to 0: node types, drag to add. Canvas double-click opens it with `search_nodes`.
- Right panel, 360px, min 320px, resizable: `tab.in`, `tab.out`, `tab.params`, `tab.rec`. No selection: start time, total steps, `run.errors`.

### Run controls

Three fixed slots; buttons never move or hide, unavailable ones are grey.

- Slot 1: `run.start` ("Run"), `run.pause` ("Pause") while running, `run.resume` ("Continue") while paused; grey while awaiting confirmation. Width fits the longest label.
- Slot 2: `run.step` ("Step"), only while paused.
- Slot 3: `run.stop` ("Stop"), while running, paused or awaiting confirmation. Tooltip `run.stop_title` ("Hold position, motors stay powered. Use the hardware button for e-stop."). No e-stop control anywhere.
- Pause finishes the current node; slot 1 shows `run.pausing` ("Pausing…", grey) until the `paused` event.
- `conn.ok` ("Connected"); on SSE loss `conn.retry` ("Reconnecting") and all controls grey until events replay.
- Adapter `target` = real robot: `real_tag` ("REAL"), 1px red border, 22px high, 12px text; never guess from the name. Simulated: `run.target_sim` ("Sim").

### Status word

| Colour | Keys |
|---|---|
| amber | `run.word.paused_breakpoint` ("Paused · breakpoint"), `run.word.paused_step` ("Paused · step"), `run.word.paused_user` ("Paused · manual"), `run.word.paused_restart` ("Paused · after restart"), `run.word.approval` ("Awaiting confirmation"), `run.word.paused_write_timeout` ("Paused · write timeout"), `run.word.paused_motion_timeout` ("Paused · motion timeout"), `run.word.paused_operator_lost` ("Paused · page disconnected"), `run.word.step_limit` ("Step limit reached") |
| red | `run.word.error` ("Error"), `run.word.paused_storage` ("Paused · could not write the run record"), `run.word.paused_adapter_lost` ("Paused · robot controller lost"; Continue grey until it returns), `run.word.stop_rejected` ("Stop rejected") |
| `--muted` | `run.word.running` ("Running"), `run.word.completed` ("Completed"), `run.word.stopped` ("Stopped") |

Steps: `steps.normal` ("Steps {used} / limit {limit}"); at the limit `steps.hit` ("Stop · steps {used} / limit {limit}") in amber, and the summary names the last node.

### Banners

Below the top bar: 36px, max two, newest on top, stay until handled.

| Case | Text | Buttons | Left line |
|---|---|---|---|
| Canvas edited mid-run | `banner.version` ("The canvas changed. This run still uses v{version}.") | `banner.rerun` ("Rerun from the new version"), new run | none |
| Approval | `banner.approval` ("“{name}” is waiting for your confirmation.") | `banner.reject` ("Reject"), `banner.approve` ("Approve", primary) | 3px amber |
| `storage_error` | `banner.storage` ("Could not write the run record: {detail}. The run is paused.") | `banner.continue` ("Continue") | 3px red |

Approval buttons repeat at the right-panel top, below a `note.placeholder` input.

On 409, no banner: a 12px line under the controls fades after 4 s and the page refreshes from the server. `pause_mismatch`: `mismatch.other_page` ("Another page already continued this run."). `awaiting_approval`: `mismatch.approval` ("This step is waiting for confirmation. Approve or reject it first.").

## 2. Node

- Width 240px fixed. Height from port count, unchanged by zoom.
- Radius 6px, border 1px in every state, side padding 12px.
- Title row 32px: type icon, side-effect mark, name 14px/600; state icon + word 12px.
- Port rows 24px, name 12px, dot 8px. Summary row 216px, one line.
- No params on nodes. Titles truncate with "…".
- Selected: `outline: 2px solid var(--blue); outline-offset: 2px`.
- Emphasis: border colour plus `box-shadow: inset 0 0 0 1px <same colour>`. Never change `border-width`.
- Zoom < 0.6: title and state icon only (`visibility:hidden`).

## 3. Node states

Icon + word + border; icons alone must separate states in greyscale. Each `status.*` key uses the matching `state-*` icon.

- Default border: `status.idle`, `status.queued`, `status.done_s` / `status.done_ms` (green icon), `status.stopped`, `status.interrupted`; `status.skipped` dashed.
- Blue: `status.running` (only spinner).
- Amber, emphasis: `status.paused_before`, `status.paused_after`, `status.paused` (after restart), `status.interrupted_unknown` (`state-unknown`), `status.awaiting`.
- Red, emphasis: `status.error` plus summary.
- 1px red dashed, no fill: `status.action_missing` (`state-unknown`).

Rules:
- Interrupted: crash only; non-motion nodes rerun on continue.
- Unknown, motion node: no auto rerun. Panel: `readback.read`, `readback.title`, then `readback.retry`, `readback.done`, Stop; grey with `readback.reading` until read back, else `error.readback_required`.
- Unknown, `write` node: `idem.note`.
- Timeout: normal node is error with `error.timeout_s`; `write` node is unknown with `error.timeout_write`.
- Running detail, no progress bar: `status.running_pct`, `status.running_feedback`, `status.running_s`.
- Precondition wait: `status.running_wait`, blue; log `rec.waited`. Stop gives stopped; limit gives error `error.precondition_failed`; pause or disconnect requeues and restarts the wait at zero.
- Cancel rejected: still running, `status.cancel_rejected` amber; top bar adds `run.stop_rejected_note`. Retry = Stop again.
- Action missing: panel `error.action_missing`. Run is blocked while any exists; `run.action_missing_count` next to Run jumps to the first. Cleared only by replacing the action or deleting the node.

## 4. Breakpoints

- 12px zone at the left edge = before, right edge = after. Hover shows a light red dot; click sets a solid red 10px dot; click again clears. `bp.before_on`, `bp.before_off`, `bp.after_on`, `bp.after_off`.
- Conditional (`params.bp_round` or field test): right-click the dot. It gets a white bar; hover shows the condition.
- Keys: F9 toggle, F5 run/continue, F6 pause, F10 step, Shift+F5 stop.

## 5. Input sources

Labels 12px `--muted`; stale is never red.

- Solid grey dot: live, no label.
- Hollow dot: `src.cache_time`. With slash: `src.stale` (hover lists changed nodes).
- Dot + pin: `src.pinned`; `--muted` pin: `src.pinned_unused`.
- Small square: `src.manual`.

Single-node run: dropdown of `round.option`, default `round.latest`; `in.run_this` (primary), `in.pin`.

## 6. Edges

- Data: 1.5px grey solid. Selected: 2px blue.
- Carrying data: blue dashed flow (`stroke-dashoffset`), only from emit to downstream start.
- Back edge: 1.5px grey dashed, routed below, label `loop.round` (target run count).
- Hover: first 200 chars of last data, `edge.open_panel`.
- Type mismatch: target port greys out while dragging.

## 7. Groups

Ctrl+G on a selection: 1px grey dashed frame, radius 8px, name top-left, `groups.run` top-right. Outside edges become pinnable frame inputs; back edges show `group.back_edge`.

## 8. Side effects

- `mark-write` before the title: `write` nodes, motion actions, actions missing from the hand-written table, tool nodes allowing a `write` tool. Hover: `side.write`, `side.motion`, `side.write_detail`. `read` nodes: no mark.
- Replay: `write` nodes show `status.done_mock`; `in.really` opens `confirm.external`.
- `deadline.adapter` actions: 12px amber `params.no_expiry` at the form bottom; no node mark.

## 9. Rounds

Nodes show the latest round. Panel top: `round.nav`. Stepping over a back edge increments both.

## 10. Palette and params

- Header `palette.source`, refresh button. States `palette.loading` (grey, no skeleton), `palette.disconnected` (Run grey, `run.disabled_no_controller`), `palette.empty`.
- `palette.control` group: `node.start`, `node.output`, `node.constant`, `node.condition` (`port.yes`, `port.no`), `node.merge`, `node.human_confirm`.
- Forms come from the adapter. Units sit in the input. No range: `params.no_range`; never invent one.
- Camera: no thumbnail. Port `port.image_size`; output tab shows the image at panel width with `out.cap`; edge hover `edge.image_preview`.

## 11. Colour tokens

On `:root` and `[data-theme="dark"]`; no other colours. Contrast is against `--surface` unless noted (light / dark). Text needs 4.5:1, `--edge` and `--control` 3:1; `--grid` and `--hair` are decorative.

| Token | Light | Dark | Contrast |
|---|---|---|---|
| `--bg` canvas | #F6F6F4 | #141413 | |
| `--grid` 20px dots | #E2E2DD | #2A2A27 | 1.20 / 1.28 on `--bg` |
| `--surface` panels, nodes | #FFFFFF | #1E1E1C | |
| `--surface2` code, disabled | #FAFAF8 | #232321 | |
| `--text` | #1A1A18 | #ECECE8 | 17.43 / 14.10 |
| `--muted` | #5C5C57 | #A3A39C | 6.72 / 6.58; 6.43 / 6.20 on `--surface2` |
| `--border` node | #C9C9C3 | #3D3D39 | |
| `--control` | #85857F | #73736C | 3.71 / 3.50; 3.43 / 3.86 on `--bg` |
| `--edge` | #8A8A84 | #77776F | 3.21 / 4.08 on `--bg` |
| `--hair` | #E6E6E1 | #2E2E2B | |
| `--blue` | #1F6FEB | #4C8DF6 | 4.63 / 5.13 |
| `--green` | #1A7F37 | #3FB950 | 5.08 / 6.57 |
| `--red` | #C62828 | #F06A6A | 5.62 / 5.55; 4.83 / 4.87 on `--redsoft` |
| `--amber` | #8F6000 | #D4A72C | 5.47 / 7.44; 4.95 / 5.77 on `--ambersoft` |
| `--redsoft` | #FBEAEA | #3A2222 | |
| `--ambersoft` | #FBF3E0 | #3A3020 | |
| `--onblue` | #FFFFFF | #0B1220 | 4.63 / 5.75 on `--blue` |

## 12. Theme

The far-right menu offers `theme.follow` (default), `theme.light` and `theme.dark`. An inline `<head>` script sets `data-theme` before CSS loads, and follow mode tracks `prefers-color-scheme`. Dark mode has no shadows. The logo does not change.

## 13. Type

- UI: `"Inter", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", sans-serif`.
- Data: `"JetBrains Mono", ui-monospace, monospace`, 400/500, `tabular-nums`. `font-display: block`, served locally.

| Size / weight / line | Use |
|---|---|
| 16 / 600 / 24px | panel title |
| 14 / 600 / 20px | node title |
| 13 / 400 / 20px | body, buttons |
| 12 / 400-500 / 18px | ports, states |
| 11 / 400 / 16px | shortcuts, mono |

Code: mono 12px, line 1.6. `vendor-manifest.json`: `InterVariable.woff2` 352,240 B (Inter 4.1); `JetBrainsMono-Regular.woff2` 92,164 B, `JetBrainsMono-Medium.woff2` 93,824 B (2.304).

## 14. Spacing and sizes

Spacing: 4, 8, 12, 16, 24. Buttons: 28px (top bar), 32px (panel primary), radius 4px, 72px minimum width (`.btn.w`). Grid and snap: 20px. The minimap is off by default, sits bottom-right, and M toggles it.

## 15. Icons

`icons/`: 21 SVGs, 16×16, stroke 1.5, `currentColor`.

- Node types (`--muted`): `node-start`, `node-output`, `node-constant`, `node-template`, `node-model`, `node-tool`, `node-condition`, `node-merge`, `node-approval`.
- States as in §3, plus `mark-write`. Done `--green`, error `--red`, paused `--amber`, running `--blue` (1 s linear spin), others `--muted`.
- Reduced motion: no spin, no edge flow, no transitions.

## 16. Language and settings

- `web/i18n/en.js` and `web/i18n/zh-CN.js` are ES modules with no build step. Add every new key to both.
- Defaults: language from `navigator.languages` (a first entry starting with `zh` gives Chinese, otherwise English), `prefers-color-scheme`, and `prefers-reduced-motion` (`motion.follow`, `motion.reduce`, `motion.full`).
- Choices go in `localStorage`: `roboact.lang` (`en`/`zh-CN`) and `roboact.theme`, synced across tabs via `storage`. `lang.follow` resets the language.
- The browser stores only display preferences: language, theme, zoom, pan, panel widths and the log filter. Server settings are display-only.
- The language button sits left of theme and shows `lang.zh` or `lang.en`. No flags. `<head>` sets `<html lang>`. Switching never reloads the page or interrupts a run.
- Translate by code; storage and logs hold codes only. Never translate user text or values. Adapter labels: `labels[lang]`, else `name` in mono. Times 24-hour, mono.

### Crowding

Measured with HarfBuzz, `InterVariable.woff2`, weight 400, current `en.js`.

1. The title row has about 188px. Show icon + one word (`status.interrupted`, `status.waiting`, `status.running`). The qualifier goes to the 216px summary row (`status.summary.result_unknown`, `status.summary.waiting_precondition`). Both languages follow this. At 12px, `status.interrupted_unknown` ("Interrupted · result unknown") is 160px and `status.running_wait` ("Running · waiting for precondition") is 191px.
2. The top bar must fit on one line at 1280px. The longest string is `run.word.paused_storage` ("Paused · could not write the run record"), 239px at 13px. Never push out connection, language or theme; collapse the adapter name, then the version.
3. Buttons size to their text. The right panel wraps.

## 17. Flow import / export (UI)

- `flow.export` and `flow.import` in the top-bar flow menu; no large buttons.
- Export asks `flow.export_format` (`flow.format_json`, `flow.format_yaml`); default filename = flow name. Node positions are exported. Not in the file: breakpoints, pins, run records, images, undo, zoom, theme, language. Secret params export as `null`.
- Import accepts `.json`, `.yaml`, `.yml`. It always creates and opens a new flow, never overwrites, never starts a run. No grey-out during a run, no replace confirm.
- Rejected, canvas unchanged: newer format version, unknown field, duplicate id, edge to a missing port, port type mismatch, loop with no exit, YAML custom tag or alias. The dialog stays open with `flow.import_failed` and one `flow.import_error` per error, by path (e.g. `nodes[3].params.speed`).
- Unknown action: imported anyway; the node shows `error.action_missing` (§3). Run is blocked while any such node exists, with `run.action_missing_count` next to Run. Only replacing the action or deleting the node unblocks it; `flow.review_enable` does not.
- Robot, adapter, action-list version or unit mismatch: an amber banner at the top lists each one (`flow.mismatch_robot`, `flow.mismatch_adapter`, `flow.mismatch_actions`, `flow.mismatch_units`) with one button `flow.review_enable`. Run is disabled (`flow.run_locked`) until it is pressed.
- `motion` param limits are checked before each run against the connected robot, not at import. The failing node and param are marked (`run.param_out_of_limit`).
- File format and versioning are defined elsewhere.

## 18. Forbidden

Glow, gradients, frosted glass, layered shadows (one node shadow `0 1px 2px rgb(0 0 0 / .06)`, light only); always-animated edges; colour-only states; opacity for skipped or disabled; size change on zoom; minimap open by default.

## 19. Acceptance

- States are distinct in greyscale.
- 12 screens per language, light and dark (`-dark` files).
- No English title overflow; the top bar is one line at 1280px.
- No missing translations or raw keys.

## 20. Deferred

- Auth: the token screen (`r-token`) is not built and has no keys yet.
- The post-restart display follows §1 and §3 until the execution model settles.
