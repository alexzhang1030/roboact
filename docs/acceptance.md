# RoboAct v1 acceptance procedures

Version 1.5 (2026-10-31). Companion to `prd-en-v1.5.md` section 4. exec-model = `exec-model-v0.md`.

<a id="1"></a>
## 1. Zero build, offline
1. Use a clean machine with only Python, network off, dependencies available locally.
2. `pip install`, start the server, open the browser.
3. Pass: the app works.
4. Check the repo: no build script; no `.ts` source compiled by any tool outside the browser.
5. Browser network panel: every request goes to the backend (127.0.0.1); none go to the internet.

<a id="1.1"></a>
## 1.1 Traceable dependencies
1. Change one byte in one vendored file.
2. Run CI. Pass: CI recomputes sha256 of every vendored file, reports the mismatch with the manifest, and blocks merge.

<a id="2"></a>
## 2. Breakpoint
1. Set a before-run breakpoint on node 3; run the whole flow.
2. Pass: run stops at node 3; nodes 3 and 4 have no new run records.

<a id="3"></a>
## 3. Edit and continue
1. At a breakpoint, change one input; continue.
2. Pass: the downstream node's record shows the changed value.

<a id="4"></a>
## 4. Refresh
1. While stopped at a breakpoint, refresh the page.
2. Pass: still stopped at the same node; input and output still shown.

<a id="5"></a>
## 5. Single node
1. Run a middle node using the last run's record.
2. Pass: only that node gets a new record.
3. Start a full run with a fake `motion` action and, while it is running, open a node's menu, then box-select two nodes. Pass: "Run this only", "Run for real" and the group run button are disabled and show the reason. Pause the flow: all three are enabled. Let it finish: all three are enabled.
4. While a full run is running, call the single-node run endpoint and the group run endpoint directly (not through the UI). Pass: the server refuses each with the reason; no new record; the robot state does not change from either call.

<a id="6"></a>
## 6. Group
1. Box-select 2 middle nodes; run the group.
2. Pass: only those 2 nodes get new records; entry data equals the chosen run's upstream output.

<a id="7"></a>
## 7. Rerun after error
1. Make a node fail; rerun from it.
2. Pass: upstream nodes get no new records.

<a id="8"></a>
## 8. Side effects
1. Rerun a `write` node alone, with the external service replaced by a test stub. Pass: the stub gets no second request.
2. Replay a `read` node. Pass: it uses the cached value.
3. Rerun a fake-adapter `motion` node alone. Pass: a confirmation appears first; without confirming, no action is sent.

<a id="9"></a>
## 9. Version lock
1. While stopped at a breakpoint, edit the canvas.
2. Pass: the run continues on the old version; `banner.rerun` appears.

<a id="10"></a>
## 10. Color-blind check
1. Take a grayscale screenshot of the UI.
2. Pass: error, breakpoint and done states are distinguishable.

<a id="11"></a>
## 11. Server restart
1. Stop at a breakpoint; restart the server; continue to the end.
2. Pass: final output equals a run without restart.
3. For nodes whose result varies per run (e.g. model call), compare against the cached value in the record.

<a id="12"></a>
## 12. Snapshot staleness
1. Use two nodes not in the same loop.
2. Change only the downstream node's params; rerun it. Pass: upstream snapshot not marked "maybe stale".
3. Change an upstream node's param. Pass: downstream snapshot marked "maybe stale"; slash and `src.stale` shown.

<a id="13"></a>
## 13. Human approval
1. Run a flow with a Human approval node in the middle.
2. Pass: run stops there, state `status.awaiting`; no new downstream records.
3. Click Reject. Pass: only the reject branch runs; approve-branch nodes show "skipped".
4. Click from two pages at once. Pass: first wins; second is told it was already handled.

<a id="14"></a>
## 14. Loop exit
1. Run a loop whose exit condition becomes true on round 3.
2. Pass: each loop node has exactly 3 records, rounds 1, 2, 3.

<a id="15"></a>
## 15. Step limit
1. Run a loop whose exit never holds, with total step limit 10.
2. Pass: stops after step 10; state `run.word.step_limit`; no step 11 record.

<a id="16"></a>
## 16. Loop breakpoints
1. Set a breakpoint in a loop with default settings; run. Pass: stops every round.
2. Change it to "only round 2"; run. Pass: round 1 does not stop; round 2 stops.

<a id="16.1"></a>
## 16.1 Breakpoint on round 2
1. Set the breakpoint to round 2; run.
2. Pass: when stopped, the node has only its round 1 record.

<a id="17"></a>
## 17. Single run in a loop
1. Run one loop node alone with round 2 input.
2. Pass: new record's input equals the round 2 record; no other node gets a record.

<a id="18"></a>
## 18. Loop without exit
1. Build a loop with no condition node as exit; click Run.
2. Pass: run refused; message says what is missing.

<a id="18.1"></a>
## 18.1 Restart mid-loop
1. Restart the server halfway through a loop; continue to the end.
2. Pass: final result equals a run without restart.

<a id="18.2"></a>
## 18.2 In-loop snapshot
1. Change a loop node's own params.
2. Pass: its later rounds' input snapshots are marked "maybe stale".

<a id="19"></a>
## 19. Canvas performance (inference; formal only after re-measuring on Alex's computer)
1. Load 500 nodes and about 550 edges; switch each node's state 10 times per second; pan and zoom at the same time.
2. Convert each frame interval to frames with `round(dt / 16.67)`. Do not compare milliseconds (frame times are multiples of 16.7 ms; the old "33 ms" threshold was a typo for 1 frame).
3. Pass: p95 of frames ≤ 2.
4. CI runs it and prints the numbers; failure does not block PRs (`7d1fc79` gave p95 33.4 ms; earlier the same test gave 16.7 ms).
5. Formal pass on Alex's computer: no long task over 100 ms after mount.
6. Reference (Torvalds, headless Chrome, software rendering, after the size rules): p95 16.7 ms, worst frame 33.4 ms, 0 long tasks (`/workspace/agent-ui/spike/results.json`).

<a id="19.1"></a>
## 19.1 SSE batching
1. After the v1 backend exists, rerun 19 with real Python SSE pushes.
2. Pass: browser receives at most 10 state events per second; frame times meet 19.

<a id="19.2"></a>
## 19.2 No batching delay on stop
1. Trigger breakpoint, step and user pause 20 times each.
2. Server and browser on the same machine. Log the time the server writes the paused state, and the time of the first browser frame after the pause event. Do not start from the click or from the browser receiving the event.
3. Pass: every difference ≤ 100 ms; p95 ≤ 20 ms (20 ms is Thompson's inference, set by Torvalds after measuring; exec-model A5).

<a id="19.3"></a>
## 19.3 Fixed node size
1. Cross zoom 0.6 six times; toggle selection six times.
2. Pass: Vue Flow node-size updates = 0 (`spike/results_select.json`).

<a id="19.4"></a>
## 19.4 Wait for stable (A38)
1. Fake-adapter action with `precondition_wait_s = 2`; `read_state` returns "not stable" for the first 1 s, then "stable". Pass: action sent after about 1 s; one record.
2. Make `read_state` always "not stable". Pass: `precondition_failed` after 2 s; action not sent.
3. Remove the field. Pass: `precondition_failed` on the first "not stable" read.

<a id="19.5"></a>
## 19.5 Light and dark
1. Set the system to dark; open for the first time. Pass: dark.
2. Switch to light manually; refresh. Pass: still light.
3. Take a grayscale screenshot of each theme. Pass: six states distinguishable: error, breakpoint, done, interrupted · result unknown, page disconnected, robot controller disconnected (method as in 10).
4. Network off. Pass: fonts render in both themes; no font request leaves the machine.

<a id="19.6"></a>
## 19.6 UI language
1. Clear browser local storage. Open with browser language English, then Chinese, then French. Pass: English, Chinese, English.
2. Switch to Chinese. Pass: top bar, node panel, right panel, all status words and dialog buttons change; no reload; running flow does not stop. After refresh, still Chinese.
3. CI: both dictionaries have identical keys; frontend source has no hard-coded UI text (fail on any CJK character or English sentence not in a dictionary).
4. Delete one key from the zh dictionary. Pass: that spot shows English, not the key name.
5. Switch language, open an old run record. Pass: states shown in the new language.
6. Take one screenshot per screen per language. Pass: nothing untranslated, no truncated text.
7. In the Chinese UI, reach "interrupted · result unknown"; `kill -9` the backend; switch to English; restart. Pass: the step still waits for `retry` or `mark_done`, shown in English. If it became pending or error, UI text was stored.

<a id="19.7"></a>
## 19.7 UI settings
1. Clear local storage; set system dark; turn on browser reduced motion; open. Pass: dark, no state animation.
2. Change theme, language, panel width, grid; refresh. Pass: all kept.
3. Set theme to "follow system". Pass: returns to the system setting.
4. Write a bad value into `localStorage`. Pass: page opens and falls back to browser settings.
5. Open two tabs; switch theme and language in one. Pass: the other follows.
6. Clear browser storage, or open the same flow on another machine. Pass: theme and language return to browser settings; disconnect pause time, `timeout_s` and `precondition_wait_s` unchanged and absent from `localStorage`.

<a id="19.8"></a>
## 19.8 Pre-push check
1. Put a full private address in the 192.168 range into a file in `.agents/docs/`. The value lives only in the test, never in the PRD.
2. Run the pre-push check locally. Pass: blocked.
3. Repeat with the file in `docs/`. Pass: blocked.

<a id="19.9"></a>
## 19.9 Block other pages (A40)
1. With `curl`, send: a request with `Host: evil.example`; a command with `Content-Type: text/plain`; a WebSocket handshake from another origin.
2. Pass: all three rejected; the fake adapter receives no action.

<a id="19.10"></a>
## 19.10 Dev toolchain
1. On a clean clone run `scripts/check.sh`. Pass, in order: `uv sync --locked`, `ruff check`, `ruff format --check`, pyright, pytest, private-IP scan, gitleaks.
2. Pyright: strict mode for the `roboact` package, basic mode for tests, no file-wide ignores. Every remaining `# type: ignore` has a one-line reason.
3. GitHub CI runs the same script. Pass: any failure blocks merge (except 19).
4. CI linux/aarch64 job (`ubuntu-24.04-arm`) runs `uv sync --frozen` and pytest (Jetson Thor is aarch64).
5. (Before real robot, not v0.1.) Check `rclpy` is absent from `pyproject.toml`, including optional groups; it is imported only in adapter modules, from system Python via `--system-site-packages`; type checking ignores its missing import only there.
6. Check Python is 3.12 (Jazzy, Ubuntu 24.04, REP 2000).
7. (Before real robot, not v0.1.) Real robot: on aarch64 Ubuntu with ROS 2, a clean checkout passes `uv sync --frozen` and pytest; the adapter can `import rclpy`. No frontend build step.

<a id="r1-r6"></a>
## Pre-real-robot checks (part of R1–R6)
1. Start a cancellable fake `motion` action that runs 30 s. Cut the page connection for real through a TCP proxy. Pass: cancel reaches the adapter and the server pauses within 10 s of the cut; robot state is read back and shown. Run 5 times; report each time.
2. Repeat with a non-cancellable action with `timeout_s` ≤ 10 s. Pass: it runs to end, then the server pauses.
3. Set a non-cancellable `motion` action's `timeout_s` to 11. Pass: Run is blocked and names the node.
4. Make the adapter refuse the cancel. Pass: node shows `status.cancel_rejected`, runs to end, then pauses.
5. Confirm "stable" is detectable: blocking actions return only once stable, or `read_state` has a stable field; at least one, with a cited source. Neither: do not connect a real robot.
6. Press Stop while a cancellable fake `motion` action and a fake gripper action are running. Pass: the adapter is asked to hold; after the robot stops, state is read several times at a fixed interval and every change between reads is within the controller's goal tolerance; the gripper keeps its grip; nothing moves to a safe pose. Do not compare with the state at the moment of Stop: a real or MuJoCo JTC decelerates first, so the stop position differs by the braking distance (Tedrake, [JTC docs](https://control.ros.org/jazzy/doc/ros2_controllers/joint_trajectory_controller/doc/userdoc.html)). Press Stop while a fake `finish_step` action is running: it runs to its end, then holds; it never runs past 10 s. Load an adapter with an action that declares `safe_pose`: it is refused and the action is named.

<a id="20"></a>
## 20 Flow file import / export

1. Build a flow with a loop, a control node, a `motion` node and a param marked secret.
2. From the UI (not the API), export as JSON twice with no edits: identical files. Import, export again: byte-identical; the secret param is `null` and its node shows "missing input" with Run disabled until it is filled in.
3. Export as YAML, convert to JSON: equals the JSON export from step 2.
4. Files contain no run records, pins, images, breakpoints, undo history, zoom, theme, language or server address.
5. Import a hand-written `.yml` file with unquoted `mode: on`: it stays a string (swap the parser to YAML 1.1 rules: this must fail). In the same file, unquoted `ver: 1.10` for a string param imports, but Run is blocked and names that node and param; quoted `"1.10"` runs with `1.10`.
6. Import, one at a time, files with a newer `format_version`, an unknown field, a duplicate id, an edge to a missing port, mismatched port types, a loop without an exit, a custom tag, an alias, a duplicate key, two YAML documents, a `%YAML 1.1` directive, 1000 levels of nesting in 2 KB: each is refused (never a 500), nothing changes, the dialog shows the path of each error.
7. Import a file with an older `format_version`: it is migrated and loads.
8. Import a file with an unknown action: that node shows "action gone"; the Run button shows 1 remaining. "Review and enable run" does not enable Run; replacing the action or deleting the node does.
9. Import a file from a different robot, adapter, `action_list_version` or units: the amber bar lists each difference; Run is disabled until "Review and enable run". Then switch to another robot: Run locks again. Send the approval field from the client yourself: Run stays locked.
10. Import while a flow is open: a new flow is created; the old one is unchanged; the run list has no new entry.
11. Set a `motion` param above the connected robot's joint or velocity limit, press Run: the run is blocked and the node and param are named. Switch to a robot with wider limits: the same flow runs.
12. Import a file over 5 MiB: refused before the body is fully read.
13. Start a flow on the robot, then import an 800 KB YAML file: the running flow does not pause and the adapter heartbeat does not time out.
14. Set an enum param to a value outside its enum, and a nested `motion` param over its limit: Run is blocked and each is named.
15. Fill in the secret param on a "missing input" node: the mark clears without a reload.
16. Follow the README offline install on a clean machine: YAML import works.
17. Export a flow as JSON and as YAML and commit both. Change one param and move one node; export again. Pass: `git diff` shows only the changed param value and the moved node's position lines; both files end with one newline; a float such as `0.1` is written as `0.1`.

<a id="21"></a>
## 21 Start and End
1. Create a new flow. Pass: it has one Start and one End; End has an input port and no output port.
2. Add a second Start, press Run; then a second End, press Run; then delete End, press Run. Pass: each Run is refused and the message names the problem.
3. Connect Start to a node that does not lead to End, press Run. Pass: Run is refused and says End cannot be reached from Start.
4. Box-select two nodes with no Start or End and run the group. Pass: the group runs.
5. Run a flow where Start feeds End directly and also feeds a fake `motion` action on a parallel branch that finishes after End is reached. Pass: the run stays running until the action finishes, then shows completed; End shows its input value, and the panel shows the same value.
6. Import a JSON file and a YAML file that each contain a node of kind `output`. Pass: each import is refused before the canvas changes; the message names the node and says `output` is now `end`.
