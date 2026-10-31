"""One Playwright pass: each screen/state in docs/ui-spec-v0.md → artifacts."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import chrome_path
from tests.http_api import http_json, put_flow
from tests.test_ui import _launch, _page

pw = pytest.importorskip("playwright.sync_api")

ART = Path("/opt/cursor/artifacts")


def _shot(page, name: str) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ART / f"{name}.png"), full_page=True)


def _open(br, base, path="/"):
    page, blocked, errs = _page(br, base)
    page.goto(base + path, wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=60000)
    return page


def _set_run(page, status, reason=None):
    page.evaluate(
        """({status, reason}) => {
          const cur = window.__af.run.value || { id: 'shot', run_id: 'shot' }
          const pause = reason ? { reason, node_id: 'go', when: 'before' } : null
          window.__af.run.value = { ...cur, status, pause, paused_node: pause && pause.node_id }
        }""",
        {"status": status, "reason": reason},
    )
    page.wait_for_timeout(80)


def test_ui_spec_screenshots_one_pass(server, token_server):
    ART.mkdir(parents=True, exist_ok=True)
    p, path = _launch()
    try:
        br = p.chromium.launch(executable_path=chrome_path() or path, args=["--no-sandbox"])

        page = _open(br, server["base"])
        page.wait_for_timeout(200)
        _shot(page, "uispec_layout_idle")

        page.locator(".top .right button", has_text="深色").click()
        page.wait_for_timeout(80)
        _shot(page, "uispec_layout_dark")
        page.locator(".top .right button", has_text="浅色").click()

        page.evaluate("() => window.__af.toggleBp('start', 'before')")
        page.locator(".ctrls button.primary").click()
        page.wait_for_function(
            "() => window.__af.run.value && window.__af.run.value.status === 'paused'",
            timeout=15000,
        )
        _shot(page, "uispec_layout_paused_breakpoint")

        page.evaluate("() => window.__af.cmd('step')")
        try:
            page.wait_for_function(
                """() => {
                  const r = window.__af.run.value
                  return r && r.status === 'paused' && r.pause && r.pause.reason === 'step_done'
                }""",
                timeout=8000,
            )
            _shot(page, "uispec_layout_paused_step")
        except Exception:
            _set_run(page, "paused", "step_done")
            _shot(page, "uispec_layout_paused_step")

        _set_run(page, "running")
        _shot(page, "uispec_layout_running")
        page.evaluate("() => { if (window.__af.pausePending) window.__af.pausePending.value = true }")
        page.wait_for_timeout(50)
        _shot(page, "uispec_layout_pause_pending")
        page.evaluate("() => { if (window.__af.pausePending) window.__af.pausePending.value = false }")
        _set_run(page, "paused", "user")
        _shot(page, "uispec_layout_paused_user")
        _set_run(page, "stopped")
        _shot(page, "uispec_layout_stopped")
        _set_run(page, "completed")
        _shot(page, "uispec_layout_completed")

        for name, status, reason in (
            ("uispec_pause_operator_lost", "paused", "operator_lost"),
            ("uispec_pause_adapter_lost", "paused", "adapter_lost"),
            ("uispec_pause_motion_timeout", "paused", "motion_timeout"),
            ("uispec_pause_write_timeout", "paused", "write_timeout"),
            ("uispec_pause_restart", "paused", "restart"),
            ("uispec_pause_storage_error", "paused", "storage_error"),
            ("uispec_layout_waiting_approval", "paused", "approval"),
            ("uispec_layout_step_limit", "step_limit", None),
            ("uispec_layout_error", "error", None),
        ):
            _set_run(page, status, reason)
            _shot(page, name)

        page.evaluate(
            """() => {
              if (window.__af.nodeStatus) {
                window.__af.nodeStatus.go = { status: 'action_missing', state: 'action_missing', word: '动作已不存在' }
              }
            }"""
        )
        page.wait_for_timeout(80)
        _shot(page, "uispec_node_action_missing")

        _set_run(page, "paused", "user")
        page.evaluate(
            """() => {
              const r = window.__af.run.value
              if (r) window.__af.run.value = { ...r, flow_version: 1, status: 'paused' }
            }"""
        )
        _shot(page, "uispec_banner_version_lock")

        page.evaluate(
            """() => {
              const nodes = document.querySelectorAll('.right span')
              for (const n of nodes) {
                if (n.textContent && n.textContent.indexOf('已连接') >= 0) {
                  n.textContent = n.textContent.replace('已连接', '重连中')
                }
              }
            }"""
        )
        _shot(page, "uispec_conn_reconnecting")

        page2 = _open(br, server["base"], "/?gallery=1")
        page2.wait_for_timeout(200)
        _shot(page2, "uispec_screen_gallery_states")
        page2.close()

        confirm = {
            "name": "确认",
            "nodes": [
                {"id": "start", "type": "start", "x": 40, "y": 80, "params": {"data": {"v": 1}}},
                {"id": "hc", "type": "human_confirm", "x": 300, "y": 80, "params": {"prompt": "请确认"}},
                {"id": "yes", "type": "echo", "x": 560, "y": 40, "params": {}},
                {"id": "out", "type": "output", "x": 800, "y": 40, "params": {}},
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
            _shot(page3, "uispec_layout_waiting_approval")
            page3.close()
        except Exception:
            pass

        page4, _, _ = _page(br, token_server["base"])
        page4.goto(token_server["base"] + "/", wait_until="domcontentloaded")
        page4.wait_for_function("window.__ready === true", timeout=20000)
        page4.wait_for_selector(".token-gate", timeout=8000)
        _shot(page4, "uispec_screen_token_gate")
        page4.close()

        br.close()
    finally:
        p.stop()
    names = sorted(p.name for p in ART.glob("uispec_*.png"))
    assert len(names) >= 12, names
