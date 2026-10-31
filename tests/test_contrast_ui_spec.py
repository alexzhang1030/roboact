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


def test_section_17_4_table_values():
    for fg, bg, light_want, dark_want, minimum in SECTION_17_4:
        assert LIGHT_CSS[fg] == LIGHT_SPEC[fg]
        assert LIGHT_CSS[bg] == LIGHT_SPEC[bg]
        assert DARK_CSS[fg] == DARK_SPEC[fg]
        assert DARK_CSS[bg] == DARK_SPEC[bg]
        light = round(contrast_ratio(LIGHT_CSS[fg], LIGHT_CSS[bg]), 2)
        dark = round(contrast_ratio(DARK_CSS[fg], DARK_CSS[bg]), 2)
        assert light == light_want, f"light {fg}/{bg}: {light} != {light_want}"
        assert dark == dark_want, f"dark {fg}/{bg}: {dark} != {dark_want}"
        assert light >= minimum, f"light {fg}/{bg}: {light} < {minimum}"
        assert dark >= minimum, f"dark {fg}/{bg}: {dark} < {minimum}"
        assert meets(light, minimum)
        assert meets(dark, TEXT_MIN if minimum >= 4.5 else NONTEXT_MIN)
