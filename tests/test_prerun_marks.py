"""Pre-run Start/End marks appear only after Run is refused, then clear."""

from __future__ import annotations

from pathlib import Path

import pytest
from roboact.flow_file import dump_json

from tests.conftest import launch_chromium
from tests.http_api import put_flow
from tests.test_ui import _launch, _page

pw = pytest.importorskip("playwright.sync_api")


def _browser(p):
    return launch_chromium(p)


def _two_end_graph() -> dict:
    return {
        "name": "two ends",
        "nodes": [
            {"id": "start", "type": "start", "x": 80, "y": 200, "params": {"data": {"v": 1}}},
            {"id": "e1", "type": "end", "x": 400, "y": 80, "params": {}},
            {"id": "e2", "type": "end", "x": 400, "y": 280, "params": {}},
        ],
        "edges": [
            {
                "id": "a",
                "source": "start",
                "source_port": "data",
                "target": "e1",
                "target_port": "value",
                "kind": "data",
            },
            {
                "id": "b",
                "source": "start",
                "source_port": "data",
                "target": "e2",
                "target_port": "value",
                "kind": "data",
            },
        ],
        "bump": True,
    }


def _open(br, base):
    page, _blocked, _errs = _page(br, base)
    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=30000)
    page.evaluate("() => window.__af.setLocaleMode('en')")
    page.wait_for_function("() => window.__af.slot1Disabled.value === false", timeout=15000)
    return page


def _refuse(page) -> None:
    page.keyboard.press("F5")
    page.wait_for_selector("[data-testid=run-refused]", timeout=10000)


def _blocked_count(page) -> int:
    return page.locator('.fn[data-status="blocked"]').count()


def test_marks_appear_only_after_run_refused(server) -> None:
    put_flow(server["base"], _two_end_graph())
    p, _path = _launch()
    try:
        br = _browser(p)
        page = _open(br, server["base"])
        assert page.locator("[data-testid=run-refused]").count() == 0
        assert _blocked_count(page) == 0
        _refuse(page)
        assert "Found 2." in page.locator("[data-testid=run-refused]").inner_text()
        assert _blocked_count(page) == 2
        assert page.locator('.fn[data-status="error"]').count() == 0
        words = page.locator('.fn[data-status="blocked"] .fn-st-word').all_inner_texts()
        assert words == ["Can't run", "Can't run"], words
        br.close()
    finally:
        p.stop()


def test_edit_reruns_check_and_passing_clears(server) -> None:
    put_flow(server["base"], _two_end_graph())
    p, _path = _launch()
    try:
        br = _browser(p)
        page = _open(br, server["base"])
        _refuse(page)
        page.evaluate(
            """() => {
              const n = window.__af.nodes.value.find(x => x.id === 'e2')
              n.position = { x: n.position.x + 40, y: n.position.y }
              window.__af.scheduleSave(false)
            }"""
        )
        page.wait_for_timeout(800)
        assert _blocked_count(page) == 2
        assert page.locator("[data-testid=run-refused]").count() == 1
        page.evaluate(
            """() => {
              window.__af.nodes.value = window.__af.nodes.value.filter(x => x.id !== 'e2')
              window.__af.edges.value = window.__af.edges.value.filter(
                e => e.source !== 'e2' && e.target !== 'e2'
              )
              window.__af.scheduleSave(true)
            }"""
        )
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=run-refused]')",
            timeout=10000,
        )
        assert _blocked_count(page) == 0
        br.close()
    finally:
        p.stop()


def test_deleting_marked_node_clears(server) -> None:
    put_flow(server["base"], _two_end_graph())
    p, _path = _launch()
    try:
        br = _browser(p)
        page = _open(br, server["base"])
        _refuse(page)
        page.evaluate(
            """() => {
              const node = window.__af.nodes.value.find(x => x.id === 'e2')
              window.__af.vf.addSelectedNodes([node])
            }"""
        )
        page.keyboard.press("Delete")
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=run-refused]')",
            timeout=10000,
        )
        assert _blocked_count(page) == 0
        br.close()
    finally:
        p.stop()


def test_starting_run_clears_marks(server) -> None:
    put_flow(server["base"], _two_end_graph())
    p, _path = _launch()
    try:
        br = _browser(p)
        page = _open(br, server["base"])
        _refuse(page)
        assert _blocked_count(page) == 2
        page.evaluate("() => window.__af.doRun('single', { node_id: 'start' })")
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=run-refused]')",
            timeout=10000,
        )
        assert _blocked_count(page) == 0
        br.close()
    finally:
        p.stop()


def test_importing_another_flow_clears_marks(server, tmp_path: Path) -> None:
    put_flow(server["base"], _two_end_graph())
    doc = {
        "format": "roboact-flow",
        "format_version": 1,
        "flow": {"id": "flow-again", "name": "again", "revision": 1, "step_limit": 20},
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "title": "Start",
                "params": {"data": {}},
                "position": {"x": 40, "y": 40},
            },
            {
                "id": "e1",
                "kind": "end",
                "title": "End",
                "params": {},
                "position": {"x": 280, "y": 40},
            },
            {
                "id": "e2",
                "kind": "end",
                "title": "End",
                "params": {},
                "position": {"x": 280, "y": 200},
            },
        ],
        "edges": [
            {"from": {"node": "start", "port": "data"}, "to": {"node": "e1", "port": "value"}},
            {"from": {"node": "start", "port": "data"}, "to": {"node": "e2", "port": "value"}},
        ],
    }
    path = tmp_path / "again.json"
    path.write_text(dump_json(doc), encoding="utf-8")
    p, _path = _launch()
    try:
        br = _browser(p)
        page = _open(br, server["base"])
        _refuse(page)
        page.locator('[data-testid="flow-export"]').click()
        page.wait_for_selector('[data-testid="flow-import"]', state="attached", timeout=10000)
        page.set_input_files('[data-testid="flow-import"]', str(path))
        page.wait_for_function(
            """() => {
              const name = window.__af.meta.value && window.__af.meta.value.name
              return name === 'again' && !document.querySelector('[data-testid=run-refused]')
            }""",
            timeout=15000,
        )
        assert _blocked_count(page) == 0
        br.close()
    finally:
        p.stop()


def test_reload_clears_marks(server) -> None:
    put_flow(server["base"], _two_end_graph())
    p, _path = _launch()
    try:
        br = _browser(p)
        page = _open(br, server["base"])
        _refuse(page)
        assert _blocked_count(page) == 2
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=30000)
        page.wait_for_function("() => window.__af.slot1Disabled.value === false", timeout=15000)
        assert page.locator("[data-testid=run-refused]").count() == 0
        assert _blocked_count(page) == 0
        br.close()
    finally:
        p.stop()
