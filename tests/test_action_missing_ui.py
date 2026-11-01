"""Action-missing title row, and the import-failure dialog copy."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from roboact.flow_file import dump_json

from tests.test_flow_file import _gone_missing_doc, _open_ui, _ui_import

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
EN_LINE = "nodes[2] (id out): `output` is now `end`."
EN_PATH = "nodes[2] (id out)"


def _catalog(name: str) -> dict[str, str]:
    text = (ROOT / "web" / "i18n" / name).read_text(encoding="utf-8")
    blob = text[text.find("{") : text.rfind("}") + 1]
    return ast.literal_eval(blob)


def _fill(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def _output_file(path: Path) -> None:
    doc = {
        "format": "roboact-flow",
        "format_version": 1,
        "flow": {"id": "flow-old", "name": "old output", "revision": 1, "step_limit": 20},
        "nodes": [
            {
                "id": "n_start",
                "kind": "start",
                "title": "Start",
                "params": {"data": {}},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "n_mid",
                "kind": "constant",
                "title": "C",
                "params": {"value": "a", "value_type": "string"},
                "position": {"x": 40, "y": 0},
            },
            {
                "id": "out",
                "kind": "output",
                "title": "Out",
                "params": {},
                "position": {"x": 80, "y": 0},
            },
        ],
        "edges": [],
    }
    path.write_text(dump_json(doc), encoding="utf-8")


def _title_row(page) -> dict:
    return page.evaluate(
        """() => {
          const card = document.querySelector('.fn[data-status="missing"]')
          const word = card && card.querySelector('.fn-st-word')
          const ico = card && card.querySelector('.fn-st .ico')
          const summary = card && card.querySelector('[data-testid="node-summary"]')
          return {
            word: word ? word.textContent : '',
            icon: ico ? (ico.getAttribute('style') || '') : '',
            crosses: document.querySelectorAll('.fn-gone-x').length,
            summary: summary ? summary.textContent : '',
          }
        }"""
    )


def test_action_missing_title_row_icon_and_word(server, tmp_path: Path) -> None:
    src = tmp_path / "gone.json"
    src.write_text(dump_json(_gone_missing_doc()), encoding="utf-8")
    playwright, browser, ctx, page = _open_ui(server["base"])
    try:
        _ui_import(page, src)
        page.wait_for_function(
            """() => {
              const c = window.__af && window.__af.runCheck && window.__af.runCheck.value
              return !!(c && (c.action_gone || []).includes('ghost'))
            }""",
            timeout=15000,
        )
        page.evaluate("() => window.__af.setLocaleMode('en')")
        en = _title_row(page)
        assert en["word"] == "Action no longer exists", en
        assert "state-unknown" in en["icon"], en
        assert en["crosses"] == 0, en
        assert en["summary"] == "This action is no longer provided by the adapter.", en

        page.evaluate("() => window.__af.setLocaleMode('zh-CN')")
        zh_cat = _catalog("zh-CN.js")
        zh = _title_row(page)
        assert zh["word"] == zh_cat["status.action_missing"], zh
        assert "state-unknown" in zh["icon"], zh
        assert zh["crosses"] == 0, zh
        assert zh["summary"] == zh_cat["error.action_missing"], zh
    finally:
        ctx.close()
        browser.close()
        playwright.stop()


def _mono(page) -> str:
    return page.locator("[data-testid=import-path-id]").evaluate(
        "el => getComputedStyle(el).fontFamily"
    )


def test_import_failure_dialog_close_and_path(server, tmp_path: Path) -> None:
    src = tmp_path / "old.json"
    _output_file(src)
    playwright, browser, ctx, page = _open_ui(server["base"])
    try:
        _ui_import(page, src)
        page.wait_for_selector("[data-testid=import-errors]", timeout=10000)
        page.evaluate("() => window.__af.setLocaleMode('en')")
        assert page.locator("[data-testid=import-close]").inner_text() == "Close"
        assert page.locator("[data-testid=import-errors] li").inner_text() == EN_LINE
        assert page.locator("[data-testid=import-path-id]").inner_text() == EN_PATH
        assert "monospace" in _mono(page).lower() or "JetBrains Mono" in _mono(page)

        zh = _catalog("zh-CN.js")
        zh_path = _fill(zh["flow.import_path_id"], path="nodes[2]", id="out")
        zh_line = _fill(zh["flow.output_renamed"], path=zh_path)
        page.evaluate("() => window.__af.setLocaleMode('zh-CN')")
        assert page.locator("[data-testid=import-close]").inner_text() == zh["flow.import_close"]
        assert page.locator("[data-testid=import-errors] li").inner_text() == zh_line
        path = page.locator("[data-testid=import-path-id]").inner_text()
        assert path == zh_path, path
        assert "nodes[2]" in path and "id" in path and path.endswith("out）"), path
        font = _mono(page)
        assert "monospace" in font.lower() or "JetBrains Mono" in font, font
    finally:
        ctx.close()
        browser.close()
        playwright.stop()
