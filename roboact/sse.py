from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from .store import StoreLike


class SseHub:
    def __init__(self, store: StoreLike | None = None, interval: float = 0.1) -> None:
        self.store = store
        self.interval = interval
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._dirty: dict[str, dict[str, Any]] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._seq_from: dict[str, int] = {}
        self._immediate: set[str] = set()
        self._task: asyncio.Task | None = None
        self.events_sent: dict[str, int] = {}

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=80)
        self._subs.setdefault(run_id, []).append(q)
        return q

    def subscriber_count(self, run_id: str) -> int:
        return len(self._subs.get(run_id) or [])

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(run_id) or []
        if q in subs:
            subs.remove(q)
        if not subs:
            self._subs.pop(run_id, None)

    def note_change(self, run_id: str, node_id: str | None, payload: dict[str, Any]) -> int:
        if self.store is not None:
            seq = self.store.add_change(run_id, payload.get("kind") or "node", node_id, payload)
        else:
            self._local_seq = getattr(self, "_local_seq", 0) + 1
            seq = self._local_seq
        if node_id:
            bucket = self._dirty.setdefault(run_id, {})
            bucket[node_id] = payload
            self._seq_from.setdefault(run_id, seq)
        meta = self._meta.setdefault(run_id, {})
        meta.update(
            {
                k: v
                for k, v in payload.items()
                if k in ("steps_used", "max_steps", "edges_active", "status")
            }
        )
        meta["seq_to"] = seq
        return seq

    def update_node(self, run_id: str, node_id: str, payload: dict[str, Any]) -> int:
        return self.note_change(run_id, node_id, payload)

    def flush(self, run_id: str) -> dict[str, Any] | None:
        nodes = dict(self._dirty.get(run_id) or {})
        frame = self.flush_nodes(run_id)
        if not frame:
            return None
        return {"nodes": nodes, "frame": frame}

    def flush_nodes(self, run_id: str) -> str | None:
        nodes = self._dirty.pop(run_id, None)
        meta = self._meta.pop(run_id, {})
        if not nodes and not meta:
            return None
        seq_from = self._seq_from.pop(run_id, meta.get("seq_to") or 1)
        seq_to = meta.get("seq_to") or seq_from
        body = {
            "run_id": run_id,
            "seq_from": seq_from,
            "seq_to": seq_to,
            "steps_used": meta.get("steps_used"),
            "max_steps": meta.get("max_steps"),
            "nodes": [{"node_id": nid, **st} for nid, st in (nodes or {}).items()],
            "edges_active": meta.get("edges_active") or [],
        }
        return self._emit(run_id, "nodes", body, seq_to)

    def emit(self, run_id: str, event: str, data: dict[str, Any], seq: int | None = None) -> str:
        resolved: int
        if seq is not None:
            resolved = seq
        elif self.store is not None:
            resolved = self.store.add_change(run_id, event, data.get("node_id"), data)
        else:
            self._local_seq = int(getattr(self, "_local_seq", 0)) + 1
            resolved = self._local_seq
        return self._emit(run_id, event, data, resolved)

    def flush_now(self, run_id: str) -> None:
        self.flush_nodes(run_id)

    def _emit(self, run_id: str, event: str, data: dict[str, Any], seq: int) -> str:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        frame = f"id: {seq}\nevent: {event}\ndata: {payload}\n\n"
        dead = []
        for q in list(self._subs.get(run_id, [])):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                snap = json.dumps({"overflow": True, **data}, ensure_ascii=False, default=str)
                try:
                    q.put_nowait(f"id: {seq}\nevent: snapshot\ndata: {snap}\n\n")
                except asyncio.QueueFull:
                    dead.append(q)
        for q in dead:
            self.unsubscribe(run_id, q)
        self.events_sent[run_id] = self.events_sent.get(run_id, 0) + 1
        return frame

    def replay_after(self, run_id: str, after: int) -> list[str]:
        if self.store is None:
            return []
        rows = self.store.changes_after(run_id, after)
        if not rows:
            return []
        nodes: dict[str, Any] = {}
        last_pause = None
        last_finish = None
        for r in rows:
            kind = r["kind"]
            p = r["payload"]
            if (kind in ("node", "nodes") or r.get("node_id")) and r.get("node_id"):
                nodes[r["node_id"]] = p
            if kind == "paused":
                last_pause = (r["seq"], p)
            if kind == "run_finished":
                last_finish = (r["seq"], p)
        frames = []
        if nodes:
            seq_to = rows[-1]["seq"]
            body = {
                "run_id": run_id,
                "seq_from": rows[0]["seq"],
                "seq_to": seq_to,
                "nodes": [{"node_id": i, **s} for i, s in nodes.items()],
                "edges_active": [],
            }
            frames.append(
                f"id: {seq_to}\nevent: nodes\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
            )
        if last_pause:
            frames.append(
                f"id: {last_pause[0]}\nevent: paused\ndata: {
                    json.dumps(last_pause[1], ensure_ascii=False)
                }\n\n"
            )
        if last_finish:
            frames.append(
                f"id: {last_finish[0]}\nevent: run_finished\ndata: {
                    json.dumps(last_finish[1], ensure_ascii=False)
                }\n\n"
            )
        return frames

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.interval)
                for run_id in list(self._dirty.keys()) + list(self._meta.keys()):
                    self.flush_nodes(run_id)
        except asyncio.CancelledError:
            for run_id in list(self._dirty):
                self.flush_nodes(run_id)
            raise
