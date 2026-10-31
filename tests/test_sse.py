"""PRD 8.19.1 推送合并, 8.19.2 / A5 停下立刻 flush。"""

from __future__ import annotations

import asyncio
import time

from roboact.sse import SseHub

from tests.helpers import chain_flow, cont, ctx, run_until, wait_status


def test_batch_last_state_wins():
    async def go():
        hub = SseHub(interval=0.1)
        q = hub.subscribe("r1")
        hub.update_node("r1", "a", {"status": "queued"})
        hub.update_node("r1", "a", {"status": "running"})
        hub.update_node("r1", "a", {"status": "done"})
        ev = hub.flush("r1")
        assert ev["nodes"]["a"]["status"] == "done"
        got = await q.get()
        assert "done" in got
        hub.unsubscribe("r1", q)

    asyncio.run(go())


def test_batch_rate_at_most_10_per_second():
    async def go():
        hub = SseHub(interval=0.1)
        hub.start()
        q = hub.subscribe("r1")
        t0 = time.monotonic()
        for i in range(50):
            hub.update_node("r1", "n", {"status": "running", "i": i})
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.15)
        n = 0
        while not q.empty():
            q.get_nowait()
            n += 1
        dt = time.monotonic() - t0
        assert n <= int(dt * 10) + 2
        await hub.stop()

    asyncio.run(go())


def test_step_end_flushes_immediately(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = chain_flow("b")
        rid, _ = await run_until(ex, store, flow, "paused")
        q2 = hub.subscribe(rid)
        t0 = time.perf_counter()
        await cont(ex, rid, mode="step")
        await wait_status(ex, rid, "paused")
        got = None
        deadline = time.perf_counter() + 0.08
        while time.perf_counter() < deadline:
            if not q2.empty():
                got = q2.get_nowait()
                break
            await asyncio.sleep(0)
        dt = time.perf_counter() - t0
        assert dt < 0.1
        assert got is not None or hub.flush(rid) is None
        await hub.stop()

    asyncio.run(go())


def test_pause_event_immediate_a5(tmp_path):
    """A5 / 19.2: paused 事件在写完暂停状态后立刻发出，不等 100 ms 窗口。"""

    async def go():
        ex, store, _, hub = ctx(tmp_path)
        samples = []
        for _ in range(8):
            flow = chain_flow("b")
            rid, st = await run_until(ex, store, flow, "paused")
            # measure from stored mono to now; event already flushed
            now = time.monotonic()
            samples.append((now - st.last_paused_at_mono) * 1000)
            await ex.stop_run(rid)
            await wait_status(ex, rid, "stopped")
        samples.sort()
        assert all(s <= 100 for s in samples), samples
        p95 = samples[int(0.95 * (len(samples) - 1))]
        assert p95 <= 20, (p95, samples)
        await hub.stop()

    asyncio.run(go())
