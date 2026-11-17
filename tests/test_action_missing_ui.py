"""Action-missing title row, and the import-failure dialog copy."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from roboact.flow_file import dump_json

from tests.test_flow_file import _gone_missing_doc, _open_ui, _ui_import

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
EN_LINE = "nodes[2] (id out): output is now end."
CODE_VALUES = ["nodes[2]", "out", "output", "end"]


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
          const typeIco = card && card.querySelector('.fn-type-ico')
          const summary = card && card.querySelector('[data-testid="node-summary"]')
          const probe = document.createElement('span')
          probe.style.color = 'var(--red)'
          document.body.appendChild(probe)
          const red = getComputedStyle(probe).color
          probe.remove()
          return {
            word: word ? word.textContent : '',
            icon: ico ? (ico.getAttribute('style') || '') : '',
            typeIcon: typeIco ? (typeIco.getAttribute('style') || '') : '',
            wordColor: word ? getComputedStyle(word).color : '',
            red,
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
        assert en["word"] == "Action gone", en
        assert "state-unknown" in en["icon"], en
        assert "node-tool" in en["typeIcon"], en
        assert en["wordColor"] == en["red"], en
        assert en["crosses"] == 0, en
        assert en["summary"] == "This action is no longer provided by the adapter.", en

        page.evaluate("() => window.__af.setLocaleMode('zh-CN')")
        zh_cat = _catalog("zh-CN.js")
        zh = _title_row(page)
        assert zh["word"] == zh_cat["status.action_missing"], zh
        assert "state-unknown" in zh["icon"], zh
        assert "node-tool" in zh["typeIcon"], zh
        assert zh["wordColor"] == zh["red"], zh
        assert zh["crosses"] == 0, zh
        assert zh["summary"] == zh_cat["error.action_missing"], zh
    finally:
        ctx.close()
        browser.close()
        playwright.stop()


def _line_dom(page) -> dict:
    return page.evaluate(
        """() => {
          const li = document.querySelector('[data-testid=import-errors] li')
          const codes = [...li.querySelectorAll('code')].map(el => el.textContent)
          const codeFont = getComputedStyle(li.querySelector('code')).fontFamily
          const liStyle = getComputedStyle(li)
          return {
            text: li.textContent,
            codes,
            backticks: li.textContent.includes('`'),
            liFont: liStyle.fontFamily,
            liSize: liStyle.fontSize,
            codeFont,
          }
        }"""
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
        en_dom = _line_dom(page)
        assert en_dom["text"] == EN_LINE, en_dom
        assert en_dom["codes"] == CODE_VALUES, en_dom
        assert en_dom["backticks"] is False, en_dom
        assert en_dom["liSize"] == "13px", en_dom
        assert "Inter" in en_dom["liFont"], en_dom
        assert "monospace" in en_dom["codeFont"].lower() or "JetBrains Mono" in en_dom["codeFont"]

        zh = _catalog("zh-CN.js")
        zh_path = _fill(zh["flow.import_path_id"], path="nodes[2]", id="out")
        zh_line = _fill(zh["flow.output_renamed"], path=zh_path, old="output", new="end")
        page.evaluate("() => window.__af.setLocaleMode('zh-CN')")
        assert page.locator("[data-testid=import-close]").inner_text() == zh["flow.import_close"]
        zh_dom = _line_dom(page)
        assert zh_dom["text"] == zh_line, zh_dom
        assert zh_dom["codes"] == CODE_VALUES, zh_dom
        assert zh_dom["backticks"] is False, zh_dom
        assert zh_dom["liSize"] == "13px", zh_dom
        assert "Inter" in zh_dom["liFont"], zh_dom
        assert "monospace" in zh_dom["codeFont"].lower() or "JetBrains Mono" in zh_dom["codeFont"]
    finally:
        ctx.close()
        browser.close()
        playwright.stop()
