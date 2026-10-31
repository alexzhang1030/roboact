"""PRD 19.6 — catalog key parity. Correction 13 — no zh defaults in storage."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from roboact.catalog import en

from tests.http_api import http_json

ROOT = Path(__file__).resolve().parents[1]
EN = ROOT / "web" / "i18n" / "en.js"
ZH = ROOT / "web" / "i18n" / "zh-CN.js"
HAN = re.compile(r"[\u3400-\u9fff]")
ZH_DEFAULT_KEYS = (
    "flow.demo",
    "flow.untitled",
    "confirm.default",
    "adapter.generic",
    "adapter.fake",
)


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


def _han_paths(obj: object, path: str = "$") -> list[str]:
    """Han in JSON, skipping per-locale labels['zh-CN'] (allowed bilingual dicts)."""
    if isinstance(obj, str):
        return [path] if HAN.search(obj) else []
    if isinstance(obj, dict):
        hits: list[str] = []
        for key, val in obj.items():
            if key == "zh-CN":
                continue
            hits.extend(_han_paths(val, f"{path}.{key}"))
        return hits
    if isinstance(obj, list):
        hits: list[str] = []
        for i, val in enumerate(obj):
            hits.extend(_han_paths(val, f"{path}[{i}]"))
        return hits
    return []


def test_python_defaults_are_not_zh() -> None:
    hits: list[str] = []
    for path in (ROOT / "roboact").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for key in ZH_DEFAULT_KEYS:
            needle = f'zh("{key}")'
            if needle in text:
                hits.append(f"{path.relative_to(ROOT)}:{needle}")
    assert hits == [], hits


def test_han_only_in_zh_cn_catalog() -> None:
    skip_parts = {".git", ".venv", "vendor", "node_modules", "__pycache__"}
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or skip_parts.intersection(path.parts):
            continue
        if path.name == "zh-CN.js":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if HAN.search(text):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_en_api_stored_values_have_no_han(server) -> None:
    boot = http_json(server["base"], "/api/bootstrap")
    listed = http_json(server["base"], "/api/flows")
    created = http_json(server["base"], "/api/flows", "POST", {})
    flow = boot["flow"]
    adapter = boot["adapter"]
    new_flow = created["flow"]
    assert flow["name"] == en("flow.demo")
    assert adapter["name"] == en("adapter.fake")
    assert HAN.search(adapter["source"] or "") is None
    assert all(HAN.search(row["name"] or "") is None for row in listed["flows"])
    assert new_flow["name"] == en("flow.untitled")
    assert HAN.search(json.dumps(flow, ensure_ascii=False)) is None
    assert HAN.search(json.dumps(listed, ensure_ascii=False)) is None
    assert HAN.search(json.dumps(new_flow, ensure_ascii=False)) is None
    assert _han_paths(boot) == []
    prompt = next(
        spec["default_params"].get("prompt")
        for spec in boot["node_types"]
        if spec.get("type") == "human_confirm"
    )
    assert prompt == en("confirm.default")
