# Rehearsal: roboact hero

Gold: no gold tells this story. Layout is the wide-hero crop from `references/framing.md` (the `linear` example), pinned to the top-left the way the brief requires, not inset. Motion is a measured drag, as in `freeform-drag`, plus a crosshair edge drag. The product chrome is the 44px top bar, not a browser window.

Beats: idle canvas (Start → Go to pose) → drag Gripper from the palette onto the 20px grid → drag the pose port to Gripper's target port, edge after the drop → Run, nodes run one at a time, Steps counts, hold on Completed → drag Gripper back, edge and statuses leave with it. Loop 11.5s (start mark to seam mark, 11512 ms).

Captured: 1200 light contact sheet, 1200 dark contact sheet, 390 still at the hold. Reduced-motion poster is the completed graph.

Film:

- Status word width jump → fixed slot so the icon and the word box stay put.
- Steps overlapping the count widget → one catalog string, no nested counter.
- Run button padding lopsided → equal 12px padding.
- Edge bbox jump when a dragged node passes the source port → handles no longer flip sides.
- Spinner `.ico` box changes ~5px when the running glyph swaps to the check. The slot is 16px; the pulse is the rotated bounding rect. Node positions do not jump on the grid snap.
- `near-tangent` of node tops by 2–3px during the drag (in flight, 0.8s). Settled nodes share a y.
- Compact `loose stack`: the phone title and status are two lines so "Done · 0.4 s" fits. Ink gap is about 12px. Not tightened further.
- Manual content changes (edge appears, Steps number, status word, top-bar word) are the beats.

Review: layout.mjs at 1200 and 390 reported no issues (no truncated title, top bar one line). jumps.mjs after the edge fix reports only the spinner box.

Assumptions: node cards are 280px wide on the wide stage so the title and the done line share one row. The product node is 240px and would clip "Go to pose".

Unresolved: see the PR for strings the brief names that are not in `web/i18n/en.js`.

Not checked: a side-by-side still against a running product screenshot. Reduced-motion playback beyond the poster state. Sound (there is none).
