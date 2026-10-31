"""exec-model v0.2 A23–A37 (PRD §8 mapping in each name). R1–R8 are real-robot N/A in v1."""
from __future__ import annotations

import asyncio
import time
import uuid

from roboact.adapter import FakeAdapter
from roboact.errors import ApiError
from roboact.executor import Executor
from roboact.nodes import bind_adapter, ingest_action, list_specs, rejected_actions
from roboact.sse import SseHub
from roboact.store import Store, open_store
from tests.helpers import boot, cont, e, make_flow, n, recs, run_until, wait_status


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


def test_a23_heartbeat_cancels_goal_when_backend_dies():
    async def go():
        adapter = FakeAdapter()
        adapter.heartbeat_timeout_s = 0.2
        adapter.async_steps = 40
        adapter.start_watchdog()
        adapter.receive_heartbeat()
        exec_task = asyncio.create_task(adapter.execute("fake_set_pose", {"x": 1.0, "y": 0.0, "z": 0.0}))
        await asyncio.sleep(0.05)
        assert adapter.list_active_goals()
        await asyncio.sleep(0.35)
        assert adapter.goals_cancelled_by_lost_heartbeat >= 1
        result = await exec_task
        assert result.success is False
        await adapter.stop_watchdog()

    asyncio.run(go())


def test_a24_controller_expiry_stops_goal():
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 80
        result = await adapter.execute(
            "fake_set_pose", {"x": 1.0, "y": 0.0, "z": 0.0}, deadline=time.time() + 0.12,
        )
        assert result.success is False
        assert "超时" in (result.message or "")

    asyncio.run(go())


def test_a25_cancellable_actions_accept_cancel():
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 30
        task = asyncio.create_task(adapter.execute("fake_set_pose", {"x": 1.0, "y": 0.0, "z": 0.0}))
        await asyncio.sleep(0.05)
        handles = [h for h in adapter._handles.values() if not h.done]
        assert handles
        res = await adapter.cancel(handles[0])
        assert res.accepted is True
        result = await task
        assert result.success is False

    asyncio.run(go())


def test_a26_stop_calls_cancel_no_new_goal(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 20
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
        t0 = time.time()
        while time.time() - t0 < 2:
            ctrl = ex.get(rid)
            if adapter.calls and ctrl and ctrl.current_nid == "go" and ctrl.current_inst is not None:
                break
            await asyncio.sleep(0.01)
        n0 = len(adapter.calls)
        out = await ex.stop_run(rid)
        assert out.get("status") in ("stopping", "stopped", "running")
        await wait_status(ex, rid, "stopped", "completed", timeout=4)
        assert len(adapter.calls) == n0
        assert ex.get(rid).cancel_called.get("go") is True
        await hub.stop()

    asyncio.run(go())


def test_a27_cancel_rejected_stays_running(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.reject_next_cancel = True
        adapter.async_steps = 20
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
        await asyncio.sleep(0.05)
        out = await ex.stop_run(rid)
        assert out.get("cancel") == "rejected"
        st = ex.get(rid)
        assert st.status == "running"
        assert st.node_state.get("go", {}).get("state") == "cancel_rejected"
        await wait_status(ex, rid, "completed", "stopped", "error", timeout=4)
        await hub.stop()

    asyncio.run(go())


def test_a28_motion_timeout_requires_readback(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 80
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        from roboact.nodes import get_type
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


def test_a30_missing_action_blocks_run(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.drop_action_ids.add("fake_set_pose")
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        try:
            await run_until(ex, store, pose_flow(), "completed")
            raise AssertionError
        except ApiError as err:
            assert err.error == "action_missing"
        assert adapter.calls == []
        await hub.stop()

    asyncio.run(go())


def test_a31_rejected_missing_timeout_and_write_side_effect():
    adapter = FakeAdapter()
    adapter.extra_raw_actions = [
        {
            "id": "broken_timeout",
            "title": "缺超时",
            "return_mode": "blocking",
            "outputs": [{"name": "ok", "type": "any"}],
            "side_effect": "none",
            "cancel": {"cancellable": True},
        },
        {
            "id": "broken_write",
            "title": "非法写",
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
    assert "缺" in by_id["broken_timeout"]["reason"]
    assert "broken_write" in by_id
    assert "side_effect" in by_id["broken_write"]["missing"]
    assert "write" in by_id["broken_write"]["reason"]
    cls, rej = ingest_action({"id": "implicit", "return_mode": "blocking", "timeout_s": 3,
                              "outputs": [{"name": "ok", "type": "any"}],
                              "cancel": {"cancellable": True, "after": "hold"},
                              "expiry": "controller", "read_state": True}, adapter)
    assert rej is None and cls is not None
    assert cls.side_effect.value == "motion"


def test_a32_goal_progress_waits_for_result(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 6
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        flow = pose_flow(after={"enabled": True})
        rid, st = await run_until(ex, store, flow, "paused", timeout=4)
        assert st.pause["reason"] == "breakpoint"
        assert st.pause["when"] == "after"
        go = [r for r in recs(store, rid) if r["node_id"] == "go"]
        assert go and go[0]["status"] == "done"
        assert "out" not in {r["node_id"] for r in recs(store, rid)}
        await hub.stop()

    asyncio.run(go())


def test_a33_operator_lost_pauses_without_cancel(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 4
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.ui_lost_s = 0.15
        from roboact.nodes import SleepNode
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
        # drop subscribers: note_ui then wait with no SSE
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
        from roboact.nodes import SleepNode
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


def test_a35_motion_output_readonly(tmp_path):
    async def go():
        adapter = FakeAdapter()
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        flow = pose_flow(after={"enabled": True})
        rid, st = await run_until(ex, store, flow, "paused", timeout=4)
        assert st.pause["when"] == "after"
        try:
            await cont(ex, rid, edits={"outputs": {"pose": {"x": 9, "y": 9, "z": 9}}})
            raise AssertionError
        except ApiError as err:
            assert err.error == "motion_output_readonly"
        await hub.stop()

    asyncio.run(go())


def test_a36_images_stay_out_of_sqlite(tmp_path):
    async def go():
        adapter = FakeAdapter()
        bind_adapter(adapter)
        db = tmp_path / "runs.db"
        store = open_store(db)
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        flow = make_flow(
            [n("start", "start", data={}), n("cam", "fake_camera"), n("out", "output")],
            [e("e0", "start", "data", "cam", "value"), e("e1", "cam", "frame", "out", "value")],
        )
        for _ in range(10):
            rid, _ = await run_until(ex, store, flow, "completed", timeout=3)
            rec = [r for r in recs(store, rid) if r["node_id"] == "cam"][0]
            frame = (rec.get("output") or {}).get("frame") or {}
            assert frame.get("omitted") is True or "data" not in frame
            assert frame.get("width") and frame.get("height") and frame.get("captured_at")
            img = ex.image_at(rid, rec["step"], "frame")
            assert img and img.get("data")
        raw = db.read_bytes()
        assert b"\x89PNG" not in raw
        assert b"data:image/png;base64" not in raw
        # restart process view: images gone
        ex.images.clear()
        last = store.list_runs()[0]["run_id"]
        cam = [r for r in recs(store, last) if r["node_id"] == "cam"][0]
        assert ex.image_at(last, cam["step"], "frame") is None
        await hub.stop()

    asyncio.run(go())


def test_a37_non_loopback_requires_token(tmp_path):
    from roboact.server import App, require_bind_token

    async def go():
        tok = uuid.uuid4().hex
        app = App(str(tmp_path / "auth.db"), token=tok, require_token=True)

        class W:
            def __init__(self) -> None:
                self.buf = b""

            def write(self, b: bytes) -> None:
                self.buf += b

            async def drain(self) -> None:
                return

            def close(self) -> None:
                return

            async def wait_closed(self) -> None:
                return

        w = W()
        await app.handle("GET", "/api/bootstrap", {}, b"", w)
        assert b"401" in w.buf
        w2 = W()
        await app.handle("GET", "/api/bootstrap", {"x-roboact-token": tok}, b"", w2)
        assert b"200" in w2.buf
        try:
            require_bind_token("0.0.0.0", None)
            raise AssertionError("should require token")
        except SystemExit:
            pass
        require_bind_token("127.0.0.1", None)

    asyncio.run(go())
