from __future__ import annotations

from typing import Any

from .nodes import get_instance
from .util import dump, sha256_text


def node_map(flow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in flow.get("nodes") or []}


def edges_of(flow: dict[str, Any]) -> list[dict[str, Any]]:
    return list(flow.get("edges") or [])


def outgoing(flow: dict[str, Any], node_id: str, port: str | None = None) -> list[dict[str, Any]]:
    out = []
    for e in edges_of(flow):
        if e["source"] != node_id:
            continue
        if port is not None and e.get("source_port") != port:
            continue
        out.append(e)
    return out


def incoming(flow: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    return [e for e in edges_of(flow) if e["target"] == node_id]


def fingerprint(node: dict[str, Any]) -> str:
    payload = {"type": node.get("type"), "params": node.get("params") or {}}
    return sha256_text(dump(payload))


def sccs(flow: dict[str, Any]) -> list[set[str]]:
    """Tarjan strongly connected components."""
    ids = [n["id"] for n in flow.get("nodes") or []]
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges_of(flow):
        if e["source"] in adj and e["target"] in adj:
            adj[e["source"]].append(e["target"])
    index = 0
    stack: list[str] = []
    on = set()
    idx: dict[str, int] = {}
    low: dict[str, int] = {}
    out: list[set[str]] = []

    def strong(v: str) -> None:
        nonlocal index
        idx[v] = index
        low[v] = index
        index += 1
        stack.append(v)
        on.add(v)
        for w in adj[v]:
            if w not in idx:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp: set[str] = set()
            while True:
                w = stack.pop()
                on.discard(w)
                comp.add(w)
                if w == v:
                    break
            out.append(comp)

    for v in ids:
        if v not in idx:
            strong(v)
    return out


def cyclic_sccs(flow: dict[str, Any]) -> list[set[str]]:
    cyclic = []
    for comp in sccs(flow):
        if len(comp) > 1:
            cyclic.append(comp)
            continue
        nid = next(iter(comp))
        if any(e["source"] == nid and e["target"] == nid for e in edges_of(flow)):
            cyclic.append(comp)
    return cyclic


def cycle_node_ids(flow: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for c in cyclic_sccs(flow):
        out |= c
    return out


def cycle_exit_details(flow: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = node_map(flow)
    out = []
    for comp in cyclic_sccs(flow):
        conds = [i for i in comp if nodes.get(i, {}).get("type") == "condition"]
        has_exit = False
        for cid in conds:
            for e in outgoing(flow, cid):
                if e["target"] not in comp:
                    has_exit = True
        if not has_exit:
            out.append({"node_ids": sorted(comp), "condition_ids": conds})
    return out


def cycle_exit_errors(flow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for item in cycle_exit_details(flow):
        names = ", ".join(item["node_ids"])
        conds = item["condition_ids"]
        if not conds:
            errors.append(
                f"this loop has no condition exit (nodes {names}). "
                "add a condition node and wire yes or no out of the loop"
            )
        else:
            errors.append(
                "loop has condition node(s) ("
                + ", ".join(sorted(conds))
                + f") but yes/no are not wired out of the loop (nodes {names})"
            )
    return errors


def influencing_map(flow: dict[str, Any]) -> dict[str, set[str]]:
    ids = [n["id"] for n in flow.get("nodes") or []]
    succ: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges_of(flow):
        succ.setdefault(e["source"], []).append(e["target"])
    reach: dict[str, set[str]] = {}
    for u in ids:
        seen = {u}
        stack = [u]
        while stack:
            x = stack.pop()
            for v in succ.get(x, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        reach[u] = seen
    cyclic = cycle_node_ids(flow)
    inf: dict[str, set[str]] = {i: set() for i in ids}
    for u in ids:
        for v in reach[u]:
            if u == v and u not in cyclic:
                continue
            inf[v].add(u)
    return inf


def connected_types(flow: dict[str, Any], edge: dict[str, Any]) -> tuple[str, str]:
    nodes = node_map(flow)
    src = nodes.get(edge["source"])
    dst = nodes.get(edge["target"])
    src_t = "any"
    dst_t = "any"
    if src:
        inst = get_instance(src["type"])
        for p in inst.output_ports(src.get("params")):
            if p["name"] == edge.get("source_port"):
                src_t = p["json_type"]
    if dst:
        inst = get_instance(dst["type"])
        for p in inst.input_ports(dst.get("params")):
            if p["name"] == edge.get("target_port"):
                dst_t = p["json_type"]
    return src_t, dst_t


def types_compatible(src_t: str, dst_t: str) -> bool:
    if src_t in ("any", "") or dst_t in ("any", ""):
        return True
    return src_t == dst_t


def params_changed(old: dict[str, Any] | None, new: dict[str, Any] | None) -> set[str]:
    old_n = {n["id"]: n for n in (old or {}).get("nodes") or []}
    changed: set[str] = set()
    for n in (new or {}).get("nodes") or []:
        prev = old_n.get(n["id"])
        if prev is None:
            continue
        if fingerprint(prev) != fingerprint(n):
            changed.add(n["id"])
    return changed
