"""exec-model v0.2: adapter target, bidirectional heartbeat, cancel, restart."""
from __future__ import annotations

import asyncio

from roboact.adapter import AdapterInfo, FakeAdapter
from roboact.errors import ApiError
from roboact.executor import Executor
from roboact.nodes import bind_adapter
from roboact.sse import SseHub
from roboact.store import Store
from tests.helpers import boot, ctx, e, make_flow, n, recs, run_until, wait_status


def test_adapter_descriptor_uses_target_not_kind():
    ad = FakeAdapter()
    info = ad.info()
    assert isinstance(info, AdapterInfo)
    assert info.target == "sim"
    assert info.name == "假适配器"
    assert info.version
    assert info.action_list_version == "fake-v1"
    pub = ad.public()
    assert "kind" not in pub
    assert pub["target"] == "sim"
    tick = next(a for a in ad.list_actions() if a.id == "fake_tick")
    assert tick.expiry == "adapter_only"
    pose = next(a for a in ad.list_actions() if a.id == "fake_set_pose")
    assert pose.expiry == "controller"
    assert pose.return_mode == "goal_progress"
    assert pose.read_state is True


def test_fake_adapter_cancels_goal_when_executor_killed():
    """Kill the executor (stop backend heartbeats); FakeAdapter cancels the in-flight goal."""

    async def go():
        adapter = FakeAdapter()
        adapter.heartbeat_timeout_s = 0.2
        adapter.async_steps = 40
        adapter.start_watchdog()
        adapter.receive_heartbeat()

        exec_task = asyncio.create_task(adapter.execute("fake_set_pose", {"x": 1.0, "y": 0.0, "z": 0.0}))
        await asyncio.sleep(0.05)
        assert adapter.list_active_goals()

        async def beats_then_die() -> None:
            for _ in range(3):
                adapter.receive_heartbeat()
                await asyncio.sleep(0.04)

        await beats_then_die()
        await asyncio.sleep(0.35)
        assert adapter.goals_cancelled_by_lost_heartbeat >= 1
        result = await exec_task
        assert result.success is False
        cancelled = [h for h in list(adapter._handles.values()) + list(adapter._finished.values()) if h.cancelled]
        assert cancelled
        assert any(h.cancelled_by_lost_heartbeat for h in cancelled)
        await adapter.stop_watchdog()

    asyncio.run(go())


def test_cancel_rejected_node_stays_running(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.reject_next_cancel = True
        adapter.async_steps = 20
        bind_adapter(adapter)
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        flow = make_flow([
            n("start", "start", data={"x": 1, "y": 0, "z": 0}),
            n("go", "fake_set_pose"),
        ], [e("e0", "start", "data", "go", "target")])
        rid, _ = await run_until(ex, store, flow, "running", timeout=2)
        await asyncio.sleep(0.05)
        out = await ex.stop_run(rid)
        assert out.get("cancel") == "rejected"
        st = ex.get(rid)
        assert st.status == "running"
        assert st.node_state.get("go", {}).get("state") == "cancel_rejected"
        await wait_status(ex, rid, "completed", "stopped", "error", timeout=4)
        await hub.stop()

    asyncio.run(go())


def test_stop_uncancellable_blocking_records_not_cancelled(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.force_uncancellable = True
        adapter.block_s = 0.35
        bind_adapter(adapter)  # descriptor must see cancellable=False
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        flow = make_flow([
            n("g", "fake_gripper", open=True),
        ], [])
        # gripper has no required incoming ports — give it a start token
        flow = make_flow([
            n("start", "start", data={"open": True}),
            n("g", "fake_gripper"),
        ], [e("e0", "start", "data", "g", "target")])
        rid, _ = await run_until(ex, store, flow, "running", timeout=2)
        await asyncio.sleep(0.05)
        out = await ex.stop_run(rid)
        assert out["status"] == "stopping"
        st = await wait_status(ex, rid, "stopped", timeout=3)
        assert st.status == "stopped"
        assert "g" in st.not_cancelled or "" in st.not_cancelled or st.not_cancelled
        logs = store.changes_after(rid, 0)
        assert any((c.get("payload") or {}).get("event") == "not cancelled" for c in logs)
        await hub.stop()

    asyncio.run(go())


def test_restart_async_queries_goal_blocking_reads_state(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 12
        bind_adapter(adapter)
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        flow = make_flow([
            n("start", "start", data={"x": 0.5, "y": 0, "z": 0}),
            n("go", "fake_set_pose"),
        ], [e("e0", "start", "data", "go", "target")])
        fid, ver = boot(store, flow)
        rid = await ex.start_full(fid, ver)
        await asyncio.sleep(0.08)
        # abandon mid-flight like a crash
        ctrl = ex.get(rid)
        if ctrl and ctrl.task:
            ctrl.abandoned = True
            ex.runs.pop(rid, None)
        await hub.stop()
        hub2 = SseHub(store)
        hub2.start()
        ex2 = Executor(store, adapter, hub2, str(tmp_path / "data"))
        ex2.ui_lost_s = None
        await ex2.adopt_unfinished()
        st = ex2.get(rid)
        assert st and st.status == "paused"
        assert st.pause["reason"] == "restart"
        recov = st.pause.get("recovery") or {}
        assert recov.get("return_mode") == "goal_progress"
        assert recov.get("goal_status") is not None
        # never auto-retry moves: no retry key
        assert "go" not in st.retry_keys
        await hub2.stop()

        # blocking path
        adapter2 = FakeAdapter()
        adapter2.block_s = 0.4
        bind_adapter(adapter2)
        store3 = Store(tmp_path / "t2.db")
        hub3 = SseHub(store3, interval=0.1)
        hub3.start()
        ex3 = Executor(store3, adapter2, hub3, str(tmp_path / "data2"))
        ex3.ui_lost_s = None
        flow_b = make_flow([
            n("start", "start", data={"open": True}),
            n("g", "fake_gripper"),
        ], [e("e0", "start", "data", "g", "target")])
        fid2, ver2 = boot(store3, flow_b)
        rid2 = await ex3.start_full(fid2, ver2)
        await asyncio.sleep(0.08)
        ctrl = ex3.get(rid2)
        if ctrl:
            ctrl.abandoned = True
            ex3.runs.pop(rid2, None)
        hub4 = SseHub(store3)
        hub4.start()
        ex4 = Executor(store3, adapter2, hub4, str(tmp_path / "data2"))
        await ex4.adopt_unfinished()
        st2 = ex4.get(rid2)
        assert st2 and st2.pause["reason"] == "restart"
        recov2 = st2.pause.get("recovery") or {}
        assert recov2.get("return_mode") == "blocking"
        assert recov2.get("goal_status") is None
        assert recov2.get("read_state") is not None
        await hub3.stop()
        await hub4.stop()

    asyncio.run(go())


def test_action_list_mismatch_blocks_run(tmp_path):
    async def go():
        adapter = FakeAdapter()
        bind_adapter(adapter)
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        flow = make_flow([n("start", "start", data={})], [])
        flow["action_list_version"] = "old-list"
        try:
            await run_until(ex, store, flow, "completed")
            raise AssertionError("mismatch should block")
        except ApiError as err:
            assert err.error == "action_list_mismatch"
        await hub.stop()

    asyncio.run(go())


def test_adapter_lost_pauses(tmp_path):
    async def go():
        from roboact.nodes import SleepNode
        adapter = FakeAdapter()
        bind_adapter(adapter)
        ex, store, _, hub = ctx(tmp_path)
        ex.adapter = adapter
        ex.adapter_heartbeat_s = 0.15
        ex.start_background()
        old_sleep = SleepNode.timeout_s
        SleepNode.timeout_s = 5
        flow = make_flow([
            n("start", "start", data={"v": 1}),
            n("a", "echo"),
            n("s", "sleep", seconds=0.8),
            n("b", "echo"),
        ], [
            e("e0", "start", "data", "a", "value"),
            e("e1", "a", "value", "s", "value"),
            e("e2", "s", "value", "b", "value"),
        ])
        try:
            rid, _ = await run_until(ex, store, flow, "running", timeout=2)
            adapter.connected = False
            st = await wait_status(ex, rid, "paused", timeout=4)
            assert st.pause["reason"] == "adapter_lost"
        finally:
            SleepNode.timeout_s = old_sleep
        await ex.stop_background()
        await hub.stop()

    asyncio.run(go())


def test_loopback_only_bind():
    from roboact.server import require_loopback_host

    require_loopback_host("127.0.0.1")
    try:
        require_loopback_host("0.0.0.0")
        raise AssertionError("non-loopback must exit")
    except SystemExit:
        pass


def test_missing_action_marked(tmp_path):
    async def go():
        adapter = FakeAdapter()
        bind_adapter(adapter)
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        flow = make_flow([
            n("start", "start", data={}),
            n("gone", "no_such_action"),
        ], [e("e0", "start", "data", "gone", "value")])
        try:
            await run_until(ex, store, flow, "completed")
            raise AssertionError
        except ApiError as err:
            assert err.error == "action_missing"
        await hub.stop()

    asyncio.run(go())
