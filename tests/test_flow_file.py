"""Acceptance #20 — flow file import / export."""

from __future__ import annotations

import json
import socket
import time
from typing import Any
from urllib.parse import urlparse

from roboact.adapter import FakeAdapter
from roboact.catalog import zh
from roboact.flow_file import (
    MAX_FILE_BYTES,
    dump_json,
    export_flow,
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
        "adapter": {"name": zh("adapter.fake"), "target": "sim", "version": "0.2"},
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
            "adapter": {"name": zh("adapter.fake"), "target": "sim", "version": "0.2"},
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


def _import(base: str, text: str, fmt: str = "json", timeout: float = 30.0) -> tuple[int, Any]:
    return http_status(
        base, "/api/flows/import", "POST", {"text": text, "format": fmt}, timeout=timeout
    )


def _import_declared_oversize(base: str, declared: int) -> tuple[int, Any]:
    """Reject on Content-Length without sending the declared body."""
    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    req = (
        "POST /api/flows/import HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {declared}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(req)
        chunks: list[bytes] = []
        while True:
            piece = sock.recv(65536)
            if not piece:
                break
            chunks.append(piece)
    raw = b"".join(chunks)
    head, _, rest = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
    code = int(status_line.split()[1])
    try:
        payload = json.loads(rest.decode() or "null")
    except json.JSONDecodeError:
        payload = {"error": rest.decode(errors="replace")}
    return code, payload


def _export(base: str, flow_id: str, fmt: str = "json") -> Any:
    return http_json(base, "/api/flows/export", "POST", {"flow_id": flow_id, "format": fmt})


def _deep_yaml(levels: int) -> str:
    body = "v: 1"
    for _ in range(levels):
        body = "a:\n" + "\n".join("  " + line for line in body.splitlines())
    return body + "\n"


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
  adapter: {name: "__ADAPTER__", version: "0.2", target: "sim"}
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
""".replace("__ADAPTER__", zh("adapter.fake"))
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
        ("yaml", "%YAML 1.1\n---\nformat: roboact-flow\nformat_version: 1\n"),
        ("yaml", "%TAG ! tag:example.com,2024:\n---\na: 1\n"),
        (
            "yaml",
            "format: roboact-flow\nformat: other\nformat_version: 1\n"
            "flow: {id: a, name: a, revision: 1, step_limit: 1}\nnodes: []\nedges: []\n",
        ),
        ("yaml", "---\nformat: roboact-flow\nformat_version: 1\n---\na: 1\n"),
        ("yaml", _deep_yaml(1000)),
    ]
    for fmt, text in bad:
        code, body = _import(base, text, fmt, timeout=60.0)
        assert 400 <= code < 500, (fmt, text[:80], body)
        assert body["error"] == "import_failed"
        errs = body.get("detail", {}).get("errors") or []
        assert errs, (fmt, body)
        assert any(str(e.get("path") or e.get("message") or "") for e in errs), (fmt, body)
        ids1, graph1, runs1 = _snapshot(base)
        assert ids1 == ids0
        assert graph1["id"] == graph0["id"]
        assert graph1["nodes"] == graph0["nodes"]
        assert runs1 == runs0
    code, body = _import_declared_oversize(base, 6 * 1024 * 1024)
    assert 400 <= code < 500, body
    assert body["error"] == "import_failed"
    errs = body.get("detail", {}).get("errors") or []
    assert errs and any(e.get("path") for e in errs), body
    ids1, graph1, runs1 = _snapshot(base)
    assert ids1 == ids0
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
    code, body = _import_declared_oversize(base, MAX_FILE_BYTES + 1)
    assert code == 422
    assert body["error"] == "import_failed"
    assert body["detail"]["errors"][0]["path"] == "request"
    ids1, graph1, runs1 = _snapshot(base)
    assert ids1 == ids0
    assert graph1["nodes"] == graph0["nodes"]
    assert runs1 == runs0


def test_20_export_ui_path_is_stable(server):
    base = server["base"]
    code, imported = _import(base, dump_json(run_doc()), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    rev0 = imported["flow"]["revision"]
    graph = http_json(base, f"/api/flows/{fid}")["flow"]
    http_json(base, f"/api/flows/{fid}", "PUT", {**graph, "bump": False})
    first = _export(base, fid, "json")
    graph2 = http_json(base, f"/api/flows/{fid}")["flow"]
    http_json(base, f"/api/flows/{fid}", "PUT", {**graph2, "bump": False})
    second = _export(base, fid, "json")
    assert first["text"] == second["text"]
    assert json.loads(first["text"])["flow"]["revision"] == rev0


def test_20_client_unlock_ignored(server):
    base = server["base"]
    doc = run_doc()
    doc["flow"]["robot"] = {"id": "other-arm", "model": "Other"}
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    graph = http_json(base, f"/api/flows/{fid}")["flow"]
    http_json(
        base,
        f"/api/flows/{fid}",
        "PUT",
        {
            **graph,
            "bump": False,
            "mismatch_unlocked": True,
            "unlock_record": {
                "file_id": graph.get("file_id"),
                "robot_id": "other-arm",
                "adapter_name": "x",
                "action_list_version": "fake-v1",
                "units_angle": "rad",
                "units_length": "m",
            },
        },
    )
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert not check["mismatch_unlocked"]
    assert not check["can_run"]


def test_20_unlock_relocks_when_robot_changes(server):
    base = server["base"]
    doc = run_doc()
    doc["flow"]["robot"] = {"id": "other-arm", "model": "Other"}
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    http_json(base, f"/api/flows/{fid}/review-enable", "POST", {})
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["mismatch_unlocked"]
    assert check["can_run"]
    http_json(base, "/api/test/adapter", "POST", {"robot_id": "changed-robot"})
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert not check["mismatch_unlocked"]
    assert not check["can_run"]
    kinds = {m["kind"] for m in check["mismatches"]}
    assert "robot" in kinds


def test_20_velocity_limit_inside_target(server):
    base = server["base"]
    doc = run_doc()
    for node in doc["nodes"]:
        if node["id"] == "go":
            node["params"] = {"target": {"x": 0.1, "y": 0.1, "z": 0.2, "speed": 9}}
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    http_json(
        base,
        "/api/test/adapter",
        "POST",
        {"velocity_limits": {"speed": [0, 2]}},
    )
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["limit_errors"]
    assert any(e["param"] == "speed" for e in check["limit_errors"])
    code, body = http_status(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1})
    assert code == 409
    assert body["error"] == "param_out_of_limit"
    assert "speed" in body["message"]


def test_20_non_import_action_list_is_hard_error(server):
    base = server["base"]
    boot = http_json(base, "/api/bootstrap")
    fid = boot["flow"]["id"]
    assert not boot["flow"].get("file_id")
    http_json(base, "/api/test/adapter", "POST", {"action_list_version": "changed-list"})
    code, body = http_status(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1})
    assert code == 409
    assert body["error"] == "action_list_mismatch"


def test_20_yaml_size_and_parse_report():
    adapter = FakeAdapter()
    bind_adapter(adapter)
    nodes = [
        {
            "id": "start",
            "type": "start",
            "x": 0,
            "y": 0,
            "params": {"data": {}},
            "title": "Start",
        }
    ]
    edges = []
    prev = "start"
    for i in range(498):
        nid = f"n{i}"
        nodes.append(
            {
                "id": nid,
                "type": "echo",
                "x": i,
                "y": 0,
                "params": {},
                "title": nid,
            }
        )
        edges.append(
            {
                "id": f"e{i}",
                "source": prev,
                "source_port": "data" if i == 0 else "value",
                "target": nid,
                "target_port": "value",
            }
        )
        prev = nid
    nodes.append({"id": "out", "type": "output", "x": 0, "y": 1, "params": {}, "title": "Out"})
    edges.append(
        {
            "id": "eout",
            "source": prev,
            "source_port": "value",
            "target": "out",
            "target_port": "value",
        }
    )
    graph = {
        "id": "flow-500",
        "file_id": "flow-500",
        "name": "five-hundred",
        "revision": 1,
        "step_limit": 1000,
        "nodes": nodes,
        "edges": edges,
        "groups": [],
    }
    text, _name = export_flow(graph, "yaml", adapter)
    size_500 = len(text.encode("utf-8"))
    line = "# " + ("x" * 77) + "\n"
    n = (MAX_FILE_BYTES - 16) // len(line.encode("utf-8"))
    blob = line * n + "a: 1\n"
    assert len(blob.encode("utf-8")) >= MAX_FILE_BYTES - 80
    t0 = time.perf_counter()
    parse_yaml(blob)
    parse_s = time.perf_counter() - t0
    print(f"C9_YAML_500_NODE_BYTES={size_500}")
    print(f"C9_YAML_5MIB_PARSE_S={parse_s:.4f}")
    assert size_500 > 0
    assert parse_s >= 0
