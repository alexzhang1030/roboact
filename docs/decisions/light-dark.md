# Light and dark

**Decided:** Two themes, CSS variables only. First load follows `prefers-color-scheme`. The top-bar appearance control is follow system (default), light, or dark, stored as `roboact.theme`. An inline head script sets `data-theme` before CSS so dark does not flash white. Follow-system listens to the media query; a second tab follows via `storage`. Status meanings do not change with theme (running blue, pause/unknown amber, error/controller-lost/REAL red, done green). Dark uses no shadows.

**Why:** Alex asked for light/dark. One stylesheet and token swap keeps contrast and status shapes identical. Rams owns the token tables in §17.

**Who:** Alex, Rams

**Date:** 2026-10-31

**Spec:** [PRD §5 / 19.5](../prd.md), [UI spec §17](../ui-spec.md)
