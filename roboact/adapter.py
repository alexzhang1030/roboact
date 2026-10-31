from __future__ import annotations

import asyncio
import base64
import struct
import time
import zlib
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .util import now_iso


ProgressFn = Callable[["Progress"], None | Awaitable[None]]


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str = "any"
    unit: str | None = None
    range: tuple[float, float] | None = None
    enum: list[Any] | None = None
    default: Any = None
    required: bool = True
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
    read_state: bool = False
    expiry: Literal["controller", "adapter_only"] = "controller"
    side_effect_note: str = ""
    confirm_text: str = ""
    icon: str = ""


class Progress(BaseModel):
    percent: float | None = None
    text: str | None = None
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
    def __init__(self, action_id: str, idem_key: str | None, cancellable: bool, post_cancel: str) -> None:
        self.id = f"{action_id}:{idem_key or id(self)}"
        self.action_id = action_id
        self.idem_key = idem_key
        self.cancellable = cancellable
        self.post_cancel = post_cancel
        self.cancelled = False
        self.cancelled_by_lost_heartbeat = False
        self.done = False
        self.result: ActionResult | None = None
        self.progress: Progress | None = None
        self.deadline: float | None = None
        self.return_mode: str = "blocking"


class CrashBetweenWriteAndKey(Exception):
    """Test hook: died after the side effect and before the idempotency key."""


class Adapter:
    """Controller-specific implementation. The canvas never sees SDK vs ROS 2."""

    adapter_id: str = "adapter"
    name: str = "适配器"
    version: str = "0.2"
    target: Literal["sim", "real"] = "sim"
    action_list_version: str = "v0"
    connected: bool = True
    heartbeat_timeout_s: float = 3.0

    def __init__(self) -> None:
        self.fetched_at: str | None = None
        self._handles: dict[str, ExecuteHandle] = {}
        self._finished: dict[str, ExecuteHandle] = {}
        self.reject_next_cancel = False
        self.force_uncancellable = False
        self._last_backend_beat = time.monotonic()
        self._watchdog_task: asyncio.Task | None = None
        self._lost_backend_fired = False
        self.goals_cancelled_by_lost_heartbeat = 0

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
        raise KeyError(f"未知动作: {action_id}")

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
            "connected": self.connected,
            "status": "disconnected" if not self.connected else ("empty" if not actions else "ok"),
            "source": f"{self.name} · {self.target}",
            "fetched_at": self.fetched_at,
            "actions": actions,
            "rejected_actions": list(getattr(self, "rejected_actions", []) or []),
        }

    def receive_heartbeat(self) -> None:
        """Backend → adapter heartbeat. Missing beats cancel in-flight goals."""
        self._last_backend_beat = time.monotonic()
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
            try:
                await task
            except asyncio.CancelledError:
                pass

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
            out.append({
                "id": h.id,
                "action_id": h.action_id,
                "idem_key": h.idem_key,
                "return_mode": h.return_mode,
                "cancelled": h.cancelled,
                "done": h.done,
            })
        return out

    async def goal_status(self, handle_id: str | None = None, idem_key: str | None = None) -> dict[str, Any]:
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

    async def execute(
        self,
        action_id: str,
        inputs: dict[str, Any],
        on_progress: ProgressFn | None = None,
        idem_key: str | None = None,
        deadline: float | None = None,
    ) -> ActionResult:
        desc = self.descriptor(action_id)
        handle = ExecuteHandle(action_id, idem_key, desc.cancel.cancellable, desc.cancel.post_cancel)
        handle.deadline = deadline
        handle.return_mode = "goal_progress" if desc.return_mode in ("goal_progress", "async") else desc.return_mode
        self._handles[handle.id] = handle
        try:
            if desc.return_mode in ("goal_progress", "async"):
                await self.send_goal(action_id, inputs, handle)
                return await self._wait_async(handle, on_progress)
            if on_progress:
                await _emit(on_progress, Progress(text="执行中"))
            result = await self.run_blocking(action_id, inputs, handle)
            handle.done = True
            handle.result = result
            return result
        finally:
            self._finished[handle.id] = handle
            if handle.done:
                self._handles.pop(handle.id, None)

    async def send_goal(self, action_id: str, inputs: dict[str, Any], handle: ExecuteHandle) -> None:
        raise NotImplementedError

    async def poll_progress(self, handle: ExecuteHandle) -> Progress | None:
        return handle.progress

    async def get_result(self, handle: ExecuteHandle) -> ActionResult:
        if handle.result is None:
            raise RuntimeError("结果还没有准备好")
        return handle.result

    async def run_blocking(self, action_id: str, inputs: dict[str, Any], handle: ExecuteHandle) -> ActionResult:
        raise NotImplementedError

    async def cancel(self, handle: ExecuteHandle | None) -> CancelResult:
        if self.reject_next_cancel:
            self.reject_next_cancel = False
            return CancelResult(accepted=False, reason="取消被拒")
        if handle is None:
            return CancelResult(accepted=False, reason="没有可取消的动作")
        if self.force_uncancellable or not handle.cancellable:
            return CancelResult(accepted=False, reason="这个动作不能取消")
        handle.cancelled = True
        return CancelResult(accepted=True, post_cancel=handle.post_cancel)

    async def read_state(self, action_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def _wait_async(self, handle: ExecuteHandle, on_progress: ProgressFn | None) -> ActionResult:
        while not handle.done:
            if handle.cancelled:
                handle.done = True
                handle.result = ActionResult(success=False, message="已取消")
                break
            if handle.deadline is not None and time.time() >= handle.deadline:
                handle.done = True
                handle.result = ActionResult(success=False, message="超时")
                break
            prog = await self.poll_progress(handle)
            if prog and on_progress:
                await _emit(on_progress, prog)
            await asyncio.sleep(0.04)
        return handle.result or ActionResult(success=False, message="没有结果")


async def _emit(fn: ProgressFn, progress: Progress) -> None:
    out = fn(progress)
    if asyncio.iscoroutine(out):
        await out


def fake_png(width: int = 64, height: int = 48, seed: int = 0) -> bytes:
    """Tiny generated PNG, not written to disk."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

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


def image_payload(png: bytes, width: int, height: int, captured_at: str | None = None) -> dict[str, Any]:
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
    if isinstance(value, dict) and (value.get("type") == "image" or value.get("mime", "").startswith("image/")):
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


# Hand-written per-action table. Missing side_effect ⇒ motion (enforced in ingest).
FAKE_ACTIONS: list[ActionDescriptor] = [
    ActionDescriptor(
        id="fake_read_pose",
        version="fake-v1",
        title="假·读位姿",
        params=[],
        outputs=[
            OutputSpec(name="pose", type="any", title="位姿"),
        ],
        side_effect="read",
        return_mode="blocking",
        timeout_s=5,
        typical_s=0.05,
        cancel=CancelSpec(cancellable=True, post_cancel="hold"),
        preconditions=[Precondition(text="仿真位姿可读")],
        read_state=False,
        expiry="controller",
        side_effect_note="会读外部：读仿真位姿",
        icon="node-output",
    ),
    ActionDescriptor(
        id="fake_set_pose",
        version="fake-v1",
        title="假·到点",
        params=[
            ParamSpec(name="target", type="any", required=False, title="目标"),
            ParamSpec(name="x", type="number", unit="m", range=(-2.0, 2.0), default=0.0, required=False, title="X"),
            ParamSpec(name="y", type="number", unit="m", range=(-2.0, 2.0), default=0.0, required=False, title="Y"),
            ParamSpec(name="z", type="number", unit="m", range=(0.0, 2.0), default=0.0, required=False, title="Z"),
        ],
        outputs=[OutputSpec(name="pose", type="any", title="到达位姿")],
        side_effect="motion",
        return_mode="goal_progress",
        timeout_s=60,
        typical_s=0.4,
        cancel=CancelSpec(cancellable=True, post_cancel="hold", after="hold", method="cancel_goal"),
        preconditions=[Precondition(text="仿真控制器已连接", check="connected")],
        read_state=True,
        expiry="controller",
        side_effect_note="会驱动仿真关节到目标点",
        confirm_text="将在仿真里把末端移到目标点。",
        icon="node-tool",
    ),
    ActionDescriptor(
        id="fake_gripper",
        version="fake-v1",
        title="假·夹爪",
        params=[
            ParamSpec(name="open", type="boolean", default=True, required=False, title="张开"),
            ParamSpec(name="target", type="any", required=False, title="目标"),
        ],
        outputs=[OutputSpec(name="open", type="boolean", title="张开")],
        side_effect="motion",
        return_mode="blocking",
        timeout_s=8,
        typical_s=0.2,
        cancel=CancelSpec(cancellable=True, post_cancel="safe_pose", after="safe_pose", method="cancel_goal"),
        preconditions=[Precondition(text="夹爪未被锁定")],
        read_state=True,
        expiry="controller",
        side_effect_note="会驱动仿真夹爪",
        confirm_text="将在仿真里开合夹爪。",
        icon="node-tool",
    ),
    ActionDescriptor(
        id="fake_tick",
        version="fake-v1",
        title="假·节拍",
        params=[],
        outputs=[OutputSpec(name="n", type="number", title="节拍")],
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
        title="假·相机",
        params=[],
        outputs=[OutputSpec(name="frame", type="image", title="画面")],
        side_effect="read",
        return_mode="blocking",
        timeout_s=5,
        typical_s=0.08,
        cancel=CancelSpec(cancellable=True, post_cancel="hold"),
        preconditions=[Precondition(text="仿真相机可用")],
        read_state=False,
        expiry="adapter_only",
        side_effect_note="会读外部：生成一张测试图，不写盘",
        icon="node-model",
    ),
]


class FakeAdapter(Adapter):
    """In-memory sim adapter. Actions are fixtures named fake_*. No real controller."""

    adapter_id = "fake"
    name = "假适配器"
    version = "0.2"
    target = "sim"
    action_list_version = "fake-v1"

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
        self.block_s = 0.0
        self.calls: list[dict[str, Any]] = []
        self._async_jobs: dict[str, asyncio.Task] = {}
        self.extra_raw_actions: list[Any] = []
        self.drop_action_ids: set[str] = set()

    def list_actions(self) -> list[Any]:
        out: list[Any] = []
        for a in FAKE_ACTIONS:
            if a.id in self.drop_action_ids:
                continue
            if self.force_uncancellable and a.id == "fake_gripper":
                out.append(a.model_copy(update={"cancel": CancelSpec(cancellable=False, post_cancel=a.cancel.post_cancel, after=a.cancel.after)}))
            else:
                out.append(a)
        out.extend(self.extra_raw_actions)
        return out

    async def read_state(self, action_id: str) -> dict[str, Any]:
        if action_id == "fake_gripper":
            return {"open": self.gripper_open}
        return {"pose": dict(self.pose)}

    async def run_blocking(self, action_id: str, inputs: dict[str, Any], handle: ExecuteHandle) -> ActionResult:
        self.calls.append({"action": action_id, "inputs": inputs, "idem_key": handle.idem_key})
        if self.block_s:
            t0 = time.monotonic()
            while time.monotonic() - t0 < self.block_s:
                if handle.cancelled:
                    return ActionResult(success=False, message="已取消")
                await asyncio.sleep(0.02)
        if action_id == "fake_read_pose":
            return ActionResult(success=True, outputs={"pose": dict(self.pose)}, measured={"pose": dict(self.pose)})
        if action_id == "fake_tick":
            self.tick += 1
            return ActionResult(success=True, outputs={"n": self.tick}, measured={"n": self.tick})
        if action_id == "fake_camera":
            png = fake_png(64, 48, seed=self.tick)
            frame = image_payload(png, 64, 48)
            return ActionResult(success=True, outputs={"frame": frame}, measured={"bytes": frame["bytes"]})
        if action_id == "fake_gripper":
            opened = _as_open(inputs)
            return self._commit_write(handle.idem_key, {"kind": "gripper", "open": opened}, {"open": opened})
        raise KeyError(action_id)

    async def send_goal(self, action_id: str, inputs: dict[str, Any], handle: ExecuteHandle) -> None:
        self.calls.append({"action": action_id, "inputs": inputs, "idem_key": handle.idem_key})
        if action_id != "fake_set_pose":
            raise KeyError(action_id)
        pose = _as_pose(inputs)

        async def job() -> None:
            try:
                for i in range(self.async_steps):
                    if handle.cancelled:
                        handle.result = ActionResult(success=False, message="已取消")
                        handle.done = True
                        return
                    if handle.deadline is not None and time.time() >= handle.deadline:
                        handle.result = ActionResult(success=False, message="超时")
                        handle.done = True
                        return
                    handle.progress = Progress(percent=round((i + 1) / self.async_steps * 100), text=f"到点 {i + 1}/{self.async_steps}")
                    await asyncio.sleep(0.04)
                handle.result = self._commit_write(handle.idem_key, {"kind": "pose", **pose}, {"pose": pose})
                if self.delay_after_commit:
                    await asyncio.sleep(self.delay_after_commit)
            except CrashBetweenWriteAndKey:
                handle.result = None
                raise
            finally:
                handle.done = True

        self._async_jobs[handle.id] = asyncio.create_task(job())

    async def _wait_async(self, handle: ExecuteHandle, on_progress: ProgressFn | None) -> ActionResult:
        task = self._async_jobs.get(handle.id)
        while not handle.done:
            if handle.deadline is not None and time.time() >= handle.deadline and not handle.cancelled:
                handle.result = handle.result or ActionResult(success=False, message="超时")
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

    def _commit_write(self, idem_key: str | None, record: dict[str, Any], outputs: dict[str, Any]) -> ActionResult:
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
