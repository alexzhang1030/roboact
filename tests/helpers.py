from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from roboact.adapter import FakeAdapter
from roboact.executor import Executor, compute_stale
from roboact.nodes import bind_adapter
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


def ctx(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    hub = SseHub(store, interval=0.1)
    hub.start()
    tools = Tools(tmp_path / "data")
    ex = Executor(store, tools, hub, str(tmp_path / "data"))
    ex.ui_lost_s = None
    return ex, store, tools, hub


def adapter_ctx(tmp_path: Path, adapter: FakeAdapter | None = None):
    adapter = adapter or FakeAdapter()
    bind_adapter(adapter)
    store = Store(tmp_path / "t.db")
    hub = SseHub(store, interval=0.1)
    hub.start()
    ex = Executor(store, adapter, hub, str(tmp_path / "data"))
    ex.ui_lost_s = None
    return ex, store, adapter, hub


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
