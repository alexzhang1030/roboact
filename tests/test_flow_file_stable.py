"""Canonical flow-file bytes (docs/flow-file.md section 8)."""

from __future__ import annotations

import difflib
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
    parse_yaml,
)

from .http_api import http_json
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


def _ordered(values: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: values[key] for key in keys}


def _node(
    nid: str, typ: str, x: int, y: int, params: dict[str, Any], *, flip: bool
) -> dict[str, Any]:
    body = {"id": nid, "type": typ, "x": x, "y": y, "params": params, "breakpoints": {}}
    if not flip:
        return body
    return {key: body[key] for key in reversed(list(body))}


def _edge(
    eid: str,
    source: str,
    source_port: str,
    target: str,
    target_port: str,
    *,
    flip: bool,
) -> dict[str, Any]:
    body = {
        "id": eid,
        "source": source,
        "target": target,
        "source_port": source_port,
        "target_port": target_port,
        "kind": "data",
    }
    if not flip:
        return body
    return {key: body[key] for key in reversed(list(body))}


def _save_graph(
    base: str,
    flow_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    flip: bool,
) -> None:
    """PUT the graph the way the page does in saveFlow."""
    body: dict[str, Any] = {
        "id": flow_id,
        "name": "order-check",
        "nodes": nodes,
        "edges": edges,
        "groups": [],
        "step_limit": 200,
        "bump": True,
    }
    if flip:
        body = {key: body[key] for key in reversed(list(body))}
    http_json(base, f"/api/flows/{flow_id}", "PUT", body)


def _value_only_changes(before: str, after: str) -> list[tuple[str, str]]:
    """In-place value edits. Inserts, deletes, and permuted blocks are reorders."""
    left = before.splitlines()
    right = after.splitlines()
    changes: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(a=left, b=right)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        block_a = left[i1:i2]
        block_b = right[j1:j2]
        same_len = tag == "replace" and len(block_a) == len(block_b)
        reordered = block_a != block_b and sorted(block_a) == sorted(block_b)
        if not same_len or reordered:
            diff = "\n".join(
                difflib.unified_diff(left, right, fromfile="build-a", tofile="build-b", lineterm="")
            )
            raise AssertionError(f"moved or reordered lines\n{diff}")
        changes.extend(pair for pair in zip(block_a, block_b, strict=True) if pair[0] != pair[1])
    return changes


def _assert_same_sorted_keys(left: Any, right: Any, path: str = "") -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        keys_l = list(left)
        keys_r = list(right)
        assert keys_l == keys_r, path
        assert keys_l == sorted(keys_l), path
        for key in keys_l:
            child = f"{path}.{key}" if path else key
            _assert_same_sorted_keys(left[key], right[key], child)
        return
    if isinstance(left, list) and isinstance(right, list):
        assert len(left) == len(right), path
        for index, (item_l, item_r) in enumerate(zip(left, right, strict=True)):
            _assert_same_sorted_keys(item_l, item_r, f"{path}[{index}]")


def test_build_order_export_diff_is_values_only(server: dict[str, Any]) -> None:
    """Same flow saved twice through PUT /api/flows/{id}, nodes and params in opposite order.

    Node ids are chosen by the page (`n_` plus random in addNode). The server
    stores those ids; they do not depend on creation order. Both builds use the
    same ids and only vary the order of nodes, edges, and param keys.
    """
    base = server["base"]
    pose = {"x": 0.4, "y": 0.1, "z": 0.2}
    data = {"x": 0.4, "y": 0.1, "z": 0.2}
    note = {"mode": "on", "ver": "1.10", "token": "filled"}
    nodes_spec = [
        ("n_start", "start", 40, 160, "data", data),
        ("n_go", "fake_set_pose", 300, 160, None, pose),
        ("n_note", "fake_note", 40, 320, None, note),
        ("n_out", "output", 560, 160, None, {}),
    ]
    edges_spec = [
        ("e_start", "n_start", "data", "n_go", "target"),
        ("e_go", "n_go", "pose", "n_out", "value"),
    ]

    def build(flip: bool) -> str:
        created = http_json(base, "/api/flows", "POST", {"name": "order-check"})
        flow_id = str(created["flow"]["id"])
        spec = list(reversed(nodes_spec)) if flip else list(nodes_spec)
        nodes: list[dict[str, Any]] = []
        for nid, typ, x, y, nest, params in spec:
            keys = list(reversed(list(params))) if flip else list(params)
            built = _ordered(params, keys)
            if nest:
                built = {nest: built}
            nodes.append(_node(nid, typ, x, y, built, flip=flip))
            _save_graph(base, flow_id, list(nodes), [], flip=flip)
        edges: list[dict[str, Any]] = []
        edge_spec = list(reversed(edges_spec)) if flip else list(edges_spec)
        for eid, source, source_port, target, target_port in edge_spec:
            edges.append(_edge(eid, source, source_port, target, target_port, flip=flip))
            _save_graph(base, flow_id, list(nodes), list(edges), flip=flip)
        return flow_id

    first = build(False)
    second = build(True)
    exported: dict[str, tuple[str, str]] = {}
    for fmt in ("json", "yaml"):
        left = http_json(base, "/api/flows/export", "POST", {"flow_id": first, "format": fmt})
        right = http_json(base, "/api/flows/export", "POST", {"flow_id": second, "format": fmt})
        exported[fmt] = (left["text"], right["text"])
        changes = _value_only_changes(left["text"], right["text"])
        diff = "\n".join(
            difflib.unified_diff(
                left["text"].splitlines(),
                right["text"].splitlines(),
                fromfile=f"build-a.{fmt}",
                tofile=f"build-b.{fmt}",
                lineterm="",
            )
        )
        assert len(changes) == 1, diff
        before, after = changes[0]
        assert before.split("fl_", 1)[0] == after.split("fl_", 1)[0]
        assert "fl_" in before and "fl_" in after

    js_left, js_right = exported["json"]
    _assert_same_sorted_keys(json.loads(js_left), json.loads(js_right))
    ys_left, ys_right = exported["yaml"]
    _assert_same_sorted_keys(parse_yaml(ys_left), parse_yaml(ys_right))
    ids = [node["id"] for node in json.loads(js_left)["nodes"]]
    assert ids == sorted(ids)
    assert ids != [row[0] for row in nodes_spec]
    assert ids != [row[0] for row in reversed(nodes_spec)]


def test_param_lists_keep_given_order(server: dict[str, Any]) -> None:
    """Waypoints and loop steps are not sorted on JSON or YAML export."""
    base = server["base"]
    waypoints = [
        {"y": 0.9, "x": 0.2},
        {"y": 0.0, "x": 0.1},
        {"y": 0.3, "x": 0.5},
    ]
    steps = ["third", "first", "second"]
    created = http_json(base, "/api/flows", "POST", {"name": "waypoints"})
    flow_id = str(created["flow"]["id"])
    _save_graph(
        base,
        flow_id,
        [
            _node(
                "n_go",
                "fake_set_pose",
                40,
                80,
                {"z": 0.2, "waypoints": waypoints, "steps": steps, "x": 0.4, "y": 0.1},
                flip=False,
            )
        ],
        [],
        flip=False,
    )
    exported = {
        fmt: http_json(base, "/api/flows/export", "POST", {"flow_id": flow_id, "format": fmt})[
            "text"
        ]
        for fmt in ("json", "yaml")
    }
    parsed = {
        "json": json.loads(exported["json"]),
        "yaml": parse_yaml(exported["yaml"]),
    }
    expect_xy = [{"x": 0.2, "y": 0.9}, {"x": 0.1, "y": 0.0}, {"x": 0.5, "y": 0.3}]
    assert [point["x"] for point in expect_xy] != sorted(point["x"] for point in expect_xy)
    assert steps != sorted(steps)
    for fmt, tree in parsed.items():
        params = tree["nodes"][0]["params"]
        assert params["waypoints"] == expect_xy, fmt
        assert params["steps"] == steps, fmt


def test_20_17_param_change_and_node_move(server: dict[str, Any]) -> None:
    """Acceptance §20 step 17: one param edit and one move, then export again.

    The diff is only those lines. Save uses PUT /api/flows/{id} and export uses
    POST /api/flows/export, the same calls as the page.
    """
    base = server["base"]
    created = http_json(base, "/api/flows", "POST", {"name": "order-check"})
    flow_id = str(created["flow"]["id"])
    nodes = [
        _node("n_go", "fake_set_pose", 300, 160, {"x": 0.4, "y": 0.1, "z": 0.2}, flip=False),
        _node("n_out", "output", 560, 160, {}, flip=False),
        _node("n_start", "start", 40, 160, {"data": {"x": 0.4, "y": 0.1, "z": 0.2}}, flip=False),
    ]
    edges = [
        _edge("e_start", "n_start", "data", "n_go", "target", flip=False),
        _edge("e_go", "n_go", "pose", "n_out", "value", flip=False),
    ]
    _save_graph(base, flow_id, nodes, edges, flip=False)

    def _export(fmt: str) -> str:
        body = http_json(base, "/api/flows/export", "POST", {"flow_id": flow_id, "format": fmt})
        return str(body["text"])

    before = {fmt: _export(fmt) for fmt in ("json", "yaml")}
    go = next(node for node in nodes if node["id"] == "n_go")
    go["x"] = 480
    go["params"] = {**go["params"], "z": 0.9}
    # bump false keeps revision unchanged, so the diff is the param and the move.
    http_json(
        base,
        f"/api/flows/{flow_id}",
        "PUT",
        {
            "id": flow_id,
            "name": "order-check",
            "nodes": nodes,
            "edges": edges,
            "groups": [],
            "step_limit": 200,
            "bump": False,
        },
    )
    after = {fmt: _export(fmt) for fmt in ("json", "yaml")}
    for fmt in ("json", "yaml"):
        changes = _value_only_changes(before[fmt], after[fmt])
        diff = "\n".join(
            difflib.unified_diff(
                before[fmt].splitlines(),
                after[fmt].splitlines(),
                fromfile=f"before.{fmt}",
                tofile=f"after.{fmt}",
                lineterm="",
            )
        )
        assert len(changes) == 2, diff
        old_lines = [pair[0] for pair in changes]
        new_lines = [pair[1] for pair in changes]
        assert any("0.2" in line and "0.9" not in line for line in old_lines), diff
        assert any("0.9" in line for line in new_lines), diff
        assert any("300" in line for line in old_lines), diff
        assert any("480" in line for line in new_lines), diff
