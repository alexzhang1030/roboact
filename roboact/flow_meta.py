"""Keys copied through store create/save so import metadata survives edits."""

from __future__ import annotations

from . import FORMAT_VERSION

GRAPH_FILE_KEYS = (
    "file_id",
    "revision",
    "step_limit",
    "file_adapter",
    "file_robot",
    "file_units",
)


def default_start_node() -> dict:
    return {"id": "start", "type": "start", "x": 240, "y": 200, "params": {"data": {}}}


def assemble_graph(flow_id: str, name: str, version: int, body: dict | None, nodes: list) -> dict:
    src = body or {}
    graph = {
        "id": flow_id,
        "flow_id": flow_id,
        "name": name,
        "version": version,
        "format_version": FORMAT_VERSION,
        "nodes": nodes,
        "edges": src.get("edges") or [],
        "groups": src.get("groups") or [],
        "adapter_id": src.get("adapter_id"),
        "action_list_version": src.get("action_list_version"),
    }
    return with_file_meta(graph, body)


def with_file_meta(graph: dict, body: dict | None) -> dict:
    src = body or {}
    for key in GRAPH_FILE_KEYS:
        if key in src:
            graph[key] = src[key]
    return graph


def persist_unlock_record(graph: dict, body: dict | None) -> dict:
    """Saves only. Create and import must never copy a client unlock_record."""
    rec = (body or {}).get("unlock_record")
    if rec is not None:
        graph["unlock_record"] = rec
    return graph
