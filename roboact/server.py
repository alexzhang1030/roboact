from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import DEFAULT_STEP_LIMIT, SSE_RETRY_MS
from .adapter import FakeAdapter
from .catalog import zh
from .errors import ApiError
from .executor import Executor
from .graph import params_changed
from .nodes import bind_adapter, get_instance, list_specs, rejected_actions
from .sse import SseHub
from .store import open_store
from .util import now_iso

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
WEB = ROOT / "web"
DOCS = ROOT / "docs"
ASSETS = ROOT / "assets"
PKG_STATIC = PKG / "static"
REASONS = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    410: "Gone",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    500: "Error",
}
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".map": "application/json",
}

ALLOWED_HOST_NAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
COMMAND_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def parse_host_header(raw: str) -> tuple[str, str]:
    h = (raw or "").strip()
    if not h:
        return "", ""
    if h.startswith("["):
        end = h.find("]")
        if end < 0:
            return h.lower(), ""
        name = h[: end + 1].lower()
        rest = h[end + 1 :]
        port = rest[1:] if rest.startswith(":") else ""
        return name, port
    if h.count(":") == 1:
        name, port = h.split(":", 1)
        return name.lower(), port
    return h.lower(), ""


def host_allowed(host_header: str, _bind_port: int | None) -> bool:
    """Loopback Host only. Port may differ from the bind (SSH / TCP proxy)."""
    name, _port = parse_host_header(host_header)
    return name in ALLOWED_HOST_NAMES


def origin_allowed(origin: str, host_header: str) -> bool:
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    req_name, req_port = parse_host_header(host_header)
    req_host = req_name.strip("[]")
    if host != req_host and not ({host, req_host} <= {"127.0.0.1", "localhost"}):
        return False
    origin_port = parsed.port
    if origin_port is None:
        origin_port = 443 if parsed.scheme == "https" else 80
    return not (req_port and str(origin_port) != req_port)


def request_origin(headers: dict[str, str]) -> str:
    """EventSource on Chromium is same-origin and often omits Origin; Referer is the page."""
    origin = (headers.get("origin") or "").strip()
    if origin:
        return origin
    referer = (headers.get("referer") or "").strip()
    if not referer:
        return ""
    try:
        parsed = urlparse(referer)
    except Exception:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def content_type_is_json(headers: dict[str, str]) -> bool:
    ct = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    return ct == "application/json"


class App:
    def __init__(self, db_path: str, test_mode: bool = False, port: int | None = None) -> None:
        self.data_dir = str(Path(db_path).resolve().parent)
        self.store = open_store(db_path)
        self.adapter = FakeAdapter()
        bind_adapter(self.adapter)
        self.hub = SseHub(self.store)
        self.ex = Executor(self.store, self.adapter, self.hub, self.data_dir)
        self.test_mode = test_mode
        self.port = port
        self._ensure_flow()

    def _demo_graph(self) -> dict[str, Any]:
        return {
            "name": zh("flow.demo"),
            "adapter_id": self.adapter.adapter_id,
            "action_list_version": self.adapter.action_list_version,
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "x": 40,
                    "y": 160,
                    "params": {"data": {"x": 0.4, "y": 0.1, "z": 0.2}},
                },
                {
                    "id": "go",
                    "type": "fake_set_pose",
                    "x": 300,
                    "y": 160,
                    "params": {"x": 0.4, "y": 0.1, "z": 0.2},
                },
                {
                    "id": "grip",
                    "type": "fake_gripper",
                    "x": 560,
                    "y": 160,
                    "params": {"open": True},
                },
                {"id": "cam", "type": "fake_camera", "x": 300, "y": 360, "params": {}},
                {"id": "out", "type": "output", "x": 560, "y": 360, "params": {}},
            ],
            "edges": [
                {
                    "id": "e0",
                    "source": "start",
                    "source_port": "data",
                    "target": "go",
                    "target_port": "target",
                },
                {
                    "id": "e1",
                    "source": "go",
                    "source_port": "pose",
                    "target": "grip",
                    "target_port": "target",
                },
                {
                    "id": "e2",
                    "source": "grip",
                    "source_port": "open",
                    "target": "out",
                    "target_port": "value",
                },
            ],
            "groups": [],
        }

    def _ensure_flow(self) -> dict[str, Any]:
        flows = self.store.list_flows()
        if flows:
            g = self.store.latest_graph(flows[0]["flow_id"])
            if g:
                return g
        return self.store.create_flow(zh("flow.demo"), self._demo_graph())

    def _pick_flow(self, wanted: str | None) -> dict[str, Any]:
        if wanted:
            g = self.store.latest_graph(wanted)
            if g:
                return g
        return self._ensure_flow()

    async def start(self) -> None:
        self.hub.start()
        self.ex.start_background()
        await self.ex.adopt_unfinished()

    async def handle(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        parsed = urlparse(path)
        route = parsed.path
        query = {k: v[0] if v else "" for k, v in parse_qs(parsed.query).items()}
        if not host_allowed(headers.get("host") or "", self.port):
            await self._json(
                writer, 403, {"error": "bad_host", "message": "bad_host", "detail": {}}
            )
            return
        if (headers.get("upgrade") or "").lower() == "websocket":
            if not origin_allowed(headers.get("origin") or "", headers.get("host") or ""):
                await self._json(
                    writer, 403, {"error": "bad_origin", "message": "bad_origin", "detail": {}}
                )
                return
            await self._json(
                writer, 400, {"error": "no_websocket", "message": "no_websocket", "detail": {}}
            )
            return
        is_events = route.endswith("/events")
        if is_events and not origin_allowed(request_origin(headers), headers.get("host") or ""):
            await self._json(
                writer, 403, {"error": "bad_origin", "message": "bad_origin", "detail": {}}
            )
            return
        if (
            route.startswith("/api/")
            and method in COMMAND_METHODS
            and not content_type_is_json(headers)
        ):
            await self._json(
                writer,
                415,
                {"error": "bad_content_type", "message": "bad_content_type", "detail": {}},
            )
            return
        try:
            data = json.loads(body.decode("utf-8") or "null") if body else None
        except json.JSONDecodeError:
            await self._json(
                writer, 400, {"error": "invalid_graph", "message": "invalid_graph", "detail": {}}
            )
            return
        try:
            await self._dispatch(method, route, query, data, headers, writer)
        except ApiError as e:
            await self._json(writer, e.status, e.body())
        except FileNotFoundError:
            await self._json(
                writer, 404, {"error": "not_found", "message": "not_found", "detail": {}}
            )
        except Exception as e:
            await self._json(
                writer,
                500,
                {"error": "internal", "message": "internal", "detail": {"type": type(e).__name__}},
            )

    async def _dispatch(
        self,
        method: str,
        route: str,
        query: dict[str, str],
        data: Any,
        headers: dict[str, str],
        writer: asyncio.StreamWriter,
    ) -> None:
        if method == "OPTIONS":
            await self._raw(writer, 204, b"", "text/plain")
            return
        if method == "HEAD":
            await self._static(writer, route, head=True)
            return
        if route == "/api/bootstrap" and method == "GET":
            flow = self._pick_flow(query.get("flow_id") or query.get("flow"))
            fid = flow["id"]
            runs = self.store.list_runs(fid)
            current = None
            records = []
            snap = None
            for r in runs:
                if r["status"] in ("running", "paused"):
                    current = r
                    snap = self.ex.snapshot(r["run_id"])
                    records = self.ex.decorate_steps(
                        r["run_id"], self.store.list_steps(r["run_id"], limit=10000)
                    )
                    break
            await self._json(
                writer,
                200,
                {
                    "flow": flow,
                    "node_types": [t for t in list_specs(self.adapter)],
                    "rejected_actions": rejected_actions(self.adapter),
                    "adapter": self.adapter.public(),
                    "tools": [],
                    "runs": runs[:30],
                    "run": snap or current,
                    "records": records,
                    "pins": self.store.list_pins(fid),
                    "breakpoints": self.store.list_breakpoints(fid),
                    "step_limit_default": DEFAULT_STEP_LIMIT,
                },
            )
            return
        if route == "/api/adapter" and method == "GET":
            await self._json(writer, 200, self.adapter.public())
            return
        if route == "/api/adapter/refresh" and method == "POST":
            bind_adapter(self.adapter)
            await self._json(writer, 200, self.adapter.public())
            return
        if route == "/api/robot/state" and method == "GET":
            action_id = query.get("action") or query.get("action_id") or ""
            state = await self.adapter.read_state(action_id)
            await self._json(writer, 200, {"state": state, "at": now_iso()})
            return
        if route == "/api/node-types" and method == "GET":
            await self._json(
                writer,
                200,
                {
                    "node_types": list_specs(self.adapter),
                    "rejected_actions": rejected_actions(self.adapter),
                },
            )
            return
        if route == "/api/flows" and method == "GET":
            await self._json(writer, 200, {"flows": self.store.list_flows()})
            return
        if route == "/api/flows" and method == "POST":
            flow = self.store.create_flow((data or {}).get("name") or zh("flow.untitled"), data)
            await self._json(writer, 200, {"flow": flow})
            return
        if route.startswith("/api/flows/") and "/breakpoints/" in route:
            await self._breakpoints(method, route, data, writer)
            return
        if route.startswith("/api/flows/") and "/pins/" in route:
            await self._pins(method, route, data, writer)
            return
        if route.startswith("/api/flows/") and method in ("GET", "PUT"):
            fid = route.split("/", 3)[-1]
            if method == "GET":
                flow = self.store.latest_graph(fid)
                if not flow:
                    raise ApiError(404, "not_found", "flow not found")
                await self._json(
                    writer,
                    200,
                    {
                        "flow": flow,
                        "pins": self.store.list_pins(fid),
                        "breakpoints": self.store.list_breakpoints(fid),
                    },
                )
                return
            old = self.store.latest_graph(fid)
            bump = bool((data or {}).get("bump", True))
            body = dict(data or {})
            body.setdefault("adapter_id", self.adapter.adapter_id)
            body.setdefault("action_list_version", self.adapter.action_list_version)
            flow = self.store.save_flow(fid, body, bump=bump)
            if old and params_changed(old, flow):
                pass
            self.ex.notify_version(fid, flow["version"])
            await self._json(writer, 200, {"flow": flow})
            return
        if route == "/api/single-runs/node" and method == "POST":
            data = data or {}
            rid = await self.ex.start_node(
                data["flow_id"],
                int(data["flow_version"]),
                data["node_id"],
                input_spec=data.get("input"),
                side_effects=data.get("side_effects") or "default",
                confirm=data.get("confirm"),
                client_id=data.get("client_id") or "",
                request_id=data.get("request_id"),
            )
            await self._json(writer, 201, {"run_id": rid})
            return
        if route == "/api/single-runs/group" and method == "POST":
            data = data or {}
            rid, echo = await self.ex.start_group(
                data["flow_id"],
                int(data["flow_version"]),
                list(data.get("node_ids") or []),
                entries=data.get("entries"),
                side_effects=data.get("side_effects") or "default",
                confirm=data.get("confirm"),
                client_id=data.get("client_id") or "",
                request_id=data.get("request_id"),
                max_steps=data.get("max_steps"),
                source_run_id=data.get("source_run_id"),
            )
            await self._json(writer, 201, {"run_id": rid, "entries": echo})
            return
        if route == "/api/runs" and method == "GET":
            await self._json(writer, 200, {"runs": self.store.list_runs(query.get("flow_id"))})
            return
        if route == "/api/runs" and method == "POST":
            data = data or {}
            fid = data.get("flow_id") or self._ensure_flow()["id"]
            ver = data.get("flow_version")
            if ver is None:
                meta = self.store.get_flow_meta(fid)
                ver = meta["latest_version"] if meta else 1
            rid = await self.ex.start_full(
                fid,
                int(ver),
                initial_input=data.get("initial_input"),
                max_steps=data.get("max_steps") or data.get("step_limit"),
                client_id=data.get("client_id") or "",
                request_id=data.get("request_id"),
            )
            snap = self.ex.snapshot(rid)
            await self._json(
                writer,
                201,
                {
                    "run_id": rid,
                    "flow_version": snap["flow_version"],
                    "max_steps": snap["max_steps"],
                    "status": snap["status"],
                    "last_event_id": snap["last_event_id"],
                    "run": snap,
                },
            )
            return
        if route.startswith("/api/runs/") and route.endswith("/events") and method == "GET":
            run_id = route.split("/")[3]
            after = headers.get("last-event-id") or query.get("after") or ""
            try:
                after_n = int(after) if after != "" else None
            except ValueError:
                after_n = None
            await self._sse(writer, run_id, after_n)
            return
        if route.startswith("/api/runs/") and method == "GET":
            parts = route.split("/")
            run_id = parts[3]
            if len(parts) >= 5 and parts[4] == "images":
                if len(parts) < 7:
                    raise ApiError(404, "not_found", "image not found")
                img = self.ex.image_at(run_id, int(parts[5]), parts[6])
                if not img:
                    raise ApiError(410, "image_gone", "image is no longer in memory")
                await self._json(writer, 200, img)
                return
            if len(parts) >= 5 and parts[4] == "steps":
                if len(parts) >= 6:
                    rec = self.store.get_step(run_id, int(parts[5]))
                    if not rec:
                        raise ApiError(404, "not_found", "record not found")
                    await self._json(writer, 200, self.ex.decorate_steps(run_id, [rec])[0])
                    return
                node_id = query.get("node_id")
                after_step = int(query.get("after_step") or 0)
                limit = int(query.get("limit") or 200)
                steps = self.ex.decorate_steps(
                    run_id,
                    self.store.list_steps(
                        run_id, node_id=node_id, after_step=after_step, limit=limit
                    ),
                )
                for s in steps:
                    raw = json.dumps(s.get("input"), ensure_ascii=False, default=str)
                    if len(raw) > 65536:
                        s["input"] = {"truncated": True, "preview": raw[:200]}
                        s["inputs"] = s["input"]
                    raw = json.dumps(s.get("output"), ensure_ascii=False, default=str)
                    if len(raw) > 65536:
                        s["output"] = {"truncated": True, "preview": raw[:200]}
                        s["outputs"] = s["output"]
                nxt = steps[-1]["step"] if steps else after_step
                await self._json(
                    writer, 200, {"steps": steps, "records": steps, "next_after_step": nxt}
                )
                return
            snap = self.ex.snapshot(run_id)
            recs = self.ex.decorate_steps(run_id, self.store.list_steps(run_id, limit=10000))
            await self._json(writer, 200, {**snap, "run": snap, "records": recs, "steps": recs})
            return
        if route.startswith("/api/runs/") and method == "POST":
            parts = route.split("/")
            run_id = parts[3]
            action = parts[4] if len(parts) > 4 else ""
            data = data or {}
            client = data.get("client_id") or ""
            if action == "ui-beat":
                self.ex.note_ui(run_id)
                await self._json(writer, 200, {"ok": True})
                return
            if action == "pause":
                out = await self.ex.pause(run_id, client)
                await self._json(writer, 202, out)
                return
            if action == "continue":
                out = await self.ex.continue_run(
                    run_id,
                    data.get("pause_id") or "",
                    client,
                    data.get("edits"),
                    resolution=data.get("resolution"),
                    readback_id=data.get("readback_id"),
                )
                await self._json(writer, 200, {**out, "run": self.ex.snapshot(run_id)})
                return
            if action == "step":
                out = await self.ex.step_run(
                    run_id,
                    data.get("pause_id") or "",
                    client,
                    data.get("edits"),
                    resolution=data.get("resolution"),
                    readback_id=data.get("readback_id"),
                )
                await self._json(writer, 200, {**out, "run": self.ex.snapshot(run_id)})
                return
            if action == "readback":
                out = await self.ex.readback(run_id)
                await self._json(writer, 200, {"readback": out, "run": self.ex.snapshot(run_id)})
                return
            if action == "stop":
                out = await self.ex.stop_run(run_id, client)
                await self._json(writer, 200, out)
                return
            if action == "approve":
                out = await self.ex.approve(
                    run_id,
                    data.get("pause_id") or "",
                    data.get("decision") or "approve",
                    client,
                    data.get("comment"),
                )
                await self._json(writer, 200, {**out, "run": self.ex.snapshot(run_id)})
                return
            if action == "rerun-from":
                rid = await self.ex.rerun_from(
                    run_id,
                    int(data["step"]),
                    data.get("side_effects") or "default",
                    data.get("confirm"),
                    client,
                )
                snap = self.ex.snapshot(rid)
                await self._json(
                    writer,
                    201,
                    {
                        "run_id": rid,
                        "parent_run_id": run_id,
                        "base_step": snap.get("step") and 0,
                        "run": snap,
                    },
                )
                return
            raise ApiError(404, "not_found", "unknown action")
        if self.test_mode and route == "/api/test/flood-status" and method == "POST":
            data = data or {}
            run_id = data.get("run_id") or "perf"
            n = int(data.get("n") or 500)
            ticks = int(data.get("ticks") or 20)
            statuses = ["idle", "queued", "running", "done", "error", "paused_before"]
            for t in range(ticks):
                for i in range(n):
                    self.hub.update_node(
                        run_id,
                        f"n{i}",
                        {"state": statuses[(t + i) % 6], "status": statuses[(t + i) % 6]},
                    )
                await asyncio.sleep(0.01)
            await self._json(
                writer,
                200,
                {"ok": True, "ticks": ticks, "events_sent": self.hub.events_sent.get(run_id, 0)},
            )
            return
        if self.test_mode and route == "/api/test/crash-before-t2" and method == "POST":
            self.ex.hooks["crash_before_t2"] = True
            await self._json(writer, 200, {"ok": True})
            return
        if self.test_mode and route == "/api/test/step-delay" and method == "POST":
            self.ex.step_delay_s = float((data or {}).get("seconds") or 0)
            await self._json(writer, 200, {"ok": True, "seconds": self.ex.step_delay_s})
            return
        if self.test_mode and route == "/api/test/adapter" and method == "POST":
            data = data or {}
            if "async_steps" in data:
                self.adapter.async_steps = int(data["async_steps"])
            if "async_sleep_s" in data:
                self.adapter.async_sleep_s = float(data["async_sleep_s"])
            if "block_s" in data:
                self.adapter.block_s = float(data["block_s"])
            await self._json(
                writer,
                200,
                {
                    "ok": True,
                    "async_steps": self.adapter.async_steps,
                    "async_sleep_s": getattr(self.adapter, "async_sleep_s", 0.04),
                    "block_s": self.adapter.block_s,
                },
            )
            return
        if method == "GET":
            await self._static(writer, route)
            return
        raise ApiError(404, "not_found", "endpoint not found")

    async def _breakpoints(
        self,
        method: str,
        route: str,
        data: Any,
        writer: asyncio.StreamWriter,
    ) -> None:
        # /api/flows/{flow_id}/breakpoints/{node_id}/{when}
        rest = route[len("/api/flows/") :]
        fid, _, tail = rest.partition("/breakpoints/")
        node_id, _, when = tail.partition("/")
        if method == "PUT":
            graph = self.store.latest_graph(fid)
            node = None
            if graph:
                node = next((n for n in graph["nodes"] if n["id"] == node_id), None)
            body = data or {}
            if body.get("mode") == "condition" and node:
                field = (body.get("condition") or {}).get("field") or ""
                inst = get_instance(node["type"])
                names = list(inst.Inputs.model_fields) + [
                    p["name"] for p in inst.input_ports(node.get("params"))
                ]
                top = field.split(".")[0]
                if field and top not in names:
                    raise ApiError(
                        422,
                        "invalid_graph",
                        "breakpoint field is not on the input model",
                        {"field": field},
                    )
            bp = self.store.set_breakpoint(fid, node_id, when, body)
            self.hub.emit(
                "_flow_" + fid, "breakpoints", {"flow_id": fid, "changed": [bp], "removed": []}
            )
            await self._json(writer, 200, {"breakpoint": bp})
            return
        if method == "DELETE":
            self.store.delete_breakpoint(fid, node_id, when)
            await self._raw(writer, 204, b"", "text/plain")
            return
        raise ApiError(404, "not_found", "endpoint not found")

    async def _pins(
        self,
        method: str,
        route: str,
        data: Any,
        writer: asyncio.StreamWriter,
    ) -> None:
        rest = route[len("/api/flows/") :]
        fid, _, tail = rest.partition("/pins/")
        node_id, _, port = tail.partition("/")
        if method == "PUT":
            data = data or {}
            fr = data.get("from") or {}
            pin = self.store.set_pin(
                fid,
                node_id,
                port,
                value=data.get("value"),
                from_run=fr.get("run_id") or data.get("from_run"),
                from_step=fr.get("step") or data.get("from_step"),
            )
            await self._json(writer, 200, {"pin": pin})
            return
        if method == "DELETE":
            self.store.delete_pin(fid, node_id, port)
            await self._raw(writer, 204, b"", "text/plain")
            return
        raise ApiError(404, "not_found", "endpoint not found")

    async def _sse(self, writer: asyncio.StreamWriter, run_id: str, after: int | None) -> None:
        head = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream; charset=utf-8\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "\r\n"
        )
        writer.write(head.encode())
        writer.write(f"retry: {SSE_RETRY_MS}\n\n".encode())
        await writer.drain()
        if after is None:
            try:
                snap = self.ex.snapshot(run_id)
            except ApiError:
                snap = {"run_id": run_id, "status": "unknown", "nodes": {}}
            frame = f"id: {snap.get('last_event_id') or 0}\nevent: snapshot\ndata: {
                json.dumps(snap, ensure_ascii=False, default=str)
            }\n\n"
            writer.write(frame.encode())
            await writer.drain()
        else:
            for frame in self.hub.replay_after(run_id, after):
                writer.write(frame.encode())
            await writer.drain()
        q = self.hub.subscribe(run_id)
        self.ex.note_ui(run_id)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                    writer.write(payload.encode() if isinstance(payload, str) else payload)
                    await writer.drain()
                except TimeoutError:
                    writer.write(b": ping\n\n")
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            self.hub.unsubscribe(run_id, q)

    async def _static(self, writer: asyncio.StreamWriter, route: str, head: bool = False) -> None:
        rel = "index.html" if route in ("", "/") else unquote(route).lstrip("/")
        if rel.startswith("static/"):
            target = (PKG_STATIC / rel[7:]).resolve()
            root = PKG_STATIC.resolve()
        elif rel.startswith("docs/fonts/"):
            # Design sheets load ../fonts from /docs/design/; same files as the
            # package.
            target = (PKG_STATIC / "fonts" / rel[11:]).resolve()
            root = (PKG_STATIC / "fonts").resolve()
        elif rel.startswith("docs/"):
            target = (DOCS / rel[5:]).resolve()
            root = DOCS.resolve()
        elif rel.startswith("assets/"):
            target = (ASSETS / rel[7:]).resolve()
            root = ASSETS.resolve()
        elif rel.startswith("web/"):
            target = (WEB / rel[4:]).resolve()
            root = WEB.resolve()
        else:
            target = (WEB / rel).resolve()
            root = WEB.resolve()
        if not str(target).startswith(str(root)):
            await self._raw(writer, 403, b"forbidden", "text/plain")
            return
        if not target.is_file():
            await self._raw(writer, 404, b"not found", "text/plain")
            return
        data = target.read_bytes()
        ext = target.suffix.lower()
        mime = MIME.get(ext) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        await self._raw(writer, 200, data, mime, head=head)

    async def _json(self, writer: asyncio.StreamWriter, code: int, obj: Any) -> None:
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        await self._raw(writer, code, raw, "application/json; charset=utf-8")

    async def _raw(
        self, writer: asyncio.StreamWriter, code: int, body: bytes, mime: str, head: bool = False
    ) -> None:
        reason = REASONS.get(code, "OK")
        extra = ""
        header = (
            f"HTTP/1.1 {code} {reason}\r\n"
            f"Content-Type: {mime}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"{extra}"
            "Cache-Control: no-cache\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(header.encode() + (b"" if head else body))
        await writer.drain()
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
    header_bytes = await reader.readuntil(b"\r\n\r\n")
    head, _ = header_bytes.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length") or 0)
    body = await reader.readexactly(n) if n else b""
    return method, path, headers, body


async def _client(app: App, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        method, path, headers, body = await asyncio.wait_for(_read_request(reader), timeout=30)
        await app.handle(method, path, headers, body, writer)
    except Exception:
        with contextlib.suppress(Exception):
            writer.close()


BIND_HOST = "127.0.0.1"


def refuse_host_flag(argv: list[str] | None = None) -> None:
    """PRD v1.4.10: `--host` is not provided. Bind is 127.0.0.1 only."""
    args = sys.argv[1:] if argv is None else argv
    if "--host" not in args:
        return
    print(
        "error: --host is not provided. Bind is 127.0.0.1 only. "
        "To use the UI from another computer, SSH port-forward: "
        "ssh -L 17465:127.0.0.1:17465 <controller>",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(2)


async def serve(port: int, db_path: str, test_mode: bool = False) -> None:
    app = App(db_path, test_mode=test_mode, port=port)
    await app.start()
    server = await asyncio.start_server(lambda r, w: _client(app, r, w), BIND_HOST, port)
    sockets = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    print(f"roboact listening on http://{BIND_HOST}:{port}  ({sockets})", flush=True)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    refuse_host_flag(argv)
    p = argparse.ArgumentParser(description="Roboact")
    p.add_argument("--port", type=int, default=int(os.environ.get("ROBOACT_PORT", "7465")))
    p.add_argument("--db", default=os.environ.get("ROBOACT_DB", "data/roboact.db"))
    args = p.parse_args(argv)
    test_mode = os.environ.get("ROBOACT_TEST") == "1"
    try:
        asyncio.run(serve(args.port, args.db, test_mode=test_mode))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
