"""PRD 19.6 — catalog key parity."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EN = ROOT / "web" / "i18n" / "en.js"
ZH = ROOT / "web" / "i18n" / "zh-CN.js"


def _catalog(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    start = text.find("{")
    end = text.rfind("}")
    blob = text[start : end + 1]
    return ast.literal_eval(blob)


# ui-spec v1 "Keys to add" (36).
UI_SPEC_KEYS = (
    "run.target_sim",
    "run.stop_rejected_note",
    "run.action_missing_count",
    "run.disabled_no_controller",
    "error.readback_required",
    "error.action_missing",
    "readback.read",
    "src.cache_time",
    "edge.open_panel",
    "edge.image_preview",
    "group.back_edge",
    "side.write_detail",
    "params.no_expiry",
    "params.no_range",
    "palette.source",
    "port.image_size",
    "status.waiting",
    "status.summary.result_unknown",
    "status.summary.waiting_precondition",
    "flow.export",
    "flow.import",
    "flow.export_format",
    "flow.format_json",
    "flow.format_yaml",
    "flow.import_failed",
    "flow.import_error",
    "flow.mismatch_adapter",
    "flow.mismatch_actions",
    "flow.mismatch_robot",
    "flow.review_enable",
    "flow.mismatch_units",
    "flow.run_locked",
    "run.param_out_of_limit",
    "run.param_type_mismatch",
    "run.missing_input_count",
    "status.missing_input",
)


def test_catalogs_same_keys():
    en = _catalog(EN)
    zh = _catalog(ZH)
    assert set(en) == set(zh), {
        "missing_in_zh": sorted(set(en) - set(zh)),
        "extra_in_zh": sorted(set(zh) - set(en)),
    }
    for k, v in en.items():
        assert isinstance(v, str) and v != "", k
    for k, v in zh.items():
        assert isinstance(v, str) and v != "", k


def test_ui_spec_keys_present():
    en = _catalog(EN)
    zh = _catalog(ZH)
    assert len(UI_SPEC_KEYS) == 36
    assert "flow.read_only" not in en
    assert "flow.read_only" not in zh
    missing_en = [k for k in UI_SPEC_KEYS if k not in en]
    missing_zh = [k for k in UI_SPEC_KEYS if k not in zh]
    assert missing_en == []
    assert missing_zh == []
