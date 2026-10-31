from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol

from . import FORMAT_VERSION
from .catalog import en
from .flow_meta import persist_unlock_record, with_file_meta
from .util import dump, new_id, now_iso, sha256_text


class StoreLike(Protocol):
    """Run-record store. SQLite is the server default; MemoryStore is for tests."""

    path: Path
    fail_next_commit: bool

    def close(self) -> None: ...
    def list_flows(self) -> list[dict[str, Any]]: ...
    def get_flow_meta(self, flow_id: str) -> dict[str, Any] | None: ...
    def get_version(self, flow_id: str, version: int) -> dict[str, Any] | None: ...
    def get_flow(self, flow_id: str) -> dict[str, Any] | None: ...
    def latest_graph(self, flow_id: str) -> dict[str, Any] | None: ...
    def create_flow(self, name: str = "", body: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def save_flow(
        self, flow_id: str, body: dict[str, Any], bump: bool = True
    ) -> dict[str, Any]: ...
    def insert_run(self, row: dict[str, Any]) -> None: ...
    def update_run(self, run_id: str, **fields: Any) -> None: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def list_runs(self, flow_id: str | None = None) -> list[dict[str, Any]]: ...
    def unfinished_runs(self) -> list[dict[str, Any]]: ...
    def remember_request(self, request_id: str, run_id: str) -> None: ...
    def run_for_request(self, request_id: str) -> str | None: ...
    def add_step(self, rec: dict[str, Any]) -> None: ...
    def update_step(self, run_id: str, step: int, **fields: Any) -> None: ...
    def delete_step(self, run_id: str, step: int) -> None: ...
    def list_steps(
        self, run_id: str, node_id: str | None = None, after_step: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]: ...
    def get_step(self, run_id: str, step: int) -> dict[str, Any] | None: ...
    def running_steps(self, run_id: str) -> list[dict[str, Any]]: ...
    def last_step_for_node(
        self, run_id: str, node_id: str, round: int | None = None
    ) -> dict[str, Any] | None: ...
    def add_pending(
        self,
        run_id: str,
        edge_id: str,
        to_node: str,
        to_port: str,
        payload: Any,
        produced_step: int,
        retry_idem_key: str | None = None,
    ) -> int: ...
    def list_pending(self, run_id: str, unconsumed_only: bool = False) -> list[dict[str, Any]]: ...
    def consume_items(self, run_id: str, item_ids: list[int], step: int) -> None: ...
    def unconsume_items(
        self, run_id: str, step: int, retry_idem_key: str | None = None
    ) -> None: ...
    def pending_before(self, run_id: str, k: int) -> list[dict[str, Any]]: ...
    def update_unconsumed_from_step(
        self, run_id: str, produced_step: int, edge_id: str, payload: Any
    ) -> None: ...
    def add_change(
        self, run_id: str, kind: str, node_id: str | None, payload: dict[str, Any]
    ) -> int: ...
    def changes_after(self, run_id: str, seq: int) -> list[dict[str, Any]]: ...
    def last_seq(self, run_id: str) -> int: ...
    def set_breakpoint(
        self, flow_id: str, node_id: str, when: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...
    def get_breakpoint(self, flow_id: str, node_id: str, when: str) -> dict[str, Any] | None: ...
    def list_breakpoints(self, flow_id: str) -> list[dict[str, Any]]: ...
    def delete_breakpoint(self, flow_id: str, node_id: str, when: str) -> None: ...
    def set_pin(
        self,
        flow_id: str,
        node_id: str,
        port: str,
        value: Any = None,
        from_run: str | None = None,
        from_step: int | None = None,
    ) -> dict[str, Any]: ...
    def delete_pin(self, flow_id: str, node_id: str, port: str) -> None: ...
    def list_pins(self, flow_id: str) -> list[dict[str, Any]]: ...
    def add_command(
        self, run_id: str, client_id: str, kind: str, pause_id: str | None, result: str
    ) -> None: ...


class MemoryStore:
    """In-memory default. Process restart drops runs, pins, breakpoints, pending."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else Path(":memory:")
        self.fail_next_commit = False
        self._lock = threading.Lock()
        self._flows: dict[str, dict[str, Any]] = {}
        self._versions: dict[tuple[str, int], dict[str, Any]] = {}
        self._runs: dict[str, dict[str, Any]] = {}
        self._steps: dict[str, list[dict[str, Any]]] = {}
        self._pending: dict[str, list[dict[str, Any]]] = {}
        self._changes: dict[str, list[dict[str, Any]]] = {}
        self._bps: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._pins: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._commands: dict[str, list[dict[str, Any]]] = {}
        self._requests: dict[str, str] = {}

    def close(self) -> None:
        return

    def _write(self) -> None:
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("disk I/O error (test hook)")

    # --- flows ---

    def list_flows(self) -> list[dict[str, Any]]:
        return sorted(self._flows.values(), key=lambda r: r.get("updated_at") or "", reverse=True)

    def get_flow_meta(self, flow_id: str) -> dict[str, Any] | None:
        row = self._flows.get(flow_id)
        return dict(row) if row else None

    def get_version(self, flow_id: str, version: int) -> dict[str, Any] | None:
        g = self._versions.get((flow_id, int(version)))
        if not g:
            return None
        out = dict(g)
        out.update(
            {
                "id": flow_id,
                "flow_id": flow_id,
                "version": int(version),
                "format_version": FORMAT_VERSION,
            }
        )
        return out

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        return self.latest_graph(flow_id)

    def latest_graph(self, flow_id: str) -> dict[str, Any] | None:
        meta = self.get_flow_meta(flow_id)
        if not meta:
            return None
        g = self.get_version(flow_id, meta["latest_version"])
        if g is not None:
            g["name"] = meta["name"]
        return g

    def create_flow(
        self, name: str = "", body: dict[str, Any] | None = None, *, flow_id: str | None = None
    ) -> dict[str, Any]:
        name = name or en("flow.untitled")
        flow_id = flow_id or new_id("fl_")
        nodes = (body or {}).get("nodes") or [
            {"id": "start", "type": "start", "x": 240, "y": 200, "params": {"data": {}}}
        ]
        graph = {
            "id": flow_id,
            "flow_id": flow_id,
            "name": name,
            "version": 1,
            "format_version": FORMAT_VERSION,
            "nodes": nodes,
            "edges": (body or {}).get("edges") or [],
            "groups": (body or {}).get("groups") or [],
            "adapter_id": (body or {}).get("adapter_id"),
            "action_list_version": (body or {}).get("action_list_version"),
        }
        with_file_meta(graph, body)
        self._write()
        self._flows[flow_id] = {
            "flow_id": flow_id,
            "name": name,
            "latest_version": 1,
            "format_version": FORMAT_VERSION,
            "updated_at": now_iso(),
        }
        self._versions[(flow_id, 1)] = dict(graph)
        return graph

    def save_flow(self, flow_id: str, body: dict[str, Any], bump: bool = True) -> dict[str, Any]:
        meta = self.get_flow_meta(flow_id)
        if meta is None:
            return self.create_flow(body.get("name") or en("flow.untitled"), body, flow_id=flow_id)
        version = int(meta["latest_version"]) + (1 if bump else 0)
        graph = {
            "id": flow_id,
            "flow_id": flow_id,
            "name": body.get("name") or meta["name"],
            "version": version,
            "format_version": FORMAT_VERSION,
            "nodes": body.get("nodes") or [],
            "edges": body.get("edges") or [],
            "groups": body.get("groups") or [],
            "adapter_id": body.get("adapter_id"),
            "action_list_version": body.get("action_list_version"),
        }
        with_file_meta(graph, body)
        persist_unlock_record(graph, body)
        self._write()
        self._flows[flow_id] = {
            **meta,
            "name": graph["name"],
            "latest_version": version,
            "updated_at": now_iso(),
        }
        self._versions[(flow_id, version)] = dict(graph)
        return graph

    # --- runs ---

    def insert_run(self, row: dict[str, Any]) -> None:
        self._write()
        self._runs[row["run_id"]] = {
            "run_id": row["run_id"],
            "kind": row.get("kind") or "full",
            "flow_id": row["flow_id"],
            "flow_version": row["flow_version"],
            "parent_run_id": row.get("parent_run_id"),
            "base_step": row.get("base_step"),
            "status": row["status"],
            "pause": row.get("pause"),
            "max_steps": row["max_steps"],
            "steps_used": row.get("steps_used") or 0,
            "side_effects": row.get("side_effects") or "default",
            "target": row.get("target") or "sim",
            "created_at": row.get("created_at") or now_iso(),
            "finished_at": row.get("finished_at"),
            "error": row.get("error"),
            "result": row.get("result"),
        }

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        self._write()
        row = self._runs.get(run_id)
        if not row:
            return
        mapping = {"pause_json": "pause", "error_json": "error", "result_json": "result"}
        for k, v in fields.items():
            row[mapping.get(k, k)] = v

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._runs.get(run_id)
        return dict(row) if row else None

    def list_runs(self, flow_id: str | None = None) -> list[dict[str, Any]]:
        rows = list(self._runs.values())
        if flow_id:
            rows = [r for r in rows if r["flow_id"] == flow_id]
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return [
            {
                k: r.get(k)
                for k in (
                    "run_id",
                    "kind",
                    "flow_id",
                    "flow_version",
                    "status",
                    "max_steps",
                    "steps_used",
                    "created_at",
                    "finished_at",
                )
            }
            for r in rows
        ]

    def unfinished_runs(self) -> list[dict[str, Any]]:
        # Restart recovery is on hold. MemoryStore is empty after a process
        # restart.
        out: list[dict[str, Any]] = []
        for r in self._runs.values():
            if r["status"] not in ("running", "paused"):
                continue
            got = self.get_run(r["run_id"])
            if got is not None:
                out.append(got)
        return out

    def remember_request(self, request_id: str, run_id: str) -> None:
        self._requests.setdefault(request_id, run_id)

    def run_for_request(self, request_id: str) -> str | None:
        return self._requests.get(request_id)

    # --- steps ---

    def add_step(self, rec: dict[str, Any]) -> None:
        self._write()
        row = {
            "run_id": rec["run_id"],
            "step": rec["step"],
            "node_id": rec["node_id"],
            "round": rec["round"],
            "status": rec["status"],
            "input": rec.get("input"),
            "input_source": rec.get("input_source"),
            "input_ref": rec.get("input_ref"),
            "edited": rec.get("edited"),
            "output": rec.get("output"),
            "error": rec.get("error"),
            "started_at": rec.get("started_at") or now_iso(),
            "ended_at": rec.get("ended_at"),
            "duration_ms": rec.get("duration_ms"),
            "config_hash": rec.get("config_hash"),
            "upstream_hashes": rec.get("upstream_hashes") or {},
            "input_sha256": rec.get("input_sha256"),
            "side_effect": rec.get("side_effect"),
            "side_effect_mode": rec.get("side_effect_mode"),
            "idem_key": rec.get("idem_key"),
            "retry_of": rec.get("retry_of"),
            "phase": rec.get("phase"),
        }
        self._steps.setdefault(rec["run_id"], []).append(row)

    def delete_step(self, run_id: str, step: int) -> None:
        self._write()
        self._steps[run_id] = [s for s in self._steps.get(run_id, []) if s["step"] != step]

    def update_step(self, run_id: str, step: int, **fields: Any) -> None:
        self._write()
        mapping = {
            "input_json": "input",
            "output_json": "output",
            "error_json": "error",
            "edited_json": "edited",
            "upstream_hashes_json": "upstream_hashes",
        }
        for rec in self._steps.get(run_id, []):
            if rec["step"] == step:
                for k, v in fields.items():
                    rec[mapping.get(k, k)] = v
                return

    def list_steps(
        self, run_id: str, node_id: str | None = None, after_step: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        rows = [dict(s) for s in self._steps.get(run_id, []) if s["step"] > after_step]
        if node_id:
            rows = [s for s in rows if s["node_id"] == node_id]
        rows.sort(key=lambda s: s["step"])
        return rows[:limit]

    def get_step(self, run_id: str, step: int) -> dict[str, Any] | None:
        rows = self.list_steps(run_id, after_step=step - 1, limit=1)
        return rows[0] if rows and rows[0]["step"] == step else None

    def running_steps(self, run_id: str) -> list[dict[str, Any]]:
        return [s for s in self.list_steps(run_id, limit=10000) if s["status"] == "running"]

    def last_step_for_node(
        self, run_id: str, node_id: str, round: int | None = None
    ) -> dict[str, Any] | None:
        recs = [s for s in self.list_steps(run_id, node_id=node_id, limit=10000)]
        if round is not None:
            recs = [s for s in recs if s["round"] == round]
        return recs[-1] if recs else None

    # --- pending ---

    def add_pending(
        self,
        run_id: str,
        edge_id: str,
        to_node: str,
        to_port: str,
        payload: Any,
        produced_step: int,
        retry_idem_key: str | None = None,
    ) -> int:
        self._write()
        items = self._pending.setdefault(run_id, [])
        item_id = max((p["item_id"] for p in items), default=0) + 1
        items.append(
            {
                "run_id": run_id,
                "item_id": item_id,
                "edge_id": edge_id,
                "to_node": to_node,
                "to_port": to_port,
                "payload": payload,
                "produced_step": produced_step,
                "consumed_step": None,
                "retry_idem_key": retry_idem_key,
            }
        )
        return item_id

    def list_pending(self, run_id: str, unconsumed_only: bool = False) -> list[dict[str, Any]]:
        rows = [dict(p) for p in self._pending.get(run_id, [])]
        rows.sort(key=lambda p: p["item_id"])
        if unconsumed_only:
            rows = [r for r in rows if r["consumed_step"] is None]
        return rows

    def consume_items(self, run_id: str, item_ids: list[int], step: int) -> None:
        self._write()
        want = set(item_ids)
        for p in self._pending.get(run_id, []):
            if p["item_id"] in want:
                p["consumed_step"] = step

    def unconsume_items(self, run_id: str, step: int, retry_idem_key: str | None = None) -> None:
        self._write()
        for p in self._pending.get(run_id, []):
            if p.get("consumed_step") == step:
                p["consumed_step"] = None
                if retry_idem_key:
                    p["retry_idem_key"] = retry_idem_key

    def pending_before(self, run_id: str, k: int) -> list[dict[str, Any]]:
        out = []
        for r in self.list_pending(run_id):
            if r["produced_step"] < k and (r["consumed_step"] is None or r["consumed_step"] >= k):
                out.append(r)
        return out

    def update_unconsumed_from_step(
        self, run_id: str, produced_step: int, edge_id: str, payload: Any
    ) -> None:
        self._write()
        for p in self._pending.get(run_id, []):
            if (
                p["produced_step"] == produced_step
                and p["edge_id"] == edge_id
                and p["consumed_step"] is None
            ):
                p["payload"] = payload

    # --- changes / bp / pins / commands ---

    def add_change(
        self, run_id: str, kind: str, node_id: str | None, payload: dict[str, Any]
    ) -> int:
        rows = self._changes.setdefault(run_id, [])
        seq = (rows[-1]["seq"] + 1) if rows else 1
        rows.append(
            {
                "run_id": run_id,
                "seq": seq,
                "at": now_iso(),
                "kind": kind,
                "node_id": node_id,
                "payload": payload,
            }
        )
        return seq

    def changes_after(self, run_id: str, seq: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self._changes.get(run_id, []) if r["seq"] > seq]

    def last_seq(self, run_id: str) -> int:
        rows = self._changes.get(run_id) or []
        return rows[-1]["seq"] if rows else 0

    def set_breakpoint(
        self, flow_id: str, node_id: str, when: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        bp = {
            "flow_id": flow_id,
            "node_id": node_id,
            "when": when,
            "mode": body.get("mode") or "every",
            "round": body.get("round"),
            "condition": body.get("condition"),
            "enabled": bool(body.get("enabled", True)),
        }
        self._bps[(flow_id, node_id, when)] = bp
        return dict(bp)

    def get_breakpoint(self, flow_id: str, node_id: str, when: str) -> dict[str, Any] | None:
        row = self._bps.get((flow_id, node_id, when))
        return dict(row) if row else None

    def list_breakpoints(self, flow_id: str) -> list[dict[str, Any]]:
        return [dict(v) for (fid, _, _), v in self._bps.items() if fid == flow_id]

    def delete_breakpoint(self, flow_id: str, node_id: str, when: str) -> None:
        self._bps.pop((flow_id, node_id, when), None)

    def set_pin(
        self,
        flow_id: str,
        node_id: str,
        port: str,
        value: Any = None,
        from_run: str | None = None,
        from_step: int | None = None,
    ) -> dict[str, Any]:
        pin = {
            "flow_id": flow_id,
            "node_id": node_id,
            "port": port,
            "value": value,
            "from_run": from_run,
            "from_step": from_step,
            "pinned_at": now_iso(),
        }
        self._pins[(flow_id, node_id, port)] = pin
        return dict(pin)

    def delete_pin(self, flow_id: str, node_id: str, port: str) -> None:
        self._pins.pop((flow_id, node_id, port), None)

    def list_pins(self, flow_id: str) -> list[dict[str, Any]]:
        return [dict(v) for (fid, _, _), v in self._pins.items() if fid == flow_id]

    def add_command(
        self, run_id: str, client_id: str, kind: str, pause_id: str | None, result: str
    ) -> None:
        rows = self._commands.setdefault(run_id, [])
        cmd_id = (rows[-1]["cmd_id"] + 1) if rows else 1
        rows.append(
            {
                "run_id": run_id,
                "cmd_id": cmd_id,
                "client_id": client_id,
                "kind": kind,
                "pause_id": pause_id,
                "at": now_iso(),
                "result": result,
            }
        )


def open_store(path: str | Path) -> StoreLike:
    """Server default: SQLite run records. Tests use MemoryStore."""
    from .store_sqlite import SqliteStore

    return SqliteStore(path)


# Tests import Store(...) — in-memory, path ignored. Server calls
# open_store(db).
Store = MemoryStore

# silence unused import in type checkers that expect dump/sha256 from
# older Store
_ = dump
_ = sha256_text
