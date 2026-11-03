# TopoPot Next Steps

Continue this backlog from the fixes completed on 2026-10-27. `AGENTS.md` has the exact Blender commands and testing constraints.

## Completed in the latest pass

- Built-in symmetry has been removed by request. Use Blender's Mirror modifier on a separate retopology mesh instead. The N-panel header displays "TopoPot - Retopology Tool" and its manifest version.

- Screen-picking and projected hole-loop caches invalidate on matching depsgraph object/data updates, `sync()`, and undo/load; added same-count update regression coverage.
- Brush Relax reuses an edit/reference visibility snapshot for the modal stroke instead of rebuilding the edit BVH on every mouse move.
- Fill Hole reuses projected boundary-loop data while mesh and view stay unchanged.
- Draw Ctrl-preview reuses computed preview geometry while pointer, view, mesh, and settings stay unchanged.
- Edge drag refuses to merge both endpoints into one target; added regression coverage.
- Fixed-tolerance UI, smoke-test import guard, and stale README validator example were addressed.
- Alt-left-click is knife insertion/connection; Alt-left-drag over an edge is loop cut, with a freely sliding position, fast-crossing detection, and a stable source edge. Alt-delete was removed. Ctrl-drag is reserved for strips and cancels on interior/unselected edges.
- Draw and Fill Hole have separate real-event window regressions, including chained knife clicks, Alt-drag loops, previews, cancellation, and undo/redo.

## 1. Profile and reduce live-preview cost

- Profile in a real Blender window on representative small, medium, and large meshes; separate viewport drawing, hover picking, and modal preview time.
- Draw Ctrl-preview and strip-drag preview still copy the full BMesh (`preview()` / `strip_preview()`) when the preview changes. Investigate a reusable scratch mesh or local preview path without letting preview differ from commit.
- The screen index still rebuilds after a view change. Measure the one-time pause on large meshes; consider incremental rebuilding or a local candidate query if it remains noticeable.
- Reuse the GPU shader created by `draw_hover()` instead of requesting the built-in shader on each redraw.

## 2. Harden topology merges and cache behavior

- The dragged-edge merge rejects two endpoints claiming the same target. Add tests for distinct targets that would collapse an existing face, create duplicate faces, or produce a non-manifold edge; decide which targets should be rejected and ensure release is atomic.
- Verify cache invalidation with real native Edit Mode hide/unhide and undo/redo events; current regression tests exercise depsgraph updates with unchanged element counts.
- Keep cached mesh data as indices or coordinates, not BMesh wrappers; continue invalidating it through `sync()` and depsgraph updates.

## 3. Expand real-event regression coverage

- `blender_ui_smoke.py` exercises Fill Hole gestures; `blender_draw_ui_smoke.py` exercises Draw clicks, strip creation/backtracking, local drags, Alt loop cuts, previews and undo/redo. Remaining Draw event coverage includes welding, Ctrl edge split and follow-up cut, and edge endpoint merging.
- Keep performance measurements repeatable (warm and cold cache, before/after view navigation) so future optimization claims are based on the same workload.

## 4. Reduce maintenance friction

- Split `RP_OT_click`'s modal gesture dispatch into smaller gesture handlers/state objects; preserve BMesh index/count reacquisition across events.
- Replace `pick()`'s mode booleans with clearer vertex/edge/element helpers, keeping compatibility wrappers if tests or operators depend on the current signature.
- Consider splitting `topology.py` into fill, strip, and general mesh operations while retaining `topology` as the public test/import façade.

## 5. Optional functionality (confirm before starting)

- Edge slide / loop-slide and more direct loop/ring operations.
- Additional brush constraints or surface-preserving relaxation.

## Before release

- Update `README.md` and `CHANGELOG.md`, bump `topopot_retopo_tool/blender_manifest.toml` version, then run the smoke, real-event UI, reference-collection, build, and extension-validation commands in `AGENTS.md`.
- Do not include generated `__pycache__` files. The working tree currently also has untracked profiler probes (`probe_perf4.py`, `probe_view.py`); inspect/clean those deliberately before release rather than packaging them.
