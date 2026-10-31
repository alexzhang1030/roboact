"""RoboAct flow file import / export (docs/flow-file.md + PRD Flow file)."""

from __future__ import annotations

import json
import multiprocessing
import re
from dataclasses import dataclass, field
from io import StringIO
from typing import Any, Literal

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.composer import ComposerError
from ruamel.yaml.constructor import ConstructorError, DuplicateKeyError
from ruamel.yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from ruamel.yaml.scanner import ScannerError

from . import DEFAULT_STEP_LIMIT
from .adapter import Adapter, ParamSpec
from .graph import connected_types, cycle_exit_details, types_compatible
from .nodes import SideEffect, get_instance, get_type, known_type

SUPPORTED_FORMAT_VERSION = 1
FORMAT_NAME = "roboact-flow"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_BODY_BYTES = MAX_FILE_BYTES
MAX_DEPTH = 64
MAX_STRING_CHARS = 65536
JS_MAX_SAFE = 9007199254740991
YAML_PARSE_TIMEOUT_S = 180.0
KNOWN_JSON_TYPES = frozenset(
    {
        "",
        "any",
        "array",
        "bool",
        "boolean",
        "float",
        "image",
        "int",
        "integer",
        "json",
        "null",
        "number",
        "object",
        "str",
        "string",
        "text",
    }
)

BUILTIN_KINDS = frozenset(
    {"start", "output", "constant", "condition", "merge", "human_approval", "action"}
)
KIND_TO_TYPE = {
    "start": "start",
    "output": "output",
    "constant": "constant",
    "condition": "condition",
    "merge": "merge",
    "human_approval": "human_confirm",
}
TYPE_TO_KIND = {v: k for k, v in KIND_TO_TYPE.items()}

TOP_KEYS = frozenset({"format", "format_version", "flow", "nodes", "edges"})
FLOW_KEYS = frozenset(
    {
        "id",
        "name",
        "revision",
        "step_limit",
        "action_list_version",
        "adapter",
        "robot",
        "units",
    }
)
FLOW_REQUIRED = ("id", "name", "revision", "step_limit")
ADAPTER_KEYS = frozenset({"name", "version", "target"})
ROBOT_KEYS = frozenset({"id", "model"})
UNITS_KEYS = frozenset({"angle", "length"})
NODE_KEYS = frozenset({"id", "kind", "action", "title", "params", "position"})
NODE_REQUIRED = ("id", "kind", "title", "params", "position")
POS_KEYS = frozenset({"x", "y"})
EDGE_KEYS = frozenset({"from", "to"})
END_KEYS = frozenset({"node", "port"})
CORE_TAGS = frozenset(
    {
        None,
        "",
        "!",
        "?",
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:map",
        "tag:yaml.org,2002:seq",
    }
)

YamlVersion = tuple[int, int] | None


@dataclass(frozen=True)
class FileIssue:
    path: str
    message: str


class FlowFileRejected(Exception):
    def __init__(self, issues: list[FileIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in issues))


@dataclass
class RunBlockers:
    action_gone: list[str] = field(default_factory=list)
    missing_input: list[str] = field(default_factory=list)
    type_errors: list[dict[str, str]] = field(default_factory=list)
    limit_errors: list[dict[str, str]] = field(default_factory=list)
    mismatches: list[dict[str, str]] = field(default_factory=list)

    def blocks_run(self, unlocked: bool) -> bool:
        if self.action_gone or self.missing_input or self.type_errors or self.limit_errors:
            return True
        return bool(self.mismatches) and not unlocked


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def canonical_filename(name: str, fmt: str) -> str:
    stem = re.sub(r"[\\/]+", "-", (name or "flow").strip()) or "flow"
    ext = "yaml" if fmt in ("yaml", "yml") else "json"
    return f"{stem}.{ext}"


def import_flow(text: str, fmt: str, adapter: Adapter) -> dict[str, Any]:
    tree = parse_and_validate(text, fmt)
    return doc_to_graph(tree, adapter)


def export_flow(graph: dict[str, Any], fmt: str, adapter: Adapter) -> tuple[str, str]:
    doc = graph_to_doc(graph, adapter)
    kind = _norm_fmt(fmt)
    text = dump_yaml(doc) if kind == "yaml" else dump_json(doc)
    return text, canonical_filename(str(doc["flow"]["name"]), kind)


def parse_and_validate(
    text: object, fmt: str, *, yaml_version: YamlVersion = None
) -> dict[str, Any]:
    if not isinstance(text, str):
        raise FlowFileRejected([FileIssue("", "file text must be a string")])
    raw = text.encode("utf-8")
    if len(raw) > MAX_FILE_BYTES:
        raise FlowFileRejected(
            [FileIssue("", f"file exceeds {MAX_FILE_BYTES} bytes (checked before parsing)")]
        )
    kind = _norm_fmt(fmt)
    tree = parse_yaml(text, version=yaml_version) if kind == "yaml" else parse_json(text)
    issues: list[FileIssue] = []
    _check_limits(tree, "", 1, issues)
    if issues:
        raise FlowFileRejected(issues)
    _validate_document(tree, issues)
    if issues:
        raise FlowFileRejected(issues)
    return tree


def parse_json(text: str) -> Any:
    def no_dup(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, val in pairs:
            name = str(key)
            if name in out:
                raise FlowFileRejected([FileIssue(name, "duplicate key")])
            out[name] = val
        return out

    def no_const(name: str) -> Any:
        raise FlowFileRejected([FileIssue("", f"JSON constant {name} is not allowed")])

    try:
        return json.loads(text, parse_constant=no_const, object_pairs_hook=no_dup)
    except FlowFileRejected:
        raise
    except json.JSONDecodeError as e:
        raise FlowFileRejected(
            [FileIssue("", f"JSON parse error at line {e.lineno} column {e.colno}: {e.msg}")]
        ) from e


def parse_yaml(text: str, *, version: YamlVersion = None) -> Any:
    _reject_percent_lines(text)
    return _parse_yaml_in_process(text, version)


def unlock_valid(graph: dict[str, Any], adapter: Adapter) -> bool:
    """True only for this import + this flow + the robot currently connected."""
    rec = graph.get("unlock_record")
    if not isinstance(rec, dict) or not graph.get("file_id"):
        return False
    if rec.get("file_id") != graph.get("file_id"):
        return False
    robot = adapter.robot()
    units = adapter.units()
    return (
        rec.get("robot_id") == robot.get("id")
        and rec.get("adapter_name") == adapter.name
        and rec.get("action_list_version") == adapter.action_list_version
        and rec.get("units_angle") == units.get("angle")
        and rec.get("units_length") == units.get("length")
    )


def make_unlock_record(graph: dict[str, Any], adapter: Adapter) -> dict[str, Any]:
    robot = adapter.robot()
    units = adapter.units()
    return {
        "action_list_version": adapter.action_list_version,
        "adapter_name": adapter.name,
        "file_id": graph.get("file_id"),
        "robot_id": robot.get("id"),
        "units_angle": units.get("angle"),
        "units_length": units.get("length"),
    }


def is_imported(graph: dict[str, Any]) -> bool:
    return bool(graph.get("file_id"))


def dump_yaml(doc: dict[str, Any]) -> str:
    yaml = YAML(typ="rt", pure=True)
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    buf = StringIO()
    yaml.dump(_yaml_tree(doc), buf)
    text = buf.getvalue()
    return text if text.endswith("\n") else text + "\n"


def graph_to_doc(graph: dict[str, Any], adapter: Adapter) -> dict[str, Any]:
    nodes_out: list[dict[str, Any]] = []
    for node in graph.get("nodes") or []:
        typ = str(node.get("type") or "")
        kind = TYPE_TO_KIND.get(typ, "action")
        params = dict(node.get("params") or {})
        for spec in _param_specs(typ):
            if spec.secret:
                params[spec.name] = None
        item: dict[str, Any] = {
            "id": node["id"],
            "kind": kind,
            "params": params,
            "position": {"x": node.get("x") or 0, "y": node.get("y") or 0},
            "title": node.get("title") or "",
        }
        if kind == "action":
            item["action"] = typ
        nodes_out.append(item)
    nodes_out.sort(key=lambda n: str(n["id"]))
    edges_out: list[dict[str, Any]] = []
    for edge in graph.get("edges") or []:
        edges_out.append(
            {
                "from": {
                    "node": edge["source"],
                    "port": edge.get("source_port") or "in",
                },
                "to": {
                    "node": edge["target"],
                    "port": edge.get("target_port") or "in",
                },
            }
        )
    edges_out.sort(
        key=lambda e: (e["from"]["node"], e["from"]["port"], e["to"]["node"], e["to"]["port"])
    )
    adapter_info = graph.get("file_adapter") or {
        "name": adapter.name,
        "target": adapter.target,
        "version": adapter.version,
    }
    robot = graph.get("file_robot") or adapter.robot()
    units = graph.get("file_units") or adapter.units()
    revision = graph.get("revision")
    if revision is None:
        revision = graph.get("version") or 1
    return {
        "edges": edges_out,
        "flow": {
            "action_list_version": graph.get("action_list_version") or adapter.action_list_version,
            "adapter": adapter_info,
            "id": graph.get("file_id") or graph.get("id") or "",
            "name": graph.get("name") or "",
            "revision": int(revision),
            "robot": robot,
            "step_limit": int(graph.get("step_limit") or DEFAULT_STEP_LIMIT),
            "units": units,
        },
        "format": FORMAT_NAME,
        "format_version": SUPPORTED_FORMAT_VERSION,
        "nodes": nodes_out,
    }


def doc_to_graph(doc: dict[str, Any], adapter: Adapter) -> dict[str, Any]:
    del adapter
    flow = doc["flow"]
    nodes: list[dict[str, Any]] = []
    for item in doc["nodes"]:
        kind = item["kind"]
        typ = item["action"] if kind == "action" else KIND_TO_TYPE[kind]
        pos = item["position"]
        nodes.append(
            {
                "id": item["id"],
                "params": dict(item.get("params") or {}),
                "title": item.get("title") or "",
                "type": typ,
                "x": pos["x"],
                "y": pos["y"],
            }
        )
    edges: list[dict[str, Any]] = []
    for item in doc["edges"]:
        src, dst = item["from"], item["to"]
        edges.append(
            {
                "id": f"e_{src['node']}_{src['port']}_{dst['node']}_{dst['port']}",
                "kind": "data",
                "source": src["node"],
                "source_port": src["port"],
                "target": dst["node"],
                "target_port": dst["port"],
            }
        )
    return {
        "action_list_version": flow.get("action_list_version"),
        "edges": edges,
        "file_adapter": flow.get("adapter"),
        "file_id": flow["id"],
        "file_robot": flow.get("robot"),
        "file_units": flow.get("units"),
        "groups": [],
        "name": flow["name"],
        "nodes": nodes,
        "revision": flow["revision"],
        "step_limit": flow["step_limit"],
    }


def inspect_run(graph: dict[str, Any], adapter: Adapter) -> RunBlockers:
    out = RunBlockers()
    for node in graph.get("nodes") or []:
        nid = str(node.get("id") or "")
        typ = str(node.get("type") or "")
        title = str(node.get("title") or nid)
        if not known_type(typ):
            out.action_gone.append(nid)
            continue
        params = node.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        specs = _param_specs(typ)
        for spec in specs:
            if spec.secret and params.get(spec.name) is None:
                out.missing_input.append(nid)
                break
        inspect_values(typ, params, adapter, title or nid, out)
    out.mismatches = _mismatches(graph, adapter)
    return out


def inspect_values(
    typ: str,
    values: dict[str, Any],
    adapter: Adapter,
    title: str,
    out: RunBlockers | None = None,
) -> RunBlockers:
    """Type / enum / range / motion-limit checks on a known value map (params or linked inputs)."""
    dest = out or RunBlockers()
    if not known_type(typ):
        return dest
    cls = get_type(typ)
    specs = {s.name: s for s in _param_specs(typ)}
    schema = _params_schema(typ)
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for key, val in values.items():
        spec = specs.get(key)
        if spec is not None and spec.secret and val is None:
            continue
        schema_type = ""
        prop = props.get(key) if isinstance(props, dict) else None
        if isinstance(prop, dict):
            raw_t = prop.get("type")
            if isinstance(raw_t, list):
                schema_type = str(raw_t[0] if raw_t else "")
            elif raw_t is not None:
                schema_type = str(raw_t)
            enum = prop.get("enum")
            if isinstance(enum, list) and val not in enum:
                dest.type_errors.append({"node": title, "param": key, "type": "enum"})
                continue
        declared = (spec.type if spec is not None else schema_type) or schema_type
        if spec is not None and spec.enum is not None and val not in spec.enum:
            dest.type_errors.append({"node": title, "param": key, "type": "enum"})
            continue
        if declared and not _value_matches_type(val, declared):
            dest.type_errors.append({"node": title, "param": key, "type": declared})
            continue
        if (
            spec is not None
            and spec.range is not None
            and isinstance(val, (int, float))
            and not isinstance(val, bool)
        ):
            lo, hi = spec.range
            if val < lo or val > hi:
                dest.limit_errors.append({"node": title, "param": key})
    if cls.side_effect == SideEffect.motion:
        _collect_motion_limits(values, title, adapter, dest)
    return dest


def _mismatches(graph: dict[str, Any], adapter: Adapter) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    fa = graph.get("file_adapter") or {}
    if isinstance(fa, dict) and fa.get("name") and fa["name"] != adapter.name:
        found.append(
            {"kind": "adapter", "file": str(fa.get("name") or ""), "current": adapter.name}
        )
    saved = graph.get("action_list_version")
    current = adapter.action_list_version
    if saved and current and saved != current:
        found.append({"kind": "actions", "file": str(saved), "current": str(current)})
    fr = graph.get("file_robot") or {}
    cur_r = adapter.robot()
    if isinstance(fr, dict) and fr.get("id") and fr["id"] != cur_r.get("id"):
        found.append(
            {
                "kind": "robot",
                "file": str(fr.get("id") or ""),
                "current": str(cur_r.get("id") or ""),
            }
        )
    fu = graph.get("file_units") or {}
    cur_u = adapter.units()
    if isinstance(fu, dict) and (fu.get("angle") or fu.get("length")):
        file_u = f"{fu.get('angle')}/{fu.get('length')}"
        now_u = f"{cur_u.get('angle')}/{cur_u.get('length')}"
        if file_u != now_u:
            found.append({"kind": "units", "file": file_u, "current": now_u})
    return found


def _norm_fmt(fmt: str) -> Literal["json", "yaml"]:
    f = (fmt or "json").lower()
    if f in ("yaml", "yml"):
        return "yaml"
    if f == "json":
        return "json"
    raise FlowFileRejected([FileIssue("format", f"unknown file format {fmt!r}")])


def _yaml_safe(version: YamlVersion = None) -> YAML:
    yaml = YAML(typ="safe", pure=True)
    yaml.version = version or (1, 2)
    yaml.allow_duplicate_keys = False
    return yaml


def _reject_percent_lines(text: str) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("%"):
            raise FlowFileRejected([FileIssue(f"line {i}", "YAML directives are not allowed")])


def _parse_yaml_in_process(text: str, version: YamlVersion) -> Any:
    ctx = multiprocessing.get_context("fork")
    q: Any = ctx.Queue()
    proc = ctx.Process(target=_yaml_process_entry, args=(text, version, q))
    proc.start()
    proc.join(YAML_PARSE_TIMEOUT_S)
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        if proc.is_alive():
            proc.kill()
            proc.join(1)
        raise FlowFileRejected([FileIssue("", "YAML parse timed out")])
    if q.empty():
        raise FlowFileRejected([FileIssue("", "YAML parse failed")])
    kind, payload = q.get()
    if kind == "ok":
        return payload
    issues = [FileIssue(str(path), str(msg)) for path, msg in payload]
    raise FlowFileRejected(issues or [FileIssue("", "YAML parse failed")])


def _yaml_process_entry(text: str, version: YamlVersion, q: Any) -> None:
    try:
        q.put(("ok", _parse_yaml_inner(text, version)))
    except FlowFileRejected as e:
        q.put(("rej", [(i.path, i.message) for i in e.issues]))
    except RecursionError:
        q.put(("rej", [("", f"nesting exceeds {MAX_DEPTH}")]))
    except Exception as e:
        q.put(("rej", [(_exc_loc(e), f"YAML parse error: {type(e).__name__}: {e}")]))


def _exc_loc(exc: BaseException) -> str:
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is None:
        return ""
    return f"line {mark.line + 1} column {mark.column + 1}"


def _parse_yaml_inner(text: str, version: YamlVersion) -> Any:
    _reject_percent_lines(text)
    _scan_yaml_forbidden(text)
    yaml = _yaml_safe(version)
    try:
        data = yaml.load(StringIO(text))
    except RecursionError:
        raise
    except (
        ComposerError,
        ConstructorError,
        DuplicateKeyError,
        ParserError,
        ScannerError,
    ) as e:
        loc = _exc_loc(e)
        where = f" at {loc}" if loc else ""
        raise FlowFileRejected([FileIssue(loc, f"YAML parse error{where}: {e}")]) from e
    except Exception as e:
        loc = _exc_loc(e)
        where = f" at {loc}" if loc else ""
        raise FlowFileRejected(
            [FileIssue(loc, f"YAML parse error{where}: {type(e).__name__}: {e}")]
        ) from e
    try:
        return _plain(data)
    except RecursionError as e:
        raise FlowFileRejected([FileIssue("", f"nesting exceeds {MAX_DEPTH}")]) from e


def _scan_yaml_forbidden(text: str) -> None:
    yaml = _yaml_safe()
    try:
        events = list(yaml.parse(StringIO(text)))
    except RecursionError:
        raise
    except Exception as e:
        mark = _exc_loc(e)
        where = f" at {mark}" if mark else ""
        raise FlowFileRejected([FileIssue(mark, f"YAML parse error{where}: {e}")]) from e
    issues: list[FileIssue] = []
    stack: list[dict[str, Any]] = []

    def ev_loc(ev: Any) -> str:
        mark = getattr(ev, "start_mark", None)
        if mark is None:
            return ""
        return f"line {mark.line + 1} column {mark.column + 1}"

    for ev in events:
        tag = getattr(ev, "tag", None)
        if tag not in CORE_TAGS:
            issues.append(FileIssue(ev_loc(ev), f"YAML custom tag not allowed: {tag}"))
        anchor = getattr(ev, "anchor", None)
        if anchor and not isinstance(ev, AliasEvent):
            issues.append(FileIssue(ev_loc(ev), "YAML anchors are not allowed"))
        if isinstance(ev, AliasEvent):
            issues.append(FileIssue(ev_loc(ev), "YAML aliases are not allowed"))
        if isinstance(ev, MappingStartEvent):
            stack.append({"type": "map", "expect_key": True})
        elif isinstance(ev, SequenceStartEvent):
            stack.append({"type": "seq"})
        elif isinstance(ev, (MappingEndEvent, SequenceEndEvent)):
            if stack:
                stack.pop()
            if stack and stack[-1]["type"] == "map":
                stack[-1]["expect_key"] = True
        elif isinstance(ev, ScalarEvent) and stack and stack[-1]["type"] == "map":
            if stack[-1]["expect_key"]:
                if ev.value == "<<":
                    issues.append(FileIssue(ev_loc(ev), "YAML merge keys are not allowed"))
                stack[-1]["expect_key"] = False
            else:
                stack[-1]["expect_key"] = True
    if issues:
        raise FlowFileRejected(issues)


def _plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def _yaml_tree(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = CommentedMap()
        for key in sorted(obj):
            out[key] = _yaml_tree(obj[key])
        return out
    if isinstance(obj, list):
        out_s = CommentedSeq()
        for item in obj:
            out_s.append(_yaml_tree(item))
        return out_s
    if isinstance(obj, str):
        return DoubleQuotedScalarString(obj)
    return obj


def _check_limits(obj: Any, path: str, depth: int, issues: list[FileIssue]) -> None:
    if depth > MAX_DEPTH:
        issues.append(FileIssue(path or "", f"nesting exceeds {MAX_DEPTH}"))
        return
    if isinstance(obj, str) and len(obj) > MAX_STRING_CHARS:
        issues.append(FileIssue(path or "", f"string exceeds {MAX_STRING_CHARS} characters"))
    if isinstance(obj, int) and not isinstance(obj, bool) and abs(obj) > JS_MAX_SAFE:
        issues.append(FileIssue(path or "", "integer exceeds ±2^53"))
    if isinstance(obj, dict):
        for key, val in obj.items():
            if not isinstance(key, str):
                issues.append(FileIssue(path or "", "object keys must be strings"))
                continue
            if len(key) > MAX_STRING_CHARS:
                issues.append(
                    FileIssue(_join(path, key), f"string exceeds {MAX_STRING_CHARS} characters")
                )
            _check_limits(val, _join(path, key), depth + 1, issues)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            _check_limits(val, f"{path}[{i}]", depth + 1, issues)


def _validate_document(tree: Any, issues: list[FileIssue]) -> None:
    if not isinstance(tree, dict):
        issues.append(FileIssue("", "file must be an object"))
        return
    _forbid_keys(tree, TOP_KEYS, "", issues)
    fmt = tree.get("format")
    if fmt != FORMAT_NAME:
        issues.append(FileIssue("format", f"expected {FORMAT_NAME!r}"))
    ver = tree.get("format_version")
    if not isinstance(ver, int) or isinstance(ver, bool):
        issues.append(FileIssue("format_version", "must be an integer"))
        return
    if ver > SUPPORTED_FORMAT_VERSION:
        issues.append(
            FileIssue(
                "format_version",
                f"file is from a newer RoboAct (v{ver}), "
                f"this build reads v{SUPPORTED_FORMAT_VERSION}",
            )
        )
        return
    # Lower versions migrate in place (v0 → v1 is a no-op besides optional fields).
    flow = tree.get("flow")
    if not isinstance(flow, dict):
        issues.append(FileIssue("flow", "must be an object"))
        return
    _validate_flow(flow, issues)
    nodes = tree.get("nodes")
    if not isinstance(nodes, list):
        issues.append(FileIssue("nodes", "must be an array"))
        return
    edges = tree.get("edges")
    if not isinstance(edges, list):
        issues.append(FileIssue("edges", "must be an array"))
        return
    seen: dict[str, int] = {}
    for i, node in enumerate(nodes):
        _validate_node(node, i, seen, issues)
    for i, edge in enumerate(edges):
        _validate_edge_shape(edge, i, issues)
    if issues:
        return
    _validate_graph(tree, issues)


def _validate_flow(flow: dict[str, Any], issues: list[FileIssue]) -> None:
    _forbid_keys(flow, FLOW_KEYS, "flow", issues)
    for key in FLOW_REQUIRED:
        if key not in flow:
            issues.append(FileIssue(f"flow.{key}", "required"))
    if "id" in flow and not isinstance(flow["id"], str):
        issues.append(FileIssue("flow.id", "must be a string"))
    if "name" in flow and not isinstance(flow["name"], str):
        issues.append(FileIssue("flow.name", "must be a string"))
    if "revision" in flow and not _is_int(flow["revision"]):
        issues.append(FileIssue("flow.revision", "must be an integer"))
    if "step_limit" in flow and not _is_int(flow["step_limit"]):
        issues.append(FileIssue("flow.step_limit", "must be an integer"))
    if "action_list_version" in flow and not isinstance(flow["action_list_version"], str):
        issues.append(FileIssue("flow.action_list_version", "must be a string"))
    if "adapter" in flow:
        _validate_obj(
            flow["adapter"],
            ADAPTER_KEYS,
            "flow.adapter",
            issues,
            required=("name", "version", "target"),
        )
    if "robot" in flow:
        _validate_obj(flow["robot"], ROBOT_KEYS, "flow.robot", issues, required=("id", "model"))
    if "units" in flow:
        units = flow["units"]
        _validate_obj(units, UNITS_KEYS, "flow.units", issues, required=("angle", "length"))
        if isinstance(units, dict):
            if units.get("angle") not in ("rad", "deg", None):
                issues.append(FileIssue("flow.units.angle", "must be rad or deg"))
            if units.get("length") not in ("m", "mm", None):
                issues.append(FileIssue("flow.units.length", "must be m or mm"))


def _validate_node(node: Any, index: int, seen: dict[str, int], issues: list[FileIssue]) -> None:
    path = f"nodes[{index}]"
    if not isinstance(node, dict):
        issues.append(FileIssue(path, "must be an object"))
        return
    _forbid_keys(node, NODE_KEYS, path, issues)
    for key in NODE_REQUIRED:
        if key not in node:
            issues.append(FileIssue(f"{path}.{key}", "required"))
    nid = node.get("id")
    if not isinstance(nid, str) or not nid:
        issues.append(FileIssue(f"{path}.id", "must be a non-empty string"))
    elif nid in seen:
        issues.append(FileIssue(f"{path}.id", f"duplicate id {nid!r}"))
    else:
        seen[nid] = index
    kind = node.get("kind")
    if kind not in BUILTIN_KINDS:
        issues.append(FileIssue(f"{path}.kind", "unknown kind"))
    if "title" in node and not isinstance(node["title"], str):
        issues.append(FileIssue(f"{path}.title", "must be a string"))
    if "params" in node and not isinstance(node["params"], dict):
        issues.append(FileIssue(f"{path}.params", "must be an object"))
    pos = node.get("position")
    if pos is not None:
        if not isinstance(pos, dict):
            issues.append(FileIssue(f"{path}.position", "must be an object"))
        else:
            _forbid_keys(pos, POS_KEYS, f"{path}.position", issues)
            for axis in ("x", "y"):
                if axis not in pos:
                    issues.append(FileIssue(f"{path}.position.{axis}", "required"))
                elif not _is_num(pos[axis]):
                    issues.append(FileIssue(f"{path}.position.{axis}", "must be a number"))
    if kind == "action":
        if not isinstance(node.get("action"), str) or not node.get("action"):
            issues.append(FileIssue(f"{path}.action", "required string"))
    elif "action" in node:
        issues.append(FileIssue(f"{path}.action", "unknown field"))


def _validate_edge_shape(edge: Any, index: int, issues: list[FileIssue]) -> None:
    path = f"edges[{index}]"
    if not isinstance(edge, dict):
        issues.append(FileIssue(path, "must be an object"))
        return
    _forbid_keys(edge, EDGE_KEYS, path, issues)
    for end in ("from", "to"):
        obj = edge.get(end)
        ep = f"{path}.{end}"
        if not isinstance(obj, dict):
            issues.append(FileIssue(ep, "must be an object"))
            continue
        _forbid_keys(obj, END_KEYS, ep, issues)
        for key in ("node", "port"):
            if key not in obj:
                issues.append(FileIssue(f"{ep}.{key}", "required"))
            elif not isinstance(obj[key], str) or not obj[key]:
                issues.append(FileIssue(f"{ep}.{key}", "must be a non-empty string"))


def _validate_graph(doc: dict[str, Any], issues: list[FileIssue]) -> None:
    nodes = doc["nodes"]
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)}
    graph = {
        "nodes": [
            {
                "id": n["id"],
                "params": n.get("params") or {},
                "type": (
                    n["action"]
                    if n.get("kind") == "action"
                    else KIND_TO_TYPE.get(str(n.get("kind") or ""), "")
                ),
                "x": (n.get("position") or {}).get("x") or 0,
                "y": (n.get("position") or {}).get("y") or 0,
            }
            for n in nodes
            if isinstance(n, dict)
        ],
        "edges": [
            {
                "id": f"e{i}",
                "source": e["from"]["node"],
                "source_port": e["from"]["port"],
                "target": e["to"]["node"],
                "target_port": e["to"]["port"],
            }
            for i, e in enumerate(doc["edges"])
            if isinstance(e, dict)
            and isinstance(e.get("from"), dict)
            and isinstance(e.get("to"), dict)
        ],
    }
    for i, edge in enumerate(doc["edges"]):
        if not isinstance(edge, dict):
            continue
        fr, to = edge.get("from") or {}, edge.get("to") or {}
        for end, obj in (("from", fr), ("to", to)):
            if not isinstance(obj, dict):
                continue
            nid = obj.get("node")
            port = obj.get("port")
            if nid not in by_id:
                issues.append(FileIssue(f"edges[{i}].{end}.node", f"missing node {nid!r}"))
                continue
            src = by_id[nid]
            kind = src.get("kind")
            typ = (
                str(src.get("action") or "")
                if kind == "action"
                else KIND_TO_TYPE.get(str(kind or ""), "")
            )
            raw_params = src.get("params") or {}
            params = raw_params if isinstance(raw_params, dict) else {}
            ports = _ports_of(typ, params, "in" if end == "to" else "out")
            if ports is None:
                continue
            if port not in ports:
                issues.append(
                    FileIssue(f"edges[{i}].{end}.port", f"missing port {port!r} on {nid}")
                )
    if issues:
        return
    for i, edge in enumerate(graph["edges"]):
        src_n = next(n for n in graph["nodes"] if n["id"] == edge["source"])
        dst_n = next(n for n in graph["nodes"] if n["id"] == edge["target"])
        src_type = str(src_n["type"])
        dst_type = str(dst_n["type"])
        if not known_type(src_type) or not known_type(dst_type):
            continue
        src_t, dst_t = connected_types(graph, edge)
        if not types_compatible(src_t, dst_t):
            issues.append(FileIssue(f"edges[{i}]", "port type mismatch"))
    for detail in cycle_exit_details(graph):
        ids = ",".join(detail["node_ids"])
        issues.append(FileIssue("edges", f"loop without an exit ({ids})"))


def _ports_of(typ: str, params: dict[str, Any], side: str) -> set[str] | None:
    if not typ or not known_type(typ):
        return None
    inst = get_instance(typ)
    ports = inst.input_ports(params) if side == "in" else inst.output_ports(params)
    return {p["name"] for p in ports}


def _param_specs(typ: str) -> list[ParamSpec]:
    if not known_type(typ):
        return []
    desc = getattr(get_type(typ), "action_desc", None)
    if desc is None:
        return []
    return list(desc.params)


def _value_matches_type(val: Any, declared: str) -> bool:
    kind = (declared or "any").lower()
    if kind not in KNOWN_JSON_TYPES:
        return False
    if kind in ("any", ""):
        return True
    if kind in ("string", "str", "text"):
        return isinstance(val, str)
    if kind in ("number", "float"):
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if kind in ("integer", "int"):
        return isinstance(val, int) and not isinstance(val, bool)
    if kind in ("boolean", "bool"):
        return isinstance(val, bool)
    if kind in ("object", "json"):
        return isinstance(val, dict)
    if kind == "array":
        return isinstance(val, list)
    if kind == "null":
        return val is None
    if kind == "image":
        return isinstance(val, dict)
    return False


def _params_schema(typ: str) -> dict[str, Any]:
    try:
        schema = get_type(typ).Params.model_json_schema()
    except Exception:
        return {}
    return schema


def _collect_motion_limits(obj: Any, title: str, adapter: Adapter, out: RunBlockers) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                limits = adapter.joint_limits.get(key) or adapter.velocity_limits.get(key)
                if limits is None:
                    continue
                lo, hi = limits
                if val < lo or val > hi:
                    out.limit_errors.append({"node": title, "param": key})
            else:
                _collect_motion_limits(val, title, adapter, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_motion_limits(item, title, adapter, out)


def _forbid_keys(
    obj: dict[str, Any], allowed: frozenset[str], path: str, issues: list[FileIssue]
) -> None:
    for key in obj:
        if key not in allowed:
            issues.append(FileIssue(_join(path, key), "unknown field"))


def _validate_obj(
    obj: Any,
    allowed: frozenset[str],
    path: str,
    issues: list[FileIssue],
    required: tuple[str, ...] = (),
) -> None:
    if not isinstance(obj, dict):
        issues.append(FileIssue(path, "must be an object"))
        return
    _forbid_keys(obj, allowed, path, issues)
    for key in required:
        if key not in obj:
            issues.append(FileIssue(f"{path}.{key}", "required"))
        elif not isinstance(obj[key], str):
            issues.append(FileIssue(f"{path}.{key}", "must be a string"))


def _is_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _is_num(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key
