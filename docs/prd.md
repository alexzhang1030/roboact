# PRD v1: RoboAct (agent flow canvas, phase 1: flow)

Version 1.5 (2026-10-31). Owner: Fadell · PRD. Repo: alexzhang1030/roboact.

exec-model = `exec-model-v0.md` (A-numbers: its acceptance items); ui-spec = `ui-spec-v0.md`. Backticked Chinese = zh locale value; English wording: Rams.

UI strings are written as i18n keys (`web/i18n/en.js`, `web/i18n/zh-CN.js`).

## 1. Goal

- User: Alex, later embodied-agent developers. Robot: quadruped, arm, camera at arm end, Jetson Thor.
- Wire a task as nodes (look, walk, grasp), run, find the failing step without a full rerun.

## 2. User flow

First run

1. `pip install`, start, open browser. No Node, no build.
2. Infinite canvas, one Start node.
3. Add nodes (sidebar, double-click), drag edges. Zoomed out: title and state; zoomed in: ports. Params in right panel.
4. Breakpoint: click node's left edge; before-run and after-run separate.
5. Run. States: idle `status.idle`, queued `status.queued`, running `status.running`, at breakpoint `run.word.paused_breakpoint`, waiting for approval `status.awaiting`, done `status.done`, error `status.error`, skipped `status.skipped`, stopped `status.stopped` (user Stop), interrupted `status.interrupted` (crash only), interrupted · result unknown `status.interrupted_unknown` (exec-model failure table). None auto-rerun.
   - Timeout: plain node → error "timeout"; `write`/`motion` → result unknown, run pauses.
   - Result unknown (`write`/`motion` only): `read_state`, then user picks rerun or mark done; own icon.
   - Only the active edge animates.
6. At breakpoint: Continue, Step, Stop; panel shows input, output, duration; edit input, continue.
7. Run one node with past-run, manual or pinned input; upstream not rerun. Pins only in single-node/group runs; full runs mark them `src.pinned_unused`. Cached input port: hollow circle naming its source run. While a flow is running, single-node runs ("Run this only" and "Run for real") are disabled in the UI and refused by the server with the reason; allowed when the flow is paused or finished. Reason: another action would move the robot while the flow does; also an open browser dialog over 10 s triggers the disconnect pause (Steve, 2026-11-01; Torvalds, `app.js:985`, `1009`, `1482`, `1486`).
8. Run group: box-select; incoming edges become pinnable entry ports; outside nodes don't run. Same rule as step 7: while a flow is running, group run is disabled in the UI and refused by the server with the reason; allowed when paused or finished (Steve, 2026-11-01).
9. Error: red plus icon and text; panel shows I/O and error; rerun from here, upstream not rerun.
10. Side-effect node (email, file write, external write API): title mark; replay mocked (`run.target_sim`) unless user clicks real run.
11. Loop: dashed back edge routed below, labeled "round 3" (target's run count); exit via condition node; top bar "steps 37 / limit N"; node shows latest round, panel lists all.
12. Run locks flow version; editing while paused offers `banner.rerun` (new run from start; old stays paused).

12.1 Human approval: run waits; panel shows input, Approve, Reject, note; chosen branch continues. Every run.

Daily use

13. Save flows, open past runs, compare two runs' I/O per node.

Disconnects

14. Refresh or disconnect ≤ 10 s: run unaffected. Page disconnected > 10 s: cancel the current action if its `cancel` says cancellable, then pause; a non-cancellable or refused cancel runs to end, then pause; top bar `run.word.paused_operator_lost`. Same state on return. Worst case is 10 s plus the remaining time of one non-cancellable action. (Alex, 2026-11-01)

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
- Node types from adapter at startup; none → `palette.disconnected`, flows can't run; removed → red `status.action_missing` (ui-spec §16), kept.
- Action fields: `id`, list `version`, `params`, `outputs` (incl. image), `side_effect` (none/read/motion; default motion), `return_mode` (blocking/poll), `timeout_s`, optional `typical_s`, `cancel`, `preconditions`, optional `precondition_wait_s`, `read_state` (required for motion).
- `params` entry: name, type, unit, range or options, default, required.
- `cancel`: cancellable or not; where the robot stops; per-cancel accepted flag.
- Result carries measured end state.
- Missing required field → no node; panel names it. Names and types from interface, rest hand-written; param form generated from it.
- Action-list version locked with flow; mismatch fails before start (inference).
- Stop and cancel outcome per action; refused cancel keeps node running, shows `status.cancel_rejected`; non-cancellable runs to end.
- Non-cancellable `motion` action with `timeout_s` > 10 s: Run blocked, checked before every run, names the node (cap inference, Thompson; Tedrake to confirm).
- Cancel during execution goes through the adapter (ROS 2 actions can cancel a goal mid-execution); the executor does not only check between nodes.
- Node stays running until final result.
- Camera node: `read`, one image in Output tab with time, round, step (ui-spec §16); no thumbnail; memory only; log dimensions, time, size.
- Running label: "running · 40%" or "running · 12 s"; no progress bars.
- Snapshot = run-record input plus upstream-config fingerprint; upstream param/code change → maybe stale, still usable, not deleted.
- Entry ports: cache (hollow, run time); stale (slashed, `src.stale`, hover lists changed nodes, no red); pinned (filled with pin, `src.pinned`, never expires).
- Pause on `asyncio.Event`; restart resumes before next node; no data via process memory.
- Pydantic → JSON Schema; same-type ports only; images as metadata (exec-model v0.2 §2.2); large outputs path + hash (inference).
- Side effects: `none`, `read` (cached replay), `motion` (real, confirm first), `write` (test, mock, idempotency key); adapter `write` → `rejected_actions`.
- Data only via edges.

Loops (§5.1)

- Stop: condition exit, step limit (`run.word.step_limit`), user. No exit: warn on save, refuse run.
- Step limit default 1000 (Alex, 2026-11-01). No round limit.
- Breakpoints every round, or "only round N", or condition comparing existing input fields (no Python); Step over back edge enters next round.
- Record key run id + global step, plus node and round; no overwrite; unread edge data persisted.
- Staleness includes the node itself in a loop.
- Single run in loop: pick round (default last). Partial-loop group: back edge is an entry port (chosen round or pin), no looping inside.
- Mid-loop rerun continues that round; earlier records kept.

Control nodes (§5.2)

- Start, End, Constant, Condition (only loop exit), Merge, Human approval (exec-model §2.4); `none`. End replaces Output (type `end`); no extra node (Alex, 2026-11-01).
- A flow has exactly one Start and one End; a new flow starts with both. Run is refused, naming the problem, if either is missing or duplicated, or if no path leads from Start to End. Run group (step 8) skips this check.
- End has one input port and no output port; the run result is End's input, shown on End and in the panel when the run finishes.
- Reaching End does not end the run; the run completes when no node is left to run, as main does now (`executor.py:1090-1093`). Reason: stopping at End would cut off branches still running, including `motion` actions that cannot be cancelled. Nodes with no input (Constant) still run first.
- A file with a node of kind `output` is refused at import; the message names the node and says `output` is now `end`. No conversion, `format_version` stays 1 (Steve, 2026-11-01). Test group: "text template", "model call", "tool call".
- No seventh "wait" node (Tedrake). Reach goal: `return_mode = blocking`; JTC succeeds within goal tolerance and `constraints.goal_time` ([JTC](https://control.ros.org/master/doc/ros2_controllers/joint_trajectory_controller/doc/userdoc.html)); Nav2 goal checker accepts position and heading ([SimpleGoalChecker](https://docs.nav2.org/configuration/packages/nav2_controller-plugins/simple_goal_checker.html)). Wait for person: Human approval. Long or non-robot waits: not v1.
- `precondition_wait_s`: poll `read_state` until true, else `precondition_failed`; absent → check once; shows `status.running_wait`; `timeout_s` starts at send; Stop → stopped, no read-back; resume restarts wait; restart → pending (exec-model v0.2.1 §2.5.5, A38); one record. Poll interval and fields: adapter author.
- Blocking returns before stable and no stable field in `read_state` → adapter must add one (pre-real-robot check).

Hardware

- Robot model and interface specs await a source (Alex or Tedrake).
- Backend shares the controller with control (inference): normal priority, off control-loop cores, memory cap; control threads never wait on it. ros2_control loops are real-time ([controller_manager](https://control.ros.org/rolling/doc/ros2_control/controller_manager/doc/userdoc.html), [RT Linux](https://docs.ros.org/en/jazzy/Tutorials/Miscellaneous/Building-Realtime-rt_preempt-kernel-for-ROS-2.html)).
- Canvas Stop is not e-stop; e-stop is the robot's hardware path, never web or HTTP; no UI e-stop button. After Stop the controller stays powered and holds the current position; never cut power or torque, since an arm falls under gravity when torque is cut (Tedrake). No move to a safe pose (Alex, 2026-11-01). In v0.1 every action declares what it does after Stop or cancel: `hold` (the gripper keeps its grip) or `finish_step`. A `finish_step` action counts as non-cancellable: it runs to its end under the rule 14 cap of 10 s, then holds. An action declaring `safe_pose` is refused when the adapter loads; the fake adapter's `fake_gripper` changes to hold (Steve, 2026-11-01). JTC (ros2_controllers' joint trajectory controller) holds position by default after a trajectory is cancelled ([JTC docs](https://control.ros.org/master/doc/ros2_controllers/joint_trajectory_controller/doc/userdoc.html)); check the first robot's controller once chosen (Tedrake). Proposed: IEC 60204-1 category 2 (hold, motors on; Thompson's suggestion, not certified).
- After cancel: robot state and gripper hold read back and shown.
- Adapter reports `name`, `version`, `target` (sim/real, a field, not name-guessed), action-list version.
- No per-run sim/real toggle; adapter name right of flow name; red `real_tag` tag when real.
- v1: fake adapter, invented actions named as fake (sim).
- v0.1 acceptance and CI stay on the fake adapter. It models time only: no kinematics, no real stop delay, `stable` is a set flag, camera is a generated image, no base. Before real robot: a ROS 2 Jazzy sim adapter on MuJoCo with mujoco_ros2_control, starting with one arm JTC goal plus cancel to measure cancel-to-stop time and hold; JTC goal tolerance as the cited `stable` (Tedrake, `/workspace/robotics/roboact-sim-choice-2026-11-01.md`; apt package, Thor rendering and stop delay unverified).
- Adapter heartbeat; loss cancels active goals (ROS 2 goals outlive clients, [rclcpp #2241](https://github.com/ros2/rclcpp/issues/2241)).
- Before real robot: the adapter or robot side stops on its own when the server heartbeat is lost, so a hung server still stops motion; the server's 10 s page pause is a backup (Tedrake, Thompson; inference).
- `timeout_s` → controller expiry; controller without it: description says "expiry by adapter only", UI flags it.
- Crash: `motion` → result unknown; poll actions query goal, blocking use `read_state`; human confirms.
- Adapter silent 3 s → pause, `run.word.paused_adapter_lost`; deadline sent to adapter; 60 s Python timer as backup. Defaults adjustable, no source (Torvalds).
- The browser runs on another computer on the LAN (Alex, 2026-11-01). How it connects is Alex's open choice:
  - SSH forwarding (current code, Steve's recommendation): listen 127.0.0.1 only; no `--host`; the other computer runs `ssh -L`; no login in v0.1.
  - Direct IP: `--host`, session login and Host/Origin allow-list per Thompson's `lan-auth-v1.md` (sha256 `d0c998f2…913039dc`), `r-token` screen (Rams); its own PR updates this section, A40 and acceptance 19.9.
- Network loss: each action's adapter-defined behavior.
- A40 (exec-model v0.2.3 §0.2): `Host` `127.0.0.1`/`localhost`/`[::1]`; same `Origin`; `Content-Type: application/json`.

### Flow file (import / export) — added 2026-10-31 at Alex's request

- Format: `flow-file-format-v1.md` (Thompson); where it differs, this PRD wins. JSON and YAML share one schema; JSON is canonical.
- Export the current flow as `.json` or `.yaml`; default file name is the flow name. Exporting an unchanged flow twice gives identical files; saving without edits does not bump `flow.revision`. Import accepts `.json`, `.yaml` and `.yml`.
- The file holds `format_version`, `flow.revision`, the adapter's `action_list_version`, the robot and adapter it came from, angle and length units, nodes (`id`, `kind`, `action`, `params`, `position`) and edges by node id and port name (loop back-edges are ordinary edges).
- Not in the file: run records, pins, images, breakpoints, undo history, zoom, theme, language. Params marked secret export as `null`; after import that node shows "missing input" and Run stays disabled until the value is filled in.
- Import creates a new flow and opens it; it never overwrites an existing flow and never starts a run.
- All checks run before anything changes. Rejected (canvas unchanged, dialog lists each error with its path, e.g. `nodes[3].params.speed`): newer `format_version`, unknown fields, duplicate keys, more than one YAML document, a `%YAML` directive, too deep or too large, duplicate ids, edges to missing nodes or ports, mismatched port types, loops without an exit. Older `format_version` is migrated step by step.
- Actions the current adapter lacks load as "action gone". Run stays disabled while any such node remains; the user must replace its action or delete it. The Run button shows how many remain.
- A different robot, adapter, `action_list_version` or units shows an amber bar listing each difference; Run stays disabled until "Review and enable run". This button clears only this lock, never "action gone" or "missing input". The server stores the approval for that exact robot, adapter, `action_list_version` and units; any change locks Run again, and a client cannot set it.
- Import checks file structure only (version, fields, edges). Before every run, each param, including nested ones, is checked against its action's JSON Schema (type, enum, range): a hand-written unquoted `1.10` for a string param (YAML reads it as the number 1.1) blocks the run and names the node and param. `motion` params are checked before every run against the connected robot's joint and velocity limits; any out-of-limit param, including nested ones, blocks the run and names the node and param. Node marks ("action gone", "missing input") clear as soon as the cause is fixed, without a reload.
- Limits (inference, no source): request body ≤ 5 MiB, refused before reading the rest; nesting depth ≤ 64; string ≤ 65536 chars. Any bad file gives a listed error, never a 500.
- Parsing runs off the main server loop; an import never delays a running flow or the adapter heartbeat.
- YAML is parsed on the server only, with YAML 1.2 core rules, safe mode, no custom tags, no anchors or aliases (ruamel.yaml `YAML(typ='safe', pure=True)`, version locked with uv and installed by the offline install in the README; it allows aliases by default, so the server rejects them itself). Export quotes every string.

## 4. Acceptance criteria

1. Offline, Python-only machine runs the app; no build; requests only to 127.0.0.1. Steps: acceptance.md#1

1.1 CI fails on any vendored-file sha256 mismatch. Steps: acceptance.md#1.1

2. Breakpoint before node 3 stops there; nodes 3 and 4 get no records. Steps: acceptance.md#2

3. Input edited at a breakpoint reaches downstream. Steps: acceptance.md#3

4. Refresh at a breakpoint keeps node and I/O. Steps: acceptance.md#4

5. Single-node run records only that node; single-node and group runs refused while a flow is running, in UI and server. Steps: acceptance.md#5

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

19.2 Server writes paused state to first browser frame ≤ 100 ms, p95 ≤ 20 ms (A5). Not from the click, not from the browser receiving the event. Steps: acceptance.md#19.2

19.3 0 node-size updates across zoom 0.6 and selection. Steps: acceptance.md#19.3

19.4 `precondition_wait_s` sends when stable, else `precondition_failed` (A38). Steps: acceptance.md#19.4

19.5 Themes follow system, persist; six states distinguishable; fonts offline. Steps: acceptance.md#19.5

19.6 Language picked from browser, switched live, fully translated; codes survive restart. Steps: acceptance.md#19.6

19.7 UI settings persist, sync, fall back; server values never in `localStorage`. Steps: acceptance.md#19.7

19.8 Private IP in `.agents/docs/` or `docs/` blocks push. Steps: acceptance.md#19.8

19.9 A40 checks reject bad `Host`, `Content-Type`, `Origin`. Steps: acceptance.md#19.9

19.10 `scripts/check.sh` passes on clean clone and aarch64; failures block merge except 19. Steps 5 and 7 need a real ROS adapter: before real robot, not v0.1 (Steve, 2026-11-01). Steps: acceptance.md#19.10

20. Flow file: export, import, export is byte-identical; YAML converted to JSON equals the JSON export; unquoted `mode: on` stays a string; bad files change nothing; "action gone", "missing input" and mismatched files all lock Run. Steps: acceptance.md#20

21. Start and End: exactly one each; no path from Start to End refuses Run; result shown on End; run ends when nothing is left to run; `output` files refused. Steps: acceptance.md#21

A6: passing (PR #6). A38: 19.4. A40: 19.9. R1–R6 include two checks; Steps: acceptance.md#r1-r6

## 5. Risks and open questions

- A6 passed in PR #6 (main `2cdd0f4`): cancellable 30 s action, TCP cut, paused at 9.631–9.678 s over 5 runs; `timeout_s` = 11 non-cancellable `motion` blocks Run (Torvalds). Fake adapter only; the 10 s cap still needs Tedrake's per-robot check before the real robot.
- Click to Paused: 35–54 frames under CI load, from queued node updates (Torvalds); same 3–4 frames on main and the A6 branch locally. Outside A5; fix in executor with A6; no limit set (Alex).
- Cancel stop behavior (hold in place or decelerate) depends on each controller; adapter must state and test it (Tedrake, inference).
- Only 500 nodes tested; at thousands, LiteGraph (canvas) may win; re-measure first.
- Perf numbers: headless, software rendering, ~60 Hz cap; compare libraries only.
- React 19 has no UMD (esm.sh per upgrade).
- Side-effect marks and no-memory data rely on node authors; check in code review. External state may change before single-node reruns.
- Alex (2026-11-01): v0.1 runs on simulation only; first robot, controller and version not chosen, so heartbeat timing and the 10 s cap stay open. Target robot is a quadruped with an arm and a wrist camera (Steve); simulator: see robot section (Tedrake). JetPack 7 real-time kernel and controller = Thor (not blocking v1).
- Torvalds: re-measure 19, 19.1; confirm 60 s timeout, 2 s SSE reconnect, 19.2 20 ms; fake-adapter actions.
- Thompson: exec-model v0.2 keeps v0.1 storage and restart pause, A18, A20; adds action fields, pause/stop/crash under both `return_mode` values, cancel refused, `motion` rules (no mock, no idempotency dedupe, read state before resume).
- Rams: English status words and buttons in the zh table; verify English fits nodes and top bar.
- Tedrake: once vendor known, cite stop, controller power-loss and gripper power-loss behavior (adapter acceptance, not blocking v1).
- Tedrake: confirm control nodes; `node-list-v0.md` obsolete (9 nodes kept for executor tests).
- Flow file: export writes keys in a fixed order so git diffs stay small; positions stay in the file (Alex, 2026-11-01). Keys sorted, nodes by id, edges by their endpoints, 2-space indent, one final newline (already on main, `flow_file.py`, Torvalds); floats in the shortest form that reads back the same. Lists inside params (waypoints, loop steps) keep their order; only top-level nodes and edges are sorted (`flow-file-format-v1.md`, sha256 `412b44f4…628fee`, Thompson). Load then save with no edits gives the same bytes; a one-field edit changes only that line.
