"""A38 / PRD 19.4 — precondition_wait_s: wait-then-send, deadline, no-wait."""

from __future__ import annotations

import asyncio

from roboact.adapter import FakeAdapter, Precondition

from tests.helpers import adapter_ctx, e, make_flow, n, recs, run_until


def _stand_adapter(
    *, wait_s: float | None, stable: bool = True, stable_after_s: float | None = None
) -> FakeAdapter:
    adapter = FakeAdapter()
    adapter.precondition_poll_s = 0.05
    adapter.stable = stable
    adapter.stable_after_s = stable_after_s
    update: dict = {
        "preconditions": [Precondition(text="stable stance", check="stable")],
    }
    if wait_s is not None:
        update["precondition_wait_s"] = wait_s
    adapter.action_updates["fake_set_pose"] = update
    return adapter


def _pose_flow():
    return make_flow(
        [
            n("start", "start", data={"x": 0.4, "y": 0.1, "z": 0.2}),
            n("go", "fake_set_pose"),
        ],
        [e("e0", "start", "data", "go", "target")],
    )


def _go_steps(store, rid):
    return [r for r in recs(store, rid) if r["node_id"] == "go"]


def test_a38_wait_then_send_when_stable(tmp_path):
    async def go():
        adapter = _stand_adapter(wait_s=2, stable_after_s=1.0)
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        rid, st = await run_until(ex, store, _pose_flow(), "completed", timeout=8)
        await hub.stop()
        assert st.status == "completed"
        assert len(adapter.goals_sent) == 1
        assert 0.85 <= (adapter.last_precondition_wait_s or 0) <= 1.35
        go_recs = _go_steps(store, rid)
        assert len(go_recs) == 1
        assert go_recs[0].get("phase") == "sent"

    asyncio.run(go())


def test_a38_deadline_precondition_failed_no_goal(tmp_path):
    async def go():
        adapter = _stand_adapter(wait_s=2, stable=False)
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        rid, st = await run_until(ex, store, _pose_flow(), "error", timeout=8)
        await hub.stop()
        assert st.status == "error"
        assert adapter.goals_sent == []
        go_recs = _go_steps(store, rid)
        assert len(go_recs) == 1
        assert go_recs[0]["error"]["type"] == "precondition_failed"
        assert go_recs[0]["error"].get("message") == "precondition_failed"

    asyncio.run(go())


def test_a38_no_wait_fails_on_first_read(tmp_path):
    async def go():
        adapter = _stand_adapter(wait_s=None, stable=False)
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        rid, st = await run_until(ex, store, _pose_flow(), "error", timeout=8)
        await hub.stop()
        assert st.status == "error"
        assert adapter.goals_sent == []
        go_recs = _go_steps(store, rid)
        assert len(go_recs) == 1
        assert go_recs[0]["error"]["type"] == "precondition_failed"

    asyncio.run(go())
