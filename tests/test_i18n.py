"""PRD 19.6 / ui-spec §18 — catalogs, first-load locale, persist, no hardcoded UI."""

from __future__ import annotations

import ast
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


def _follow_browser_flag(text: str) -> str:
    m = re.search(r"FOLLOW_BROWSER_LANG\s*=\s*(true|false)", text)
    assert m, "FOLLOW_BROWSER_LANG missing"
    return m.group(1)


def test_detect_browser_lang_rule():
    src = (ROOT / "web" / "js" / "prefs.js").read_text(encoding="utf-8")
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert "function detectBrowserLang" in src
    assert "startsWith('zh')" in src
    assert "'zh-CN' : 'en'" in src or "return 'zh-CN'" in src
    assert "function detectLang()" in html
    assert "indexOf('zh')" in html
    assert _follow_browser_flag(src) == _follow_browser_flag(html)
    assert "if (!FOLLOW_BROWSER_LANG) return 'en'" in src
    assert "if (!FOLLOW_BROWSER_LANG) return 'en'" in html


# Catalog display strings that must not be pasted into frontend source.
# Short / structural tokens are allowed (units, JSON type names, locale ids).
_ALLOWED_LITERALS = {
    "",
    "en",
    "zh-CN",
    "system",
    "light",
    "dark",
    "reduce",
    "full",
    "JSON",
    "json",
    "number",
    "boolean",
    "text",
    "string",
    "F5",
    "F10",
    "F6",
}


def test_no_hardcoded_catalog_strings_in_frontend_source():
    en = _catalog(EN)
    banned = {v for v in en.values() if len(v) >= 4 and "{" not in v} - _ALLOWED_LITERALS
    hits = []
    allowed = {EN.resolve(), ZH.resolve()}
    str_lit = re.compile(r"""(['"])((?:\\.|(?!\1).){3,}?)\1""")
    for path in FRONT.rglob("*"):
        if path.suffix not in {".js", ".html"}:
            continue
        if "vendor" in path.parts:
            continue
        if path.resolve() in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "t(" in line or "CATALOGS" in line:
                continue
            for _, lit in str_lit.findall(line):
                raw = lit.encode("utf-8").decode("unicode_escape") if "\\" in lit else lit
                if raw in banned:
                    hits.append(f"{path.relative_to(ROOT)}:{i}:{raw}")
    assert hits == [], "hard-coded UI strings in frontend:\n" + "\n".join(hits)


def test_no_rtoken_catalog_keys():
    en = _catalog(EN)
    assert not any("token" in k.lower() for k in en)


def test_pref_keys_use_roboact_prefix():
    src = (ROOT / "web" / "js" / "prefs.js").read_text(encoding="utf-8")
    keys = dict(re.findall(r"^\s+(\w+): '(roboact\.[^']+)'", src, flags=re.M))
    assert keys == {
        "theme": "roboact.theme",
        "lang": "roboact.lang",
        "motion": "roboact.motion",
        "panels": "roboact.panels",
        "grid": "roboact.grid",
        "logFilter": "roboact.logFilter",
        "viewportPrefix": "roboact.viewport.",
    }
    assert "token" not in keys
    assert "readSession" not in src
    assert "writeSession" not in src
    assert "STORAGE_PREFIX = 'roboact.'" in src
    assert "key.startsWith(STORAGE_PREFIX)" in src
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    for call in re.findall(r"(?:local|session)Storage\.\w+Item\('([^']+)'\)", html + "\n" + src):
        assert call.startswith("roboact."), call
    assert "roboact-theme" not in src
    assert "roboact-theme" not in html
    i18n = (FRONT / "js" / "i18n.js").read_text(encoding="utf-8")
    assert "roboact-theme" not in i18n
