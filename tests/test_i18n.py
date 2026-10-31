"""PRD 19.6 / ui-spec §18 — catalogs, first-load locale, persist, no hardcoded UI."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EN = ROOT / "web" / "i18n" / "en.js"
ZH = ROOT / "web" / "i18n" / "zh-CN.js"
FRONT = ROOT / "web"
CJK = re.compile(r"[\u4e00-\u9fff]")


def _catalog(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    # `export default { ... }`
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


def test_no_hardcoded_cjk_in_frontend_source():
    allowed = {EN.resolve(), ZH.resolve()}
    hits = []
    for path in FRONT.rglob("*"):
        if path.suffix not in {".js", ".html", ".css"}:
            continue
        if "vendor" in path.parts:
            continue
        if path.resolve() in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if CJK.search(text):
            for i, line in enumerate(text.splitlines(), 1):
                if CJK.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{i}:{line.strip()}")
    assert hits == [], "hard-coded CJK in frontend:\n" + "\n".join(hits)


def test_detect_browser_lang_rule():
    src = (ROOT / "web" / "js" / "prefs.js").read_text(encoding="utf-8")
    assert "function detectBrowserLang" in src
    # First load is English regardless of browser language (one-line rule).
    assert "return 'en'" in src


def test_head_script_defaults_english():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert "function detectLang()" in html
    assert "return 'en'" in html


def test_no_rtoken_catalog_keys():
    en = _catalog(EN)
    assert not any("token" in k.lower() for k in en)
