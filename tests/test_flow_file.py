"""Acceptance #20 — flow file import / export."""

from __future__ import annotations

import json
from typing import Any

from roboact.adapter import FakeAdapter
from roboact.flow_file import (
    MAX_FILE_BYTES,
    dump_json,
    parse_and_validate,
    parse_yaml,
)
from roboact.nodes import bind_adapter

from .http_api import http_json, http_status

FORBIDDEN_KEYS = (
    "breakpoints",
    "pins",
    "records",
    "runs",
    "images",
    "undo",
    "zoom",
    "theme",
    "language",
    "server",
    "host",
)


def sample_doc(**flow_over: Any) -> dict[str, Any]:
    flow = {
        "action_list_version": "fake-v1",
        "adapter": {"name": "假适配器", "target": "sim", "version": "0.2"},
        "id": "flow-sample",
        "name": "sample",
        "revision": 3,
        "robot": {"id": "fake-1", "model": "FakeArm"},
        "step_limit": 200,
        "units": {"angle": "rad", "length": "m"},
    }
    flow.update(flow_over)
    return {
        "edges": [
            {"from": {"node": "start", "port": "data"}, "to": {"node": "cond", "port": "value"}},
            {"from": {"node": "cond", "port": "no"}, "to": {"node": "go", "port": "target"}},
            {"from": {"node": "go", "port": "pose"}, "to": {"node": "cond", "port": "value"}},
            {"from": {"node": "cond", "port": "yes"}, "to": {"node": "out", "port": "value"}},
        ],
        "flow": flow,
        "format": "roboact-flow",
        "format_version": 1,
        "nodes": [
            {
                "action": "fake_note",
                "id": "note",
                "kind": "action",
                "params": {"mode": "on", "token": "placeholder-token", "ver": "1.10"},
                "position": {"x": 40, "y": 320},
                "title": "Note",
            },
            {
                "action": "fake_set_pose",
                "id": "go",
                "kind": "action",
                "params": {"x": 0.4, "y": 0.1, "z": 0.2},
                "position": {"x": 400, "y": 160},
                "title": "Go",
            },
            {
                "id": "c_empty",
                "kind": "constant",
                "params": {"value": "", "value_type": "string"},
                "position": {"x": 40, "y": 480},
                "title": "Empty",
            },
            {
                "id": "c_float",
                "kind": "constant",
                "params": {"value": 0.1, "value_type": "number"},
                "position": {"x": 200, "y": 480},
                "title": "Float",
            },
            {
                "id": "c_on",
                "kind": "constant",
                "params": {"value": "on", "value_type": "string"},
                "position": {"x": 40, "y": 400},
                "title": "On",
            },
            {
                "id": "c_oct",
                "kind": "constant",
                "params": {"value": "0123", "value_type": "string"},
                "position": {"x": 200, "y": 400},
                "title": "Oct",
            },
            {
                "id": "c_ver",
                "kind": "constant",
                "params": {"value": "1.10", "value_type": "string"},
                "position": {"x": 360, "y": 400},
                "title": "Ver",
            },
            {
                "id": "cond",
                "kind": "condition",
                "params": {"compare": 1, "field": "value", "op": "gte"},
                "position": {"x": 200, "y": 160},
                "title": "Cond",
            },
            {
                "id": "out",
                "kind": "output",
                "params": {},
                "position": {"x": 600, "y": 160},
                "title": "Out",
            },
            {
                "id": "start",
                "kind": "start",
                "params": {"data": {"value": 0}},
                "position": {"x": 40, "y": 160},
                "title": "Start",
            },
        ],
    }


def run_doc(**param_over: Any) -> dict[str, Any]:
    params = {"mode": "on", "token": "filled", "ver": "1.10"}
    params.update(param_over)
    return {
        "edges": [
            {"from": {"node": "start", "port": "data"}, "to": {"node": "go", "port": "target"}},
            {"from": {"node": "go", "port": "pose"}, "to": {"node": "out", "port": "value"}},
        ],
        "flow": {
            "action_list_version": "fake-v1",
            "adapter": {"name": "假适配器", "target": "sim", "version": "0.2"},
            "id": "flow-run",
            "name": "runnable",
            "revision": 1,
            "robot": {"id": "fake-1", "model": "FakeArm"},
            "step_limit": 50,
            "units": {"angle": "rad", "length": "m"},
        },
        "format": "roboact-flow",
        "format_version": 1,
        "nodes": [
            {
                "action": "fake_note",
                "id": "note",
                "kind": "action",
                "params": params,
                "position": {"x": 40, "y": 300},
                "title": "Note",
            },
            {
                "action": "fake_set_pose",
                "id": "go",
                "kind": "action",
                "params": {"x": 0.4, "y": 0.1, "z": 0.2},
                "position": {"x": 240, "y": 160},
                "title": "Go",
            },
            {
                "id": "out",
                "kind": "output",
                "params": {},
                "position": {"x": 480, "y": 160},
                "title": "Out",
            },
            {
                "id": "start",
                "kind": "start",
                "params": {"data": {"x": 0.4, "y": 0.1, "z": 0.2}},
                "position": {"x": 40, "y": 160},
                "title": "Start",
            },
        ],
    }


def _import(base: str, text: str, fmt: str = "json") -> tuple[int, Any]:
    return http_status(base, "/api/flows/import", "POST", {"text": text, "format": fmt})


def _export(base: str, flow_id: str, fmt: str = "json") -> Any:
    return http_json(base, "/api/flows/export", "POST", {"flow_id": flow_id, "format": fmt})


def _snapshot(base: str) -> tuple[list[str], dict[str, Any], int]:
    boot = http_json(base, "/api/bootstrap")
    flows = http_json(base, "/api/flows")["flows"]
    runs = http_json(base, "/api/runs", "GET")
    n_runs = len(runs.get("runs") or [])
    return [f["flow_id"] for f in flows], boot["flow"], n_runs


def test_yaml_12_on_stays_string_and_11_does_not():
    text = "mode: on\nver: 1.10\n"
    assert parse_yaml(text)["mode"] == "on"
    assert parse_yaml(text)["ver"] == 1.1
    eleven = parse_yaml(text, version=(1, 1))
    assert eleven["mode"] is True


def test_20_round_trip_and_secret(server):
    base = server["base"]
    bind_adapter(FakeAdapter())
    doc = sample_doc()
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    exp_json = _export(base, fid, "json")
    again = _export(base, fid, "json")
    assert exp_json["text"] == again["text"]
    tree = json.loads(exp_json["text"])
    note = next(n for n in tree["nodes"] if n["id"] == "note")
    assert note["params"]["token"] is None
    blob = json.dumps(tree)
    for key in FORBIDDEN_KEYS:
        assert f'"{key}"' not in blob
    exp_yaml = _export(base, fid, "yaml")
    from_yaml = parse_and_validate(exp_yaml["text"], "yaml")
    assert dump_json(from_yaml) == exp_json["text"]
    code, redacted = _import(base, exp_json["text"], "json")
    assert code == 200, redacted
    assert "note" in redacted["missing_input"]
    rid = redacted["flow"]["id"]
    assert _export(base, rid, "json")["text"] == exp_json["text"]
    check = http_json(base, f"/api/flows/{rid}/run-check")
    assert not check["can_run"]
    code, _ = http_status(base, "/api/runs", "POST", {"flow_id": rid, "flow_version": 1})
    assert code == 409
    graph = http_json(base, f"/api/flows/{rid}")["flow"]
    for node in graph["nodes"]:
        if node["id"] == "note":
            node["params"]["token"] = "filled"
    http_json(base, f"/api/flows/{rid}", "PUT", {**graph, "bump": True})
    check = http_json(base, f"/api/flows/{rid}/run-check")
    assert "note" not in check["missing_input"]


def test_20_handwritten_yaml_types(server):
    base = server["base"]
    yml = """
format: roboact-flow
format_version: 1
flow:
  id: "hand"
  name: "hand"
  revision: 1
  step_limit: 40
  action_list_version: "fake-v1"
  adapter: {name: "假适配器", version: "0.2", target: "sim"}
  robot: {id: "fake-1", model: "FakeArm"}
  units: {angle: "rad", length: "m"}
nodes:
  - id: "start"
    kind: start
    title: Start
    params: {data: {x: 0.1}}
    position: {x: 0, y: 0}
  - id: "note"
    kind: action
    action: fake_note
    title: Note
    params: {mode: on, ver: 1.10, token: "filled"}
    position: {x: 40, y: 80}
  - id: "go"
    kind: action
    action: fake_set_pose
    title: Go
    params: {x: 0.1, y: 0.1, z: 0.1}
    position: {x: 200, y: 0}
  - id: "out"
    kind: output
    title: Out
    params: {}
    position: {x: 400, y: 0}
edges:
  - from: {node: start, port: data}
    to: {node: go, port: target}
  - from: {node: go, port: pose}
    to: {node: out, port: value}
"""
    code, imported = _import(base, yml, "yml")
    assert code == 200, imported
    note = next(n for n in imported["flow"]["nodes"] if n["id"] == "note")
    assert note["params"]["mode"] == "on"
    assert note["params"]["ver"] == 1.1
    fid = imported["flow"]["id"]
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["type_errors"]
    assert any(e["param"] == "ver" for e in check["type_errors"])
    code, body = http_status(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1})
    assert code == 409
    assert body["error"] == "param_type_mismatch"
    assert "ver" in body["message"]
    note["params"]["ver"] = "1.10"
    http_json(
        base,
        f"/api/flows/{fid}",
        "PUT",
        {**imported["flow"], "nodes": imported["flow"]["nodes"], "bump": True},
    )
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["can_run"]
    started = http_json(
        base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 2, "max_steps": 20}
    )
    assert started["run_id"]


def test_20_bad_files_leave_canvas(server):
    base = server["base"]
    ids0, graph0, runs0 = _snapshot(base)
    bad: list[tuple[str, str]] = [
        ("json", '{"format": "roboact-flow", "format_version":'),
        (
            "yaml",
            "format: roboact-flow\nformat_version: !!python/object:os.system\n",
        ),
        (
            "yaml",
            "format: roboact-flow\nformat_version: 1\n"
            "flow: &x {id: a, name: a, revision: 1, step_limit: 1}\n"
            "nodes: []\nedges: []\ncopy: *x\n",
        ),
        ("json", dump_json({**sample_doc(), "format_version": 2})),
        (
            "json",
            dump_json(
                {
                    **sample_doc(),
                    "nodes": sample_doc()["nodes"]
                    + [
                        {
                            "id": "start",
                            "kind": "start",
                            "title": "Dup",
                            "params": {},
                            "position": {"x": 1, "y": 1},
                        }
                    ],
                }
            ),
        ),
        ("json", dump_json({**sample_doc(), "extra": True})),
        (
            "json",
            dump_json(
                {
                    **sample_doc(),
                    "edges": sample_doc()["edges"]
                    + [
                        {
                            "from": {"node": "go", "port": "pose"},
                            "to": {"node": "out", "port": "nope"},
                        }
                    ],
                }
            ),
        ),
        (
            "json",
            dump_json(
                {
                    **run_doc(),
                    "edges": [
                        {
                            "from": {"node": "note", "port": "ok"},
                            "to": {"node": "note", "port": "mode"},
                        }
                    ],
                }
            ),
        ),
        (
            "json",
            dump_json(
                {
                    **run_doc(),
                    "edges": [
                        {
                            "from": {"node": "go", "port": "pose"},
                            "to": {"node": "note", "port": "mode"},
                        },
                        {
                            "from": {"node": "note", "port": "ok"},
                            "to": {"node": "go", "port": "target"},
                        },
                    ],
                }
            ),
        ),
        (
            "yaml",
            "format: roboact-flow\nformat_version: 1\nflow: !custom {}\nnodes: []\nedges: []\n",
        ),
    ]
    for fmt, text in bad:
        code, body = _import(base, text, fmt)
        assert code == 422, (fmt, text[:80], body)
        assert body["error"] == "import_failed"
        assert body["detail"]["errors"]
        ids1, graph1, runs1 = _snapshot(base)
        assert ids1 == ids0
        assert graph1["id"] == graph0["id"]
        assert graph1["nodes"] == graph0["nodes"]
        assert runs1 == runs0


def test_20_older_format_migrates(server):
    base = server["base"]
    doc = sample_doc()
    doc["format_version"] = 0
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    exp = _export(base, imported["flow"]["id"], "json")
    assert json.loads(exp["text"])["format_version"] == 1


def test_20_unknown_action_locks_run(server):
    base = server["base"]
    doc = run_doc()
    doc["nodes"].append(
        {
            "action": "gone.action",
            "id": "ghost",
            "kind": "action",
            "params": {},
            "position": {"x": 80, "y": 80},
            "title": "Ghost",
        }
    )
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    assert imported["action_gone"] == ["ghost"]
    fid = imported["flow"]["id"]
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["action_gone"] == ["ghost"]
    assert not check["can_run"]
    http_json(base, f"/api/flows/{fid}/review-enable", "POST", {})
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert not check["can_run"]
    graph = imported["flow"]
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != "ghost"]
    http_json(base, f"/api/flows/{fid}", "PUT", {**graph, "bump": True})
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["action_gone"] == []
    assert check["can_run"]


def test_20_mismatch_review(server):
    base = server["base"]
    doc = run_doc()
    doc["flow"]["robot"] = {"id": "other-arm", "model": "Other"}
    doc["flow"]["adapter"] = {"name": "OtherAdapter", "target": "sim", "version": "9"}
    doc["flow"]["action_list_version"] = "old-list"
    doc["flow"]["units"] = {"angle": "deg", "length": "mm"}
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    kinds = {m["kind"] for m in imported["mismatches"]}
    assert kinds == {"adapter", "actions", "robot", "units"}
    fid = imported["flow"]["id"]
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert not check["can_run"]
    code, body = http_status(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1})
    assert code == 409
    assert body["error"] == "run_locked"
    http_json(base, f"/api/flows/{fid}/review-enable", "POST", {})
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["mismatch_unlocked"]
    assert check["can_run"]
    started = http_json(
        base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1, "max_steps": 20}
    )
    assert started["run_id"]


def test_20_import_creates_new_flow(server):
    base = server["base"]
    ids0, graph0, runs0 = _snapshot(base)
    code, imported = _import(base, dump_json(run_doc()), "json")
    assert code == 200, imported
    assert imported["flow"]["id"] not in ids0
    ids1, _graph1, runs1 = _snapshot(base)
    assert imported["flow"]["id"] in ids1
    assert graph0["id"] in ids1
    old = http_json(base, f"/api/flows/{graph0['id']}")["flow"]
    assert old["nodes"] == graph0["nodes"]
    assert runs1 == runs0


def test_20_motion_limits(server):
    base = server["base"]
    doc = run_doc()
    for node in doc["nodes"]:
        if node["id"] == "go":
            node["params"] = {"x": 0.1, "y": 0.1, "z": 1.5}
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    http_json(
        base,
        "/api/test/adapter",
        "POST",
        {"joint_limits": {"x": [-2, 2], "y": [-2, 2], "z": [0, 1]}},
    )
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["limit_errors"]
    assert any(e["param"] == "z" for e in check["limit_errors"])
    code, body = http_status(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1})
    assert code == 409
    assert body["error"] == "param_out_of_limit"
    assert "z" in body["message"]
    http_json(
        base,
        "/api/test/adapter",
        "POST",
        {"joint_limits": {"x": [-2, 2], "y": [-2, 2], "z": [0, 2]}},
    )
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["can_run"]
    started = http_json(
        base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1, "max_steps": 20}
    )
    assert started["run_id"]


def test_20_too_large(server):
    base = server["base"]
    ids0, graph0, runs0 = _snapshot(base)
    text = "x" * (MAX_FILE_BYTES + 1)
    code, body = _import(base, text, "json")
    assert code == 422
    assert body["error"] == "import_failed"
    ids1, graph1, runs1 = _snapshot(base)
    assert ids1 == ids0
    assert graph1["nodes"] == graph0["nodes"]
    assert runs1 == runs0
