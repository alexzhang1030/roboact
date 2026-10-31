"""PRD 13 人工确认：等待时无下游记录；驳回只走驳回支路；两页同时点先到的生效。"""
from __future__ import annotations

import asyncio

from roboact.errors import ApiError
from tests.helpers import confirm_flow, ctx, e, make_flow, n, rec_nodes, recs, run_until, wait_status


def test_waiting_confirm_writes_no_downstream(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        rid, st = await run_until(ex, store, confirm_flow(), "paused")
        assert st.status == "paused"
        assert st.pause["reason"] == "approval"
        assert st.paused_node == "hc"
        assert st.node_state["hc"]["state"] == "awaiting_approval"
        names = rec_nodes(store, rid)
        assert "hc" not in names
        assert "yes" not in names and "no" not in names
        assert "out_yes" not in names and "out_no" not in names
        await hub.stop()
    asyncio.run(go())


def test_reject_runs_only_reject_branch(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        rid, st = await run_until(ex, store, confirm_flow(), "paused")
        await ex.approve(rid, st.pause["pause_id"], "reject", comment="不对")
        st = await wait_status(ex, rid, "completed")
        assert st.status == "completed"
        names = rec_nodes(store, rid)
        assert "hc" in names and "no" in names and "out_no" in names
        assert "yes" not in names and "out_yes" not in names
        assert st.node_state["yes"]["state"] == "skipped"
        assert st.node_state["out_yes"]["state"] == "skipped"
        hc = [r for r in recs(store, rid) if r["node_id"] == "hc"][0]
        assert hc["outputs"]["pass"] is None
        assert hc["outputs"]["reject"] is not None
        await hub.stop()
    asyncio.run(go())


def test_continue_and_step_409_while_awaiting(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        rid, st = await run_until(ex, store, confirm_flow(), "paused")
        pid = st.pause["pause_id"]
        try:
            await ex.continue_run(rid, pid)
            raise AssertionError("continue should 409")
        except ApiError as err:
            assert err.status == 409
            assert err.error == "awaiting_approval"
        try:
            await ex.step_run(rid, pid)
            raise AssertionError("step should 409")
        except ApiError as err:
            assert err.status == 409
            assert err.error == "awaiting_approval"
        assert ex.get(rid).status == "paused"
        assert ex.get(rid).pause["reason"] == "approval"
        await hub.stop()
    asyncio.run(go())


def test_reject_marks_other_branch_skipped_immediately(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        rid, st = await run_until(ex, store, confirm_flow(), "paused")
        await ex.approve(rid, st.pause["pause_id"], "reject")
        t0 = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - t0 < 2:
            st = ex.get(rid)
            yes = (st.node_state.get("yes") or {}).get("state")
            out_yes = (st.node_state.get("out_yes") or {}).get("state")
            if yes == "skipped" and out_yes == "skipped":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("unused branch was not skipped immediately")
        assert "yes" not in rec_nodes(store, rid)
        await wait_status(ex, rid, "completed")
        await hub.stop()
    asyncio.run(go())


def test_two_pages_first_approve_wins(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        rid, st = await run_until(ex, store, confirm_flow(), "paused")
        pid = st.pause["pause_id"]
        r1 = await ex.approve(rid, pid, "approve", client_id="page-a")
        assert r1["status"] == "running"
        try:
            await ex.approve(rid, pid, "reject", client_id="page-b")
            raise AssertionError("second approve should fail")
        except ApiError as err:
            assert err.status == 409
            assert err.error == "pause_mismatch"
        await wait_status(ex, rid, "completed")
        names = rec_nodes(store, rid)
        assert "yes" in names and "no" not in names
        await hub.stop()
    asyncio.run(go())


def test_a22_loop_confirm_does_not_skip_later_round(tmp_path):
    """A22: 环里人工确认驳回后，输出乙不能被标跳过。"""
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow(
            [
                n("start", "start", data={"value": 0}),
                n("work", "inc"),
                n("cond", "condition", field="value", op="gte", compare=99),
                n("hc", "human_confirm", prompt="这一轮可以结束吗"),
                n("out_a", "output"),
                n("out_b", "output"),
            ],
            [
                e("e0", "start", "data", "work", "value"),
                e("e1", "work", "value", "cond", "value"),
                e("e2", "cond", "yes", "out_a", "value"),
                e("e3", "cond", "no", "hc", "value"),
                e("e4", "hc", "reject", "work", "value"),
                e("e5", "hc", "pass", "out_b", "value"),
            ],
        )
        rid, st = await run_until(ex, store, flow, "paused")
        assert st.pause["reason"] == "approval"
        await ex.approve(rid, st.pause["pause_id"], "reject")
        st = await wait_status(ex, rid, "paused")
        assert (st.node_state.get("out_b") or {}).get("state") != "skipped"
        await ex.approve(rid, st.pause["pause_id"], "approve")
        st = await wait_status(ex, rid, "completed")
        names = rec_nodes(store, rid)
        assert "out_b" in names
        assert "out_a" not in names
        await hub.stop()
    asyncio.run(go())
