from __future__ import annotations

import asyncio
import base64
import contextlib
import struct
import time
import zlib
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from .catalog import en, zh
from .util import now_iso

ProgressFn = Callable[["Progress"], Awaitable[None] | None]


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str = "any"
    unit: str | None = None
    range: tuple[float, float] | None = None
    enum: list[Any] | None = None
    default: Any = None
    required: bool = True
    secret: bool = False
    title: str | None = None


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str = "any"
    unit: str | None = None
    title: str | None = None


class CancelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancellable: bool = True
    post_cancel: Literal["hold", "safe_pose", "finish_step"] = "hold"
    after: Literal["hold", "safe_pose", "finish_step"] | None = None
    method: str = ""


class Precondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    check: str | None = None


class AdapterInfo(BaseModel):
    """Adapter-level descriptor: name, version, target, action-list version."""

    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    target: Literal["sim", "real"]
    action_list_version: str


class ActionDescriptor(BaseModel):
    """Auto fields come from the adapter interface; the rest is the hand-written table."""

    model_config = ConfigDict(extra="forbid")
    id: str
    version: str
    title: str
    params: list[ParamSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    side_effect: Literal["none", "read", "motion"] = "motion"
    return_mode: Literal["blocking", "goal_progress"] = "blocking"
    timeout_s: float = 60
    typical_s: float | None = None
    cancel: CancelSpec = Field(default_factory=CancelSpec)
    preconditions: list[Precondition] = Field(default_factory=list)
    precondition_wait_s: float | None = None
    read_state: bool = False
    expiry: Literal["controller", "adapter_only"] = "controller"
    labels: dict[str, str] = Field(default_factory=dict)
    name: str | None = None
    side_effect_note: str = ""
    confirm_text: str = ""
    icon: str = ""


class Progress(BaseModel):
    percent: float | None = None
    text: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    at: str = Field(default_factory=now_iso)


class ActionResult(BaseModel):
    success: bool = True
    outputs: dict[str, Any] = Field(default_factory=dict)
    measured: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class CancelResult(BaseModel):
    accepted: bool
    reason: str = ""
    post_cancel: Literal["hold", "safe_pose", "finish_step"] = "hold"


class ExecuteHandle:
    def __init__(
        self,
        action_id: str,
        idem_key: str | None,
        cancellable: bool,
        post_cancel: Literal["hold", "safe_pose", "finish_step"],
    ) -> None:
        self.id = f"{action_id}:{idem_key or id(self)}"
        self.action_id = action_id
        self.idem_key = idem_key
        self.cancellable = cancellable
        self.post_cancel: Literal["hold", "safe_pose", "finish_step"] = post_cancel
        self.cancelled = False
        self.cancelled_by_lost_heartbeat = False
        self.done = False
        self.result: ActionResult | None = None
        self.progress: Progress | None = None
        self.deadline: float | None = None
        self.return_mode: str = "blocking"


class CrashBetweenWriteAndKey(Exception):
    """Test hook: died after the side effect and before the idempotency key."""


class PreconditionFailed(Exception):
    """Preconditions still unmet; the goal was not sent."""

    def __init__(self, message: str = "precondition_failed", state: dict[str, Any] | None = None):
        super().__init__(message)
        self.error_type = "precondition_failed"
        self.state = state or {}


class WaitAborted(Exception):
    """Wait stopped before send (stop / pause / page or adapter lost)."""

    def __init__(self, reason: str = "user"):
        super().__init__(reason)
        self.reason = reason


class Adapter:
    """Controller-specific implementation. The canvas never sees SDK vs ROS 2."""

    adapter_id: str = "adapter"
    name: str = en("adapter.generic")
    version: str = "0.2"
    target: Literal["sim", "real"] = "sim"
    action_list_version: str = "v0"
    connected: bool = True
    heartbeat_timeout_s: float = 3.0
    robot_id: str = "adapter-robot"
    robot_model: str = "generic"
    units_angle: str = "rad"
    units_length: str = "m"
    _DEFAULT_JOINT_LIMITS: ClassVar[dict[str, tuple[float, float]]] = {}
    _DEFAULT_VELOCITY_LIMITS: ClassVar[dict[str, tuple[float, float]]] = {}

    def robot(self) -> dict[str, str]:
        return {"id": self.robot_id, "model": self.robot_model}

    def units(self) -> dict[str, str]:
        return {"angle": self.units_angle, "length": self.units_length}

    def __init__(self) -> None:
        self.rejected_actions: list[dict[str, Any]] = []
        self.fetched_at: str | None = None
        self._handles: dict[str, ExecuteHandle] = {}
        self._finished: dict[str, ExecuteHandle] = {}
        self.reject_next_cancel = False
        self.force_uncancellable = False
        self._last_backend_beat = time.monotonic()
        self._watchdog_task: asyncio.Task | None = None
        self._lost_backend_fired = False
        self.goals_cancelled_by_lost_heartbeat = 0
        self.last_precondition_wait_s: float | None = None
        self.last_precondition_state: dict[str, Any] | None = None
        self.joint_limits: dict[str, tuple[float, float]] = dict(type(self)._DEFAULT_JOINT_LIMITS)
        self.velocity_limits: dict[str, tuple[float, float]] = dict(
            type(self)._DEFAULT_VELOCITY_LIMITS
        )
        self.heartbeat_times: list[float] = []

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name=self.name,
            version=self.version,
            target=self.target,
            action_list_version=self.action_list_version,
        )

    def list_actions(self) -> list[ActionDescriptor]:
        raise NotImplementedError

    def descriptor(self, action_id: str) -> ActionDescriptor:
        for d in self.list_actions():
            if d.id == action_id:
                return d
        raise KeyError(f"unknown action: {action_id}")

    def status(self) -> str:
        if not self.connected:
            return "disconnected"
        if not self.list_actions():
            return "empty"
        return "ok"

    def public(self) -> dict[str, Any]:
        self.fetched_at = now_iso()
        actions = [a.model_dump() for a in self.list_actions()] if self.connected else []
        return {
            "id": self.adapter_id,
            "name": self.name,
            "version": self.version,
            "target": self.target,
            "action_list_version": self.action_list_version,
            "robot": self.robot(),
            "units": self.units(),
            "connected": self.connected,
            "status": "disconnected" if not self.connected else ("empty" if not actions else "ok"),
            "source": f"{self.name} · {self.target}",
            "fetched_at": self.fetched_at,
            "actions": actions,
            "rejected_actions": list(getattr(self, "rejected_actions", []) or []),
        }

    def receive_heartbeat(self) -> None:
        """Backend → adapter heartbeat. Missing beats cancel in-flight goals."""
        now = time.monotonic()
        self.heartbeat_times.append(now)
        self._last_backend_beat = now
        self._lost_backend_fired = False

    async def ping(self) -> bool:
        return bool(self.connected)

    def start_watchdog(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = loop.create_task(self._watchdog())

    async def stop_watchdog(self) -> None:
        task = self._watchdog_task
        self._watchdog_task = None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.05)
                if time.monotonic() - self._last_backend_beat >= self.heartbeat_timeout_s:
                    await self._cancel_inflight_lost_backend()
        except asyncio.CancelledError:
            return

    async def _cancel_inflight_lost_backend(self) -> None:
        if self._lost_backend_fired:
            return
        self._lost_backend_fired = True
        for handle in list(self._handles.values()):
            if handle.done:
                continue
            handle.cancelled = True
            handle.cancelled_by_lost_heartbeat = True
            self.goals_cancelled_by_lost_heartbeat += 1

    def handle_by_idem(self, idem_key: str | None) -> ExecuteHandle | None:
        if not idem_key:
            return None
        for h in self._handles.values():
            if h.idem_key == idem_key:
                return h
        for h in self._finished.values():
            if h.idem_key == idem_key:
                return h
        return None

    def list_active_goals(self) -> list[dict[str, Any]]:
        out = []
        for h in self._handles.values():
            if h.done:
                continue
            out.append(
                {
                    "id": h.id,
                    "action_id": h.action_id,
                    "idem_key": h.idem_key,
                    "return_mode": h.return_mode,
                    "cancelled": h.cancelled,
                    "done": h.done,
                }
            )
        return out

    async def goal_status(
        self, handle_id: str | None = None, idem_key: str | None = None
    ) -> dict[str, Any]:
        handle = None
        if handle_id and handle_id in self._handles:
            handle = self._handles[handle_id]
        elif handle_id and handle_id in self._finished:
            handle = self._finished[handle_id]
        elif idem_key:
            handle = self.handle_by_idem(idem_key)
        if handle is None:
            return {"found": False}
        return {
            "found": True,
            "id": handle.id,
            "action_id": handle.action_id,
            "done": handle.done,
            "cancelled": handle.cancelled,
            "cancelled_by_lost_heartbeat": handle.cancelled_by_lost_heartbeat,
            "result": handle.result.model_dump() if handle.result else None,
        }

    def precondition_poll_interval(self, desc: ActionDescriptor) -> float:
        """How often this adapter re-reads state while waiting. Not set by the engine."""
        return 0.1

    def preconditions_hold(self, desc: ActionDescriptor, state: dict[str, Any]) -> bool:
        for pre in desc.preconditions:
            if not pre.check:
                continue
            if not state.get(pre.check):
                return False
        return True

    async def await_preconditions(
        self,
        desc: ActionDescriptor,
        should_abort: Callable[[], str | None] | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Re-read read_state until preconditions hold or the wait window ends.

        No `precondition_wait_s` means one read (current behaviour). The poll
        interval is this adapter's, not the executor's.
        """
        wait_s = desc.precondition_wait_s
        need = any(p.check for p in desc.preconditions) or wait_s is not None
        if not need:
            self.last_precondition_wait_s = 0.0
            self.last_precondition_state = None
            return True, None
        t0 = time.monotonic()
        deadline = t0 if wait_s is None else t0 + float(wait_s)
        last: dict[str, Any] | None = None
        while True:
            if should_abort:
                reason = should_abort()
                if reason:
                    self.last_precondition_wait_s = time.monotonic() - t0
                    raise WaitAborted(reason)
            last = await self.read_state(desc.id)
            self.last_precondition_state = last
            if self.preconditions_hold(desc, last):
                self.last_precondition_wait_s = time.monotonic() - t0
                return True, last
            now = time.monotonic()
            if wait_s is None or now >= deadline:
                self.last_precondition_wait_s = now - t0
                return False, last
            interval = self.precondition_poll_interval(desc)
            remain = deadline - now
            if remain <= 0:
                self.last_precondition_wait_s = time.monotonic() - t0
                return False, last
            slept = 0.0
            slice_s = min(interval, remain)
            while slept < slice_s:
                if should_abort:
                    reason = should_abort()
                    if reason:
                        self.last_precondition_wait_s = time.monotonic() - t0
                        raise WaitAborted(reason)
                hop = min(0.05, slice_s - slept)
                await asyncio.sleep(hop)
                slept += hop

    async def execute(
        self,
        action_id: str,
        inputs: dict[str, Any],
        on_progress: ProgressFn | None = None,
        idem_key: str | None = None,
        deadline: float | None = None,
        on_phase: Callable[[str], Awaitable[None] | None] | None = None,
        should_abort: Callable[[], str | None] | None = None,
    ) -> ActionResult:
        desc = self.descriptor(action_id)
        need_wait = any(p.check for p in desc.preconditions) or desc.precondition_wait_s is not None
        if need_wait and on_phase:
            out = on_phase("waiting_precondition")
            if asyncio.iscoroutine(out):
                await out
        ok, last = await self.await_preconditions(desc, should_abort=should_abort)
        if not ok:
            return ActionResult(
                success=False,
                message="precondition_failed",
                measured=dict(last or {}),
            )
        if on_phase:
            out = on_phase("sent")
            if asyncio.iscoroutine(out):
                await out
        if deadline is None:
            deadline = time.time() + float(desc.timeout_s or 60)
        handle = ExecuteHandle(
            action_id, idem_key, desc.cancel.cancellable, desc.cancel.post_cancel
        )
        handle.deadline = deadline
        handle.return_mode = (
            "goal_progress" if desc.return_mode in ("goal_progress", "async") else desc.return_mode
        )
        self._handles[handle.id] = handle
        try:
            if desc.return_mode in ("goal_progress", "async"):
                await self.send_goal(action_id, inputs, handle)
                return await self._wait_async(handle, on_progress)
            if on_progress:
                await _emit(on_progress, Progress(text="adapter.running"))
            result = await self.run_blocking(action_id, inputs, handle)
            handle.done = True
            handle.result = result
            return result
        finally:
            self._finished[handle.id] = handle
            if handle.done:
                self._handles.pop(handle.id, None)

    async def send_goal(
        self, action_id: str, inputs: dict[str, Any], handle: ExecuteHandle
    ) -> None:
        raise NotImplementedError

    async def poll_progress(self, handle: ExecuteHandle) -> Progress | None:
        return handle.progress

    async def get_result(self, handle: ExecuteHandle) -> ActionResult:
        if handle.result is None:
            raise RuntimeError("result not ready")
        return handle.result

    async def run_blocking(
        self, action_id: str, inputs: dict[str, Any], handle: ExecuteHandle
    ) -> ActionResult:
        raise NotImplementedError

    async def cancel(self, handle: ExecuteHandle | None) -> CancelResult:
        if self.reject_next_cancel:
            self.reject_next_cancel = False
            return CancelResult(accepted=False, reason="cancel rejected")
        if handle is None:
            return CancelResult(accepted=False, reason="nothing to cancel")
        if self.force_uncancellable or not handle.cancellable:
            return CancelResult(accepted=False, reason="this action cannot be cancelled")
        handle.cancelled = True
        return CancelResult(accepted=True, post_cancel=handle.post_cancel)

    async def read_state(self, action_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def _wait_async(
        self, handle: ExecuteHandle, on_progress: ProgressFn | None
    ) -> ActionResult:
        while not handle.done:
            if handle.cancelled:
                handle.done = True
                handle.result = ActionResult(success=False, message="cancelled")
                break
            if handle.deadline is not None and time.time() >= handle.deadline:
                handle.done = True
                handle.result = ActionResult(success=False, message="timeout")
                break
            prog = await self.poll_progress(handle)
            if prog and on_progress:
                await _emit(on_progress, prog)
            await asyncio.sleep(0.04)
        return handle.result or ActionResult(success=False, message="no_result")


async def _emit(fn: ProgressFn, progress: Progress) -> None:
    out = fn(progress)
    if asyncio.iscoroutine(out):
        await out


def fake_png(width: int = 64, height: int = 48, seed: int = 0) -> bytes:
    """Tiny generated PNG, not written to disk."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(((x * 3 + seed) & 255, (y * 5 + seed) & 255, (x + y + seed) & 255))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def image_payload(
    png: bytes, width: int, height: int, captured_at: str | None = None
) -> dict[str, Any]:
    return {
        "type": "image",
        "mime": "image/png",
        "bytes": len(png),
        "width": width,
        "height": height,
        "captured_at": captured_at or now_iso(),
        "data": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
    }


def image_summary(value: Any) -> Any:
    """Logs keep size/time/bytes only — never image pixels."""
    if isinstance(value, dict) and (
        value.get("type") == "image" or value.get("mime", "").startswith("image/")
    ):
        return {
            "type": "image",
            "mime": value.get("mime") or "image/png",
            "bytes": value.get("bytes"),
            "width": value.get("width"),
            "height": value.get("height"),
            "captured_at": value.get("captured_at"),
            "omitted": True,
        }
    if isinstance(value, dict):
        return {k: image_summary(v) for k, v in value.items()}
    if isinstance(value, list):
        return [image_summary(v) for v in value]
    return value


# Hand-written per-action table. Missing side_effect ⇒ motion (enforced in
# ingest).
FAKE_ACTIONS: list[ActionDescriptor] = [
    ActionDescriptor(
        id="fake_read_pose",
        version="fake-v1",
        title=en("node.fake_read_pose"),
        name="Read pose",
        labels={"en": en("node.fake_read_pose"), "zh-CN": zh("node.fake_read_pose")},
        params=[],
        outputs=[
            OutputSpec(name="pose", type="any", title="pose"),
        ],
        side_effect="read",
        return_mode="blocking",
        timeout_s=5,
        typical_s=0.05,
        cancel=CancelSpec(cancellable=True, post_cancel="hold"),
        preconditions=[Precondition(text="sim pose readable")],
        read_state=False,
        expiry="controller",
        side_effect_note="reads sim pose",
        icon="node-output",
    ),
    ActionDescriptor(
        id="fake_set_pose",
        version="fake-v1",
        title=en("node.fake_set_pose"),
        name="Go to pose",
        labels={"en": en("node.fake_set_pose"), "zh-CN": zh("node.fake_set_pose")},
        params=[
            ParamSpec(name="target", type="any", required=False, title="target"),
            ParamSpec(
                name="x",
                type="number",
                unit="m",
                range=(-2.0, 2.0),
                default=0.0,
                required=False,
                title="X",
            ),
            ParamSpec(
                name="y",
                type="number",
                unit="m",
                range=(-2.0, 2.0),
                default=0.0,
                required=False,
                title="Y",
            ),
            ParamSpec(
                name="z",
                type="number",
                unit="m",
                range=(0.0, 2.0),
                default=0.0,
                required=False,
                title="Z",
            ),
        ],
        outputs=[OutputSpec(name="pose", type="any", title="pose")],
        side_effect="motion",
        return_mode="goal_progress",
        timeout_s=60,
        typical_s=0.4,
        cancel=CancelSpec(cancellable=True, post_cancel="hold", after="hold", method="cancel_goal"),
        preconditions=[Precondition(text="sim controller connected", check="connected")],
        read_state=True,
        expiry="controller",
        side_effect_note="moves sim joints to the target",
        confirm_text="Move the sim end-effector to the target pose.",
        icon="node-tool",
    ),
    ActionDescriptor(
        id="fake_gripper",
        version="fake-v1",
        title=en("node.fake_gripper"),
        name="Gripper",
        labels={"en": en("node.fake_gripper"), "zh-CN": zh("node.fake_gripper")},
        params=[
            ParamSpec(name="open", type="boolean", default=True, required=False, title="open"),
            ParamSpec(name="target", type="any", required=False, title="target"),
        ],
        outputs=[OutputSpec(name="open", type="boolean", title="open")],
        side_effect="motion",
        return_mode="blocking",
        timeout_s=8,
        typical_s=0.2,
        cancel=CancelSpec(
            cancellable=True, post_cancel="safe_pose", after="safe_pose", method="cancel_goal"
        ),
        preconditions=[Precondition(text="gripper not locked")],
        read_state=True,
        expiry="controller",
        side_effect_note="drives the sim gripper",
        confirm_text="Open or close the sim gripper.",
        icon="node-tool",
    ),
    ActionDescriptor(
        id="fake_tick",
        version="fake-v1",
        title=en("node.fake_tick"),
        name="Tick",
        labels={"en": en("node.fake_tick"), "zh-CN": zh("node.fake_tick")},
        params=[],
        outputs=[OutputSpec(name="n", type="number", title="tick")],
        side_effect="none",
        return_mode="blocking",
        timeout_s=5,
        typical_s=0.01,
        cancel=CancelSpec(cancellable=True, post_cancel="finish_step"),
        preconditions=[],
        read_state=False,
        expiry="adapter_only",
        icon="node-constant",
    ),
    ActionDescriptor(
        id="fake_camera",
        version="fake-v1",
        title=en("node.fake_camera"),
        name="Camera",
        labels={"en": en("node.fake_camera"), "zh-CN": zh("node.fake_camera")},
        params=[],
        outputs=[OutputSpec(name="frame", type="image", title="frame")],
        side_effect="read",
        return_mode="blocking",
        timeout_s=5,
        typical_s=0.08,
        cancel=CancelSpec(cancellable=True, post_cancel="hold"),
        preconditions=[Precondition(text="sim camera available")],
        read_state=False,
        expiry="adapter_only",
        side_effect_note="reads a generated test frame; does not write disk",
        icon="node-model",
    ),
]

# Test-only. Not in FAKE_ACTIONS, so it never appears in the palette.
FAKE_NOTE_ACTION = ActionDescriptor(
    id="fake_note",
    version="fake-v1",
    title="Note",
    name="Note",
    labels={"en": "Note", "zh-CN": "Note"},
    params=[
        ParamSpec(
            name="mode",
            type="string",
            default="on",
            required=False,
            title="Mode",
            enum=["on", "off"],
        ),
        ParamSpec(name="ver", type="string", default="1.10", required=False, title="Ver"),
        ParamSpec(
            name="token",
            type="string",
            default=None,
            required=False,
            secret=True,
            title="Token",
        ),
    ],
    outputs=[OutputSpec(name="ok", type="boolean", title="OK")],
    side_effect="none",
    return_mode="blocking",
    timeout_s=5,
    typical_s=0.01,
    cancel=CancelSpec(cancellable=True, post_cancel="finish_step"),
    preconditions=[],
    read_state=False,
    expiry="adapter_only",
    icon="node-constant",
)


class FakeAdapter(Adapter):
    """In-memory sim adapter. Actions are fixtures named fake_*. No real controller."""

    adapter_id = "fake"
    name = en("adapter.fake")
    version = "0.2"
    target = "sim"
    action_list_version = "fake-v1"
    robot_id = "fake-1"
    robot_model = "FakeArm"
    units_angle = "rad"
    units_length = "m"
    _DEFAULT_JOINT_LIMITS: ClassVar[dict[str, tuple[float, float]]] = {
        "x": (-2.0, 2.0),
        "y": (-2.0, 2.0),
        "z": (0.0, 2.0),
    }
    _DEFAULT_VELOCITY_LIMITS: ClassVar[dict[str, tuple[float, float]]] = {"speed": (0.0, 2.0)}

    def __init__(self) -> None:
        super().__init__()
        self.pose = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.gripper_open = True
        self.tick = 0
        self.writes: list[dict[str, Any]] = []
        self.idem: dict[str, ActionResult] = {}
        self.crash_after_write_before_key = False
        self.delay_after_commit = 0.0
        self.async_steps = 4
        self.async_sleep_s = 0.04
        self.block_s = 0.0
        self.calls: list[dict[str, Any]] = []
        self._async_jobs: dict[str, asyncio.Task] = {}
        self.extra_raw_actions: list[Any] = []
        self.drop_action_ids: set[str] = set()
        self.action_updates: dict[str, dict[str, Any]] = {}
        self.stable: bool = True
        self.stable_after_s: float | None = None
        self._read_t0: float | None = None
        self.read_state_calls = 0
        self.goals_sent: list[dict[str, Any]] = []
        self.goal_sent_at: float | None = None
        self.precondition_poll_s = 0.05

    def precondition_poll_interval(self, desc: ActionDescriptor) -> float:
        return float(self.precondition_poll_s)

    def list_actions(self) -> list[Any]:
        out: list[Any] = []
        for a in FAKE_ACTIONS:
            if a.id in self.drop_action_ids:
                continue
            updates = dict(self.action_updates.get(a.id) or {})
            if self.force_uncancellable and a.id == "fake_gripper":
                updates["cancel"] = CancelSpec(
                    cancellable=False, post_cancel=a.cancel.post_cancel, after=a.cancel.after
                )
            out.append(a.model_copy(update=updates) if updates else a)
        out.extend(self.extra_raw_actions)
        return out

    async def read_state(self, action_id: str) -> dict[str, Any]:
        self.read_state_calls += 1
        now = time.monotonic()
        if self._read_t0 is None:
            self._read_t0 = now
        if self.stable_after_s is not None:
            stable = (now - self._read_t0) >= float(self.stable_after_s)
        else:
            stable = bool(self.stable)
        state: dict[str, Any] = {
            "stable": stable,
            "connected": bool(self.connected),
        }
        if action_id == "fake_gripper":
            state["open"] = self.gripper_open
        else:
            state["pose"] = dict(self.pose)
        return state

    def _note_goal_sent(self, action_id: str, inputs: dict[str, Any], idem_key: str | None) -> None:
        self.goal_sent_at = time.monotonic()
        self.goals_sent.append(
            {"action": action_id, "inputs": inputs, "idem_key": idem_key, "at": self.goal_sent_at}
        )

    async def run_blocking(
        self, action_id: str, inputs: dict[str, Any], handle: ExecuteHandle
    ) -> ActionResult:
        self._note_goal_sent(action_id, inputs, handle.idem_key)
        self.calls.append({"action": action_id, "inputs": inputs, "idem_key": handle.idem_key})
        if self.block_s:
            t0 = time.monotonic()
            while time.monotonic() - t0 < self.block_s:
                if handle.cancelled:
                    return ActionResult(success=False, message="cancelled")
                await asyncio.sleep(0.02)
        if action_id == "fake_note":
            return ActionResult(success=True, outputs={"ok": True})
        if action_id == "fake_read_pose":
            return ActionResult(
                success=True, outputs={"pose": dict(self.pose)}, measured={"pose": dict(self.pose)}
            )
        if action_id == "fake_tick":
            self.tick += 1
            return ActionResult(success=True, outputs={"n": self.tick}, measured={"n": self.tick})
        if action_id == "fake_camera":
            png = fake_png(64, 48, seed=self.tick)
            frame = image_payload(png, 64, 48)
            return ActionResult(
                success=True, outputs={"frame": frame}, measured={"bytes": frame["bytes"]}
            )
        if action_id == "fake_gripper":
            opened = _as_open(inputs)
            return self._commit_write(
                handle.idem_key, {"kind": "gripper", "open": opened}, {"open": opened}
            )
        raise KeyError(action_id)

    async def send_goal(
        self, action_id: str, inputs: dict[str, Any], handle: ExecuteHandle
    ) -> None:
        self._note_goal_sent(action_id, inputs, handle.idem_key)
        self.calls.append({"action": action_id, "inputs": inputs, "idem_key": handle.idem_key})
        if action_id != "fake_set_pose":
            raise KeyError(action_id)
        pose = _as_pose(inputs)

        async def job() -> None:
            try:
                for i in range(self.async_steps):
                    if handle.cancelled:
                        handle.result = ActionResult(success=False, message="cancelled")
                        handle.done = True
                        return
                    if handle.deadline is not None and time.time() >= handle.deadline:
                        handle.result = ActionResult(success=False, message="timeout")
                        handle.done = True
                        return
                    handle.progress = Progress(
                        percent=round((i + 1) / self.async_steps * 100),
                        text="adapter.pose_progress",
                        params={"i": i + 1, "n": self.async_steps},
                    )
                    await asyncio.sleep(float(getattr(self, "async_sleep_s", 0.04) or 0.04))
                handle.result = self._commit_write(
                    handle.idem_key, {"kind": "pose", **pose}, {"pose": pose}
                )
                if self.delay_after_commit:
                    await asyncio.sleep(self.delay_after_commit)
            except CrashBetweenWriteAndKey:
                handle.result = None
                raise
            finally:
                handle.done = True

        self._async_jobs[handle.id] = asyncio.create_task(job())

    async def _wait_async(
        self, handle: ExecuteHandle, on_progress: ProgressFn | None
    ) -> ActionResult:
        task = self._async_jobs.get(handle.id)
        while not handle.done:
            if (
                handle.deadline is not None
                and time.time() >= handle.deadline
                and not handle.cancelled
            ):
                handle.result = handle.result or ActionResult(success=False, message="timeout")
                handle.done = True
                break
            if handle.progress and on_progress:
                await _emit(on_progress, handle.progress)
            await asyncio.sleep(0.03)
        if task:
            await task
        if handle.result is None:
            raise CrashBetweenWriteAndKey("killed between write and idempotency key")
        return handle.result

    def _commit_write(
        self, idem_key: str | None, record: dict[str, Any], outputs: dict[str, Any]
    ) -> ActionResult:
        if idem_key and idem_key in self.idem:
            return self.idem[idem_key]
        if self.crash_after_write_before_key:
            self.crash_after_write_before_key = False
            # One transaction: neither the write nor the key is kept.
            raise CrashBetweenWriteAndKey("killed between write and idempotency key")
        if record.get("kind") == "pose":
            self.pose = {"x": float(record["x"]), "y": float(record["y"]), "z": float(record["z"])}
        elif record.get("kind") == "gripper":
            self.gripper_open = bool(record["open"])
        self.writes.append(dict(record))
        result = ActionResult(success=True, outputs=outputs, measured=dict(outputs))
        if idem_key:
            self.idem[idem_key] = result
        return result

    def write_count(self) -> int:
        return len(self.writes)

    def idem_count(self) -> int:
        return len(self.idem)

    # Names used by older write-fixture tests
    def note_lines(self) -> list[str]:
        out = []
        for w in self.writes:
            if w.get("kind") == "pose":
                out.append(f"{w.get('x')},{w.get('y')},{w.get('z')}")
            else:
                out.append(str(w))
        return out

    @property
    def ledger(self) -> Any:
        class _L:
            def __init__(self, n: int):
                self._n = n

            def count(self) -> int:
                return self._n

        return _L(self.idem_count())


def _as_pose(inputs: dict[str, Any]) -> dict[str, float]:
    raw = inputs.get("target") if isinstance(inputs.get("target"), dict) else inputs
    return {
        "x": float((raw or {}).get("x") or inputs.get("x") or 0),
        "y": float((raw or {}).get("y") or inputs.get("y") or 0),
        "z": float((raw or {}).get("z") or inputs.get("z") or 0),
    }


def _as_open(inputs: dict[str, Any]) -> bool:
    if isinstance(inputs.get("target"), dict) and "open" in inputs["target"]:
        return bool(inputs["target"]["open"])
    if "open" in inputs:
        return bool(inputs["open"])
    return True
