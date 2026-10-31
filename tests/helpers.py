from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from roboact.catalog import zh
from roboact.executor import Executor, compute_stale
from roboact.sse import SseHub
from roboact.store import Store
from roboact.tools import Tools
from roboact.util import new_id


def n(id: str, typ: str, *, x: int = 0, y: int = 0, breakpoints: dict | None = None, **params):
    return {
        "id": id,
        "type": typ,
        "x": x,
        "y": y,
        "params": params,
        "breakpoints": breakpoints or {},
    }


def e(id: str, src: str, sp: str, dst: str, dp: str):
    return {
        "id": id,
        "source": src,
        "source_port": sp,
        "target": dst,
        "target_port": dp,
        "kind": "data",
    }


def make_flow(nodes, edges, fid="fl_test", version=1):
    return {
        "id": fid,
        "name": "test",
        "version": version,
        "format_version": 1,
        "nodes": nodes,
        "edges": edges,
        "groups": [],
    }


def chain_flow(bp_node: str | None = None, phase: str = "before", extra_bp: dict | None = None):
    bps = {}
    if bp_node:
        bps[bp_node] = {phase: {"enabled": True, **(extra_bp or {})}}
    nodes = [
        n("start", "start", data={"v": 1}),
        n("a", "echo", breakpoints=bps.get("a", {})),
        n("b", "echo", breakpoints=bps.get("b", {})),
        n("c", "echo", breakpoints=bps.get("c", {})),
        n("out", "output"),
    ]
    edges = [
        e("e0", "start", "data", "a", "value"),
        e("e1", "a", "value", "b", "value"),
        e("e2", "b", "value", "c", "value"),
        e("e3", "c", "value", "out", "value"),
    ]
    return make_flow(nodes, edges)


def loop_flow(compare=3, op="gte", inc_bp=None, fail=False):
    inc_type = "fail" if fail else "inc"
    nodes = [
        n("start", "start", data={"value": 0}),
        n("inc", inc_type, breakpoints={"before": inc_bp} if inc_bp else {}),
        n("cond", "condition", field="value", op=op, compare=compare),
        n("out", "output"),
    ]
    edges = [
        e("e0", "start", "data", "inc", "value"),
        e("e1", "inc", "value", "cond", "value"),
        e("e2", "cond", "yes", "out", "value"),
        e("e3", "cond", "no", "inc", "value"),
    ]
    return make_flow(nodes, edges)


def confirm_flow():
    nodes = [
        n("start", "start", data={"v": 1}),
        n("hc", "human_confirm", prompt=zh("confirm.default")),
        n("yes", "echo"),
        n("no", "echo"),
        n("out_yes", "output"),
        n("out_no", "output"),
    ]
    edges = [
        e("e0", "start", "data", "hc", "value"),
        e("e1", "hc", "pass", "yes", "value"),
        e("e2", "hc", "reject", "no", "value"),
        e("e3", "yes", "value", "out_yes", "value"),
        e("e4", "no", "value", "out_no", "value"),
    ]
    return make_flow(nodes, edges)


def write_flow(line="hello"):
    nodes = [
        n("start", "start", data={"line": line}),
        n(
            "mail",
            "tool_call",
            tool="append_note",
            allowed_tools=["append_note", "read_note", "clock"],
        ),
        n("out", "output"),
    ]
    edges = [
        e("e0", "start", "data", "mail", "args"),
        e("e1", "mail", "result", "out", "value"),
    ]
    return make_flow(nodes, edges)


def read_flow():
    nodes = [
        n("start", "start", data={}),
        n("look", "tool_call", tool="read_note", allowed_tools=["read_note", "clock"]),
        n("out", "output"),
    ]
    edges = [
        e("e0", "start", "data", "look", "args"),
        e("e1", "look", "result", "out", "value"),
    ]
    return make_flow(nodes, edges)


def ctx(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    hub = SseHub(store, interval=0.1)
    hub.start()
    tools = Tools(tmp_path / "data")
    ex = Executor(store, tools, hub, str(tmp_path / "data"))
    ex.ui_lost_s = None
    return ex, store, tools, hub


def boot(store: Store, flow: dict) -> tuple[str, int]:
    body = {**flow, "id": new_id("fl_")}
    body.pop("flow_id", None)
    g = store.create_flow(flow.get("name") or "test", body)
    fid = g["id"]
    for node in flow.get("nodes") or []:
        for when, spec in (node.get("breakpoints") or {}).items():
            if spec and spec.get("enabled"):
                body: dict[str, Any] = {"enabled": True}
                if spec.get("round"):
                    body["mode"] = "round"
                    body["round"] = spec["round"]
                elif spec.get("field"):
                    body["mode"] = "condition"
                    body["condition"] = {
                        "field": spec["field"],
                        "op": spec.get("op") or "eq",
                        "value": spec.get("value"),
                    }
                else:
                    body["mode"] = "every"
                store.set_breakpoint(fid, node["id"], when, body)
    return fid, g["version"]


async def wait_status(ex: Executor, run_id: str, *want: str, timeout: float = 4.0):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        st = ex.get(run_id)
        last = st.status if st else None
        if st and st.status in want:
            return st
        await asyncio.sleep(0.01)
    raise TimeoutError(f"want {want}, last={last}")


async def run_until(ex, store, flow, *want, timeout=4.0, kind="full", **kw):
    fid, ver = boot(store, flow)
    if kind == "node":
        rid = await ex.start_node(fid, ver, kw.pop("node_id"), **kw)
    elif kind == "group":
        rid, _ = await ex.start_group(fid, ver, kw.pop("node_ids"), **kw)
    elif kind == "rerun":
        rid = await ex.rerun_from(kw.pop("parent_run_id"), kw.pop("step"), **kw)
    else:
        rid = await ex.start_full(
            fid,
            ver,
            max_steps=kw.get("max_steps") or kw.get("step_limit"),
            initial_input=kw.get("initial_input"),
            side_effects=kw.get("side_effects") or "default",
        )
    st = await wait_status(ex, rid, *want, timeout=timeout)
    return rid, st


async def cont(ex, rid, edits=None, mode="continue", resolution=None, readback_id=None):
    st = ex.get(rid)
    pid = (st.pause or {}).get("pause_id")
    if readback_id is None and st.pause and (st.pause.get("readback") or {}).get("readback_id"):
        readback_id = st.pause["readback"]["readback_id"]
    if mode == "step":
        return await ex.step_run(
            rid, pid, edits=edits, resolution=resolution, readback_id=readback_id
        )
    return await ex.continue_run(
        rid, pid, edits=edits, resolution=resolution, readback_id=readback_id
    )


def recs(store, rid):
    rows = store.list_steps(rid, limit=10000)
    latest = None
    row = store.get_run(rid)
    if row:
        latest = store.latest_graph(row["flow_id"])
    out = []
    for s in rows:
        s = dict(s)
        info = compute_stale(s, latest)
        s["stale"] = info["is_stale"]
        s["stale_info"] = info
        s["inputs"] = s.get("input")
        s["outputs"] = s.get("output")
        s["mocked"] = s.get("side_effect_mode") == "mock"
        out.append(s)
    return out


def rec_nodes(store, rid):
    return [r["node_id"] for r in recs(store, rid)]
