"""Browser-side A4 / A5 / A6. Measurements are taken in Chromium against the real backend."""

from __future__ import annotations

import itertools
import time
from pathlib import Path

import pytest
from roboact import A5_MAX_MS, A5_P95_MS
from roboact.adapter import FAKE_ACTIONS
from roboact.catalog import zh

from tests.conftest import launch_chromium
from tests.http_api import (
    SSE_WRAP,
    echo_chain_graph,
    http_json,
    http_status,
    motion_graph,
    put_flow,
)
from tests.tcp_proxy import TcpProxy
from tests.test_i18n import _catalog
from tests.test_ui import _launch, _page

_ZH = _catalog(Path(__file__).resolve().parents[1] / "web" / "i18n" / "zh-CN.js")

pw = pytest.importorskip("playwright.sync_api")


def _browser(p):
    return launch_chromium(p)


def _open(br, base, wrap_sse=False):
    page, blocked, errs = _page(br, base)
    if wrap_sse:
        page.add_init_script(SSE_WRAP)
    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=120000)
    return page, blocked, errs


def _wait_run(page, *statuses, timeout=120000):
    wanted = list(statuses)
    page.wait_for_function(
        """(wanted) => {
          const r = window.__af && window.__af.run && window.__af.run.value
          return r && wanted.includes(r.status)
        }""",
        arg=wanted,
        timeout=timeout,
    )


def _run_id(page) -> str:
    rid = page.evaluate(
        "() => window.__af.run.value && (window.__af.run.value.run_id || window.__af.run.value.id)"
    )
    assert rid, "no run id on the page"
    return rid


def _click_run(page):
    return page.evaluate(
        "async () => { await window.__af.doRun('all'); return window.__af.run.value && "
        "window.__af.run.value.status }"
    )


def _ui_states(page) -> dict:
    return page.evaluate("""() => {
      const s = window.__af.nodeStatus
      const out = {}
      for (const id of Object.keys(s)) out[id] = s[id].state || s[id].status
      return out
    }""")


def _backend_states(snap: dict) -> dict:
    nodes = snap.get("nodes") or (snap.get("run") or {}).get("nodes") or {}
    return {k: (v.get("state") or v.get("status")) for k, v in nodes.items() if isinstance(v, dict)}


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _p95(xs: list[float]) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    k = (len(s) - 1) * 0.95
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _stop_run(page) -> None:
    page.evaluate("() => window.__af.cmd('stop')")
    page.wait_for_function(
        """() => {
          const r = window.__af.run.value
          return !r || ['stopped', 'completed', 'error'].includes(r.status)
            || (r.status === 'paused' && r.pause && r.pause.reason)
        }""",
        timeout=30000,
    )
    st = page.evaluate("() => window.__af.run.value && window.__af.run.value.status")
    if st == "paused":
        page.evaluate("() => window.__af.cmd('stop')")
        page.wait_for_function(
            """() => {
              const r = window.__af.run.value
              return !r || ['stopped', 'completed', 'error'].includes(r.status)
            }""",
            timeout=30000,
        )


_A5_MEASURE = """(kind) => {
  return new Promise((resolve) => {
    window.__afPauseTimes = []
    const started = Date.now()
    const prevMs = (window.__af.run.value && window.__af.run.value.paused_at_ms) || 0
    if (kind === 'run') window.__af.doRun('all')
    if (kind === 'user') document.querySelector('.slot-1').click()
    if (kind === 'step') document.querySelector('.slot-2').click()
    const t0Of = () => {
      const evs = window.__afPauseTimes
      if (evs && evs.length && evs[0].paused_at_ms) return evs[0].paused_at_ms
      const run = window.__af.run.value
      if (run && run.status === 'paused' && run.paused_at_ms && run.paused_at_ms !== prevMs) {
        return run.paused_at_ms
      }
      return 0
    }
    const poll = () => {
      const t0 = t0Of()
      if (t0) {
        const run = window.__af.run.value
        resolve({
          ms: Date.now() - t0,
          reason: (run && run.pause && run.pause.reason) || '',
        })
        return
      }
      if (Date.now() - started > 20000) {
        const run = window.__af.run.value
        resolve({
          ms: null,
          timed_out: true,
          reason: (run && run.pause && run.pause.reason) || '',
          status: run && run.status,
        })
        return
      }
      requestAnimationFrame(poll)
    }
    requestAnimationFrame(poll)
  })
}"""


def test_a5_pause_latency_frames_in_browser(server):
    """A5 / PRD 19.2: server write of paused → first frame after pause event."""
    flow = put_flow(server["base"], echo_chain_graph(8, "A5 small"))
    fid = flow["id"]
    http_json(server["base"], "/api/test/step-delay", "POST", {"seconds": 0.04})
    http_json(
        server["base"],
        f"/api/flows/{fid}/breakpoints/n2/before",
        "PUT",
        {"enabled": True, "mode": "every"},
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        page, blocked, _errs = _open(br, server["base"])
        page.wait_for_function(
            "() => document.querySelectorAll('.vue-flow__node').length >= 6",
            timeout=60000,
        )
        by_kind: dict[str, list[float]] = {"breakpoint": [], "step": [], "user": []}

        for _ in range(20):
            rec = page.evaluate(_A5_MEASURE, "run")
            assert not rec.get("timed_out"), rec
            assert rec.get("ms") is not None, rec
            assert rec.get("reason") == "breakpoint", rec
            by_kind["breakpoint"].append(float(rec["ms"]))
            page.evaluate("() => { window.__afPauseTimes = [] }")
            rec_s = page.evaluate(_A5_MEASURE, "step")
            assert not rec_s.get("timed_out"), rec_s
            assert rec_s.get("ms") is not None, rec_s
            assert rec_s.get("reason") == "step_done", rec_s
            by_kind["step"].append(float(rec_s["ms"]))
            _stop_run(page)

        http_json(server["base"], f"/api/flows/{fid}/breakpoints/n2/before", "DELETE")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=120000)
        for _ in range(20):
            _click_run(page)
            _wait_run(page, "running", timeout=30000)
            page.wait_for_function(
                """() => {
                  const r = window.__af.run.value
                  return r && r.status === 'running' && (r.steps_used || r.global_step || 0) >= 1
                }""",
                timeout=30000,
            )
            rec = page.evaluate(_A5_MEASURE, "user")
            assert not rec.get("timed_out"), rec
            assert rec.get("ms") is not None, rec
            assert rec.get("reason") == "user", rec
            by_kind["user"].append(float(rec["ms"]))
            _stop_run(page)

        all_ms = by_kind["breakpoint"] + by_kind["step"] + by_kind["user"]
        assert len(all_ms) == 60, {k: len(v) for k, v in by_kind.items()}
        for kind, xs in by_kind.items():
            print(
                f"A5 {kind} n={len(xs)} min={min(xs):.2f} median={_median(xs):.2f} "
                f"p95={_p95(xs):.2f} max={max(xs):.2f} samples={xs}",
                flush=True,
            )
        mn, med, p95, mx = min(all_ms), _median(all_ms), _p95(all_ms), max(all_ms)
        print(
            f"A5 server-write→first-frame over {len(all_ms)}: "
            f"min={mn:.2f} median={med:.2f} p95={p95:.2f} max={mx:.2f}",
            flush=True,
        )
        assert mx <= A5_MAX_MS, f"A5 max {mx:.2f}ms > {A5_MAX_MS}ms samples={all_ms}"
        assert p95 <= A5_P95_MS, f"A5 p95 {p95:.2f}ms > {A5_P95_MS}ms samples={all_ms}"
        assert blocked == []
        br.close()
    finally:
        p.stop()


def test_a4_sse_batching_500_nodes_browser(server):
    """A4 / PRD 19.1: 500 nodes in Chromium, real SSE, ≤10 batches/s, canvas matches backend."""
    put_flow(server["base"], echo_chain_graph(500, "A4 500 nodes"))
    http_json(server["base"], "/api/test/step-delay", "POST", {"seconds": 0.01})
    p, _path = _launch()
    try:
        br = _browser(p)
        page, blocked, _errs = _open(br, server["base"], wrap_sse=True)
        page.wait_for_function(
            "() => document.querySelectorAll('.vue-flow__node').length >= 400",
            timeout=120000,
        )
        page.evaluate("() => { window.__sseLog = [] }")
        t0 = time.monotonic()
        _click_run(page)
        _wait_run(page, "completed", "error", "stopped", "step_limit", timeout=180000)
        dt = time.monotonic() - t0
        status = page.evaluate("() => window.__af.run.value && window.__af.run.value.status")
        assert status == "completed", status
        log = page.evaluate("() => window.__sseLog")
        rid = _run_id(page)
        nodes_ev = [e for e in log if e.get("type") == "nodes"]
        n_batches = len(nodes_ev)
        rate = n_batches / dt if dt > 0 else 999
        print(
            f"A4 SSE batches: n={n_batches} duration_s={dt:.3f} batches_per_sec={rate:.3f}",
            flush=True,
        )
        assert rate <= 10, f"A4 batches/s {rate:.3f} > 10 (n={n_batches} dt={dt:.3f}s)"
        snap = http_json(server["base"], f"/api/runs/{rid}")
        ui = _ui_states(page)
        be = _backend_states(snap)
        mismatches = []
        for nid, st in be.items():
            if ui.get(nid) != st:
                mismatches.append((nid, ui.get(nid), st))
        assert mismatches == [], f"canvas != backend: {mismatches[:12]} ({len(mismatches)} total)"
        assert blocked == []
        br.close()
    finally:
        p.stop()


def _configure_motion(base: str, **body: object) -> None:
    http_json(base, "/api/test/adapter", "POST", body)


def _cut_to_operator_lost(
    page, proxy: TcpProxy, base: str, *, wait_s: float = 20.0
) -> tuple[float, dict]:
    """Cut the page mid-`go`. Return (seconds from cut to pause, snap)."""
    _click_run(page)
    _wait_run(page, "running", timeout=20000)
    rid = _run_id(page)
    _wait_node_running(base, rid, "go")
    time.sleep(0.4)
    recs_at_cut = list(http_json(base, f"/api/runs/{rid}").get("records") or [])
    assert not any(r.get("node_id") == "grip" for r in recs_at_cut), recs_at_cut
    http_json(base, "/api/test/adapter", "POST", {"clear_cancel_calls": True})
    cut = time.monotonic()
    proxy.half_open()
    paused = None
    while time.monotonic() - cut < wait_s:
        snap = http_json(base, f"/api/runs/{rid}")
        st = (snap.get("run") or snap).get("status")
        if st == "paused":
            paused = snap
            break
        time.sleep(0.05)
    dt_lost = time.monotonic() - cut
    assert paused is not None, f"backend never paused after half-open (waited {dt_lost:.3f}s)"
    reason = ((paused.get("run") or paused).get("pause") or {}).get("reason")
    assert reason == "operator_lost", (
        reason,
        paused.get("pause") or (paused.get("run") or {}).get("pause"),
    )
    recs_after = list(paused.get("records") or [])
    assert not any(r.get("node_id") == "grip" for r in recs_after), [
        r.get("node_id") for r in recs_after
    ]
    return dt_lost, paused


def _wait_node_running(base: str, rid: str, nid: str, timeout: float = 15.0) -> dict:
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        snap = http_json(base, f"/api/runs/{rid}")
        last = snap
        node = (snap.get("nodes") or {}).get(nid) or {}
        recs = snap.get("records") or []
        if (node.get("state") or node.get("status")) == "running" or any(
            r.get("node_id") == nid and r.get("status") == "running" for r in recs
        ):
            return snap
        time.sleep(0.04)
    raise TimeoutError(f"{nid} never started; last={last}")


def test_a6_reconnect_and_half_open_page_lost(server):
    """A6: real TCP cut + restore → run finishes, Last-Event-ID does not go backwards."""
    put_flow(server["base"], motion_graph("A6 reconnect"))
    http_json(
        server["base"], "/api/test/adapter", "POST", {"async_steps": 20, "async_sleep_s": 0.3}
    )
    proxy = TcpProxy("127.0.0.1", server["port"])
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, proxy.base, wrap_sse=True)
        page.evaluate("() => { window.__sseLog = [] }")
        _click_run(page)
        _wait_run(page, "running", timeout=30000)
        rid = _run_id(page)
        _wait_node_running(server["base"], rid, "go")
        ids_before = page.evaluate(
            "() => window.__sseLog.filter(e => e.id).map(e => e.type + ':' + e.id)"
        )
        cut_t0 = time.monotonic()
        proxy.cut()
        page.wait_for_function(
            """(retry) => {
              const t = document.body.innerText
              return t.includes('Reconnecting') || t.includes(retry)
            }""",
            arg=zh("conn.retry"),
            timeout=8000,
        )
        time.sleep(1.6)
        proxy.restore()
        cut_dt = time.monotonic() - cut_t0
        page.wait_for_function(
            """(ok) => {
              const t = document.body.innerText
              return t.includes('Connected') || t.includes(ok)
            }""",
            arg=zh("conn.ok"),
            timeout=15000,
        )
        _wait_run(page, "completed", "paused", "error", "stopped", timeout=120000)
        status = page.evaluate("() => window.__af.run.value && window.__af.run.value.status")
        assert status == "completed", status
        log = page.evaluate("() => window.__sseLog")
        keyed = [f"{e['type']}:{e.get('id')}" for e in log if e.get("id")]
        assert len(keyed) > len(ids_before), (len(keyed), len(ids_before), cut_dt)
        node_ids = []
        for e in log:
            if e.get("type") == "nodes" and str(e.get("id") or "").isdigit():
                node_ids.append(int(e["id"]))
        # Last-Event-ID replay may resend the last seq; ids must not go
        # backwards.
        assert node_ids == sorted(node_ids), node_ids
        snap = http_json(server["base"], f"/api/runs/{rid}")
        ui = _ui_states(page)
        be = _backend_states(snap)
        mismatches = [(nid, ui.get(nid), st) for nid, st in be.items() if ui.get(nid) != st]
        assert mismatches == [], f"reconnect canvas != backend: {mismatches[:12]}"
        print(
            f"A6 reconnect: cut_duration_s={cut_dt:.3f} events_before={len(ids_before)} "
            f"events_after={len(keyed)} node_seq={node_ids}",
            flush=True,
        )
        br.close()
    finally:
        p.stop()
        proxy.close()


def _restore_and_stop(page, proxy: TcpProxy) -> None:
    proxy.restore()
    page.wait_for_function(
        """(ok) => {
          const t = document.body.innerText
          return t.includes('Connected') || t.includes(ok)
        }""",
        arg=zh("conn.ok"),
        timeout=15000,
    )
    _stop_run(page)
    page.wait_for_timeout(200)


def test_r16_cancellable_cut_pauses_within_10s(server):
    """r1–r6 step 1: cancellable 30 s motion, real TCP cut, pause ≤ 10 s, 5 reps."""
    put_flow(server["base"], motion_graph("r16 cancellable 30s"))
    _configure_motion(server["base"], async_steps=1, async_sleep_s=30.0)
    proxy = TcpProxy("127.0.0.1", server["port"])
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, proxy.base)
        samples: list[float] = []
        for i in range(5):
            dt, snap = _cut_to_operator_lost(page, proxy, server["base"], wait_s=15)
            calls = http_json(server["base"], "/api/test/adapter").get("cancel_calls") or []
            pause = (snap.get("run") or snap).get("pause") or {}
            rb = (pause.get("readback") or {}).get("state") or {}
            samples.append(dt)
            print(
                f"r1-r6 step1 rep {i + 1}/5: {dt:.3f}s cancel_calls={len(calls)} "
                f"readback={bool(rb)}",
                flush=True,
            )
            assert calls, f"cancel did not reach the adapter on rep {i + 1}"
            assert rb, f"robot state not read back on rep {i + 1}: {pause}"
            assert dt <= 10.0, f"r1-r6 step1 pause {dt:.3f}s > 10s"
            proxy.restore()
            page.wait_for_function(
                """(ok) => {
                  const t = document.body.innerText
                  return t.includes('Connected') || t.includes(ok)
                }""",
                arg=zh("conn.ok"),
                timeout=15000,
            )
            page.evaluate(
                """async () => {
                  const run = window.__af.run.value
                  const rid = run && (run.run_id || run.id)
                  const r = await (await fetch('/api/runs/' + rid)).json()
                  const snap = r.run || r
                  window.__af.run.value = {
                    ...run, ...snap, status: snap.status, pause: snap.pause,
                  }
                  window.__af.selected.value = 'go'
                }"""
            )
            page.wait_for_selector("[data-testid=readback]", timeout=10000)
            _stop_run(page)
            page.wait_for_timeout(200)
        print(f"r1-r6 step1 five times (s): {samples}", flush=True)
        br.close()
    finally:
        p.stop()
        proxy.close()


def test_r16_noncancellable_runs_to_end(server):
    """r1–r6 step 2: non-cancellable timeout_s ≤ 10, runs to end, then pause."""
    put_flow(server["base"], motion_graph("r16 noncancellable"))
    _configure_motion(
        server["base"],
        async_steps=1,
        async_sleep_s=10.3,
        action_updates={
            "fake_set_pose": {
                "timeout_s": 10,
                "cancel": {"cancellable": False, "post_cancel": "hold"},
            }
        },
    )
    proxy = TcpProxy("127.0.0.1", server["port"])
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, proxy.base)
        dt, snap = _cut_to_operator_lost(page, proxy, server["base"], wait_s=20)
        calls = http_json(server["base"], "/api/test/adapter").get("cancel_calls") or []
        recs = list(snap.get("records") or [])
        go = [r for r in recs if r.get("node_id") == "go"]
        print(
            f"r1-r6 step2: {dt:.3f}s cancel_calls={len(calls)} go_status="
            f"{go[-1].get('status') if go else None}",
            flush=True,
        )
        assert calls == [], calls
        assert go and go[-1].get("status") in ("done", "stopped"), go
        _restore_and_stop(page, proxy)
        br.close()
    finally:
        p.stop()
        proxy.close()


def test_r16_timeout_cap_blocks_run_browser(server):
    """r1–r6 step 3: non-cancellable timeout_s = 11 → Run blocked, names the node."""
    put_flow(server["base"], motion_graph("r16 timeout cap"))
    _configure_motion(
        server["base"],
        action_updates={
            "fake_set_pose": {
                "timeout_s": 11,
                "cancel": {"cancellable": False, "post_cancel": "hold"},
            }
        },
    )
    boot = http_json(server["base"], "/api/bootstrap")
    fid = boot["flow"]["id"]
    check = http_json(server["base"], f"/api/flows/{fid}/run-check")
    assert check.get("can_run") is False, check
    assert check.get("timeout_errors"), check
    node = check["timeout_errors"][0]["node"]
    assert node, check
    code, body = http_status(
        server["base"],
        "/api/runs",
        "POST",
        {
            "flow_id": fid,
            "flow_version": boot["flow"]["version"],
            "max_steps": 1000,
            "client_id": "t",
        },
    )
    assert code == 409, (code, body)
    assert (body or {}).get("error") == "motion_timeout_cap", body
    assert node in str((body or {}).get("message") or "") or node in str(
        (body or {}).get("detail") or ""
    ), body
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        page.wait_for_function(
            """() => {
              const c = window.__af.runCheck.value
              return c && (c.timeout_errors || []).length
            }""",
            timeout=15000,
        )
        assert page.evaluate("() => window.__af.slot1Disabled.value") is True
        text = page.evaluate("() => window.__af.timeoutCapText.value")
        assert node in (text or ""), (node, text)
        assert page.locator("[data-testid=motion-timeout-cap]").inner_text()
        br.close()
    finally:
        p.stop()


def test_r16_cancel_rejected_then_pause(server):
    """r1–r6 step 4: adapter refuses cancel → cancel_rejected, runs to end, pause."""
    put_flow(server["base"], motion_graph("r16 cancel rejected"))
    _configure_motion(
        server["base"],
        async_steps=1,
        async_sleep_s=15.0,
        reject_next_cancel=True,
        clear_cancel_calls=True,
    )
    proxy = TcpProxy("127.0.0.1", server["port"])
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, proxy.base)
        _click_run(page)
        _wait_run(page, "running", timeout=20000)
        rid = _run_id(page)
        _wait_node_running(server["base"], rid, "go")
        time.sleep(0.4)
        proxy.half_open()
        cut = time.monotonic()
        saw_rej = False
        paused = None
        while time.monotonic() - cut < 25:
            snap = http_json(server["base"], f"/api/runs/{rid}")
            node = (snap.get("nodes") or {}).get("go") or {}
            if (node.get("state") or node.get("status")) == "cancel_rejected":
                saw_rej = True
            if (snap.get("run") or snap).get("status") == "paused":
                paused = snap
                break
            time.sleep(0.05)
        dt = time.monotonic() - cut
        assert saw_rej, "node never showed status.cancel_rejected"
        assert paused is not None, f"never paused ({dt:.3f}s)"
        reason = ((paused.get("run") or paused).get("pause") or {}).get("reason")
        assert reason == "operator_lost", reason
        go = [r for r in (paused.get("records") or []) if r.get("node_id") == "go"]
        assert go and go[-1].get("status") == "done", go
        calls = http_json(server["base"], "/api/test/adapter").get("cancel_calls") or []
        assert calls, "refused cancel must still be asked of the adapter"
        print(
            f"r1-r6 step4: {dt:.3f}s cancel_rejected then pause, cancel_calls={len(calls)}",
            flush=True,
        )
        br.close()
    finally:
        p.stop()
        proxy.close()


def _select_in(page, nid: str) -> None:
    page.evaluate(
        """(nid) => { window.__af.selected.value = nid; window.__af.tab.value = 'in' }""",
        nid,
    )


def _confirm(page):
    return page.locator("dialog.confirm-dialog")


def _focused_confirm(page) -> str | None:
    return page.evaluate(
        "() => document.activeElement && document.activeElement.getAttribute('data-confirm')"
    )


def _open_run_this(page, label: str = "Run this only"):
    page.get_by_role("button", name=label).click()
    _confirm(page).wait_for(state="visible")


def test_motion_single_run_confirm_cancel_sends_nothing(server) -> None:
    """Acceptance 8.3: confirm is shown; dismissing it sends no action."""
    put_flow(server["base"], motion_graph("motion confirm"))
    text = next(a.confirm_text for a in FAKE_ACTIONS if a.id == "fake_set_pose")
    assert text
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "go")
        _open_run_this(page)
        assert page.locator("#confirm-dialog-body").inner_text() == text
        page.locator("[data-confirm=cancel]").click()
        _confirm(page).wait_for(state="hidden")
        assert page.evaluate("() => window.__af.run.value") in (None, False)
        runs = http_json(server["base"], "/api/runs")
        assert runs["runs"] == []
        probe = http_json(server["base"], "/api/test/adapter")
        assert probe.get("goals_sent") == []
        br.close()
    finally:
        p.stop()


def test_motion_single_run_zh_confirm_shows_chinese_and_accept_sends(server) -> None:
    """zh-CN single run of Go to pose: Chinese body; Run sends, Cancel does not."""
    put_flow(server["base"], motion_graph("motion confirm zh"))
    text = zh("confirm.set_pose")
    assert text
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        page.evaluate("() => window.__af.setLocaleMode('zh-CN')")
        page.wait_for_function("() => document.documentElement.dataset.lang === 'zh-CN'")
        _select_in(page, "go")
        _open_run_this(page, zh("in.run_this"))
        assert page.locator("#confirm-dialog-body").inner_text() == text
        page.locator("[data-confirm=cancel]").click()
        _confirm(page).wait_for(state="hidden")
        assert page.evaluate("() => window.__af.run.value") in (None, False)
        assert http_json(server["base"], "/api/runs")["runs"] == []
        assert http_json(server["base"], "/api/test/adapter").get("goals_sent") == []
        _open_run_this(page, zh("in.run_this"))
        with page.expect_response(lambda r: "/api/single-runs/node" in r.url) as sent:
            page.locator("[data-confirm=run]").click()
        assert sent.value.status == 201, sent.value.status
        assert sent.value.request.post_data_json["confirm"] is True
        page.wait_for_timeout(500)
        goals = http_json(server["base"], "/api/test/adapter").get("goals_sent") or []
        assert goals == ["fake_set_pose"], goals
        br.close()
    finally:
        p.stop()


def test_confirm_focus_starts_on_cancel(server) -> None:
    put_flow(server["base"], motion_graph("confirm focus"))
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "go")
        _open_run_this(page)
        assert _focused_confirm(page) == "cancel"
        page.keyboard.press("Tab")
        assert _focused_confirm(page) == "run"
        page.keyboard.press("Shift+Tab")
        assert _focused_confirm(page) == "cancel"
        page.keyboard.press("Escape")
        br.close()
    finally:
        p.stop()


def test_confirm_enter_on_cancel_does_not_run(server) -> None:
    put_flow(server["base"], motion_graph("confirm enter cancel"))
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "go")
        _open_run_this(page)
        page.keyboard.press("Enter")
        _confirm(page).wait_for(state="hidden")
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.__af.run.value") in (None, False)
        assert http_json(server["base"], "/api/runs")["runs"] == []
        assert http_json(server["base"], "/api/test/adapter").get("goals_sent") == []
        br.close()
    finally:
        p.stop()


def test_confirm_escape_cancels(server) -> None:
    put_flow(server["base"], motion_graph("confirm esc"))
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "go")
        _open_run_this(page)
        page.keyboard.press("Escape")
        _confirm(page).wait_for(state="hidden")
        page.wait_for_timeout(300)
        assert http_json(server["base"], "/api/runs")["runs"] == []
        br.close()
    finally:
        p.stop()


def test_confirm_backdrop_cancels(server) -> None:
    put_flow(server["base"], motion_graph("confirm backdrop"))
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "go")
        _open_run_this(page)
        page.mouse.click(8, 8)
        _confirm(page).wait_for(state="hidden")
        page.wait_for_timeout(300)
        assert http_json(server["base"], "/api/runs")["runs"] == []
        br.close()
    finally:
        p.stop()


def test_confirm_run_sends(server) -> None:
    put_flow(server["base"], motion_graph("confirm run"))
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "go")
        _open_run_this(page)
        with page.expect_response(lambda r: "/api/single-runs/node" in r.url) as sent:
            page.locator("[data-confirm=run]").click()
        assert sent.value.status == 201, sent.value.status
        assert sent.value.request.post_data_json["confirm"] is True
        page.wait_for_timeout(400)
        goals = http_json(server["base"], "/api/test/adapter").get("goals_sent") or []
        assert goals == ["fake_set_pose"], goals
        br.close()
    finally:
        p.stop()


def test_confirm_focus_returns(server) -> None:
    put_flow(server["base"], motion_graph("confirm focus return"))
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "go")
        opener = page.get_by_role("button", name="Run this only")
        opener.focus()
        opener.click()
        _confirm(page).wait_for(state="visible")
        page.keyboard.press("Escape")
        _confirm(page).wait_for(state="hidden")
        same = page.evaluate(
            """() => {
              const btn = [...document.querySelectorAll('button')]
                .find(el => (el.textContent || '').includes('Run this only'))
              return document.activeElement === btn
            }"""
        )
        assert same
        br.close()
    finally:
        p.stop()


def test_confirm_zh_title_and_buttons(server) -> None:
    put_flow(server["base"], motion_graph("confirm zh labels"))
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        page.evaluate("() => window.__af.setLocaleMode('zh-CN')")
        page.wait_for_function("() => document.documentElement.dataset.lang === 'zh-CN'")
        _select_in(page, "go")
        _open_run_this(page, zh("in.run_this"))
        name = _ZH["node.fake_set_pose"]
        assert page.locator("#confirm-dialog-title").inner_text() == _ZH["confirm.title"].replace(
            "{name}", name
        )
        assert page.locator("[data-confirm=cancel]").inner_text() == _ZH["confirm.cancel"]
        assert page.locator("[data-confirm=run]").inner_text() == _ZH["confirm.run"]
        assert page.locator("#confirm-dialog-body").inner_text() == zh("confirm.set_pose")
        page.keyboard.press("Escape")
        br.close()
    finally:
        p.stop()


def _tool_graph(name: str) -> dict:
    graph = motion_graph(name)
    graph["nodes"].append(
        {
            "id": "tool",
            "type": "tool_call",
            "title": "Clock tool",
            "x": 40,
            "y": 40,
            "params": {"tool": "clock", "allowed_tools": []},
        }
    )
    return graph


def test_confirm_tool_run_sends_confirm_text(server) -> None:
    put_flow(server["base"], _tool_graph("confirm tool"))
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "tool")
        page.get_by_role("button", name="Run for real").click()
        _confirm(page).wait_for(state="visible")
        body = page.locator("#confirm-dialog-body").inner_text()
        assert body == "This will run the selected tool for real (it may write files)."
        assert page.locator("#confirm-dialog-title").inner_text() == "Run this tool for real?"
        with page.expect_response(lambda r: "/api/single-runs/node" in r.url) as sent:
            page.locator("[data-confirm=run]").click()
        assert sent.value.status == 201, sent.value.status
        assert sent.value.request.post_data_json["confirm"] == body
        br.close()
    finally:
        p.stop()


_LONG_CONFIRM = " ".join(
    f"Sentence {i} of the motion confirm must remain fully readable." for i in range(1, 61)
)
_BUSY = "The flow is running. Pause it, stop it, or wait for it to finish."


def _inside_viewport(page) -> dict:
    return page.evaluate(
        """() => {
          const vh = window.innerHeight
          const inside = (el) => {
            const r = el.getBoundingClientRect()
            return r.height > 0 && r.top >= -1 && r.bottom <= vh + 1
          }
          const body = document.getElementById('confirm-dialog-body')
          const dlg = document.getElementById('confirm-dialog')
          return {
            title: inside(document.getElementById('confirm-dialog-title')),
            cancel: inside(document.querySelector('[data-confirm=cancel]')),
            run: inside(document.querySelector('[data-confirm=run]')),
            scrollable: body.scrollHeight > body.clientHeight,
            text: body.textContent,
            dialogH: dlg.getBoundingClientRect().height,
            maxH: vh - 48,
          }
        }"""
    )


def test_confirm_body_scrolls_when_taller_than_viewport(server) -> None:
    """A body longer than the viewport stays fully in the element; only it scrolls."""
    put_flow(server["base"], motion_graph("confirm long"))
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {
            "action_updates": {
                "fake_set_pose": {
                    "confirm_text": _LONG_CONFIRM,
                    "confirm_labels": {"en": _LONG_CONFIRM, "zh-CN": _LONG_CONFIRM},
                }
            }
        },
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        _select_in(page, "go")
        _open_run_this(page)
        info = _inside_viewport(page)
        assert info["text"] == _LONG_CONFIRM, (len(info["text"]), len(_LONG_CONFIRM))
        assert info["scrollable"], info
        assert info["title"] and info["cancel"] and info["run"], info
        assert info["dialogH"] <= info["maxH"] + 1, info
        page.keyboard.press("Escape")
        br.close()
    finally:
        p.stop()


def test_run_buttons_off_while_node_executes(server) -> None:
    """Run-this, rerun, and group run are disabled while a node executes. Pin stays on."""
    graph = motion_graph("confirm busy")
    graph["groups"] = [{"id": "g1", "name": "Bunch", "node_ids": ["go", "grip"]}]
    put_flow(server["base"], graph)
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {"async_steps": 1, "async_sleep_s": 40.0},
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        page.evaluate("async () => { await window.__af.doRun('all') }")
        _wait_run(page, "running")
        rid = _run_id(page)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snap = http_json(server["base"], f"/api/runs/{rid}")
            node = (snap.get("nodes") or {}).get("go") or {}
            st = node.get("state") or node.get("status")
            if (snap.get("run") or snap).get("status") == "running" and st == "running":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("go never started")
        _select_in(page, "go")
        run_this = page.get_by_role("button", name="Run this only")
        assert run_this.is_disabled()
        assert page.get_by_role("button", name="Rerun from here").is_disabled()
        assert not page.get_by_role("button", name="Pin this input").is_disabled()
        assert page.locator("#in-busy").inner_text() == _BUSY
        assert run_this.get_attribute("aria-describedby") == "in-busy"
        assert not _confirm(page).is_visible()
        page.evaluate("() => { window.__af.selected.value = null }")
        group_run = page.get_by_role("button", name="Run this group")
        assert group_run.is_disabled()
        assert page.locator("#in-busy-group-g1").inner_text() == _BUSY
        assert group_run.get_attribute("aria-describedby") == "in-busy-group-g1"
        _stop_run(page)
        page.wait_for_function(
            """() => {
              const r = window.__af.run.value
              return r && ['stopped', 'completed', 'error'].includes(r.status)
            }""",
            timeout=20000,
        )
        _select_in(page, "go")
        assert not page.get_by_role("button", name="Run this only").is_disabled()
        assert page.locator("#in-busy").count() == 0
        br.close()
    finally:
        p.stop()


def _describedby_missing(page) -> list:
    return page.evaluate(
        """() => {
          const missing = []
          for (const el of document.querySelectorAll('[aria-describedby]')) {
            const id = el.getAttribute('aria-describedby')
            if (!id || !document.getElementById(id)) {
              missing.push({ text: (el.textContent || '').trim(), id: id || '' })
            }
          }
          return missing
        }"""
    )


def _show_version_banner(page) -> None:
    page.evaluate(
        """() => {
          const meta = window.__af.meta.value
          window.__af.meta.value = { ...meta, version: (meta.version || 0) + 1 }
        }"""
    )
    page.wait_for_function(
        """() => [...document.querySelectorAll('button')].some(
          b => (b.textContent || '').includes('Rerun from the new version')
            || (b.textContent || '').includes('从新版本重跑')
        )"""
    )


def test_busy_describedby_targets_exist(server) -> None:
    """Every aria-describedby while a node executes names an element that is in the DOM."""
    put_flow(server["base"], motion_graph("confirm describedby"))
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {"async_steps": 1, "async_sleep_s": 40.0},
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        page.evaluate("async () => { await window.__af.doRun('all') }")
        _wait_run(page, "running")
        rid = _run_id(page)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snap = http_json(server["base"], f"/api/runs/{rid}")
            node = (snap.get("nodes") or {}).get("go") or {}
            st = node.get("state") or node.get("status")
            if (snap.get("run") or snap).get("status") == "running" and st == "running":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("go never started")
        _select_in(page, "go")
        _show_version_banner(page)

        def check(label: str) -> None:
            missing = _describedby_missing(page)
            assert missing == [], (label, missing)
            banner = page.locator("#in-busy-banner")
            assert banner.count() == 1, label
            assert (
                page.get_by_role("button", name="Rerun from the new version").get_attribute(
                    "aria-describedby"
                )
                == "in-busy-banner"
            )

        check("no groups, node selected")
        page.evaluate("() => { window.__af.selected.value = null }")
        page.wait_for_timeout(50)
        check("no groups, nothing selected")
        page.evaluate(
            """() => {
              window.__af.groups.value = [{ id: 'g1', name: 'Bunch', node_ids: ['go', 'grip'] }]
            }"""
        )
        page.wait_for_timeout(50)
        check("a group, nothing selected")
        group = page.get_by_role("button", name="Run this group")
        assert group.get_attribute("aria-describedby") == "in-busy-group-g1"
        assert page.locator("#in-busy-group-g1").count() == 1
        _select_in(page, "go")
        check("a group, node selected")
        _stop_run(page)
        br.close()
    finally:
        p.stop()


def test_confirm_open_while_paused_keeps_ui_beat(server) -> None:
    """Paused (breakpoint) still allows confirm, and the dialog does not drop the page."""
    flow = put_flow(server["base"], motion_graph("confirm paused beat"))
    http_json(
        server["base"],
        f"/api/flows/{flow['id']}/breakpoints/go/before",
        "PUT",
        {"enabled": True, "mode": "every"},
    )
    http_json(
        server["base"],
        "/api/test/adapter",
        "POST",
        {"async_steps": 1, "async_sleep_s": 40.0},
    )
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, server["base"])
        beats: list[float] = []

        def _on_req(req) -> None:
            if req.method == "POST" and req.url.endswith("/ui-beat"):
                beats.append(time.monotonic())

        page.on("request", _on_req)
        page.evaluate("async () => { await window.__af.doRun('all') }")
        _wait_run(page, "paused")
        _select_in(page, "go")
        assert not page.get_by_role("button", name="Run this only").is_disabled()
        beats.clear()
        opened = time.monotonic()
        _open_run_this(page)
        page.wait_for_timeout(12000)
        closed = time.monotonic()
        assert _confirm(page).is_visible()
        snap = http_json(server["base"], f"/api/runs/{_run_id(page)}")
        run = snap.get("run") or snap
        assert run.get("status") == "paused", run.get("status")
        assert (run.get("pause") or {}).get("reason") == "breakpoint"
        during = [t for t in beats if opened <= t <= closed]
        assert len(during) >= 4, during
        gaps = [b - a for a, b in itertools.pairwise(during)]
        assert gaps and max(gaps) < 4.5, gaps
        page.keyboard.press("Escape")
        _stop_run(page)
        br.close()
    finally:
        p.stop()
