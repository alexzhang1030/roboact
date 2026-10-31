from __future__ import annotations

import hashlib
from typing import Any, Iterable

from .nodes import get_instance
from .util import dump


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
    return hashlib.sha256(dump(payload).encode("utf-8")).hexdigest()


def fingerprints(flow: dict[str, Any]) -> dict[str, str]:
    return {n["id"]: fingerprint(n) for n in flow.get("nodes") or []}


def reachable_from(flow: dict[str, Any], start_id: str) -> set[str]:
    seen: set[str] = set()
    stack = [start_id]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        for e in outgoing(flow, u):
            stack.append(e["target"])
    return seen


def can_reach(flow: dict[str, Any], src: str, dst: str) -> bool:
    return dst in reachable_from(flow, src)


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


def is_back_edge(flow: dict[str, Any], edge: dict[str, Any]) -> bool:
    """An edge is a back-edge if its target can reach its source (closes a cycle)."""
    src, dst = edge["source"], edge["target"]
    if src == dst:
        return True
    # Walk from dst without using this edge; if we reach src, this edge closes a cycle.
    seen: set[str] = set()
    stack = [dst]
    skip = (edge.get("id"), src, dst, edge.get("source_port"), edge.get("target_port"))
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        if u == src:
            return True
        for e in outgoing(flow, u):
            key = (e.get("id"), e["source"], e["target"], e.get("source_port"), e.get("target_port"))
            if key == skip:
                continue
            stack.append(e["target"])
    return False


def cycle_exit_errors(flow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = node_map(flow)
    for comp in cyclic_sccs(flow):
        conds = [i for i in comp if nodes.get(i, {}).get("type") == "condition"]
        has_exit = False
        for cid in conds:
            for e in outgoing(flow, cid):
                if e["target"] not in comp:
                    has_exit = True
        names = ", ".join(sorted(comp))
        if not has_exit:
            if not conds:
                errors.append(
                    f"这个环没有条件判断节点作出口（节点 {names}）。"
                    "请加入条件判断节点，并将其「是」或「否」连到环外。"
                )
            else:
                errors.append(
                    f"环中有条件节点（{', '.join(sorted(conds))}），"
                    f"但其「是」「否」都未连到环外（节点 {names}）。"
                )
    return errors


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


def influencing_nodes(flow: dict[str, Any], node_id: str) -> set[str]:
    """Nodes whose param change should stale snapshots of node_id."""
    cyclic = cycle_node_ids(flow)
    result: set[str] = set()
    for n in flow.get("nodes") or []:
        nid = n["id"]
        if node_id not in reachable_from(flow, nid):
            continue
        if nid == node_id and nid not in cyclic:
            continue
        result.add(nid)
    return result


def stale_targets(flow: dict[str, Any], changed_id: str) -> set[str]:
    """Snapshots of these nodes become stale when changed_id's params change."""
    targets = reachable_from(flow, changed_id)
    if changed_id not in cycle_node_ids(flow):
        targets.discard(changed_id)
    return targets


def seeds(flow: dict[str, Any], allowed: set[str] | None = None) -> list[str]:
    out = []
    for n in flow.get("nodes") or []:
        if allowed is not None and n["id"] not in allowed:
            continue
        inc = [e for e in incoming(flow, n["id"]) if allowed is None or e["source"] in allowed]
        if not inc:
            out.append(n["id"])
    return out


def required_ports(flow: dict[str, Any], node_id: str, allowed: set[str] | None = None) -> set[str]:
    ports: set[str] = set()
    for e in incoming(flow, node_id):
        if allowed is not None and e["source"] not in allowed:
            continue
        ports.add(e.get("target_port") or "in")
    return ports


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


def validate_for_run(flow: dict[str, Any]) -> list[str]:
    errors = list(cycle_exit_errors(flow))
    if not any(n.get("type") == "start" for n in flow.get("nodes") or []):
        errors.append("流程缺少「开始」节点。")
    return errors


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
