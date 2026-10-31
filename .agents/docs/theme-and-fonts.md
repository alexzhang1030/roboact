# Theme and fonts

**Current conclusion.** ui-spec **§17** is the font and theme spec. It overrides §10/§11 where they disagree.

Latin UI text and digits use Inter (variable 100–900). Code, parameter values, times, and step counts use JetBrains Mono Regular 400 + Medium 500–700 with `font-variant-numeric: tabular-nums`. CJK is not bundled; fallback is PingFang SC, Microsoft YaHei, Noto Sans CJK SC, Noto Sans SC, sans-serif.

## Where the files live

Three woff2 files plus their OFL texts ship **inside the Python package** so `python -m roboact` serves them without a CDN:

- `roboact/static/fonts/InterVariable.woff2`
- `roboact/static/fonts/JetBrainsMono-Regular.woff2`
- `roboact/static/fonts/JetBrainsMono-Medium.woff2`

Each row is in `vendor-manifest.json` with sha256 and `OFL-1.1`. Licence copies sit next to the fonts and under `vendor/licenses/`. `@font-face` uses `font-display: block` and local `/static/fonts/` URLs only.

Type scale is exactly 16 / 14 / 13 / 12 / 11 (CSS `--fs-*`).

## Appearance

The control is the far-right top-bar button (half-filled circle). Three choices: follow system (default), light, dark. Stored as `roboact.theme`. An inline `<head>` script sets `data-theme` before CSS so dark does not flash white. Follow-system listens to `prefers-color-scheme`. A second tab follows via the `storage` event.

Themes swap **CSS variables only**. Status meanings stay the same (running blue, pause/unknown amber, error/controller-lost/REAL red, done green). Dark uses no shadows; layering is 1px borders and surface steps.

`--control` is the button/input border. Contrast tests import `docs/design/contrast.py` and assert the §17.4 table (grid dots decorative, skipped).

## Reduced motion (ui-spec §18.1)

When `prefers-reduced-motion: reduce` is on (or `roboact.motion=reduce`), running icons do not spin, edges do not flow, and status transitions do not animate. Running is shown by colour and the Running word only.

## Rejected

- Bundling CJK fonts.
- A token-gate screen. `r-token` sheets in `docs/design/` are historical; do not implement them (see [auth-and-network.md](auth-and-network.md)).
- Restyling in a loop to match mock chrome (logo, crumb, example graph). Report mismatches; do not chase them.

## Who / date / spec

Rams, Steve, 2026-10-31. [UI spec §17 / §18](../../docs/ui-spec.md), [PRD 19.5 / 19.7](../../docs/prd.md).
