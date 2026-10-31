"""exec-model A1–A17. PRD §8 对应关系写在各测试名里。"""
from __future__ import annotations

import asyncio
import time

from roboact.errors import ApiError
from roboact.executor import Executor
from roboact.sse import SseHub
from tests.helpers import (
    boot,
    chain_flow,
    confirm_flow,
    cont,
    ctx,
    e,
    loop_flow,
    make_flow,
    n,
    rec_nodes,
    recs,
    run_until,
    wait_status,
    write_flow,
)


def test_a1_write_crash_does_not_double(tmp_path):
    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        flow = write_flow("once")
        fid, ver = boot(store, flow)
        ex.hooks["crash_before_t2"] = True
        rid = await ex.start_full(fid, ver)
        await asyncio.sleep(0.4)
        assert tools.note_lines() == ["once"]
        assert ex.get(rid) is None
        hub2 = SseHub(store)
        hub2.start()
        ex2 = Executor(store, tools, hub2, str(tmp_path / "data"))
        await ex2.adopt_unfinished()
        st = ex2.get(rid)
        assert st and st.status == "paused"
        assert st.node_state.get("mail", {}).get("state") == "interrupted_unknown"
        await cont(ex2, rid)
        await wait_status(ex2, rid, "completed")
        assert tools.note_lines() == ["once"]
        assert tools.ledger.count() == 1
        await hub.stop()
        await hub2.stop()
    asyncio.run(go())


def test_a2_pending_survives_restart(tmp_path):
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
        assert [r["round"] for r in inc] == [1, 2, 3]
        await hub2.stop()
    asyncio.run(go())


def test_a3_restart_does_not_autoresume(tmp_path):
    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        rid, _ = await run_until(ex, store, chain_flow("b"), "paused")
        n0 = len(recs(store, rid))
        await hub.stop()
        hub2 = SseHub(store)
        hub2.start()
        ex2 = Executor(store, tools, hub2, str(tmp_path / "data"))
        await ex2.adopt_unfinished()
        await asyncio.sleep(0.3)
        assert ex2.get(rid).status == "paused"
        assert ex2.get(rid).pause["reason"] == "restart"
        assert len(recs(store, rid)) == n0
        await hub2.stop()
    asyncio.run(go())


def test_a8_two_continues_one_wins(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        rid, st = await run_until(ex, store, chain_flow("b"), "paused")
        pid = st.pause["pause_id"]
        ok = await ex.continue_run(rid, pid, client_id="a")
        assert ok["status"] == "running"
        try:
            await ex.continue_run(rid, pid, client_id="b")
            raise AssertionError("second continue should 409")
        except ApiError as err:
            assert err.error == "pause_mismatch"
        await wait_status(ex, rid, "completed")
        await hub.stop()
    asyncio.run(go())


def test_a11_step_limit_not_error(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        rid, st = await run_until(ex, store, loop_flow(compare=-1, op="eq"), "step_limit", step_limit=10)
        assert st.status == "step_limit"
        assert st.error is None
        assert max(r["step"] for r in recs(store, rid)) == 10
        await hub.stop()
    asyncio.run(go())


def test_a15_storage_error_pauses(tmp_path):
    async def go():
        import sqlite3
        ex, store, _, hub = ctx(tmp_path)
        rid, st = await run_until(ex, store, chain_flow("b"), "paused")
        orig = store.add_step

        def flaky(rec):
            if rec.get("node_id") == "b":
                raise sqlite3.OperationalError("disk I/O error (test hook)")
            return orig(rec)

        store.add_step = flaky
        await cont(ex, rid)
        st = await wait_status(ex, rid, "paused")
        assert st.pause["reason"] == "storage_error"
        store.add_step = orig
        await cont(ex, rid)
        st = await wait_status(ex, rid, "completed")
        assert st.status == "completed"
        await hub.stop()
    asyncio.run(go())


def test_user_stop_is_stopped_not_interrupted(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow([n("s", "sleep", seconds=2.0)], [])
        rid, _ = await run_until(ex, store, flow, "running", timeout=1)
        await ex.stop_run(rid)
        st = await wait_status(ex, rid, "stopped")
        assert st.status == "stopped"
        for ns in st.node_state.values():
            assert ns.get("state") not in ("interrupted", "interrupted_unknown")
            if ns.get("state") in ("running", "stopped"):
                assert ns.get("state") == "stopped"
        await hub.stop()
    asyncio.run(go())


def test_write_timeout_is_interrupted_unknown(tmp_path):
    async def go():
        from roboact.nodes import ToolNode
        ex, store, tools, hub = ctx(tmp_path)
        old = ToolNode.timeout_s
        ToolNode.timeout_s = 0.15
        tools.delay_after_commit = 2.0
        try:
            rid, st = await run_until(ex, store, write_flow("slow"), "paused", timeout=4)
            assert st.pause and st.pause["reason"] == "write_timeout"
            assert st.node_state.get("mail", {}).get("state") == "interrupted_unknown"
            rec = [r for r in recs(store, rid) if r["node_id"] == "mail"][0]
            assert rec["status"] == "interrupted_unknown"
            assert rec["error"]["type"] == "timeout"
            tools.delay_after_commit = 0.0
            await cont(ex, rid)
            st = await wait_status(ex, rid, "completed")
            assert st.status == "completed"
            assert tools.note_lines() == ["slow"]
            assert tools.ledger.count() == 1
        finally:
            ToolNode.timeout_s = old
        await hub.stop()
    asyncio.run(go())


def test_a16_timeout(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow([n("s", "sleep", seconds=2.0)], [])
        rid, st = await run_until(ex, store, flow, "error", timeout=3)
        assert st.status == "error"
        rec = recs(store, rid)[0]
        assert rec["error"]["type"] == "timeout"
        await hub.stop()
    asyncio.run(go())


def test_a17_loop_without_exit(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow(
            [n("start", "start", data={}), n("a", "echo"), n("b", "echo")],
            [e("e0", "start", "data", "a", "value"), e("e1", "a", "value", "b", "value"), e("e2", "b", "value", "a", "value")],
        )
        try:
            await run_until(ex, store, flow, "completed")
            raise AssertionError
        except ApiError as err:
            assert err.error == "loop_without_exit"
            assert err.detail.get("node_ids")
        await hub.stop()
    asyncio.run(go())


def test_full_run_ignores_pins(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow()
        fid, ver = boot(store, flow)
        store.set_pin(fid, "a", "value", value={"pinned": True})
        rid = await ex.start_full(fid, ver)
        st = await wait_status(ex, rid, "completed")
        snap = ex.snapshot(rid)
        assert any(p["node_id"] == "a" for p in snap["unused_pins"])
        a = [r for r in recs(store, rid) if r["node_id"] == "a"][0]
        assert a["input_source"] == "upstream"
        await hub.stop()
    asyncio.run(go())
