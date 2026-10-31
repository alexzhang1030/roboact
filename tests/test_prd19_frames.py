"""PRD 19: local canvas-perf gate is frame count, not milliseconds."""

from __future__ import annotations

from tests.prd19 import ms_to_frames, p95_frames, within_prd19_limit


def test_prd19_frame_count_threshold():
    assert ms_to_frames(33.4) == 2
    assert p95_frames([33.4]) == 2
    assert within_prd19_limit([33.4]) is True
    assert ms_to_frames(50.0) == 3
    assert p95_frames([50.0]) == 3
    assert within_prd19_limit([50.0]) is False
