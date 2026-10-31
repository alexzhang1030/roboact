"""ui-spec §17: Inter + JetBrains Mono from the package static dir; CJK is system-only."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "web" / "css" / "app.css").read_text(encoding="utf-8")
SPEC = (ROOT / "docs" / "ui-spec.md").read_text(encoding="utf-8")
STACK = '"Inter", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", sans-serif'
MONO = '"JetBrains Mono", ui-monospace, monospace'
FONT_FILES = (
    "roboact/static/fonts/InterVariable.woff2",
    "roboact/static/fonts/JetBrainsMono-Regular.woff2",
    "roboact/static/fonts/JetBrainsMono-Medium.woff2",
)


def test_ui_font_is_inter_plus_system_cjk():
    assert f"--font: {STACK}" in CSS
    assert "font-family: var(--font)" in CSS
    assert "system-ui" not in CSS.split("--font:")[1].split(";")[0]
    assert STACK.replace('"', "") in SPEC.replace("`", "") or 'font-family: "Inter"' in SPEC


def test_data_surfaces_use_jetbrains_mono_tabular():
    assert '--mono: "JetBrains Mono"' in CSS or f"--mono: {MONO}" in CSS
    assert "font-variant-numeric: tabular-nums" in CSS
    for needle in (
        ".kv",
        ".round-nav",
        ".img-cap",
        ".loop-label",
        ".top .ver",
        ".top .right",
        ".fn-st-word",
    ):
        assert needle in CSS
    assert "font-family: var(--mono)" in CSS


def test_font_face_is_local_block_display():
    assert 'src: url("/static/fonts/InterVariable.woff2")' in CSS
    assert 'src: url("/static/fonts/JetBrainsMono-Regular.woff2")' in CSS
    assert 'src: url("/static/fonts/JetBrainsMono-Medium.woff2")' in CSS
    assert CSS.count("font-display: block") >= 3
    assert "font-display: swap" not in CSS
    assert not re.search(r"@font-face[^}]+https?://", CSS)


def test_package_fonts_are_in_manifest_with_ofl():
    man = json.loads((ROOT / "vendor-manifest.json").read_text(encoding="utf-8"))
    by_file = {f["file"]: f for f in man["files"]}
    for rel in FONT_FILES:
        row = by_file[rel]
        assert row["license"] == "OFL-1.1"
        assert row["license_file"].startswith("vendor/licenses/")
        assert (ROOT / row["license_file"]).is_file()
        path = ROOT / rel
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        assert "cdn" not in (row.get("source_url") or "").lower()
    assert (ROOT / "roboact/static/fonts/Inter-LICENSE.txt").is_file()
    assert (ROOT / "roboact/static/fonts/JetBrainsMono-OFL.txt").is_file()
    sizes = {rel: (ROOT / rel).stat().st_size for rel in FONT_FILES}
    assert sizes["roboact/static/fonts/InterVariable.woff2"] == 352240
    assert sizes["roboact/static/fonts/JetBrainsMono-Regular.woff2"] == 92164
    assert sizes["roboact/static/fonts/JetBrainsMono-Medium.woff2"] == 93824


def test_type_scale_is_exactly_five_sizes():
    for px, lh, _weight_hint in (
        ("16", "24", "600"),
        ("14", "20", "600"),
        ("13", "20", "400"),
        ("12", "18", None),
        ("11", "16", "400"),
    ):
        assert f"--fs-{px}: {px}px" in CSS
        assert f"--lh-{px}: {lh}px" in CSS
    assert CSS.count("font-size: 11px") == 0 or ".kbd" in CSS
    assert "font-size: var(--fs-11)" in CSS
    assert "font-size: var(--fs-16)" in CSS


def test_fonts_array_merged_as_is_and_sha256_matches_shipped():
    man = json.loads((ROOT / "vendor-manifest.json").read_text(encoding="utf-8"))
    src = json.loads((ROOT / "fonts" / "vendor-manifest.fonts.json").read_text(encoding="utf-8"))
    assert man["fonts"] == src["fonts"]
    shipped = ROOT / "roboact" / "static" / "fonts"
    for row in src["fonts"]:
        path = shipped / row["file"]
        assert path.is_file(), row["file"]
        assert path.stat().st_size == row["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        assert row["license"] == "OFL-1.1"
        assert row["version"]
        assert row["source"].startswith("https://")
