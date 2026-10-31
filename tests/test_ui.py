"""PRD 19 canvas performance and 19.3 node-size stability."""

from __future__ import annotations

import os
import struct
import sys
import urllib.parse
import zlib

import pytest

from tests.conftest import chrome_path, launch_chromium

pw = pytest.importorskip("playwright.sync_api")


def _launch():
    path = chrome_path()
    if path is None:
        # bundled chromium from `playwright install` is fine
        path = None
    try:
        p = pw.sync_playwright().start()
        return p, path
    except Exception as e:
        pytest.skip("playwright/chromium not available: " + str(e))


def _page(browser, base):
    blocked = []
    page = browser.new_page(viewport={"width": 1280, "height": 800})

    def route(r):
        host = urllib.parse.urlparse(r.request.url).hostname
        if host in ("127.0.0.1", "localhost"):
            r.continue_()
        else:
            blocked.append(r.request.url)
            r.abort()

    page.route("**/*", route)
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errs.append(m.text))
    return page, blocked, errs


def _png_gray(png: bytes):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    i = 8
    w = h = 0
    dat = b""
    while i < len(png):
        ln = struct.unpack(">I", png[i : i + 4])[0]
        typ = png[i + 4 : i + 8]
        chunk = png[i + 8 : i + 8 + ln]
        i += 12 + ln
        if typ == b"IHDR":
            w, h = struct.unpack(">II", chunk[:8])
        elif typ == b"IDAT":
            dat += chunk
        elif typ == b"IEND":
            break
    raw = zlib.decompress(dat)
    # filter 0 assumed per row for screenshots
    rows = []
    stride = w * 4 + 1
    prev = bytearray(w * 4)
    for y in range(h):
        row = bytearray(raw[y * stride + 1 : (y + 1) * stride])
        # Paeth/sub etc. Chrome usually filter 1 or 0. Reconstruct filter
        # 0/1/2/4.
        filt = raw[y * stride]
        out = bytearray(w * 4)
        for x in range(w * 4):
            a = out[x - 4] if x >= 4 else 0
            b = prev[x]
            c = prev[x - 4] if x >= 4 else 0
            v = row[x]
            if filt == 0:
                out[x] = v
            elif filt == 1:
                out[x] = (v + a) & 255
            elif filt == 2:
                out[x] = (v + b) & 255
            elif filt == 3:
                out[x] = (v + ((a + b) // 2)) & 255
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                out[x] = (v + pr) & 255
            else:
                out[x] = v
        prev = out
        rows.append(out)
    pix = []
    for row in rows:
        for x in range(w):
            r, g, b = row[x * 4], row[x * 4 + 1], row[x * 4 + 2]
            pix.append((r * 3 + g * 6 + b) // 10)
    return w, h, pix


def test_prd19_canvas_performance_500_nodes(server):
    p, path = _launch()
    try:
        br = launch_chromium(p, path)
        page, blocked, _errs = _page(br, server["base"])
        page.goto(server["base"] + "/?n=500", wait_until="domcontentloaded")
        page.wait_for_function("window.__result", timeout=90000)
        r = page.evaluate("window.__result")
        assert r["domNodes"] == 500
        recorded = {
            phase: {k: r[phase][k] for k in ("frames", "medianMs", "p95Ms", "maxMs")}
            for phase in ("pan", "ticks", "both")
        }
        if os.environ.get("CI"):
            print(
                "PRD19 canvas perf (CI, record only):",
                recorded,
                file=sys.__stdout__,
                flush=True,
            )
        else:
            limit_ms = 2 * 1000 / 60 + 0.5
            for phase in ("pan", "ticks", "both"):
                assert r[phase]["p95Ms"] <= limit_ms, phase + " " + str(r[phase])
            long_after = [t for t in r["longTasks"] if t["ms"] > 100]
            assert long_after == [], long_after
        assert blocked == []
        br.close()
    finally:
        p.stop()


def test_zoom_and_select_do_not_remeasure(server):
    p, path = _launch()
    try:
        br = launch_chromium(p, path)
        page, blocked, _errs = _page(br, server["base"])
        page.goto(server["base"] + "/?n=80&idle=1", wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=30000)
        page.wait_for_timeout(500)
        page.evaluate("window.__dim = 0")
        for z in (0.5, 0.7, 0.45, 0.8, 0.4, 0.9):
            page.evaluate("z => window.__af.vf.setViewport({x:0,y:0,zoom:z})", z)
            page.wait_for_timeout(80)
        page.evaluate("""() => {
          document.querySelectorAll('.fn').forEach((el, i) => {
            if (i < 6) el.classList.toggle('sel-test')
          })
        }""")
        page.add_style_tag(content=".fn.sel-test{outline:2px solid #1F6FEB;outline-offset:2px}")
        for _ in range(6):
            page.evaluate(
                "() => document.querySelectorAll('.fn').forEach((el,i)=>{ if(i<6) "
                "el.classList.toggle('sel-test') })"
            )
            page.wait_for_timeout(50)
        dim = page.evaluate("window.__dim")
        assert dim == 0
        assert blocked == []
        br.close()
    finally:
        p.stop()
