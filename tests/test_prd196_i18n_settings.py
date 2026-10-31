"""PRD 19.6 / 19.7 / 19.8 — locale, persist, overflow, codes-only storage, follow-browser reset."""

from __future__ import annotations

import pytest
from roboact import ADAPTER_HEARTBEAT_S, UI_LOST_TIMEOUT_S

from tests.http_api import http_json, motion_graph, put_flow
from tests.test_prd195_theme_contrast_fonts import _browser, _goto_ready, _wired_page
from tests.test_ui import _launch

pw = pytest.importorskip("playwright.sync_api")


def test_prd196_default_locale_from_navigator(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
        page, blocked, _seen, _errs = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        assert page.evaluate("() => localStorage.getItem('roboact.lang')") in (None, "system")
        assert page.evaluate("() => document.documentElement.dataset.lang") == "en"
        assert page.locator(".slot-1").inner_text().startswith("Run")
        page.locator('[data-testid="lang-btn"]').click()
        page.locator('[data-lang-mode="zh-CN"]').click()
        page.wait_for_function("() => document.documentElement.dataset.lang === 'zh-CN'")
        assert page.evaluate("() => localStorage.getItem('roboact.lang')") == "zh-CN"
        assert "运行" in page.locator(".slot-1").inner_text()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        assert page.evaluate("() => localStorage.getItem('roboact.lang')") == "zh-CN"
        assert page.evaluate("() => document.documentElement.dataset.lang") == "zh-CN"
        assert "运行" in page.locator(".slot-1").inner_text()
        assert blocked == []
        br.close()
    finally:
        p.stop()


def test_prd196_zh_navigator_first_load_is_chinese(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        assert page.evaluate("() => localStorage.getItem('roboact.lang')") in (None, "system")
        assert page.evaluate("() => document.documentElement.dataset.lang") == "zh-CN"
        assert "运行" in page.locator(".slot-1").inner_text()
        br.close()
    finally:
        p.stop()


def test_prd196_storage_event_syncs_second_tab(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
        page_a, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page_a, server["base"] + "/")
        page_b = ctx.new_page()
        page_b.goto(server["base"] + "/", wait_until="domcontentloaded")
        page_b.wait_for_function("window.__ready === true", timeout=20000)
        assert page_b.evaluate("() => document.documentElement.dataset.lang") == "en"
        page_a.evaluate("() => window.__af.setLocaleMode('zh-CN')")
        page_b.wait_for_function(
            "() => document.documentElement.dataset.lang === 'zh-CN'", timeout=5000
        )
        page_a.evaluate("() => window.__af.setThemeMode('dark')")
        page_b.wait_for_function(
            "() => document.documentElement.dataset.theme === 'dark'", timeout=5000
        )
        br.close()
    finally:
        p.stop()


OVERFLOW_SEL = (
    ".slot-1, .slot-2, .slot-3, .run-word, .real-tag, .gstate, .gstate-word, "
    ".fn-st-word, .file-open, .top .right > button, [data-testid=theme-btn], "
    "[data-testid=lang-btn], [data-testid=motion-btn]"
)

OVERFLOW_JS = """(sel) => {
  return [...document.querySelectorAll(sel)].map(el => ({
    t: (el.textContent || '').trim().slice(0, 48),
    cls: el.className,
    sw: el.scrollWidth, cw: el.clientWidth,
  })).filter(x => x.cw > 0 && x.sw > x.cw + 1)
}"""


def _apply_screen(page, name: str) -> None:
    if name == "idle":
        return
    if name == "gallery":
        return
    if name == "paused-breakpoint":
        page.evaluate("() => window.__af.setThemeMode && 0")
        _set_run(page, "paused", "breakpoint")
        return
    if name == "running":
        _set_run(page, "running")
        return
    if name == "waiting":
        _set_run(page, "running")
        page.evaluate(
            "() => { if (window.__af.selected) window.__af.selected.value = 'go'; if "
            "(window.__af.tab) window.__af.tab.value = 'rec' }"
        )
        _set_node(
            page,
            "go",
            {
                "status": "running",
                "state": "running",
                "phase": "waiting_precondition",
                "wait_s": 3,
                "wait_max_s": 8,
            },
        )
        return
    if name == "unknown":
        _set_run(page, "paused", "motion_timeout")
        _set_node(page, "go", {"status": "interrupted_unknown", "state": "interrupted_unknown"})
        return
    if name == "unknown-reading":
        _set_run(page, "paused", "motion_timeout")
        page.evaluate(
            "() => { if (window.__af.selected) window.__af.selected.value = 'go'; if "
            "(window.__af.tab) window.__af.tab.value = 'rec' }"
        )
        _set_node(page, "go", {"status": "interrupted_unknown", "state": "interrupted_unknown"})
        return
    if name == "cancel-rejected":
        _set_run(page, "running")
        _set_node(page, "go", {"status": "cancel_rejected", "state": "cancel_rejected"})
        return
    if name == "operator-lost":
        _set_run(page, "paused", "operator_lost")
        return
    if name == "adapter-lost":
        _set_run(page, "paused", "adapter_lost")
        return
    if name == "action-missing":
        _set_node(page, "go", {"status": "action_missing", "state": "action_missing"})
        return
    if name == "camera":
        page.evaluate(
            "() => { if (window.__af.selected) window.__af.selected.value = 'cam'; if "
            "(window.__af.tab) window.__af.tab.value = 'out' }"
        )
        return
    raise AssertionError("unknown screen " + name)


def _set_run(page, status, reason=None):
    page.evaluate(
        """({status, reason}) => {
          const cur = window.__af.run.value || { id: 'shot', run_id: 'shot' }
          const pause = reason ? { reason, node_id: 'go', when: 'before' } : null
          window.__af.run.value = { ...cur, status, pause, paused_node: pause && pause.node_id }
        }""",
        {"status": status, "reason": reason},
    )
    page.wait_for_timeout(40)


def _set_node(page, nid, payload):
    page.evaluate(
        """({nid, payload}) => {
          if (window.__af.nodeStatus) window.__af.nodeStatus[nid] = payload
        }""",
        {"nid": nid, "payload": payload},
    )
    page.wait_for_timeout(30)


EN_SCREENS = (
    "idle",
    "paused-breakpoint",
    "running",
    "waiting",
    "unknown",
    "unknown-reading",
    "cancel-rejected",
    "operator-lost",
    "adapter-lost",
    "action-missing",
    "camera",
    "gallery",
)


def test_prd196_english_no_clip_on_screens(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        page.evaluate("() => window.__af.setLocaleMode('en')")
        clipped = []
        for name in EN_SCREENS:
            if name == "gallery":
                page.goto(server["base"] + "/?gallery=1", wait_until="domcontentloaded")
                page.wait_for_function("window.__ready === true", timeout=20000)
                page.evaluate("() => window.__af.setLocaleMode('en')")
            else:
                _apply_screen(page, name)
            page.wait_for_timeout(40)
            overflow = page.evaluate(OVERFLOW_JS, OVERFLOW_SEL)
            if overflow:
                clipped.append({"screen": name, "overflow": overflow})
        assert clipped == [], clipped
        br.close()
    finally:
        p.stop()


def test_prd197_clear_storage_resets_prefs_not_timeouts(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(
            viewport={"width": 1280, "height": 800}, locale="en-US", color_scheme="dark"
        )
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        page.evaluate(
            """() => {
              window.__af.setThemeMode('light')
              window.__af.setLocaleMode('zh-CN')
            }"""
        )
        boot = http_json(server["base"], "/api/bootstrap")
        limit = boot.get("step_limit_default")
        page.evaluate("() => { localStorage.clear(); sessionStorage.clear() }")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        assert page.evaluate("() => localStorage.getItem('roboact.theme')") in (None, "system")
        assert page.evaluate("() => localStorage.getItem('roboact.lang')") in (None, "system")
        assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
        assert page.evaluate("() => document.documentElement.dataset.lang") == "en"
        stored = page.evaluate("() => Object.keys(localStorage)")
        assert not any("timeout" in k or "pause" in k for k in stored)
        boot2 = http_json(server["base"], "/api/bootstrap")
        assert boot2.get("step_limit_default") == limit
        assert ADAPTER_HEARTBEAT_S == 3.0
        assert UI_LOST_TIMEOUT_S == 10.0
        br.close()
    finally:
        p.stop()


def test_prd197_clear_storage_zh_resets_to_browser_lang(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(
            viewport={"width": 1280, "height": 800}, locale="zh-CN", color_scheme="light"
        )
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        page.evaluate(
            """() => {
              window.__af.setThemeMode('dark')
              window.__af.setLocaleMode('en')
            }"""
        )
        page.evaluate("() => { localStorage.clear(); sessionStorage.clear() }")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        assert page.evaluate("() => document.documentElement.dataset.theme") == "light"
        assert page.evaluate("() => document.documentElement.dataset.lang") == "zh-CN"
        assert "运行" in page.locator(".slot-1").inner_text()
        br.close()
    finally:
        p.stop()


def test_prd198_codes_survive_language_switch(server):
    """Chinese UI → interrupted_unknown → kill -9 backend → English → restart → retry/mark_done."""
    import os
    import subprocess
    import sys
    import time
    import urllib.request
    from pathlib import Path

    put_flow(server["base"], motion_graph("i18n-unknown"))
    http_json(
        server["base"], "/api/test/adapter", "POST", {"async_steps": 80, "async_sleep_s": 0.08}
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        page.evaluate("() => window.__af.setLocaleMode('zh-CN')")
        page.evaluate("() => window.__af.doRun('all')")
        page.wait_for_function(
            "() => window.__af.run.value && window.__af.run.value.status === 'running'",
            timeout=15000,
        )
        page.wait_for_function(
            """() => {
              const s = window.__af.nodeStatus.go
              return s && (s.state === 'running' || s.status === 'running')
            }""",
            timeout=15000,
        )
        assert any(
            "运行" in w or "中断" in w for w in page.locator(".fn-st-word").all_inner_texts()
        )
        os.kill(server["proc"].pid, 9)
        server["proc"].wait(timeout=5)
        page.evaluate("() => window.__af.setLocaleMode('en')")
        page.wait_for_function("() => document.documentElement.dataset.lang === 'en'")
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "ROBOACT_TEST": "1", "PYTHONPATH": str(root)}
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "roboact",
                "--host",
                "127.0.0.1",
                "--port",
                str(server["port"]),
                "--db",
                server["db"],
            ],
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        server["proc"] = proc
        t0 = time.time()
        while time.time() - t0 < 8:
            if proc.poll() is not None:
                out = proc.stdout.read().decode() if proc.stdout else ""
                raise RuntimeError("restarted server exited: " + out)
            try:
                urllib.request.urlopen(server["base"] + "/api/bootstrap", timeout=0.3)
                break
            except Exception:
                time.sleep(0.05)
        else:
            proc.kill()
            raise RuntimeError("restarted server did not start")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        assert page.evaluate("() => document.documentElement.dataset.lang") == "en"
        page.wait_for_function(
            """() => {
              const r = window.__af.run && window.__af.run.value
              const go = window.__af.nodeStatus && window.__af.nodeStatus.go
              const interrupted = go.state === 'interrupted_unknown'
                || go.status === 'interrupted_unknown'
              return r && r.status === 'paused' && go && interrupted
            }""",
            timeout=15000,
        )
        page.evaluate("() => { window.__af.selected.value = 'go'; window.__af.tab.value = 'rec' }")
        page.wait_for_timeout(80)
        words = page.locator(".fn-st-word").all_inner_texts()
        assert any("Interrupted" in w for w in words), words
        assert not any("中断" in w for w in words), words
        body = page.inner_text("body")
        assert "Redo this step" in body or "Mark done" in body, body[:800]
        br.close()
    finally:
        p.stop()


def test_prd196_french_navigator_is_english(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="fr-FR")
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        langs = page.evaluate("() => Array.from(navigator.languages || [])")
        assert langs and str(langs[0]).lower().startswith("fr"), langs
        assert page.evaluate("() => localStorage.getItem('roboact.lang')") in (None, "system")
        assert page.evaluate("() => document.documentElement.dataset.lang") == "en"
        assert page.locator(".slot-1").inner_text().startswith("Run")
        br.close()
    finally:
        p.stop()


def test_prd197_reduced_motion_first_load_and_follow_browser(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            color_scheme="dark",
            reduced_motion="reduce",
        )
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
        assert page.evaluate("() => document.documentElement.dataset.motion") == "reduce"
        page.locator('[data-testid="motion-btn"]').click()
        page.locator('[data-motion-mode="full"]').click()
        page.wait_for_function("() => document.documentElement.dataset.motion === 'full'")
        assert page.evaluate("() => localStorage.getItem('roboact.motion')") == "full"
        page.locator('[data-testid="motion-btn"]').click()
        page.locator('[data-motion-mode="system"]').click()
        page.wait_for_function("() => document.documentElement.dataset.motion === 'reduce'")
        assert page.evaluate("() => localStorage.getItem('roboact.motion')") == "system"
        br.close()
    finally:
        p.stop()


def test_prd197_bad_localstorage_falls_back_to_browser(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            color_scheme="dark",
            reduced_motion="reduce",
        )
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        page.evaluate(
            """() => {
              localStorage.setItem('roboact.theme', 'neon')
              localStorage.setItem('roboact.lang', 'de')
              localStorage.setItem('roboact.motion', 'maybe')
              localStorage.setItem('roboact.panels', '{bad')
              localStorage.setItem('roboact.viewport.x', '{bad')
              localStorage.setItem('roboact.logFilter', 'maybe')
            }"""
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
        assert page.evaluate("() => document.documentElement.dataset.lang") == "en"
        assert page.evaluate("() => document.documentElement.dataset.motion") == "reduce"
        assert page.evaluate("() => document.documentElement.dataset.themeMode") == "system"
        assert page.evaluate("() => document.documentElement.dataset.langMode") == "system"
        assert page.evaluate("() => document.documentElement.dataset.motionMode") == "system"
        width = page.evaluate("() => window.__af.rightW.value")
        assert width == 360
        br.close()
    finally:
        p.stop()


def test_prd197_panel_grid_persist_and_follow_theme(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(
            viewport={"width": 1280, "height": 800}, locale="en-US", color_scheme="dark"
        )
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        page.evaluate(
            """() => {
              window.__af.setThemeMode('light')
              window.__af.setLocaleMode('zh-CN')
              window.__af.leftOpen.value = false
              window.__af.rightW.value = 400
              window.__af.showMini.value = false
              window.__af.logFilter.value = 'error'
            }"""
        )
        page.wait_for_timeout(80)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        assert page.evaluate("() => document.documentElement.dataset.theme") == "light"
        assert page.evaluate("() => document.documentElement.dataset.lang") == "zh-CN"
        panels = page.evaluate("() => JSON.parse(localStorage.getItem('roboact.panels') || '{}')")
        assert panels.get("leftOpen") is False
        assert panels.get("rightW") == 400
        assert page.evaluate("() => localStorage.getItem('roboact.grid')") == "0"
        assert page.evaluate("() => localStorage.getItem('roboact.logFilter')") == "error"
        assert page.evaluate("() => window.__af.leftOpen.value") is False
        assert page.evaluate("() => window.__af.rightW.value") == 400
        assert page.evaluate("() => window.__af.showMini.value") is False
        assert page.evaluate("() => window.__af.logFilter.value") == "error"
        page.locator('[data-testid="theme-btn"]').click()
        page.locator('[data-theme-mode="system"]').click()
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        page.locator('[data-testid="lang-btn"]').click()
        page.locator('[data-lang-mode="system"]').click()
        page.wait_for_function("() => document.documentElement.dataset.lang === 'en'")
        br.close()
    finally:
        p.stop()


def test_prd197_viewport_persist_per_flow(server):
    boot = http_json(server["base"], "/api/bootstrap")
    fid = boot["flow"]["id"]
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        page.evaluate(
            """(id) => {
              localStorage.setItem(
                'roboact.viewport.' + id,
                JSON.stringify({x: 120, y: -40, zoom: 1.35})
              )
            }""",
            fid,
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        page.wait_for_timeout(200)
        vp = page.evaluate(
            """() => {
              const v = window.__af.vf.viewport.value
              return { x: v.x, y: v.y, zoom: v.zoom }
            }"""
        )
        assert abs(vp["x"] - 120) < 4, vp
        assert abs(vp["y"] + 40) < 4, vp
        assert abs(vp["zoom"] - 1.35) < 0.05, vp
        br.close()
    finally:
        p.stop()
