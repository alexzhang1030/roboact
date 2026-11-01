"""Stop holds in place. r1–r6 step 6 and acceptance 8.3."""

from __future__ import annotations

import asyncio
import itertools
import time

import pytest
from roboact.adapter import FAKE_ACTIONS, FAKE_NOTE_ACTION, CancelSpec, FakeAdapter
from roboact.errors import ApiError
from roboact.nodes import bind_adapter, get_type, list_specs, rejected_actions

from tests.helpers import adapter_ctx, boot, run_until, wait_status
from tests.test_a23_a37 import pose_flow

TARGET = {"x": 0.4, "y": 0.1, "z": 0.2}
# r1–r6 step 6: a finish_step action never runs past 10 s.
FINISH_STEP_MAX_S = 10.0
# Fixed interval for the post-stop reads. The limit is the adapter's goal tolerance.
HOLD_READS = 4
HOLD_INTERVAL_S = 0.05


def _motion_row(action_id: str, *, cancel: dict) -> dict:
    return {
        "id": action_id,
        "title": action_id,
        "version": "t",
        "side_effect": "motion",
        "return_mode": "blocking",
        "timeout_s": 4,
        "outputs": [{"name": "pose", "type": "any"}],
        "cancel": cancel,
        "read_state": True,
        "expiry": "controller",
    }


def test_load_refuses_safe_pose_and_names_the_action() -> None:
    adapter = FakeAdapter()
    adapter.extra_raw_actions = [
        _motion_row(
            "park_arm",
            cancel={"cancellable": True, "post_cancel": "safe_pose", "after": "safe_pose"},
        )
    ]
    bind_adapter(adapter)
    types = {spec["type_id"] for spec in list_specs(adapter)}
    assert "park_arm" not in types
    assert "fake_set_pose" in types
    rej = next(row for row in rejected_actions(adapter) if row["id"] == "park_arm")
    assert "park_arm" in rej["reason"]
    assert "safe_pose" in rej["reason"]
    grip = get_type("fake_gripper").action_desc
    assert grip is not None
    assert grip.cancel.cancellable is True
    assert grip.cancel.post_cancel == "hold"
    assert grip.cancel.after == "hold"
    tick = get_type("fake_tick").action_desc
    assert tick is not None
    assert tick.cancel.cancellable is False
    assert tick.cancel.post_cancel == "hold"
    assert FAKE_NOTE_ACTION.cancel.cancellable is False
    assert FAKE_NOTE_ACTION.cancel.post_cancel == "hold"


def test_finish_step_loads_as_noncancellable_hold() -> None:
    adapter = FakeAdapter()
    adapter.extra_raw_actions = [
        {
            "id": "finish_then_hold",
            "title": "Finish then hold",
            "return_mode": "blocking",
            "timeout_s": 3,
            "outputs": [{"name": "ok", "type": "any"}],
            "side_effect": "none",
            "cancel": {"cancellable": True, "post_cancel": "finish_step"},
        }
    ]
    bind_adapter(adapter)
    desc = get_type("finish_then_hold").action_desc
    assert desc is not None
    assert desc.cancel.cancellable is False
    assert desc.cancel.post_cancel == "hold"


async def _wait_call(adapter: FakeAdapter, action: str, timeout: float = 2.0) -> None:
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        if any(row.get("action") == action for row in adapter.calls):
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(adapter.calls)


def _pose_delta(a: dict[str, float], b: dict[str, float]) -> float:
    return max(abs(float(a[k]) - float(b[k])) for k in ("x", "y", "z"))


async def _held_samples(adapter: FakeAdapter) -> list[tuple[dict[str, float], bool]]:
    """Read pose and grip several times at one fixed interval, after the stop."""
    samples: list[tuple[dict[str, float], bool]] = []
    for i in range(HOLD_READS):
        if i:
            await asyncio.sleep(HOLD_INTERVAL_S)
        pose = (await adapter.read_state("fake_set_pose"))["pose"]
        open_ = bool((await adapter.read_state("fake_gripper"))["open"])
        samples.append((pose, open_))
    return samples


def _assert_held(adapter: FakeAdapter, samples: list[tuple[dict[str, float], bool]]) -> None:
    tol = float(adapter.goal_tolerance)
    assert len(samples) >= 3
    for (pose_a, open_a), (pose_b, open_b) in itertools.pairwise(samples):
        assert _pose_delta(pose_a, pose_b) <= tol
        assert open_a is open_b
    assert all(open_ is False for _, open_ in samples)
    assert samples[-1][0] != adapter.safe_pose_target


def test_stop_holds_motion_and_gripper(tmp_path) -> None:
    """r1–r6 step 6: hold, then settled reads stay inside the goal tolerance."""

    async def go() -> None:
        adapter = FakeAdapter()
        adapter.pose = {"x": 0.2, "y": -0.3, "z": 0.5}
        adapter.gripper_open = False
        adapter.async_steps = 20
        adapter.async_sleep_s = 0.1
        adapter.block_s = 1.2
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        try:
            rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
            await _wait_call(adapter, "fake_set_pose")
            t0 = asyncio.get_event_loop().time()
            while adapter.pose == {"x": 0.2, "y": -0.3, "z": 0.5}:
                if asyncio.get_event_loop().time() - t0 > 2:
                    raise TimeoutError(adapter.pose)
                await asyncio.sleep(0.01)
            grip_task = asyncio.create_task(adapter.execute("fake_gripper", {"open": True}))
            await _wait_call(adapter, "fake_gripper")
            out = await ex.stop_run(rid)
            assert out["status"] == "stopping"
            asked = [row for row in adapter.cancel_calls if row.get("post_cancel") == "hold"]
            assert {row["action_id"] for row in asked} >= {"fake_set_pose", "fake_gripper"}
            st = await wait_status(ex, rid, "stopped", timeout=3)
            assert st.status == "stopped"
            grip_result = await grip_task
            assert grip_result.success is False
            samples = await _held_samples(adapter)
            _assert_held(adapter, samples)
            assert samples[-1][0] != TARGET
        finally:
            await hub.stop()

    asyncio.run(go())


def test_finish_step_stop_runs_to_end_then_holds_within_10s(tmp_path) -> None:
    """r1–r6 step 6: a finish_step action runs to its end, then holds, within 10 s."""

    async def go() -> None:
        adapter = FakeAdapter()
        adapter.pose = {"x": 0.2, "y": -0.3, "z": 0.5}
        adapter.gripper_open = False
        adapter.async_steps = 4
        adapter.async_sleep_s = 0.08
        raw = next(a.model_dump() for a in FAKE_ACTIONS if a.id == "fake_set_pose")
        raw["timeout_s"] = 4
        raw["cancel"] = {
            "cancellable": True,
            "post_cancel": "finish_step",
            "after": "finish_step",
        }
        adapter.drop_action_ids.add("fake_set_pose")
        adapter.extra_raw_actions.append(raw)
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        try:
            desc = get_type("fake_set_pose").action_desc
            assert desc is not None
            assert desc.cancel.cancellable is False
            assert desc.cancel.post_cancel == "hold"
            assert float(desc.timeout_s) <= FINISH_STEP_MAX_S
            rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
            await _wait_call(adapter, "fake_set_pose")
            started = float(adapter.goal_sent_at or time.monotonic())
            out = await ex.stop_run(rid)
            assert out["status"] == "stopping"
            assert adapter.cancel_calls == []
            st = await wait_status(ex, rid, "stopped", timeout=FINISH_STEP_MAX_S)
            assert st.status == "stopped"
            assert time.monotonic() - started <= FINISH_STEP_MAX_S
            assert adapter.pose == TARGET
            _assert_held(adapter, await _held_samples(adapter))
        finally:
            await hub.stop()

    asyncio.run(go())


def test_noncancellable_runs_to_end_then_holds(tmp_path) -> None:
    async def go() -> None:
        adapter = FakeAdapter()
        adapter.pose = {"x": 0.2, "y": -0.3, "z": 0.5}
        adapter.async_steps = 6
        adapter.async_sleep_s = 0.12
        adapter.action_updates["fake_set_pose"] = {
            "timeout_s": 5,
            "cancel": CancelSpec(cancellable=False, post_cancel="hold"),
        }
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        try:
            rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
            await _wait_call(adapter, "fake_set_pose")
            out = await ex.stop_run(rid)
            assert out["status"] == "stopping"
            assert adapter.cancel_calls == []
            st = await wait_status(ex, rid, "stopped", timeout=3)
            assert st.status == "stopped"
            assert adapter.pose == TARGET
            await asyncio.sleep(0.3)
            assert (await adapter.read_state("fake_set_pose"))["pose"] == TARGET
            assert adapter.pose != adapter.safe_pose_target
        finally:
            await hub.stop()

    asyncio.run(go())


def test_motion_single_run_requires_confirm(tmp_path) -> None:
    async def go() -> None:
        ex, store, adapter, hub = adapter_ctx(tmp_path)
        try:
            fid, ver = boot(store, pose_flow())
            with pytest.raises(ApiError) as ei:
                await ex.start_node(fid, ver, "go")
            assert ei.value.error == "confirm_required"
            wording = get_type("fake_set_pose").confirm_text
            with pytest.raises(ApiError) as ei:
                await ex.start_node(fid, ver, "go", confirm=wording)
            assert ei.value.error == "confirm_required"
            assert adapter.goals_sent == []
            rid = await ex.start_node(fid, ver, "go", confirm=True)
            await _wait_call(adapter, "fake_set_pose")
            assert rid
            assert adapter.goals_sent
            rid = await ex.start_node(fid, ver, "go", confirm="fake_set_pose")
            assert rid
        finally:
            await hub.stop()

    asyncio.run(go())
