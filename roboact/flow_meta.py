"""Keys copied through store create/save so import metadata survives edits."""

from __future__ import annotations

GRAPH_FILE_KEYS = (
    "file_id",
    "revision",
    "step_limit",
    "file_adapter",
    "file_robot",
    "file_units",
    "mismatch_unlocked",
)


def with_file_meta(graph: dict, body: dict | None) -> dict:
    src = body or {}
    for key in GRAPH_FILE_KEYS:
        if key in src:
            graph[key] = src[key]
    return graph
