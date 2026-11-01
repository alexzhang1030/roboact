# RoboAct flow file format v1

Status: proposal by Thompson · Systems, 2026-10-31. For Fadell (PRD/acceptance) and Torvalds (implementation, separate PR after #2).
Basis: prd-en-v1.5.md (flows stored as nodes, ports, edges, params, version; action fields; adapter reports action-list version; loops; "action gone" state per ui-spec).

## 1. One structure, two encodings
- JSON and YAML encode the same object tree. Import parses either into that tree, then runs one validator.
- YAML is read with the YAML 1.2 core schema only, in safe mode: no custom tags, no anchors/aliases, no merge keys (`<<`). Reject the file if any appear.
  Why: under YAML 1.1 rules `yes`/`no`/`on`/`off` become booleans, so a param changes without any error.
- Unquoted `1.10` is a float `1.1` under YAML 1.2 core too (Torvalds tested). Quoting on export does not help hand-written files, so param types are checked before every run (with the motion limit check) against the current adapter's JSON Schema for each action: a string param holding a number, or the reverse, refuses the run and names node and param. Import does not check param types, because the adapter may not be connected at import.
- ruamel.yaml `YAML(typ='safe', pure=True)` accepts anchors/aliases by default; the importer must detect and refuse them itself.
- YAML export quotes every string value, so a string param such as `"on"` or `"0123"` survives.
- Integers outside ±2^53 cannot round-trip through JS numbers. Validator rejects them with the path (inference: only matters if an action has such a param).

## 2. Top level
```yaml
format: roboact-flow          # fixed string; anything else -> refuse
format_version: 1             # integer; version of THIS file layout
flow:
  id: "flow-…"                # kept on import
  name: "pick-and-place"
  revision: 12                # the flow's own version counter (PRD "version lock")
  step_limit: 200             # loop limit N
  action_list_version: "…"    # from adapter at export time
  adapter: { name: "…", version: "…", target: "sim" }   # informational; not checked on import
nodes: [ … ]                  # sorted by id (§8)
edges: [ … ]                  # sorted by (from.node, from.port, to.node, to.port) (§8)
```
`format_version` and `flow.revision` are different things: the first changes when the file layout changes, the second changes every time the user edits the flow.

## 3. Nodes and edges
```yaml
nodes:
  - id: "n7"
    kind: action              # start | end | constant | condition | merge | human_approval | action
    action: "arm.move_to"     # only when kind=action
    title: "Move to bin"
    params: { speed: 0.3, frame: "base" }
    position: { x: 640, y: 220 }   # canvas coordinates; required (Rams item 4)
edges:
  - from: { node: "n7", port: "done" }
    to:   { node: "n9", port: "in" }
```
- Loops need no extra structure: a back edge is an ordinary edge. Validator applies the same rule as the editor (a loop must have a condition node as exit, PRD acceptance 18).
- Edges point at node id + port name, never at array index, so reordering never breaks them.
- Reserved for later (PRD "BT/graph later"): `edges[].type` (data/control). v1 writers omit it; v1 readers refuse it, so a v2 file is not silently read as v1.

## 4. Not in the file
Run records, pins, run I/O, camera frames/images, breakpoints, undo history, zoom, theme, language, and any param the action marks as secret (API key, token).
Breakpoints and pins are debugging state tied to runs; leaving them out means an imported flow starts clean. Fadell to confirm this is the wanted behaviour.
Secret params: written as `null`; import marks the node "missing input"; Run disabled until filled. Without this rule an exported "model call" node leaks its key.

## 5. Import: order of checks, all before the canvas is touched
1. Parse. Error shows line:column (YAML) or byte offset/line (JSON).
2. `format` wrong -> refuse.
3. `format_version` > supported -> refuse: "file is from a newer RoboAct (v2), this build reads v1". Lower -> migrate step by step, then continue.
4. Structure: required fields, types, unknown fields -> refuse with path, e.g. `nodes[3].params.speed`. Unknown fields are refused, not dropped, so nothing disappears silently.
5. Graph: duplicate node id, edge to missing node/port, port type mismatch, loop without exit -> refuse with path.
6. Built-in `kind` unknown -> refuse. `kind: output` (renamed to `end`) is refused here with the node id, e.g. `nodes[2] (id n_out): kind "output" was renamed to "end"`; never converted on import. After the kind check: exactly one `start` and exactly one `end` node, else refuse naming the count found (checked after `output`, so an old file gets the rename message, not "0 end nodes"). `format_version` stays 1.
7. `action` not offered by current adapter -> import anyway, node shows "action gone" (ui-spec).
8. `action_list_version` differs from adapter -> import anyway; run fails before start, as the PRD already says.
9. Only now create and open a new flow. Any refusal in 1–6 leaves the canvas exactly as it was.

## 6. Acceptance (for Fadell to phrase)
- Round trip: export JSON -> import -> export JSON gives byte-identical output (canonical bytes: §8). Same for YAML. Export YAML -> import -> export JSON equals the direct JSON export.
- Test flow must include: a loop with back edge, a Constant with string params `"on"`, `"0123"`, `"1.10"`, a float `0.1`, an empty string, a node whose action the adapter lacks.
- Bad files, each must refuse with the path shown and leave the canvas unchanged: truncated JSON, YAML with `!!python/object` tag, YAML alias, `format_version: 2`, edge to missing node, duplicate id, unknown field, a node with `kind: output`, zero or two `end` nodes.
- Hand-written YAML: a string param written unquoted as `ver: 1.10` imports, but Run is refused with node and param named; `ver: "1.10"` runs.
- Hand-written YAML import: a string param written unquoted as `mode: on` must import as the string `"on"`. Switching the parser to YAML 1.1 rules turns it into `true`, so this case is how to check the test covers the parser setting.

## 7. Aligned with PRD sha256 4acfd471…e295be37 (2026-10-31 22:23) — overrides sections 4 and 5
- Breakpoints, pins and run records are NOT in the file (Fadell confirmed).
- Action unknown to current adapter -> import anyway, node shows "action gone"; Run stays disabled while any such node exists. "Review and enable run" does not unlock it.
- Robot, adapter, action-list version or units differ -> import, amber banner, Run disabled until user confirms ("Review and enable run").
- Import always creates and opens a new flow; never replaces, never starts a run. Accepts `.json`, `.yaml`, `.yml`.
- "Review and enable run" unlocks only robot/adapter/action-list/units mismatch. It does not unlock "action gone" or "missing input" nodes; Run shows how many remain.
- YAML is parsed on the server with ruamel.yaml `YAML(typ='safe', pure=True)`, version locked with uv. Not PyYAML (YAML 1.1). Aliases refused by our own check.
- New fields (Tedrake): `flow.robot: { id, model }` as reported by the adapter; `flow.units: { angle: "rad"|"deg", length: "m"|"mm" }`. Adapter must report a robot id; PRD adapter fields today are name, version, target, action-list version only.
- Limits (constants, inference, no source): MAX_FILE_BYTES = 5 MiB checked before parsing; MAX_DEPTH = 64; MAX_STRING_CHARS = 65536. PRD perf target 500 nodes / ~550 edges is far below 5 MiB.
- Motion params (per Tedrake, 22:20): NOT checked at import. Before every run, compare each `side_effect: motion` node's params with the connected robot's joint limits and velocity limits; any out of range -> run refused, node and param named. Import-time checks are structure only (section 5 steps 1-6); param types and limits are checked before run, because the robot may not be connected at import and may change afterwards.

## 8. Canonical bytes

An exported file is for git. Open a file that is already in this spelling, export it again with no edits, and the bytes are the same. One edit changes only the lines of that edit. JSON and YAML are two spellings of one document; each spelling has one byte sequence. `export_flow` is the only writer. The browser posts `{flow_id, format}` to `POST /api/flows/export` and saves the returned `text`.

1. Key order. Every object's keys are written in Unicode code point order (`sorted`, case-sensitive: `A` before `a` before `b`), not in the order they arrived from the browser or from the file. Absent keys are omitted. A key the schema does not name is still refused on import (§5). If a dump is asked to write one anyway, it is included in that same alphabetical order.
2. Lists. Only the top-level `nodes` list is sorted, by `id`, and only the top-level `edges` list is sorted, by `(from.node, from.port, to.node, to.port)`. Every list inside `params` keeps its original order. That includes waypoints, loop body steps, and any list nested under them. JSON and YAML export do not sort those lists.
3. Numbers.
   - An integer is written as an integer. `1` stays `1`.
   - A float is written with Python's shortest round-trip spelling (`repr`, the spelling `json.dumps` uses). `0.1` is `0.1`, never `0.10000000000000001`. When that spelling uses an exponent it has two exponent digits: `1e-7` is `1e-07`. `1.0` stays `1.0`, so it does not become the integer `1`. Signed zero stays `-0.0`.
   - `NaN` and `Infinity` (either sign) are refused on export and on import. They are not JSON.
   - Booleans are `true` and `false`. JSON null is `null`. YAML null is an empty scalar (`key:` with nothing after it), which parses back as null.
4. Layout. Both encodings:
   - Indent is two spaces. JSON nests two spaces per level, `{` and `}` on their own lines, one space after `:`. YAML is block style: a nested key is two spaces in from its parent; a sequence dash is two spaces in from its parent key; the keys of that element are two spaces in from the dash.
   - Line breaks are `\n` only. Lines are not folded or wrapped.
   - UTF-8, no BOM. Non-ASCII characters are written as themselves, not as `\u` escapes.
   - The file ends with exactly one `\n`. No leading `---` and no `%YAML` directive.
   - JSON strings use ordinary JSON escapes. YAML double-quotes every string value (§1).
