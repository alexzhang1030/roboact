"""ui-spec §10: CSS matches the colour table; WCAG pairs hold in both themes.

If Rams revises ui-spec §10, update `docs/ui-spec.md` and `web/css/app.css`
together — this file reads both and fails when they drift.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "web" / "css" / "app.css").read_text(encoding="utf-8")
SPEC = (ROOT / "docs" / "ui-spec.md").read_text(encoding="utf-8")
_spec = importlib.util.spec_from_file_location("design_contrast", ROOT / "docs" / "design" / "contrast.py")
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
contrast_ratio = _mod.contrast_ratio
meets = _mod.meets
relative_luminance = _mod.relative_luminance

# Text ≥ 4.5:1; state borders / icons / edges ≥ 3.0:1 (WCAG 2.2 non-text).
TEXT_MIN = 4.5
NONTEXT_MIN = 3.0
# Grid is a 1px decorative dot field. §10 lists the colour, not a 3:1 claim.
GRID_MIN = 1.05


def parse_css_color(value: str) -> str:
    """Normalise `#rgb` / `#rrggbb` / `rgb()` / `rgba()` to `#RRGGBB`."""
    s = (value or "").strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return "#" + h.upper()
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
    if m:
        return "#{:02X}{:02X}{:02X}".format(int(m[1]), int(m[2]), int(m[3]))
    raise ValueError("unrecognised colour: " + s)


def parse_css_palette(css: str, selector: str) -> dict[str, str]:
    m = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert m, f"missing {selector} block in app.css"
    out: dict[str, str] = {}
    for name, hex_ in re.findall(r"--([a-z]+):\s*(#[0-9A-Fa-f]{3,8})", m.group(1)):
        out[name] = parse_css_color(hex_)
    return out


def parse_uispec_palettes(spec: str) -> tuple[dict[str, str], dict[str, str]]:
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    for name, light_hex, dark_hex in re.findall(
        r"\|\s*`--([a-z]+)`\s*\|\s*(#[0-9A-Fa-f]{6})\s*\|[^|]*\|\s*(#[0-9A-Fa-f]{6})",
        spec,
    ):
        light[name] = parse_css_color(light_hex)
        dark[name] = parse_css_color(dark_hex)
    return light, dark


LIGHT_CSS = parse_css_palette(CSS, ":root")
DARK_CSS = parse_css_palette(CSS, '[data-theme="dark"]')
LIGHT_SPEC, DARK_SPEC = parse_uispec_palettes(SPEC)


def _assert_text(name: str, palette: dict[str, str]) -> None:
    for fg in ("text", "muted"):
        for bg in ("bg", "surface"):
            ratio = contrast_ratio(palette[fg], palette[bg])
            assert meets(ratio, TEXT_MIN), f"{name} {fg} on {bg}: {ratio:.2f} < {TEXT_MIN}"


def _assert_state_and_chrome(name: str, palette: dict[str, str]) -> None:
    # operator_lost uses amber; adapter_lost uses red; edges use --edge.
    for fg in ("blue", "green", "red", "amber", "edge"):
        for bg in ("bg", "surface"):
            ratio = contrast_ratio(palette[fg], palette[bg])
            assert meets(ratio, NONTEXT_MIN), f"{name} {fg} on {bg}: {ratio:.2f} < {NONTEXT_MIN}"
    grid = contrast_ratio(palette["grid"], palette["bg"])
    assert grid >= GRID_MIN, f"{name} grid on bg: {grid:.2f} < {GRID_MIN}"
    # Disabled buttons: muted on bg (and surface). No opacity (§13).
    for bg in ("bg", "surface"):
        ratio = contrast_ratio(palette["muted"], palette[bg])
        assert meets(ratio, TEXT_MIN), f"{name} disabled muted on {bg}: {ratio:.2f} < {TEXT_MIN}"


def test_css_matches_uispec_section10():
    needed = ("bg", "grid", "surface", "text", "muted", "border", "edge", "blue", "green", "red", "amber", "control")
    for key in needed:
        assert LIGHT_SPEC[key] == LIGHT_CSS[key], f"light --{key}: spec {LIGHT_SPEC[key]} css {LIGHT_CSS[key]}"
        assert DARK_SPEC[key] == DARK_CSS[key], f"dark --{key}: spec {DARK_SPEC[key]} css {DARK_CSS[key]}"


def test_ui_spec_section10_contrast_both_themes():
    _assert_text("light", LIGHT_CSS)
    _assert_text("dark", DARK_CSS)
    _assert_state_and_chrome("light", LIGHT_CSS)
    _assert_state_and_chrome("dark", DARK_CSS)


def test_operator_lost_amber_and_adapter_lost_red():
    """PRD 19.5 / ui-spec §16.z: 页面断开 = amber, 机器人控制器断开 = red."""
    for name, palette in (("light", LIGHT_CSS), ("dark", DARK_CSS)):
        for bg in ("bg", "surface"):
            amber = contrast_ratio(palette["amber"], palette[bg])
            red = contrast_ratio(palette["red"], palette[bg])
            assert meets(amber, NONTEXT_MIN), f"{name} operator_lost amber on {bg}: {amber:.2f}"
            assert meets(red, NONTEXT_MIN), f"{name} adapter_lost red on {bg}: {red:.2f}"
            # Status words are 12px text — hold 4.5:1 as well (Rams' 4.50).
            assert meets(amber, TEXT_MIN), f"{name} operator_lost text amber on {bg}: {amber:.2f}"
            assert meets(red, TEXT_MIN), f"{name} adapter_lost text red on {bg}: {red:.2f}"


def test_disabled_button_no_opacity_in_css():
    assert re.search(r"button:disabled\s*\{[^}]*opacity:\s*1", CSS), CSS
    # Primary disabled uses --bg, not --border (muted-on-border is < 4.5).
    block = re.search(r"button\.primary:disabled\s*\{([^}]+)\}", CSS)
    assert block, "missing button.primary:disabled"
    assert "var(--bg)" in block.group(1)
    assert "var(--muted)" in block.group(1)


def test_edge_and_grid_pairs():
    for name, palette in (("light", LIGHT_CSS), ("dark", DARK_CSS)):
        edge_bg = contrast_ratio(palette["edge"], palette["bg"])
        edge_surface = contrast_ratio(palette["edge"], palette["surface"])
        assert meets(edge_bg, NONTEXT_MIN), f"{name} edge on bg: {edge_bg:.2f}"
        assert meets(edge_surface, NONTEXT_MIN), f"{name} edge on surface: {edge_surface:.2f}"
        grid_bg = contrast_ratio(palette["grid"], palette["bg"])
        assert grid_bg >= GRID_MIN, f"{name} grid on bg: {grid_bg:.2f}"
        # Grid stays a whisper; it must not jump to a second palette colour.
        assert grid_bg < 2.0, f"{name} grid on bg unexpectedly loud: {grid_bg:.2f}"
