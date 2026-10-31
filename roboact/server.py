from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import DEFAULT_STEP_LIMIT, SSE_RETRY_MS
from .adapter import FakeAdapter
from .errors import ApiError
from .executor import Executor
from .graph import params_changed
from .nodes import bind_adapter, get_instance, list_specs, rejected_actions
from .sse import SseHub
from .store import open_store
from .util import now_iso


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DOCS = ROOT / "docs"
ASSETS = ROOT / "assets"
REASONS = {200: "OK", 201: "Created", 202: "Accepted", 204: "No Content", 400: "Bad Request",
           401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 409: "Conflict",
           410: "Gone",
           422: "Unprocessable Entity", 500: "Error"}
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

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class App:
    def __init__(self, db_path: str, test_mode: bool = False, token: str | None = None, require_token: bool = False) -> None:
        self.data_dir = str(Path(db_path).resolve().parent)
        self.store = open_store(db_path)
        self.adapter = FakeAdapter()
        bind_adapter(self.adapter)
        self.hub = SseHub(self.store)
        self.ex = Executor(self.store, self.adapter, self.hub, self.data_dir)
        self.test_mode = test_mode
        self.token = token
        self.require_token = require_token
        self._ensure_flow()

    def _demo_graph(self) -> dict[str, Any]:
        return {
            "name": "假适配器演示",
            "adapter_id": self.adapter.adapter_id,
            "action_list_version": self.adapter.action_list_version,
            "nodes": [
                {"id": "start", "type": "start", "x": 40, "y": 160, "params": {"data": {"x": 0.4, "y": 0.1, "z": 0.2}}},
                {"id": "go", "type": "fake_set_pose", "x": 300, "y": 160, "params": {"x": 0.4, "y": 0.1, "z": 0.2}},
                {"id": "grip", "type": "fake_gripper", "x": 560, "y": 160, "params": {"open": True}},
                {"id": "cam", "type": "fake_camera", "x": 300, "y": 360, "params": {}},
                {"id": "out", "type": "output", "x": 560, "y": 360, "params": {}},
            ],
            "edges": [
                {"id": "e0", "source": "start", "source_port": "data", "target": "go", "target_port": "target"},
                {"id": "e1", "source": "go", "source_port": "pose", "target": "grip", "target_port": "target"},
                {"id": "e2", "source": "grip", "source_port": "open", "target": "out", "target_port": "value"},
            ],
            "groups": [],
        }

    def _ensure_flow(self) -> dict[str, Any]:
        flows = self.store.list_flows()
        if flows:
            g = self.store.latest_graph(flows[0]["flow_id"])
            if g:
                return g
        return self.store.create_flow("假适配器演示", self._demo_graph())

    def _token_ok(self, headers: dict[str, str], query: dict[str, str]) -> bool:
        if not self.require_token:
            return True
        got = (
            headers.get("x-roboact-token")
            or headers.get("authorization", "").removeprefix("Bearer ").strip()
            or query.get("token")
            or ""
        )
        return bool(self.token) and got == self.token

    async def start(self) -> None:
        self.hub.start()
        self.ex.start_background()
        await self.ex.adopt_unfinished()

    async def handle(self, method: str, path: str, headers: dict[str, str], body: bytes, writer: asyncio.StreamWriter) -> None:
        parsed = urlparse(path)
        route = parsed.path
        query = {k: v[0] if v else "" for k, v in parse_qs(parsed.query).items()}
        if route.startswith("/api/") and not self._token_ok(headers, query):
            await self._json(writer, 401, {"error": "unauthorized", "message": "需要访问令牌。", "detail": {}})
            return
        try:
            data = json.loads(body.decode("utf-8") or "null") if body else None
        except json.JSONDecodeError:
            await self._json(writer, 400, {"error": "invalid_graph", "message": "JSON 无法解析", "detail": {}})
            return
        try:
            await self._dispatch(method, route, query, data, headers, writer)
        except ApiError as e:
            await self._json(writer, e.status, e.body())
        except FileNotFoundError:
            await self._json(writer, 404, {"error": "not_found", "message": "找不到资源", "detail": {}})
        except Exception as e:
            await self._json(writer, 500, {"error": "internal", "message": str(e), "detail": {}})

    async def _dispatch(self, method, route, query, data, headers, writer) -> None:
        if method == "OPTIONS":
            await self._raw(writer, 204, b"", "text/plain")
            return
        if route == "/api/bootstrap" and method == "GET":
            flow = self._ensure_flow()
            fid = flow["id"]
            runs = self.store.list_runs(fid)
            current = None
            records = []
            snap = None
            for r in runs:
                if r["status"] in ("running", "paused"):
                    current = r
                    snap = self.ex.snapshot(r["run_id"])
                    records = self.ex.decorate_steps(r["run_id"], self.store.list_steps(r["run_id"], limit=10000))
                    break
            await self._json(writer, 200, {
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
            })
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
            await self._json(writer, 200, {
                "node_types": list_specs(self.adapter),
                "rejected_actions": rejected_actions(self.adapter),
            })
            return
        if route == "/api/flows" and method == "GET":
            await self._json(writer, 200, {"flows": self.store.list_flows()})
            return
        if route == "/api/flows" and method == "POST":
            flow = self.store.create_flow((data or {}).get("name") or "未命名流程", data)
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
                    raise ApiError(404, "not_found", "找不到流程")
                await self._json(writer, 200, {
                    "flow": flow,
                    "pins": self.store.list_pins(fid),
                    "breakpoints": self.store.list_breakpoints(fid),
                })
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
                data["flow_id"], int(data["flow_version"]), data["node_id"],
                input_spec=data.get("input"), side_effects=data.get("side_effects") or "default",
                confirm=data.get("confirm"), client_id=data.get("client_id") or "",
                request_id=data.get("request_id"),
            )
            await self._json(writer, 201, {"run_id": rid})
            return
        if route == "/api/single-runs/group" and method == "POST":
            data = data or {}
            rid, echo = await self.ex.start_group(
                data["flow_id"], int(data["flow_version"]), list(data.get("node_ids") or []),
                entries=data.get("entries"), side_effects=data.get("side_effects") or "default",
                confirm=data.get("confirm"), client_id=data.get("client_id") or "",
                request_id=data.get("request_id"), max_steps=data.get("max_steps"),
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
                fid, int(ver),
                initial_input=data.get("initial_input"),
                max_steps=data.get("max_steps") or data.get("step_limit"),
                client_id=data.get("client_id") or "",
                request_id=data.get("request_id"),
            )
            snap = self.ex.snapshot(rid)
            await self._json(writer, 201, {
                "run_id": rid, "flow_version": snap["flow_version"],
                "max_steps": snap["max_steps"], "status": snap["status"],
                "last_event_id": snap["last_event_id"], "run": snap,
            })
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
                    raise ApiError(404, "not_found", "找不到图像")
                img = self.ex.image_at(run_id, int(parts[5]), parts[6])
                if not img:
                    raise ApiError(410, "image_gone", "图像已不在内存里")
                await self._json(writer, 200, img)
                return
            if len(parts) >= 5 and parts[4] == "steps":
                if len(parts) >= 6:
                    rec = self.store.get_step(run_id, int(parts[5]))
                    if not rec:
                        raise ApiError(404, "not_found", "找不到记录")
                    await self._json(writer, 200, self.ex.decorate_steps(run_id, [rec])[0])
                    return
                node_id = query.get("node_id")
                after_step = int(query.get("after_step") or 0)
                limit = int(query.get("limit") or 200)
                steps = self.ex.decorate_steps(run_id, self.store.list_steps(run_id, node_id=node_id, after_step=after_step, limit=limit))
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
                await self._json(writer, 200, {"steps": steps, "records": steps, "next_after_step": nxt})
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
            if action == "pause":
                out = await self.ex.pause(run_id, client)
                await self._json(writer, 202, out)
                return
            if action == "continue":
                out = await self.ex.continue_run(
                    run_id, data.get("pause_id") or "", client, data.get("edits"),
                    resolution=data.get("resolution"), readback_id=data.get("readback_id"),
                )
                await self._json(writer, 200, {**out, "run": self.ex.snapshot(run_id)})
                return
            if action == "step":
                out = await self.ex.step_run(
                    run_id, data.get("pause_id") or "", client, data.get("edits"),
                    resolution=data.get("resolution"), readback_id=data.get("readback_id"),
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
                out = await self.ex.approve(run_id, data.get("pause_id") or "", data.get("decision") or "approve", client, data.get("comment"))
                await self._json(writer, 200, {**out, "run": self.ex.snapshot(run_id)})
                return
            if action == "rerun-from":
                rid = await self.ex.rerun_from(run_id, int(data["step"]), data.get("side_effects") or "default", data.get("confirm"), client)
                snap = self.ex.snapshot(rid)
                await self._json(writer, 201, {"run_id": rid, "parent_run_id": run_id, "base_step": snap.get("step") and 0, "run": snap})
                return
            raise ApiError(404, "not_found", "未知动作")
        if self.test_mode and route == "/api/test/flood-status" and method == "POST":
            data = data or {}
            run_id = data.get("run_id") or "perf"
            n = int(data.get("n") or 500)
            ticks = int(data.get("ticks") or 20)
            statuses = ["idle", "queued", "running", "done", "error", "paused_before"]
            for t in range(ticks):
                for i in range(n):
                    self.hub.update_node(run_id, f"n{i}", {"state": statuses[(t + i) % 6], "status": statuses[(t + i) % 6]})
                await asyncio.sleep(0.01)
            await self._json(writer, 200, {"ok": True, "ticks": ticks, "events_sent": self.hub.events_sent.get(run_id, 0)})
            return
        if self.test_mode and route == "/api/test/crash-before-t2" and method == "POST":
            self.ex.hooks["crash_before_t2"] = True
            await self._json(writer, 200, {"ok": True})
            return
        if method == "GET":
            await self._static(writer, route)
            return
        raise ApiError(404, "not_found", "找不到接口")

    async def _breakpoints(self, method, route, data, writer) -> None:
        # /api/flows/{flow_id}/breakpoints/{node_id}/{when}
        rest = route[len("/api/flows/"):]
        fid, _, tail = rest.partition("/breakpoints/")
        node_id, _, when = tail.partition("/")
        if method == "PUT":
            graph = self.store.latest_graph(fid)
            node = None
            if graph:
                node = next((n for n in graph["nodes"] if n["id"] == node_id), None)
            body = data or {}
            if body.get("mode") == "condition" and node:
                field = ((body.get("condition") or {}).get("field") or "")
                inst = get_instance(node["type"])
                names = list(inst.Inputs.model_fields) + [p["name"] for p in inst.input_ports(node.get("params"))]
                top = field.split(".")[0]
                if field and top not in names:
                    raise ApiError(422, "invalid_graph", "断点条件字段不在输入模型里", {"field": field})
            bp = self.store.set_breakpoint(fid, node_id, when, body)
            self.hub.emit("_flow_" + fid, "breakpoints", {"flow_id": fid, "changed": [bp], "removed": []})
            await self._json(writer, 200, {"breakpoint": bp})
            return
        if method == "DELETE":
            self.store.delete_breakpoint(fid, node_id, when)
            await self._raw(writer, 204, b"", "text/plain")
            return
        raise ApiError(404, "not_found", "找不到接口")

    async def _pins(self, method, route, data, writer) -> None:
        rest = route[len("/api/flows/"):]
        fid, _, tail = rest.partition("/pins/")
        node_id, _, port = tail.partition("/")
        if method == "PUT":
            data = data or {}
            fr = data.get("from") or {}
            pin = self.store.set_pin(
                fid, node_id, port,
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
        raise ApiError(404, "not_found", "找不到接口")

    async def _sse(self, writer: asyncio.StreamWriter, run_id: str, after: int | None) -> None:
        head = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream; charset=utf-8\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "Access-Control-Allow-Origin: *\r\n"
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
            frame = f"id: {snap.get('last_event_id') or 0}\nevent: snapshot\ndata: {json.dumps(snap, ensure_ascii=False, default=str)}\n\n"
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
                except asyncio.TimeoutError:
                    writer.write(b": ping\n\n")
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            self.hub.unsubscribe(run_id, q)

    async def _static(self, writer: asyncio.StreamWriter, route: str) -> None:
        rel = "index.html" if route in ("", "/") else unquote(route).lstrip("/")
        if rel.startswith("docs/"):
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
        await self._raw(writer, 200, data, mime)

    async def _json(self, writer: asyncio.StreamWriter, code: int, obj: Any) -> None:
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        await self._raw(writer, code, raw, "application/json; charset=utf-8")

    async def _raw(self, writer: asyncio.StreamWriter, code: int, body: bytes, mime: str) -> None:
        reason = REASONS.get(code, "OK")
        extra = "Access-Control-Allow-Origin: *\r\nAccess-Control-Allow-Headers: *\r\nAccess-Control-Allow-Methods: *\r\n"
        head = (
            f"HTTP/1.1 {code} {reason}\r\n"
            f"Content-Type: {mime}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"{extra}"
            "Cache-Control: no-cache\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(head.encode() + body)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


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
        try:
            writer.close()
        except Exception:
            pass


def _is_loopback(host: str) -> bool:
    return host in LOOPBACK


def normalize_token(token: str | None) -> str | None:
    if token is None:
        return None
    stripped = token.strip()
    return stripped or None


def require_bind_token(host: str, token: str | None) -> None:
    """Refuse to start on a non-loopback bind when no token was provided.

    There is no built-in token. Callers must pass --token or ROBOACT_TOKEN.
    """
    if not _is_loopback(host) and not normalize_token(token):
        raise SystemExit(
            "非本机 --host 必须带 --token（也可设环境变量 ROBOACT_TOKEN）。"
            "没有令牌时只能绑定回环地址（127.0.0.1 / localhost / ::1）。"
        )


async def serve(host: str, port: int, db_path: str, test_mode: bool = False, token: str | None = None) -> None:
    token = normalize_token(token)
    require_bind_token(host, token)
    require_token = (not _is_loopback(host)) or bool(token)
    app = App(db_path, test_mode=test_mode, token=token, require_token=require_token)
    await app.start()
    server = await asyncio.start_server(lambda r, w: _client(app, r, w), host, port)
    sockets = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    print(f"roboact listening on http://{host}:{port}  ({sockets})", flush=True)
    async with server:
        await server.serve_forever()


def resolve_token(cli_token: str | None) -> str | None:
    if cli_token is not None:
        return normalize_token(cli_token)
    return normalize_token(os.environ.get("ROBOACT_TOKEN"))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Roboact")
    p.add_argument("--host", default=os.environ.get("ROBOACT_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("ROBOACT_PORT", "7465")))
    p.add_argument("--db", default=os.environ.get("ROBOACT_DB", "data/roboact.db"))
    p.add_argument(
        "--token",
        default=None,
        help="访问令牌。绑定非回环地址时必须提供。也可通过环境变量 ROBOACT_TOKEN 传入。没有默认值。",
    )
    args = p.parse_args(argv)
    test_mode = os.environ.get("ROBOACT_TEST") == "1"
    try:
        asyncio.run(serve(args.host, args.port, args.db, test_mode=test_mode, token=resolve_token(args.token)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
