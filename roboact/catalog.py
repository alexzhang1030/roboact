"""Load UI catalogs. Product Chinese lives only in web/i18n/zh-CN.js."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PAIR = re.compile(r"'((?:\\'|[^'])*)':\s*'((?:\\'|[^'])*)'")


@cache
def load(locale: str) -> dict[str, str]:
    name = "en.js" if locale == "en" else "zh-CN.js"
    text = (_ROOT / "web" / "i18n" / name).read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for m in _PAIR.finditer(text):
        out[m.group(1).replace("\\'", "'")] = m.group(2).replace("\\'", "'").replace("\\n", "\n")
    return out


def zh(key: str) -> str:
    return load("zh-CN")[key]


def en(key: str) -> str:
    return load("en")[key]


def yes_tokens() -> tuple[str, ...]:
    return ("1", "true", "yes", zh("parse.yes").lower())
