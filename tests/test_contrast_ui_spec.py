"""ui-spec §17.3 / §17.4: CSS tokens match the table; contrast.py is the formula.

Grid dots are decorative — they are listed in §17.4 but not asserted.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "web" / "css" / "app.css").read_text(encoding="utf-8")
SPEC = (ROOT / "docs" / "ui-spec.md").read_text(encoding="utf-8")
_spec = importlib.util.spec_from_file_location(
    "design_contrast", ROOT / "docs" / "design" / "contrast.py"
)
assert _spec is not None
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
assert hasattr(_mod, "cr"), "docs/design/contrast.py must export cr(fg, bg)"


def contrast_ratio(a: str, b: str) -> float:
    return float(_mod.cr(a, b))


def meets(ratio: float, minimum: float) -> bool:
    return round(ratio, 2) >= minimum


TEXT_MIN = 4.5
NONTEXT_MIN = 3.0


def parse_css_color(value: str) -> str:
    s = (value or "").strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return "#" + h.upper()
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
    if m:
        return f"#{int(m[1]):02X}{int(m[2]):02X}{int(m[3]):02X}"
    raise ValueError("unrecognised colour: " + s)


def parse_css_palette(css: str, selector: str) -> dict[str, str]:
    m = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert m, f"missing {selector} block in app.css"
    out: dict[str, str] = {}
    for name, hex_ in re.findall(r"--([a-z0-9]+):\s*(#[0-9A-Fa-f]{3,8})", m.group(1)):
        out[name] = parse_css_color(hex_)
    return out


def parse_uispec_section17(spec: str) -> tuple[dict[str, str], dict[str, str]]:
    start = spec.find("### 17.3")
    end = spec.find("### 17.4", start)
    assert start != -1 and end != -1, "ui-spec missing §17.3 / §17.4"
    block = spec[start:end]
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    for name, light_hex, dark_hex in re.findall(
        r"\|\s*`--([a-z0-9]+)`\s*\|[^|\n]*\|\s*`?(#[0-9A-Fa-f]{6})`?\s*\|\s*`?(#[0-9A-Fa-f]{6})",
        block,
    ):
        light[name] = parse_css_color(light_hex)
        dark[name] = parse_css_color(dark_hex)
    return light, dark


LIGHT_CSS = parse_css_palette(CSS, ":root")
DARK_CSS = parse_css_palette(CSS, '[data-theme="dark"]')
LIGHT_SPEC, DARK_SPEC = parse_uispec_section17(SPEC)

# fg, bg, light expected, dark expected, minimum. Grid is omitted (decorative).
SECTION_17_4 = (
    ("text", "surface", 17.43, 14.10, 4.5),
    ("muted", "surface", 6.72, 6.58, 4.5),
    ("muted", "surface2", 6.43, 6.20, 4.5),
    ("amber", "surface", 5.47, 7.44, 4.5),
    ("amber", "ambersoft", 4.95, 5.77, 4.5),
    ("red", "surface", 5.62, 5.55, 4.5),
    ("red", "redsoft", 4.83, 4.87, 4.5),
    ("blue", "surface", 4.63, 5.13, 4.5),
    ("green", "surface", 5.08, 6.57, 4.5),
    ("onblue", "blue", 4.63, 5.75, 4.5),
    ("edge", "bg", 3.21, 4.08, 3.0),
    ("control", "surface", 3.71, 3.50, 3.0),
    ("control", "bg", 3.43, 3.86, 3.0),
)


def test_css_matches_uispec_section17_tokens():
    needed = (
        "bg",
        "grid",
        "surface",
        "surface2",
        "text",
        "muted",
        "border",
        "control",
        "edge",
        "hair",
        "blue",
        "green",
        "red",
        "amber",
        "redsoft",
        "ambersoft",
        "onblue",
    )
    for key in needed:
        assert key in LIGHT_SPEC, f"§17.3 missing light --{key}"
        assert key in DARK_SPEC, f"§17.3 missing dark --{key}"
        assert LIGHT_SPEC[key] == LIGHT_CSS[key], f"light --{key}: spec {LIGHT_SPEC[key]} css {
            LIGHT_CSS[key]
        }"
        assert DARK_SPEC[key] == DARK_CSS[key], f"dark --{key}: spec {DARK_SPEC[key]} css {
            DARK_CSS[key]
        }"
    assert LIGHT_CSS["amber"] == "#8F6000"
    assert LIGHT_CSS["control"] == "#85857F"
    assert DARK_CSS["control"] == "#73736C"


def test_section_17_4_table_values():
    for fg, bg, light_want, dark_want, minimum in SECTION_17_4:
        light = round(contrast_ratio(LIGHT_CSS[fg], LIGHT_CSS[bg]), 2)
        dark = round(contrast_ratio(DARK_CSS[fg], DARK_CSS[bg]), 2)
        assert light == light_want, f"light {fg}/{bg}: {light} != {light_want}"
        assert dark == dark_want, f"dark {fg}/{bg}: {dark} != {dark_want}"
        assert light >= minimum, f"light {fg}/{bg}: {light} < {minimum}"
        assert dark >= minimum, f"dark {fg}/{bg}: {dark} < {minimum}"


def test_grid_dots_are_decorative_not_asserted():
    light = round(contrast_ratio(LIGHT_CSS["grid"], LIGHT_CSS["bg"]), 2)
    dark = round(contrast_ratio(DARK_CSS["grid"], DARK_CSS["bg"]), 2)
    assert light == 1.20
    assert dark == 1.28
    assert light < 3.0 and dark < 3.0


def test_operator_lost_amber_and_adapter_lost_red():
    for name, palette in (("light", LIGHT_CSS), ("dark", DARK_CSS)):
        for bg in ("bg", "surface"):
            amber = contrast_ratio(palette["amber"], palette[bg])
            red = contrast_ratio(palette["red"], palette[bg])
            assert meets(amber, TEXT_MIN), f"{name} operator_lost amber on {bg}: {amber:.2f}"
            assert meets(red, TEXT_MIN), f"{name} adapter_lost red on {bg}: {red:.2f}"


def test_disabled_button_no_opacity_in_css():
    assert re.search(r"button:disabled\s*\{[^}]*opacity:\s*1", CSS), CSS
    block = re.search(r"button\.primary:disabled\s*\{([^}]+)\}", CSS)
    assert block, "missing button.primary:disabled"
    assert "var(--bg)" in block.group(1)
    assert "var(--muted)" in block.group(1)


def test_buttons_and_inputs_use_control_border():
    assert "border: 1px solid var(--control)" in CSS
    assert re.search(r"button\s*\{[^}]*var\(--control\)", CSS)
    assert re.search(r"input,\s*select,\s*textarea\s*\{[^}]*var\(--control\)", CSS)
