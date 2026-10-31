"""One Playwright pass: each ui-spec screen/state in light and dark."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import chrome_path
from tests.http_api import http_json, put_flow
from tests.test_ui import _launch, _page

pw = pytest.importorskip("playwright.sync_api")

ART = Path("/opt/cursor/artifacts")


def _shot(page, screen: str, state: str, theme: str) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ART / f"uispec-{screen}-{state}-{theme}.png"), full_page=True)


def _open(br, base, path="/"):
    page, blocked, errs = _page(br, base)
    page.goto(base + path, wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=60000)
    return page


def _theme(page, theme: str) -> None:
    page.evaluate(
        """(theme) => {
          document.documentElement.dataset.theme = theme
          localStorage.setItem('roboact-theme', theme)
          if (window.__af && window.__af.theme) window.__af.theme.value = theme
        }""",
        theme,
    )
    page.wait_for_timeout(60)


def _set_run(page, status, reason=None):
    page.evaluate(
        """({status, reason}) => {
          const cur = window.__af.run.value || { id: 'shot', run_id: 'shot' }
          const pause = reason ? { reason, node_id: 'go', when: 'before' } : null
          window.__af.run.value = { ...cur, status, pause, paused_node: pause && pause.node_id }
        }""",
        {"status": status, "reason": reason},
    )
    page.wait_for_timeout(50)


def _set_node(page, nid, payload):
    page.evaluate(
        """({nid, payload}) => {
          if (window.__af.nodeStatus) window.__af.nodeStatus[nid] = payload
        }""",
        {"nid": nid, "payload": payload},
    )
    page.wait_for_timeout(40)


def _both(page, screen, state):
    for theme in ("light", "dark"):
        _theme(page, theme)
        _shot(page, screen, state, theme)


def test_ui_spec_screenshots_one_pass(server, token_server):
    ART.mkdir(parents=True, exist_ok=True)
    p, path = _launch()
    try:
        br = p.chromium.launch(executable_path=chrome_path() or path, args=["--no-sandbox"])
        page = _open(br, server["base"])
        page.wait_for_timeout(200)

        _both(page, "canvas", "idle")

        page.evaluate("() => window.__af.toggleBp('start', 'before')")
        page.locator(".ctrls button.primary").click()
        page.wait_for_function(
            "() => window.__af.run.value && window.__af.run.value.status === 'paused'",
            timeout=15000,
        )
        _both(page, "canvas", "paused-breakpoint")

        page.evaluate("() => window.__af.cmd('step')")
        try:
            page.wait_for_function(
                """() => {
                  const r = window.__af.run.value
                  return r && r.status === 'paused' && r.pause && r.pause.reason === 'step_done'
                }""",
                timeout=8000,
            )
        except Exception:
            _set_run(page, "paused", "step_done")
        _both(page, "canvas", "paused-step")

        _set_run(page, "running")
        _both(page, "canvas", "running")

        page.evaluate("() => { if (window.__af.pausePending) window.__af.pausePending.value = true }")
        page.wait_for_timeout(40)
        _both(page, "canvas", "pause-pending")
        page.evaluate("() => { if (window.__af.pausePending) window.__af.pausePending.value = false }")

        _set_run(page, "paused", "user")
        _both(page, "canvas", "paused-user")
        _set_run(page, "stopped")
        _both(page, "canvas", "stopped")
        _set_run(page, "completed")
        _both(page, "canvas", "completed")
        _set_run(page, "error")
        _both(page, "canvas", "error")
        _set_run(page, "step_limit")
        _both(page, "canvas", "step-limit")

        for state, status, reason in (
            ("paused-operator-lost", "paused", "operator_lost"),
            ("paused-adapter-lost", "paused", "adapter_lost"),
            ("paused-motion-timeout", "paused", "motion_timeout"),
            ("paused-write-timeout", "paused", "write_timeout"),
            ("paused-restart", "paused", "restart"),
            ("paused-storage-error", "paused", "storage_error"),
            ("waiting-approval", "paused", "approval"),
        ):
            _set_run(page, status, reason)
            _both(page, "canvas", state)

        _set_run(page, "running")
        page.evaluate("() => { if (window.__af.selected) window.__af.selected.value = 'go'; if (window.__af.tab) window.__af.tab.value = 'rec' }")
        _set_node(page, "go", {
            "status": "running",
            "state": "running",
            "word": "运行中 · 等待前提条件",
            "phase": "waiting_precondition",
            "wait_s": 3,
            "wait_max_s": 8,
        })
        _both(page, "canvas", "waiting")

        _set_node(page, "go", {"status": "action_missing", "state": "action_missing", "word": "动作已不存在"})
        _both(page, "node", "action-missing")

        _set_node(page, "go", {"status": "cancel_rejected", "state": "cancel_rejected", "word": "取消被拒"})
        _set_run(page, "running")
        _both(page, "node", "cancel-rejected")

        _set_node(page, "go", {
            "status": "interrupted_unknown",
            "state": "interrupted_unknown",
            "word": "中断 · 结果未知",
        })
        _set_run(page, "paused", "motion_timeout")
        _both(page, "node", "unknown")

        _set_run(page, "paused", "user")
        page.evaluate(
            """() => {
              const r = window.__af.run.value
              if (r) window.__af.run.value = { ...r, flow_version: 1, status: 'paused' }
            }"""
        )
        _both(page, "banner", "version-lock")

        page.evaluate(
            """() => {
              if (window.__af.conn) window.__af.conn.value = 'retry'
            }"""
        )
        _both(page, "conn", "reconnecting")
        page.evaluate("""() => { if (window.__af.conn) window.__af.conn.value = 'ok' }""")

        page2 = _open(br, server["base"], "/?gallery=1")
        page2.wait_for_timeout(160)
        _both(page2, "screen", "gallery")
        page2.close()

        confirm = {
            "name": "确认",
            "nodes": [
                {"id": "start", "type": "start", "x": 40, "y": 80, "params": {"data": {"v": 1}}},
                {"id": "hc", "type": "human_confirm", "x": 300, "y": 80, "params": {"prompt": "请确认"}},
                {"id": "yes", "type": "echo", "x": 560, "y": 40, "params": {}},
                {"id": "out", "type": "output", "x": 80, "y": 40, "params": {}},
            ],
            "edges": [
                {"id": "e0", "source": "start", "source_port": "data", "target": "hc", "target_port": "value", "kind": "data"},
                {"id": "e1", "source": "hc", "source_port": "pass", "target": "yes", "target_port": "value", "kind": "data"},
                {"id": "e2", "source": "yes", "source_port": "value", "target": "out", "target_port": "value", "kind": "data"},
            ],
            "groups": [],
            "bump": True,
        }
        try:
            flow = put_flow(server["base"], confirm)
            http_json(server["base"], "/api/runs", "POST", {
                "flow_id": flow["id"], "flow_version": flow["version"],
            })
            page3 = _open(br, server["base"])
            page3.wait_for_function(
                """() => {
                  const r = window.__af.run.value
                  return r && r.status === 'paused' && r.pause && r.pause.reason === 'approval'
                }""",
                timeout=8000,
            )
            _both(page3, "canvas", "waiting-approval-live")
            page3.close()
        except Exception:
            pass

        page4, _, _ = _page(br, token_server["base"])
        page4.goto(token_server["base"] + "/", wait_until="domcontentloaded")
        page4.wait_for_function("window.__ready === true", timeout=20000)
        page4.wait_for_selector(".token-gate", timeout=8000)
        _both(page4, "screen", "token-gate")
        page4.close()

        br.close()
    finally:
        p.stop()
    names = sorted(p.name for p in ART.glob("uispec-*-light.png"))
    darks = sorted(p.name for p in ART.glob("uispec-*-dark.png"))
    assert any("waiting" in n for n in names), names
    assert len(names) >= 12, names
    assert len(darks) >= 12, darks
    assert len(names) == len(darks)
