"""Canonical flow-file bytes (docs/flow-file.md section 8)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from roboact.adapter import FakeAdapter
from roboact.flow_file import (
    FlowFileRejected,
    dump_json,
    dump_yaml,
    export_flow,
    import_flow,
    parse_and_validate,
)

from .test_flow_file import sample_doc


def _reverse_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _reverse_keys(val) for key, val in reversed(list(obj.items()))}
    if isinstance(obj, list):
        return [_reverse_keys(item) for item in obj]
    return obj


def _number_token(text: str, key: str) -> str:
    line = next(row.strip().rstrip(",") for row in text.splitlines() if f'"{key}":' in row)
    return line.split(": ", 1)[1]


def _node_span(lines: list[str], node_id: str) -> range:
    id_at = next(i for i, line in enumerate(lines) if f'"id": "{node_id}"' in line)
    start = id_at
    while start > 0 and lines[start].strip() != "{":
        start -= 1
    depth = 0
    end = start
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        end = index
        if index > start and depth == 0:
            break
    return range(start, end + 1)


def _canonical_pair() -> tuple[str, str]:
    adapter = FakeAdapter()
    graph = import_flow(dump_json(sample_doc()), "json", adapter)
    js, _name = export_flow(graph, "json", adapter)
    ys, _name = export_flow(graph, "yaml", adapter)
    return js, ys


def test_trailing_newline_and_indent() -> None:
    js, ys = _canonical_pair()
    for text in (js, ys):
        raw = text.encode("utf-8")
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert "\r" not in text and "\t" not in text
        assert text.endswith("\n") and not text.endswith("\n\n")
        for line in text.splitlines():
            spaces = len(line) - len(line.lstrip(" "))
            assert spaces % 2 == 0
    flow_id = json.loads(js)["flow"]["id"]
    assert js.splitlines()[1].startswith("  ")
    assert any(line.startswith("    {") for line in js.splitlines())
    assert f'  id: "{flow_id}"' in ys.splitlines()
    assert any(line.startswith("  - ") for line in ys.splitlines())
    assert any(line.startswith("    ") and not line.startswith("    -") for line in ys.splitlines())


def test_shuffled_keys_export_the_same_bytes() -> None:
    adapter = FakeAdapter()
    original = dump_json(sample_doc())
    shuffled = json.dumps(_reverse_keys(json.loads(original)))
    assert shuffled != original
    out, _name = export_flow(import_flow(shuffled, "json", adapter), "json", adapter)
    again, _name = export_flow(import_flow(original, "json", adapter), "json", adapter)
    assert out == again
    assert dump_json(_reverse_keys(json.loads(original))) == dump_json(json.loads(original))
    tree = json.loads(out)
    assert list(tree) == sorted(tree)
    assert [node["id"] for node in tree["nodes"]] == sorted(node["id"] for node in tree["nodes"])


def test_float_spellings_and_rejected_non_finite() -> None:
    text = dump_json(
        {
            "ratio": 0.1,
            "tiny": 1e-7,
            "one_float": 1.0,
            "one_int": 1,
            "neg_zero": -0.0,
            "flag": True,
        }
    )
    assert "0.10000000000000001" not in text
    assert _number_token(text, "ratio") == "0.1"
    assert _number_token(text, "tiny") == "1e-07"
    assert _number_token(text, "one_float") == "1.0"
    assert _number_token(text, "one_int") == "1"
    assert _number_token(text, "neg_zero") == "-0.0"
    assert _number_token(text, "flag") == "true"
    assert dump_json(json.loads(text)) == text
    assert list(json.loads(text)) == ["flag", "neg_zero", "one_float", "one_int", "ratio", "tiny"]
    yaml = dump_yaml({"ratio": 0.1, "tiny": 1e-7, "one_float": 1.0, "one_int": 1})
    assert "ratio: 0.1\n" in yaml
    assert "tiny: 1e-07\n" in yaml
    assert "one_float: 1.0\n" in yaml
    assert "one_int: 1\n" in yaml
    assert "0.10000000000000001" not in yaml
    assert yaml.endswith("\n") and not yaml.endswith("\n\n")
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(FlowFileRejected):
            dump_json({"v": bad})
        with pytest.raises(FlowFileRejected):
            dump_yaml({"v": bad})
    for src in ("a: .nan\n", "a: .inf\n", "a: -.inf\n"):
        with pytest.raises(FlowFileRejected):
            parse_and_validate(src, "yaml")


def test_moving_one_node_changes_only_that_node() -> None:
    adapter = FakeAdapter()
    text, _name = export_flow(
        import_flow(dump_json(sample_doc()), "json", adapter), "json", adapter
    )
    graph = import_flow(text, "json", adapter)
    node = next(item for item in graph["nodes"] if item["id"] == "go")
    node["x"] = int(node["x"]) + 20
    after, _name = export_flow(graph, "json", adapter)
    before_lines = text.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    changed = [i for i, (a, b) in enumerate(zip(before_lines, after_lines, strict=True)) if a != b]
    span = _node_span(before_lines, "go")
    assert changed
    assert all(index in span for index in changed)
    assert all('"x"' in before_lines[index] for index in changed)
    others = [item["id"] for item in graph["nodes"] if item["id"] != "go"]
    for index in changed:
        for other in others:
            assert other not in after_lines[index]


def test_save_twice_matches() -> None:
    adapter = FakeAdapter()
    text, _name = export_flow(
        import_flow(dump_json(sample_doc()), "json", adapter), "json", adapter
    )
    second, _name = export_flow(import_flow(text, "json", adapter), "json", adapter)
    assert second == text
