"""PRD 8.4 刷新不丢, 8.11 服务端重启, 8.18.1 环中途重启。"""

from __future__ import annotations

import asyncio

from roboact.executor import Executor
from roboact.sse import SseHub

from tests.helpers import chain_flow, cont, ctx, loop_flow, recs, run_until, wait_status


def test_paused_state_survives_reload(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow("b")
        rid, _ = await run_until(ex, store, flow, "paused")
        row = store.get_run(rid)
        assert row["status"] == "paused"
        assert (row["pause"] or {}).get("node_id") == "b"
        rec = recs(store, rid)
        assert [r["node_id"] for r in rec] == ["start", "a"]
        snap = ex.snapshot(rid)
        assert snap["paused_node"] == "b"
        pause_id = snap["pause"]["pause_id"]
        snap2 = ex.snapshot(rid)
        assert snap2["pause"]["pause_id"] == pause_id
        await hub.stop()

    asyncio.run(go())


def test_restart_then_continue_matches(tmp_path):
    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        flow = chain_flow("b")
        rid, _ = await run_until(ex, store, flow, "paused")
        await hub.stop()
        hub2 = SseHub(store)
        hub2.start()
        ex2 = Executor(store, tools, hub2, str(tmp_path / "data"))
        await ex2.adopt_unfinished()
        st = ex2.get(rid)
        assert st and st.status == "paused"
        assert st.pause and st.pause.get("reason") == "restart"
        await asyncio.sleep(0.2)
        assert len(recs(store, rid)) == 2
        await cont(ex2, rid)
        st = await wait_status(ex2, rid, "completed")
        assert st.status == "completed"
        names = [r["node_id"] for r in recs(store, rid)]
        assert names == ["start", "a", "b", "c", "out"]
        await hub2.stop()

    asyncio.run(go())


def test_loop_restart_midway(tmp_path):
    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        flow = loop_flow(inc_bp={"enabled": True, "round": 2})
        rid, _ = await run_until(ex, store, flow, "paused")
        await hub.stop()
        hub2 = SseHub(store)
        hub2.start()
        ex2 = Executor(store, tools, hub2, str(tmp_path / "data"))
        await ex2.adopt_unfinished()
        await cont(ex2, rid)
        st = await wait_status(ex2, rid, "completed")
        assert st.status == "completed"
        inc = [r for r in recs(store, rid) if r["node_id"] == "inc"]
        cond = [r for r in recs(store, rid) if r["node_id"] == "cond"]
        assert [r["round"] for r in inc] == [1, 2, 3]
        assert [r["round"] for r in cond] == [1, 2, 3]
        await hub2.stop()

    asyncio.run(go())
