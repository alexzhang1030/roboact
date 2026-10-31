# PRD v1: RoboAct (agent flow canvas, phase 1: flow)

Version 1.5 (2026-10-31). Owner: Fadell · PRD. Repo: alexzhang1030/roboact.

exec-model = `exec-model-v0.md` (A-numbers: its acceptance items); ui-spec = `ui-spec-v0.md`. Backticked Chinese = zh locale value; English wording: Rams.

## 1. Goal

- User: Alex, later embodied-agent developers. Robot: quadruped, arm, camera at arm end, Jetson Thor.
- Wire a task as nodes (look, walk, grasp), run, find the failing step without a full rerun.

## 2. User flow

First run

1. `pip install`, start, open browser. No Node, no build.
2. Infinite canvas, one Start node.
3. Add nodes (sidebar, double-click), drag edges. Zoomed out: title and state; zoomed in: ports. Params in right panel.
4. Breakpoint: click node's left edge; before-run and after-run separate.
5. Run. States: idle `status.idle` "Idle", queued `status.queued` "Queued", running `run.start` "Run", at breakpoint `停在断点`, waiting for approval `status.awaiting` "Awaiting confirmation", done `status.done` "Done", error `status.error` "Error", skipped `status.skipped` "Skipped", stopped `status.stopped` "Stopped" (user Stop), interrupted `status.interrupted` "Interrupted" (crash only), interrupted · result unknown `status.interrupted_unknown` "Interrupted · result unknown" (exec-model failure table). None auto-rerun.
   - Timeout: plain node → error "timeout"; `write`/`motion` → result unknown, run pauses.
   - Result unknown (`write`/`motion` only): `read_state`, then user picks rerun or mark done; own icon.
   - Only the active edge animates.
6. At breakpoint: Continue, Step, Stop; panel shows input, output, duration; edit input, continue.
7. Run one node with past-run, manual or pinned input; upstream not rerun. Pins only in single-node/group runs; full runs mark them `src.pinned_unused` "Pin unused this run". Cached input port: hollow circle naming its source run.
8. Run group: box-select; incoming edges become pinnable entry ports; outside nodes don't run.
9. Error: red plus icon and text; panel shows I/O and error; rerun from here, upstream not rerun.
10. Side-effect node (email, file write, external write API): title mark; replay mocked (`模拟`) unless user clicks real run.
11. Loop: dashed back edge routed below, labeled "round 3" (target's run count); exit via condition node; top bar "steps 37 / limit N"; node shows latest round, panel lists all.
12. Run locks flow version; editing while paused offers `banner.rerun` "Rerun from the new version" (new run from start; old stays paused).

12.1 Human approval: run waits; panel shows input, Approve, Reject, note; chosen branch continues. Every run.

Daily use

13. Save flows, open past runs, compare two runs' I/O per node.

Disconnects

14. Refresh or disconnect ≤ 10 s: run unaffected. Page disconnected > 10 s: finish current node (not cancelled), then pause; top bar `run.word.paused_operator_lost` "Paused · page disconnected". Same state on return.

## 3. Requirements

Constraints (Alex, 2026-10-31 15:56)

- Python backend and executor. Web page.
- Zero build: native ES modules / import map; no bundler, npm build, TypeScript compile.
- Offline, vendored deps, no CDN. Manifest per file: package, version, URL, conversion tool and command ("none"), sha256; change together.
- Predefined node types only; no user Python.
- Runs on robot controller; one node type per adapter atomic action; one adapter per controller type; vendor, ROS 2/SDK, real/sim hidden in adapter.
- Disk: run logs only; images and sensor data in memory (inference).
- `append_note`/`demo_notes.db`: tests A18, A20 only (exec-model v0.2 §2.4, §11).
- Cycles allowed; not DAG-only.

Scope

- Must: infinite canvas; loops (exit, step limit, per-round records); node/edge editing; full, single-node, group runs; Human approval; breakpoints (before/after, continue, step, stop, edit and continue); per-run I/O records; pins; rerun from any node; mock replay; version lock; undo/redo; flow files; light/dark.
- Won't: BT/graph views, user Python, multi-user, marketplace, deployment/monitoring, desktop app, build step, CDN.
- BT/graph later: node type field, data vs control edges, layout-independent components, file format version.

UI

- Vue Flow 1.48.2 (6 npm files, dep `vue`); React Flow fallback; no LiteGraph (no fixes after 0.17.2). Eval: `/workspace/agent-ui/flow-lib-eval-2026-10-31.md`.
- `globalThis.process ??= {env:{NODE_ENV:"production"}}` before import map.
- Fixed node size: zoom < 0.6 → `visibility:hidden`; selection via outline (resize: 250–297 ms at 500 nodes; ui-spec §14).
- Plain CSS variables; vendored Inter (text) and JetBrains Mono (numbers, code), OFL; system Chinese font; colors ui-spec §17.
- English and Simplified Chinese; switch beside theme toggle, no reload or run interruption; dates and numbers follow language.
- Text only from `i18n/en.js`, `i18n/zh-CN.js`; missing zh → English, never key name; status words one table in ui-spec.
- Storage and API hold codes only (`operator_lost`, `interrupted_unknown`, `waiting_precondition`, `precondition_failed`, `adapter_lost`).
- Untranslated: adapter text (optional `labels` `{"en": ..., "zh": ...}` → `name` → `id`), user text, values, units (`rad`, `m/s`).
- Settings: browser value until chosen, then `localStorage`; tabs sync via `storage` event; bad value → fallback.

| Setting | Browser source | Key | Fallback |
|---|---|---|---|
| Language | `navigator.languages`[0] `zh*` | `roboact.lang` (`en`/`zh-CN`) | English |
| Theme | `prefers-color-scheme` | `roboact.theme` (`system`/`light`/`dark`) | system, else light |
| Motion | `prefers-reduced-motion` | `roboact.motion` (`system`/`reduce`/`full`) | full |
| Viewport | none | `roboact.viewport.<flow id>` | fit |
| Panels | none | `roboact.panels` | defaults |
| Grid | none | `roboact.grid` | on |
| Log filter | none | `roboact.logFilter` | all |

- Server-only, read-only in page, changes validated and logged: `timeout_s`, `precondition_wait_s`, 10 s page pause, 3 s adapter pause, `expiry`, adapter, step limit.

Repo

- License: MIT (Alex, 2026-10-31); `LICENSE`, `pyproject.toml`; OFL in `vendor/licenses/`.
- `docs/`: PRD, specs, designs. Decisions: PCR files (what, why, who, date) in `.agents/docs/`, index `.agents/docs/README.md`; no `docs/decisions/`; Alex alone marks "approved".
- Never commit chats, robot IPs, private vendor docs, tokens. Pre-push: gitleaks, private-IP scan (`10.`, `192.168.`, `172.16–31.`) of `git ls-files`.

Executor

- No auth in v1 (tokens: exec-model v0.2.2 §0.2, A39).
- SQLite, stored separately: flows (nodes, ports, edges, params, version); run records (run id, node, version, I/O, state, duration, error, input source).
- Input source: real upstream, cache, manual pin, manual entry.
- HTTP: run all, run from, run subgraph, pause before/after, resume, step, stop.
- SSE batched per 100 ms, last state per node; pause, breakpoint, step flush immediately; records stay one per step; reconnect fetches state first.
- Node types from adapter at startup; none → `palette.disconnected` "Robot controller not connected", flows can't run; removed → red `status.action_missing` "Action no longer exists" (ui-spec §16), kept.
- Action fields: `id`, list `version`, `params`, `outputs` (incl. image), `side_effect` (none/read/motion; default motion), `return_mode` (blocking/poll), `timeout_s`, optional `typical_s`, `cancel`, `preconditions`, optional `precondition_wait_s`, `read_state` (required for motion).
- `params` entry: name, type, unit, range or options, default, required.
- `cancel`: cancellable or not; where the robot stops; per-cancel accepted flag.
- Result carries measured end state.
- Missing required field → no node; panel names it. Names and types from interface, rest hand-written; param form generated from it.
- Action-list version locked with flow; mismatch fails before start (inference).
- Stop and cancel outcome per action; refused cancel keeps node running, shows `status.cancel_rejected` "Cancel rejected"; non-cancellable runs to end.
- Node stays running until final result.
- Camera node: `read`, one image in Output tab with time, round, step (ui-spec §16); no thumbnail; memory only; log dimensions, time, size.
- Running label: "running · 40%" or "running · 12 s"; no progress bars.
- Snapshot = run-record input plus upstream-config fingerprint; upstream param/code change → maybe stale, still usable, not deleted.
- Entry ports: cache (hollow, run time); stale (slashed, `src.stale` "Cache · upstream changed", hover lists changed nodes, no red); pinned (filled with pin, `src.pinned` "Pinned", never expires).
- Pause on `asyncio.Event`; restart resumes before next node; no data via process memory.
- Pydantic → JSON Schema; same-type ports only; images as metadata (exec-model v0.2 §2.2); large outputs path + hash (inference).
- Side effects: `none`, `read` (cached replay), `motion` (real, confirm first), `write` (test, mock, idempotency key); adapter `write` → `rejected_actions`.
- Data only via edges.

Loops (§5.1)

- Stop: condition exit, step limit (`run.word.step_limit` "Step limit reached"), user. No exit: warn on save, refuse run.
- Step limit default 1000 (Steve, 2026-10-31, temporary). No round limit.
- Breakpoints every round, or "only round N", or condition comparing existing input fields (no Python); Step over back edge enters next round.
- Record key run id + global step, plus node and round; no overwrite; unread edge data persisted.
- Staleness includes the node itself in a loop.
- Single run in loop: pick round (default last). Partial-loop group: back edge is an entry port (chosen round or pin), no looping inside.
- Mid-loop rerun continues that round; earlier records kept.

Control nodes (§5.2)

- Start, Output, Constant, Condition (only loop exit), Merge, Human approval (exec-model §2.4); `none`. Test group: "text template", "model call", "tool call".
- No seventh "wait" node (Tedrake). Reach goal: `return_mode = blocking`; JTC succeeds within goal tolerance and `constraints.goal_time` ([JTC](https://control.ros.org/master/doc/ros2_controllers/joint_trajectory_controller/doc/userdoc.html)); Nav2 goal checker accepts position and heading ([SimpleGoalChecker](https://docs.nav2.org/configuration/packages/nav2_controller-plugins/simple_goal_checker.html)). Wait for person: Human approval. Long or non-robot waits: not v1.
- `precondition_wait_s`: poll `read_state` until true, else `precondition_failed`; absent → check once; shows `status.running_wait` "Running · waiting for precondition"; `timeout_s` starts at send; Stop → stopped, no read-back; resume restarts wait; restart → pending (exec-model v0.2.1 §2.5.5, A38); one record. Poll interval and fields: adapter author.
- Blocking returns before stable and no stable field in `read_state` → adapter must add one (pre-real-robot check).

Hardware

- Robot model and interface specs await a source (Alex or Tedrake).
- Backend shares the controller with control (inference): normal priority, off control-loop cores, memory cap; control threads never wait on it. ros2_control loops are real-time ([controller_manager](https://control.ros.org/rolling/doc/ros2_control/controller_manager/doc/userdoc.html), [RT Linux](https://docs.ros.org/en/jazzy/Tutorials/Miscellaneous/Building-Realtime-rt_preempt-kernel-for-ROS-2.html)).
- Canvas Stop is not e-stop; e-stop is the robot's hardware path, never web or HTTP; no UI e-stop button. Proposed: IEC 60204-1 category 2 (hold, motors on; Thompson's suggestion, not certified).
- After cancel: robot state and gripper hold read back and shown.
- Adapter reports `name`, `version`, `target` (sim/real, a field, not name-guessed), action-list version.
- No per-run sim/real toggle; adapter name right of flow name; red `real_tag` "REAL" tag when real.
- v1: fake adapter, invented actions named as fake (sim).
- Adapter heartbeat; loss cancels active goals (ROS 2 goals outlive clients, [rclcpp #2241](https://github.com/ros2/rclcpp/issues/2241)).
- `timeout_s` → controller expiry; controller without it: description says "expiry by adapter only", UI flags it.
- Crash: `motion` → result unknown; poll actions query goal, blocking use `read_state`; human confirms.
- Adapter silent 3 s → pause, `run.word.paused_adapter_lost` "Paused · robot controller lost"; deadline sent to adapter; 60 s Python timer as backup. Defaults adjustable, no source (Torvalds).
- Listen 127.0.0.1 only; no `--host`; remote browser via SSH forwarding.
- Network loss: each action's adapter-defined behavior.
- LAN access later: `--host` and tokens ship together.
- A40 (exec-model v0.2.3 §0.2): `Host` `127.0.0.1`/`localhost`/`[::1]`; same `Origin`; `Content-Type: application/json`.

## 4. Acceptance criteria

1. Offline, Python-only machine runs the app; no build; requests only to 127.0.0.1. Steps: acceptance.md#1

1.1 CI fails on any vendored-file sha256 mismatch. Steps: acceptance.md#1.1

2. Breakpoint before node 3 stops there; nodes 3 and 4 get no records. Steps: acceptance.md#2

3. Input edited at a breakpoint reaches downstream. Steps: acceptance.md#3

4. Refresh at a breakpoint keeps node and I/O. Steps: acceptance.md#4

5. Single-node run records only that node. Steps: acceptance.md#5

6. 2-node group run records only those 2; entry data matches chosen run. Steps: acceptance.md#6

7. Rerun from failed node adds no upstream records. Steps: acceptance.md#7

8. `write` replay mocked; `read` uses cache; `motion` asks first. Steps: acceptance.md#8

9. Edit at breakpoint: old version continues; rerun prompt shown. Steps: acceptance.md#9

10. Error, breakpoint, done distinguishable in grayscale. Steps: acceptance.md#10

11. Restart at breakpoint gives the same output. Steps: acceptance.md#11

12. Only upstream edits stale downstream snapshots. Steps: acceptance.md#12

13. Approval waits; Reject runs one branch, other skipped; first click wins. Steps: acceptance.md#13

14. Exit at round 3: 3 records per loop node. Steps: acceptance.md#14

15. Limit 10: stops after step 10, step limit exceeded. Steps: acceptance.md#15

16. Loop breakpoint stops every round, or only the chosen round. Steps: acceptance.md#16

16.1 Stopped at round 2: node has only its round 1 record. Steps: acceptance.md#16.1

17. Single run with round 2 input matches round 2 record. Steps: acceptance.md#17

18. Loop without exit refused with reason. Steps: acceptance.md#18

18.1 Restart mid-loop gives the same result. Steps: acceptance.md#18.1

18.2 Own param change stales the node's later rounds. Steps: acceptance.md#18.2

19. Perf (inference): 500 nodes, ~550 edges, 10 changes/node/s: p95 ≤ 2 frames; on Alex's computer no long task > 100 ms; CI reports only. Steps: acceptance.md#19

19.1 Real SSE: ≤ 10 events/s; frames meet 19. Steps: acceptance.md#19.1

19.2 Pause to first frame ≤ 100 ms, p95 ≤ 20 ms (A5). Steps: acceptance.md#19.2

19.3 0 node-size updates across zoom 0.6 and selection. Steps: acceptance.md#19.3

19.4 `precondition_wait_s` sends when stable, else `precondition_failed` (A38). Steps: acceptance.md#19.4

19.5 Themes follow system, persist; six states distinguishable; fonts offline. Steps: acceptance.md#19.5

19.6 Language picked from browser, switched live, fully translated; codes survive restart. Steps: acceptance.md#19.6

19.7 UI settings persist, sync, fall back; server values never in `localStorage`. Steps: acceptance.md#19.7

19.8 Private IP in `.agents/docs/` or `docs/` blocks push. Steps: acceptance.md#19.8

19.9 A40 checks reject bad `Host`, `Content-Type`, `Origin`. Steps: acceptance.md#19.9

19.10 `scripts/check.sh` passes on clean clone and aarch64; failures block merge except 19. Steps: acceptance.md#19.10

A6: failing (see 5). A38: 19.4. A40: 19.9. R1–R6 include two checks; Steps: acceptance.md#r1-r6

## 5. Risks and open questions

- A6 failing: 11.251 s measured (PR #1) vs ≤ 10 s; merged by Alex (2026-10-31 21:38); fix before real robot; 10 s is not a real-robot bound until then.
- A6 (Torvalds): `motion` started in 10 s–11.25 s? Extra 1.25 s from heartbeat interval or waiting for current node (step 14)?
- No auth; tokens (exec-model §0.2) or SSH before real robot.
- Only 500 nodes tested; at thousands, LiteGraph (canvas) may win; re-measure first.
- Perf numbers: headless, software rendering, ~60 Hz cap; compare libraries only.
- React 19 has no UMD (esm.sh per upgrade).
- Side-effect marks and no-memory data rely on node authors; check in code review. External state may change before single-node reruns.
- Alex: step limit (1000 temporary); browser location (decides auth, affects v1); Stop = hold or safe pose; JetPack 7 real-time kernel and controller = Thor (not blocking v1).
- Torvalds: re-measure 19, 19.1; confirm 60 s timeout, 2 s SSE reconnect, 19.2 20 ms; fake-adapter actions.
- Thompson: exec-model v0.2 keeps v0.1 storage and restart pause, A18, A20; adds action fields, pause/stop/crash under both `return_mode` values, cancel refused, `motion` rules (no mock, no idempotency dedupe, read state before resume).
- Rams: English status words and buttons in the zh table; verify English fits nodes and top bar.
- Tedrake: once vendor known, cite stop, controller power-loss and gripper power-loss behavior (adapter acceptance, not blocking v1).
- Tedrake: confirm control nodes; `node-list-v0.md` obsolete (9 nodes kept for executor tests).
