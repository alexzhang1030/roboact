"""PRD 14 / acceptance r1–r6: in-flight page-loss cancel, timeout cap, refuse."""

from __future__ import annotations

import asyncio

import pytest
from roboact.adapter import CancelSpec, FakeAdapter
from roboact.errors import ApiError
from roboact.flow_file import inspect_run
from roboact.nodes import bind_adapter

from tests.helpers import adapter_ctx, recs, run_until, wait_status
from tests.test_a23_a37 import pose_flow


def _pose_uncancellable(timeout_s: float) -> FakeAdapter:
    adapter = FakeAdapter()
    adapter.action_updates["fake_set_pose"] = {
        "timeout_s": timeout_s,
        "cancel": CancelSpec(cancellable=False, post_cancel="hold"),
    }
    bind_adapter(adapter)
    return adapter


def test_r16_in_flight_cancel_then_operator_lost(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 1
        adapter.async_sleep_s = 2.0
        bind_adapter(adapter)
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.ui_lost_s = 0.2
        rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
        ex.note_ui(rid)
        t0 = asyncio.get_event_loop().time()
        st = await wait_status(ex, rid, "paused", timeout=4)
        dt = asyncio.get_event_loop().time() - t0
        assert st.pause["reason"] == "operator_lost"
        assert adapter.cancel_calls, "cancel must go through the adapter"
        assert (st.pause.get("readback") or {}).get("state"), st.pause
        rec = [r for r in recs(store, rid) if r["node_id"] == "go"]
        assert rec, recs(store, rid)
        assert rec[-1]["status"] in ("stopped", "cancelled")
        assert dt < 1.0, dt
        await hub.stop()

    asyncio.run(go())


def test_r16_noncancellable_runs_to_end_then_pauses(tmp_path):
    async def go():
        adapter = _pose_uncancellable(5)
        adapter.async_steps = 1
        adapter.async_sleep_s = 0.55
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.ui_lost_s = 0.15
        rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
        ex.note_ui(rid)
        st = await wait_status(ex, rid, "paused", timeout=4)
        assert st.pause["reason"] == "operator_lost"
        assert adapter.cancel_calls == []
        go_recs = [r for r in recs(store, rid) if r["node_id"] == "go"]
        assert go_recs and go_recs[-1]["status"] == "done", go_recs
        await hub.stop()

    asyncio.run(go())


def test_r16_timeout_cap_blocks_run(tmp_path):
    adapter = _pose_uncancellable(11)
    blockers = inspect_run(pose_flow(), adapter)
    assert blockers.timeout_errors
    assert blockers.blocks_run(True)
    assert (
        "Go to pose" in blockers.timeout_errors[0]["node"]
        or blockers.timeout_errors[0]["node_id"] == "go"
    )

    async def go():
        ex, _store, _adapter, hub = adapter_ctx(tmp_path, adapter)
        with pytest.raises(ApiError) as ei:
            await run_until(ex, _store, pose_flow(), "running", timeout=2)
        assert ei.value.error == "motion_timeout_cap"
        assert "Go to pose" in ei.value.message or "go" in ei.value.message
        await hub.stop()

    asyncio.run(go())


def test_r16_cancel_rejected_runs_to_end_then_pauses(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 1
        adapter.async_sleep_s = 0.7
        adapter.reject_next_cancel = True
        bind_adapter(adapter)
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.ui_lost_s = 0.15
        rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
        ex.note_ui(rid)
        saw_rej = False
        for _ in range(80):
            st = ex.get(rid)
            node = (st.node_state if st else {}).get("go") or {}
            if (node.get("state") or node.get("status")) == "cancel_rejected":
                saw_rej = True
                break
            await asyncio.sleep(0.02)
        assert saw_rej, "node must show status.cancel_rejected"
        st = await wait_status(ex, rid, "paused", timeout=4)
        assert st.pause["reason"] == "operator_lost"
        assert adapter.cancel_calls
        go_node = st.node_state.get("go") or {}
        assert go_node.get("state") == "done", go_node
        assert go_node.get("duration_ms") is not None
        go_recs = [r for r in recs(store, rid) if r["node_id"] == "go"]
        assert go_recs and go_recs[-1]["status"] == "done", go_recs
        await hub.stop()

    asyncio.run(go())
