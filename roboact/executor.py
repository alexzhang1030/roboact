from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import time
from dataclasses import dataclass, field
from typing import Any

from . import ADAPTER_HEARTBEAT_S, DEFAULT_STEP_LIMIT, NODE_TIMEOUT_S, UI_LOST_TIMEOUT_S
from .adapter import (
    Adapter,
    CrashBetweenWriteAndKey,
    Progress,
    WaitAborted,
    image_summary,
)
from .errors import ApiError
from .flow_file import inspect_run
from .graph import (
    connected_types,
    cycle_exit_details,
    cycle_exit_errors,
    incoming,
    influencing_map,
    influencing_nodes,
    node_map,
    outgoing,
    types_compatible,
)
from .nodes import (
    NodeType,
    RunContext,
    SideEffect,
    config_hash,
    dump_outputs,
    get_instance,
    get_type,
    known_type,
    parse_inputs,
    parse_params,
)
from .sse import SseHub
from .store import StoreLike
from .util import compare, dump, get_path, new_id, now_iso, sha256_text

MOTION_RESOLVE_REASONS = {"motion_timeout", "adapter_lost", "restart"}

TERMINAL = {"completed", "error", "stopped", "step_limit"}
SEED_TYPES = {"start", "constant", "nop", "sleep"}


@dataclass
class RunCtrl:
    run_id: str
    kind: str
    flow: dict[str, Any]
    flow_id: str
    flow_version: int
    max_steps: int
    side_effects: str
    allowed: set[str] | None = None
    parent_run_id: str | None = None
    base_step: int = 0
    status: str = "running"
    steps_used: int = 0
    pause: dict[str, Any] | None = None
    resume: asyncio.Event = field(default_factory=asyncio.Event)
    step_mode: bool = False
    user_pause: bool = False
    stop_flag: bool = False
    node_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    cmd_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    t2_retry: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    edits: dict[str, Any] | None = None
    retry_keys: dict[str, str] = field(default_factory=dict)
    retry_of: dict[str, int] = field(default_factory=dict)
    last_paused_at_mono: float = 0.0
    input_source_hint: str | None = None
    entry_sources: dict[tuple[str, str], str] = field(default_factory=dict)
    base_rounds: dict[str, int] = field(default_factory=dict)
    unused_pins: list[dict[str, str]] = field(default_factory=list)
    ignore_bp: tuple[Any, Any] | None = None
    last_used_pause_id: str | None = None
    abandoned: bool = False
    current_inst: Any = None
    current_nid: str | None = None
    current_started: float = 0.0
    cancel_called: dict[str, bool] = field(default_factory=dict)
    crash_confirmed: set[str] = field(default_factory=set)
    needs_crash_confirm: set[str] = field(default_factory=set)
    images: dict[tuple[int, str], dict[str, Any]] = field(default_factory=dict)
    saw_ui: bool = False
    last_ui_at: float = 0.0
    not_cancelled: list[str] = field(default_factory=list)
    recovery: dict[str, Any] | None = None
    adapter_lost_flag: bool = False
    confirm: str | None = None
    done_counts: dict[str, int] = field(default_factory=dict)
    influencing: dict[str, set[str]] | None = None
    hash_cache: dict[str, str] = field(default_factory=dict)
    step_phase: str | None = None
    wait_started: float = 0.0
    wait_max_s: float | None = None

    @property
    def paused_node(self) -> str | None:
        return (self.pause or {}).get("node_id")

    @property
    def paused_phase(self) -> str | None:
        return (self.pause or {}).get("when")

    @property
    def current(self) -> Any:
        class C:
            def __init__(self, rnd: int):
                self.round = rnd

        return C((self.pause or {}).get("round") or 0)


class Executor:
    def __init__(self, store: StoreLike, adapter: Adapter, hub: SseHub, data_dir: str) -> None:
        self.store = store
        self.adapter = adapter
        self.tools = adapter
        self.hub = hub
        self.data_dir = data_dir
        self.runs: dict[str, RunCtrl] = {}
        self.crash_before_t2 = False
        self.hooks: dict[str, Any] = {}
        self.images: dict[str, dict[tuple[int, str], dict[str, Any]]] = {}
        self.ui_lost_s: float | None = UI_LOST_TIMEOUT_S
        self.adapter_heartbeat_s: float = ADAPTER_HEARTBEAT_S
        self.step_delay_s: float = 0.0
        self._hb_task: asyncio.Task | None = None
        self._last_adapter_ok = time.monotonic()

    def get(self, run_id: str) -> RunCtrl | None:
        return self.runs.get(run_id)

    # ----- start -----

    async def start_full(
        self,
        flow_id: str,
        flow_version: int,
        *,
        initial_input: dict | None = None,
        max_steps: int | None = None,
        client_id: str = "",
        request_id: str | None = None,
        kind: str = "full",
        node_id: str | None = None,
        node_ids: list[str] | None = None,
        side_effects: str = "default",
        parent_run_id: str | None = None,
        base_step: int = 0,
        seed_pending: list[dict] | None = None,
        confirm: str | None = None,
        input_source_hint: str | None = None,
        entry_sources: dict | None = None,
        base_rounds: dict | None = None,
    ) -> str:
        if request_id:
            existing = self.store.run_for_request(request_id)
            if existing:
                return existing
        graph = self.store.get_version(flow_id, flow_version)
        if graph is None:
            raise ApiError(
                400,
                "invalid_graph",
                "找不到这个流程版本。",
                {"flow_id": flow_id, "flow_version": flow_version},
            )
        graph = copy.deepcopy(graph)
        self._check_adapter(graph)
        self._validate_graph(graph)
        self._check_cycles(graph, kind, node_ids)
        if initial_input is not None:
            nodes = []
            for n in graph["nodes"]:
                if n["type"] == "start":
                    p = dict(n.get("params") or {})
                    p["data"] = initial_input
                    nodes.append({**n, "params": p})
                else:
                    nodes.append(n)
            graph = {**graph, "nodes": nodes}

        allowed = None
        if kind == "node":
            if not node_id:
                raise ApiError(400, "invalid_graph", "单节点运行需要 node_id")
            allowed = {node_id}
        elif kind == "group":
            if not node_ids:
                raise ApiError(400, "invalid_graph", "分组运行需要 node_ids")
            allowed = set(node_ids)

        run_id = new_id("rn_")
        unused = []
        if kind == "full":
            unused = [
                {"node_id": p["node_id"], "port": p["port"]} for p in self.store.list_pins(flow_id)
            ]
        ctrl = RunCtrl(
            run_id=run_id,
            kind=kind,
            flow=graph,
            flow_id=flow_id,
            flow_version=flow_version,
            max_steps=int(max_steps or DEFAULT_STEP_LIMIT),
            side_effects=side_effects,
            allowed=allowed,
            parent_run_id=parent_run_id,
            base_step=base_step,
            input_source_hint=input_source_hint,
            entry_sources=entry_sources or {},
            base_rounds=base_rounds or {},
            unused_pins=unused,
            confirm=confirm,
        )
        ctrl.influencing = influencing_map(graph)
        self.runs[run_id] = ctrl
        self.store.insert_run(
            {
                "run_id": run_id,
                "kind": kind,
                "flow_id": flow_id,
                "flow_version": flow_version,
                "parent_run_id": parent_run_id,
                "base_step": base_step,
                "status": "running",
                "max_steps": ctrl.max_steps,
                "steps_used": 0,
                "side_effects": side_effects,
            }
        )
        if request_id:
            self.store.remember_request(request_id, run_id)
        if seed_pending:
            for it in seed_pending:
                self.store.add_pending(
                    run_id,
                    it.get("edge_id") or "",
                    it["to_node"],
                    it["to_port"],
                    it["payload"],
                    it.get("produced_step") or 0,
                    it.get("retry_idem_key"),
                )
        ctrl.task = asyncio.create_task(self._loop(ctrl))
        return run_id

    async def start_node(
        self,
        flow_id: str,
        flow_version: int,
        node_id: str,
        input_spec: dict | None = None,
        side_effects: str = "default",
        confirm: str | None = None,
        client_id: str = "",
        request_id: str | None = None,
        max_steps: int | None = None,
    ) -> str:
        graph = self.store.get_version(flow_id, flow_version)
        if graph is None:
            raise ApiError(400, "invalid_graph", "找不到这个流程版本。")
        nodes = node_map(graph)
        if node_id not in nodes:
            raise ApiError(400, "invalid_graph", f"找不到节点 {node_id}")
        node = nodes[node_id]
        if side_effects == "real" or (
            known_type(node["type"])
            and get_type(node["type"]).side_effect == SideEffect.motion
            and getattr(self.adapter, "target", "sim") == "real"
        ):
            self._check_confirm(node, confirm)
        inputs, source = self._resolve_node_input(flow_id, node, input_spec)
        seed = [
            {"to_node": node_id, "to_port": k, "payload": v, "edge_id": "", "produced_step": 0}
            for k, v in (inputs or {}).items()
        ]
        return await self.start_full(
            flow_id,
            flow_version,
            kind="node",
            node_id=node_id,
            seed_pending=seed,
            side_effects=side_effects,
            confirm=confirm,
            client_id=client_id,
            request_id=request_id,
            max_steps=max_steps or 1,
            input_source_hint=source,
        )

    async def start_group(
        self,
        flow_id: str,
        flow_version: int,
        node_ids: list[str],
        entries: dict | None = None,
        side_effects: str = "default",
        confirm: str | None = None,
        client_id: str = "",
        request_id: str | None = None,
        max_steps: int | None = None,
        source_run_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        graph = self.store.get_version(flow_id, flow_version)
        if graph is None:
            raise ApiError(400, "invalid_graph", "找不到这个流程版本。")
        allowed = set(node_ids)
        echo: dict[str, Any] = {}
        seed: list[dict] = []
        entry_sources: dict[tuple[str, str], str] = {}
        for e in graph.get("edges") or []:
            if e["target"] not in allowed or e["source"] in allowed:
                continue
            spec = (entries or {}).get(e.get("id") or "") or {}
            val, source = self._resolve_entry(flow_id, graph, e, spec, source_run_id)
            seed.append(
                {
                    "edge_id": e.get("id") or "",
                    "to_node": e["target"],
                    "to_port": e.get("target_port") or "in",
                    "payload": val,
                    "produced_step": 0,
                }
            )
            entry_sources[(e["target"], e.get("target_port") or "in")] = source
            echo[e.get("id") or f"{e['source']}-{e['target']}"] = {"source": source, "value": val}
        if side_effects == "real":
            for nid in node_ids:
                self._check_confirm(node_map(graph)[nid], confirm)
        rid = await self.start_full(
            flow_id,
            flow_version,
            kind="group",
            node_ids=node_ids,
            seed_pending=seed,
            side_effects=side_effects,
            confirm=confirm,
            client_id=client_id,
            request_id=request_id,
            max_steps=max_steps,
            entry_sources=entry_sources,
        )
        return rid, echo

    async def rerun_from(
        self,
        parent_id: str,
        step: int,
        side_effects: str = "default",
        confirm: str | None = None,
        client_id: str = "",
    ) -> str:
        parent = self.store.get_run(parent_id)
        if not parent:
            raise ApiError(404, "not_found", "找不到原运行")
        rec = self.store.get_step(parent_id, step)
        if not rec:
            raise ApiError(400, "invalid_graph", f"找不到第 {step} 步")
        pending = self.store.pending_before(parent_id, step)
        seed = [
            {
                "edge_id": p.get("edge_id") or "",
                "to_node": p["to_node"],
                "to_port": p["to_port"],
                "payload": p["payload"],
                "produced_step": 0,
                "retry_idem_key": p.get("retry_idem_key"),
            }
            for p in pending
        ]
        base: dict[str, int] = {}
        for s in self.store.list_steps(parent_id, limit=10000):
            if s["step"] < step and s["status"] == "done":
                base[s["node_id"]] = base.get(s["node_id"], 0) + 1
        return await self.start_full(
            parent["flow_id"],
            parent["flow_version"],
            kind="rerun",
            parent_run_id=parent_id,
            base_step=step - 1,
            seed_pending=seed,
            side_effects=side_effects,
            confirm=confirm,
            client_id=client_id,
            base_rounds=base,
            input_source_hint="cache",
        )

    def _check_cycles(self, graph: dict[str, Any], kind: str, node_ids: list[str] | None) -> None:
        if kind == "group" and node_ids:
            allowed = set(node_ids)
            for d in cycle_exit_details(
                {
                    **graph,
                    "nodes": [n for n in graph["nodes"] if n["id"] in allowed],
                    "edges": [e for e in graph["edges"] if e["source"] in allowed],
                }
            ):
                if set(d["node_ids"]) <= allowed:
                    outs = [
                        e
                        for e in graph["edges"]
                        if e["source"] in d["node_ids"] and e["target"] not in allowed
                    ]
                    conds = [i for i in d["node_ids"] if node_map(graph)[i]["type"] == "condition"]
                    if not conds and not outs:
                        raise ApiError(
                            400,
                            "loop_without_exit",
                            "这个环没有条件判断节点作出口。",
                            {"node_ids": d["node_ids"]},
                        )
            return
        if kind == "full":
            bad = cycle_exit_details(graph)
            if bad:
                raise ApiError(
                    400,
                    "loop_without_exit",
                    cycle_exit_errors(graph)[0],
                    {"node_ids": bad[0]["node_ids"]},
                )

    def _check_adapter(self, graph: dict[str, Any]) -> None:
        if not getattr(self.adapter, "connected", True):
            raise ApiError(
                409, "adapter_disconnected", "未连上机器人控制器。流程可以打开，但不能运行。"
            )
        blockers = inspect_run(graph, self.adapter)
        if blockers.action_gone:
            raise ApiError(
                409,
                "action_missing",
                "有节点的动作已不存在。",
                {"nodes": blockers.action_gone, "node_ids": blockers.action_gone},
            )
        if blockers.missing_input:
            raise ApiError(
                409,
                "missing_input",
                f"{len(blockers.missing_input)} nodes need input",
                {"nodes": blockers.missing_input, "node_ids": blockers.missing_input},
            )
        if blockers.type_errors:
            err = blockers.type_errors[0]
            raise ApiError(
                409,
                "param_type_mismatch",
                f"{err['node']}: {err['param']} must be {err['type']}",
                {"errors": blockers.type_errors},
            )
        if blockers.limit_errors:
            err = blockers.limit_errors[0]
            raise ApiError(
                409,
                "param_out_of_limit",
                f"{err['node']}: {err['param']} is outside this robot's limits",
                {"errors": blockers.limit_errors},
            )
        if blockers.mismatches and not graph.get("mismatch_unlocked"):
            raise ApiError(
                409,
                "run_locked",
                "Review the differences above before running.",
                {"mismatches": blockers.mismatches},
            )

    def _validate_graph(self, graph: dict[str, Any]) -> None:
        missing = [n for n in graph.get("nodes") or [] if not known_type(n["type"])]
        for n in missing:
            # marked by the caller via node_state if a run already exists
            raise ApiError(
                409, "action_missing", f"动作已不存在：{n.get('type')}", {"node_id": n.get("id")}
            )
        for n in graph.get("nodes") or []:
            try:
                get_type(n["type"])
            except KeyError as e:
                raise ApiError(
                    409, "action_missing", "动作已不存在。", {"node_id": n.get("id")}
                ) from e
            try:
                parse_params(n["type"], n.get("params"))
            except Exception as e:
                raise ApiError(
                    400,
                    "invalid_graph",
                    f"节点 {n.get('id')} 参数无效：{e}",
                    {"node_id": n.get("id")},
                ) from e
        for e in graph.get("edges") or []:
            src_t, dst_t = connected_types(graph, e)
            if not types_compatible(src_t, dst_t):
                raise ApiError(400, "invalid_graph", "连线两端端口类型不同。", {"edge": e})

    def _check_confirm(self, node: dict[str, Any], confirm: str | None) -> None:
        cls = get_type(node["type"])
        if (
            cls.side_effect == SideEffect.motion
            and getattr(self.adapter, "target", "sim") == "real"
        ):
            text = cls.confirm_text or "将让机器人执行这个动作。"
            if confirm != text:
                raise ApiError(422, "confirm_required", "真机重跑会动的节点需要确认。")
            return
        text = cls.confirm_text
        if not text:
            return
        if confirm != text:
            raise ApiError(422, "invalid_graph", "真执行需要确认文字与节点声明一致。")

    def _resolve_node_input(
        self, flow_id: str, node: dict[str, Any], spec: dict | None
    ) -> tuple[dict[str, Any], str]:
        spec = spec or {"kind": "default"}
        kind = spec.get("kind") or "default"
        if kind == "manual":
            return dict(spec.get("value") or {}), "manual"
        if kind == "pinned":
            out = {}
            for p in self.store.list_pins(flow_id):
                if p["node_id"] == node["id"]:
                    out[p["port"]] = self._pin_value(p)
            return out, "pinned"
        if kind == "record":
            rec = self.store.get_step(spec["run_id"], spec["step"])
            if not rec:
                raise ApiError(400, "invalid_graph", "找不到那条运行记录")
            return dict(rec.get("input") or {}), "cache"
        # default: last successful record of this node
        for run in self.store.list_runs(flow_id):
            rec = self.store.last_step_for_node(run["run_id"], node["id"], spec.get("round"))
            if rec and rec.get("status") == "done":
                return dict(rec.get("input") or {}), "cache"
        return {}, "manual"

    def _pin_value(self, pin: dict[str, Any]) -> Any:
        if pin.get("value") is not None:
            return pin["value"]
        if pin.get("from_run") and pin.get("from_step") is not None:
            rec = self.store.get_step(pin["from_run"], pin["from_step"])
            if rec:
                return (rec.get("input") or {}).get(pin["port"])
        return None

    def _resolve_entry(
        self, flow_id: str, graph: dict, edge: dict, spec: dict, source_run_id: str | None
    ) -> tuple[Any, str]:
        kind = spec.get("kind") or "default"
        port = edge.get("source_port")
        if kind == "manual":
            return spec.get("value"), "manual"
        if kind == "pinned":
            pins = [
                p
                for p in self.store.list_pins(flow_id)
                if p["node_id"] == edge["target"] and p["port"] == edge.get("target_port")
            ]
            if pins:
                return self._pin_value(pins[0]), "pinned"
        if kind == "record" or (spec.get("run_id") and spec.get("step")):
            run_key = spec.get("run_id") or spec.get("from", {}).get("run_id")
            step = spec.get("step")
            if isinstance(run_key, str) and isinstance(step, int):
                rec = self.store.get_step(run_key, step)
                if rec:
                    return (rec.get("output") or {}).get(port), "cache"
        run_id = spec.get("run_id") or source_run_id
        if run_id:
            rec = self.store.last_step_for_node(run_id, edge["source"], spec.get("round"))
            if rec:
                return (rec.get("output") or {}).get(port), "cache"
        for run in self.store.list_runs(flow_id):
            rec = self.store.last_step_for_node(run["run_id"], edge["source"])
            if rec and rec.get("output") is not None:
                return (rec.get("output") or {}).get(port), "cache"
        return None, "cache"

    # ----- commands -----

    async def pause(self, run_id: str, client_id: str = "") -> dict[str, Any]:
        ctrl = self._need(run_id)
        async with ctrl.cmd_lock:
            if ctrl.status == "paused":
                return {"status": "paused"}
            if ctrl.status in TERMINAL:
                raise ApiError(409, "already_finished", "这次运行已经结束。")
            ctrl.user_pause = True
            self.store.add_command(run_id, client_id, "pause", None, "202")
            return {"status": "running"}

    async def continue_run(
        self,
        run_id: str,
        pause_id: str,
        client_id: str = "",
        edits: dict | None = None,
        mode: str = "continue",
        resolution: str | None = None,
        readback_id: str | None = None,
    ) -> dict[str, Any]:
        ctrl = self._need(run_id)
        async with ctrl.cmd_lock:
            waiting = (ctrl.pause or {}).get("reason") == "approval"
            if waiting and mode != "approve":
                raise ApiError(409, "awaiting_approval", "这一步在等人工确认，先通过或驳回。")
            if not ctrl.pause or ctrl.pause.get("pause_id") != pause_id:
                raise ApiError(
                    409,
                    "pause_mismatch",
                    "另一个页面已经继续了这次运行。",
                    {"pause_id": (ctrl.pause or {}).get("pause_id") or ctrl.last_used_pause_id},
                )
            if ctrl.status != "paused":
                raise ApiError(409, "not_paused", "这次运行当前没有停住。")
            reason = ctrl.pause.get("reason")
            side = self._paused_side(ctrl)
            if reason in MOTION_RESOLVE_REASONS and side == "motion":
                rb = (ctrl.pause or {}).get("readback") or {}
                if not readback_id or readback_id != rb.get("readback_id"):
                    raise ApiError(
                        409, "readback_required", "先读回机器人状态，再选重做或当作已做完。"
                    )
                if resolution == "mark_done":
                    self._apply_mark_done(ctrl, edits)
                elif resolution != "retry":
                    raise ApiError(409, "readback_required", "需要 resolution=retry 或 mark_done")
            if edits and not (
                reason in MOTION_RESOLVE_REASONS and side == "motion" and resolution == "mark_done"
            ):
                self._apply_edits(ctrl, edits)
            if reason == "adapter_lost" and not getattr(self.adapter, "connected", True):
                raise ApiError(409, "adapter_disconnected", "机器人控制器仍未连上，不能继续。")
            if reason in (
                "breakpoint",
                "restart",
                "user",
                "storage_error",
                "write_timeout",
                "adapter_lost",
                "operator_lost",
                "motion_timeout",
            ):
                ctrl.ignore_bp = (ctrl.pause.get("node_id"), ctrl.pause.get("when"))
            ctrl.last_used_pause_id = pause_id
            ctrl.step_mode = mode == "step"
            ctrl.user_pause = False
            ctrl.status = "running"
            self.store.update_run(run_id, status="running", pause=None)
            self.store.add_command(run_id, client_id, mode, pause_id, "200")
            self.hub.emit(
                run_id, "resumed", {"mode": mode, "by_client": client_id, "at": now_iso()}
            )
            ctrl.pause = None
            ctrl.resume.set()
            return {"status": "running"}

    async def step_run(
        self,
        run_id: str,
        pause_id: str,
        client_id: str = "",
        edits: dict | None = None,
        resolution: str | None = None,
        readback_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.continue_run(
            run_id,
            pause_id,
            client_id,
            edits,
            mode="step",
            resolution=resolution,
            readback_id=readback_id,
        )

    async def stop_run(self, run_id: str, client_id: str = "") -> dict[str, Any]:
        ctrl = self._need(run_id)
        async with ctrl.cmd_lock:
            if ctrl.status in TERMINAL:
                raise ApiError(409, "already_finished", "这次运行已经结束。")
            self.store.add_command(run_id, client_id, "stop", None, "200")
            if ctrl.step_phase == "waiting_precondition":
                ctrl.stop_flag = True
                return {"status": "stopping"}
            if ctrl.status == "paused":
                ctrl.stop_flag = True
                self._finish(ctrl, "stopped")
                ctrl.resume.set()
                return {"status": "stopped"}
            inst = ctrl.current_inst
            desc = getattr(type(inst), "action_desc", None) if inst is not None else None
            blocking = bool(desc and desc.return_mode == "blocking")
            cancellable = True if desc is None else bool(desc.cancel.cancellable)
            if inst is not None and desc is not None and blocking and not cancellable:
                ctrl.stop_flag = True
                self._record_not_cancelled(ctrl, ctrl.current_nid)
                return {"status": "stopping"}
            if inst is not None:
                result = await inst.cancel()
                ctrl.cancel_called[ctrl.current_nid or ""] = True
                if result and not result.accepted:
                    if ctrl.current_nid:
                        rnd = (ctrl.node_state.get(ctrl.current_nid) or {}).get("round") or 0
                        self._set_node(
                            ctrl,
                            ctrl.current_nid,
                            "cancel_rejected",
                            rnd,
                            ctrl.steps_used,
                            error_summary="cancel_rejected",
                        )
                    return {"status": "running", "cancel": "rejected"}
                ctrl.stop_flag = True
                if desc is None and ctrl.task:
                    ctrl.task.cancel()
                elif desc is not None and desc.return_mode == "blocking" and ctrl.task:
                    # blocking already returning; loop will see stop_flag
                    pass
                return {"status": "stopping"}
            ctrl.stop_flag = True
            self._finish(ctrl, "stopped")
            if ctrl.task and not ctrl.task.done():
                ctrl.task.cancel()
            return {"status": "stopped"}

    def _record_not_cancelled(self, ctrl: RunCtrl, nid: str | None) -> None:
        label = nid or ""
        ctrl.not_cancelled.append(label)
        self.store.add_change(
            ctrl.run_id, "run_log", nid, {"event": "not cancelled", "node_id": nid}
        )
        recs = self.store.running_steps(ctrl.run_id)
        for rec in recs:
            if not nid or rec["node_id"] == nid:
                self.store.update_step(
                    ctrl.run_id,
                    rec["step"],
                    error={"type": "not_cancelled", "message": "not cancelled"},
                )

    async def approve(
        self,
        run_id: str,
        pause_id: str,
        decision: str,
        client_id: str = "",
        comment: str | None = None,
    ) -> dict[str, Any]:
        ctrl = self._need(run_id)
        async with ctrl.cmd_lock:
            if not ctrl.pause or ctrl.pause.get("pause_id") != pause_id:
                raise ApiError(409, "pause_mismatch", "另一个页面已经处理了这次确认。")
            if ctrl.status != "paused":
                raise ApiError(409, "not_paused", "这次运行当前没有停住。")
            if ctrl.pause.get("reason") != "approval":
                raise ApiError(409, "not_paused", "当前不是在等待确认。")
            if decision not in ("approve", "reject"):
                raise ApiError(422, "invalid_graph", "decision 必须是 approve 或 reject")
            ctrl.decision = {
                "decision": decision,
                "comment": comment,
                "node_id": ctrl.pause.get("node_id"),
            }
            ctrl.last_used_pause_id = pause_id
            ctrl.status = "running"
            self.store.update_run(run_id, status="running", pause=None)
            self.store.add_command(run_id, client_id, "approve", pause_id, "200")
            self.hub.emit(
                run_id, "resumed", {"mode": "approve", "by_client": client_id, "at": now_iso()}
            )
            ctrl.pause = None
            ctrl.resume.set()
            return {"status": "running"}

    def _apply_edits(self, ctrl: RunCtrl, edits: dict[str, Any]) -> None:
        pause = ctrl.pause or {}
        when = pause.get("when")
        nid = pause.get("node_id")
        node = node_map(ctrl.flow).get(nid or "") or {}
        if when == "before" and edits.get("inputs") is not None:
            try:
                parse_inputs(node.get("type") or "echo", edits["inputs"], node.get("params"))
            except Exception as e:
                raise ApiError(422, "invalid_graph", f"改的输入无效：{e}") from e
            ctrl.edits = {"inputs": edits["inputs"], "original": pause.get("inputs")}
        elif when == "after" and edits.get("outputs") is not None:
            if known_type(node.get("type") or ""):
                cls = get_type(node["type"])
                if cls.side_effect == SideEffect.motion:
                    raise ApiError(422, "motion_output_readonly", "动作节点的输出不能改。")
            step = pause.get("step")
            if not isinstance(step, int):
                ctrl.edits = None
                return
            rec = self.store.get_step(ctrl.run_id, step)
            if rec:
                original = rec.get("output")
                self.store.update_step(
                    ctrl.run_id, step, output=edits["outputs"], edited={"original": original}
                )
                for e in outgoing(ctrl.flow, rec["node_id"]):
                    port = e.get("source_port")
                    val = (edits["outputs"] or {}).get(port)
                    if val is None:
                        continue
                    self.store.update_unconsumed_from_step(
                        ctrl.run_id, step, e.get("id") or "", val
                    )
            ctrl.edits = None

    # ----- recovery -----

    async def adopt_unfinished(self) -> list[str]:
        ids = []
        for row in self.store.unfinished_runs():
            graph = self.store.get_version(row["flow_id"], row["flow_version"])
            if not graph:
                continue
            ctrl = RunCtrl(
                run_id=row["run_id"],
                kind=row["kind"],
                flow=graph,
                flow_id=row["flow_id"],
                flow_version=row["flow_version"],
                max_steps=row["max_steps"],
                side_effects=row.get("side_effects") or "default",
                allowed=None,
                parent_run_id=row.get("parent_run_id"),
                base_step=row.get("base_step") or 0,
                status="paused",
                steps_used=row.get("steps_used") or 0,
            )
            crashed_nid = None
            mid_action = False
            recovery: dict[str, Any] | None = None
            waiting_nid = None
            for rec in self.store.running_steps(ctrl.run_id):
                if rec.get("phase") == "waiting_precondition":
                    self.store.unconsume_items(ctrl.run_id, rec["step"])
                    self.store.delete_step(ctrl.run_id, rec["step"])
                    self._set_node(ctrl, rec["node_id"], "queued", rec.get("round") or 0, None)
                    waiting_nid = rec["node_id"]
                    continue
                node = node_map(ctrl.flow).get(rec["node_id"]) or {}
                side = self._side(node, rec.get("input") or {})
                from_adapter = False
                if known_type(node.get("type") or ""):
                    from_adapter = bool(getattr(get_type(node["type"]), "from_adapter", False))
                new_status = "interrupted_unknown" if side in ("write", "motion") else "interrupted"
                self.store.update_step(
                    ctrl.run_id,
                    rec["step"],
                    status=new_status,
                    ended_at=now_iso(),
                    error={"type": "crash", "message": "crash"},
                )
                # Never auto-retry. Write fixtures keep the idempotency key so a later
                # human continue can retry once; motion nodes only read state /
                # goal status.
                if side == "write" and not from_adapter:
                    self.store.unconsume_items(ctrl.run_id, rec["step"])
                    if rec.get("idem_key"):
                        ctrl.retry_keys[rec["node_id"]] = rec["idem_key"]
                        ctrl.retry_of[rec["node_id"]] = rec["step"]
                elif side == "motion":
                    self.store.unconsume_items(ctrl.run_id, rec["step"])
                recovery = await self._recover_action(rec, node)
                self._set_node(
                    ctrl,
                    rec["node_id"],
                    new_status,
                    rec["round"],
                    rec["step"],
                    error_summary="中断 · 结果未知"
                    if new_status == "interrupted_unknown"
                    else "中断",
                    recovery=recovery,
                )
                crashed_nid = rec["node_id"]
                mid_action = True
                if from_adapter:
                    pass
            old_pause = row.get("pause") or {}
            node_id = crashed_nid or waiting_nid or old_pause.get("node_id")
            when = old_pause.get("when") or "before"
            reason = "restart"
            ctrl.recovery = recovery
            ctrl.pause = self._make_pause(
                ctrl, reason, node_id, when, old_pause.get("inputs"), old_pause.get("round") or 0
            )
            if recovery:
                ctrl.pause["recovery"] = recovery
                ctrl.pause["goal_check"] = recovery.get("goal_status")
            crashed_node = node_map(ctrl.flow).get(node_id or "") or {}
            if mid_action and self._side(crashed_node, {}) == "motion":
                ctrl.pause["readback"] = await self._read_robot(ctrl, node_id)
            self.store.update_run(ctrl.run_id, status="paused", pause=ctrl.pause)
            ctrl.resume = asyncio.Event()
            ctrl.influencing = influencing_map(graph)
            for s in self.store.list_steps(ctrl.run_id, limit=10000):
                if s["status"] in (
                    "done",
                    "error",
                    "stopped",
                    "interrupted",
                    "interrupted_unknown",
                ):
                    ctrl.done_counts[s["node_id"]] = ctrl.done_counts.get(s["node_id"], 0) + 1
            self.runs[ctrl.run_id] = ctrl
            ctrl.task = asyncio.create_task(self._loop(ctrl))
            ids.append(ctrl.run_id)
        return ids

    async def _recover_action(self, rec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
        """Restart: async → goal status; blocking → read_state only. Never auto-retry."""
        info: dict[str, Any] = {
            "active_goals": [],
            "read_state": None,
            "goal_status": None,
            "return_mode": None,
        }
        adapter = self.adapter
        list_fn = getattr(adapter, "list_active_goals", None)
        if callable(list_fn):
            try:
                info["active_goals"] = list_fn() or []
            except Exception as e:
                info["active_goals"] = [{"error": str(e)}]
        desc = None
        if known_type(node.get("type") or ""):
            desc = getattr(get_type(node["type"]), "action_desc", None)
        if desc is not None:
            info["return_mode"] = desc.return_mode
            if desc.return_mode in ("goal_progress", "async"):
                try:
                    info["goal_status"] = await adapter.goal_status(idem_key=rec.get("idem_key"))
                except Exception as e:
                    info["goal_status"] = {"found": False, "error": str(e)}
            elif desc.read_state:
                try:
                    info["read_state"] = await adapter.read_state(desc.id)
                except Exception as e:
                    info["read_state"] = {"error": str(e)}
            if desc.return_mode in ("goal_progress", "async") and desc.read_state:
                try:
                    info["read_state"] = await adapter.read_state(desc.id)
                except Exception as e:
                    info["read_state"] = {"error": str(e)}
        else:
            try:
                info["read_state"] = await adapter.read_state(node.get("type") or "")
            except Exception:
                info["read_state"] = None
        return info

    def start_background(self) -> None:
        if self._hb_task is None or self._hb_task.done():
            self._last_adapter_ok = time.monotonic()
            start = getattr(self.adapter, "start_watchdog", None)
            if callable(start):
                start()
            self._hb_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_background(self) -> None:
        if self._hb_task:
            self._hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._hb_task
            self._hb_task = None
        await self.adapter.stop_watchdog()

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                ok = await self._ping_adapter()
                if ok:
                    self._last_adapter_ok = time.monotonic()
                    beat = getattr(self.adapter, "receive_heartbeat", None)
                    if callable(beat):
                        beat()
                elif time.monotonic() - self._last_adapter_ok >= self.adapter_heartbeat_s:
                    await self._pause_running_adapter_lost()
                await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            return

    async def _ping_adapter(self) -> bool:
        ping = getattr(self.adapter, "ping", None)
        if ping is None:
            return True
        try:
            if inspect.iscoroutinefunction(ping):
                return bool(await ping())
            return bool(ping())
        except Exception:
            return False

    async def _pause_running_adapter_lost(self) -> None:
        for ctrl in list(self.runs.values()):
            if ctrl.status != "running":
                continue
            ctrl.adapter_lost_flag = True
            ctrl.user_pause = True

    def note_ui(self, run_id: str) -> None:
        ctrl = self.runs.get(run_id)
        if ctrl:
            ctrl.saw_ui = True
            ctrl.last_ui_at = time.monotonic()

    def ui_subscribers(self, run_id: str) -> int:
        return self.hub.subscriber_count(run_id)

    # ----- main loop -----

    async def _loop(self, ctrl: RunCtrl) -> None:
        try:
            while ctrl.status not in TERMINAL:
                if ctrl.stop_flag:
                    self._finish(ctrl, "stopped")
                    break
                if ctrl.t2_retry:
                    self._commit_t2(ctrl, **ctrl.t2_retry)
                    ctrl.t2_retry = None
                if ctrl.status == "paused":
                    await ctrl.resume.wait()
                    ctrl.resume.clear()
                    if ctrl.status in TERMINAL:
                        break
                ready = self._ready_nodes(ctrl)
                if not ready:
                    self._mark_skipped(ctrl)
                    self._finish(ctrl, "completed")
                    break
                if ctrl.steps_used + 1 > ctrl.max_steps:
                    self._finish(ctrl, "step_limit")
                    break
                nid = ready[0]
                node = node_map(ctrl.flow)[nid]
                nxt_round = self._next_round(ctrl, nid)
                peeked, item_ids = self._peek_inputs(ctrl, nid)
                if ctrl.edits and ctrl.edits.get("inputs") is not None:
                    peeked = ctrl.edits["inputs"]
                    original = ctrl.edits.get("original")
                    ctrl.edits = {"original": original, "used": True}
                if (
                    node["type"] == "human_confirm"
                    and not (ctrl.decision and ctrl.decision.get("node_id") in (None, nid))
                    and not ctrl.decision
                ):
                    await self._pause(ctrl, "approval", nid, "before", peeked, nxt_round)
                    continue
                if self._hit_bp(ctrl, nid, "before", nxt_round, peeked):
                    await self._pause(ctrl, "breakpoint", nid, "before", peeked, nxt_round)
                    continue
                if ctrl.adapter_lost_flag:
                    ctrl.adapter_lost_flag = False
                    ctrl.user_pause = False
                    await self._pause(ctrl, "adapter_lost", nid, "before", peeked, nxt_round)
                    continue
                if self._ui_lost(ctrl):
                    await self._pause(ctrl, "operator_lost", nid, "before", peeked, nxt_round)
                    continue
                if ctrl.user_pause:
                    await self._pause(ctrl, "user", nid, "before", peeked, nxt_round)
                    continue
                await self._dispatch(ctrl, nid, peeked, item_ids, nxt_round)
                if self.step_delay_s:
                    await asyncio.sleep(self.step_delay_s)
                if ctrl.abandoned or ctrl.run_id not in self.runs:
                    break
                if ctrl.status in TERMINAL:
                    break
                if ctrl.step_mode:
                    nxt = self._ready_nodes(ctrl)
                    nn = nxt[0] if nxt else None
                    await self._pause(
                        ctrl,
                        "step_done",
                        nn,
                        "before",
                        None,
                        self._next_round(ctrl, nn) if nn else 0,
                    )
                    ctrl.step_mode = False
                    continue
                if ctrl.adapter_lost_flag:
                    ctrl.adapter_lost_flag = False
                    nxt = self._ready_nodes(ctrl)
                    nn = nxt[0] if nxt else None
                    await self._pause(ctrl, "adapter_lost", nn, "before", None, 0)
                    continue
                if self._ui_lost(ctrl):
                    nxt = self._ready_nodes(ctrl)
                    nn = nxt[0] if nxt else None
                    await self._pause(ctrl, "operator_lost", nn, "before", None, 0)
                    continue
                if ctrl.user_pause:
                    nxt = self._ready_nodes(ctrl)
                    nn = nxt[0] if nxt else None
                    await self._pause(ctrl, "user", nn, "before", None, 0)
        except asyncio.CancelledError:
            self._on_cancel(ctrl)
        except ApiError:
            raise
        except Exception as e:
            ctrl.error = {"type": type(e).__name__, "message": str(e)}
            self._finish(ctrl, "error")

    async def _dispatch(
        self, ctrl: RunCtrl, nid: str, inputs: dict[str, Any], item_ids: list[int], rnd: int
    ) -> None:
        if ctrl.stop_flag or ctrl.abandoned or ctrl.run_id not in self.runs:
            if ctrl.stop_flag and ctrl.status not in TERMINAL:
                self._finish(ctrl, "stopped")
            return
        node = node_map(ctrl.flow)[nid]
        cls = get_type(node["type"])
        inst = cls()
        side = self._side(node, inputs)
        step = ctrl.steps_used + 1
        retry_of = ctrl.retry_of.pop(nid, None)
        idem = ctrl.retry_keys.pop(nid, None) or f"{ctrl.run_id}:{step}"
        mode = "real"
        cached = None
        if ctrl.kind != "full" and side == "write" and ctrl.side_effects != "real":
            mode = "mock"
        elif ctrl.kind != "full" and side == "read" and ctrl.side_effects != "real":
            cached = self._find_cache(ctrl, nid, inputs)
            if cached is not None:
                mode = "cache"
        source = self._input_source(ctrl, nid, inputs)
        if ctrl.edits and ctrl.edits.get("used"):
            source = "manual"
        nmap = node_map(ctrl.flow)
        inf = (ctrl.influencing or {}).get(nid)
        if inf is None:
            inf = influencing_nodes(ctrl.flow, nid)
        fps = {}
        for i in inf:
            node_i = nmap.get(i)
            if not node_i:
                continue
            h = ctrl.hash_cache.get(i)
            if h is None:
                h = config_hash(node_i)
                ctrl.hash_cache[i] = h
            fps[i] = h
        desc = getattr(cls, "action_desc", None)
        start_phase = None
        if desc is not None and (
            desc.precondition_wait_s is not None or any(p.check for p in desc.preconditions)
        ):
            start_phase = "waiting_precondition"
        try:
            self.store.add_step(
                {
                    "run_id": ctrl.run_id,
                    "step": step,
                    "node_id": nid,
                    "round": rnd,
                    "status": "running",
                    "input": inputs,
                    "input_source": source,
                    "output": None,
                    "started_at": now_iso(),
                    "config_hash": config_hash(node),
                    "upstream_hashes": fps,
                    "input_sha256": sha256_text(dump(inputs)),
                    "side_effect": side,
                    "side_effect_mode": mode,
                    "idem_key": idem,
                    "retry_of": retry_of,
                    "edited": {"original": ctrl.edits.get("original")}
                    if ctrl.edits and ctrl.edits.get("used")
                    else None,
                    "phase": start_phase,
                }
            )
            self.store.consume_items(ctrl.run_id, item_ids, step)
            self.store.update_run(ctrl.run_id, steps_used=step)
        except Exception as e:
            await self._pause(ctrl, "storage_error", nid, "before", inputs, rnd, message=str(e))
            return
        ctrl.steps_used = step
        ctrl.edits = None
        inst.idem = idem
        ctrl.current_inst = inst
        ctrl.current_nid = nid
        ctrl.current_started = time.monotonic()
        ctrl.step_phase = start_phase
        ctrl.wait_started = time.monotonic()
        ctrl.wait_max_s = (
            float(desc.precondition_wait_s)
            if desc and desc.precondition_wait_s is not None
            else None
        )
        if start_phase == "waiting_precondition":
            self._set_node(
                ctrl,
                nid,
                "running",
                rnd,
                step,
                phase=start_phase,
                wait_s=0,
                wait_max_s=ctrl.wait_max_s,
            )
        else:
            self._set_node(ctrl, nid, "running", rnd, step)

        outputs: dict[str, Any] = {}
        err = None
        status = "done"
        t0 = time.perf_counter()
        timeout = getattr(cls, "timeout_s", NODE_TIMEOUT_S)
        saw_progress = False

        async def on_progress(p: Progress) -> None:
            nonlocal saw_progress
            saw_progress = True
            self._set_node(
                ctrl,
                nid,
                "running",
                rnd,
                step,
                progress=p.percent,
                feedback=p.text,
            )

        elapsed_task: asyncio.Task | None = None

        async def _elapsed() -> None:
            try:
                while True:
                    await asyncio.sleep(1)
                    if ctrl.step_phase == "waiting_precondition":
                        secs = int(time.monotonic() - (ctrl.wait_started or ctrl.current_started))
                        self._set_node(
                            ctrl,
                            nid,
                            "running",
                            rnd,
                            step,
                            phase="waiting_precondition",
                            wait_s=secs,
                            wait_max_s=ctrl.wait_max_s,
                        )
                        continue
                    if saw_progress:
                        continue
                    secs = int(time.monotonic() - ctrl.current_started)
                    self._set_node(ctrl, nid, "running", rnd, step, elapsed_s=secs)
            except asyncio.CancelledError:
                return

        try:
            elapsed_task = asyncio.create_task(_elapsed())
            if mode == "mock":
                out_m = inst.mock(
                    _coerce_inputs(inst, inputs), parse_params(node["type"], node.get("params"))
                )
                outputs = dump_outputs(out_m)
            elif mode == "cache":
                outputs = cached or {}
            else:
                decision = (
                    (ctrl.decision or {}).get("decision")
                    if node["type"] == "human_confirm"
                    else None
                )
                comment = (
                    (ctrl.decision or {}).get("comment")
                    if node["type"] == "human_confirm"
                    else None
                )
                if node["type"] == "human_confirm":
                    ctrl.decision = None

                async def on_phase(phase: str) -> None:
                    ctrl.step_phase = phase
                    self.store.update_step(ctrl.run_id, step, phase=phase)
                    if phase == "waiting_precondition":
                        ctrl.wait_started = time.monotonic()
                        self._set_node(
                            ctrl,
                            nid,
                            "running",
                            rnd,
                            step,
                            phase=phase,
                            wait_s=0,
                            wait_max_s=ctrl.wait_max_s,
                        )
                    elif phase == "sent":
                        ctrl.current_started = time.monotonic()
                        self._set_node(ctrl, nid, "running", rnd, step, phase=phase)

                ctx = RunContext(
                    ctrl.run_id,
                    step,
                    rnd,
                    idem,
                    self.data_dir,
                    self.adapter,
                    decision=decision,
                    on_progress=on_progress,
                    on_phase=on_phase,
                    should_abort=lambda: self._wait_abort_reason(ctrl),
                )
                wait_budget = float(desc.precondition_wait_s or 0) if desc else 0.0
                async with asyncio.timeout(float(timeout) + wait_budget + 0.5):
                    out_m = await inst.run(
                        _coerce_inputs(inst, inputs),
                        parse_params(node["type"], node.get("params")),
                        ctx,
                    )
                outputs = dump_outputs(out_m)
                if comment:
                    self.store.update_step(ctrl.run_id, step, edited={"comment": comment})
        except TimeoutError:
            if ctrl.stop_flag:
                status = "stopped"
                err = None
            elif side == "write" or side == "motion":
                status = "interrupted_unknown"
                err = {"type": "timeout", "message": "timeout", "timeout_s": int(timeout)}
            else:
                status = "error"
                err = {"type": "timeout", "message": "timeout", "timeout_s": int(timeout)}
        except CrashBetweenWriteAndKey:
            self.store.update_run(ctrl.run_id, status="running")
            ctrl.abandoned = True
            self.runs.pop(ctrl.run_id, None)
            return
        except WaitAborted as e:
            if elapsed_task:
                elapsed_task.cancel()
            ctrl.current_inst = None
            if e.reason == "stop" or ctrl.stop_flag:
                status = "stopped"
                err = None
                ctrl.step_phase = None
            else:
                self._revert_waiting_step(ctrl, step, item_ids)
                ctrl.step_phase = None
                reason = (
                    e.reason if e.reason in ("user", "operator_lost", "adapter_lost") else "user"
                )
                if reason == "adapter_lost":
                    ctrl.adapter_lost_flag = False
                if reason == "user":
                    ctrl.user_pause = False
                self._set_node(ctrl, nid, "queued", rnd, None)
                await self._pause(ctrl, reason, nid, "before", inputs, rnd, skip_readback=True)
                return
        except Exception as e:
            if ctrl.stop_flag:
                status = "stopped"
                err = None
            else:
                status = "error"
                err = {
                    "type": getattr(e, "error_type", None) or type(e).__name__,
                    "message": str(e),
                }
                state = getattr(e, "state", None)
                if state:
                    err["state"] = state
        finally:
            if elapsed_task:
                elapsed_task.cancel()
            ctrl.current_inst = None
        if ctrl.abandoned or ctrl.run_id not in self.runs:
            return
        dt = (time.perf_counter() - t0) * 1000
        if side == "write" and (self.crash_before_t2 or self.hooks.get("crash_before_t2")):
            self.crash_before_t2 = False
            self.hooks["crash_before_t2"] = False
            # Simulate kill -9 after the write and before T2: leave the step
            # `running`.
            self.store.update_run(ctrl.run_id, status="running")
            ctrl.abandoned = True
            self.runs.pop(ctrl.run_id, None)
            return
        ctrl.step_phase = None
        stored = image_summary(outputs)
        if outputs:
            bucket = self.images.setdefault(ctrl.run_id, {})
            for port, val in outputs.items():
                if isinstance(val, dict) and (
                    val.get("type") == "image" or str(val.get("mime") or "").startswith("image/")
                ):
                    bucket[(step, port)] = val
        t2 = dict(
            status=status,
            output=stored,
            live_output=outputs,
            error=err,
            ended_at=now_iso(),
            duration_ms=dt,
            node_id=nid,
            round=rnd,
            step=step,
            side=side,
            mode=mode,
        )
        try:
            self._commit_t2(ctrl, **t2)
        except Exception as e:
            ctrl.t2_retry = t2
            await self._pause(ctrl, "storage_error", nid, "after", inputs, rnd, message=str(e))
            return
        if node["type"] == "output":
            ctrl.result = inputs.get("value")
            self.store.update_run(ctrl.run_id, result=image_summary(ctrl.result))
        if status == "stopped":
            self._set_node(ctrl, nid, "stopped", rnd, step, duration_ms=dt, side_effect_mode=mode)
            self._finish(ctrl, "stopped")
            return
        if status == "interrupted_unknown" and err and err.get("type") == "timeout":
            self._set_node(
                ctrl,
                nid,
                "interrupted_unknown",
                rnd,
                step,
                duration_ms=dt,
                error_summary=(err or {}).get("message"),
                side_effect_mode=mode,
            )
            self.store.unconsume_items(ctrl.run_id, step, retry_idem_key=idem)
            if side == "write":
                ctrl.retry_keys[nid] = idem
                ctrl.retry_of[nid] = step
                await self._pause(
                    ctrl, "write_timeout", nid, "after", inputs, rnd, message="write_timeout"
                )
            else:
                await self._pause(
                    ctrl, "motion_timeout", nid, "before", inputs, rnd, message="motion_timeout"
                )
            return
        if status in ("error", "interrupted_unknown"):
            self._set_node(
                ctrl,
                nid,
                "error" if status == "error" else status,
                rnd,
                step,
                duration_ms=dt,
                error_summary=(err or {}).get("message"),
                side_effect_mode=mode,
            )
            ctrl.error = err
            self._finish(ctrl, "error")
            return
        self._set_node(ctrl, nid, "done", rnd, step, duration_ms=dt, side_effect_mode=mode)
        if self._hit_bp(ctrl, nid, "after", rnd, inputs):
            await self._pause(ctrl, "breakpoint", nid, "after", inputs, rnd)

    def _commit_t2(self, ctrl: RunCtrl, **t2: Any) -> None:
        status, outputs, err, ended = t2["status"], t2["output"], t2["error"], t2["ended_at"]
        live = t2.get("live_output") or outputs
        step, nid = t2["step"], t2["node_id"]
        self.store.update_step(
            ctrl.run_id,
            step,
            status=status,
            output=outputs,
            error=err,
            ended_at=ended,
            duration_ms=t2.get("duration_ms"),
        )
        if status in ("done", "error", "stopped", "interrupted", "interrupted_unknown"):
            ctrl.done_counts[nid] = ctrl.done_counts.get(nid, 0) + 1
        if status == "done" and ctrl.kind != "node":
            for port, val in (live or {}).items():
                if val is None or str(port).startswith("_"):
                    continue
                for e in outgoing(ctrl.flow, nid, port):
                    if ctrl.allowed is not None and e["target"] not in ctrl.allowed:
                        continue
                    self.store.add_pending(
                        ctrl.run_id,
                        e.get("id") or f"{e['source']}-{e['target']}",
                        e["target"],
                        e.get("target_port") or "in",
                        val,
                        step,
                    )
        edges = [p["edge_id"] for p in self.store.list_pending(ctrl.run_id, unconsumed_only=True)]
        self._set_node(
            ctrl,
            nid,
            "done" if status == "done" else status,
            t2["round"],
            step,
            duration_ms=t2.get("duration_ms"),
            error_summary=(err or {}).get("message") if err else None,
            side_effect_mode=t2.get("mode"),
            edges=edges,
        )
        node = node_map(ctrl.flow).get(nid) or {}
        if status == "done" and node.get("type") == "human_confirm":
            self._mark_unused_branch(ctrl, nid, outputs)

    def _mark_unused_branch(self, ctrl: RunCtrl, nid: str, outputs: dict[str, Any] | None) -> None:
        unused_starts: list[str] = []
        used_starts: list[str] = []
        for port, val in (outputs or {}).items():
            for edge in outgoing(ctrl.flow, nid, port):
                if val is None or str(port).startswith("_"):
                    unused_starts.append(edge["target"])
                else:
                    used_starts.append(edge["target"])
        if not unused_starts:
            return

        def reach(starts: list[str]) -> set[str]:
            seen: set[str] = set()
            stack = list(starts)
            while stack:
                u = stack.pop()
                if u in seen:
                    continue
                seen.add(u)
                for edge in outgoing(ctrl.flow, u):
                    stack.append(edge["target"])
            return seen

        via_used = reach(used_starts)
        for p in self.store.list_pending(ctrl.run_id, unconsumed_only=True):
            via_used |= reach([p["to_node"]])
        via_unused = reach(unused_starts)
        done = {s["node_id"] for s in self.store.list_steps(ctrl.run_id, limit=10000)}
        for x in via_unused - via_used - done - {nid}:
            self._set_node(ctrl, x, "skipped", 0, None)

    def _on_cancel(self, ctrl: RunCtrl) -> None:
        if ctrl.status in TERMINAL:
            return
        running = self.store.running_steps(ctrl.run_id)
        for rec in running:
            self.store.update_step(ctrl.run_id, rec["step"], status="stopped", ended_at=now_iso())
            self._set_node(ctrl, rec["node_id"], "stopped", rec["round"], rec["step"])
            ctrl.done_counts[rec["node_id"]] = ctrl.done_counts.get(rec["node_id"], 0) + 1
        self._finish(ctrl, "stopped")

    # ----- ready / inputs -----

    def _ready_nodes(self, ctrl: RunCtrl) -> list[str]:
        if ctrl.kind == "node" and ctrl.allowed:
            nid = next(iter(ctrl.allowed))
            return [nid] if self._exec_count(ctrl, nid) == 0 else []
        pending = self.store.list_pending(ctrl.run_id, unconsumed_only=True)
        by_node: dict[str, list] = {}
        for p in pending:
            by_node.setdefault(p["to_node"], []).append(p)
        ready = []
        for n in ctrl.flow.get("nodes") or []:
            nid = n["id"]
            if ctrl.allowed is not None and nid not in ctrl.allowed:
                continue
            inst = get_instance(n["type"])
            req = [p["name"] for p in inst.input_ports(n.get("params")) if p.get("required")]
            connected = {
                e.get("target_port")
                for e in incoming(ctrl.flow, nid)
                if ctrl.allowed is None or e["source"] in ctrl.allowed
            }
            need = [p for p in req if p in connected] if connected else (req if req else [])
            have = {p["to_port"] for p in by_node.get(nid, [])}
            if need:
                if set(need) <= have:
                    ready.append(nid)
            elif nid in by_node:
                ready.append(nid)
            elif ctrl.kind != "rerun" and self._exec_count(ctrl, nid) == 0:
                inc = [
                    e
                    for e in incoming(ctrl.flow, nid)
                    if ctrl.allowed is None or e["source"] in ctrl.allowed
                ]
                if (not inc and n["type"] in SEED_TYPES) or (not inc and n["type"] == "fail"):
                    ready.append(nid)
        return ready

    def _peek_inputs(self, ctrl: RunCtrl, nid: str) -> tuple[dict[str, Any], list[int]]:
        items = [
            p
            for p in self.store.list_pending(ctrl.run_id, unconsumed_only=True)
            if p["to_node"] == nid
        ]
        by_port: dict[str, list] = {}
        for p in items:
            by_port.setdefault(p["to_port"], []).append(p)
        inputs, ids = {}, []
        for port, lst in by_port.items():
            lst.sort(key=lambda x: x["item_id"])
            inputs[port] = lst[0]["payload"]
            ids.append(lst[0]["item_id"])
        return inputs, ids

    def _exec_count(self, ctrl: RunCtrl, nid: str) -> int:
        retry_key = ctrl.retry_keys.get(nid)
        if retry_key:
            recs = [
                s
                for s in self.store.list_steps(ctrl.run_id, node_id=nid, limit=10000)
                if s["status"] in ("done", "error", "stopped", "interrupted", "interrupted_unknown")
            ]
            recs = [
                s
                for s in recs
                if s.get("idem_key") != retry_key
                or s["status"] not in ("interrupted_unknown", "error")
            ]
            return len(recs)
        return ctrl.done_counts.get(nid, 0)

    def _ui_lost(self, ctrl: RunCtrl) -> bool:
        # Liveness is only last_ui_at (SSE subscribe + page ui-beat).
        # A half-open TCP socket still accepts SSE writes and keeps
        # n_subscribers > 0; that must not count as a live page.
        if self.ui_lost_s is None:
            return False
        if not ctrl.saw_ui:
            return False
        return (time.monotonic() - ctrl.last_ui_at) >= self.ui_lost_s

    def _next_round(self, ctrl: RunCtrl, nid: str | None) -> int:
        if not nid:
            return 0
        return (ctrl.base_rounds.get(nid) or 0) + self._exec_count(ctrl, nid) + 1

    def _input_source(self, ctrl: RunCtrl, nid: str, inputs: dict[str, Any]) -> str:
        if ctrl.kind == "full":
            return "upstream"
        if ctrl.kind == "node" and ctrl.input_source_hint:
            return ctrl.input_source_hint
        for port in inputs:
            if (nid, port) in ctrl.entry_sources:
                return ctrl.entry_sources[(nid, port)]
        if ctrl.kind in ("group", "rerun"):
            return "cache" if ctrl.input_source_hint else "upstream"
        return "upstream"

    def _side(self, node: dict[str, Any], inputs: dict[str, Any]) -> str:
        cls = get_type(node["type"])
        if node["type"] == "tool_call":
            name = (
                (inputs or {}).get("tool_name") or (node.get("params") or {}).get("tool") or "clock"
            )
            side_fn = getattr(self.adapter, "side", None)
            if callable(side_fn):
                try:
                    return str(side_fn(name))
                except Exception:
                    return "none"
            return "none"
        return cls.side_effect.value

    def _find_cache(self, ctrl: RunCtrl, nid: str, inputs: dict[str, Any]) -> dict | None:
        want = sha256_text(dump(inputs))
        ch = config_hash(node_map(ctrl.flow)[nid])
        for run in self.store.list_runs(ctrl.flow_id):
            for s in self.store.list_steps(run["run_id"], node_id=nid, limit=10000):
                if (
                    s["status"] == "done"
                    and s.get("config_hash") == ch
                    and s.get("input_sha256") == want
                ):
                    return s.get("output")
        return None

    def _hit_bp(self, ctrl: RunCtrl, nid: str, when: str, rnd: int, inputs: dict | None) -> bool:
        if ctrl.ignore_bp == (nid, when):
            ctrl.ignore_bp = None
            return False
        bp = self.store.get_breakpoint(ctrl.flow_id, nid, when)
        if not bp or not bp.get("enabled"):
            return False
        mode = bp.get("mode") or "every"
        if mode == "round":
            return int(bp.get("round") or 0) == rnd
        if mode == "condition":
            cond = bp.get("condition") or {}
            left = get_path(inputs, cond.get("field"))
            return compare(left, cond.get("op") or "eq", cond.get("value"))
        return True

    def _mark_skipped(self, ctrl: RunCtrl) -> None:
        done = {s["node_id"] for s in self.store.list_steps(ctrl.run_id, limit=10000)}
        for n in ctrl.flow.get("nodes") or []:
            if ctrl.allowed is not None and n["id"] not in ctrl.allowed:
                continue
            if n["id"] not in done:
                self._set_node(ctrl, n["id"], "skipped", 0, None)

    # ----- pause / finish / status -----

    def _make_pause(
        self,
        ctrl: RunCtrl,
        reason: str,
        node_id: str | None,
        when: str,
        inputs: dict | None = None,
        rnd: int = 0,
        message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "pause_id": new_id("ps_"),
            "reason": reason,
            "node_id": node_id,
            "when": when,
            "step": ctrl.steps_used,
            "round": rnd,
            "inputs": inputs,
            "message": message,
        }

    def _wait_abort_reason(self, ctrl: RunCtrl) -> str | None:
        if ctrl.stop_flag:
            return "stop"
        if ctrl.adapter_lost_flag:
            return "adapter_lost"
        if self._ui_lost(ctrl):
            return "operator_lost"
        if ctrl.user_pause:
            return "user"
        return None

    def _revert_waiting_step(self, ctrl: RunCtrl, step: int, item_ids: list[int]) -> None:
        self.store.unconsume_items(ctrl.run_id, step)
        self.store.delete_step(ctrl.run_id, step)
        if ctrl.steps_used == step:
            ctrl.steps_used = max(0, step - 1)
            self.store.update_run(ctrl.run_id, steps_used=ctrl.steps_used)

    async def _pause(
        self,
        ctrl: RunCtrl,
        reason: str,
        node_id: str | None,
        when: str,
        inputs: dict | None,
        rnd: int,
        message: str | None = None,
        skip_readback: bool = False,
    ) -> None:
        ctrl.status = "paused"
        ctrl.pause = self._make_pause(ctrl, reason, node_id, when, inputs, rnd, message)
        ctrl.last_paused_at_mono = time.monotonic()
        state = {
            "approval": "awaiting_approval",
            "confirm_after_crash": "awaiting_approval",
            "breakpoint": "paused_before" if when == "before" else "paused_after",
            "user": "paused_before",
            "step_done": "paused_before",
            "restart": "paused_before",
            "storage_error": "paused_before",
            "adapter_lost": "paused_before",
            "operator_lost": "paused_before",
            "motion_timeout": None,
        }.get(reason)
        if reason in ("write_timeout", "motion_timeout"):
            state = None
        if not skip_readback and reason in ("motion_timeout", "adapter_lost") and node_id:
            ctrl.pause["readback"] = await self._read_robot(ctrl, node_id)
        if node_id and state:
            self._set_node(ctrl, node_id, state, rnd, ctrl.steps_used)
        self.store.update_run(ctrl.run_id, status="paused", pause=ctrl.pause)
        self.hub.flush_now(ctrl.run_id)
        self.hub.emit(
            ctrl.run_id,
            "paused",
            {**ctrl.pause, "at": now_iso(), "paused_at_mono": ctrl.last_paused_at_mono},
        )
        if reason == "storage_error":
            self.hub.emit(ctrl.run_id, "storage_error", {"message": message or "", "at": now_iso()})
        ctrl.resume.clear()
        await ctrl.resume.wait()
        ctrl.resume.clear()

    def _finish(self, ctrl: RunCtrl, status: str) -> None:
        if ctrl.status in TERMINAL:
            return
        ctrl.status = status
        ctrl.pause = None
        self.store.update_run(
            ctrl.run_id,
            status=status,
            finished_at=now_iso(),
            error=ctrl.error,
            result=ctrl.result,
            steps_used=ctrl.steps_used,
        )
        last = self.store.list_steps(ctrl.run_id, after_step=max(0, ctrl.steps_used - 1), limit=1)
        last_node = last[-1]["node_id"] if last else None
        self.hub.flush_now(ctrl.run_id)
        self.hub.emit(
            ctrl.run_id,
            "run_finished",
            {
                "status": status,
                "steps_used": ctrl.steps_used,
                "max_steps": ctrl.max_steps,
                "last_step": ctrl.steps_used,
                "last_node_id": last_node,
                "error": ctrl.error,
                "at": now_iso(),
            },
        )

    def _set_node(
        self, ctrl: RunCtrl, nid: str, state: str, rnd: int, step: int | None, **extra: Any
    ) -> None:
        body = {
            "state": state,
            "status": state,
            "round": rnd,
            "step": step,
            "last_step": step,
            **{k: v for k, v in extra.items() if v is not None and k != "edges"},
        }
        ctrl.node_state[nid] = {**ctrl.node_state.get(nid, {}), **body}
        self.hub.note_change(
            ctrl.run_id,
            nid,
            {
                "kind": "node",
                "state": state,
                "status": state,
                "round": rnd,
                "step": step,
                "duration_ms": extra.get("duration_ms"),
                "error_summary": extra.get("error_summary"),
                "side_effect_mode": extra.get("side_effect_mode"),
                "steps_used": ctrl.steps_used,
                "max_steps": ctrl.max_steps,
                "edges_active": extra.get("edges") or [],
                "word": extra.get("word"),
                "progress": extra.get("progress"),
                "feedback": extra.get("feedback"),
                "elapsed_s": extra.get("elapsed_s"),
                "recovery": extra.get("recovery"),
                "phase": extra.get("phase"),
                "wait_s": extra.get("wait_s"),
                "wait_max_s": extra.get("wait_max_s"),
            },
        )

    def snapshot(self, run_id: str) -> dict[str, Any]:
        ctrl = self.runs.get(run_id)
        row = self.store.get_run(run_id)
        if not row:
            raise ApiError(404, "not_found", "找不到运行")
        latest = self.store.get_flow_meta(row["flow_id"])
        nodes = dict(ctrl.node_state) if ctrl else {}
        if not nodes:
            for s in self.store.list_steps(run_id, limit=10000):
                nodes[s["node_id"]] = {
                    "state": s["status"] if s["status"] != "done" else "done",
                    "status": s["status"],
                    "round": s["round"],
                    "last_step": s["step"],
                    "side_effect_mode": s.get("side_effect_mode"),
                    "error_summary": (s.get("error") or {}).get("message")
                    if s.get("error")
                    else None,
                }
        edges = [p["edge_id"] for p in self.store.list_pending(run_id, unconsumed_only=True)]
        pause = ctrl.pause if ctrl else row.get("pause")
        status = ctrl.status if ctrl else row["status"]
        return {
            "run_id": run_id,
            "id": run_id,
            "kind": row["kind"],
            "flow_id": row["flow_id"],
            "flow_version": row["flow_version"],
            "latest_flow_version": latest["latest_version"] if latest else row["flow_version"],
            "status": status,
            "max_steps": row["max_steps"],
            "step_limit": row["max_steps"],
            "steps_used": ctrl.steps_used if ctrl else row["steps_used"],
            "global_step": ctrl.steps_used if ctrl else row["steps_used"],
            "step": ctrl.steps_used if ctrl else row["steps_used"],
            "pause": pause,
            "paused_node": (pause or {}).get("node_id") if pause else None,
            "paused_phase": (pause or {}).get("when") if pause else None,
            "nodes": nodes,
            "node_status": nodes,
            "edges_active": edges,
            "animated_edges": edges,
            "result": (ctrl.result if ctrl else row.get("result")),
            "error": (ctrl.error if ctrl else row.get("error")),
            "last_event_id": self.store.last_seq(run_id),
            "paused_at_mono": ctrl.last_paused_at_mono if ctrl else 0,
            "unused_pins": ctrl.unused_pins if ctrl else [],
            "recovery": (pause or {}).get("recovery")
            if pause
            else (ctrl.recovery if ctrl else None),
            "not_cancelled": list(ctrl.not_cancelled) if ctrl else [],
        }

    def notify_version(self, flow_id: str, latest: int) -> None:
        for ctrl in self.runs.values():
            if (
                ctrl.flow_id == flow_id
                and ctrl.status not in TERMINAL
                and ctrl.flow_version != latest
            ):
                self.hub.emit(
                    ctrl.run_id,
                    "version_drift",
                    {"locked_version": ctrl.flow_version, "latest_version": latest},
                )

    def decorate_steps(self, run_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        row = self.store.get_run(run_id)
        latest = self.store.latest_graph(row["flow_id"]) if row else None
        pins = (
            {(p["node_id"], p["port"]) for p in self.store.list_pins(row["flow_id"])}
            if row
            else set()
        )
        out = []
        for s in steps:
            rec = dict(s)
            rec["inputs"] = rec.get("input")
            rec["outputs"] = rec.get("output")
            live = self.images.get(run_id) or {}
            if rec.get("output"):
                merged = dict(rec["output"])
                for port in list(merged):
                    img = live.get((rec["step"], port))
                    if img:
                        merged[port] = img
                rec["outputs"] = merged
                rec["output"] = merged
            rec["mocked"] = rec.get("side_effect_mode") == "mock"
            stale = compute_stale(rec, latest)
            rec["stale"] = stale
            rec["pinned"] = any((rec["node_id"], p) in pins for p in (rec.get("input") or {}))
            out.append(rec)
        return out

    def _paused_side(self, ctrl: RunCtrl) -> str:
        nid = (ctrl.pause or {}).get("node_id")
        node = node_map(ctrl.flow).get(nid or "") or {}
        if not node or not known_type(node.get("type") or ""):
            return "none"
        return self._side(node, {})

    async def _read_robot(self, ctrl: RunCtrl, node_id: str | None) -> dict[str, Any]:
        node = node_map(ctrl.flow).get(node_id or "") or {}
        action_id = node.get("type") or ""
        state: Any = None
        try:
            state = await self.adapter.read_state(action_id)
        except Exception as exc:
            state = {"error": str(exc)}
        return {
            "readback_id": new_id("rb_"),
            "at": now_iso(),
            "state": state,
            "node_id": node_id,
        }

    async def readback(self, run_id: str) -> dict[str, Any]:
        ctrl = self._need(run_id)
        nid = (ctrl.pause or {}).get("node_id") or ctrl.current_nid
        rb = await self._read_robot(ctrl, nid)
        if ctrl.pause:
            ctrl.pause["readback"] = rb
            self.store.update_run(run_id, pause=ctrl.pause)
        return rb

    def _apply_mark_done(self, ctrl: RunCtrl, edits: dict | None) -> None:
        nid = (ctrl.pause or {}).get("node_id")
        if not nid:
            return
        steps = [
            s
            for s in self.store.list_steps(ctrl.run_id, node_id=nid, limit=10000)
            if s["status"] == "interrupted_unknown"
        ]
        if not steps:
            return
        rec = steps[-1]
        outputs = (edits or {}).get("outputs") if edits else None
        if not outputs:
            outputs = dict(((ctrl.pause or {}).get("readback") or {}).get("state") or {})
        self.store.update_step(
            ctrl.run_id, rec["step"], status="done", output=outputs, ended_at=now_iso()
        )
        for port, val in (outputs or {}).items():
            if val is None or str(port).startswith("_"):
                continue
            for edge in outgoing(ctrl.flow, nid, port):
                if ctrl.allowed is not None and edge["target"] not in ctrl.allowed:
                    continue
                self.store.add_pending(
                    ctrl.run_id,
                    edge.get("id") or f"{edge['source']}-{edge['target']}",
                    edge["target"],
                    edge.get("target_port") or "in",
                    val,
                    rec["step"],
                )
        self._set_node(ctrl, nid, "done", rec["round"], rec["step"], extra=" · 人工确认")

    def image_at(self, run_id: str, step: int, port: str) -> dict[str, Any] | None:
        bucket = self.images.get(run_id) or {}
        return bucket.get((step, port))

    def _need(self, run_id: str) -> RunCtrl:
        ctrl = self.runs.get(run_id)
        if not ctrl:
            raise ApiError(404, "not_found", f"找不到运行 {run_id}")
        return ctrl


def compute_stale(rec: dict[str, Any], latest_graph: dict[str, Any] | None) -> dict[str, Any]:
    hashes = rec.get("upstream_hashes") or {}
    if rec.get("input_source") == "pinned":
        return {"is_stale": False, "changed_nodes": []}
    changed = []
    if latest_graph:
        nodes = node_map(latest_graph)
        for nid, old_h in hashes.items():
            node = nodes.get(nid)
            if node is None or config_hash(node) != old_h:
                changed.append(nid)
    return {"is_stale": bool(changed), "changed_nodes": changed}


def _coerce_inputs(inst: NodeType, raw: dict[str, Any]):
    from pydantic import BaseModel, ConfigDict

    class Dyn(BaseModel):
        model_config = ConfigDict(extra="allow")

    try:
        return inst.Inputs.model_validate(raw or {})
    except Exception:
        return Dyn.model_validate(raw or {})
