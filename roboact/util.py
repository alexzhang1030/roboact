from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from .catalog import yes_tokens


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


def dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def load(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    return json.loads(text)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_path(obj: Any, path: str | None) -> Any:
    if not path:
        return obj
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def coerce_compare(left: Any, raw: Any) -> Any:
    if raw is None or left is None:
        return raw
    if isinstance(left, bool):
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in yes_tokens()
        return bool(raw)
    if isinstance(left, int) and not isinstance(left, bool):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    if isinstance(left, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return raw
    return raw


OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a is not None and b is not None and a < b,
    "le": lambda a, b: a is not None and b is not None and a <= b,
    "lte": lambda a, b: a is not None and b is not None and a <= b,
    "gt": lambda a, b: a is not None and b is not None and a > b,
    "ge": lambda a, b: a is not None and b is not None and a >= b,
    "gte": lambda a, b: a is not None and b is not None and a >= b,
    "contains": lambda a, b: a is not None and str(b) in str(a),
    "empty": lambda a, b: a in (None, "", [], {}),
    "truthy": lambda a, b: bool(a),
    "falsy": lambda a, b: not bool(a),
}


def compare(left: Any, op: str, right: Any) -> bool:
    op = op or "eq"
    fn = OPS.get(op)
    if fn is None:
        raise ValueError(f"unknown compare op: {op}")
    try:
        return bool(fn(left, coerce_compare(left, right)))
    except TypeError:
        return False
