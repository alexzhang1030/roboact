from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from .adapter import ActionDescriptor, Adapter, CrashBetweenWriteAndKey
from .catalog import zh
from .util import now_iso

SIDES = {
    "read_note": "read",
    "append_note": "write",
    "clock": "none",
}


class NoteLedger:
    """SQLite file: note lines and idempotency keys share one transaction."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.isolation_level = None
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS ledger (key TEXT PRIMARY KEY, result TEXT, created_at TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, line TEXT, created_at TEXT)"
        )

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0])

    def note_lines(self) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute("SELECT line FROM notes ORDER BY id").fetchall()
            if r[0] != ""
        ]

    def append_atomic(
        self, line: str, idem_key: str | None, notes_path: Path, crash_between: bool = False
    ) -> Any:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if idem_key:
                hit = self.conn.execute(
                    "SELECT result FROM ledger WHERE key=?", (idem_key,)
                ).fetchone()
                if hit:
                    self.conn.commit()
                    return json.loads(hit[0])
            self.conn.execute("INSERT INTO notes(line, created_at) VALUES (?,?)", (line, now_iso()))
            if crash_between:
                self.conn.rollback()
                raise CrashBetweenWriteAndKey("killed between write and idempotency key")
            result = {"appended": line, "path": str(notes_path)}
            if idem_key:
                self.conn.execute(
                    "INSERT OR REPLACE INTO ledger(key,result,created_at) VALUES(?,?,?)",
                    (idem_key, json.dumps(result, ensure_ascii=False), now_iso()),
                )
            self.conn.commit()
        except CrashBetweenWriteAndKey:
            raise
        except Exception:
            self.conn.rollback()
            raise
        self._rewrite_file(notes_path)
        return result

    def _rewrite_file(self, notes_path: Path) -> None:
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(ln + "\n" for ln in self.note_lines())
        notes_path.write_text(text, encoding="utf-8")


class Tools(Adapter):
    """Test-fixture tool host. Same Adapter surface so Executor can ping it."""

    adapter_id = "tools"
    name = "test fixture"
    version = "fixture"
    target = "sim"
    action_list_version = "fixture"
    connected = True

    def __init__(self, data_dir: str | Path) -> None:
        super().__init__()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.notes = self.data_dir / "notes.txt"
        self.ledger = NoteLedger(self.data_dir / "demo_notes.db")
        self.calls: list[dict[str, Any]] = []
        self.crash_between_write_and_key = False
        self.append_delay = 0.0
        self.delay_after_commit = 0.0

    def receive_heartbeat(self) -> None:
        return

    def list_actions(self) -> list[ActionDescriptor]:
        return []

    async def read_state(self, action_id: str) -> dict[str, Any]:
        return {}

    def side(self, name: str) -> str:
        if name not in SIDES:
            raise ValueError(f"unknown tool: {name}")
        return SIDES[name]

    async def call(self, name: str, args: dict[str, Any], idem_key: str | None) -> Any:
        self.calls.append({"tool": name, "args": args, "idem_key": idem_key})
        if name == "read_note":
            if self.notes.exists():
                return {"text": self.notes.read_text(encoding="utf-8")}
            return {"text": ""}
        if name == "append_note":
            if self.append_delay:
                await asyncio.sleep(self.append_delay)
            line = str(
                args.get("line")
                or args.get("text")
                or args.get("value")
                or args.get("content")
                or args.get(zh("parse.note_content"))
                or ""
            )
            crash = self.crash_between_write_and_key
            self.crash_between_write_and_key = False
            result = self.ledger.append_atomic(line, idem_key, self.notes, crash_between=crash)
            if self.delay_after_commit:
                await asyncio.sleep(self.delay_after_commit)
            return result
        if name == "clock":
            return {"now": now_iso()}
        raise ValueError(f"unknown tool: {name}")

    def note_lines(self) -> list[str]:
        if self.ledger.note_lines():
            return self.ledger.note_lines()
        if not self.notes.exists():
            return []
        return [ln for ln in self.notes.read_text(encoding="utf-8").splitlines() if ln != ""]
