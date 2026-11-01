"""End node: renamed output type, full-run structure, result on End."""

from __future__ import annotations

import asyncio

from roboact.adapter import FakeAdapter
from roboact.errors import ApiError

from tests.helpers import adapter_ctx, boot, ctx, e, make_flow, n, run_until, wait_status
from tests.http_api import http_json, http_status


def _start_end(linked: bool = True, extra_ends: int = 0, drop_end: bool = False):
    nodes = [n("start", "start", data={"v": 1})]
    edges = []
    if not drop_end:
        nodes.append(n("end", "end"))
        if linked:
            edges.append(e("e0", "start", "data", "end", "value"))
    for i in range(extra_ends):
        nid = f"end{i}"
        nodes.append(n(nid, "end"))
        edges.append(e(f"ex{i}", "start", "data", nid, "value"))
    return make_flow(nodes, edges)


def test_full_run_refuses_missing_extra_and_unreachable_end(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        cases = [
            (_start_end(drop_end=True), "need_one_end", "Found 0."),
            (_start_end(extra_ends=1), "need_one_end", "Found 2."),
            (_start_end(linked=False), "end_unreachable", "can't be reached"),
            (
                make_flow([n("end", "end")], []),
                "need_one_start",
                "Found 0.",
            ),
            (
                make_flow(
                    [n("s1", "start", data={}), n("s2", "start", data={}), n("end", "end")],
                    [e("e0", "s1", "data", "end", "value")],
                ),
                "need_one_start",
                "Found 2.",
            ),
        ]
        try:
            for flow, code, needle in cases:
                fid, ver = boot(store, flow)
                try:
                    await ex.start_full(fid, ver)
                    raise AssertionError(f"{code} should refuse")
                except ApiError as err:
                    assert err.status == 409
                    assert err.error == code
                    assert needle in err.message
            fid, ver = boot(store, _start_end())
            rid = await ex.start_full(fid, ver)
            st = await wait_status(ex, rid, "completed", timeout=4)
            assert st.result == {"v": 1}
        finally:
            await hub.stop()

    asyncio.run(go())


def test_group_run_does_not_require_end(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow([n("a", "echo"), n("b", "echo")], [e("e0", "a", "value", "b", "value")])
        fid, ver = boot(store, flow)
        try:
            rid, _ = await ex.start_group(fid, ver, ["a", "b"])
            st = await wait_status(ex, rid, "completed", "error", timeout=4)
            assert st.status == "completed"
        finally:
            await hub.stop()

    asyncio.run(go())


def test_put_type_output_is_rejected(server):
    base = server["base"]
    boot = http_json(base, "/api/bootstrap")
    fid = boot["flow"]["id"]
    before = http_json(base, f"/api/flows/{fid}")["flow"]
    code, body = http_status(
        base,
        f"/api/flows/{fid}",
        "PUT",
        {
            "name": before["name"],
            "nodes": [
                {"id": "start", "type": "start", "x": 0, "y": 0, "params": {"data": {}}},
                {"id": "out", "type": "output", "x": 80, "y": 0, "params": {}},
            ],
            "edges": [
                {
                    "id": "e0",
                    "source": "start",
                    "source_port": "data",
                    "target": "out",
                    "target_port": "value",
                    "kind": "data",
                }
            ],
            "bump": True,
        },
    )
    assert code == 422, body
    assert body["message"] == 'nodes[1] (id out): type "output" is now "end"'
    after = http_json(base, f"/api/flows/{fid}")["flow"]
    assert after["version"] == before["version"]
    assert [n["id"] for n in after["nodes"]] == [n["id"] for n in before["nodes"]]


def test_new_flow_has_linked_start_and_end(server):
    base = server["base"]
    created = http_json(base, "/api/flows", "POST", {"name": "fresh"})
    flow = created["flow"]
    types = {n["id"]: n["type"] for n in flow["nodes"]}
    assert types == {"start": "start", "end": "end"}
    assert any(
        e["source"] == "start" and e["target"] == "end" and e["target_port"] == "value"
        for e in flow["edges"]
    )
    check = http_json(base, f"/api/flows/{flow['id']}/run-check")
    assert check["can_run"] is True
    assert check["structure"] is None


def test_run_check_names_missing_end(server):
    base = server["base"]
    boot = http_json(base, "/api/bootstrap")
    fid = boot["flow"]["id"]
    graph = http_json(base, f"/api/flows/{fid}")["flow"]
    graph["nodes"] = [n for n in graph["nodes"] if n["type"] != "end"]
    graph["edges"] = [e for e in graph["edges"] if e["target"] != "end" and e["source"] != "end"]
    graph["bump"] = True
    saved = http_json(base, f"/api/flows/{fid}", "PUT", graph)
    check = http_json(base, f"/api/flows/{saved['flow']['id']}/run-check")
    assert check["can_run"] is False
    assert check["structure"]["code"] == "need_one_end"
    assert check["structure"]["n"] == 0
    assert check["structure"]["node_ids"] == []
    assert "Found 0." in check["structure"]["message"]
    code, body = http_status(
        base,
        "/api/runs",
        "POST",
        {"flow_id": fid, "flow_version": saved["flow"]["version"]},
    )
    assert code == 409
    assert body["error"] == "need_one_end"
    assert "Found 0." in body["message"]


def test_selected_node_run_skips_end_check(tmp_path):
    async def go():
        ex, store, _, hub = ctx(tmp_path)
        flow = make_flow([n("c", "constant", value="1", value_type="string")], [])
        fid, ver = boot(store, flow)
        try:
            rid = await ex.start_node(fid, ver, "c")
            st = await wait_status(ex, rid, "completed", "error", timeout=4)
            assert st.status == "completed"
        finally:
            await hub.stop()

    asyncio.run(go())


def test_parallel_motion_keeps_running_after_end(tmp_path):
    """End stores its input, but a motion branch still running keeps the run open."""

    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 8
        adapter.async_sleep_s = 0.05
        ex, store, _, hub = adapter_ctx(tmp_path, adapter)
        flow = make_flow(
            [
                n("start", "start", data={"v": 7}),
                n("end", "end"),
                n("go", "fake_set_pose"),
            ],
            [
                e("e0", "start", "data", "end", "value"),
                e("e1", "start", "data", "go", "target"),
            ],
        )
        fid, ver = boot(store, flow)
        try:
            rid = await ex.start_full(fid, ver)
            saw_running = False
            for _ in range(400):
                st = ex.get(rid)
                assert st is not None
                end_done = (st.node_state.get("end") or {}).get("state") == "done"
                go_done = (st.node_state.get("go") or {}).get("state") == "done"
                if end_done and not go_done:
                    assert st.status == "running"
                    assert st.result == {"v": 7}
                    saw_running = True
                    break
                await asyncio.sleep(0.01)
            assert saw_running, "End finished while the motion branch was still running"
            st = await wait_status(ex, rid, "completed", timeout=4)
            assert st.result == {"v": 7}
            assert (st.node_state.get("go") or {}).get("state") == "done"
        finally:
            await hub.stop()

    asyncio.run(go())


def test_demo_chain_result_is_end_input(tmp_path):
    async def go():
        ex, store, _, hub = adapter_ctx(tmp_path)
        flow = make_flow(
            [
                n("start", "start", data={"x": 0.4, "y": 0.1, "z": 0.2}),
                n("go", "fake_set_pose"),
                n("grip", "fake_gripper", open=True),
                n("end", "end"),
            ],
            [
                e("e0", "start", "data", "go", "target"),
                e("e1", "go", "pose", "grip", "target"),
                e("e2", "grip", "open", "end", "value"),
            ],
        )
        try:
            _, st = await run_until(ex, store, flow, "completed", timeout=6)
            assert st.result is True
        finally:
            await hub.stop()

    asyncio.run(go())
