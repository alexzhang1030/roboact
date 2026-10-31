"""PRD 8.12 快照过期, 8.18.2 环内快照。"""

from __future__ import annotations

import asyncio
import copy

from roboact.executor import compute_stale
from roboact.graph import stale_targets

from tests.helpers import ctx, e, loop_flow, make_flow, n, recs, run_until


def test_downstream_param_change_does_not_stale_upstream(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow(
            [n("start", "start", data={"v": 1}), n("a", "echo"), n("b", "echo")],
            [e("e0", "start", "data", "a", "value"), e("e1", "a", "value", "b", "value")],
        )
        rid, _ = await run_until(ex, store, flow, "completed")
        row = store.get_run(rid)
        latest = store.latest_graph(row["flow_id"])
        assert latest is not None
        mutated = copy.deepcopy(latest)
        for node in mutated["nodes"]:
            if node["id"] == "b":
                node["params"] = {**(node.get("params") or {}), "_x": "changed"}
        store.save_flow(row["flow_id"], mutated, bump=True)
        latest2 = store.latest_graph(row["flow_id"])
        assert latest2 is not None
        a = [r for r in recs(store, rid) if r["node_id"] == "a"][-1]
        b = [r for r in recs(store, rid) if r["node_id"] == "b"][-1]
        assert compute_stale(a, latest2)["is_stale"] is False
        assert compute_stale(b, latest2)["is_stale"] is False
        assert "b" not in stale_targets(latest2, "b")
        await hub.stop()

    asyncio.run(go())


def test_upstream_param_change_stales_downstream(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow(
            [n("start", "start", data={"v": 1}), n("a", "echo"), n("b", "echo")],
            [e("e0", "start", "data", "a", "value"), e("e1", "a", "value", "b", "value")],
        )
        rid, _ = await run_until(ex, store, flow, "completed")
        row = store.get_run(rid)
        latest = store.latest_graph(row["flow_id"])
        assert latest is not None
        mutated = copy.deepcopy(latest)
        for node in mutated["nodes"]:
            if node["id"] == "a":
                node["params"] = {**(node.get("params") or {}), "_x": "changed"}
        store.save_flow(row["flow_id"], mutated, bump=True)
        latest2 = store.latest_graph(row["flow_id"])
        assert latest2 is not None
        assert "b" in stale_targets(latest2, "a")
        assert "a" not in stale_targets(latest2, "a")
        a = [r for r in recs(store, rid) if r["node_id"] == "a"][-1]
        b = [r for r in recs(store, rid) if r["node_id"] == "b"][-1]
        assert compute_stale(a, latest2)["is_stale"] is False
        assert compute_stale(b, latest2)["is_stale"] is True
        await hub.stop()

    asyncio.run(go())


def test_loop_self_param_change_stales_own_snapshots(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = loop_flow()
        rid, _ = await run_until(ex, store, flow, "completed")
        row = store.get_run(rid)
        latest = store.latest_graph(row["flow_id"])
        assert latest is not None
        assert "inc" in stale_targets(latest, "inc")
        mutated = copy.deepcopy(latest)
        for node in mutated["nodes"]:
            if node["id"] == "inc":
                node["params"] = {**(node.get("params") or {}), "_x": "changed"}
        store.save_flow(row["flow_id"], mutated, bump=True)
        latest2 = store.latest_graph(row["flow_id"])
        assert latest2 is not None
        inc = [r for r in recs(store, rid) if r["node_id"] == "inc"]
        assert inc and all(compute_stale(r, latest2)["is_stale"] for r in inc)
        await hub.stop()

    asyncio.run(go())
