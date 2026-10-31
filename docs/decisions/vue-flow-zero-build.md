# Vue Flow 1.48.2, zero build

**Decided:** The canvas is Vue Flow 1.48.2. The frontend is zero-build: native ES modules and an import map, six vendored files from the npm package, no bundler, no TypeScript compile step. React Flow is the fallback. LiteGraph is out.

**Why:** Alex and Steve required Python on the backend and no frontend build on a robot controller. Vue Flow is the only candidate that is DOM nodes (so Chinese status, errors, and tooltips work), ships as plain ES modules, and stayed smooth at 500 nodes. LiteGraph paints nodes on canvas, so those UI details would be custom, and its standalone repo is archived.

**Who:** Alex (zero-build / offline), Steve (no bundler), Fadell (PRD)

**Date:** 2026-10-31

**Spec:** [PRD §2 and §5](../prd.md), [UI spec header](../ui-spec.md), [flow-lib eval](../flow-lib-eval-2026-10-31.md)
