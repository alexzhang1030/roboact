"""PRD 19.6 / 19.7 / 19.8 — locale, persist, overflow, codes-only storage, follow-browser reset."""
from __future__ import annotations

import pytest

from tests.conftest import chrome_path
from tests.http_api import http_json, motion_graph, put_flow
from tests.test_prd195_theme_contrast_fonts import _browser, _goto_ready, _wired_page
from tests.test_ui import _launch

pw = pytest.importorskip("playwright.sync_api")


def test_prd196_default_locale_from_navigator(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
        page, blocked, seen, errs = _wired_page(ctx, server["base"])
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


def test_prd196_zh_navigator_first_load_is_english(server):
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
        page, _, _, _ = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        assert page.evaluate("() => document.documentElement.dataset.lang") == "en"
        assert page.locator(".slot-1").inner_text().startswith("Run")
        br.close()
    finally:
        p.stop()


OVERFLOW_SEL = (
    ".slot-1, .slot-2, .slot-3, .run-word, .real-tag, .gstate, .gstate-word, "
    ".fn-st-word, .file-open, .top .right > button, [data-testid=theme-btn], [data-testid=lang-btn]"
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
        page.evaluate("() => { if (window.__af.selected) window.__af.selected.value = 'go'; if (window.__af.tab) window.__af.tab.value = 'rec' }")
        _set_node(page, "go", {
            "status": "running", "state": "running",
            "phase": "waiting_precondition", "wait_s": 3, "wait_max_s": 8,
        })
        return
    if name == "unknown":
        _set_run(page, "paused", "motion_timeout")
        _set_node(page, "go", {"status": "interrupted_unknown", "state": "interrupted_unknown"})
        return
    if name == "unknown-reading":
        _set_run(page, "paused", "motion_timeout")
        page.evaluate("() => { if (window.__af.selected) window.__af.selected.value = 'go'; if (window.__af.tab) window.__af.tab.value = 'rec' }")
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
        page.evaluate("() => { if (window.__af.selected) window.__af.selected.value = 'cam'; if (window.__af.tab) window.__af.tab.value = 'out' }")
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
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, locale="en-US", color_scheme="dark")
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
        boot2 = http_json(server["base"], "/api/bootstrap")
        assert boot2.get("step_limit_default") == limit
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
    http_json(server["base"], "/api/test/adapter", "POST", {"async_steps": 80, "async_sleep_s": 0.08})
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
        assert any("运行" in w or "中断" in w for w in page.locator(".fn-st-word").all_inner_texts())
        os.kill(server["proc"].pid, 9)
        server["proc"].wait(timeout=5)
        page.evaluate("() => window.__af.setLocaleMode('en')")
        page.wait_for_function("() => document.documentElement.dataset.lang === 'en'")
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "ROBOACT_TEST": "1", "PYTHONPATH": str(root)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "roboact", "--host", "127.0.0.1", "--port", str(server["port"]), "--db", server["db"]],
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
              return r && r.status === 'paused' && go && (go.state === 'interrupted_unknown' || go.status === 'interrupted_unknown')
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
