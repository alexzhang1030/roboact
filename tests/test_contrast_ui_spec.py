"""ui-spec §10: table colour pairs meet WCAG contrast in both themes."""
from __future__ import annotations

# Exact table values from docs/ui-spec.md §10.
LIGHT = {
    "bg": "#F6F6F4",
    "surface": "#FFFFFF",
    "text": "#1A1A18",
    "muted": "#5C5C57",
    "blue": "#1F6FEB",
    "green": "#1A7F37",
    "red": "#C62828",
    "amber": "#9A6700",
}
DARK = {
    "bg": "#141413",
    "surface": "#1E1E1C",
    "text": "#ECECE8",
    "muted": "#A3A39C",
    "blue": "#4C8DF6",
    "green": "#3FB950",
    "red": "#F06A6A",
    "amber": "#D4A72C",
}


def _hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    r, g, b = _hex_rgb(hex_color)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = (la, lb) if la >= lb else (lb, la)
    return (lighter + 0.05) / (darker + 0.05)


def _assert_theme(name: str, palette: dict[str, str]) -> None:
    for fg in ("text", "muted"):
        for bg in ("bg", "surface"):
            ratio = contrast_ratio(palette[fg], palette[bg])
            assert ratio >= 4.5, f"{name} {fg} on {bg}: {ratio:.2f} < 4.5"
    for fg in ("blue", "green", "red", "amber"):
        for bg in ("bg", "surface"):
            ratio = contrast_ratio(palette[fg], palette[bg])
            assert ratio >= 3.0, f"{name} {fg} on {bg}: {ratio:.2f} < 3.0"


def test_ui_spec_section10_contrast_both_themes():
    _assert_theme("light", LIGHT)
    _assert_theme("dark", DARK)
