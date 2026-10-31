"""Must-keep: A31 palette reject; 10 s page-lost; 3 s adapter-lost; motion readback."""

from __future__ import annotations

import asyncio

from roboact.adapter import FakeAdapter
from roboact.errors import ApiError
from roboact.executor import Executor
from roboact.nodes import (
    SleepNode,
    bind_adapter,
    get_type,
    ingest_action,
    list_specs,
    rejected_actions,
)
from roboact.sse import SseHub
from roboact.store import Store

from tests.helpers import boot, cont, e, make_flow, n, run_until, wait_status


def adapter_ctx(tmp_path, adapter=None):
    adapter = adapter or FakeAdapter()
    bind_adapter(adapter)
    store = Store(tmp_path / "t.db")
    hub = SseHub(store, interval=0.1)
    hub.start()
    ex = Executor(store, adapter, hub, str(tmp_path / "data"))
    ex.ui_lost_s = None
    return ex, store, adapter, hub


def pose_flow(**go_bp):
    return make_flow(
        [
            n("start", "start", data={"x": 0.4, "y": 0.1, "z": 0.2}),
            n("go", "fake_set_pose", breakpoints=go_bp or {}),
            n("out", "output"),
        ],
        [
            e("e0", "start", "data", "go", "target"),
            e("e1", "go", "pose", "out", "value"),
        ],
    )


def test_a28_motion_timeout_requires_readback(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 80
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        get_type("fake_set_pose").timeout_s = 0.15
        try:
            rid, st = await run_until(ex, store, pose_flow(), "paused", timeout=4)
            assert st.pause["reason"] == "motion_timeout"
            assert st.node_state.get("go", {}).get("state") == "interrupted_unknown"
            assert (st.pause.get("readback") or {}).get("readback_id")
            pid = st.pause["pause_id"]
            try:
                await ex.continue_run(rid, pid)
                raise AssertionError("continue without readback should 409")
            except ApiError as err:
                assert err.error == "readback_required"
            assert len(adapter.calls) == 1
            await cont(ex, rid, resolution="retry")
            st = await wait_status(ex, rid, "completed", "paused", "error", timeout=4)
            assert st.status in ("completed", "paused", "error")
        finally:
            get_type("fake_set_pose").timeout_s = 60
        await hub.stop()

    asyncio.run(go())


def test_a29_restart_mid_motion_no_auto_retry(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 20
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        fid, ver = boot(store, pose_flow())
        rid = await ex.start_full(fid, ver)
        await asyncio.sleep(0.08)
        n0 = len(adapter.calls)
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
        assert st.node_state.get("go", {}).get("state") == "interrupted_unknown"
        recov = st.pause.get("recovery") or {}
        assert recov.get("return_mode") == "goal_progress"
        assert st.pause.get("goal_check") is not None or recov.get("goal_status") is not None
        await asyncio.sleep(0.3)
        assert len(adapter.calls) == n0
        assert "go" not in st.retry_keys
        await hub2.stop()

    asyncio.run(go())


def test_a31_rejected_missing_timeout_and_write_side_effect():
    adapter = FakeAdapter()
    adapter.extra_raw_actions = [
        {
            "id": "broken_timeout",
            "title": "missing timeout",
            "return_mode": "blocking",
            "outputs": [{"name": "ok", "type": "any"}],
            "side_effect": "none",
            "cancel": {"cancellable": True},
        },
        {
            "id": "broken_write",
            "title": "illegal write",
            "return_mode": "blocking",
            "timeout_s": 5,
            "outputs": [{"name": "ok", "type": "any"}],
            "side_effect": "write",
            "cancel": {"cancellable": True},
        },
    ]
    bind_adapter(adapter)
    types = {t["type_id"] for t in list_specs(adapter)}
    assert "broken_timeout" not in types
    assert "broken_write" not in types
    rejected = rejected_actions(adapter)
    by_id = {r["id"]: r for r in rejected}
    assert "broken_timeout" in by_id
    assert "timeout_s" in by_id["broken_timeout"]["missing"]
    assert "missing" in by_id["broken_timeout"]["reason"]
    assert "broken_write" in by_id
    assert "side_effect" in by_id["broken_write"]["missing"]
    assert "write" in by_id["broken_write"]["reason"]
    cls, rej = ingest_action(
        {
            "id": "implicit",
            "return_mode": "blocking",
            "timeout_s": 3,
            "outputs": [{"name": "ok", "type": "any"}],
            "cancel": {"cancellable": True, "after": "hold"},
            "expiry": "controller",
            "read_state": True,
        },
        adapter,
    )
    assert rej is None and cls is not None
    assert cls.side_effect.value == "motion"


def test_a33_operator_lost_pauses_without_cancel(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 4
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.ui_lost_s = 0.15
        old_sleep = SleepNode.timeout_s
        SleepNode.timeout_s = 5
        flow = make_flow(
            [
                n("start", "start", data={"v": 1}),
                n("a", "echo"),
                n("s", "sleep", seconds=0.6),
                n("b", "echo"),
            ],
            [
                e("e0", "start", "data", "a", "value"),
                e("e1", "a", "value", "s", "value"),
                e("e2", "s", "value", "b", "value"),
            ],
        )
        rid, _ = await run_until(ex, store, flow, "running", timeout=2)
        ex.note_ui(rid)
        st = await wait_status(ex, rid, "paused", timeout=4)
        assert st.pause["reason"] == "operator_lost"
        SleepNode.timeout_s = old_sleep
        await hub.stop()

    asyncio.run(go())


def test_a34_adapter_lost_pauses(tmp_path):
    async def go():
        adapter = FakeAdapter()
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.adapter_heartbeat_s = 0.15
        ex.start_background()
        old_sleep = SleepNode.timeout_s
        SleepNode.timeout_s = 5
        flow = make_flow(
            [
                n("start", "start", data={"v": 1}),
                n("a", "echo"),
                n("s", "sleep", seconds=0.8),
                n("b", "echo"),
            ],
            [
                e("e0", "start", "data", "a", "value"),
                e("e1", "a", "value", "s", "value"),
                e("e2", "s", "value", "b", "value"),
            ],
        )
        rid, _ = await run_until(ex, store, flow, "running", timeout=2)
        adapter.connected = False
        st = await wait_status(ex, rid, "paused", timeout=4)
        assert st.pause["reason"] == "adapter_lost"
        SleepNode.timeout_s = old_sleep
        await ex.stop_background()
        await hub.stop()

    asyncio.run(go())
