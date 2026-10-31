from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from . import FORMAT_VERSION, SQLITE_BUSY_TIMEOUT_MS
from .flow_meta import with_file_meta
from .util import dump, load, new_id, now_iso, sha256_text

SCHEMA = f"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};
CREATE TABLE IF NOT EXISTS flows (
  flow_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  latest_version INTEGER NOT NULL,
  format_version INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_versions (
  flow_id TEXT, version INTEGER, graph_json TEXT, graph_sha256 TEXT, created_at TEXT,
  PRIMARY KEY (flow_id, version));
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, kind TEXT,
  flow_id TEXT, flow_version INTEGER,
  parent_run_id TEXT, base_step INTEGER,
  status TEXT,
  pause_json TEXT, max_steps INTEGER, steps_used INTEGER,
  side_effects TEXT, created_at TEXT, finished_at TEXT, error_json TEXT, result_json TEXT);
CREATE TABLE IF NOT EXISTS steps (
  run_id TEXT, step INTEGER, node_id TEXT, round INTEGER,
  status TEXT,
  input_json TEXT, input_source TEXT, input_ref_json TEXT, edited_json TEXT,
  output_json TEXT, error_json TEXT,
  started_at TEXT, ended_at TEXT, duration_ms REAL,
  config_hash TEXT, upstream_hashes_json TEXT, input_sha256 TEXT,
  side_effect TEXT, side_effect_mode TEXT,
  idem_key TEXT, retry_of INTEGER,
  phase TEXT,
  PRIMARY KEY (run_id, step));
CREATE INDEX IF NOT EXISTS steps_by_node ON steps (run_id, node_id, round);
CREATE TABLE IF NOT EXISTS pending (
  run_id TEXT, item_id INTEGER, edge_id TEXT, to_node TEXT, to_port TEXT,
  payload_json TEXT, produced_step INTEGER, consumed_step INTEGER, retry_idem_key TEXT,
  PRIMARY KEY (run_id, item_id));
CREATE TABLE IF NOT EXISTS changes (
  run_id TEXT, seq INTEGER, at TEXT, kind TEXT, node_id TEXT, payload_json TEXT,
  PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS breakpoints (
  flow_id TEXT, node_id TEXT, "when" TEXT, mode TEXT, round INTEGER,
  condition_json TEXT, enabled INTEGER,
  PRIMARY KEY (flow_id, node_id, "when"));
CREATE TABLE IF NOT EXISTS pins (
  flow_id TEXT, node_id TEXT, port TEXT, value_json TEXT, from_run TEXT,
  from_step INTEGER, pinned_at TEXT,
  PRIMARY KEY (flow_id, node_id, port));
CREATE TABLE IF NOT EXISTS commands (
  run_id TEXT, cmd_id INTEGER, client_id TEXT, kind TEXT, pause_id TEXT, at TEXT, result TEXT,
  PRIMARY KEY (run_id, cmd_id));
CREATE TABLE IF NOT EXISTS request_ids (
  request_id TEXT PRIMARY KEY, run_id TEXT);
"""


class SqliteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()
        self.fail_next_commit = False

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(steps)").fetchall()}
        if "phase" not in cols:
            self.conn.execute("ALTER TABLE steps ADD COLUMN phase TEXT")
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _run(self, fn: Any) -> Any:
        with self._lock:
            if self.fail_next_commit:
                self.fail_next_commit = False
                raise sqlite3.OperationalError("disk I/O error (test hook)")
            return fn()

    def execute(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        def go():
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

        return self._run(go)

    def executescript_tx(self, fn: Any) -> Any:
        def go():
            try:
                self.conn.execute("BEGIN")
                result = fn(self.conn)
                if self.fail_next_commit:
                    self.fail_next_commit = False
                    raise sqlite3.OperationalError("disk I/O error (test hook)")
                self.conn.commit()
                return result
            except Exception:
                self.conn.rollback()
                raise

        return self._run(go)

    def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # --- flows / versions ---

    def list_flows(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM flows ORDER BY updated_at DESC")

    def get_flow_meta(self, flow_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM flows WHERE flow_id=?", (flow_id,))

    def get_version(self, flow_id: str, version: int) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM flow_versions WHERE flow_id=? AND version=?", (flow_id, version)
        )
        if not row:
            return None
        g = load(row["graph_json"], {})
        g.update(
            {
                "id": flow_id,
                "flow_id": flow_id,
                "version": version,
                "format_version": FORMAT_VERSION,
            }
        )
        return g

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
        self, name: str = "未命名流程", body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        flow_id = (body or {}).get("id") or (body or {}).get("flow_id") or new_id("fl_")
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
        raw = dump(graph)

        def go(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute(
                "INSERT INTO flows(flow_id,name,latest_version,format_version,updated_at) "
                "VALUES(?,?,?,?,?)",
                (flow_id, name, 1, FORMAT_VERSION, now_iso()),
            )
            conn.execute(
                "INSERT INTO flow_versions(flow_id,version,graph_json,graph_sha256,created_at) "
                "VALUES(?,?,?,?,?)",
                (flow_id, 1, raw, sha256_text(raw), now_iso()),
            )
            return graph

        return self.executescript_tx(go)

    def save_flow(self, flow_id: str, body: dict[str, Any], bump: bool = True) -> dict[str, Any]:
        meta = self.get_flow_meta(flow_id)
        if meta is None:
            return self.create_flow(body.get("name") or "未命名流程", {**body, "id": flow_id})
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
        raw = dump(graph)

        def go(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute(
                "UPDATE flows SET name=?, latest_version=?, updated_at=? WHERE flow_id=?",
                (graph["name"], version, now_iso(), flow_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO "
                "flow_versions(flow_id,version,graph_json,graph_sha256,created_at) "
                "VALUES(?,?,?,?,?)",
                (flow_id, version, raw, sha256_text(raw), now_iso()),
            )
            return graph

        return self.executescript_tx(go)

    # --- runs ---

    def insert_run(self, row: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO runs(
               run_id,kind,flow_id,flow_version,parent_run_id,base_step,status,pause_json,
               max_steps,steps_used,side_effects,created_at,finished_at,error_json,result_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["run_id"],
                row.get("kind") or "full",
                row["flow_id"],
                row["flow_version"],
                row.get("parent_run_id"),
                row.get("base_step"),
                row["status"],
                dump(row.get("pause")) if row.get("pause") else None,
                row["max_steps"],
                row.get("steps_used") or 0,
                row.get("side_effects") or "default",
                row.get("created_at") or now_iso(),
                row.get("finished_at"),
                dump(row.get("error")) if row.get("error") else None,
                dump(row.get("result")) if row.get("result") else None,
            ),
        )

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, args = [], []
        mapping = {"pause": "pause_json", "error": "error_json", "result": "result_json"}
        for k, v in fields.items():
            col = mapping.get(k, k)
            cols.append(f"{col}=?")
            if col.endswith("_json") and not isinstance(v, str) and v is not None:
                args.append(dump(v))
            else:
                args.append(v)
        args.append(run_id)
        self.execute(f"UPDATE runs SET {', '.join(cols)} WHERE run_id=?", tuple(args))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not row:
            return None
        row["pause"] = load(row.pop("pause_json"), None)
        row["error"] = load(row.pop("error_json"), None)
        row["result"] = load(row.pop("result_json"), None)
        return row

    def list_runs(self, flow_id: str | None = None) -> list[dict[str, Any]]:
        if flow_id:
            return self._all(
                "SELECT run_id,kind,flow_id,flow_version,status,max_steps,"
                "steps_used,created_at,finished_at FROM runs "
                "WHERE flow_id=? ORDER BY created_at DESC",
                (flow_id,),
            )
        return self._all(
            "SELECT run_id,kind,flow_id,flow_version,status,max_steps,"
            "steps_used,created_at,finished_at FROM runs "
            "ORDER BY created_at DESC"
        )

    def unfinished_runs(self) -> list[dict[str, Any]]:
        ids = self._all("SELECT run_id FROM runs WHERE status IN ('running','paused')")
        out: list[dict[str, Any]] = []
        for r in ids:
            got = self.get_run(r["run_id"])
            if got is not None:
                out.append(got)
        return out

    def remember_request(self, request_id: str, run_id: str) -> None:
        self.execute(
            "INSERT OR IGNORE INTO request_ids(request_id,run_id) VALUES(?,?)", (request_id, run_id)
        )

    def run_for_request(self, request_id: str) -> str | None:
        row = self._one("SELECT run_id FROM request_ids WHERE request_id=?", (request_id,))
        return row["run_id"] if row else None

    # --- steps ---

    def add_step(self, rec: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO steps(
               run_id,step,node_id,round,status,input_json,input_source,input_ref_json,
               edited_json,output_json,error_json,started_at,ended_at,duration_ms,
               config_hash,upstream_hashes_json,input_sha256,
               side_effect,side_effect_mode,idem_key,retry_of,phase)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["run_id"],
                rec["step"],
                rec["node_id"],
                rec["round"],
                rec["status"],
                dump(rec.get("input")),
                rec.get("input_source"),
                dump(rec.get("input_ref")),
                dump(rec.get("edited")),
                dump(rec.get("output")),
                dump(rec.get("error")) if rec.get("error") else None,
                rec.get("started_at") or now_iso(),
                rec.get("ended_at"),
                rec.get("duration_ms"),
                rec.get("config_hash"),
                dump(rec.get("upstream_hashes")),
                rec.get("input_sha256"),
                rec.get("side_effect"),
                rec.get("side_effect_mode"),
                rec.get("idem_key"),
                rec.get("retry_of"),
                rec.get("phase"),
            ),
        )

    def delete_step(self, run_id: str, step: int) -> None:
        self.execute("DELETE FROM steps WHERE run_id=? AND step=?", (run_id, step))

    def update_step(self, run_id: str, step: int, **fields: Any) -> None:
        mapping = {
            "input": "input_json",
            "output": "output_json",
            "error": "error_json",
            "edited": "edited_json",
            "upstream_hashes": "upstream_hashes_json",
        }
        cols, args = [], []
        for k, v in fields.items():
            col = mapping.get(k, k)
            cols.append(f"{col}=?")
            if col.endswith("_json") and not isinstance(v, str) and v is not None:
                args.append(dump(v))
            else:
                args.append(v)
        args.extend([run_id, step])
        self.execute(f"UPDATE steps SET {', '.join(cols)} WHERE run_id=? AND step=?", tuple(args))

    def list_steps(
        self, run_id: str, node_id: str | None = None, after_step: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        if node_id:
            rows = self._all(
                "SELECT * FROM steps WHERE run_id=? AND node_id=? AND step>? ORDER BY step LIMIT ?",
                (run_id, node_id, after_step, limit),
            )
        else:
            rows = self._all(
                "SELECT * FROM steps WHERE run_id=? AND step>? ORDER BY step LIMIT ?",
                (run_id, after_step, limit),
            )
        for r in rows:
            r["input"] = load(r.pop("input_json"), {})
            r["output"] = load(r.pop("output_json"), {})
            r["error"] = load(r.pop("error_json"), None)
            r["edited"] = load(r.pop("edited_json"), None)
            r["input_ref"] = load(r.pop("input_ref_json"), None)
            r["upstream_hashes"] = load(r.pop("upstream_hashes_json"), {})
        return rows

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

    def next_item_id(self, run_id: str) -> int:
        row = self._one(
            "SELECT COALESCE(MAX(item_id),0) AS m FROM pending WHERE run_id=?", (run_id,)
        )
        return int(row["m"]) + 1 if row else 1

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
        item_id = self.next_item_id(run_id)
        self.execute(
            """INSERT INTO pending(
               run_id,item_id,edge_id,to_node,to_port,payload_json,
               produced_step,consumed_step,retry_idem_key)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                item_id,
                edge_id,
                to_node,
                to_port,
                dump(payload),
                produced_step,
                None,
                retry_idem_key,
            ),
        )
        return item_id

    def list_pending(self, run_id: str, unconsumed_only: bool = False) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM pending WHERE run_id=? ORDER BY item_id", (run_id,))
        for r in rows:
            r["payload"] = load(r.pop("payload_json"), None)
        if unconsumed_only:
            rows = [r for r in rows if r["consumed_step"] is None]
        return rows

    def consume_items(self, run_id: str, item_ids: list[int], step: int) -> None:
        for i in item_ids:
            self.execute(
                "UPDATE pending SET consumed_step=? WHERE run_id=? AND item_id=?", (step, run_id, i)
            )

    def unconsume_items(self, run_id: str, step: int, retry_idem_key: str | None = None) -> None:
        if retry_idem_key:
            self.execute(
                "UPDATE pending SET consumed_step=NULL, retry_idem_key=? WHERE run_id=? AND "
                "consumed_step=?",
                (retry_idem_key, run_id, step),
            )
        else:
            self.execute(
                "UPDATE pending SET consumed_step=NULL WHERE run_id=? AND consumed_step=?",
                (run_id, step),
            )

    def pending_before(self, run_id: str, k: int) -> list[dict[str, Any]]:
        """Items produced before k and not consumed before k."""
        rows = self.list_pending(run_id)
        out = []
        for r in rows:
            if r["produced_step"] < k and (r["consumed_step"] is None or r["consumed_step"] >= k):
                out.append(r)
        return out

    def update_unconsumed_from_step(
        self, run_id: str, produced_step: int, edge_id: str, payload: Any
    ) -> None:
        self.execute(
            "UPDATE pending SET payload_json=? WHERE run_id=? AND produced_step=? AND edge_id=? "
            "AND consumed_step IS NULL",
            (dump(payload), run_id, produced_step, edge_id),
        )

    # --- changes / bp / pins / commands ---

    def add_change(
        self, run_id: str, kind: str, node_id: str | None, payload: dict[str, Any]
    ) -> int:
        row = self._one("SELECT COALESCE(MAX(seq),0) AS m FROM changes WHERE run_id=?", (run_id,))
        seq = int(row["m"]) + 1 if row else 1
        self.execute(
            "INSERT INTO changes(run_id,seq,at,kind,node_id,payload_json) VALUES(?,?,?,?,?,?)",
            (run_id, seq, now_iso(), kind, node_id, dump(payload)),
        )
        return seq

    def changes_after(self, run_id: str, seq: int) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM changes WHERE run_id=? AND seq>? ORDER BY seq", (run_id, seq)
        )
        for r in rows:
            r["payload"] = load(r.pop("payload_json"), {})
        return rows

    def last_seq(self, run_id: str) -> int:
        row = self._one("SELECT COALESCE(MAX(seq),0) AS m FROM changes WHERE run_id=?", (run_id,))
        return int(row["m"]) if row else 0

    def set_breakpoint(
        self, flow_id: str, node_id: str, when: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        self.execute(
            """INSERT OR REPLACE INTO breakpoints(
               flow_id,node_id,"when",mode,round,condition_json,enabled)
               VALUES(?,?,?,?,?,?,?)""",
            (
                flow_id,
                node_id,
                when,
                body.get("mode") or "every",
                body.get("round"),
                dump(body.get("condition")) if body.get("condition") else None,
                1 if body.get("enabled", True) else 0,
            ),
        )
        row = self.get_breakpoint(flow_id, node_id, when)
        if row is None:
            raise RuntimeError("breakpoint missing after write")
        return row

    def get_breakpoint(self, flow_id: str, node_id: str, when: str) -> dict[str, Any] | None:
        row = self._one(
            'SELECT * FROM breakpoints WHERE flow_id=? AND node_id=? AND "when"=?',
            (flow_id, node_id, when),
        )
        if not row:
            return None
        row["condition"] = load(row.pop("condition_json"), None)
        row["enabled"] = bool(row["enabled"])
        row["when"] = row.pop("when") if "when" in row else when
        return row

    def list_breakpoints(self, flow_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM breakpoints WHERE flow_id=?", (flow_id,))
        for r in rows:
            r["condition"] = load(r.pop("condition_json"), None)
            r["enabled"] = bool(r["enabled"])
        return rows

    def delete_breakpoint(self, flow_id: str, node_id: str, when: str) -> None:
        self.execute(
            'DELETE FROM breakpoints WHERE flow_id=? AND node_id=? AND "when"=?',
            (flow_id, node_id, when),
        )

    def set_pin(
        self,
        flow_id: str,
        node_id: str,
        port: str,
        value: Any = None,
        from_run: str | None = None,
        from_step: int | None = None,
    ) -> dict[str, Any]:
        self.execute(
            "INSERT OR REPLACE INTO "
            "pins(flow_id,node_id,port,value_json,from_run,from_step,pinned_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                flow_id,
                node_id,
                port,
                dump(value) if value is not None else None,
                from_run,
                from_step,
                now_iso(),
            ),
        )
        return {
            "flow_id": flow_id,
            "node_id": node_id,
            "port": port,
            "value": value,
            "from_run": from_run,
            "from_step": from_step,
        }

    def delete_pin(self, flow_id: str, node_id: str, port: str) -> None:
        self.execute(
            "DELETE FROM pins WHERE flow_id=? AND node_id=? AND port=?", (flow_id, node_id, port)
        )

    def list_pins(self, flow_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM pins WHERE flow_id=?", (flow_id,))
        for r in rows:
            r["value"] = load(r.pop("value_json"), None)
        return rows

    def add_command(
        self, run_id: str, client_id: str, kind: str, pause_id: str | None, result: str
    ) -> None:
        row = self._one(
            "SELECT COALESCE(MAX(cmd_id),0) AS m FROM commands WHERE run_id=?", (run_id,)
        )
        cmd_id = int(row["m"]) + 1 if row else 1
        self.execute(
            "INSERT INTO commands(run_id,cmd_id,client_id,kind,pause_id,at,result) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, cmd_id, client_id, kind, pause_id, now_iso(), result),
        )


# Leftover disk implementation. Do not extend. Default store is MemoryStore.
Store = SqliteStore
