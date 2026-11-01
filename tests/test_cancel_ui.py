"""Cancel UI: lost-line, Can't cancel, and the non-cancellable timeout block."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from roboact.adapter import CancelSpec, FakeAdapter
from roboact.nodes import bind_adapter

from tests.helpers import adapter_ctx, recs, run_until, wait_status
from tests.http_api import http_json, motion_graph, put_flow
from tests.test_a23_a37 import pose_flow

ROOT = Path(__file__).resolve().parents[1]
pw = pytest.importorskip("playwright.sync_api")


def _catalog(name: str) -> dict[str, str]:
    text = (ROOT / "web" / "i18n" / name).read_text(encoding="utf-8")
    blob = text[text.find("{") : text.rfind("}") + 1]
    return ast.literal_eval(blob)


def test_cancel_ui_catalog_strings() -> None:
    en = _catalog("en.js")
    zh = _catalog("zh-CN.js")
    assert en["node.no_cancel"] == "Can't cancel"
    assert en["lost.cancelled"] == ("Page disconnected. {node} was cancelled, then the run paused.")
    assert en["lost.ran_to_end"] == (
        "Page disconnected. {node} can't be cancelled; it ran {s} s more, then the run paused."
    )
    assert en["lost.cancel_rejected"] == (
        "Page disconnected. The robot refused to cancel {node}; it ran to end, then the run paused."
    )
    assert en["run.no_cancel_too_long"] == (
        "{node} can't be cancelled and may run {s} s. The limit is 10 s."
    )
    assert en["run.no_cancel_too_long_count"] == ("{n} nodes can't be cancelled and run too long")
    for key in (
        "node.no_cancel",
        "lost.cancelled",
        "lost.ran_to_end",
        "lost.cancel_rejected",
        "run.no_cancel_too_long",
        "run.no_cancel_too_long_count",
    ):
        assert zh[key]
        assert zh[key] != en[key]


def _pose_uncancellable(timeout_s: float, sleep_s: float) -> FakeAdapter:
    adapter = FakeAdapter()
    adapter.async_steps = 1
    adapter.async_sleep_s = sleep_s
    adapter.action_updates["fake_set_pose"] = {
        "timeout_s": timeout_s,
        "cancel": CancelSpec(cancellable=False, post_cancel="hold"),
    }
    bind_adapter(adapter)
    return adapter


def test_lost_outcome_cancelled(tmp_path) -> None:
    async def go() -> None:
        adapter = FakeAdapter()
        adapter.async_steps = 1
        adapter.async_sleep_s = 2.0
        bind_adapter(adapter)
        ex, store, _adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.ui_lost_s = 0.2
        rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
        ex.note_ui(rid)
        st = await wait_status(ex, rid, "paused", timeout=4)
        pause = st.pause
        assert pause is not None
        assert pause["reason"] == "operator_lost"
        assert pause["lost_outcome"] == "cancelled"
        assert "lost_extra_s" not in pause
        assert (st.node_state.get("go") or {}).get("state") == "stopped"
        assert recs(store, rid)[-1]["status"] == "stopped"
        await hub.stop()

    asyncio.run(go())


def test_lost_outcome_ran_to_end(tmp_path) -> None:
    async def go() -> None:
        adapter = _pose_uncancellable(5, 0.55)
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.ui_lost_s = 0.15
        rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
        ex.note_ui(rid)
        st = await wait_status(ex, rid, "paused", timeout=4)
        assert st.pause["lost_outcome"] == "ran_to_end"
        extra = st.pause["lost_extra_s"]
        assert isinstance(extra, int | float)
        assert extra >= 0
        assert (st.node_state.get("go") or {}).get("state") == "done"
        assert adapter.cancel_calls == []
        await hub.stop()

    asyncio.run(go())


def test_lost_outcome_cancel_rejected(tmp_path) -> None:
    async def go() -> None:
        adapter = FakeAdapter()
        adapter.async_steps = 1
        adapter.async_sleep_s = 0.7
        adapter.reject_next_cancel = True
        bind_adapter(adapter)
        ex, store, adapter, hub = adapter_ctx(tmp_path, adapter)
        ex.ui_lost_s = 0.15
        rid, _ = await run_until(ex, store, pose_flow(), "running", timeout=2)
        ex.note_ui(rid)
        st = await wait_status(ex, rid, "paused", timeout=4)
        pause = st.pause
        assert pause is not None
        assert pause["lost_outcome"] == "cancel_rejected"
        assert "lost_extra_s" not in pause
        go_node = st.node_state.get("go") or {}
        assert go_node.get("state") == "done", go_node
        assert go_node.get("duration_ms") is not None
        assert adapter.cancel_calls
        go_recs = [r for r in recs(store, rid) if r["node_id"] == "go"]
        assert go_recs[-1]["status"] == "done"
        await hub.stop()

    asyncio.run(go())


def _browser(p):
    from tests.conftest import launch_chromium

    return launch_chromium(p)


def _launch():
    from tests.test_ui import _launch as launch

    return launch()


def _open(br, base):
    from tests.test_a4_a6_browser import _open as open_page

    return open_page(br, base)


def _english(page) -> None:
    page.evaluate("() => window.__af.setLocaleMode('en')")


def test_pre_run_no_cancel_too_long_blocks_and_marks(server) -> None:
    graph = motion_graph("cancel ui cap")
    graph["nodes"][1]["title"] = "Go to pose"
    put_flow(server["base"], graph)
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {
            "action_updates": {
                "fake_set_pose": {
                    "timeout_s": 11,
                    "cancel": {"cancellable": False, "post_cancel": "hold"},
                }
            }
        },
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _english(page)
        page.wait_for_function(
            """() => {
              const c = window.__af.runCheck.value
              return c && (c.timeout_errors || []).length
            }""",
            timeout=15000,
        )
        assert page.evaluate("() => window.__af.slot1Disabled.value") is False
        assert page.locator("[data-testid=motion-timeout-cap]").count() == 0
        assert page.locator('.fn[data-status="blocked"]').count() == 0
        page.locator("button.slot-1").click()
        page.wait_for_selector("[data-testid=motion-timeout-cap]", timeout=10000)
        assert page.evaluate("() => window.__af.slot1Disabled.value") is True
        count = page.locator("[data-testid=motion-timeout-cap]").inner_text()
        assert count == "1 node can't be cancelled and runs too long", count
        summary = page.locator(
            '.fn[data-status="blocked"] [data-testid="node-summary"]'
        ).inner_text()
        assert summary == "Can't cancel. Runs up to 11 s.", summary
        assert page.locator('.fn[data-status="error"]').count() == 0
        page.locator("[data-testid=motion-timeout-cap]").click()
        page.wait_for_function("() => window.__af.selected.value === 'go'", timeout=5000)
        panel = page.locator("[data-testid=panel-no-cancel]").inner_text()
        assert "Go to pose" in panel and "11" in panel and "10" in panel, panel
        br.close()
    finally:
        p.stop()


def test_no_cancel_label_on_node_and_palette_truncates_title(server) -> None:
    long_title = "Move the arm along the full safe corridor before the grasp"
    graph = motion_graph("cancel ui label")
    graph["nodes"][1]["title"] = long_title
    put_flow(server["base"], graph)
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {
            "action_updates": {
                "fake_set_pose": {
                    "timeout_s": 5,
                    "cancel": {"cancellable": False, "post_cancel": "hold"},
                }
            }
        },
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _english(page)
        page.evaluate("() => window.__af.setThemeMode('light')")
        page.wait_for_selector("[data-testid=node-no-cancel]", timeout=15000)
        page.wait_for_selector("[data-testid=palette-no-cancel]", timeout=5000)
        badge = page.locator(".fn [data-testid=node-no-cancel]").inner_text()
        pal = page.locator("[data-testid=palette-no-cancel]").first.inner_text()
        assert badge == "Can't cancel", badge
        assert pal == "Can't cancel", pal
        laid = page.evaluate(
            """() => {
              const card = document.querySelector('.fn[data-node-type="fake_set_pose"]')
              const name = card.querySelector('.fn-name')
              const word = card.querySelector('.fn-no-cancel')
              const row = document.querySelector('.nt:has(.nt-no-cancel)')
              const rowName = row.querySelector('.nt-name')
              const rowWord = row.querySelector('.nt-no-cancel')
              const cs = getComputedStyle(word)
              const ns = getComputedStyle(name)
              return {
                nameOverflow: name.scrollWidth > name.clientWidth + 1,
                wordFull: word.scrollWidth <= word.clientWidth + 1,
                wordAfterName: word.getBoundingClientRect().left
                  >= name.getBoundingClientRect().left,
                rowWordAfter: rowWord.getBoundingClientRect().left
                  >= rowName.getBoundingClientRect().left,
                wordSize: cs.fontSize,
                wordColor: cs.color,
                nameEllipsis: ns.textOverflow,
                rowEllipsis: getComputedStyle(rowName).textOverflow,
                rowWordFlex: getComputedStyle(rowWord).flexGrow,
              }
            }"""
        )
        assert laid["nameOverflow"] is True, laid
        assert laid["wordFull"] is True, laid
        assert laid["wordAfterName"] is True, laid
        assert laid["rowWordAfter"] is True, laid
        assert laid["wordSize"] == "12px", laid
        assert laid["wordColor"] == "rgb(92, 92, 87)", laid
        assert laid["nameEllipsis"] == "ellipsis", laid
        assert laid["rowEllipsis"] == "ellipsis", laid
        assert laid["rowWordFlex"] == "0", laid
        br.close()
    finally:
        p.stop()


def _sync_lost(page, rid: str) -> dict:
    """Read the paused run back into the page. A half-open cut does not deliver SSE."""
    return page.evaluate(
        """async (rid) => {
          const r = await (await fetch('/api/runs/' + rid)).json()
          const snap = r.run || r
          const run = window.__af.run.value
          window.__af.run.value = { ...run, ...snap, status: snap.status, pause: snap.pause }
          const nodes = r.nodes || snap.nodes || {}
          const status = window.__af.nodeStatus
          for (const id of Object.keys(nodes)) {
            status[id] = { ...(status[id] || {}), ...nodes[id] }
          }
          window.__af.selected.value = (snap.pause && snap.pause.node_id) || 'go'
          return snap.pause || {}
        }""",
        rid,
    )


def _lost_line(page) -> str:
    loc = page.locator("[data-testid=lost-line]")
    assert loc.count() == 1
    return loc.inner_text()


def test_lost_line_cancelled(server) -> None:
    from tests.tcp_proxy import TcpProxy
    from tests.test_a4_a6_browser import _cut_to_operator_lost, _run_id

    put_flow(server["base"], motion_graph("lost cancelled"))
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {"async_steps": 1, "async_sleep_s": 30},
    )
    proxy = TcpProxy("127.0.0.1", server["port"])
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, proxy.base)
        _english(page)
        _dt, snap = _cut_to_operator_lost(page, proxy, server["base"], wait_s=15)
        pause = (snap.get("run") or snap).get("pause") or {}
        assert pause.get("lost_outcome") == "cancelled", pause
        proxy.restore()
        page.wait_for_function(
            """() => document.body.innerText.includes('Connected')""",
            timeout=15000,
        )
        rid = _run_id(page)
        shown = _sync_lost(page, rid)
        assert shown.get("lost_outcome") == "cancelled"
        text = _lost_line(page)
        assert text == ("Page disconnected. Go to pose was cancelled, then the run paused."), text
        state = page.locator('.fn[data-node-type="fake_set_pose"]').get_attribute("data-state")
        assert state == "stopped", state
        page.evaluate("() => window.__af.cmd('stop')")
        page.wait_for_timeout(400)
        _sync_lost(page, rid)
        assert page.locator("[data-testid=lost-line]").count() == 0
        br.close()
    finally:
        p.stop()
        proxy.close()


def test_lost_line_ran_to_end(server) -> None:
    from tests.tcp_proxy import TcpProxy
    from tests.test_a4_a6_browser import _cut_to_operator_lost, _run_id

    put_flow(server["base"], motion_graph("lost ran to end"))
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {
            "async_steps": 1,
            "async_sleep_s": 8,
            "action_updates": {
                "fake_set_pose": {
                    "timeout_s": 10,
                    "cancel": {"cancellable": False, "post_cancel": "hold"},
                }
            },
        },
    )
    # timeout_s must stay ≤ 10 or Run is blocked, so the action cannot outlast
    # the production 10 s disconnect timer. This test-only timer is shorter
    # than the action and longer than the page beat.
    http_json(server["base"], "/api/test/ui-lost", "POST", {"seconds": 3.5})
    boot = http_json(server["base"], "/api/bootstrap")
    spec = next(t for t in boot["node_types"] if t["type"] == "fake_set_pose")
    assert spec["action"]["cancel"]["cancellable"] is False, spec["action"]["cancel"]
    proxy = TcpProxy("127.0.0.1", server["port"])
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, proxy.base)
        _english(page)
        _dt, snap = _cut_to_operator_lost(page, proxy, server["base"], wait_s=12)
        pause = (snap.get("run") or snap).get("pause") or {}
        assert pause.get("lost_outcome") == "ran_to_end", pause
        extra = pause.get("lost_extra_s")
        assert isinstance(extra, int | float) and extra >= 1, pause
        proxy.restore()
        page.wait_for_function(
            """() => document.body.innerText.includes('Connected')""",
            timeout=15000,
        )
        rid = _run_id(page)
        _sync_lost(page, rid)
        text = _lost_line(page)
        assert text == (
            f"Page disconnected. Go to pose can't be cancelled; it ran {extra} s more, "
            "then the run paused."
        ), text
        state = page.locator('.fn[data-node-type="fake_set_pose"]').get_attribute("data-state")
        assert state == "done", state
        br.close()
    finally:
        p.stop()
        proxy.close()


def test_lost_line_cancel_rejected(server) -> None:
    from tests.tcp_proxy import TcpProxy
    from tests.test_a4_a6_browser import _cut_to_operator_lost, _run_id

    put_flow(server["base"], motion_graph("lost refused"))
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {"async_steps": 1, "async_sleep_s": 16, "reject_next_cancel": True},
    )
    proxy = TcpProxy("127.0.0.1", server["port"])
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, proxy.base)
        _english(page)
        _dt, snap = _cut_to_operator_lost(page, proxy, server["base"], wait_s=25)
        pause = (snap.get("run") or snap).get("pause") or {}
        assert pause.get("lost_outcome") == "cancel_rejected", pause
        proxy.restore()
        page.wait_for_function(
            """() => document.body.innerText.includes('Connected')""",
            timeout=15000,
        )
        rid = _run_id(page)
        _sync_lost(page, rid)
        text = _lost_line(page)
        assert text == (
            "Page disconnected. The robot refused to cancel Go to pose; "
            "it ran to end, then the run paused."
        ), text
        card = page.locator('.fn[data-node-type="fake_set_pose"]')
        page.wait_for_function(
            """() => {
              const card = document.querySelector('.fn[data-node-type="fake_set_pose"]')
              const word = card && card.querySelector('.fn-st-word')
              const took = card && card.querySelector('[data-testid=node-took]')
              return card && card.getAttribute('data-state') === 'done'
                && word && word.textContent === 'Done'
                && took && took.textContent.indexOf('Took ') === 0
            }""",
            timeout=5000,
        )
        assert card.get_attribute("data-status") == "done"
        assert page.locator(".cancel-note").inner_text() == "Cancel rejected"
        br.close()
    finally:
        p.stop()
        proxy.close()


def test_cancel_rejected_amber_then_done(server) -> None:
    """Refused cancel is amber while running, then Done plus Took once it ends."""
    put_flow(server["base"], motion_graph("reject then done"))
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {"async_steps": 1, "async_sleep_s": 2.5, "reject_next_cancel": True},
    )
    http_json(server["base"], "/api/test/ui-lost", "POST", {"seconds": 0.4})
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _english(page)
        page.evaluate("() => window.__af.setThemeMode('light')")
        page.evaluate("() => window.__af.doRun('all')")
        page.wait_for_function(
            "() => window.__af.run.value && window.__af.run.value.status === 'running'",
            timeout=20000,
        )
        rid = page.evaluate(
            "() => { const r = window.__af.run.value; return r && (r.run_id || r.id) }"
        )
        from tests.test_a4_a6_browser import _wait_node_running

        _wait_node_running(server["base"], rid, "go")
        page.evaluate("() => window.__af.stopBeat()")
        page.wait_for_function(
            """() => {
              const card = document.querySelector('.fn[data-node-type="fake_set_pose"]')
              return card && card.getAttribute('data-state') === 'cancel_rejected'
            }""",
            timeout=10000,
        )
        amber = page.evaluate(
            """() => {
              const card = document.querySelector('.fn[data-node-type="fake_set_pose"]')
              const word = card.querySelector('.fn-st')
              const ico = card.querySelector('.ico')
              return {
                status: card.getAttribute('data-status'),
                border: getComputedStyle(card).borderTopColor,
                word: getComputedStyle(word).color,
                icon: getComputedStyle(ico).color,
              }
            }"""
        )
        assert amber["status"] == "cancel_rejected", amber
        assert amber["border"] == "rgb(143, 96, 0)", amber
        assert amber["word"] == "rgb(143, 96, 0)", amber
        assert amber["icon"] == "rgb(143, 96, 0)", amber
        page.evaluate("() => { window.__af.selected.value = 'go' }")
        page.wait_for_function(
            """() => {
              const card = document.querySelector('.fn[data-node-type="fake_set_pose"]')
              const word = card && card.querySelector('.fn-st-word')
              const took = card && card.querySelector('[data-testid=node-took]')
              return card && card.getAttribute('data-state') === 'done'
                && card.getAttribute('data-status') === 'done'
                && word && word.textContent === 'Done'
                && took && took.textContent.indexOf('Took ') === 0
            }""",
            timeout=10000,
        )
        assert page.locator(".cancel-note").inner_text() == "Cancel rejected"
        br.close()
    finally:
        p.stop()


def test_done_motion_title_has_no_duration(server) -> None:
    """Done motion: title is Done, duration is the mono summary, name stays whole."""
    put_flow(server["base"], motion_graph("done title"))
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {"async_steps": 1, "async_sleep_s": 0.4},
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _english(page)
        page.evaluate(
            """() => {
              const vf = window.__af.vf
              if (vf && vf.setViewport) vf.setViewport({ x: 40, y: 80, zoom: 1 })
            }"""
        )
        page.evaluate("() => window.__af.doRun('all')")
        page.wait_for_function(
            """() => {
              const r = window.__af.run.value
              return r && r.status === 'completed'
            }""",
            timeout=20000,
        )
        card = page.locator('.fn[data-node-type="fake_set_pose"]')
        word = card.locator(".fn-st-word").inner_text()
        assert word == "Done", word
        took = card.locator("[data-testid=node-took]").inner_text()
        assert took.startswith("Took "), took
        assert "Done" not in took, took
        laid = page.evaluate(
            """() => {
              const card = document.querySelector('.fn[data-node-type="fake_set_pose"]')
              const name = card.querySelector('.fn-name')
              const tookEl = card.querySelector('[data-testid=node-took]')
              const mark = card.querySelector('.fn-side')
              const cs = getComputedStyle(tookEl)
              return {
                name: name.textContent,
                nameOverflow: name.scrollWidth > name.clientWidth + 1,
                width: Math.round(card.getBoundingClientRect().width),
                tookFont: cs.fontFamily,
                hasMark: !!mark,
              }
            }"""
        )
        assert laid["name"] == "Go to pose", laid
        assert laid["nameOverflow"] is False, laid
        assert laid["hasMark"] is True, laid
        assert laid["width"] == 240, laid
        assert "JetBrains Mono" in laid["tookFont"], laid
        br.close()
    finally:
        p.stop()
