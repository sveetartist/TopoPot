# TopoPot - Retopology Tool

Minimal retopology add-on for Blender 5.0.

## Install

Requires Blender 5.0 or newer. Open Edit > Preferences > Get Extensions,
use the menu's Install from Disk, and select the provided ZIP file. Enable TopoPot.
When migrating from the legacy add-on, disable and remove the old TopoPot add-on
first, then restart Blender and install the extension to avoid duplicate tools.

## Package

Run `python build.py` with Python 3.11 or newer. The extension ZIP is written to
`zips/topopot_retopo_tool-<version>.zip`, with `blender_manifest.toml` and
`__init__.py` at its root alongside the code, icon assets, and this README.
Edit `topopot_retopo_tool/blender_manifest.toml` to update the version or metadata.

Validate the resulting archive using Blender's official validator:

```sh
blender --factory-startup --command extension validate zips/topopot_retopo_tool-<version>.zip
```

## License

GPL-3.0-or-later (GNU General Public License version 3 or any later version).

## Use

1. Select the mesh you want to edit in Object Mode. Open the 3D View sidebar
   with N, choose TopoPot, and press Activate Draw Tool. The panel header shows
   **TopoPot - Retopology Tool** and the installed version. The selected mesh
   enters Edit Mode. With no mesh selected, activation creates an empty mesh.
   Set Reference to a collection when you want projection onto its meshes.
   In Object Mode, select reference meshes and click Move Selection to Reference
   to move them into a reference collection and assign it to Reference.
2. Ctrl-click empty space (or the Reference surface) to place the first vertex.
   Ctrl-click again to create the first edge; it is selected as a full edge, so
   the following Ctrl-clicks build triangles and quads from it instead of
   chaining more lone vertices. Pressing Ctrl anchors the closest visible border
   (boundary or loose) edge under the cursor as the starting edge, ready for a
   triangle; interior edges are ignored. Ctrl-clicking an existing vertex also
   selects the closest full edge at that vertex.
3. From a selected edge, Ctrl-click a third corner to create a triangle.
   Ctrl-click a fourth corner to replace that triangle with a quad. The new
   outer edge becomes selected, ready for the next triangle/quad pair.
   While holding Ctrl, hovering backwards behind the active boundary edge
   automatically switches the selection to the closest boundary edge of the piece.
4. To keep a triangle, left-click its next starting edge. A plain selection
   commits the triangle and resets the pending quad.
5. Ctrl-click another boundary edge to bridge it to the selected edge. Two
   selected vertices joined by an edge also count as the starting edge. Edges
   with a shared endpoint form a triangle; separate edges form a quad.
  Finish or commit a pending triangle before bridging.
6. Ctrl-click the selected edge itself to insert a vertex at the clicked
   position and select it. With that vertex selected, Ctrl-click another vertex
   or edge to cut through the topology from it. Moving with Ctrl held keeps the
   inserted vertex selected so the cut can continue; press Ctrl again to switch
   a single vertex to its closest full edge for drawing faces.

## Drawing Face Strips

Ctrl Draw uses the same border-reselection rule during hover and clicking.
**Auto Reselect** is enabled by default in Draw settings. Disable it to keep your
manually selected starting edge during Ctrl press, hover, clicking, and previews.
Moving behind or alongside the working border switches only to a clearly closer
boundary edge (at least 20% closer), keeping selection stable near equal distances.
Valid vertex connections and edge bridges remain targets rather than causing
reselection. Pending triangles and selected cut vertices keep their current anchor.
Pressing Ctrl over an interior edge does not select a nearby border.

In Draw mode, select a loose or boundary edge, then hold Ctrl and left-drag
from the middle of that edge or from empty space to draw a strip of quads.
The strip follows the dragged path, turns with the stroke, and uses the starting
edge's width for face spacing. Completed rows stay fixed along the path as you
drag; only the unfinished tip follows the cursor. A new row appears once there
is room for a face at least 60% of the starting edge width, so the tip can stretch
before splitting without redistributing all the earlier quads. Backtracking
shortens the tip and removes rows from the end. This works with Quad Mode enabled or disabled.
Keep Ctrl held and move at least 16 screen pixels while holding the left mouse
button to start drawing. Smaller movements keep the normal Ctrl-click action;
releasing Ctrl before drawing starts keeps that stroke as a click.
With Snapping enabled and a Reference collection set, new corners project onto
the reference surface with Offset. The starting edge stays in place.

An orange face preview and a "Drawing strip" message in the viewport header
indicate that strip drawing is active. Dragging backwards over the drawn strip
removes the last drawn faces so you can adjust strip length before releasing;
trimming works from anywhere above the strip, not just along its centerline.
With Snapping enabled, strips automatically snap and connect to nearby boundary
vertices along the sides as well as at the end. A corner welds when it is within
**Merge Radius** (the same screen distance used when dragging vertices) or within
the strip's own reach of three quarters of a face width, whichever is farther, so
strokes drawn alongside existing topology join it without exact aiming. Corners
farther than both limits stay separate. Hover another
visible loose or boundary edge within Merge Radius to connect: the preview and
destination edge turn green, and the header shows "Connect strip to edge". Release
over that edge to join the strip using its existing vertices. Move away to continue
free drawing. Connections to interior edges, crossed faces, and incompatible
winding are rejected; existing vertices and face normals stay in place. Adjacent
edges can connect with a triangle.

Starting new topology from a single loose edge behaves like the click workflow:
a short stroke first creates a pending triangle at the cursor — the first face
after that edge — and the next Ctrl-click completes it into a quad. Dragging
farther grows the strip as usual quads instead.

Release the left mouse button to create the
strip in one undo step; its final outer edge becomes selected for the next stroke.
Press Esc or right-click to cancel without changing the mesh. Invalid or folded
strips are rejected. A Ctrl-click without dragging keeps the existing click action.
Alt-click inserts/connects knife points. Alt-drag over an edge cuts through its
connected quad strip. Ctrl-dragging other edges cancels without cutting.

## Fill Hole

Open the Shift-Alt-Q pie menu and choose Fill Hole, which replaces the old
Preserve Borders pie entry. Point inside a visible hole to preview the filled
area in cyan; no selection is required. The viewport header shows the fill mode
and the quad/triangle counts. Left-click inside the hole to apply the preview in one undo step.
Fill Hole stays active for the next hole. Choose Fill Hole again in the pie, choose another
mode, or press Esc/right-click to return to Draw.

Drag a visible vertex while Fill Hole is active to reshape the rim toward a
rectangle before filling. Release to keep the move in one undo step; Esc or
right-click during the drag restores its original position and keeps Fill Hole
active. A click or small mouse jitter on a vertex leaves the hole unfilled.
Dragging follows Reference with Snapping enabled; without Reference it moves
at the vertex's view depth. **Merge on Drag** is enabled by default in the Fill
Hole sidebar and tool settings. Drag the cursor onto another vertex within
**Merge Radius** to snap onto it, including when grabbing off the vertex center.
The target is highlighted; release to weld into that vertex in one undo step.
Canceling restores the source without merging. This option works independently
of Reference Snapping. Disable it to preserve side counts during reshaping.
The fill preview and side counts refresh after mesh changes.

Drag the middle of an edge to move both endpoints together. While **Merge on
Drag** is enabled, either endpoint within **Merge Radius** of another visible
vertex snaps onto it, and release welds the snapping endpoints in one undo step.
Release away from merge targets keeps the move. Esc/right-click restores both
endpoints without merging. Click an edge without dragging
to collapse its endpoints into one vertex at the midpoint. Ctrl-click an edge
to insert a vertex at the clicked position and connect it to the closest visible
vertex that can form a triangle. The connection splits a neighboring face or
adds a triangle into the hole. Each gesture is one undo step and keeps Fill Hole
active. Clicking near an endpoint picks that vertex; use the middle of the edge
for edge gestures.

Quads Only is the default and requires an even number of rim vertices, at least
four. Hold Ctrl to temporarily switch to Quad/Tri; releasing Ctrl immediately
restores Quads Only, including when the mouse is stationary. Hold Ctrl while
clicking to apply the mixed preview. This is independent of Draw's Quad Mode.
In Quads Only, numbers beside the rim show each side's vertex count, including
both corner vertices. The four corners are detected from the sharpest rim turns.
Counts remain visible on invalid fills so you can subdivide or dissolve rim
vertices to match opposite sides, then hover the hole again to fill it with quads.
If the first grid folds, Fill Hole tries alternative corners and simpler
interpolation, then a valid all-quad partition for irregular holes. If those
cannot form an all-quad layout, the header suggests holding Ctrl for Quad/Tri.

Quad/Tri builds square interior cells sized from the typical rim edge length.
A transition band of quads and triangles connects the grid to every rim edge,
including when opposite sides have unequal counts or the total count is odd.
Regular grids with near-square cells are reused directly. Concave notches stay
outside the fill; narrow areas that cannot fit square cells use a boundary-based
quad/triangle layout. Triangles handle the boundary transitions instead of
stretching long faces across the hole. Existing rim vertices stay in place.
Reference snapping can deform the square cells to follow a curved surface.
Boundary connections favor nearby grid vertices and straight grid rows, with a
horizontal preference in the grid's own orientation when distances are similar.
Long diagonal fans are adjusted before triangles are paired into quads.

The fill reuses the hole's existing rim vertices and follows the surrounding
topology. With Snapping enabled and Reference set, new interior vertices project
onto the reference surface with Offset. The rim and existing geometry stay in
place. Without Reference, the fill follows the hole boundary.

Supported holes have a closed, unbranched, visible boundary with at least three
vertices in Quad/Tri mode. The preview rejects hidden or back-facing rims,
covered surfaces, and fills that would fold or create non-manifold geometry.
An unsupported fill shows a red boundary and an explanation in the header.
Preserve Borders remains available in the brush sidebar and tool settings.

## Cuts and Subdivision

Choose Draw, Brush, Box Select, or Fill Hole in the TopoPot sidebar. Box Select activates
Blender's native selection tool, including its normal modifier and X-Ray behavior.
Choose Draw or Brush in the sidebar to return from box selection.
Shift-Alt-Q opens the Draw Tool Modes pie menu in Mesh Edit Mode, including while
Box Select is active. Change its shortcut in Preferences > Add-ons > TopoPot - Retopology Tool.
Enable Preserve Borders in the brush sidebar or tool settings to relax only
interior surface vertices, leaving open borders and loose geometry fixed.
Enable Quad Mode in Draw mode to extrude quads while allowing triangle
connections between neighboring edges that share a vertex.
Each click away from the active edge extrudes a complete quad, with the new edge
centered at the cursor and selected for the next extrusion. Clicking an existing
vertex snaps one new corner to it; clicking another boundary edge bridges to it.
New corners also snap automatically to existing vertices within four times Merge Radius.

TopoPot edits the active retopology mesh without built-in symmetry. For a
symmetrical result, use Blender's Mirror modifier on a separate retopology mesh.
The reference mesh stays separate from the mesh you edit.

In Brush mode, left-drag moves visible vertices inside the brush with a soft
falloff. Hold Shift while dragging to relax geometry. Both operations snap to
Reference when set. Press F and move the mouse horizontally to adjust the brush
radius; left-click confirms and Esc/right-click cancels the size change.
Esc/right-click during a brush stroke restores the original vertex positions.
Brush strokes support normal undo and do not merge vertices.

- Hold Alt to preview a knife point or a cut line from the selected vertex.
  Alt-left-click an edge to insert a point, or click an existing vertex to reuse
  it. Further Alt-clicks connect from the previous endpoint through the faces.
  A selected drawing edge is ignored, so knife clicks do not bridge or extrude.
  Small hand movements stay clicks; precise endpoint clicks snap to existing vertices.
- Hold Alt and left-drag at least six screen pixels over an edge to switch to
  a loop-cut preview through its connected quad strip. Dragging never inserts a
  knife point first. The source edge stays fixed while the cut slides along it
  according to the current mouse position. A drag begun off-edge acquires the
  first visible crossed edge, including during fast movement between mouse events.
  Moving over a different edge does not switch the loop source.
  Alt-hover never selects another edge or modifies topology.
  New loop vertices snap to the nearest evaluated Reference surface with Offset;
  the preview uses the same snapped positions. Without Reference they stay on
  the edited edges. Existing vertices are not moved.
  Release confirms the loop at the current mouse position.
  Esc or right-click before release cancels without changing the mesh. The cut stops at
  boundaries, non-quad faces, hidden geometry, and non-manifold edges.
- A Ctrl-click without dragging subdivides the clicked edge on release unless
  a different edge is selected, in which case it bridges. Ctrl-drag from the
  selected boundary or loose edge draws a face strip; dragging an interior or
  unselected edge cancels without editing.

- Ctrl-click an edge to insert a vertex at the cursor position. Adjacent faces
  retain their surface and gain the inserted corner.
- Ctrl-click another edge to split it and connect from the previous cut point.
- Select a vertex, then Ctrl-click another vertex or edge to cut between them.
- Cuts split existing faces, including a path across multiple faces where
  Blender can resolve it. The last point stays selected for continued cutting.
- Click empty space without Ctrl to clear the cut starting point. Ctrl-click
  an edge after that to subdivide it without connecting to an earlier point.

Hold Ctrl for the insertion-point, cut-line, or bridge preview. Ctrl-Shift also
supports bridging. Connections that cannot form a face path are rejected before editing
the mesh; use intermediate edge clicks to define a path in complex topology.

Left-click selects a vertex or edge; drag a vertex to move it on the surface.
Left-drag the middle of an edge to move both endpoints together. Endpoints snap
to Reference when set; without Reference the edge translates in the view plane.
Each endpoint can also snap onto another visible vertex within Merge Radius while
dragging, highlighted in amber. Release to weld the snapping endpoints and move
the rest in one undo step. Esc/right-click restores both endpoints without
merging. Ctrl-drag on the selected boundary or loose edge
draws a face strip. Alt-click is knife; Alt-drag is loop cut.
Dragging within Merge Radius of another visible vertex snaps to that vertex and
highlights it in amber. Release to weld the dragged vertex into the target,
preserving the target position and joining the topology. Esc/right-click restores
the starting position without merging. Move away from the target before releasing
to continue an ordinary drag. Merge Radius is measured in screen pixels; picking
rejects vertices hidden behind the edited mesh or Reference.
Hold Ctrl to see a translucent polygon and outline before clicking. The preview
includes triangle-to-quad conversion and edge bridges; invalid faces show no fill.
Shift-click toggles selection. Vertices take pick priority near endpoints;
click away from an edge's endpoints to select or split it. Hovered geometry is green.
Ctrl-click an existing vertex to reuse it. Empty left-click clears selection.
Alt-click makes knife cuts and Alt-drag cuts quad loops; neither deletes geometry. Use
Blender's native X/Delete commands for deletion. Hidden or back-facing geometry
is ignored. Esc/right-click during dragging restores the
vertex position. Ctrl-Z uses
Blender's normal undo history. Middle-mouse navigation and wheel zoom remain
available. Deactivate returns to box selection.

Reference projects onto the closest evaluated mesh surface in the selected
collection, including child collections and modifiers. The edited mesh is excluded.
Offset is measured in world units. New-point clicks outside the reference do nothing.
Edge subdivision and face cuts project newly inserted vertices onto Reference,
including intermediate vertices created when cutting across multiple faces.
Existing endpoints stay in place. Previews use the same projected positions.
Offset and front-face visibility protection also apply to these inserted points.
With no Reference selected, placement uses a view-aligned plane through the
active element, or the 3D cursor for the first vertex.

Reference Tolerance is shown only in the add-on Preferences, under Picking,
and is fixed at 64 screen pixels: picking allows vertices and
edges up to that depth allowance beneath Reference, scaling with zoom. Deeply
hidden geometry remains blocked. This tolerance does
not relax drag-to-merge or surface-snapping visibility checks.

Retopology View controls Blender's native retopology overlay in the current
viewport. It is enabled by default when the add-on is enabled and when activating
the tool or switching its mode; you can turn it off in the sidebar.
View Offset is Blender's overlay offset, independent of the geometry
projection Offset. Existing object display settings are preserved on activation.

## Scope

Works on the active edit mesh; use single-object Edit Mode. Picking is in
screen space with occlusion checks against the edited mesh and Reference.
Surface placement rejects back-facing hits. Edge and loop snapping only accepts
visible, front-facing Reference surfaces; hidden portions keep their unsnapped
positions. Reference normals should point outwards. Use Retopology View to enable
Blender's native retopo display. This is a manual tool:
it does not remesh automatically, weld overlapping points, or continuously
reproject existing geometry when the reference changes. Face connections reject
crossed/degenerate faces and saturated edges. Keep strip corners ordered around
the face; for a loose starting edge, the first new corner should be nearer
the endpoint it continues from. Selection changes commit pending triangles.

## Verification

Run `blender --background --factory-startup --python-exit-code 1 --python tests/blender_smoke.py`.
`blender_smoke.py` no longer runs the suite when imported; run one test with:

```sh
blender --background --factory-startup --python-exit-code 1 --python-expr "import runpy, unittest; ns=runpy.run_path('tests/blender_smoke.py'); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('test_grid_hover_reuses_projected_boundary_loops', ns['TopologyTests'])); raise SystemExit(not result.wasSuccessful())"
```
Tests cover registration, selected-mesh activation, projection, dragging,
progressive strips, bridges, edge subdivision, cuts across faces, previews,
crossed-quad rejection, and persistence of pending topology in mesh data.
Ctrl-drag tests cover curved face strips, event-independent spacing, projection,
release, cancellation, selection, rollback of invalid strokes, click compatibility,
accidental-start protection, status messages, connections to existing edges,
side welding within Merge Radius and the face reach, backwards trimming over the
drawn strip, and the pending-triangle start from a loose edge.
Fill Hole tests cover hole detection, hover previews, reference snapping, fixed
rim vertices, winding and material preservation, invalid-fill rollback, the pie
menu entry, mode switching, applying the fill to a real Edit Mode mesh, Quads Only
by default, Ctrl press/release preview switching, concave and odd-sided holes,
quad dominance, sliver rejection, non-greedy triangle pairing, and side vertex
counts on unequal rims and invalid fills.
Interior-grid tests cover square cell sizes, rotated and scaled unequal rims,
complete rim connections, concave notches, and multiple interior grid patches.
Connection tests cover distant fan replacement, horizontal preference, rotated
grid rows, and local rim-to-grid distances.
Grid recovery tests cover alternative layouts, concave all-quad partitions,
meshes far from the origin, and rejection of fills outside the boundary.
Fill Hole dragging tests cover isolated vertex movement, cancellation, jitter,
object transforms, reference projection, preserved vertex counts, and reshaping
a real Edit Mode mesh before filling it.
Drag tests also cover Blender rebuilding the edit mesh between mouse events.
Merge tests cover snapping without early welding, canceling a merge, and
releasing away from a previously highlighted target.
Vertex start tests cover selecting the whole first edge, building the next face
from that edge instead of chaining vertices, anchoring the closest border edge
on Ctrl press while ignoring interior edges, keeping a pending anchor, and
keeping a split vertex selected for a following Ctrl-click cut.
Performance/cache tests cover screen-index invalidation after mesh updates,
cached hole-boundary projection, brush visibility-surface reuse, and preventing
both dragged edge endpoints from merging into the same target.
Movement tests verify that vertex/edge drags and Brush/Relax leave other geometry
untouched, including after BMesh rebuilds and cancellation. Strip tests also check
that completed rows stay fixed while extending, splitting the tip, and backtracking.
Alt-cut tests cover knife insertion, chained knife clicks, exact vertex snapping,
small click jitter, loops from selected boundary edges, fast edge crossings,
hover without reselection, locked targets across BMesh rebuilds, preview/commit
agreement, Reference projection, cancellation, and changed topology.
Ctrl-dragging an interior edge is checked to leave the mesh untouched.
Edge tests cover moving and canceling both endpoints, endpoint merging within
Merge Radius, midpoint collapse, Ctrl-click triangle creation, face area
preservation, and collinear rejection.
For the real event and undo regression, run a separate Blender instance with
`blender --factory-startup --enable-event-simulate --python tests/blender_ui_smoke.py`.
It checks dragging, merging with an off-center grip, edge dragging and collapse,
Ctrl-click triangle creation, undo/redo, cancellation, and filling after reshaping, writes
its result to `zips/blender_ui_<major>_<minor>.json`, then exits.
For Draw clicks, stable strip dragging, local movement, and undo/redo, run
`blender --factory-startup --enable-event-simulate --python tests/blender_draw_ui_smoke.py`.
Its result is written to `zips/blender_draw_ui_<major>_<minor>.json`.
