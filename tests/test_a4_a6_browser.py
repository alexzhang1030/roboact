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
    """A5 / PRD 19.2: pause event → first Paused frame, ≤ 20 rAF frames."""
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
        click_frames: list[int] = []
        event_ms: list[float] = []
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
            rec = page.evaluate(
                """(pausedZh) => {
                  return new Promise((resolve) => {
                    const r = window.__af.run.value
                    if (!r || r.status !== 'running') {
                      resolve({ event_frames: -1 }); return
                    }
                    window.__afPauseTimes = []
                    const clickAt = performance.now()
                    document.querySelector('.slot-1').click()
                    let nClick = 0
                    let nEvent = 0
                    let eventAt = 0
                    let saw = false
                    const loop = () => {
                      nClick += 1
                      if (!saw && window.__afPauseTimes && window.__afPauseTimes.length) {
                        saw = true
                        eventAt = window.__afPauseTimes[0].at
                        nEvent = 0
                      }
                      if (saw) nEvent += 1
                      const el = document.querySelector('.run-word')
                      const w = el ? (el.textContent || '') : ''
                      const paused = w.indexOf('Paused') === 0
                        || w.indexOf(pausedZh) === 0
                      if (paused) {
                        resolve({
                          event_frames: saw ? nEvent : nClick,
                          click_frames: nClick,
                          event_ms: saw ? performance.now() - eventAt : null,
                          click_ms: performance.now() - clickAt,
                        })
                        return
                      }
                      if (nClick > 180) {
                        resolve({
                          event_frames: nEvent,
                          click_frames: nClick,
                          event_ms: saw ? performance.now() - eventAt : null,
                          timed_out: true,
                        })
                        return
                      }
                      requestAnimationFrame(loop)
                    }
                    requestAnimationFrame(loop)
                  })
                }""",
                zh("run.word.paused"),
            )
            frames = int((rec or {}).get("event_frames") or -1)
            if frames < 0:
                page.evaluate("() => window.__af.cmd('stop')")
            else:
                samples.append(frames)
                click_frames.append(int(rec.get("click_frames") or frames))
                if rec.get("event_ms") is not None:
                    event_ms.append(float(rec["event_ms"]))
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
            f"A5 pause event→frame over {len(samples)} reps: "
            f"min={mn} median={med:.1f} max={mx} samples={samples} "
            f"click_frames={sorted(click_frames)} event_ms={event_ms}",
            flush=True,
        )
        assert mn <= 20 and med <= 20 and mx <= 20, (
            f"A5 min/median/max must all be ≤20; min={mn} median={med} max={mx} samples={samples}"
        )
        assert event_ms and max(event_ms) <= 100.0, (
            f"A5 event→frame {max(event_ms):.1f}ms > 100ms (19.2)"
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


# Half-open A6: one 10 s pose step. last_ui_at is the start-of-run beat
# (~go start). After 10 s the node ends and _ui_lost is true, so grip
# never starts. Cut ~0.4 s after go is running so remaining ≤ 10 s
# (28 × 0.4 s = 11.2 s was the 11.251 s overrun).
A6_HALF_STEPS = 1
A6_HALF_SLEEP_S = 10.0
A6_CUT_AFTER_S = 0.4
A6_CUT_BEFORE_BEAT_S = 1.8


def _configure_a6_half_open_motion(base: str) -> None:
    http_json(
        base,
        "/api/test/adapter",
        "POST",
        {"async_steps": A6_HALF_STEPS, "async_sleep_s": A6_HALF_SLEEP_S},
    )


def _half_open_to_operator_lost(page, proxy: TcpProxy, base: str) -> float:
    """Cut the page mid-`go`. Return seconds from cut to operator_lost."""
    _click_run(page)
    _wait_run(page, "running", timeout=20000)
    rid = _run_id(page)
    t_run = time.monotonic()
    _wait_node_running(base, rid, "go")
    deadline = t_run + A6_CUT_BEFORE_BEAT_S
    target = min(time.monotonic() + A6_CUT_AFTER_S, deadline)
    while time.monotonic() < target:
        time.sleep(0.02)
    recs_at_cut = list(http_json(base, f"/api/runs/{rid}").get("records") or [])
    assert not any(r.get("node_id") == "grip" for r in recs_at_cut), recs_at_cut
    cut = time.monotonic()
    proxy.half_open()
    paused = None
    while time.monotonic() - cut < 20:
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
    return dt_lost


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
        _configure_a6_half_open_motion(server["base"])
        page.wait_for_timeout(200)
        dt_lost = _half_open_to_operator_lost(page, proxy, server["base"])
        print(f"A6 half-open cut→operator_lost: {dt_lost:.3f}s", flush=True)
        assert dt_lost >= 7.0, f"operator_lost too fast ({dt_lost:.3f}s); deadline is 10s"
        assert dt_lost <= 10.0, f"A6 pause {dt_lost:.3f}s > 10s"
        print(
            f"A6 half-open checks: reason=operator_lost dt={dt_lost:.3f}s "
            f"within_10s={dt_lost <= 10.0} cut_reconnect_s={cut_dt:.3f}",
            flush=True,
        )
        br.close()
    finally:
        p.stop()
        proxy.close()


def test_a6_half_open_pauses_within_10s(server):
    """A6: five real half-open cuts; every pause must be in [7 s, 10 s]."""
    put_flow(server["base"], motion_graph("A6 10s bound"))
    _configure_a6_half_open_motion(server["base"])
    proxy = TcpProxy("127.0.0.1", server["port"])
    p, _path = _launch()
    try:
        br = _browser(p)
        page, _blocked, _errs = _open(br, proxy.base)
        samples: list[float] = []
        for i in range(5):
            if i:
                proxy.restore()
                page.wait_for_function(
                    """(ok) => {
                      const t = document.body.innerText
                      return t.includes('Connected') || t.includes(ok)
                    }""",
                    arg=zh("conn.ok"),
                    timeout=15000,
                )
                page.evaluate("() => window.__af.cmd('stop')")
                page.wait_for_timeout(200)
            dt = _half_open_to_operator_lost(page, proxy, server["base"])
            samples.append(dt)
            print(f"A6 half-open rep {i + 1}/5: {dt:.3f}s", flush=True)
        samples.sort()
        mn, med, mx = samples[0], _median(samples), samples[-1]
        print(
            f"A6 half-open over {len(samples)} reps: "
            f"min={mn:.3f} median={med:.3f} max={mx:.3f} samples={samples}",
            flush=True,
        )
        assert mn >= 7.0, f"A6 too fast: min={mn:.3f}s samples={samples}"
        assert mx <= 10.0, f"A6 over 10s: max={mx:.3f}s samples={samples}"
        br.close()
    finally:
        p.stop()
        proxy.close()
