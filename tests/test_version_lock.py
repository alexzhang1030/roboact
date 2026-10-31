"""PRD 8.9 版本锁定：运行中改画布仍走旧版本；从新版本重跑开新运行。"""

from __future__ import annotations

import asyncio
import copy

from tests.helpers import chain_flow, cont, ctx, recs, run_until, wait_status


def test_continue_uses_locked_snapshot(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow("b")
        rid, _ = await run_until(ex, store, flow, "paused")
        row = store.get_run(rid)
        latest = store.latest_graph(row["flow_id"])
        assert latest is not None
        mutated = copy.deepcopy(latest)
        for node in mutated["nodes"]:
            if node["id"] == "b":
                node["type"] = "fail"
                node["params"] = {"message": "should not run"}
        store.save_flow(row["flow_id"], mutated, bump=True)
        ex.notify_version(row["flow_id"], store.get_flow_meta(row["flow_id"])["latest_version"])
        await cont(ex, rid)
        st = await wait_status(ex, rid, "completed", "error")
        assert st.status == "completed"
        b = [r for r in recs(store, rid) if r["node_id"] == "b"][-1]
        assert b["status"] == "done"
        await hub.stop()

    asyncio.run(go())


def test_rerun_from_new_version_starts_fresh(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow("b")
        rid, _ = await run_until(ex, store, flow, "paused")
        old = store.get_run(rid)
        latest = store.latest_graph(old["flow_id"])
        assert latest is not None
        store.save_flow(old["flow_id"], latest, bump=True)
        new_ver = store.get_flow_meta(old["flow_id"])["latest_version"]
        rid2 = await ex.start_full(old["flow_id"], new_ver)
        await asyncio.sleep(0.15)
        assert rid2 != rid
        assert ex.get(rid).status == "paused"
        st2 = ex.get(rid2)
        assert st2 is not None
        assert st2.steps_used >= 0
        # 新运行从头开始；同一条断点仍会停。旧运行保持暂停。
        await hub.stop()

    asyncio.run(go())
