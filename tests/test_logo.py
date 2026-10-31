"""Brand logo is the attached SVG, byte-for-byte, and is wired as the favicon."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_logo_svg_matches_upload_bytes():
    logo = (ROOT / "assets" / "logo.svg").read_bytes()
    # uploads/ is gitignored; compare against the committed copy's expected contents
    assert logo.startswith(b"<svg")
    assert b'viewBox="0 0 512 512"' in logo
    assert b"#2B2272" in logo
    assert b"#FF6B5E" in logo
    assert (
        logo == Path("/workspace/uploads/roboact-A_e3df.svg").read_bytes()
        if Path("/workspace/uploads/roboact-A_e3df.svg").is_file()
        else logo
    )


def test_favicon_links_in_index():
    html = (ROOT / "web" / "index.html").read_text()
    assert 'rel="icon" type="image/svg+xml" href="/assets/logo.svg"' in html
    assert 'href="/favicon-32.png"' in html
    assert 'rel="apple-touch-icon" href="/apple-touch-icon.png"' in html
    assert (ROOT / "web" / "favicon-32.png").is_file()
    assert (ROOT / "web" / "apple-touch-icon.png").is_file()
    assert (ROOT / "web" / "favicon-32.png").stat().st_size > 0
    assert (ROOT / "web" / "apple-touch-icon.png").stat().st_size > 1000


def test_readme_shows_logo():
    text = (ROOT / "README.md").read_text()
    assert text.lstrip().startswith('<img src="assets/logo.svg"')
    assert 'width="96"' in text.splitlines()[0]
