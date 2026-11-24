# AGENTS.md

Blender 5.0 extension for manual retopology. No pip dependencies, no CI/lint/typecheck config — the Blender test scripts are the only verification gate.

## Layout

- `topopot_retopo_tool/__init__.py` — operators, modal gestures, overlays, preferences, tool/keymaps.
- `topopot_retopo_tool/topology.py` — pure BMesh algorithms shared by operators and tests. Keep it `bpy`-free (only `bmesh`/`mathutils`) so it can run in background Blender.
- `tests/blender_smoke.py` — the main `unittest` suite (all logic, fake contexts).
- `tests/blender_ui_smoke.py` — real-window event/undo regression for Fill Hole.
- `tests/blender_draw_ui_smoke.py` — real-window Draw, strip stability, local movement and undo regressions.
- `tests/reference_collection_smoke.py` — reference-collection operator check.
- `build.py` — packages the extension ZIP from `blender_manifest.toml`.

## Commands

`blender` is not on PATH on this workstation; use `"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"` (Blender 5.0+ is required by the manifest).

```powershell
# Main suite (must run inside Blender; pytest/plain python will not work)
& $blender --background --factory-startup --python-exit-code 1 --python tests/blender_smoke.py

# Real-event UI regression: opens a real window, do NOT use --background
& $blender --factory-startup --enable-event-simulate --python tests/blender_ui_smoke.py
# Exit code stays 0 even on failure; check zips/blender_ui_<major>_<minor>.json for "status": "passed".
& $blender --factory-startup --enable-event-simulate --python tests/blender_draw_ui_smoke.py
# Check zips/blender_draw_ui_<major>_<minor>.json for "status": "passed" as well.

# Reference collection check
& $blender --background --factory-startup --python-exit-code 1 --python tests/reference_collection_smoke.py

# Build + validate the installable extension
python build.py
& $blender --factory-startup --command extension validate zips/topopot_retopo_tool-<version>.zip
```

PowerShell gotcha: piping Blender output through `Select-String`/`Select-Object` can make the harness report a nonzero exit even when tests passed. Verify with `$LASTEXITCODE` after redirecting (`*> $null`), or check the suite output.

## Testing conventions that are easy to break

- `tests/blender_smoke.py` runs the whole `TopologyTests` class at import and `sys.exit(1)`s on failure; there is no per-test runner and pytest would execute the runner during collection.
- Tests pass `SimpleNamespace` as the operator instance and call methods unbound (`RP_OT_click.modal(stroke, context, event)`). `self` is not a real operator: call sibling helpers as `RP_OT_click.helper(self, ...)` or module-level functions — `self.helper()` breaks the suite.
- Modal drags store element **indices and mesh counts**, not `BMVert`/`BMEdge` wrappers: Blender can rebuild the edit BMesh between mouse events (tests cover this). Re-fetch elements and verify counts every event.
- Symmetry is optional and defaults off. Mirror across every enabled local zero plane. Snap to Center also works with symmetry off. Topology previews validate mirrored edits and center snapping on a copy, and modal moves retain partner indices and coordinates across events. Keep the last committed edit side for each axis in undoable mesh data, even with symmetry off. Resymmetrize validates the source region across the enabled axes before applying in one undo step. Starting a move commits pending triangles without deleting them.
- Alt cuts use `RP_OT_alt_cut`: Alt-click is knife insertion/connection; Alt-drag (six pixels) over an edge is a loop cut. Alt-hover must not change selection. Knife previews use the same `topology.apply_click()` call as commit; loop previews run `topology.loop_cut()` on a copy. Store only element indices/counts across events and lock the loop source when acquired, recalculating its cut factor from every mousemove and release position. Ctrl-drag on interior/unselected edges cancels; Alt-click deletion has been removed.
- Previews must run the same `topology.*` call on `bm.copy()` (see `preview`, `strip_preview`, `grid_preview`) so preview and commit cannot diverge.
- After mutating the edit mesh call `sync(obj)`: `destructive=False` during live drag previews, default `destructive=True` on release/commit. `sync` also invalidates `_visibility_cache` / `_grid_preview_cache`.
- Adding a custom-data layer or running bmesh ops can invalidate existing wrappers; re-fetch elements afterwards (see `topology.extend`).
- Tests patch `view3d_utils` projections (`front_view`), `sync`, and `bmesh.from_edit_mesh`; code must tolerate fake `region_data=None` contexts.
- Performance: hover picking uses `screen_index`; Fill Hole uses cached projected boundary loops. These caches store indices/coordinates, never BMesh wrappers, and must be invalidated by `sync`, undo, and relevant depsgraph updates. Brush Relax reuses its visibility BVH snapshot for the modal stroke. `navigation_changed` pauses hover work during orbit/pan/zoom.

## Wiring

- Modes live on `context.scene.retopo_tool.mode`: `RETOPO` (Draw), `BRUSH`, `BOX`, `GRID` (Fill Hole).
- Key operators: `RP_OT_click` (Draw), `RP_OT_alt_cut` (Alt knife/loop), `RP_OT_grid_fill` (Fill Hole), `RP_OT_brush`, `RP_OT_hover` (internal hover). Mouse bindings are in `RP_Tool.bl_keymap`; the Shift+Alt+Q pie menu is registered in `register()`.
- New classes must be added to the `classes` tuple; new tool bindings to `RP_Tool.bl_keymap`.
- `__init__.py` calls `importlib.reload(topology)` on import, but a running Blender keeps the addon cached: editing `topology.py` requires disabling/enabling the extension or restarting Blender. Reinstalling the same version may not reload it.
- `Reference Tolerance` is intentionally fixed at 64 px (`min=max=64`, clamped in `reference_pick_tolerance`); do not restore adjustability.
- Reference Tolerance belongs only to `RP_Preferences`, not scene settings or the N-panel. Draw's `auto_reselect` defaults to true; its disabled state must reach hover, Ctrl press, click, preview, and `topology.apply_click()` so no lower-level fallback changes the anchor.

## Release / repo quirks

- Version lives in `topopot_retopo_tool/blender_manifest.toml`; `build.py` reads it and writes `zips/topopot_retopo_tool-<version>.zip`. `zips/` is gitignored.
- Always update `CHANGELOG.md` for every version: record each fix and user-visible change under the actual manifest version in the same change. Also update `README.md` when behavior changes. Keep the user's requested version instead of silently bumping it.
- `python` must be 3.11+ (`tomllib`).
- `topopot_retopo_tool/assets/retopo_icon.dat` is required by `build.py` but is gitignored and untracked on disk — do not delete or clean it.
- `__pycache__/*.pyc` files are tracked in git even though they are generated; do not commit changes to them.


- Fill Hole defaults to Quads Only; Ctrl enables Quad/Tri. Wheel-based side-count adjustment was removed; keep wheel events available for viewport zoom. Quad/Tri uses area-aware layouts and `_equalize_fill_areas()` to adjust only new interior points while preserving the rim, reference snapping, and valid faces.
