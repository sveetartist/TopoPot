# Changelog

## 1.2.0

### Added & Improved

- Added Auto Reselect to Draw settings, enabled by default. Turning it off disables automatic anchor changes during Ctrl press, hover, clicking, and previews.
- Moved Reference Tolerance to add-on Preferences only; removed its scene setting and N-panel display. It remains fixed at 64 px.
- Ctrl Draw hover and clicking now share the same border-reselection rules. Invalid vertex hits no longer block reselection near corners, while valid connection/bridge targets, pending triangles, and selected cut vertices retain their roles.
- Border reselection now favors the closest candidate with a 20% improvement margin to prevent near-tie flicker. Pressing Ctrl over an interior edge no longer selects a nearby border instead.
- Alt-drag loop cuts now slide continuously with the mouse and commit at the release position. The source edge stays fixed while placement remains freely adjustable, including after edit-mesh rebuilds.
- Alt-left-click is now an edge/vertex knife: it inserts a point or connects from the previous selected endpoint. Alt-left-drag over an edge creates a loop cut. A six-pixel threshold keeps small click jitter from becoming a loop, and dragging never inserts a knife point first. Alt-click deletion remains removed.
- Alt-hover previews knife points/cut lines without reselection. Loop drags keep their source edge while the cut follows the mouse, detect fast crossings between mouse events, tolerate Blender rebuilding the edit mesh, and cancel safely if topology changes. Knife and loop previews run their actual topology operations on a BMesh copy before commit.
- Ctrl-drag remains available for quad strips from the selected boundary/loose edge. Ctrl-drag on other edges now cancels without creating a loop or an accidental edge split.
- Removed built-in symmetry completely: settings, Draw/strip/loop/Brush mirroring, counterpart generation and matching, seam welding, and mirrored previews. Use Blender's Mirror modifier on a separate retopology mesh when needed.
- Renamed the extension and tool to "TopoPot - Retopology Tool". The N-panel header includes the installed version, read from the manifest (1.2.0).
- Real-window Draw regressions cover clicks, stable strip rows, backtracking, preview/commit agreement, local vertex/edge movement, cancellation, and undo/redo.
- Starting a vertex or edge move, or switching tool modes, now commits pending triangles without discarding their faces when Quad Mode is enabled.
- Retopology View is enabled by default when the add-on starts, the tool is activated, or its mode is changed. The sidebar toggle remains available.
- Quad strip dragging keeps completed rows at fixed distances along the stroke. Only the unfinished tip stretches and splits; growing or trimming the strip no longer redistributes all previous faces.
- Draw Tool: Ctrl + drag backwards now removes newly drawn faces during strip drawing, including when dragging back over the strip itself rather than its centerline.
- Ctrl + drag: Quad strips automatically snap and connect to existing topology, including along the sides.
  Welds happen within Merge Radius or three quarters of a face width, whichever is farther.
- Ctrl + drag from a single loose edge (starting new topology) now begins with a pending triangle at the cursor, completed into a quad by the next click, matching the click workflow.
- Edge dragging in Draw and Fill Hole now snaps and merges either endpoint into another vertex within Merge Radius.
- Draw Tool: Ctrl-clicking a vertex selects the closest full edge at that vertex instead of leaving a lone vertex selected. Creating the first edge of new topology also selects the whole edge, so the next click builds a triangle instead of chaining more vertices.
- Draw Tool: Pressing Ctrl selects the closest visible border (boundary or loose) edge under the cursor as the starting edge, so the next click draws a triangle from it; interior edges are ignored. It only switches on the Ctrl press, so a vertex inserted by an edge split stays selected for cutting.
- Draw Tool: Ctrl-clicking the selected edge inserts a vertex at the cursor position and selects it; Ctrl-clicking another vertex or edge then cuts through the topology from it.
- Draw Tool: long quad strips and large meshes stay responsive. Picking uses a cached screen-space index, weld targets are spatially indexed, stroke resampling is incremental, and the strip preview is drawn in two batched calls. Hover work pauses while the view is orbiting, panning, or zooming so navigation keeps its frame rate.
- Fixed screen and hole-preview cache invalidation after external mesh/depsgraph updates; reused projected hole boundaries between mouse moves.
- Brush Relax reuses a per-stroke visibility snapshot instead of rebuilding the edit BVH on every mouse move.
- Dragged edge endpoints no longer both weld into one target and collapse the moved edge.
- Fixed-tolerance sidebar control now states 64 px without presenting a non-adjustable slider.
- Blender smoke tests can now be imported without running the full suite, enabling focused single-test runs.
- Reference Tolerance is fixed at 64 screen pixels (minimum and maximum), including for older scenes with a lower stored value.
- Removed Ctrl-drag from vertices: it no longer starts a strip or a vertex-to-vertex connection.
- Ctrl + Draw Mode: Hovering backwards automatically switches selection to the closest boundary edge, removing the need to preselect it manually.

## 1.1.0

### Added

- Quad strip drawing in Draw mode. Ctrl-drag from a selected boundary or loose edge to draw a strip of quads along the stroke.
- Fill Hole mode with Quads Only and Quad/Tri options, a live preview, and reference surface snapping.
- Move Selection to Reference button. In Object Mode, it moves selected meshes into the chosen reference collection, or creates and assigns one when needed.

## 1.1.1

Quick name fix.

## 1.1.2

Quick name fix.
