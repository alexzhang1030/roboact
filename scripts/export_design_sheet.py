#!/usr/bin/env python3
"""One-shot PNG export of docs/design-sheet.html → docs/design/*.png."""

from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "design"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(ROOT), **k)

        def log_message(self, fmt, *args):
            return

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1720, "height": 960})
            page.goto(
                f"http://127.0.0.1:{port}/docs/design-sheet.html", wait_until="domcontentloaded"
            )
            page.wait_for_function("window.__sheetReady === true", timeout=30000)
            page.wait_for_timeout(800)
            shots = page.query_selector_all("[data-shot]")
            if not shots:
                raise SystemExit("no [data-shot] elements")
            for el in shots:
                name = el.get_attribute("data-shot")
                el.screenshot(path=str(OUT / f"{name}.png"), animations="disabled")
                print("wrote", OUT / f"{name}.png")
            browser.close()
        httpd.shutdown()
    print(f"ok: {len(list(OUT.glob('*.png')))} pngs in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
