"""Flow graphs for acceptance behaviour tests."""

from __future__ import annotations

from typing import Any


def _node(
    nid: str, typ: str, x: int, y: int, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"id": nid, "type": typ, "x": x, "y": y, "params": params or {}}


def _edge(eid: str, src: str, sp: str, dst: str, dp: str) -> dict[str, Any]:
    return {
        "id": eid,
        "source": src,
        "source_port": sp,
        "target": dst,
        "target_port": dp,
        "kind": "data",
    }


def _graph(
    name: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "nodes": nodes,
        "edges": edges,
        "groups": [],
        "bump": True,
    }
    body.update(extra)
    return body


def chain(name: str, mids: list[str], data: dict[str, Any]) -> dict[str, Any]:
    """start -> each mid echo -> output."""
    nodes = [_node("start", "start", 40, 120, {"data": data})]
    edges: list[dict[str, Any]] = []
    prev, port = "start", "data"
    for i, nid in enumerate(mids):
        nodes.append(_node(nid, "echo", 360 + i * 300, 120))
        edges.append(_edge(f"e{i}", prev, port, nid, "value"))
        prev, port = nid, "value"
    nodes.append(_node("out", "output", 360 + len(mids) * 300, 120))
    edges.append(_edge("eout", prev, port, "out", "value"))
    return _graph(name, nodes, edges)


def loop_flow(exit_at: int, step_limit: int = 40) -> dict[str, Any]:
    """start -> inc -> condition; no loops back, yes leaves. Exit when value >= exit_at."""
    nodes = [
        _node("start", "start", 40, 160, {"data": {"value": 0}}),
        _node("body", "inc", 280, 160),
        _node(
            "cond",
            "condition",
            520,
            160,
            {"field": "value", "op": "gte", "compare": exit_at},
        ),
        _node("out", "output", 760, 160),
    ]
    edges = [
        _edge("e0", "start", "data", "body", "value"),
        _edge("e1", "body", "value", "cond", "value"),
        _edge("e2", "cond", "no", "body", "value"),
        _edge("e3", "cond", "yes", "out", "value"),
    ]
    return _graph("loop", nodes, edges, step_limit=step_limit)


def loop_without_exit() -> dict[str, Any]:
    nodes = [
        _node("start", "start", 40, 160, {"data": {"value": 1}}),
        _node("a", "echo", 280, 160),
        _node("b", "echo", 520, 160),
    ]
    edges = [
        _edge("e0", "start", "data", "a", "value"),
        _edge("e1", "a", "value", "b", "value"),
        _edge("e2", "b", "value", "a", "value"),
    ]
    return _graph("no-exit", nodes, edges)


def version_flow() -> dict[str, Any]:
    nodes = [
        _node("start", "start", 40, 160, {"data": {"value": 1}}),
        _node(
            "cond",
            "condition",
            300,
            160,
            {"field": "value", "op": "eq", "compare": 1},
        ),
        _node("yesn", "echo", 560, 40),
        _node("non", "echo", 560, 280),
    ]
    edges = [
        _edge("e0", "start", "data", "cond", "value"),
        _edge("e1", "cond", "yes", "yesn", "value"),
        _edge("e2", "cond", "no", "non", "value"),
    ]
    return _graph("version", nodes, edges)


def approval_flow() -> dict[str, Any]:
    nodes = [
        _node("start", "start", 40, 160, {"data": {"value": "go"}}),
        _node("ask", "human_confirm", 300, 160, {"prompt": "ok?"}),
        _node("ok", "echo", 560, 40),
        _node("no", "echo", 560, 280),
    ]
    edges = [
        _edge("e0", "start", "data", "ask", "value"),
        _edge("e1", "ask", "pass", "ok", "value"),
        _edge("e2", "ask", "reject", "no", "value"),
    ]
    return _graph("approval", nodes, edges)


def stale_flow() -> dict[str, Any]:
    nodes = [
        _node("up", "constant", 40, 160, {"value": "1", "value_type": "string"}),
        _node(
            "down",
            "condition",
            320,
            160,
            {"field": "value", "op": "eq", "compare": "1"},
        ),
    ]
    edges = [_edge("e0", "up", "value", "down", "value")]
    return _graph("stale", nodes, edges)


def fail_flow() -> dict[str, Any]:
    nodes = [
        _node("start", "start", 40, 160, {"data": {"value": 1}}),
        _node("n1", "echo", 280, 160),
        _node("bad", "fail", 520, 160, {"message": "tool failed (stub)"}),
        _node("n2", "echo", 760, 160),
    ]
    edges = [
        _edge("e0", "start", "data", "n1", "value"),
        _edge("e1", "n1", "value", "bad", "value"),
        _edge("e2", "bad", "value", "n2", "value"),
    ]
    return _graph("fail", nodes, edges)
