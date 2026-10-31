from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def http_json(
    base: str, path: str, method: str = "GET", body: Any = None, timeout: float = 8.0
) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if not raw:
                return None
            return json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw.decode())
        except Exception:
            payload = {"error": raw.decode(errors="replace")}
        raise RuntimeError(f"{method} {path} -> {e.code} {payload}") from e


def http_status(
    base: str, path: str, method: str = "GET", body: Any = None, timeout: float = 8.0
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            payload = json.loads(raw.decode()) if raw else None
            return r.status, payload
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw.decode())
        except Exception:
            payload = {"error": raw.decode(errors="replace")}
        return e.code, payload


def echo_chain_graph(n_nodes: int, name: str = "") -> dict[str, Any]:
    """start + echoes + output. n_nodes includes start and output."""
    n_echo = max(0, n_nodes - 2)
    nodes: list[dict[str, Any]] = [
        {"id": "start", "type": "start", "x": 0, "y": 0, "params": {"data": {"v": 1}}},
    ]
    edges: list[dict[str, Any]] = []
    prev, prev_port = "start", "data"
    for i in range(n_echo):
        nid = f"n{i}"
        nodes.append(
            {
                "id": nid,
                "type": "echo",
                "x": (i % 25) * 260,
                "y": (i // 25) * 140,
                "params": {},
            }
        )
        edges.append(
            {
                "id": f"e{i}",
                "source": prev,
                "source_port": prev_port,
                "target": nid,
                "target_port": "value",
                "kind": "data",
            }
        )
        prev, prev_port = nid, "value"
    nodes.append({"id": "out", "type": "output", "x": 40, "y": 40, "params": {}})
    edges.append(
        {
            "id": "eout",
            "source": prev,
            "source_port": prev_port,
            "target": "out",
            "target_port": "value",
            "kind": "data",
        }
    )
    return {
        "name": name or f"{n_nodes} nodes",
        "nodes": nodes,
        "edges": edges,
        "groups": [],
        "bump": True,
    }


def motion_graph(name: str = "A6 motion") -> dict[str, Any]:
    return {
        "name": name,
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "x": 40,
                "y": 160,
                "params": {"data": {"x": 0.4, "y": 0.1, "z": 0.2}},
            },
            {
                "id": "go",
                "type": "fake_set_pose",
                "x": 300,
                "y": 160,
                "params": {"x": 0.4, "y": 0.1, "z": 0.2},
            },
            {"id": "grip", "type": "fake_gripper", "x": 560, "y": 160, "params": {"open": True}},
            {"id": "out", "type": "output", "x": 560, "y": 360, "params": {}},
        ],
        "edges": [
            {
                "id": "e0",
                "source": "start",
                "source_port": "data",
                "target": "go",
                "target_port": "target",
                "kind": "data",
            },
            {
                "id": "e1",
                "source": "go",
                "source_port": "pose",
                "target": "grip",
                "target_port": "target",
                "kind": "data",
            },
            {
                "id": "e2",
                "source": "grip",
                "source_port": "open",
                "target": "out",
                "target_port": "value",
                "kind": "data",
            },
        ],
        "groups": [],
        "bump": True,
    }


def put_flow(base: str, graph: dict[str, Any]) -> dict[str, Any]:
    boot = http_json(base, "/api/bootstrap")
    fid = boot["flow"]["id"]
    out = http_json(base, f"/api/flows/{fid}", "PUT", graph, timeout=20)
    return out["flow"]


SSE_WRAP = """
(() => {
  const Orig = window.EventSource
  function Wrapped(url, cfg) {
    const es = new Orig(url, cfg)
    window.__sseLog = window.__sseLog || []
    ;['snapshot', 'nodes', 'paused', 'resumed', 'run_finished'].forEach(type => {
      es.addEventListener(type, ev => {
        window.__sseLog.push({ type, id: ev.lastEventId, t: performance.now() })
      })
    })
    return es
  }
  Wrapped.prototype = Orig.prototype
  Object.setPrototypeOf(Wrapped, Orig)
  window.EventSource = Wrapped
  window.__sseLog = []
})()
"""
