"""PRD 19.6 — catalog key parity."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EN = ROOT / "web" / "i18n" / "en.js"
ZH = ROOT / "web" / "i18n" / "zh-CN.js"


def _catalog(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    start = text.find("{")
    end = text.rfind("}")
    blob = text[start : end + 1]
    return ast.literal_eval(blob)


def test_catalogs_same_keys():
    en = _catalog(EN)
    zh = _catalog(ZH)
    assert set(en) == set(zh), {
        "missing_in_zh": sorted(set(en) - set(zh)),
        "extra_in_zh": sorted(set(zh) - set(en)),
    }
    for k, v in en.items():
        assert isinstance(v, str) and v != "", k
    for k, v in zh.items():
        assert isinstance(v, str) and v != "", k
