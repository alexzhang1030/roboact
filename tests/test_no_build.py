"""PRD 1 零构建：仓库里没有给浏览器用的 TS 编译步骤，也没有 bundler 脚本。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "uploads", "agent-tools", "__pycache__", ".pytest_cache", "data"}


def test_no_typescript_sources():
    ts = [p for p in ROOT.rglob("*.ts") if not any(s in p.parts for s in SKIP)]
    assert ts == []


def test_no_bundler_config():
    for name in ("package.json", "vite.config.js", "webpack.config.js", "rollup.config.js", "tsconfig.json"):
        assert not (ROOT / name).exists()


def test_index_uses_import_map():
    html = (ROOT / "web/index.html").read_text()
    assert "importmap" in html
    assert "globalThis.process" in html
    assert "cdn." not in html.lower()
    assert "unpkg" not in html
    assert "jsdelivr" not in html
