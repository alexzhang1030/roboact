"""exec-model v0.2.1 A38 — wait then send; deadline fail; kill mid-wait restarts as pending."""
from __future__ import annotations

import asyncio

from roboact.executor import Executor
from roboact.nodes import bind_adapter
from roboact.sse import SseHub
from roboact.store import Store
from tests.helpers import boot, run_until, wait_status
from tests.test_prd194_precondition_wait import _go_steps, _pose_flow, _stand_adapter


def test_a38_wait_then_send_when_stable(tmp_path):
    async def go():
        adapter = _stand_adapter(wait_s=2, stable_after_s=1.0)
        bind_adapter(adapter)
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
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
        bind_adapter(adapter)
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        rid, st = await run_until(ex, store, _pose_flow(), "error", timeout=8)
        await hub.stop()
        assert st.status == "error"
        assert adapter.goals_sent == []
        go_recs = _go_steps(store, rid)
        assert len(go_recs) == 1
        assert go_recs[0]["error"]["type"] == "precondition_failed"
        assert "前提条件没满足" in (go_recs[0]["error"].get("message") or "")

    asyncio.run(go())


def test_a38_kill_mid_wait_restarts_pending_no_readback(tmp_path):
    async def go():
        adapter = _stand_adapter(wait_s=8, stable=False)
        bind_adapter(adapter)
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        fid, ver = boot(store, _pose_flow())
        rid = await ex.start_full(fid, ver)
        phase = None
        for _ in range(80):
            rows = [s for s in store.list_steps(rid, limit=100) if s["node_id"] == "go"]
            if rows and rows[0].get("phase") == "waiting_precondition":
                phase = "waiting_precondition"
                break
            await asyncio.sleep(0.05)
        assert phase == "waiting_precondition"
        assert adapter.goals_sent == []
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
        go_state = (st.node_state.get("go") or {}).get("state")
        assert go_state != "interrupted_unknown"
        assert go_state in ("queued", "idle", "paused_before")
        assert not (st.pause or {}).get("readback")
        go_recs = [s for s in store.list_steps(rid, limit=100) if s["node_id"] == "go"]
        assert all(s.get("status") != "interrupted_unknown" for s in go_recs)
        await hub2.stop()

    asyncio.run(go())


def test_a38_stop_during_wait_is_stopped_no_readback(tmp_path):
    async def go():
        adapter = _stand_adapter(wait_s=8, stable=False)
        bind_adapter(adapter)
        store = Store(tmp_path / "t.db")
        hub = SseHub(store, interval=0.1)
        hub.start()
        ex = Executor(store, adapter, hub, str(tmp_path / "data"))
        ex.ui_lost_s = None
        fid, ver = boot(store, _pose_flow())
        rid = await ex.start_full(fid, ver)
        for _ in range(80):
            rows = [s for s in store.list_steps(rid, limit=100) if s["node_id"] == "go"]
            if rows and rows[0].get("phase") == "waiting_precondition":
                break
            await asyncio.sleep(0.05)
        out = await ex.stop_run(rid)
        assert out["status"] in ("stopping", "stopped")
        st = await wait_status(ex, rid, "stopped", timeout=4)
        assert st.status == "stopped"
        assert adapter.goals_sent == []
        go_recs = _go_steps(store, rid)
        assert len(go_recs) == 1
        assert go_recs[0]["status"] == "stopped"
        assert (st.node_state.get("go") or {}).get("state") == "stopped"
        assert not (st.pause or {}).get("readback")
        await hub.stop()

    asyncio.run(go())
