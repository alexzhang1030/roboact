"""Exported flow files are byte-stable (docs/flow-file.md section 8)."""

from __future__ import annotations

import json
from pathlib import Path
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
    parse_json,
    parse_yaml,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "flows"


def _files() -> list[Path]:
    return sorted(p for p in FIXTURES.iterdir() if p.suffix in {".json", ".yaml", ".yml"})


def _fmt(path: Path) -> str:
    return "yaml" if path.suffix in {".yaml", ".yml"} else "json"


def _dump(tree: Any, fmt: str) -> str:
    if fmt == "yaml":
        return dump_yaml(tree)
    return dump_json(tree)


def _parse(text: str, fmt: str) -> Any:
    if fmt == "yaml":
        return parse_yaml(text)
    return parse_json(text)


def _number_token(text: str, key: str) -> str:
    line = next(row.strip().rstrip(",") for row in text.splitlines() if f'"{key}":' in row)
    return line.split(": ", 1)[1]


def _reverse_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _reverse_keys(val) for key, val in reversed(list(obj.items()))}
    if isinstance(obj, list):
        return [_reverse_keys(item) for item in obj]
    return obj


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


def test_fixture_pair_exists() -> None:
    names = {path.name for path in _files()}
    assert names == {"sample.json", "sample.yaml"}


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.name)
def test_load_and_write_back_is_identical(path: Path) -> None:
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    assert "\r" not in text and "\t" not in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    for line in text.splitlines():
        spaces = len(line) - len(line.lstrip(" "))
        assert spaces % 2 == 0
    fmt = _fmt(path)
    assert _dump(_parse(text, fmt), fmt).encode("utf-8") == raw


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.name)
def test_save_twice_is_identical(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    fmt = _fmt(path)
    once = _dump(_parse(text, fmt), fmt)
    twice = _dump(_parse(once, fmt), fmt)
    assert once == twice == text
    adapter = FakeAdapter()
    graph = import_flow(text, fmt, adapter)
    first, _name = export_flow(graph, fmt, adapter)
    second, _name = export_flow(graph, fmt, adapter)
    assert first == second == text


def test_yaml_fixture_is_the_json_fixture() -> None:
    js = (FIXTURES / "sample.json").read_text(encoding="utf-8")
    ys = (FIXTURES / "sample.yaml").read_text(encoding="utf-8")
    assert dump_json(parse_yaml(ys)) == js
    assert dump_yaml(parse_json(js)) == ys


def test_reordered_keys_export_the_same_bytes() -> None:
    text = (FIXTURES / "sample.json").read_text(encoding="utf-8")
    scrambled = _reverse_keys(json.loads(text))
    raw = json.dumps(scrambled)
    assert raw != text
    assert dump_json(scrambled) == text
    adapter = FakeAdapter()
    out, _name = export_flow(import_flow(raw, "json", adapter), "json", adapter)
    assert out == text


def test_lists_keep_user_order() -> None:
    text = (FIXTURES / "sample.json").read_text(encoding="utf-8")
    tree = json.loads(text)
    ids = [node["id"] for node in tree["nodes"]]
    assert ids != sorted(ids)
    start = next(node for node in tree["nodes"] if node["id"] == "start")
    assert start["params"]["data"]["tags"] == ["z", "a", "m"]
    swapped = json.loads(text)
    swapped["nodes"][0], swapped["nodes"][1] = swapped["nodes"][1], swapped["nodes"][0]
    dumped = json.loads(dump_json(swapped))
    assert [node["id"] for node in dumped["nodes"]] == [node["id"] for node in swapped["nodes"]]
    assert dump_json(swapped) != text


def test_schema_key_order_not_alphabetical() -> None:
    tree = json.loads((FIXTURES / "sample.json").read_text(encoding="utf-8"))
    assert list(tree) == ["format", "format_version", "flow", "nodes", "edges"]
    assert list(tree) != sorted(tree)
    assert list(tree["flow"]) == [
        "id",
        "name",
        "revision",
        "step_limit",
        "action_list_version",
        "adapter",
        "robot",
        "units",
    ]
    assert list(tree["flow"]["adapter"]) == ["name", "version", "target"]
    assert list(tree["flow"]["robot"]) == ["id", "model"]
    assert list(tree["flow"]["units"]) == ["angle", "length"]
    action = next(node for node in tree["nodes"] if node["kind"] == "action")
    assert list(action) == ["id", "kind", "action", "title", "params", "position"]
    assert list(action["position"]) == ["x", "y"]
    plain = next(node for node in tree["nodes"] if node["kind"] != "action")
    assert list(plain) == ["id", "kind", "title", "params", "position"]
    edge = tree["edges"][0]
    assert list(edge) == ["from", "to"]
    assert list(edge["from"]) == ["node", "port"]
    assert list(edge["to"]) == ["node", "port"]


def test_extra_keys_follow_known_keys_in_code_point_order() -> None:
    obj = {
        "zzz": 1,
        "edges": [],
        "format_version": 1,
        "aaa": {"b": 1, "A": 2, "a": 3},
        "format": "roboact-flow",
        "nodes": [{"position": {"y": 2, "z": 9, "x": 1}, "id": "n"}],
    }
    text = dump_json(obj)
    tree = json.loads(text)
    assert list(tree) == ["format", "format_version", "nodes", "edges", "aaa", "zzz"]
    assert list(tree["aaa"]) == ["A", "a", "b"]
    assert list(tree["nodes"][0]) == ["id", "position"]
    assert list(tree["nodes"][0]["position"]) == ["x", "y", "z"]
    with pytest.raises(FlowFileRejected, match="unknown field"):
        parse_and_validate(text, "json")


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
    assert dump_json({"tiny": json.loads("1e-7")}) == dump_json({"tiny": json.loads("1e-07")})
    yaml = dump_yaml({"ratio": 0.1, "tiny": 1e-7, "one_float": 1.0, "one_int": 1})
    assert "ratio: 0.1\n" in yaml
    assert "tiny: 1e-07\n" in yaml
    assert "one_float: 1.0\n" in yaml
    assert "one_int: 1\n" in yaml
    assert "0.10000000000000001" not in yaml
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(FlowFileRejected):
            dump_json({"v": bad})
        with pytest.raises(FlowFileRejected):
            dump_yaml({"v": bad})
    for src in ("a: .nan\n", "a: .inf\n", "a: -.inf\n"):
        with pytest.raises(FlowFileRejected):
            parse_and_validate(src, "yaml")


def test_utf8_no_bom_and_one_newline() -> None:
    text = dump_json({"title": "café"})
    raw = text.encode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert "café".encode() in raw
    assert "\\u00e9" not in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\r" not in text
    yaml = dump_yaml({"title": "café"})
    assert "café" in yaml
    assert yaml.endswith("\n") and not yaml.endswith("\n\n")
    assert "\r" not in yaml


def test_moving_one_node_changes_only_that_node() -> None:
    text = (FIXTURES / "sample.json").read_text(encoding="utf-8")
    adapter = FakeAdapter()
    graph = import_flow(text, "json", adapter)
    node = next(item for item in graph["nodes"] if item["id"] == "go")
    node["x"] = int(node["x"]) + 20
    after = export_flow(graph, "json", adapter)[0]
    before_lines = text.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    changed = [i for i, (a, b) in enumerate(zip(before_lines, after_lines, strict=True)) if a != b]
    span = _node_span(before_lines, "go")
    assert changed
    assert all(index in span for index in changed)
    assert all("x" in before_lines[index] for index in changed)
    others = [item["id"] for item in graph["nodes"] if item["id"] != "go"]
    for index in changed:
        for other in others:
            assert other not in after_lines[index]
