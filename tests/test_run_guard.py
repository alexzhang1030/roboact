"""Refuse a new run while a node is executing. Paused, approval, and finished stay open."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from roboact.adapter import FakeAdapter
from roboact.errors import ApiError
from roboact.nodes import get_type

from tests.helpers import adapter_ctx, boot, e, make_flow, n, wait_status
from tests.http_api import http_json, http_status, put_flow

BUSY = "The flow is running. Pause it, stop it, or wait for it to finish."


def guard_graph() -> dict:
    go = n("go", "fake_set_pose", x=280, y=80)
    go["params"] = {"x": 0.2, "y": 0.1, "z": 0.2}
    return make_flow(
        [
            n("start", "start", data={"x": 0.2, "y": 0.1, "z": 0.2}),
            go,
            n("out", "end", x=560, y=80),
            n("echo", "echo", x=280, y=260),
            n(
                "tool",
                "tool_call",
                x=560,
                y=260,
                tool="clock",
                allowed_tools=["clock"],
            ),
        ],
        [
            e("e0", "start", "data", "go", "target"),
            e("e1", "go", "pose", "out", "value"),
        ],
    )


def confirm_flow() -> dict:
    return make_flow(
        [
            n("start", "start", data={"v": 1}),
            n("ask", "human_confirm", x=280),
            n("echo", "echo", x=280, y=200),
            n("out", "end", x=560),
        ],
        [
            e("e0", "start", "data", "ask", "value"),
            e("e1", "ask", "pass", "out", "value"),
        ],
    )


def _ids(base: str, fid: str) -> list[str]:
    rows = http_json(base, f"/api/runs?flow_id={fid}")["runs"]
    return [r["run_id"] for r in rows]


def _confirm(base: str, typ: str) -> str:
    boot = http_json(base, "/api/bootstrap")
    for spec in boot["node_types"]:
        if spec.get("type") == typ or spec.get("type_id") == typ:
            return spec.get("confirm_text") or ""
    raise AssertionError(typ)


def _pose(base: str) -> dict:
    state = http_json(base, "/api/robot/state?action=fake_set_pose")["state"]
    return dict(state["pose"])


def _wait(base: str, rid: str, wanted: set[str], timeout: float = 8.0) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        snap = http_json(base, f"/api/runs/{rid}")
        run = snap.get("run") or snap
        last = run.get("status")
        if last in wanted:
            return snap
        time.sleep(0.05)
    raise TimeoutError(f"want {wanted}, last={last}")


def _settle(base: str, rid: str) -> dict:
    return _wait(base, rid, {"paused", "completed", "stopped", "error", "step_limit"})


def _assert_busy(base: str, fid: str, before: list[str], path: str, body: dict) -> None:
    code, payload = http_status(base, path, "POST", body)
    assert code == 409, payload
    assert payload["error"] == "run_in_progress"
    assert payload["message"] == BUSY
    assert "run_id" not in payload
    blocker = payload["detail"]["run_id"]
    assert blocker in before
    assert _ids(base, fid) == before


def _hold_running(base: str) -> tuple[str, int, str]:
    http_json(base, "/api/test/adapter", "POST", {"async_steps": 80, "async_sleep_s": 0.1})
    flow = put_flow(base, {**guard_graph(), "bump": True})
    fid, ver = flow["id"], flow["version"]
    started = http_json(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": ver})
    rid = started["run_id"]
    _wait(base, rid, {"running"})
    return fid, ver, rid


def test_running_refuses_every_new_run(server):
    base = server["base"]
    fid, ver, rid = _hold_running(base)
    pose = _pose(base)
    ids = _ids(base, fid)
    confirm = _confirm(base, "fake_set_pose")
    _assert_busy(base, fid, ids, "/api/runs", {"flow_id": fid, "flow_version": ver})
    _assert_busy(
        base,
        fid,
        ids,
        "/api/single-runs/node",
        {"flow_id": fid, "flow_version": ver, "node_id": "echo"},
    )
    _assert_busy(
        base,
        fid,
        ids,
        "/api/single-runs/node",
        {
            "flow_id": fid,
            "flow_version": ver,
            "node_id": "go",
            "side_effects": "real",
            "confirm": confirm,
        },
    )
    _assert_busy(
        base,
        fid,
        ids,
        "/api/single-runs/group",
        {"flow_id": fid, "flow_version": ver, "node_ids": ["echo", "tool"]},
    )
    _assert_busy(base, fid, ids, f"/api/runs/{rid}/rerun-from", {"step": 1})
    assert _ids(base, fid) == ids
    assert _pose(base) == pose
    snap = http_json(base, f"/api/runs/{rid}")
    assert (snap.get("run") or snap)["status"] == "running"


def test_group_run_refused_while_running_allowed_when_paused_or_finished(server):
    """Group start writes no run while a node is executing, and works once paused or finished."""
    base = server["base"]
    fid, ver, rid = _hold_running(base)
    before = _ids(base, fid)
    pose = _pose(base)
    _assert_busy(
        base,
        fid,
        before,
        "/api/single-runs/group",
        {"flow_id": fid, "flow_version": ver, "node_ids": ["echo", "tool"]},
    )
    assert _pose(base) == pose
    assert _ids(base, fid) == before

    http_json(base, f"/api/runs/{rid}/stop", "POST", {})
    _settle(base, rid)
    http_json(
        base,
        f"/api/flows/{fid}/breakpoints/go/before",
        "PUT",
        {"enabled": True, "mode": "every"},
    )
    http_json(base, "/api/test/adapter", "POST", {"async_steps": 1, "async_sleep_s": 0.01})
    paused = http_json(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": ver})
    snap = _wait(base, paused["run_id"], {"paused"})
    assert (snap.get("run") or snap)["pause"]["reason"] == "breakpoint"
    code, payload = http_status(
        base,
        "/api/single-runs/group",
        "POST",
        {"flow_id": fid, "flow_version": ver, "node_ids": ["echo"]},
    )
    assert code == 201, payload
    _settle(base, payload["run_id"])

    http_json(base, f"/api/flows/{fid}/breakpoints/go/before", "DELETE")
    done = http_json(
        base, "/api/runs", "POST", {"flow_id": fid, "flow_version": ver, "max_steps": 20}
    )
    finished = _wait(base, done["run_id"], {"completed", "error", "paused"})
    assert (finished.get("run") or finished)["status"] == "completed"
    recorded = _ids(base, fid)
    code, payload = http_status(
        base,
        "/api/single-runs/group",
        "POST",
        {"flow_id": fid, "flow_version": ver, "node_ids": ["echo"]},
    )
    assert code == 201, payload
    assert payload["run_id"] not in recorded
    _settle(base, payload["run_id"])


def test_concurrent_full_starts_one_wins(server):
    base = server["base"]
    http_json(base, "/api/test/adapter", "POST", {"async_steps": 40, "async_sleep_s": 0.1})
    flow = put_flow(base, {**guard_graph(), "bump": True})
    fid, ver = flow["id"], flow["version"]
    body = {"flow_id": fid, "flow_version": ver}

    def post() -> tuple[int, dict]:
        return http_status(base, "/api/runs", "POST", body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _i: post(), range(2)))
    codes = sorted(code for code, _payload in results)
    assert codes == [201, 409]
    err = next(payload for code, payload in results if code == 409)
    assert err["error"] == "run_in_progress"
    assert err["message"] == BUSY
    assert len(_ids(base, fid)) == 1


def _allow_each(base: str, fid: str, ver: int, parent: str) -> None:
    confirm = _confirm(base, "fake_set_pose")
    calls = [
        ("/api/single-runs/node", {"flow_id": fid, "flow_version": ver, "node_id": "echo"}),
        (
            "/api/single-runs/node",
            {
                "flow_id": fid,
                "flow_version": ver,
                "node_id": "echo",
                "side_effects": "real",
                "confirm": confirm,
            },
        ),
        (
            "/api/single-runs/group",
            {"flow_id": fid, "flow_version": ver, "node_ids": ["echo"]},
        ),
        (f"/api/runs/{parent}/rerun-from", {"step": 1}),
        ("/api/runs", {"flow_id": fid, "flow_version": ver, "max_steps": 4}),
    ]
    for path, body in calls:
        before = set(_ids(base, fid))
        code, payload = http_status(base, path, "POST", body)
        assert code == 201, (path, payload)
        new_id = payload["run_id"]
        assert new_id not in before
        _settle(base, new_id)


def test_allowed_while_paused_at_breakpoint(server):
    base = server["base"]
    flow = put_flow(base, {**guard_graph(), "bump": True})
    fid, ver = flow["id"], flow["version"]
    http_json(
        base,
        f"/api/flows/{fid}/breakpoints/go/before",
        "PUT",
        {"enabled": True, "mode": "every"},
    )
    started = http_json(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": ver})
    rid = started["run_id"]
    snap = _wait(base, rid, {"paused"})
    run = snap.get("run") or snap
    assert run["pause"]["reason"] == "breakpoint"
    _allow_each(base, fid, ver, rid)


def test_allowed_while_awaiting_confirm(server):
    base = server["base"]
    flow = put_flow(base, {**confirm_flow(), "bump": True})
    fid, ver = flow["id"], flow["version"]
    started = http_json(base, "/api/runs", "POST", {"flow_id": fid, "flow_version": ver})
    rid = started["run_id"]
    snap = _wait(base, rid, {"paused"})
    run = snap.get("run") or snap
    assert run["pause"]["reason"] == "approval"
    code, payload = http_status(
        base,
        "/api/single-runs/node",
        "POST",
        {"flow_id": fid, "flow_version": ver, "node_id": "echo"},
    )
    assert code == 201, payload
    _settle(base, payload["run_id"])
    still = http_json(base, f"/api/runs/{rid}")
    assert (still.get("run") or still)["status"] == "paused"
    code, payload = http_status(
        base,
        "/api/single-runs/node",
        "POST",
        {
            "flow_id": fid,
            "flow_version": ver,
            "node_id": "echo",
            "side_effects": "real",
            "confirm": "real",
        },
    )
    assert code == 201, payload
    _settle(base, payload["run_id"])
    code, payload = http_status(
        base,
        "/api/single-runs/group",
        "POST",
        {"flow_id": fid, "flow_version": ver, "node_ids": ["echo"]},
    )
    assert code == 201, payload
    _settle(base, payload["run_id"])
    code, payload = http_status(base, f"/api/runs/{rid}/rerun-from", "POST", {"step": 1})
    assert code == 201, payload
    _settle(base, payload["run_id"])
    code, payload = http_status(
        base, "/api/runs", "POST", {"flow_id": fid, "flow_version": ver, "max_steps": 2}
    )
    assert code == 201, payload


def test_allowed_after_completed(server):
    base = server["base"]
    flow = put_flow(base, {**guard_graph(), "bump": True})
    fid, ver = flow["id"], flow["version"]
    http_json(base, "/api/test/adapter", "POST", {"async_steps": 1, "async_sleep_s": 0.01})
    started = http_json(
        base, "/api/runs", "POST", {"flow_id": fid, "flow_version": ver, "max_steps": 20}
    )
    rid = started["run_id"]
    snap = _wait(base, rid, {"completed", "error"})
    assert (snap.get("run") or snap)["status"] == "completed"
    _allow_each(base, fid, ver, rid)


def test_refuse_does_not_touch_adapter_and_two_starts_race(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 40
        adapter.async_sleep_s = 0.05
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        fid, ver = boot(store, guard_graph())
        rid = await ex.start_full(fid, ver)
        await wait_status(ex, rid, "running")
        for _ in range(50):
            if adapter.calls:
                break
            await asyncio.sleep(0.02)
        assert adapter.calls
        calls = len(adapter.calls)
        pose = dict(adapter.pose)
        writes = list(adapter.writes)
        ids = [row["run_id"] for row in store.list_runs(fid)]
        confirm = get_type("fake_set_pose").confirm_text
        for coro in (
            ex.start_full(fid, ver),
            ex.start_node(fid, ver, "echo"),
            ex.start_node(fid, ver, "go", side_effects="real", confirm=confirm),
            ex.start_group(fid, ver, ["echo", "tool"]),
            ex.rerun_from(rid, 1),
        ):
            with pytest.raises(ApiError) as err:
                await coro
            assert err.value.status == 409
            assert err.value.error == "run_in_progress"
            assert err.value.message == BUSY
        assert len(adapter.calls) == calls
        assert adapter.pose == pose
        assert list(adapter.writes) == writes
        assert [row["run_id"] for row in store.list_runs(fid)] == ids

        other = make_flow(
            [n("start", "start", data={"v": 1}), n("s", "echo"), n("out", "end", x=280)],
            [e("e0", "start", "data", "s", "value"), e("e1", "s", "value", "out", "value")],
        )
        fid2, ver2 = boot(store, other)
        rid2 = await ex.start_full(fid2, ver2)
        assert rid2 != rid
        await wait_status(ex, rid2, "completed", "error")
        await hub.stop()

    asyncio.run(go())


def test_two_idle_starts_under_admit_lock(tmp_path):
    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 30
        adapter.async_sleep_s = 0.05
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        fid, ver = boot(store, guard_graph())
        results = await asyncio.gather(
            ex.start_full(fid, ver),
            ex.start_full(fid, ver),
            return_exceptions=True,
        )
        oks = [item for item in results if isinstance(item, str)]
        errs = [item for item in results if isinstance(item, ApiError)]
        assert len(oks) == 1
        assert len(errs) == 1
        assert errs[0].status == 409
        assert errs[0].error == "run_in_progress"
        assert len(store.list_runs(fid)) == 1
        await hub.stop()

    asyncio.run(go())


def test_paused_and_finished_allow_single_node(tmp_path):
    async def go():
        ex, store, adapter, hub = adapter_ctx(tmp_path)
        adapter.async_steps = 1
        adapter.async_sleep_s = 0.0
        graph = guard_graph()
        for node in graph["nodes"]:
            if node["id"] == "go":
                node["breakpoints"] = {"before": {"enabled": True}}
        fid, ver = boot(store, graph)
        rid = await ex.start_full(fid, ver)
        st = await wait_status(ex, rid, "paused")
        assert st.pause["reason"] == "breakpoint"
        single = await ex.start_node(fid, ver, "echo")
        assert single != rid
        await wait_status(ex, single, "completed", "error")
        assert ex.get(rid).status == "paused"

        confirm = get_type("fake_set_pose").confirm_text
        real = await ex.start_node(fid, ver, "echo", side_effects="real", confirm=confirm)
        await wait_status(ex, real, "completed", "error", "paused")
        grouped, _echo = await ex.start_group(fid, ver, ["echo"])
        await wait_status(ex, grouped, "completed", "error")
        parent_steps = store.list_steps(rid, limit=10)
        assert parent_steps
        rerun = await ex.rerun_from(rid, parent_steps[0]["step"])
        await wait_status(ex, rerun, "paused", "completed", "error")
        assert ex.get(rid).status == "paused"

        plain = make_flow(
            [n("start", "start", data={"v": 1}), n("echo", "echo"), n("out", "end")],
            [e("e0", "start", "data", "echo", "value"), e("e1", "echo", "value", "out", "value")],
        )
        fid3, ver3 = boot(store, plain)
        done = await ex.start_full(fid3, ver3, max_steps=10)
        await wait_status(ex, done, "completed")
        again = await ex.start_node(fid3, ver3, "echo")
        await wait_status(ex, again, "completed")
        fresh = await ex.start_full(fid3, ver3, max_steps=10)
        await wait_status(ex, fresh, "completed")
        await hub.stop()

    asyncio.run(go())


def _cmds(store, rid: str) -> list[dict]:
    return list(getattr(store, "_commands", {}).get(rid, []))


def _slow_pose(node_id: str, *, y: int = 200) -> dict:
    node = n(node_id, "fake_set_pose", x=280, y=y)
    node["params"] = {"x": 0.2, "y": 0.1, "z": 0.2}
    return node


async def _until_running(ex, rid: str, adapter: FakeAdapter) -> None:
    for _ in range(50):
        st = ex.get(rid)
        if st and st.status == "running" and adapter.calls:
            return
        await asyncio.sleep(0.02)
    st = ex.get(rid)
    raise TimeoutError(f"slow run did not stay running, status={st.status if st else None}")


def test_resume_refused_while_another_run_is_executing(tmp_path):
    """Continuing a paused run must not join a run that is already executing."""

    async def go():
        adapter = FakeAdapter()
        adapter.async_steps = 30
        adapter.async_sleep_s = 0.05
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        gate = n("gate", "echo", breakpoints={"before": {"enabled": True}})
        graph = make_flow(
            [
                n("start", "start", data={"v": 1}),
                gate,
                n("out", "end", x=560),
                _slow_pose("slow"),
            ],
            [
                e("e0", "start", "data", "gate", "value"),
                e("e1", "gate", "value", "out", "value"),
            ],
        )
        fid, ver = boot(store, graph)
        paused_id = await ex.start_full(fid, ver)
        paused = await wait_status(ex, paused_id, "paused")
        assert paused.pause is not None
        assert paused.pause["reason"] == "breakpoint"
        pause_id = paused.pause["pause_id"]
        pause_before = dict(paused.pause)

        running_id = await ex.start_node(fid, ver, "slow", confirm=True, request_id="slow-req")
        await _until_running(ex, running_id, adapter)
        assert (
            await ex.start_node(fid, ver, "slow", confirm=True, request_id="slow-req") == running_id
        )
        with pytest.raises(ApiError) as fresh:
            await ex.start_full(fid, ver, request_id="other-req")
        assert fresh.value.status == 409
        assert fresh.value.error == "run_in_progress"
        assert [row["run_id"] for row in store.list_runs(fid)].count(running_id) == 1

        for resume in (
            ex.continue_run(paused_id, pause_id),
            ex.step_run(paused_id, pause_id),
        ):
            with pytest.raises(ApiError) as err:
                await resume
            assert err.value.status == 409
            assert err.value.error == "run_in_progress"
            still = ex.get(paused_id)
            assert still is not None
            assert still.status == "paused"
            assert still.pause is not None
            assert still.pause["pause_id"] == pause_id
            assert still.pause["reason"] == pause_before["reason"]
            assert still.last_used_pause_id is None
            assert all(cmd["result"] != "200" for cmd in _cmds(store, paused_id))

        await wait_status(ex, running_id, "completed", "error", timeout=6)
        resumed = await ex.continue_run(paused_id, pause_id)
        assert resumed["status"] == "running"
        after = await wait_status(ex, paused_id, "running", "completed", timeout=3)
        assert after.status != "paused"

        ask = make_flow(
            [
                n("start", "start", data={"v": 1}),
                n("ask", "human_confirm", x=280),
                n("out", "end", x=560),
                _slow_pose("motion", y=240),
            ],
            [
                e("a0", "start", "data", "ask", "value"),
                e("a1", "ask", "pass", "out", "value"),
            ],
        )
        fid2, ver2 = boot(store, ask)
        approval_id = await ex.start_full(fid2, ver2)
        approval = await wait_status(ex, approval_id, "paused")
        assert approval.pause["reason"] == "approval"
        approval_pause = approval.pause["pause_id"]
        other = await ex.start_node(fid2, ver2, "motion", confirm=True)
        await _until_running(ex, other, adapter)
        with pytest.raises(ApiError) as denied:
            await ex.approve(approval_id, approval_pause, "approve")
        assert denied.value.status == 409
        assert denied.value.error == "run_in_progress"
        held = ex.get(approval_id)
        assert held is not None
        assert held.status == "paused"
        assert held.pause is not None
        assert held.pause["pause_id"] == approval_pause
        assert held.decision is None
        assert all(cmd["result"] != "200" for cmd in _cmds(store, approval_id))
        await hub.stop()

    asyncio.run(go())
