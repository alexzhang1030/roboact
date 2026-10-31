"""PRD 19 canvas-perf frame conversion (Steve, Thompson)."""

from __future__ import annotations

FRAME_MS = 16.67
MAX_P95_FRAMES = 2


def ms_to_frames(dt_ms: float) -> int:
    """One rAF interval → frame count: ``round(dt_ms / 16.67)``."""
    return round(dt_ms / FRAME_MS)


def p95(values: list[int]) -> int:
    if not values:
        raise ValueError("p95 of empty series")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def p95_frames(intervals_ms: list[float]) -> int:
    return p95([ms_to_frames(dt) for dt in intervals_ms])


def within_prd19_limit(intervals_ms: list[float]) -> bool:
    """Pass when the p95 frame count is ≤ 2."""
    return p95_frames(intervals_ms) <= MAX_P95_FRAMES
