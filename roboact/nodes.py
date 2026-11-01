from __future__ import annotations

import asyncio
import inspect
import json
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, create_model

from . import NODE_TIMEOUT_S
from .adapter import (
    ActionDescriptor,
    ActionResult,
    Adapter,
    CancelResult,
    PreconditionFailed,
    Progress,
    WaitAborted,
)
from .catalog import en, yes_tokens
from .util import compare, dump, get_path, sha256_text


class SideEffect(StrEnum):
    none = "none"
    read = "read"
    motion = "motion"
    write = "write"  # hand-written test nodes only; adapter actions may not use this


EXTERNAL_SIDES = {SideEffect.write, SideEffect.motion}
ADAPTER_SIDES = {"none", "read", "motion"}
REJECTED_ACTIONS: list[dict[str, Any]] = []


class AnyIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: Any = None


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunContext:
    def __init__(
        self,
        run_id: str,
        step: int,
        round: int,
        idempotency_key: str,
        data_dir: str,
        adapter: Any = None,
        decision: str | None = None,
        on_progress: Any = None,
        on_phase: Any = None,
        should_abort: Any = None,
    ):
        self.run_id = run_id
        self.step = step
        self.round = round
        self.idempotency_key = idempotency_key
        self.data_dir = data_dir
        self.adapter = adapter
        self.tools = adapter  # leftover name used by older fixtures
        self.decision = decision
        self.on_progress = on_progress
        self.on_phase = on_phase
        self.should_abort = should_abort


class NodeType:
    type_id: ClassVar[str]
    title: ClassVar[str]
    Inputs: ClassVar[type[BaseModel]] = Empty
    Outputs: ClassVar[type[BaseModel]] = Empty
    Params: ClassVar[type[BaseModel]] = Empty
    side_effect: ClassVar[SideEffect] = SideEffect.none
    side_effect_note: ClassVar[str] = ""
    confirm_text: ClassVar[str] = ""
    confirm_labels: ClassVar[dict[str, str]] = {}
    timeout_s: ClassVar[float] = NODE_TIMEOUT_S
    typical_s: ClassVar[float | None] = None
    icon: ClassVar[str] = ""
    palette: ClassVar[bool] = True
    fixture: ClassVar[bool] = False
    from_adapter: ClassVar[bool] = False
    adapter_id: ClassVar[str] = ""
    group: ClassVar[str] = "control"
    name: ClassVar[str] = ""
    labels: ClassVar[dict[str, str]] = {}
    action_desc: ClassVar[ActionDescriptor | None] = None

    def __init__(self) -> None:
        self._handle = None
        self.cancelled = False
        self.idem: str | None = None

    async def run(self, inputs: BaseModel, params: BaseModel, ctx: RunContext) -> BaseModel:
        raise NotImplementedError

    def mock(self, inputs: BaseModel, params: BaseModel) -> BaseModel:
        return self.Outputs()

    async def cancel(self) -> CancelResult:
        self.cancelled = True
        return CancelResult(accepted=True)

    def input_ports(self, params: dict[str, Any] | None) -> list[dict[str, Any]]:
        return _ports_from_model(self.Inputs)

    def output_ports(self, params: dict[str, Any] | None) -> list[dict[str, Any]]:
        return _ports_from_model(self.Outputs)


def _ports_from_model(model: type[BaseModel]) -> list[dict[str, Any]]:
    out = []
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra or {}
        ptype = extra.get("port_type") if isinstance(extra, dict) else None
        out.append(
            {
                "name": name,
                "json_type": ptype or "any",
                "label": field.title or name,
                "required": field.is_required(),
            }
        )
    return out


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


# --- built-in flow-control ---


class StartParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any] = Field(default_factory=dict, title="Initial data")


class StartOut(BaseModel):
    data: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Initial data")


class StartNode(NodeType):
    type_id = "start"
    title = en("node.start")
    Inputs = Empty
    Outputs = StartOut
    Params = StartParams
    icon = "node-start"

    async def run(self, inputs: BaseModel, params: StartParams, ctx: RunContext):
        return StartOut(data=params.data)


class EndIn(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Result")


class EndNode(NodeType):
    type_id = "end"
    title = en("node.end")
    Inputs = EndIn
    Outputs = Empty
    icon = "node-end"

    async def run(self, inputs: EndIn, params: BaseModel, ctx: RunContext):
        return Empty()


class ConstantParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any = Field(default="", title="Value")
    value_type: str = Field(default="string", title="Type")


class ConstantOut(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Value")


class ConstantNode(NodeType):
    type_id = "constant"
    title = en("node.constant")
    Inputs = Empty
    Outputs = ConstantOut
    Params = ConstantParams
    icon = "node-constant"

    async def run(self, inputs: BaseModel, params: ConstantParams, ctx: RunContext):
        raw, t = params.value, params.value_type or "string"
        if t == "number":
            val: Any = int(raw) if isinstance(raw, str) and "." not in raw else float(raw)
        elif t == "boolean":
            val = raw if isinstance(raw, bool) else str(raw).lower() in yes_tokens()
        elif t == "json":
            val = raw if isinstance(raw, (dict, list)) else json.loads(raw or "{}")
        else:
            val = "" if raw is None else str(raw)
        return ConstantOut(value=val)


class CondIn(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Input")


class CondOut(BaseModel):
    yes: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Yes")
    no: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="No")


class CondParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(default="", title="Field")
    op: str = Field(default="eq", title="Compare")
    compare: Any = Field(default="", title="Compare to")
    max_rounds: int | None = Field(default=None, title="Max rounds (off in v1)")


class ConditionNode(NodeType):
    type_id = "condition"
    title = en("node.condition")
    Inputs = CondIn
    Outputs = CondOut
    Params = CondParams
    icon = "node-condition"

    async def run(self, inputs: CondIn, params: CondParams, ctx: RunContext):
        left = get_path(inputs.value, params.field)
        taken = compare(left, params.op, params.compare)
        return CondOut(yes=inputs.value if taken else None, no=inputs.value if not taken else None)


class MergeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ports: list[str] = Field(default_factory=lambda: ["a", "b"], title="Input ports")


class MergeOut(BaseModel):
    merged: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Merged")


class MergeNode(NodeType):
    type_id = "merge"
    title = en("node.merge")
    Inputs = Empty
    Outputs = MergeOut
    Params = MergeParams
    icon = "node-merge"

    def input_ports(self, params: dict[str, Any] | None) -> list[dict[str, Any]]:
        names = list((params or {}).get("ports") or ["a", "b"])
        return [{"name": n, "json_type": "any", "label": n, "required": True} for n in names]

    async def run(self, inputs: BaseModel, params: MergeParams, ctx: RunContext):
        raw = inputs.model_dump() if hasattr(inputs, "model_dump") else dict(inputs)
        return MergeOut(merged={n: raw.get(n) for n in (params.ports or raw.keys())})


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="To confirm")


class ConfirmOut(BaseModel):
    pass_: Any = Field(
        default=None, alias="pass", json_schema_extra={"port_type": "any"}, title="Approved"
    )
    reject: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Rejected")
    model_config = ConfigDict(populate_by_name=True)


class ConfirmParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(default=en("confirm.default"), title="Prompt")


class ConfirmNode(NodeType):
    type_id = "human_confirm"
    title = en("node.human_confirm")
    Inputs = ConfirmIn
    Outputs = ConfirmOut
    Params = ConfirmParams
    icon = "node-approval"

    async def run(self, inputs: ConfirmIn, params: ConfirmParams, ctx: RunContext):
        decision = getattr(ctx, "decision", None)
        if decision == "approve":
            return ConfirmOut(**{"pass": inputs.value, "reject": None})
        if decision == "reject":
            return ConfirmOut(**{"pass": None, "reject": inputs.value})
        raise RuntimeError("human confirm has no decision")


# --- test fixtures (not on the palette) ---


class EchoIn(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Value")


class EchoOut(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Value")


class EchoNode(NodeType):
    type_id = "echo"
    title = en("node.echo")
    Inputs = EchoIn
    Outputs = EchoOut
    palette = False
    fixture = True
    group = "test fixture"
    icon = "node-constant"

    async def run(self, inputs: EchoIn, params: BaseModel, ctx: RunContext):
        return EchoOut(value=inputs.value)


class IncIn(BaseModel):
    value: Any = Field(default=0, json_schema_extra={"port_type": "any"}, title="Value")


class IncOut(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Value")


class IncNode(NodeType):
    type_id = "inc"
    title = en("node.inc")
    Inputs = IncIn
    Outputs = IncOut
    palette = False
    fixture = True
    group = "test fixture"
    icon = "node-constant"

    async def run(self, inputs: IncIn, params: BaseModel, ctx: RunContext):
        v = inputs.value
        if isinstance(v, dict) and "value" in v:
            raw = v.get("value") or 0
            if not isinstance(raw, (int, float, str)):
                raw = 0
            n = int(raw) + 1
            return IncOut(value={**v, "value": n})
        if not isinstance(v, (int, float, str)) and v is not None:
            v = 0
        return IncOut(value={"value": int(v or 0) + 1})


class NopNode(NodeType):
    type_id = "nop"
    title = en("node.nop")
    Inputs = Empty
    Outputs = Empty
    palette = False
    fixture = True
    group = "test fixture"
    icon = "node-constant"

    async def run(self, inputs: BaseModel, params: BaseModel, ctx: RunContext):
        await asyncio.sleep(0)
        return Empty()


class SleepParams(BaseModel):
    seconds: float = Field(default=2.0, title="seconds")


class SleepNode(NodeType):
    type_id = "sleep"
    title = en("node.sleep")
    Params = SleepParams
    palette = False
    fixture = True
    group = "test fixture"
    icon = "node-constant"
    timeout_s = 0.2

    async def run(self, inputs: BaseModel, params: SleepParams, ctx: RunContext):
        await asyncio.sleep(float(params.seconds))
        return Empty()


class FailParams(BaseModel):
    message: str = Field(default="tool failed (stub)", title="Error")


class FailNode(NodeType):
    type_id = "fail"
    title = en("node.fail")
    Inputs = AnyIn
    Outputs = IncOut
    Params = FailParams
    palette = False
    fixture = True
    group = "test fixture"
    icon = "node-tool"

    async def run(self, inputs: BaseModel, params: FailParams, ctx: RunContext):
        raise RuntimeError(params.message)


class ToolIn(BaseModel):
    args: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Request")
    tool_name: str | None = Field(default=None, title="Tool")


class ToolOut(BaseModel):
    result: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="Result")


class ToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(default="clock", title="Tool")
    allowed_tools: list[str] = Field(default_factory=list, title="Allowed tools")


class ToolNode(NodeType):
    """Fixture only. Product nodes come from Adapter.list_actions()."""

    type_id = "tool_call"
    title = en("node.tool_call")
    Inputs = ToolIn
    Outputs = ToolOut
    Params = ToolParams
    side_effect = SideEffect.write
    palette = False
    fixture = True
    group = "test fixture"
    icon = "node-tool"
    timeout_s = NODE_TIMEOUT_S

    async def run(self, inputs: ToolIn, params: ToolParams, ctx: RunContext):
        name = params.tool or inputs.tool_name or "clock"
        raw = inputs.args if isinstance(inputs.args, dict) else {"value": inputs.args}
        result = await ctx.tools.call(name, raw or {}, ctx.idempotency_key)
        return ToolOut(result=result)

    def mock(self, inputs: BaseModel, params: BaseModel):
        return ToolOut(result={"mocked": True})


REGISTRY: dict[str, type[NodeType]] = {
    cls.type_id: cls
    for cls in (
        StartNode,
        EndNode,
        ConstantNode,
        ConditionNode,
        MergeNode,
        ConfirmNode,
        EchoNode,
        IncNode,
        NopNode,
        SleepNode,
        FailNode,
        ToolNode,
    )
}

_bound_adapter: Adapter | None = None


def register_node_type(cls: type[NodeType]) -> None:
    REGISTRY[cls.type_id] = cls


def get_type(type_id: str) -> type[NodeType]:
    if type_id not in REGISTRY:
        raise KeyError(f"unknown node type: {type_id}")
    return REGISTRY[type_id]


def get_instance(type_id: str) -> NodeType:
    return get_type(type_id)()


def known_type(type_id: str) -> bool:
    return type_id in REGISTRY


def _models_from_desc(desc: ActionDescriptor) -> tuple[type[BaseModel], type[BaseModel]]:
    in_fields: dict[str, Any] = {}
    for p in desc.params:
        extra: dict[str, Any] = {"port_type": p.type if p.type != "number" else "any"}
        schema_type = _param_json_type(p.type)
        if schema_type is not None:
            extra["type"] = schema_type
        if p.enum:
            extra["enum"] = list(p.enum)
        if p.range:
            extra["minimum"] = p.range[0]
            extra["maximum"] = p.range[1]
        default = (
            ...
            if p.required and p.default is None
            else (p.default if p.default is not None else None)
        )
        in_fields[p.name] = (
            Any,
            Field(default=default, title=p.title or p.name, json_schema_extra=extra),
        )
    out_fields: dict[str, Any] = {}
    for o in desc.outputs:
        extra_out: dict[str, Any] = {"port_type": o.type}
        out_fields[o.name] = (
            Any,
            Field(default=None, title=o.title or o.name, json_schema_extra=extra_out),
        )
    Inputs = (
        create_model(f"{desc.id}_In", __config__=ConfigDict(extra="allow"), **in_fields)
        if in_fields
        else Empty
    )
    Outputs = create_model(f"{desc.id}_Out", **out_fields) if out_fields else Empty
    return Inputs, Outputs


def _param_json_type(declared: str) -> str | None:
    kind = (declared or "").lower()
    mapping = {
        "array": "array",
        "bool": "boolean",
        "boolean": "boolean",
        "float": "number",
        "image": "object",
        "int": "integer",
        "integer": "integer",
        "json": "object",
        "null": "null",
        "number": "number",
        "object": "object",
        "str": "string",
        "string": "string",
        "text": "string",
    }
    return mapping.get(kind)


def _as_action_dict(raw: Any) -> dict[str, Any]:
    if hasattr(raw, "model_dump"):
        data = raw.model_dump()
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        data = dict(raw)
    if "expiry" not in data and data.get("deadline_enforced_by"):
        data["expiry"] = data["deadline_enforced_by"]
    data.pop("deadline_enforced_by", None)
    if data.get("return_mode") == "async":
        data["return_mode"] = "goal_progress"
    cancel = data.get("cancel")
    if (
        isinstance(cancel, dict)
        and cancel.get("after") is None
        and cancel.get("post_cancel") is not None
    ):
        data["cancel"] = {**cancel, "after": cancel["post_cancel"]}
    return data


def _settle_cancel(data: dict[str, Any]) -> dict[str, Any] | None:
    """Hold, or non-cancellable (runs to the end, then holds).

    `finish_step` means non-cancellable. `safe_pose` is refused and the
    reason names the action. Called at load, before the action is registered.
    """
    cancel = data.get("cancel")
    if not isinstance(cancel, dict):
        return None
    cancel = dict(cancel)
    data["cancel"] = cancel
    aid = str(data.get("id") or "")
    title = str(data.get("title") or data.get("name") or aid or "?")
    named = aid or title
    for key in ("post_cancel", "after"):
        if cancel.get(key) == "safe_pose":
            return {
                "id": aid,
                "title": title,
                "missing": [f"cancel.{key}"],
                "reason": f"{named}: safe_pose is refused",
            }
    if cancel.get("post_cancel") == "finish_step" or cancel.get("after") == "finish_step":
        cancel["cancellable"] = False
        cancel["post_cancel"] = "hold"
        cancel["after"] = "hold"
    for key in ("post_cancel", "after"):
        val = cancel.get(key)
        if val not in (None, "hold"):
            return {
                "id": aid,
                "title": title,
                "missing": [f"cancel.{key}"],
                "reason": (
                    f"{named}: post_cancel must be hold, or the action must be non-cancellable"
                ),
            }
    if cancel.get("cancellable") is False and cancel.get("post_cancel") in (None, "hold"):
        cancel["post_cancel"] = "hold"
    return None


def ingest_action(
    raw: Any, adapter: Adapter
) -> tuple[type[NodeType] | None, dict[str, Any] | None]:
    """Turn one list_actions() item into a node type, or a rejected_actions row.

    Adapter actions may only declare side_effect none | read | motion (missing = motion).
    write is reserved for hand-written test nodes and is rejected here.
    """
    data = _as_action_dict(raw)
    refused = _settle_cancel(data)
    if refused:
        return None, refused
    aid = data.get("id") or ""
    title = data.get("title") or aid or "?"
    se = data.get("side_effect")
    if se == "write":
        return None, {
            "id": aid,
            "title": title,
            "missing": ["side_effect"],
            "reason": "side_effect cannot be write (test nodes only)",
        }
    if se in (None, ""):
        data["side_effect"] = "motion"
        se = "motion"
    if se not in ADAPTER_SIDES:
        return None, {
            "id": aid,
            "title": title,
            "missing": ["side_effect"],
            "reason": f"side_effect cannot be {se} (adapter may report none / read / motion)",
        }
    if not data.get("title"):
        data["title"] = title
    if not data.get("version"):
        data["version"] = "0"
    missing: list[str] = []
    for field in ("id", "return_mode", "timeout_s"):
        if field not in data or data.get(field) in (None, ""):
            missing.append(field)
    if not data.get("outputs") and not data.get("result"):
        missing.append("result")
    cancel = data.get("cancel")
    if not isinstance(cancel, dict) or "cancellable" not in cancel:
        missing.append("cancel.cancellable")
    if se == "motion":
        if data.get("expiry") in (None, ""):
            missing.append("expiry")
        if "read_state" not in data:
            missing.append("read_state")
        if isinstance(cancel, dict):
            if cancel.get("after") is None and cancel.get("post_cancel") is None:
                missing.append("cancel.after")
        elif "cancel.cancellable" not in missing:
            missing.append("cancel.after")
    if missing:
        return None, {
            "id": aid,
            "title": title,
            "missing": missing,
            "reason": "missing " + ", ".join(missing),
        }
    try:
        desc = (
            raw
            if isinstance(raw, ActionDescriptor) and se == raw.side_effect
            else ActionDescriptor.model_validate(data)
        )
    except Exception as exc:
        return None, {
            "id": aid,
            "title": title,
            "missing": ["invalid"],
            "reason": str(exc),
        }
    return make_action_node(desc, adapter), None


def make_action_node(desc: ActionDescriptor, adapter: Adapter) -> type[NodeType]:
    in_model, out_model = _models_from_desc(desc)
    side = SideEffect(desc.side_effect) if desc.side_effect in ADAPTER_SIDES else SideEffect.motion

    class ActionNode(NodeType):
        type_id = desc.id
        title = desc.title
        name = desc.name or desc.id
        labels = desc.labels or {"en": desc.name or desc.id, "zh-CN": desc.title}
        Inputs = in_model
        Outputs = out_model
        Params = in_model
        side_effect = side
        side_effect_note = desc.side_effect_note
        confirm_text = desc.confirm_text
        confirm_labels = desc.confirm_labels
        timeout_s = desc.timeout_s
        typical_s = desc.typical_s
        icon = desc.icon or ("node-tool" if side == SideEffect.motion else "node-constant")
        palette = desc.id != "fake_note"
        from_adapter = True
        adapter_id = adapter.adapter_id
        group = adapter.name
        action_desc = desc

        async def run(self, inputs: BaseModel, params: BaseModel, ctx: RunContext):
            raw = inputs.model_dump() if hasattr(inputs, "model_dump") else dict(inputs or {})
            self.idem = ctx.idempotency_key

            async def _prog(p: Progress) -> None:
                if ctx.on_progress:
                    await ctx.on_progress(p)

            bound = ctx.adapter if ctx.adapter is not None else adapter
            exec_task = asyncio.create_task(
                bound.execute(
                    desc.id,
                    raw,
                    on_progress=_prog,
                    idem_key=ctx.idempotency_key,
                    deadline=None,
                    on_phase=ctx.on_phase,
                    should_abort=ctx.should_abort,
                )
            )
            await asyncio.sleep(0)
            self._handle = (
                bound.handle_by_idem(ctx.idempotency_key)
                if hasattr(bound, "handle_by_idem")
                else None
            )
            result: ActionResult = await exec_task
            self._handle = (
                bound.handle_by_idem(ctx.idempotency_key)
                if hasattr(bound, "handle_by_idem")
                else self._handle
            )
            if not result.success:
                if result.message == "precondition_failed" or (result.message or "").startswith(
                    "precondition"
                ):
                    raise PreconditionFailed(result.message, result.measured)
                if result.message == "wait_aborted":
                    raise WaitAborted("user")
                raise RuntimeError(result.message or "action_failed")
            return out_model.model_validate(result.outputs)

        def mock(self, inputs: BaseModel, params: BaseModel):
            return out_model()

        async def cancel(self) -> CancelResult:
            self.cancelled = True
            bound = adapter
            handle = self._handle
            if handle is None and hasattr(bound, "handle_by_idem"):
                handle = bound.handle_by_idem(self.idem)
            if handle is not None:
                self._handle = handle
            return await bound.cancel(handle)

    ActionNode.__name__ = f"Action_{desc.id}"
    ActionNode.__qualname__ = ActionNode.__name__
    return ActionNode


def bind_adapter(adapter: Adapter) -> Adapter:
    global _bound_adapter
    _bound_adapter = adapter
    REJECTED_ACTIONS.clear()
    adapter.rejected_actions = []
    for tid in list(REGISTRY):
        if getattr(REGISTRY[tid], "from_adapter", False):
            del REGISTRY[tid]
    if adapter.connected:
        for raw in adapter.list_actions():
            cls, rej = ingest_action(raw, adapter)
            if rej:
                REJECTED_ACTIONS.append(rej)
            elif cls is not None:
                register_node_type(cls)
    adapter.rejected_actions = list(REJECTED_ACTIONS)
    return adapter


def rejected_actions(adapter: Any = None) -> list[dict[str, Any]]:
    src = adapter if adapter is not None else _bound_adapter
    return list(getattr(src, "rejected_actions", None) or REJECTED_ACTIONS)


_SRC_HASH: dict[str, str] = {}


def config_hash(node: dict[str, Any]) -> str:
    cls = get_type(node["type"])
    key = cls.type_id
    src_h = _SRC_HASH.get(key)
    if src_h is None:
        try:
            src = inspect.getsource(cls)
        except (OSError, TypeError):
            desc = getattr(cls, "action_desc", None)
            src = dump(
                desc.model_dump()
                if desc is not None
                else {"type_id": cls.type_id, "title": cls.title}
            )
        src_h = sha256_text(src)
        _SRC_HASH[key] = src_h
    payload = dump({"type": cls.type_id, "src": src_h, "params": node.get("params") or {}})
    return sha256_text(payload)


def as_public(cls: type[NodeType], adapter: Any = None) -> dict[str, Any]:
    inst = cls()
    desc = cls.action_desc
    return {
        "type_id": cls.type_id,
        "type": cls.type_id,
        "title": cls.title,
        "label": cls.title,
        "name": cls.name or cls.type_id,
        "labels": cls.labels or {"en": cls.name or cls.type_id, "zh-CN": cls.title},
        "side_effect": cls.side_effect.value,
        "shows_write_mark": cls.side_effect in EXTERNAL_SIDES,
        "side_effect_note": cls.side_effect_note,
        "confirm_text": cls.confirm_text,
        "confirm_labels": dict(cls.confirm_labels),
        "timeout_s": cls.timeout_s,
        "typical_s": cls.typical_s,
        "icon": cls.icon,
        "palette": cls.palette and not cls.fixture,
        "fixture": cls.fixture,
        "from_adapter": cls.from_adapter,
        "adapter_id": cls.adapter_id,
        "group": cls.group,
        "action": desc.model_dump() if desc else None,
        "inputs": inst.input_ports(None),
        "outputs": inst.output_ports(None),
        "inputs_schema": _schema(cls.Inputs),
        "outputs_schema": _schema(cls.Outputs),
        "params_schema": _schema(cls.Params),
        "default_params": cls.Params().model_dump() if cls.Params is not Empty else {},
        "dynamic_inputs": "ports" if cls.type_id == "merge" else None,
    }


def list_specs(adapter: Any = None) -> list[dict[str, Any]]:
    return [as_public(c, adapter) for c in REGISTRY.values()]


def parse_inputs(
    type_id: str, raw: dict[str, Any] | None, params: dict[str, Any] | None = None
) -> BaseModel:
    cls = get_type(type_id)
    if type_id == "merge":
        return _dyn_model(raw or {})
    try:
        return cls.Inputs.model_validate(raw or {})
    except Exception:
        return _dyn_model(raw or {})


def _dyn_model(raw: dict[str, Any]) -> BaseModel:
    class Dyn(BaseModel):
        model_config = ConfigDict(extra="allow")

    return Dyn.model_validate(raw)


def parse_params(type_id: str, raw: dict[str, Any] | None) -> BaseModel:
    return get_type(type_id).Params.model_validate(raw or {})


def dump_outputs(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True)
    return dict(model)
