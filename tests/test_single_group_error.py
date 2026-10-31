"""PRD 8.5 单节点, 8.6 分组, 8.7 出错重跑。"""

from __future__ import annotations

import asyncio

from tests.helpers import chain_flow, ctx, e, make_flow, n, rec_nodes, recs, run_until, wait_status


def test_single_node_only_one_record(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow()
        rid1, _ = await run_until(ex, store, flow, "completed")
        b1 = [r for r in recs(store, rid1) if r["node_id"] == "b"][-1]
        fid = store.get_run(rid1)["flow_id"]
        ver = store.get_run(rid1)["flow_version"]
        rid2 = await ex.start_node(
            fid, ver, "b", input_spec={"kind": "record", "run_id": rid1, "step": b1["step"]}
        )
        await wait_status(ex, rid2, "completed")
        names = rec_nodes(store, rid2)
        assert names == ["b"]
        b2 = recs(store, rid2)[0]
        assert b2["inputs"] == b1["inputs"]
        await hub.stop()

    asyncio.run(go())


def test_group_two_middle_nodes(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow()
        rid1, _ = await run_until(ex, store, flow, "completed")
        fid = store.get_run(rid1)["flow_id"]
        ver = store.get_run(rid1)["flow_version"]
        rid2, _ = await ex.start_group(fid, ver, ["a", "b"], source_run_id=rid1)
        await wait_status(ex, rid2, "completed")
        names = rec_nodes(store, rid2)
        assert set(names) == {"a", "b"}
        assert "start" not in names and "c" not in names and "out" not in names
        a2 = [r for r in recs(store, rid2) if r["node_id"] == "a"][-1]
        assert a2["input_source"] in ("cache", "pinned", "manual", "upstream")
        await hub.stop()

    asyncio.run(go())


def test_error_rerun_skips_upstream(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow(
            [
                n("start", "start", data={"value": 1}),
                n("ok", "echo"),
                n("bad", "fail", message="工具失败（桩）"),
                n("out", "output"),
            ],
            [
                e("e0", "start", "data", "ok", "value"),
                e("e1", "ok", "value", "bad", "value"),
                e("e2", "bad", "value", "out", "value"),
            ],
        )
        rid1, st = await run_until(ex, store, flow, "error")
        assert st.status == "error"
        assert rec_nodes(store, rid1)[-1] == "bad"
        bad = [r for r in recs(store, rid1) if r["node_id"] == "bad"][-1]
        rid2 = await ex.rerun_from(rid1, bad["step"])
        await wait_status(ex, rid2, "error", "completed")
        names = rec_nodes(store, rid2)
        assert "start" not in names and "ok" not in names
        assert names[0] == "bad"
        await hub.stop()

    asyncio.run(go())
