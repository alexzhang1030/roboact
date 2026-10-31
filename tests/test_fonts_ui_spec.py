"""ui-spec §11: system stack for UI text, vendored JetBrains Mono for data.

If Rams revises §11 (CJK stack, sizes), update `docs/ui-spec.md` and CSS together.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "web" / "css" / "app.css").read_text(encoding="utf-8")
SPEC = (ROOT / "docs" / "ui-spec.md").read_text(encoding="utf-8")
STACK_IN_SPEC = re.search(r"`(system-ui, [^`]+)`", SPEC)
STACK = STACK_IN_SPEC.group(1) if STACK_IN_SPEC else 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif'


def test_ui_font_is_system_stack():
    assert f"--font: {STACK}" in CSS
    assert "font-family: var(--font)" in CSS


def test_data_surfaces_use_jetbrains_mono():
    assert 'font-family: "JetBrains Mono"' in CSS
    assert "--mono: \"JetBrains Mono\"" in CSS
    for needle in (".kv", ".round-nav", ".img-cap", ".loop-label", ".top .ver", ".top .right", ".fn-st-word"):
        assert needle in CSS
    assert 'font-family: var(--mono)' in CSS


def test_jetbrains_mono_is_vendored_in_manifest():
    man = json.loads((ROOT / "vendor-manifest.json").read_text(encoding="utf-8"))
    rows = [f for f in man["files"] if f["file"] == "web/vendor/fonts/JetBrainsMono-Regular.woff2"]
    assert rows, "JetBrains Mono woff2 missing from vendor-manifest.json"
    row = rows[0]
    assert row["license"] == "OFL-1.1"
    assert row.get("sha256")
    path = ROOT / row["file"]
    assert path.is_file()
    assert "cdn" not in (row.get("source_url") or "").lower()
    text = path.read_bytes()
    assert hashlib.sha256(text).hexdigest() == row["sha256"]
