"""Acceptance items whose pass condition is a record or a stub, not a screen."""

from __future__ import annotations

import asyncio
import time

from tests.acceptance_flows import loop_flow
from tests.helpers import ctx, e, make_flow, n, recs, run_until, wait_status, write_flow
from tests.http_api import http_json, put_flow


def test_8_1_write_rerun_stub_gets_no_second_request(tmp_path):
    """Rerun a write node alone: the stub is not asked a second time."""

    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        rid, _st = await run_until(ex, store, write_flow("once"), "completed")
        assert tools.note_lines() == ["once"]
        assert sum(1 for c in tools.calls if c["tool"] == "append_note") == 1
        row = store.get_run(rid)
        assert row is not None
        rid2 = await ex.start_node(row["flow_id"], row["flow_version"], "mail")
        await wait_status(ex, rid2, "completed")
        assert tools.note_lines() == ["once"]
        assert sum(1 for c in tools.calls if c["tool"] == "append_note") == 1
        second = recs(store, rid2)
        assert [s["node_id"] for s in second] == ["mail"]
        assert second[0]["side_effect_mode"] == "mock"
        await hub.stop()

    asyncio.run(go())


def test_8_2_read_replay_uses_cached_value(tmp_path):
    """Replay a read node: the new record is the cached value, and the tool is not called again."""

    async def go():
        ex, store, tools, hub = ctx(tmp_path)
        tools.notes.write_text("cached-line\n", encoding="utf-8")
        flow = make_flow(
            [
                n("start", "start", data={"k": 1}),
                n("rd", "tool_call", tool="read_note", allowed_tools=["read_note"]),
                n("out", "end"),
            ],
            [
                e("e0", "start", "data", "rd", "args"),
                e("e1", "rd", "result", "out", "value"),
            ],
        )
        rid, _st = await run_until(ex, store, flow, "completed")
        first = next(s for s in recs(store, rid) if s["node_id"] == "rd")
        assert first["side_effect_mode"] == "real"
        assert sum(1 for c in tools.calls if c["tool"] == "read_note") == 1
        cached = first["output"]
        row = store.get_run(rid)
        assert row is not None
        rid2 = await ex.start_node(row["flow_id"], row["flow_version"], "rd")
        await wait_status(ex, rid2, "completed")
        second = recs(store, rid2)
        assert [s["node_id"] for s in second] == ["rd"]
        assert second[0]["side_effect_mode"] == "cache"
        assert second[0]["output"] == cached
        assert sum(1 for c in tools.calls if c["tool"] == "read_note") == 1
        await hub.stop()

    asyncio.run(go())


def _wait_run(base: str, rid: str, *want: str, timeout: float = 20.0) -> dict:
    last = None
    t0 = time.time()
    body: dict = {}
    while time.time() - t0 < timeout:
        body = http_json(base, f"/api/runs/{rid}")
        last = (body.get("run") or body).get("status")
        if last in want:
            return body
        time.sleep(0.02)
    raise AssertionError(f"want {want}, last={last}")


def test_18_2_in_loop_param_marks_later_rounds_stale(server):
    """Changing a loop node's own params marks its later rounds' snapshots maybe stale."""
    base = server["base"]
    flow = put_flow(base, loop_flow(3))
    started = http_json(
        base,
        "/api/runs",
        "POST",
        {"flow_id": flow["id"], "flow_version": flow["version"], "max_steps": 40},
    )
    done = _wait_run(base, started["run_id"], "completed")
    cond = [r for r in done["records"] if r["node_id"] == "cond"]
    assert [r["round"] for r in cond] == [1, 2, 3]
    nodes = []
    for node in flow["nodes"]:
        if node["id"] == "cond":
            nodes.append({**node, "params": {**node["params"], "compare": 9}})
        else:
            nodes.append(node)
    http_json(base, f"/api/flows/{flow['id']}", "PUT", {**flow, "nodes": nodes, "bump": True})
    again = http_json(base, f"/api/runs/{started['run_id']}")
    later = [r for r in again["records"] if r["node_id"] == "cond" and r["round"] >= 2]
    assert [r["round"] for r in later] == [2, 3]
    assert all(r["stale"]["is_stale"] for r in later)
    assert all("cond" in (r["stale"].get("changed_nodes") or []) for r in later)
