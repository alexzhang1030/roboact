"""Keys copied through store create/save so import metadata survives edits."""

from __future__ import annotations

GRAPH_FILE_KEYS = (
    "file_id",
    "revision",
    "step_limit",
    "file_adapter",
    "file_robot",
    "file_units",
)


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
