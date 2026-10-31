"""PRD 8.8 副作用：write 重放默认模拟，read 用缓存。"""
from __future__ import annotations

import asyncio

from roboact.executor import Executor
from roboact.sse import SseHub
from roboact.adapter import CrashBetweenWriteAndKey
from roboact.tools import Tools
from tests.helpers import boot, cont, ctx, read_flow, recs, run_until, wait_status, write_flow


def test_append_note_kill_between_write_and_key(tmp_path):
    async def go():
        tools = Tools(tmp_path / "data")
        tools.crash_between_write_and_key = True
        try:
            await tools.call("append_note", {"line": "hello"}, "run1:1")
            raise AssertionError("expected crash between write and key")
        except CrashBetweenWriteAndKey:
            pass
        assert tools.note_lines() == []
        assert tools.ledger.count() == 0
        await tools.call("append_note", {"line": "hello"}, "run1:1")
        assert tools.note_lines() == ["hello"]
        await tools.call("append_note", {"line": "hello"}, "run1:1")
        assert tools.note_lines() == ["hello"]
        assert tools.ledger.count() == 1
    asyncio.run(go())


def test_append_note_kill_between_then_rerun(tmp_path):
    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        tools.crash_between_write_and_key = True
        fid, ver = boot(store, write_flow("once"))
        rid = await ex.start_full(fid, ver)
        await asyncio.sleep(0.4)
        assert tools.note_lines() == []
        assert tools.ledger.count() == 0
        assert ex.get(rid) is None
        await hub.stop()
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
        await hub2.stop()
    asyncio.run(go())


def test_write_replay_is_mocked(tmp_path):
    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        flow = write_flow("hello")
        rid1, _ = await run_until(ex, store, flow, "completed")
        assert tools.note_lines() == ["hello"]
        first = tools.ledger.count()
        fid = store.get_run(rid1)["flow_id"]
        ver = store.get_run(rid1)["flow_version"]
        rec1 = [r for r in recs(store, rid1) if r["node_id"] == "mail"][0]
        rid2 = await ex.start_node(fid, ver, "mail", input_spec={"kind": "record", "run_id": rid1, "step": rec1["step"]})
        await wait_status(ex, rid2, "completed")
        assert tools.note_lines() == ["hello"]
        assert tools.ledger.count() == first
        rec = recs(store, rid2)[0]
        assert rec["side_effect_mode"] == "mock"
        assert rec["mocked"] is True
        assert rec["outputs"]["result"]["mocked"] is True
        await hub.stop()
    asyncio.run(go())


def test_read_replay_uses_cache(tmp_path):
    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "notes.txt").write_text("cached-note\n", encoding="utf-8")
        flow = read_flow()
        rid1, _ = await run_until(ex, store, flow, "completed")
        n_read = len([c for c in tools.calls if c["tool"] == "read_note"])
        assert n_read == 1
        cached = [r for r in recs(store, rid1) if r["node_id"] == "look"][0]
        fid = store.get_run(rid1)["flow_id"]
        ver = store.get_run(rid1)["flow_version"]
        rid2 = await ex.start_node(fid, ver, "look", input_spec={"kind": "record", "run_id": rid1, "step": cached["step"]})
        await wait_status(ex, rid2, "completed")
        assert len([c for c in tools.calls if c["tool"] == "read_note"]) == 1
        rec = recs(store, rid2)[0]
        assert rec["side_effect_mode"] == "cache"
        assert rec["outputs"] == cached["outputs"]
        await hub.stop()
    asyncio.run(go())
