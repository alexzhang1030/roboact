"""WCAG 2.2 contrast helpers used by design sheets and tests.

Relative luminance and contrast-ratio follow WCAG 2.2 §1.4.3 / §1.4.11.
Grid dots are decorative: do not assert a 3:1 floor on --grid.
"""
from __future__ import annotations


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    r, g, b = hex_rgb(hex_color)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = (la, lb) if la >= lb else (lb, la)
    return (lighter + 0.05) / (darker + 0.05)


def meets(ratio: float, minimum: float) -> bool:
    # Published table values are rounded to two decimals.
    return round(ratio, 2) >= minimum
