"""PRD 8.2 断点, 8.3 改值继续, 8.16 环里断点, 8.16.1 第 2 轮停下无本轮记录。"""
from __future__ import annotations

import asyncio

from tests.helpers import chain_flow, cont, ctx, loop_flow, rec_nodes, recs, run_until, wait_status


def test_breakpoint_before_third_no_later_records(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow("b")
        rid, st = await run_until(ex, store, flow, "paused")
        assert st.paused_node == "b"
        assert st.paused_phase == "before"
        names = rec_nodes(store, rid)
        assert names == ["start", "a"]
        assert "b" not in names and "c" not in names
        await hub.stop()
    asyncio.run(go())


def test_edit_input_then_continue(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow("b")
        rid, _ = await run_until(ex, store, flow, "paused")
        await cont(ex, rid, edits={"inputs": {"value": {"edited": True, "v": 99}}})
        await wait_status(ex, rid, "completed")
        b = [r for r in recs(store, rid) if r["node_id"] == "b"][-1]
        c = [r for r in recs(store, rid) if r["node_id"] == "c"][-1]
        assert b["inputs"]["value"]["v"] == 99
        assert c["inputs"]["value"]["v"] == 99 or c["inputs"]["value"].get("edited") is True
        assert (b.get("edited") or {}).get("original") is not None
        await hub.stop()
    asyncio.run(go())


def test_loop_breakpoint_every_round(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = loop_flow(inc_bp={"enabled": True})
        rid, st = await run_until(ex, store, flow, "paused")
        assert st.paused_node == "inc"
        assert rec_nodes(store, rid) == ["start"]
        await cont(ex, rid)
        await wait_status(ex, rid, "paused")
        inc = [r for r in recs(store, rid) if r["node_id"] == "inc"]
        assert len(inc) == 1 and inc[0]["round"] == 1
        await cont(ex, rid)
        await wait_status(ex, rid, "paused")
        inc = [r for r in recs(store, rid) if r["node_id"] == "inc"]
        assert len(inc) == 2
        await hub.stop()
    asyncio.run(go())


def test_loop_breakpoint_only_round_2(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = loop_flow(inc_bp={"enabled": True, "round": 2})
        rid, st = await run_until(ex, store, flow, "paused")
        assert st.paused_node == "inc"
        inc = [r for r in recs(store, rid) if r["node_id"] == "inc"]
        assert [r["round"] for r in inc] == [1]
        assert st.current and st.current.round == 2
        await hub.stop()
    asyncio.run(go())
