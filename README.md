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

The TopoPot sidebar groups controls in collapsible sections.

- **Tools** activates TopoPot and switches between Draw, Brush, Box Select, and Fill Hole.
- **Tool Options** shows settings for the current tool, plus snapping and the pick and merge radii.
- **Symmetry** contains the axis toggles, Snap to Center, and Resymmetrize.
- **Reference** contains the reference collection, surface offset, and Move Selection to Reference.
- **Display** contains Retopology View, View Offset, and the topology color preset.

In **Display**, select the Custom preset to edit the Draw and face Overlay swatches.
The colors update the viewport immediately and share the add-on Preferences settings.
Adjust overlay opacity in the add-on Preferences.

1. Select the mesh you want to edit in Object Mode. Open the 3D View sidebar
   with N, choose TopoPot, and press Activate Draw Tool under **Tools**. The panel header shows
   **TopoPot - Retopology Tool** and the installed version. The selected mesh
   enters Edit Mode. With no mesh selected, activation creates an empty mesh.
   Under **Reference**, select a collection to project onto its meshes.
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
6. Ctrl-clicking the selected edge keeps it selected without inserting a vertex.
   With Auto Reselect enabled, Ctrl switches single or multiple selected vertices
   to a nearby boundary or loose edge for drawing. Use Alt for knife cuts.

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
strokes drawn alongside existing topology join it without exact aiming. When a
side corner welds, the free corner follows that row's displacement to preserve
the strip width, with reference snapping and face validity checks. Along an
attached boundary with much longer edges, the strip uses the neighboring row spacing
instead of inserting extra small quads based on the starting edge's width. A
single tip face grows until it reaches the next row; dragging away resumes free
strip spacing. Corners
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
Preserve Borders pie entry. All visible holes show their possible fill in cyan
while Fill Hole is active, including when the pointer is away from a hole or
outside the viewport; no selection is required. Previews refresh after viewport
navigation or a rim drag finishes. Fill Hole caches the Quads Only and Quad/Tri
layouts for the current mesh. The viewport header shows the hovered hole's fill
mode and the quad/triangle counts. Left-click inside the hole to apply the preview
in one undo step.
Fill Hole stays active for the next hole. Choose Fill Hole again in the pie, choose another
mode, or press Esc/right-click to return to Draw.

Drag a visible vertex while Fill Hole is active to reshape the rim before filling. Release to keep the move in one undo step; Esc or
right-click during the drag restores its original position and keeps Fill Hole
active. A click or small mouse jitter on a vertex selects it without moving,
merging, or filling the hole.
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
Drag the edge's center onto a neighboring edge to snap both ends together, even
if the edges have different lengths. Release to merge into the target edge.
Neighbors share a corner, a face, or an immediate connecting edge; disconnected
edges are excluded. Shared corners and target positions stay fixed. The preview
checks the actual weld and rejects non-manifold results.
Release away from merge targets keeps the move. Esc/right-click restores both
endpoints without merging. Click an edge without dragging to select it;
small mouse jitter does not collapse, merge, or move its endpoints. Ctrl-click an edge
to insert a vertex at the clicked position and connect it to the closest visible
vertex that can form a triangle. The connection splits a neighboring face or
adds a triangle into the hole. Each gesture is one undo step and keeps Fill Hole
active. Clicking near an endpoint picks that vertex; use the middle of the edge
for edge gestures.

Alt works just like Draw while Fill Hole is active: Alt-click inserts or connects
knife points, and Alt-drag over an edge slides a loop cut. Cut previews appear
over the persistent fill previews. Release to apply in one undo step; Esc/right-click
cancels the cut and keeps Fill Hole active. Alt-hover leaves selection unchanged.

Quads Only is the default. Four enlarged white labels with black outlines show
side vertex counts, including the
corner vertices. White markers with dark outlines mark the four corners that separate
the labeled sides, including when a fill is invalid. These are visual guides, not
draggable handles. Opposite sides should have matching counts for a regular grid.
The side labels and corner markers hide while Ctrl enables Quad/Tri.
Click inside the hole to fill. Quads Only needs an even number of rim vertices.
Hold Ctrl for Quad/Tri when a strict grid is not suitable. Quad/Tri prioritizes
square quads with equal edges and right angles, together with well-shaped
triangles targeting the same area as the quads. Its interior adjustment balances
shape and area together; good interior squares stay within a 1.25 edge-length
ratio and roughly 75–105 degree corners. The existing rim
and surrounding topology stay fixed. Boundary spacing and hole shape limit how
equal the areas can become; fold and sliver checks remain active. New points
continue to follow Reference when Snapping is enabled.

Wheel-based side recalculation has been removed; scrolling zooms normally.

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

In Brush mode, left-drag moves visible vertices inside the brush with a soft
falloff. Hold Shift while dragging to relax geometry. Both operations snap to
Reference when set. Press F and move the mouse horizontally to adjust the brush
radius; left-click confirms and Esc/right-click cancels the size change.
Press Shift+F and move right to increase strength or left to decrease it;
left-click confirms and Esc/right-click restores the previous strength. Strength
affects both Move and Shift-Relax, from 0% to 100%, and is also available in the
Brush sidebar and tool settings. The default 100% preserves the original behavior.
The outer cursor circle shows radius; the inner circle shows strength in the same
brush color at lower opacity. It grows with strength and updates live during
Shift+F adjustment. Both circles hide while orbiting, panning, or zooming.
Esc/right-click during a brush stroke restores the original vertex positions.
Brush strokes support normal undo.

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
- Ctrl-clicking the selected edge keeps it selected; clicking another boundary
  edge bridges from the active edge. Ctrl-drag from a selected boundary or loose
  edge draws a face strip. Ctrl never subdivides edges or cuts existing faces.
- Alt-click an edge to insert a knife point, or another vertex/edge to continue
  a cut from the previous point. Cuts can split existing faces across a path.
  Use intermediate Alt-clicks to define a path in complex topology.
- Alt-hover shows the knife preview; Alt-drag over an edge cuts a quad loop.
  Ctrl previews drawing and bridging. Ctrl-Shift also supports bridging.

Left-click selects a vertex or edge; drag a vertex to move it on the surface.
Left-drag the middle of an edge to move both endpoints together. Endpoints snap
to Reference when set; without Reference the edge translates in the view plane.
With Snapping enabled, drag its center onto a neighboring edge within Merge Radius
to snap both endpoints and weld on release. This uses the same neighboring-edge
rules as Fill Hole, keeps the target in place, and supports undo and cancellation.
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
Blender's normal undo history. After undo or redo, TopoPot refreshes edit-mesh
picking and reuses cached reference surfaces when their evaluated geometry and
transforms match. Middle-mouse navigation and wheel zoom remain
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

## Symmetry, Snap to Center, and Resymmetrize

Enable **Symmetry** in the TopoPot sidebar or tool settings. Toggle **X**, **Y**,
and **Z** independently to mirror across one or more mesh-local zero planes.
With all three enabled, an edit away from the planes mirrors into all eight
octants. Symmetry defaults to off and X is the default axis.
Draw, strips, cuts, Fill Hole, vertex and edge movement, and Brush Move/Relax
edit the actual mesh. Center vertices stay on their respective planes. When
snapping to a reference, TopoPot projects the edited region and reflects the
result across the enabled planes.
Mirrored previews use a darker tint wherever they pass behind visible geometry
or the reference surface.

Enable **Snap to Center** to place new and moved vertices exactly on the
enabled planes when they come within **Center Distance**, measured in mesh-local
units. It also works with Symmetry off. Mirrored vertices that meet on a plane
or at a plane intersection weld on release.

**Resymmetrize** rebuilds across every enabled plane in one undo step. It keeps
the region shared by the last edited side on each enabled axis and mirrors
that region into the others. TopoPot records edit sides for each mesh even
with Symmetry off. Undo and redo restore the geometry and the recorded sides
together. If a source is ambiguous or the recorded sides have no shared
geometry, choose **Negative Side** or **Positive Side** for the affected axis
in the source dialog. Resymmetrize also creates center seams through faces
that span its planes.

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
Interior-grid tests cover reduced face-area variance, valid quad shapes, rotated
and scaled unequal rims, complete rim connections, concave notches, and multiple
interior grid patches. Preview/commit comparisons include area balancing.
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
reselecting single or multiple selected vertices to an edge without Ctrl cutting.
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
Merge Radius, click selection without merging, Ctrl-click triangle creation, face area
preservation, and collinear rejection.
For the real event and undo regression, run a separate Blender instance with
`blender --factory-startup --enable-event-simulate --python tests/blender_ui_smoke.py`.
It checks dragging, merging with an off-center grip, edge dragging and selection,
Ctrl-click triangle creation, undo/redo, cancellation, and filling after reshaping, writes
its result to `zips/blender_ui_<major>_<minor>.json`, then exits.
For Draw clicks, stable strip dragging, local movement, and undo/redo, run
`blender --factory-startup --enable-event-simulate --python tests/blender_draw_ui_smoke.py`.
Its result is written to `zips/blender_draw_ui_<major>_<minor>.json`.
