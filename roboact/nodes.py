from __future__ import annotations

import asyncio
import inspect
import json
import time
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, create_model

from . import NODE_TIMEOUT_S
from .adapter import ActionDescriptor, Adapter, ActionResult, CancelResult, Progress
from .util import compare, dump, get_path, sha256_text


class SideEffect(str, Enum):
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


class NodeType:
    type_id: ClassVar[str]
    title: ClassVar[str]
    Inputs: ClassVar[type[BaseModel]] = Empty
    Outputs: ClassVar[type[BaseModel]] = Empty
    Params: ClassVar[type[BaseModel]] = Empty
    side_effect: ClassVar[SideEffect] = SideEffect.none
    side_effect_note: ClassVar[str] = ""
    confirm_text: ClassVar[str] = ""
    timeout_s: ClassVar[float] = NODE_TIMEOUT_S
    typical_s: ClassVar[float | None] = None
    icon: ClassVar[str] = ""
    palette: ClassVar[bool] = True
    fixture: ClassVar[bool] = False
    from_adapter: ClassVar[bool] = False
    adapter_id: ClassVar[str] = ""
    group: ClassVar[str] = "流程控制"
    action_desc: ClassVar[ActionDescriptor | None] = None

    def __init__(self) -> None:
        self._handle = None
        self.cancelled = False

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
        out.append({
            "name": name,
            "json_type": ptype or "any",
            "label": field.title or name,
            "required": field.is_required(),
        })
    return out


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


# --- built-in flow-control ---

class StartParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any] = Field(default_factory=dict, title="初始数据")


class StartOut(BaseModel):
    data: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="初始数据")


class StartNode(NodeType):
    type_id = "start"
    title = "开始"
    Inputs = Empty
    Outputs = StartOut
    Params = StartParams
    icon = "node-start"

    async def run(self, inputs, params: StartParams, ctx):
        return StartOut(data=params.data)


class OutputIn(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="结果")


class OutputNode(NodeType):
    type_id = "output"
    title = "输出"
    Inputs = OutputIn
    Outputs = Empty
    icon = "node-output"

    async def run(self, inputs: OutputIn, params, ctx):
        return Empty()


class ConstantParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any = Field(default="", title="值")
    value_type: str = Field(default="string", title="类型")


class ConstantOut(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="值")


class ConstantNode(NodeType):
    type_id = "constant"
    title = "常量"
    Inputs = Empty
    Outputs = ConstantOut
    Params = ConstantParams
    icon = "node-constant"

    async def run(self, inputs, params: ConstantParams, ctx):
        raw, t = params.value, params.value_type or "string"
        if t == "number":
            val: Any = int(raw) if isinstance(raw, str) and "." not in raw else float(raw)
        elif t == "boolean":
            val = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "是")
        elif t == "json":
            val = raw if isinstance(raw, (dict, list)) else json.loads(raw or "{}")
        else:
            val = "" if raw is None else str(raw)
        return ConstantOut(value=val)


class CondIn(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="输入")


class CondOut(BaseModel):
    yes: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="是")
    no: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="否")


class CondParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(default="", title="字段")
    op: str = Field(default="eq", title="比较")
    compare: Any = Field(default="", title="比较值")
    max_rounds: int | None = Field(default=None, title="最多 N 轮（未启用）")


class ConditionNode(NodeType):
    type_id = "condition"
    title = "条件判断"
    Inputs = CondIn
    Outputs = CondOut
    Params = CondParams
    icon = "node-condition"

    async def run(self, inputs: CondIn, params: CondParams, ctx):
        left = get_path(inputs.value, params.field)
        taken = compare(left, params.op, params.compare)
        return CondOut(yes=inputs.value if taken else None, no=inputs.value if not taken else None)


class MergeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ports: list[str] = Field(default_factory=lambda: ["a", "b"], title="输入端口")


class MergeOut(BaseModel):
    merged: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="合并结果")


class MergeNode(NodeType):
    type_id = "merge"
    title = "合并"
    Inputs = Empty
    Outputs = MergeOut
    Params = MergeParams
    icon = "node-merge"

    def input_ports(self, params: dict[str, Any] | None) -> list[dict[str, Any]]:
        names = list((params or {}).get("ports") or ["a", "b"])
        return [{"name": n, "json_type": "any", "label": n, "required": True} for n in names]

    async def run(self, inputs, params: MergeParams, ctx):
        raw = inputs.model_dump() if hasattr(inputs, "model_dump") else dict(inputs)
        return MergeOut(merged={n: raw.get(n) for n in (params.ports or raw.keys())})


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="待确认")


class ConfirmOut(BaseModel):
    pass_: Any = Field(default=None, alias="pass", json_schema_extra={"port_type": "any"}, title="通过")
    reject: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="驳回")
    model_config = ConfigDict(populate_by_name=True)


class ConfirmParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(default="请确认", title="提示")


class ConfirmNode(NodeType):
    type_id = "human_confirm"
    title = "人工确认"
    Inputs = ConfirmIn
    Outputs = ConfirmOut
    Params = ConfirmParams
    icon = "node-approval"

    async def run(self, inputs: ConfirmIn, params: ConfirmParams, ctx):
        decision = getattr(ctx, "decision", None)
        if decision == "approve":
            return ConfirmOut(**{"pass": inputs.value, "reject": None})
        if decision == "reject":
            return ConfirmOut(**{"pass": None, "reject": inputs.value})
        raise RuntimeError("人工确认尚未决定")


# --- test fixtures (not on the palette) ---

class EchoIn(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="值")


class EchoOut(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="值")


class EchoNode(NodeType):
    type_id = "echo"
    title = "回显"
    Inputs = EchoIn
    Outputs = EchoOut
    palette = False
    fixture = True
    group = "测试夹具"
    icon = "node-constant"

    async def run(self, inputs: EchoIn, params, ctx):
        return EchoOut(value=inputs.value)


class IncIn(BaseModel):
    value: Any = Field(default=0, json_schema_extra={"port_type": "any"}, title="值")


class IncOut(BaseModel):
    value: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="值")


class IncNode(NodeType):
    type_id = "inc"
    title = "递增"
    Inputs = IncIn
    Outputs = IncOut
    palette = False
    fixture = True
    group = "测试夹具"
    icon = "node-constant"

    async def run(self, inputs: IncIn, params, ctx):
        v = inputs.value
        if isinstance(v, dict) and "value" in v:
            n = int(v.get("value") or 0) + 1
            return IncOut(value={**v, "value": n})
        return IncOut(value={"value": int(v or 0) + 1})


class NopNode(NodeType):
    type_id = "nop"
    title = "空操作"
    Inputs = Empty
    Outputs = Empty
    palette = False
    fixture = True
    group = "测试夹具"
    icon = "node-constant"

    async def run(self, inputs, params, ctx):
        await asyncio.sleep(0)
        return Empty()


class SleepParams(BaseModel):
    seconds: float = Field(default=2.0, title="秒")


class SleepNode(NodeType):
    type_id = "sleep"
    title = "等待"
    Params = SleepParams
    palette = False
    fixture = True
    group = "测试夹具"
    icon = "node-constant"
    timeout_s = 0.2

    async def run(self, inputs, params: SleepParams, ctx):
        await asyncio.sleep(float(params.seconds))
        return Empty()


class FailParams(BaseModel):
    message: str = Field(default="工具失败（桩）", title="错误")


class FailNode(NodeType):
    type_id = "fail"
    title = "失败"
    Inputs = AnyIn
    Outputs = IncOut
    Params = FailParams
    palette = False
    fixture = True
    group = "测试夹具"
    icon = "node-tool"

    async def run(self, inputs, params: FailParams, ctx):
        raise RuntimeError(params.message)


class ToolIn(BaseModel):
    args: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="请求")
    tool_name: str | None = Field(default=None, title="工具名")


class ToolOut(BaseModel):
    result: Any = Field(default=None, json_schema_extra={"port_type": "any"}, title="结果")


class ToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(default="clock", title="工具")
    allowed_tools: list[str] = Field(default_factory=list, title="允许的工具")


class ToolNode(NodeType):
    """Fixture only. Product nodes come from Adapter.list_actions()."""

    type_id = "tool_call"
    title = "工具调用"
    Inputs = ToolIn
    Outputs = ToolOut
    Params = ToolParams
    side_effect = SideEffect.write
    palette = False
    fixture = True
    group = "测试夹具"
    icon = "node-tool"
    timeout_s = NODE_TIMEOUT_S

    async def run(self, inputs: ToolIn, params: ToolParams, ctx):
        name = params.tool or inputs.tool_name or "clock"
        raw = inputs.args if isinstance(inputs.args, dict) else {"value": inputs.args}
        result = await ctx.tools.call(name, raw or {}, ctx.idempotency_key)
        return ToolOut(result=result)

    def mock(self, inputs, params):
        return ToolOut(result={"mocked": True})


REGISTRY: dict[str, type[NodeType]] = {
    cls.type_id: cls
    for cls in (
        StartNode, OutputNode, ConstantNode, ConditionNode, MergeNode, ConfirmNode,
        EchoNode, IncNode, NopNode, SleepNode, FailNode, ToolNode,
    )
}

_BOUND_ADAPTER: Adapter | None = None


def register_node_type(cls: type[NodeType]) -> None:
    REGISTRY[cls.type_id] = cls


def get_type(type_id: str) -> type[NodeType]:
    if type_id not in REGISTRY:
        raise KeyError(f"未知节点类型: {type_id}")
    return REGISTRY[type_id]


def get_instance(type_id: str) -> NodeType:
    return get_type(type_id)()


def known_type(type_id: str) -> bool:
    return type_id in REGISTRY


def bound_adapter() -> Adapter | None:
    return _BOUND_ADAPTER


def _models_from_desc(desc: ActionDescriptor) -> tuple[type[BaseModel], type[BaseModel]]:
    in_fields: dict[str, Any] = {}
    for p in desc.params:
        extra = {"port_type": p.type if p.type != "number" else "any"}
        default = ... if p.required and p.default is None else (p.default if p.default is not None else None)
        in_fields[p.name] = (Any, Field(default=default, title=p.title or p.name, json_schema_extra=extra))
    out_fields: dict[str, Any] = {}
    for o in desc.outputs:
        extra = {"port_type": o.type}
        out_fields[o.name] = (Any, Field(default=None, title=o.title or o.name, json_schema_extra=extra))
    Inputs = create_model(f"{desc.id}_In", __config__=ConfigDict(extra="allow"), **in_fields) if in_fields else Empty
    Outputs = create_model(f"{desc.id}_Out", **out_fields) if out_fields else Empty
    return Inputs, Outputs


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
    if isinstance(cancel, dict):
        if cancel.get("after") is None and cancel.get("post_cancel") is not None:
            data["cancel"] = {**cancel, "after": cancel["post_cancel"]}
    return data


def ingest_action(raw: Any, adapter: Adapter) -> tuple[type[NodeType] | None, dict[str, Any] | None]:
    """Turn one list_actions() item into a node type, or a rejected_actions row.

    Adapter actions may only declare side_effect none | read | motion (missing = motion).
    write is reserved for hand-written test nodes and is rejected here.
    """
    data = _as_action_dict(raw)
    aid = data.get("id") or ""
    title = data.get("title") or aid or "?"
    se = data.get("side_effect")
    if se == "write":
        return None, {
            "id": aid,
            "title": title,
            "missing": ["side_effect"],
            "reason": "side_effect 不能是 write（仅测试节点可用）",
        }
    if se in (None, ""):
        data["side_effect"] = "motion"
        se = "motion"
    if se not in ADAPTER_SIDES:
        return None, {
            "id": aid,
            "title": title,
            "missing": ["side_effect"],
            "reason": f"side_effect 不能是 {se}（适配器只能报 none / read / motion）",
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
            "reason": "缺 " + "、".join(missing),
        }
    try:
        desc = raw if isinstance(raw, ActionDescriptor) and se == raw.side_effect else ActionDescriptor.model_validate(data)
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
    if desc.side_effect in ADAPTER_SIDES:
        side = SideEffect(desc.side_effect)
    else:
        side = SideEffect.motion

    class ActionNode(NodeType):
        type_id = desc.id
        title = desc.title
        Inputs = in_model
        Outputs = out_model
        Params = in_model
        side_effect = side
        side_effect_note = desc.side_effect_note
        confirm_text = desc.confirm_text
        timeout_s = desc.timeout_s
        typical_s = desc.typical_s
        icon = desc.icon or ("node-tool" if side == SideEffect.motion else "node-constant")
        palette = True
        from_adapter = True
        adapter_id = adapter.adapter_id
        group = adapter.name
        action_desc = desc

        async def run(self, inputs, params, ctx):
            raw = inputs.model_dump() if hasattr(inputs, "model_dump") else dict(inputs or {})
            self._idem = ctx.idempotency_key

            async def _prog(p: Progress) -> None:
                if ctx.on_progress:
                    await ctx.on_progress(p)

            bound = ctx.adapter if ctx.adapter is not None else adapter
            timeout_s = float(getattr(type(self), "timeout_s", None) or desc.timeout_s or NODE_TIMEOUT_S)
            deadline = time.time() + timeout_s
            exec_task = asyncio.create_task(bound.execute(
                desc.id, raw, on_progress=_prog, idem_key=ctx.idempotency_key, deadline=deadline,
            ))
            await asyncio.sleep(0)
            self._handle = bound.handle_by_idem(ctx.idempotency_key) if hasattr(bound, "handle_by_idem") else None
            result: ActionResult = await exec_task
            self._handle = bound.handle_by_idem(ctx.idempotency_key) if hasattr(bound, "handle_by_idem") else self._handle
            if not result.success:
                raise RuntimeError(result.message or "动作失败")
            return out_model.model_validate(result.outputs)

        def mock(self, inputs, params):
            return out_model()

        async def cancel(self) -> CancelResult:
            self.cancelled = True
            bound = adapter
            handle = self._handle
            if handle is None and hasattr(bound, "handle_by_idem"):
                handle = bound.handle_by_idem(getattr(self, "_idem", None))
            return await bound.cancel(handle)

    ActionNode.__name__ = f"Action_{desc.id}"
    ActionNode.__qualname__ = ActionNode.__name__
    return ActionNode


def bind_adapter(adapter: Adapter) -> Adapter:
    global _BOUND_ADAPTER, REJECTED_ACTIONS
    _BOUND_ADAPTER = adapter
    REJECTED_ACTIONS = []
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
    src = adapter if adapter is not None else _BOUND_ADAPTER
    return list(getattr(src, "rejected_actions", None) or REJECTED_ACTIONS)


def config_hash(node: dict[str, Any]) -> str:
    cls = get_type(node["type"])
    try:
        src = inspect.getsource(cls)
    except (OSError, TypeError):
        desc = getattr(cls, "action_desc", None)
        src = dump(desc.model_dump() if desc is not None else {"type_id": cls.type_id, "title": cls.title})
    payload = dump({"type": cls.type_id, "src": sha256_text(src), "params": node.get("params") or {}})
    return sha256_text(payload)


def as_public(cls: type[NodeType], adapter: Any = None) -> dict[str, Any]:
    inst = cls()
    desc = cls.action_desc
    return {
        "type_id": cls.type_id,
        "type": cls.type_id,
        "title": cls.title,
        "label": cls.title,
        "side_effect": cls.side_effect.value,
        "shows_write_mark": cls.side_effect in EXTERNAL_SIDES,
        "side_effect_note": cls.side_effect_note,
        "confirm_text": cls.confirm_text,
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


def parse_inputs(type_id: str, raw: dict[str, Any] | None, params: dict[str, Any] | None = None) -> BaseModel:
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
