"""PRD 8.14 环正常退出, 8.15 步数上限, 8.17 环里单跑, 8.18 没有出口的环。"""

from __future__ import annotations

import asyncio

from roboact.errors import ApiError

from tests.helpers import ctx, e, loop_flow, make_flow, n, recs, run_until


def test_loop_exits_at_round_3(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = loop_flow(compare=3, op="gte")
        rid, st = await run_until(ex, store, flow, "completed")
        assert st.status == "completed"
        inc = [r for r in recs(store, rid) if r["node_id"] == "inc"]
        cond = [r for r in recs(store, rid) if r["node_id"] == "cond"]
        assert [r["round"] for r in inc] == [1, 2, 3]
        assert [r["round"] for r in cond] == [1, 2, 3]
        await hub.stop()

    asyncio.run(go())


def test_step_limit_is_own_state(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = loop_flow(compare=-1, op="eq")
        rid, st = await run_until(ex, store, flow, "step_limit", step_limit=10)
        assert st.status == "step_limit"
        allr = recs(store, rid)
        assert len(allr) == 10
        assert max(r["step"] for r in allr) == 10
        assert not any(r["step"] == 11 for r in allr)
        await hub.stop()

    asyncio.run(go())


def test_single_run_loop_node_uses_round_2(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = loop_flow()
        rid1, _ = await run_until(ex, store, flow, "completed")
        r2 = next(r for r in recs(store, rid1) if r["node_id"] == "inc" and r["round"] == 2)
        fid = store.get_run(rid1)["flow_id"]
        ver = store.get_run(rid1)["flow_version"]
        rid2 = await ex.start_node(
            fid, ver, "inc", input_spec={"kind": "record", "run_id": rid1, "step": r2["step"]}
        )
        from tests.helpers import wait_status

        await wait_status(ex, rid2, "completed")
        rec = recs(store, rid2)
        assert len(rec) == 1
        assert rec[0]["node_id"] == "inc"
        assert rec[0]["inputs"] == r2["inputs"]
        await hub.stop()

    asyncio.run(go())


def test_cycle_without_condition_rejected(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow(
            [n("start", "start", data={}), n("a", "echo"), n("b", "echo")],
            [
                e("e0", "start", "data", "a", "value"),
                e("e1", "a", "value", "b", "value"),
                e("e2", "b", "value", "a", "value"),
            ],
        )
        try:
            await run_until(ex, store, flow, "completed")
            raise AssertionError("should reject")
        except ApiError as err:
            assert err.error == "loop_without_exit"
            assert "条件" in err.message
        await hub.stop()

    asyncio.run(go())
