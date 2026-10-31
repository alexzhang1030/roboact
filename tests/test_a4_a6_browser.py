"""Browser-side A4 / A5 / A6. Measurements are taken in Chromium against the real backend."""

from __future__ import annotations

import time

import pytest
from roboact.catalog import zh

from tests.conftest import launch_chromium
from tests.http_api import SSE_WRAP, echo_chain_graph, http_json, motion_graph, put_flow
from tests.tcp_proxy import TcpProxy
from tests.test_ui import _launch, _page

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


def test_a5_pause_latency_frames_in_browser(server):
    """A5 / PRD 19.2: 500-node flow running, click pause, rAF frames until Paused ≤ 20."""
    put_flow(server["base"], echo_chain_graph(500, "A5 500 nodes"))
    http_json(server["base"], "/api/test/step-delay", "POST", {"seconds": 0.01})
    p, _path = _launch()
    try:
        br = _browser(p)
        page, blocked, _errs = _open(br, server["base"])
        page.wait_for_function(
            "() => document.querySelectorAll('.vue-flow__node').length >= 400",
            timeout=120000,
        )
        samples: list[int] = []
        attempts = 0
        while len(samples) < 5 and attempts < 10:
            attempts += 1
            _click_run(page)
            _wait_run(page, "running", timeout=30000)
            page.wait_for_function(
                """() => {
                  const r = window.__af.run.value
                  return r && r.status === 'running' && (r.steps_used || r.global_step || 0) >= 1
                }""",
                timeout=30000,
            )
            frames = page.evaluate(
                """(pausedZh) => {
                  return new Promise((resolve) => {
                    const r = window.__af.run.value
                    if (!r || r.status !== 'running') { resolve(-1); return }
                    document.querySelector('.slot-1').click()
                    let n = 0
                    const loop = () => {
                      n += 1
                      const el = document.querySelector('.run-word')
                      const w = el ? (el.textContent || '') : ''
                      const paused = w.indexOf('Paused') === 0
                        || w.indexOf(pausedZh) === 0
                      if (paused) resolve(n)
                      else if (n > 180) resolve(n)
                      else requestAnimationFrame(loop)
                    }
                    requestAnimationFrame(loop)
                  })
                }""",
                zh("run.word.paused"),
            )
            if int(frames) < 0:
                page.evaluate("() => window.__af.cmd('stop')")
            else:
                samples.append(int(frames))
                page.evaluate("() => window.__af.cmd('stop')")
            page.wait_for_function(
                """() => {
                  const r = window.__af.run.value
                  return !r || ['stopped', 'completed', 'error', 'paused'].includes(r.status)
                }""",
                timeout=30000,
            )
        assert len(samples) >= 5, f"A5 needed 5 reps, got {samples}"
        samples.sort()
        mn, med, mx = samples[0], _median([float(x) for x in samples]), samples[-1]
        print(
            f"A5 pause rAF frames over {len(samples)} reps: "
            f"min={mn} median={med:.1f} max={mx} samples={samples}",
            flush=True,
        )
        assert mn <= 20 and med <= 20 and mx <= 20, (
            f"A5 min/median/max must all be ≤20; min={mn} median={med} max={mx} samples={samples}"
        )
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
    """A6: real TCP cut + restore, then half-open cut → operator_lost, no new motion."""
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

        # --- half-open: stop forwarding, no FIN, SSE writes still succeed ---
        page.evaluate("() => window.__af.cmd('stop')")
        http_json(
            server["base"], "/api/test/adapter", "POST", {"async_steps": 28, "async_sleep_s": 0.4}
        )
        page.wait_for_timeout(200)
        _click_run(page)
        _wait_run(page, "running", timeout=20000)
        rid2 = _run_id(page)
        _wait_node_running(server["base"], rid2, "go")
        recs_at_cut = list(http_json(server["base"], f"/api/runs/{rid2}").get("records") or [])
        assert not any(r.get("node_id") == "grip" for r in recs_at_cut), recs_at_cut
        cut2 = time.monotonic()
        proxy.half_open()
        paused = None
        while time.monotonic() - cut2 < 20:
            snap = http_json(server["base"], f"/api/runs/{rid2}")
            st = (snap.get("run") or snap).get("status")
            if st == "paused":
                paused = snap
                break
            time.sleep(0.05)
        dt_lost = time.monotonic() - cut2
        print(f"A6 half-open cut→operator_lost: {dt_lost:.3f}s", flush=True)
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
        # exec-model §5.1: about 10 s. Current motion may finish first; allow
        # slack.
        assert dt_lost >= 7.0, f"operator_lost too fast ({dt_lost:.3f}s); deadline is 10s"
        print(
            f"A6 half-open checks: reason=operator_lost dt={dt_lost:.3f}s "
            f"within_10s={dt_lost <= 10.0} "
            f"nodes={[r.get('node_id') for r in recs_after]} cut_reconnect_s={cut_dt:.3f}",
            flush=True,
        )
        br.close()
    finally:
        p.stop()
        proxy.close()
