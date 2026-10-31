"""PRD v1.4.4 §8 item 19.5 — light/dark, grayscale states, offline fonts, contrast."""

from __future__ import annotations

import io
import urllib.parse
from pathlib import Path

import pytest

try:
    from PIL import Image
except ImportError:
    Image = None

from tests.conftest import launch_chromium
from tests.test_contrast_ui_spec import (
    DARK_CSS,
    LIGHT_CSS,
    NONTEXT_MIN,
    TEXT_MIN,
    contrast_ratio,
    meets,
    parse_css_color,
)
from tests.test_ui import _launch, _png_gray

pw = pytest.importorskip("playwright.sync_api")

ART = Path("/opt/cursor/artifacts")

PRD195_STATES = (
    "error",
    "paused",
    "done",
    "unknown",
    "operator_lost",
    "adapter_lost",
)

LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def _gray_pixels(png: bytes):
    if Image is not None:
        im = Image.open(io.BytesIO(png)).convert("L")
        return im.size[0], im.size[1], list(im.getdata())
    return _png_gray(png)


def _gray_means(png: bytes, gw: int = 8, gh: int = 8) -> tuple[int, ...]:
    """Downsampled grayscale means. Binary occupancy fails on dark chips
    (every cell is 'dark'), so 19.5 compares tone, not a 200-threshold mask.
    """
    w, h, pix = _gray_pixels(png)
    grid = []
    for gy in range(gh):
        for gx in range(gw):
            xs = range(int(gx * w / gw), int((gx + 1) * w / gw) or 1)
            ys = range(int(gy * h / gh), int((gy + 1) * h / gh) or 1)
            vals = [pix[y * w + x] for y in ys for x in xs if 0 <= y * w + x < len(pix)]
            grid.append(0 if not vals else round(sum(vals) / len(vals)))
    return tuple(grid)


def _grids_distinct(a: tuple[int, ...], b: tuple[int, ...], min_l1: int = 16) -> bool:
    if a == b:
        return False
    return sum(abs(x - y) for x, y in zip(a, b, strict=False)) >= min_l1


def _browser(p):
    return launch_chromium(p)


def _wired_page(context, base: str):
    blocked: list[str] = []
    seen: list[str] = []
    page = context.new_page()

    def route(r):
        url = r.request.url
        seen.append(url)
        host = urllib.parse.urlparse(url).hostname
        if host in LOCAL_HOSTS:
            r.continue_()
        else:
            blocked.append(url)
            r.abort()

    page.route("**/*", route)
    page.on("request", lambda req: seen.append(req.url))
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errs.append(m.text))
    return page, blocked, seen, errs


def _goto_ready(page, url: str, timeout: int = 20000) -> None:
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=timeout)


def _theme_of(page) -> str:
    return page.evaluate("() => document.documentElement.dataset.theme")


def _css_vars(page) -> dict[str, str]:
    raw = page.evaluate(
        """() => {
          const s = getComputedStyle(document.documentElement)
          const names = [
            'bg','grid','surface','text','muted','border','edge','blue','green','red','amber'
          ]
          const out = {}
          for (const n of names) out[n] = s.getPropertyValue('--' + n).trim()
          return out
        }"""
    )
    return {k: parse_css_color(v) for k, v in raw.items() if v}


def _external_hosts(urls: list[str]) -> list[str]:
    out = []
    for url in urls:
        host = urllib.parse.urlparse(url).hostname
        if host and host not in LOCAL_HOSTS:
            out.append(url)
    return out


def _font_urls(urls: list[str]) -> list[str]:
    found = []
    for url in urls:
        low = url.lower()
        if any(low.endswith(ext) for ext in (".woff2", ".woff", ".ttf", ".otf", ".eot")) or (
            "font" in low and "font-face" not in low
        ):
            found.append(url)
    return found


def test_prd195_prefers_dark_then_persists_light(server):
    """19.5 (a)(b): system dark → first load dark; pick 浅色 → reload still light."""
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, color_scheme="dark")
        page, blocked, seen, _errs = _wired_page(ctx, server["base"])
        page.goto(server["base"] + "/", wait_until="domcontentloaded")
        # Inline head script runs before Vue — first paint is already dark.
        assert _theme_of(page) == "dark"
        page.wait_for_function("window.__ready === true", timeout=20000)
        assert _theme_of(page) == "dark"
        stored = page.evaluate("() => localStorage.getItem('roboact.theme')")
        assert stored in (None, "system")
        assert page.evaluate("() => document.documentElement.dataset.themeMode") in ("system", "")
        assert _css_vars(page)["bg"] == DARK_CSS["bg"]
        last = page.evaluate(
            "() => document.querySelector('.top .right') && document.querySelector('.top "
            ".right').lastElementChild && document.querySelector('.top "
            ".right').lastElementChild.className"
        )
        assert "theme-wrap" in (last or "")
        page.locator('[data-testid="theme-btn"]').click()
        page.locator('[data-theme-mode="light"]').click()
        page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
        assert page.evaluate("() => localStorage.getItem('roboact.theme')") == "light"
        assert _css_vars(page)["bg"] == LIGHT_CSS["bg"]

        page.reload(wait_until="domcontentloaded")
        assert _theme_of(page) == "light"
        page.wait_for_function("window.__ready === true", timeout=20000)
        assert _theme_of(page) == "light"
        assert page.evaluate("() => localStorage.getItem('roboact.theme')") == "light"
        assert _css_vars(page)["bg"] == LIGHT_CSS["bg"]
        assert blocked == []
        assert _external_hosts(seen) == []
        br.close()
    finally:
        p.stop()


def test_prd195_grayscale_six_states_both_themes(server):
    """19.5 (c): one grayscale crop set per theme; six states stay distinct."""
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
        page, blocked, seen, _errs = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/?gallery=1")
        ART.mkdir(parents=True, exist_ok=True)
        for theme in ("light", "dark"):
            page.evaluate(
                """(theme) => {
                  if (window.__af && window.__af.setThemeMode) window.__af.setThemeMode(theme)
                  else {
                    document.documentElement.dataset.theme = theme
                    localStorage.setItem('roboact.theme', theme)
                  }
                }""",
                theme,
            )
            page.wait_for_timeout(80)
            assert _theme_of(page) == theme
            strip = page.locator('[data-testid="prd195-states"]')
            strip.wait_for()
            png = strip.screenshot()
            gray_path = ART / f"prd195-states-{theme}.png"
            if Image is not None:
                Image.open(io.BytesIO(png)).convert("L").save(gray_path)
            else:
                gray_path.write_bytes(png)

            words = page.locator(".gstate-word").all_inner_texts()
            assert any(w == "Error" for w in words)
            assert any(w == "Paused · breakpoint" for w in words)
            assert any(w == "Done" for w in words)
            assert any(w == "Interrupted · result unknown" for w in words)
            assert any(w == "Paused · page disconnected" for w in words)
            assert any(w == "Paused · robot controller lost" for w in words)

            grids = {}
            for state in PRD195_STATES:
                chip = page.locator(f'.gstate[data-state="{state}"]')
                assert chip.count() == 1, state
                grids[state] = _gray_means(chip.screenshot())
            keys = list(grids)
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    assert _grids_distinct(grids[a], grids[b]), f"{theme}: {a} vs {
                        b
                    } grayscale means too close {grids[a]} / {grids[b]}"

            # Canvas node icons for the four node-level states.
            node_kinds = ("done", "paused", "error", "unknown")
            node_grids = {}
            for kind in node_kinds:
                el = page.locator(f'.fn[data-status="{kind}"] .ico').first
                node_grids[kind] = _gray_means(el.screenshot())
            nk = list(node_grids)
            for i, a in enumerate(nk):
                for b in nk[i + 1 :]:
                    assert _grids_distinct(node_grids[a], node_grids[b], min_l1=8), f"{theme} node {
                        a
                    } vs {b} {node_grids[a]} / {node_grids[b]}"
        assert blocked == []
        assert _external_hosts(seen) == []
        br.close()
    finally:
        p.stop()


def test_prd195_offline_fonts_both_themes(server):
    """19.5: network blocked, fonts still render, no font request leaves localhost."""
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light")
        page, blocked, seen, _errs = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        for theme in ("light", "dark"):
            page.evaluate(
                """(theme) => {
                  if (window.__af && window.__af.setThemeMode) window.__af.setThemeMode(theme)
                  else {
                    document.documentElement.dataset.theme = theme
                    localStorage.setItem('roboact.theme', theme)
                  }
                }""",
                theme,
            )
            page.wait_for_timeout(60)
            info = page.evaluate(
                """async () => {
                  await document.fonts.ready
                  const ui = getComputedStyle(document.body).fontFamily
                  const monoEl = document.querySelector('.fn-st-word, .top .ver, .top .right')
                  const mono = monoEl ? getComputedStyle(monoEl).fontFamily : ''
                  const check = document.fonts.check('12px "JetBrains Mono"')
                  const families = [...document.fonts].map(f => f.family)
                  const width = monoEl ? monoEl.getBoundingClientRect().width : 0
                  const uiWidth = document.body.getBoundingClientRect().width
                  return { ui, mono, check, families, width, uiWidth }
                }"""
            )
            assert "Inter" in info["ui"], info
            assert "JetBrains Mono" in info["mono"], info
            assert info["check"] or any("JetBrains Mono" in f for f in info["families"]), info
            assert page.evaluate("() => document.fonts.check('13px Inter')") or any(
                "Inter" in f for f in info["families"]
            )
            assert info["width"] > 0, info
            assert info["uiWidth"] > 0, info
        fonts = _font_urls(seen)
        assert fonts, "expected local Inter / JetBrains Mono requests"
        for url in fonts:
            host = urllib.parse.urlparse(url).hostname
            assert host in LOCAL_HOSTS, url
            assert "/static/fonts/" in url, url
            assert any(n in url for n in ("InterVariable", "JetBrainsMono"))
        assert blocked == []
        assert _external_hosts(seen) == []
        br.close()
    finally:
        p.stop()


def test_prd195_follow_system_tracks_prefers(server):
    """§17: 跟随系统 listens to prefers-color-scheme."""
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, color_scheme="dark")
        page, blocked, _seen, _errs = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        page.evaluate("() => window.__af.setThemeMode('system')")
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
        page.emulate_media(color_scheme="light")
        page.wait_for_function(
            "() => document.documentElement.dataset.theme === 'light'", timeout=5000
        )
        assert page.evaluate("() => localStorage.getItem('roboact.theme')") == "system"
        page.emulate_media(color_scheme="dark")
        page.wait_for_function(
            "() => document.documentElement.dataset.theme === 'dark'", timeout=5000
        )
        assert blocked == []
        br.close()
    finally:
        p.stop()


def test_prd195_live_contrast_operator_adapter_disabled_edge_grid(server):
    """19.5 (e): live computed contrast for amber/red, disabled buttons, edges, grid."""
    p, _path = _launch()
    try:
        br = _browser(p)
        ctx = br.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light")
        page, blocked, seen, _errs = _wired_page(ctx, server["base"])
        _goto_ready(page, server["base"] + "/")
        for theme, expected in (("light", LIGHT_CSS), ("dark", DARK_CSS)):
            page.evaluate(
                """(theme) => {
                  if (window.__af && window.__af.setThemeMode) window.__af.setThemeMode(theme)
                  else {
                    document.documentElement.dataset.theme = theme
                    localStorage.setItem('roboact.theme', theme)
                  }
                }""",
                theme,
            )
            page.wait_for_timeout(60)
            data = page.evaluate(
                """() => {
                  const vars = {}
                  const s = getComputedStyle(document.documentElement)
                  const names = ['bg','grid','surface','text','muted','border','edge','red','amber']
                  for (const n of names) {
                    vars[n] = s.getPropertyValue('--' + n).trim()
                  }
                  const disabled = document.querySelector('button:disabled')
                  const dcs = disabled ? getComputedStyle(disabled) : null
                  const primary = document.querySelector('button.primary')
                  if (primary) primary.disabled = true
                  const pcs = primary ? getComputedStyle(primary) : null
                  const canvas = document.querySelector('.vue-flow')
                  const ccs = canvas ? getComputedStyle(canvas) : null
                  return {
                    vars,
                    disabled: dcs
                      ? { color: dcs.color, bg: dcs.backgroundColor, opacity: dcs.opacity }
                      : null,
                    primaryDisabled: pcs
                      ? { color: pcs.color, bg: pcs.backgroundColor, opacity: pcs.opacity }
                      : null,
                    canvasBg: ccs ? ccs.backgroundColor : '',
                    canvasImage: ccs ? ccs.backgroundImage : '',
                  }
                }"""
            )
            palette = {k: parse_css_color(v) for k, v in data["vars"].items() if v}
            for key in ("bg", "grid", "surface", "muted", "edge", "red", "amber"):
                assert palette[key] == expected[key], f"{theme} --{key}: live {
                    palette[key]
                } expected {expected[key]}"

            for bg in ("bg", "surface"):
                assert meets(contrast_ratio(palette["amber"], palette[bg]), TEXT_MIN)
                assert meets(contrast_ratio(palette["red"], palette[bg]), TEXT_MIN)
                assert meets(contrast_ratio(palette["edge"], palette[bg]), NONTEXT_MIN)
                assert meets(contrast_ratio(palette["muted"], palette[bg]), TEXT_MIN)
            assert contrast_ratio(palette["grid"], palette["bg"]) >= 1.05

            assert data["disabled"], "no disabled button on the idle canvas"
            d_fg = parse_css_color(data["disabled"]["color"])
            d_bg = parse_css_color(data["disabled"]["bg"])
            assert float(data["disabled"]["opacity"]) == 1.0
            assert meets(contrast_ratio(d_fg, d_bg), TEXT_MIN), f"{theme} disabled {d_fg} on {d_bg}"

            assert data["primaryDisabled"]
            p_fg = parse_css_color(data["primaryDisabled"]["color"])
            p_bg = parse_css_color(data["primaryDisabled"]["bg"])
            assert float(data["primaryDisabled"]["opacity"]) == 1.0
            assert meets(contrast_ratio(p_fg, p_bg), TEXT_MIN), (
                f"{theme} primary disabled {p_fg} on {p_bg}"
            )

            assert "radial-gradient" in data["canvasImage"]
        assert blocked == []
        assert _external_hosts(seen) == []
        br.close()
    finally:
        p.stop()
