# Fonts: Inter, JetBrains Mono, CJK system

**Decided:** Latin UI and digits use Inter (variable 100–900). Code, parameter values, times, and step counts use JetBrains Mono Regular 400 and Medium 500–700 with tabular numbers. Both families ship in the package (`roboact/static/fonts/`), SIL OFL 1.1, and are listed in `vendor-manifest.json`. CJK is not bundled; the stack is PingFang SC, Microsoft YaHei, Noto Sans CJK SC, Noto Sans SC, sans-serif. `@font-face` uses `font-display: block` and local `/static/fonts/` URLs only. Type scale is 16 / 14 / 13 / 12 / 11.

**Why:** Offline on a robot controller: no CDN. CJK files are large; the host OS already has them. Inter and JetBrains Mono cover Latin UI and aligned numbers. Rams locked the stack in ui-spec §17.

**Who:** Rams (spec), Alex (light/dark + fonts request)

**Date:** 2026-10-31

**Spec:** [UI spec §17.1 / §18.3](../ui-spec.md), [PRD §5 interface](../prd.md)
