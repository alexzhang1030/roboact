"""Acceptance #20 — flow file import / export."""

from __future__ import annotations

import itertools
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from roboact.catalog import en
from roboact.flow_file import (
    MAX_FILE_BYTES,
    dump_json,
    parse_and_validate,
    parse_yaml,
)

from .conftest import launch_chromium, record_c11_heartbeat_gap
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
        "adapter": {"name": en("adapter.fake"), "target": "sim", "version": "0.2"},
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
                "kind": "end",
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
            "adapter": {"name": en("adapter.fake"), "target": "sim", "version": "0.2"},
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
                "position": {"x": 320, "y": 160},
                "title": "Go",
            },
            {
                "id": "out",
                "kind": "end",
                "params": {},
                "position": {"x": 600, "y": 160},
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
    """Compact 1000-level nest that stays near 2 KB."""
    return "a: " + "{a: " * (levels - 1) + "1" + ("}" * (levels - 1)) + "\n"


def _snapshot(base: str) -> tuple[list[str], dict[str, Any], int]:
    boot = http_json(base, "/api/bootstrap")
    flows = http_json(base, "/api/flows")["flows"]
    runs = http_json(base, "/api/runs", "GET")
    n_runs = len(runs.get("runs") or [])
    return [f["flow_id"] for f in flows], boot["flow"], n_runs


def _current_adapter(base: str) -> dict[str, Any]:
    return http_json(base, "/api/bootstrap")["adapter"]


def _forged_unlock(adapter: dict[str, Any], file_id: str) -> dict[str, Any]:
    robot = adapter.get("robot") or {}
    units = adapter.get("units") or {}
    return {
        "action_list_version": adapter.get("action_list_version"),
        "adapter_name": adapter.get("name"),
        "file_id": file_id,
        "robot_id": robot.get("id"),
        "units_angle": units.get("angle"),
        "units_length": units.get("length"),
    }


def _open_ui(base: str, flow_id: str | None = None):
    pw = pytest.importorskip("playwright.sync_api")
    try:
        playwright = pw.sync_playwright().start()
    except Exception as e:
        pytest.skip("playwright/chromium not available: " + str(e))
    try:
        browser = launch_chromium(playwright)
    except BaseException:
        playwright.stop()
        raise
    ctx = browser.new_context(
        locale="en-US", accept_downloads=True, viewport={"width": 1280, "height": 800}
    )
    ctx.add_init_script("localStorage.setItem('roboact.lang', 'en');")
    page = ctx.new_page()
    url = base + "/"
    if flow_id:
        url += "?flow_id=" + flow_id
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=120000)
    return playwright, browser, ctx, page


def _ui_import(page, path: Path) -> None:
    page.locator('[data-testid="flow-export"]').click()
    page.wait_for_selector('[data-testid="flow-import"]', state="attached", timeout=10000)
    page.set_input_files('[data-testid="flow-import"]', str(path))
    page.wait_for_timeout(800)


def _ui_export_bytes(page, fmt: str = "json") -> bytes:
    testid = "export-json" if fmt == "json" else "export-yaml"
    page.locator('[data-testid="flow-export"]').click()
    page.wait_for_selector(f'[data-testid="{testid}"]', timeout=10000)
    with page.expect_download() as dl:
        page.locator(f'[data-testid="{testid}"]').click()
    return Path(dl.value.path()).read_bytes()


def test_20_1(server):
    """Build a flow with a loop, a control node, a motion node and a secret param."""
    base = server["base"]
    code, imported = _import(base, dump_json(sample_doc()), "json")
    assert code == 200, imported
    kinds = {n.get("type") for n in imported["flow"]["nodes"]}
    assert "condition" in kinds
    assert "fake_set_pose" in kinds
    assert "fake_note" in kinds
    note = next(n for n in imported["flow"]["nodes"] if n["id"] == "note")
    assert "token" in (note.get("params") or {})
    edges = imported["flow"]["edges"]
    assert any(e["source"] == "go" and e["target"] == "cond" for e in edges)


def test_20_2(server, tmp_path):
    """UI Export twice is byte-identical; import then export keeps the secret as null."""
    base = server["base"]
    src = tmp_path / "sample.json"
    src.write_text(dump_json(sample_doc()), encoding="utf-8")
    pw, browser, ctx, page = _open_ui(base)
    try:
        _ui_import(page, src)
        page.wait_for_function(
            "() => !!(window.__af && window.__af.meta && window.__af.meta.value.id)",
            timeout=15000,
        )
        fid = page.evaluate("() => window.__af.meta.value.id")
        rev0 = http_json(base, f"/api/flows/{fid}")["flow"]["revision"]
        first = _ui_export_bytes(page, "json")
        second = _ui_export_bytes(page, "json")
        assert first == second
        tree = json.loads(first.decode())
        assert tree["flow"]["revision"] == rev0
        note = next(n for n in tree["nodes"] if n["id"] == "note")
        assert note["params"]["token"] is None
        redacted = tmp_path / "redacted.json"
        redacted.write_bytes(first)
        _ui_import(page, redacted)
        page.wait_for_function(
            """() => {
              const c = window.__af && window.__af.runCheck && window.__af.runCheck.value
              return !!(c && (c.missing_input || []).includes('note') && c.can_run === false)
            }""",
            timeout=15000,
        )
        again = _ui_export_bytes(page, "json")
        assert again == first
        assert page.evaluate("() => window.__af.slot1Disabled.value") is True
    finally:
        ctx.close()
        browser.close()
        pw.stop()


def test_20_3(server):
    """YAML export converted to JSON equals the JSON export."""
    base = server["base"]
    code, imported = _import(base, dump_json(sample_doc()), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    exp_json = _export(base, fid, "json")
    exp_yaml = _export(base, fid, "yaml")
    from_yaml = parse_and_validate(exp_yaml["text"], "yaml")
    assert dump_json(from_yaml) == exp_json["text"]


def test_20_4(server):
    """Exported files omit run records, pins, images, breakpoints, prefs, server."""
    base = server["base"]
    code, imported = _import(base, dump_json(sample_doc()), "json")
    assert code == 200, imported
    blob = _export(base, imported["flow"]["id"], "json")["text"]
    for key in FORBIDDEN_KEYS:
        assert f'"{key}"' not in blob


def test_20_5(server):
    """Hand-written YAML: mode: on stays a string; 1.1 blocks Run; quoted 1.10 runs."""
    text = "mode: on\nver: 1.10\n"
    assert parse_yaml(text)["mode"] == "on"
    assert parse_yaml(text)["ver"] == 1.1
    eleven = parse_yaml(text, version=(1, 1))
    assert eleven["mode"] is True
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
    kind: end
    title: Out
    params: {}
    position: {x: 400, y: 0}
edges:
  - from: {node: start, port: data}
    to: {node: go, port: target}
  - from: {node: go, port: pose}
    to: {node: out, port: value}
""".replace("__ADAPTER__", en("adapter.fake"))
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


def test_20_6(server):
    """Bad files: listed errors, never 500, canvas unchanged."""
    base = server["base"]
    ids0, graph0, runs0 = _snapshot(base)
    nest = _deep_yaml(1000)
    assert len(nest.encode("utf-8")) < 8000
    bad: list[tuple[str, str]] = [
        ("json", dump_json({**sample_doc(), "format_version": 2})),
        ("json", dump_json({**sample_doc(), "extra": True})),
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
        (
            "yaml",
            "format: roboact-flow\nformat_version: 1\n"
            "flow: &x {id: a, name: a, revision: 1, step_limit: 1}\n"
            "nodes: []\nedges: []\ncopy: *x\n",
        ),
        (
            "yaml",
            "format: roboact-flow\nformat: other\nformat_version: 1\n"
            "flow: {id: a, name: a, revision: 1, step_limit: 1}\nnodes: []\nedges: []\n",
        ),
        ("yaml", "---\nformat: roboact-flow\nformat_version: 1\n---\na: 1\n"),
        ("yaml", "%YAML 1.1\n---\nformat: roboact-flow\nformat_version: 1\n"),
        ("yaml", nest),
    ]
    for fmt, text in bad:
        code, body = _import(base, text, fmt, timeout=60.0)
        assert code != 500, (fmt, text[:80], body)
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


def _file_node(nid: str, kind: str, **params: Any) -> dict[str, Any]:
    return {
        "id": nid,
        "kind": kind,
        "params": params,
        "position": {"x": 0, "y": 0},
        "title": nid,
    }


def _count_doc(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "edges": [],
        "flow": {"id": "flow-count", "name": "count", "revision": 1, "step_limit": 20},
        "format": "roboact-flow",
        "format_version": 1,
        "nodes": nodes,
    }


def _joined_errors(body: dict[str, Any]) -> list[str]:
    errs = (body.get("detail") or {}).get("errors") or []
    return [f"{e.get('path')}: {e.get('message')}" for e in errs]


def test_import_rejects_kind_output_and_accepts_end_counts(server):
    """kind output is refused. Zero or two End nodes import; counts are a run check."""
    base = server["base"]
    ids0, graph0, runs0 = _snapshot(base)
    renamed = 'nodes[2] (id n_out): kind "output" is now "end"'
    code, body = _import(
        base,
        dump_json(
            _count_doc(
                [
                    _file_node("n_start", "start"),
                    _file_node("n_const", "constant", value="a", value_type="string"),
                    _file_node("n_out", "output"),
                ]
            )
        ),
        "json",
    )
    assert 400 <= code < 500, body
    assert body["error"] == "import_failed"
    assert renamed in _joined_errors(body)
    ids1, graph1, runs1 = _snapshot(base)
    assert ids1 == ids0
    assert graph1["id"] == graph0["id"]
    assert graph1["nodes"] == graph0["nodes"]
    assert runs1 == runs0

    accepted = [
        _count_doc(
            [
                _file_node("n_start", "start"),
                _file_node("n_const", "constant", value="a", value_type="string"),
            ]
        ),
        _count_doc(
            [
                _file_node("n_start", "start"),
                _file_node("n_end_a", "end"),
                _file_node("n_end_b", "end"),
            ]
        ),
    ]
    for doc in accepted:
        code, imported = _import(base, dump_json(doc), "json")
        assert code == 200, imported
        kinds = [n["type"] for n in imported["flow"]["nodes"]]
        assert kinds.count("end") == sum(1 for n in doc["nodes"] if n["kind"] == "end")


def test_20_7(server):
    """Older format_version is migrated."""
    base = server["base"]
    doc = sample_doc()
    doc["format_version"] = 0
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    exp = _export(base, imported["flow"]["id"], "json")
    assert json.loads(exp["text"])["format_version"] == 1


def test_20_8(server):
    """Unknown action: action gone; Review does not unlock; deleting the node does."""
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


def test_20_9(server):
    """Mismatch unlock is server-only; robot change and client fields re-lock."""
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
    http_json(base, f"/api/flows/{fid}/review-enable", "POST", {})
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert check["mismatch_unlocked"]
    assert check["can_run"]
    http_json(base, "/api/test/adapter", "POST", {"robot_id": "changed-robot"})
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert not check["mismatch_unlocked"]
    assert not check["can_run"]
    graph = http_json(base, f"/api/flows/{fid}")["flow"]
    http_json(
        base,
        f"/api/flows/{fid}",
        "PUT",
        {
            **graph,
            "bump": False,
            "mismatch_unlocked": True,
            "unlock_record": _forged_unlock(_current_adapter(base), graph.get("file_id") or ""),
        },
    )
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert not check["mismatch_unlocked"]
    adapter = _current_adapter(base)
    forged = http_json(
        base,
        "/api/flows",
        "POST",
        {
            "file_id": "file-forged",
            "file_robot": {"id": "other-arm", "model": "Other"},
            "mismatch_unlocked": True,
            "name": "forged",
            "unlock_record": _forged_unlock(adapter, "file-forged"),
        },
    )
    check = http_json(base, f"/api/flows/{forged['flow']['id']}/run-check")
    assert not check["mismatch_unlocked"]
    assert not check["can_run"]


def test_20_10(server):
    """Import while a flow is open creates a new flow; old flow and runs unchanged."""
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


def test_20_11(server):
    """Motion param over the connected robot's limit blocks Run; wider limits run."""
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


def test_20_12(server):
    """Over 5 MiB is refused on Content-Length before the body is read."""
    base = server["base"]
    ids0, graph0, runs0 = _snapshot(base)
    code, body = _import_declared_oversize(base, MAX_FILE_BYTES + 1)
    assert code != 500
    assert 400 <= code < 500
    assert body["error"] == "import_failed"
    assert body["detail"]["errors"][0]["path"] == "request"
    ids1, graph1, runs1 = _snapshot(base)
    assert ids1 == ids0
    assert graph1["nodes"] == graph0["nodes"]
    assert runs1 == runs0


def test_20_13(server, capfd):
    """Import 800 KB YAML while a flow is running: no pause, heartbeat does not miss."""
    base = server["base"]
    code, imported = _import(base, dump_json(run_doc()), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    http_json(base, "/api/test/adapter", "POST", {"async_steps": 40, "async_sleep_s": 0.12})
    started = http_json(
        base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1, "max_steps": 80}
    )
    rid = started["run_id"]
    t0 = time.time()
    while time.time() - t0 < 4:
        run = http_json(base, f"/api/runs/{rid}")
        if (run.get("run") or run).get("status") == "running":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("run did not start")
    beats0 = http_json(base, "/api/test/adapter")
    exp = _export(base, fid, "yaml")
    pad = "# " + ("y" * 77) + "\n"
    pad_b = pad.encode("utf-8")
    need = 800 * 1024 - len(exp["text"].encode("utf-8"))
    n = max(1, (need + len(pad_b) - 1) // len(pad_b))
    blob = pad * n + exp["text"]
    assert len(blob.encode("utf-8")) >= 800 * 1024
    icode, ibody = _import(base, blob, "yaml", timeout=60.0)
    assert icode == 200, ibody
    run = http_json(base, f"/api/runs/{rid}")
    status = (run.get("run") or run).get("status")
    assert status == "running", status
    time.sleep(0.9)
    beats = http_json(base, "/api/test/adapter")
    times = [t for t in beats["beats"] if t >= (beats0["beats"][-1] if beats0["beats"] else 0)]
    if len(times) < 2:
        times = beats["beats"]
    gaps = [b - a for a, b in itertools.pairwise(times)]
    max_gap = max(gaps) if gaps else 0.0
    steps = http_json(base, f"/api/runs/{rid}")
    rec = steps.get("run") or steps
    step_times = [s.get("started_at") or s.get("ended_at") for s in rec.get("steps") or []]
    del step_times
    record_c11_heartbeat_gap(max_gap)
    with capfd.disabled():
        sys.stderr.write(f"C11_IMPORT_MAX_HEARTBEAT_GAP_S={max_gap:.4f}\n")
        sys.stderr.flush()
    print(f"C11_IMPORT_MAX_HEARTBEAT_GAP_S={max_gap:.4f}")
    assert beats["goals_cancelled_by_lost_heartbeat"] == 0
    assert max_gap < 3.0, max_gap


def test_20_14(server):
    """Enum outside its list and a nested motion limit each block Run by name."""
    base = server["base"]
    doc = run_doc()
    for node in doc["nodes"]:
        if node["id"] == "note":
            node["params"]["mode"] = "nope"
        if node["id"] == "go":
            node["params"] = {"target": {"x": 0.1, "y": 0.1, "z": 0.2, "speed": 9}}
    code, imported = _import(base, dump_json(doc), "json")
    assert code == 200, imported
    fid = imported["flow"]["id"]
    http_json(base, "/api/test/adapter", "POST", {"velocity_limits": {"speed": [0, 2]}})
    check = http_json(base, f"/api/flows/{fid}/run-check")
    assert any(e["param"] == "mode" and e.get("type") == "enum" for e in check["type_errors"])
    assert any(e["param"] == "speed" for e in check["limit_errors"])
    code, _body = http_status(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": 1})
    assert code == 409


def test_20_15(server, tmp_path):
    """Filling a missing secret clears the mark without a reload."""
    base = server["base"]
    doc = run_doc(token=None)
    src = tmp_path / "missing.json"
    src.write_text(dump_json(doc), encoding="utf-8")
    pw, browser, ctx, page = _open_ui(base)
    try:
        _ui_import(page, src)
        page.wait_for_function(
            """() => {
              const nodes = document.querySelectorAll('.fn')
              return [...nodes].some(n => n.getAttribute('data-status') === 'missing_input')
            }""",
            timeout=15000,
        )
        page.evaluate(
            "() => { window.__af.selected.value = 'note';"
            " window.__af.updateParam('token', 'filled') }"
        )
        page.wait_for_function(
            """() => {
              const c = window.__af.runCheck.value
              const gone = !(c.missing_input || []).includes('note')
              const nodes = document.querySelectorAll('.fn')
              const amber = [...nodes].some(n => n.getAttribute('data-status') === 'missing_input')
              return gone && !amber
            }""",
            timeout=15000,
        )
        assert page.url.split("#")[0]
    finally:
        ctx.close()
        browser.close()
        pw.stop()


def test_20_16(tmp_path):
    """README offline install: pip --no-index from vendor/wheels imports ruamel.yaml."""
    root = Path(__file__).resolve().parents[1]
    wheels = root / "vendor" / "wheels"
    venv = tmp_path / "off"
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        if venv.exists():
            shutil.rmtree(venv)
        subprocess.check_call([sys.executable, "-m", "venv", "--without-pip", str(venv)])
    py = venv / "bin" / "python"
    pip = venv / "bin" / "pip"
    if pip.exists():
        subprocess.check_call(
            [
                str(pip),
                "install",
                "--no-index",
                "--no-deps",
                "--find-links",
                str(wheels),
                "ruamel.yaml==0.18.16",
            ]
        )
    else:
        uv = shutil.which("uv")
        assert uv, "need pip in the venv or uv to install from vendor/wheels"
        subprocess.check_call(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(py),
                "--no-index",
                "--no-deps",
                "--find-links",
                str(wheels),
                "ruamel.yaml==0.18.16",
            ]
        )
    out = subprocess.check_output(
        [str(py), "-c", "import ruamel.yaml; print(ruamel.yaml.__version__)"],
        text=True,
    )
    assert out.strip() == "0.18.16"


HAN = re.compile(r"[\u3400-\u9FFF]")


def _gone_missing_doc() -> dict[str, Any]:
    doc = run_doc(token=None)
    doc["nodes"].append(
        {
            "action": "gone.action",
            "id": "ghost",
            "kind": "action",
            "params": {},
            "position": {"x": 40, "y": 20},
            "title": "Gone",
        }
    )
    return doc


def _han_hits(obj: Any, path: str = "") -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in ("zh-CN", "zh"):
                continue
            hits.extend(_han_hits(val, f"{path}.{key}"))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            hits.extend(_han_hits(val, f"{path}[{i}]"))
    elif isinstance(obj, str) and HAN.search(obj):
        hits.append((path, obj))
    return hits


def test_c12_fake_note_not_in_palette(server):
    boot = http_json(server["base"], "/api/bootstrap")
    note = next(t for t in boot["node_types"] if t.get("type") == "fake_note")
    assert note["palette"] is False
    assert not any(
        t.get("type") == "fake_note" and t.get("palette") is not False for t in boot["node_types"]
    )


def test_c12_import_restores_file_positions(server):
    """Item 10: fromFlow restores file positions; run_doc used to overlap (200px gap)."""
    doc = run_doc()
    want = {n["id"]: (n["position"]["x"], n["position"]["y"]) for n in doc["nodes"]}
    assert want["go"][0] - want["start"][0] >= 240
    code, imported = _import(server["base"], dump_json(doc), "json")
    assert code == 200, imported
    got = {n["id"]: (n["x"], n["y"]) for n in imported["flow"]["nodes"]}
    assert got == want


def test_c12_en_bootstrap_and_dom_have_no_han(server):
    """Locale en: bootstrap display strings and the rendered DOM contain no Han."""
    base = server["base"]
    boot = http_json(base, "/api/bootstrap")
    assert boot["adapter"]["name"] == en("adapter.fake")
    assert boot["flow"]["name"] == en("flow.demo")
    hits = _han_hits(boot)
    assert hits == [], hits
    pw, browser, ctx, page = _open_ui(base)
    try:
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_function("window.__ready === true", timeout=120000)
        text = page.inner_text("body")
        assert not HAN.search(text), text
        assert not HAN.search(page.content())
    finally:
        ctx.close()
        browser.close()
        pw.stop()


def test_c12_counts_banner_lock_and_no_overlap(server, tmp_path):
    """Counts, lock text, mismatch lines, gone summary, no Start/Go overlap."""
    base = server["base"]
    src = tmp_path / "gone.json"
    src.write_text(dump_json(_gone_missing_doc()), encoding="utf-8")
    pw, browser, ctx, page = _open_ui(base)
    try:
        page.set_viewport_size({"width": 1280, "height": 800})
        _ui_import(page, src)
        page.wait_for_function(
            """() => {
              const c = window.__af && window.__af.runCheck && window.__af.runCheck.value
              return !!(c && (c.action_gone || []).includes('ghost')
                && (c.missing_input || []).includes('note'))
            }""",
            timeout=15000,
        )
        gone_count = page.locator('[data-testid="count-action-gone"]').inner_text()
        need_count = page.locator('[data-testid="count-missing-input"]').inner_text()
        assert gone_count == "1 missing action"
        assert need_count == "1 node needs input"
        gone = page.locator('.fn[data-status="missing"]')
        assert "Gone" in gone.inner_text()
        assert "Action no longer exists" in gone.inner_text()
        assert "This action is no longer provided by the adapter." in gone.inner_text()
        assert page.locator(".fn-gone-x").count() == 0
        assert "state-unknown" in (gone.locator(".fn-st .ico").get_attribute("style") or "")
        assert page.locator(".vue-flow__minimap").count() == 0
        assert page.locator(".fn[data-node-type='fake_note'] .port-missing").count() >= 1
        page.locator('[data-testid="count-action-gone"]').click()
        page.wait_for_function("() => window.__af.selected.value === 'ghost'", timeout=5000)
        boxes = page.evaluate(
            """() => [...document.querySelectorAll('.vue-flow__node')].map(n => {
              const r = n.getBoundingClientRect()
              return {id: n.getAttribute('data-id'), x: r.x, y: r.y, r: r.right, b: r.bottom}
            })"""
        )
        by_id = {b["id"]: b for b in boxes}
        start, go = by_id["start"], by_id["go"]
        sep = start["r"] <= go["x"] or go["r"] <= start["x"]
        sep = sep or start["b"] <= go["y"] or go["b"] <= start["y"]
        assert sep, (start, go)
        mis = tmp_path / "mis.json"
        doc = run_doc()
        doc["flow"]["robot"] = {"id": "other-arm", "model": "Other"}
        doc["flow"]["adapter"] = {"name": "OtherAdapter", "target": "sim", "version": "9"}
        doc["flow"]["action_list_version"] = "old-list"
        doc["flow"]["units"] = {"angle": "deg", "length": "mm"}
        mis.write_text(dump_json(doc), encoding="utf-8")
        _ui_import(page, mis)
        page.wait_for_selector('[data-testid="flow-mismatch"]', timeout=15000)
        lines = page.locator(".mismatch-line").count()
        assert lines == 4
        locked = page.locator('[data-testid="run-locked"]')
        assert locked.count() == 1
        assert locked.inner_text() == "Review the differences above before running."
        assert page.locator(".slot-1").get_attribute("title") == (
            "Review the differences above before running."
        )
        assert page.locator(".slot-1").get_attribute("disabled") is not None
        vis = page.evaluate(
            """() => {
              const top = document.querySelector('.top')
              const lock = document.querySelector('[data-testid="run-locked"]')
              const right = document.querySelector('.top > .right')
              const tr = top.getBoundingClientRect()
              const lr = lock.getBoundingClientRect()
              const rr = right.getBoundingClientRect()
              return {
                innerWidth: window.innerWidth,
                lockText: lock.textContent,
                lockW: lock.scrollWidth,
                lockVisibleW: lock.clientWidth,
                lockRight: lr.right,
                topRight: tr.right,
                rightLeft: rr.left,
                collapse: top.dataset.collapse || '',
              }
            }"""
        )
        assert vis["innerWidth"] == 1280, vis
        assert vis["lockText"] == "Review the differences above before running."
        assert vis["lockW"] <= vis["lockVisibleW"] + 1, vis
        assert vis["lockRight"] <= vis["topRight"] + 1, vis
        assert vis["lockRight"] <= vis["rightLeft"] + 1, vis
        top_h = page.locator(".top").evaluate("el => el.getBoundingClientRect().height")
        assert top_h <= 45, top_h
        steps_box = page.locator(".top .steps")
        overflow = steps_box.evaluate("el => el.scrollWidth - el.clientWidth")
        assert overflow <= 1, overflow
        assert page.locator(".top .lang-btn").count() == 1
        assert "Export" not in page.locator(".top .right").inner_text()
        page.locator('[data-testid="flow-export"]').click()
        menu_text = page.locator(".flow-menu").inner_text()
        assert "Export JSON…" in menu_text
        assert "Export YAML…" in menu_text
        assert "Import…" in menu_text
    finally:
        ctx.close()
        browser.close()
        pw.stop()


def _menu_item_box(page, text: str) -> dict:
    loc = page.locator('.flow-menu [role="menu"]').get_by_text(text, exact=True)
    assert loc.is_visible(), text
    box = loc.bounding_box()
    assert box, text
    return box


def test_c12_flow_name_menu_visible_and_import(server):
    """Shipped CSS: Export JSON… / YAML… download; Import… opens the file chooser."""
    base = server["base"]
    pw, browser, ctx, page = _open_ui(base)
    try:
        page.set_viewport_size({"width": 1280, "height": 800})
        inner = page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
        assert inner == {"w": 1280, "h": 800}, inner
        page.locator('[data-testid="flow-export"]').click()
        page.wait_for_selector('.flow-menu [role="menu"]', timeout=5000)
        kids = page.evaluate(
            """() => [...document.querySelectorAll('.flow-menu [role="menu"] > *')].map(el => ({
              role: el.getAttribute('role'),
              text: (el.innerText || '').trim(),
            }))"""
        )
        assert [k["text"] or k["role"] for k in kids] == [
            "Export JSON…",
            "Export YAML…",
            "separator",
            "Import…",
        ], kids
        assert page.locator('.flow-menu [role="separator"]').count() == 1
        for label in ("Export JSON…", "Export YAML…", "Import…"):
            box = _menu_item_box(page, label)
            assert box["x"] >= 0, (label, box)
            assert box["y"] >= 0, (label, box)
            assert box["x"] + box["width"] <= 1280 + 1, (label, box)
            assert box["y"] + box["height"] <= 800 + 1, (label, box)
        menu = page.locator('.flow-menu [role="menu"]').bounding_box()
        top = page.locator(".top").bounding_box()
        assert menu and top
        assert menu["y"] + menu["height"] > top["height"], (menu, top)

        with page.expect_download() as dl_json:
            page.locator('[data-testid="export-json"]').click()
        json_name = dl_json.value.suggested_filename or ""
        json_bytes = Path(dl_json.value.path()).read_bytes()
        assert json_name.endswith(".json"), json_name
        json.loads(json_bytes.decode())

        page.locator('[data-testid="flow-export"]').click()
        page.wait_for_selector('[data-testid="export-yaml"]', timeout=5000)
        with page.expect_download() as dl_yaml:
            page.locator('[data-testid="export-yaml"]').click()
        yaml_name = dl_yaml.value.suggested_filename or ""
        yaml_bytes = Path(dl_yaml.value.path()).read_bytes()
        assert yaml_name.endswith((".yaml", ".yml")), yaml_name
        parsed = parse_yaml(yaml_bytes.decode())
        assert parsed.get("format") == "roboact-flow", parsed

        page.locator('[data-testid="flow-export"]').click()
        page.wait_for_selector('.flow-menu [role="menu"]', timeout=5000)
        with page.expect_file_chooser() as chooser:
            page.locator('.flow-menu [role="menu"] .file-open').click()
        assert chooser.value is not None
    finally:
        ctx.close()
        browser.close()
        pw.stop()


GONE_LONG_DESC = (
    "This action is no longer provided by the adapter. "
    "Replace the action or delete the node; Run stays locked while any missing "
    "action remains. Extra words force the two-line clamp to overflow so the "
    "ellipsis and the native title attribute can be checked."
)


def test_c13_gone_node_description_clamp(server, tmp_path):
    """Gone-node description is at most two lines; title holds the full text."""
    base = server["base"]
    src = tmp_path / "gone.json"
    src.write_text(dump_json(_gone_missing_doc()), encoding="utf-8")
    pw, browser, ctx, page = _open_ui(base)
    try:
        page.set_viewport_size({"width": 1280, "height": 800})
        _ui_import(page, src)
        page.wait_for_function(
            """() => {
              const c = window.__af && window.__af.runCheck && window.__af.runCheck.value
              return !!(c && (c.action_gone || []).includes('ghost'))
            }""",
            timeout=15000,
        )
        page.evaluate(
            """async (text) => {
              const m = await import('/js/i18n.js')
              m.CATALOGS.en['error.action_missing'] = text
              const cur = m.locale.value
              m.locale.value = cur === 'en' ? 'zh-CN' : 'en'
              await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
              m.locale.value = 'en'
            }""",
            GONE_LONG_DESC,
        )
        page.wait_for_function(
            """(text) => {
              const el = document.querySelector(
                '.fn[data-status="missing"] [data-testid="node-summary"]'
              )
              return !!(el && el.getAttribute('title') === text)
            }""",
            arg=GONE_LONG_DESC,
            timeout=5000,
        )
        info = page.evaluate(
            """() => {
              const el = document.querySelector(
                '.fn[data-status="missing"] [data-testid="node-summary"]'
              )
              const cs = getComputedStyle(el)
              const lh = parseFloat(cs.lineHeight)
              const pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)
              return {
                title: el.getAttribute('title'),
                clientHeight: el.clientHeight,
                scrollHeight: el.scrollHeight,
                lineHeight: lh,
                pad,
                lines: lh ? (el.clientHeight - pad) / lh : null,
                clamp: cs.webkitLineClamp || cs.lineClamp,
              }
            }"""
        )
        assert info["title"] == GONE_LONG_DESC, info
        assert info["clamp"] == "2", info
        assert info["lines"] is not None and info["lines"] <= 2.05, info
        assert info["clientHeight"] <= info["lineHeight"] * 2 + info["pad"] + 1, info
    finally:
        ctx.close()
        browser.close()
        pw.stop()


GONE_ONE_LINE = "Gone."

DEMO_NODE_CLIENT_H = {
    "start": 60,
    "fake_set_pose": 132,
    "fake_gripper": 84,
    "fake_camera": 60,
    "end": 60,
}


def _set_en_catalog(page, key: str, text: str) -> None:
    page.evaluate(
        """async ({key, text}) => {
          const m = await import('/js/i18n.js')
          m.CATALOGS.en[key] = text
          const cur = m.locale.value
          m.locale.value = cur === 'en' ? 'zh-CN' : 'en'
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
          m.locale.value = 'en'
        }""",
        {"key": key, "text": text},
    )
    page.wait_for_function(
        """({key, text}) => {
          const el = document.querySelector(
            '.fn[data-status="missing"] [data-testid="node-summary"]'
          )
          return !!(el && el.getAttribute('title') === text)
        }""",
        arg={"key": key, "text": text},
        timeout=5000,
    )


def _gone_card_metrics(page) -> dict:
    return page.evaluate(
        """() => {
          const card = document.querySelector('.fn[data-status="missing"]')
          const sum = card.querySelector('[data-testid="node-summary"]')
          const body = getComputedStyle(sum)
          return {
            cardH: card.clientHeight,
            gap: card.clientHeight - (sum.offsetTop + sum.offsetHeight),
            padL: parseFloat(body.paddingLeft),
            padB: parseFloat(getComputedStyle(card).paddingBottom),
            title: sum.getAttribute('title'),
            portRows: card.querySelectorAll('.fn-port').length,
            clamp: body.webkitLineClamp || body.lineClamp,
          }
        }"""
    )


def test_c14_gone_node_card_follows_content(server, tmp_path):
    """Gone card hugs the description; a 1-line Gone node is shorter than a 2-line one."""
    base = server["base"]
    pw, browser, ctx, page = _open_ui(base)
    try:
        page.set_viewport_size({"width": 1280, "height": 800})
        demo = page.evaluate(
            """() => {
              const out = {}
              for (const el of document.querySelectorAll('.fn')) {
                out[el.getAttribute('data-node-type')] = el.clientHeight
              }
              return out
            }"""
        )
        print(f"C14 demo heights: {demo}", flush=True)
        for typ, want in DEMO_NODE_CLIENT_H.items():
            assert typ in demo, demo
            assert abs(demo[typ] - want) <= 1, (typ, demo[typ], want)

        src = tmp_path / "gone.json"
        src.write_text(dump_json(_gone_missing_doc()), encoding="utf-8")
        _ui_import(page, src)
        page.wait_for_function(
            """() => {
              const c = window.__af && window.__af.runCheck && window.__af.runCheck.value
              return !!(c && (c.action_gone || []).includes('ghost'))
            }""",
            timeout=15000,
        )
        _set_en_catalog(page, "error.action_missing", GONE_ONE_LINE)
        one = _gone_card_metrics(page)
        print(f"C14 gone one-line: {one}", flush=True)
        assert one["title"] == GONE_ONE_LINE, one
        assert one["portRows"] == 0, one
        assert abs(one["gap"] - one["padL"]) <= 1, one

        _set_en_catalog(page, "error.action_missing", GONE_LONG_DESC)
        two = _gone_card_metrics(page)
        print(f"C14 gone two-line: {two}", flush=True)
        assert two["title"] == GONE_LONG_DESC, two
        assert two["portRows"] == 0, two
        assert two["clamp"] == "2", two
        assert abs(two["gap"] - two["padL"]) <= 1, two
        assert one["cardH"] < two["cardH"], (one, two)
    finally:
        ctx.close()
        browser.close()
        pw.stop()
