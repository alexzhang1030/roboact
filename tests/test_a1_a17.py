"""Must-keep: A1 write crash does not double; timeout_s (A16)."""

from __future__ import annotations

import asyncio

from roboact.executor import Executor
from roboact.sse import SseHub

from tests.helpers import (
    boot,
    cont,
    ctx,
    make_flow,
    n,
    recs,
    run_until,
    wait_status,
    write_flow,
)


def test_a1_write_crash_does_not_double(tmp_path):
    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        flow = write_flow("once")
        fid, ver = boot(store, flow)
        ex.hooks["crash_before_t2"] = True
        rid = await ex.start_full(fid, ver)
        for _ in range(200):
            if tools.note_lines() == ["once"] and ex.get(rid) is None:
                break
            await asyncio.sleep(0.01)
        assert tools.note_lines() == ["once"]
        assert ex.get(rid) is None
        hub2 = SseHub(store)
        hub2.start()
        ex2 = Executor(store, tools, hub2, str(tmp_path / "data"))
        await ex2.adopt_unfinished()
        st = ex2.get(rid)
        assert st and st.status == "paused"
        assert st.node_state.get("mail", {}).get("state") == "interrupted_unknown"
        await cont(ex2, rid)
        await wait_status(ex2, rid, "completed")
        assert tools.note_lines() == ["once"]
        assert tools.ledger.count() == 1
        await hub.stop()
        await hub2.stop()

    asyncio.run(go())


def test_a16_timeout(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow([n("s", "sleep", seconds=2.0)], [])
        rid, st = await run_until(ex, store, flow, "error", timeout=3)
        assert st.status == "error"
        rec = recs(store, rid)[0]
        assert rec["error"]["type"] == "timeout"
        await hub.stop()

    asyncio.run(go())
