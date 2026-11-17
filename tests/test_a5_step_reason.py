"""Deliver a breakpoint run snapshot after Step and require the pause to stay step_done."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from tests.conftest import launch_chromium
from tests.http_api import echo_chain_graph, http_json, put_flow
from tests.test_ui import _launch, _page

_RUN_GET = re.compile(r"/api/runs/rn_[0-9a-f]+$")


def _reason(page: Any) -> str:
    return str(
        page.evaluate(
            """() => {
              const r = window.__af && window.__af.run && window.__af.run.value
              return (r && r.pause && r.pause.reason) || ''
            }"""
        )
    )


def _pause_reason(body: bytes) -> str:
    try:
        payload = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    run = payload.get("run") if isinstance(payload, dict) else None
    pause = run.get("pause") if isinstance(run, dict) else None
    if not isinstance(pause, dict):
        return ""
    return str(pause.get("reason") or "")


def _pump(page: Any) -> None:
    page.evaluate("() => null")


def _one_step_keeps_step_done(page: Any, base: str) -> None:
    pending: list[Any] = []
    held: dict[str, Any] = {}

    def handle(route: Any) -> None:
        url = route.request.url.split("?", 1)[0]
        if route.request.method != "GET" or _RUN_GET.search(url) is None:
            route.fallback()
            return
        pending.append(route)

    def drain(keep_breakpoint: bool) -> None:
        while pending:
            route = pending.pop(0)
            with urllib.request.urlopen(route.request.url, timeout=5) as resp:
                body = resp.read()
            if keep_breakpoint and "body" not in held and _pause_reason(body) == "breakpoint":
                held["route"] = route
                held["body"] = body
                continue
            route.fulfill(status=200, content_type="application/json", body=body)

    page.route("**/api/runs/**", handle)
    try:
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=120000)
        page.locator("button.slot-1").click()
        page.wait_for_function(
            """() => {
              const r = window.__af.run.value
              return r && r.status === 'paused' && r.pause && r.pause.reason === 'breakpoint'
            }""",
            timeout=30000,
        )
        for _ in range(300):
            drain(True)
            if "body" in held:
                break
            _pump(page)
        else:
            raise AssertionError("breakpoint run GET was not issued")
        page.locator("button.slot-2").click()
        rid = page.evaluate(
            "() => { const r = window.__af.run.value; return r && (r.run_id || r.id) }"
        )
        assert rid, "step did not keep the run id"
        server_reason = ""
        for _ in range(400):
            with urllib.request.urlopen(base + "/api/runs/" + rid, timeout=5) as resp:
                server_reason = _pause_reason(resp.read())
            if server_reason == "step_done":
                break
            _pump(page)
        assert server_reason == "step_done", server_reason
        page.wait_for_function(
            """() => {
              const r = window.__af.run.value
              return r && r.pause && r.pause.reason === 'step_done'
            }""",
            timeout=5000,
        )
        before = int(page.evaluate("() => window.__runGets || 0"))
        route = held.pop("route")
        body = held.pop("body")
        route.fulfill(status=200, content_type="application/json", body=body)
        page.wait_for_function("(n) => (window.__runGets || 0) > n", arg=before, timeout=15000)
        page.evaluate(
            """() => new Promise((resolve) => {
              queueMicrotask(() => queueMicrotask(resolve))
            })"""
        )
        assert _reason(page) == "step_done", _reason(page)
    finally:
        drain(False)
        if "route" in held and "body" in held:
            held["route"].fulfill(status=200, content_type="application/json", body=held["body"])
        page.unroute("**/api/runs/**")
        page.evaluate("() => { if (window.__af && window.__af.cmd) window.__af.cmd('stop') }")


def test_step_after_breakpoint_keeps_step_done_when_old_get_returns(server) -> None:
    """A record refresh issued at the breakpoint must not restore that pause after Step."""
    flow = put_flow(server["base"], echo_chain_graph(8, "step reason"))
    http_json(
        server["base"],
        f"/api/flows/{flow['id']}/breakpoints/n2/before",
        "PUT",
        {"enabled": True, "mode": "every"},
    )
    p, _path = _launch()
    try:
        br = launch_chromium(p)
        page, _blocked, _errs = _page(br, server["base"])
        page.add_init_script(
            """(() => {
              const orig = window.fetch.bind(window)
              window.fetch = async (...args) => {
                const res = await orig(...args)
                const url = String(args[0] && args[0].url ? args[0].url : args[0] || '')
                if (url.indexOf('/api/runs/rn_') !== -1 && url.indexOf('/events') === -1) {
                  window.__runGets = (window.__runGets || 0) + 1
                }
                return res
              }
            })()"""
        )
        _one_step_keeps_step_done(page, server["base"])
        br.close()
    finally:
        p.stop()
