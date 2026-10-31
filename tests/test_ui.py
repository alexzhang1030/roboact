"""Playwright: 离线、断点界面、灰度可辨、500 节点性能、尺寸不因选中/缩放改变。"""

from __future__ import annotations

import urllib.parse

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


def test_offline_no_external_requests(server):
    p, path = _launch()
    try:
        br = launch_chromium(p, path)
        page, blocked, errs = _page(br, server["base"])
        page.goto(server["base"] + "/", wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        page.wait_for_timeout(400)
        assert blocked == []
        assert not [e for e in errs if "process" in e or "404" in e]
        assert page.locator(".fn").count() >= 1
        br.close()
    finally:
        p.stop()


def test_breakpoint_flow_ui(server):
    p, path = _launch()
    try:
        br = launch_chromium(p, path)
        page, blocked, _errs = _page(br, server["base"])
        page.goto(server["base"] + "/", wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        page.evaluate("() => window.__af.toggleBp('start', 'before')")
        page.locator(".ctrls button.primary").click()
        page.wait_for_function(
            "() => window.__af.run.value && window.__af.run.value.status === 'paused'",
            timeout=10000,
        )
        page.wait_for_selector('.fn[data-status="paused"]', timeout=5000)
        assert page.locator('.fn[data-status="paused"]').count() >= 1
        word = page.locator('.fn[data-status="paused"] .fn-st-word').first.inner_text()
        assert "Paused" in word or "已暂停" in word, word
        assert blocked == []
        br.close()
    finally:
        p.stop()


def test_grayscale_states_distinct(server):
    p, path = _launch()
    try:
        br = launch_chromium(p, path)
        page, blocked, _errs = _page(br, server["base"])
        page.goto(server["base"] + "/?gallery=1", wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        page.wait_for_timeout(300)
        words = page.locator(".fn-st-word").all_inner_texts()
        assert any("Done" in w or "完成" in w for w in words)
        assert any("Paused" in w or "暂停" in w for w in words)
        assert any("Error" in w or "出错" in w for w in words)
        kinds = page.eval_on_selector_all(
            ".ico", "els => els.map(e => e.getAttribute('data-kind'))"
        )
        assert set(kinds) >= {"done", "paused", "error"}
        # screenshot each icon, convert to grayscale, compare occupancy grids
        grids = []
        for kind in ("done", "paused", "error"):
            el = page.locator(f'.ico[data-kind="{kind}"]').first
            png = el.screenshot()
            grids.append(_gray_grid(png))
        assert grids[0] != grids[1]
        assert grids[0] != grids[2]
        assert grids[1] != grids[2]
        assert blocked == []
        br.close()
    finally:
        p.stop()


def _gray_grid(png: bytes):

    # Prefer PIL if present; otherwise decode PNG via a tiny parser for 8-bit
    # RGBA.
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(png)).convert("L")
        w, h = im.size
        pix = list(im.getdata())
    except Exception:
        w, h, pix = _png_gray(png)
    gw, gh = 6, 6
    grid = []
    for gy in range(gh):
        for gx in range(gw):
            xs = range(int(gx * w / gw), int((gx + 1) * w / gw) or 1)
            ys = range(int(gy * h / gh), int((gy + 1) * h / gh) or 1)
            vals = [pix[y * w + x] for y in ys for x in xs if 0 <= y * w + x < len(pix)]
            grid.append(0 if not vals else (1 if (sum(vals) / len(vals)) < 200 else 0))
    return tuple(grid)


def _png_gray(png: bytes):
    import struct
    import zlib

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
        for phase in ("pan", "ticks", "both"):
            assert r[phase]["p95Ms"] <= 33, phase + " " + str(r[phase])
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
