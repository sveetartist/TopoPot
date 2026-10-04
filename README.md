# TopoPot - Retopo Tool for Blender 

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
blender --factory-startup --command extension validate zips/topopot_retopo_tool-1.20.0.zip
```

## License

GPL-3.0-or-later (GNU General Public License version 3 or any later version).

## Use

1. Select the mesh you want to edit in Object Mode. Open the 3D View sidebar
   with N, choose Retopo, and press Activate Retopo Pool. The selected mesh
   enters Edit Mode. With no mesh selected, activation creates an empty mesh.
   Set Reference to a collection when you want projection onto its meshes.
2. Ctrl-click empty space (or the Reference surface) to place a vertex.
   Ctrl-click again to create an edge.
3. From a selected edge, Ctrl-click a third corner to create a triangle.
   Ctrl-click a fourth corner to replace that triangle with a quad. The new
   outer edge becomes selected, ready for the next triangle/quad pair.
4. To keep a triangle, left-click its next starting edge. A plain selection
   commits the triangle and resets the pending quad.
5. Ctrl-click another boundary edge to bridge it to the selected edge. Two
   selected vertices joined by an edge also count as the starting edge. Edges
   with a shared endpoint form a triangle; separate edges form a quad.
  Finish or commit a pending triangle before bridging.

## Drawing Face Strips

In Retopo mode, select a loose or boundary edge, then hold Ctrl and left-drag
from the middle of that edge or from empty space to draw a strip of quads.
The strip follows the dragged path, turns with the stroke, and uses the starting
edge's width for face spacing. This works with Quad Mode enabled or disabled.
Keep Ctrl held and move at least 16 screen pixels while holding the left mouse
button to start drawing. Smaller movements keep the normal Ctrl-click action;
releasing Ctrl before drawing starts keeps that stroke as a click.
With Snapping enabled and a Reference collection set, new corners project onto
the reference surface with Offset. The starting edge stays in place.

An orange face preview and a "Drawing strip" message in the viewport header
indicate that strip drawing is active. Hover another visible loose or boundary
edge to connect: the preview and destination edge turn green, and the header
shows "Connect strip to edge". Release over that edge to join the strip using
its existing vertices. Move away to continue free drawing. Connections to
interior edges, crossed faces, and incompatible winding are rejected; existing
vertices and face normals stay in place. Adjacent edges can connect with a triangle.

Release the left mouse button to create the
strip in one undo step; its final outer edge becomes selected for the next stroke.
Press Esc or right-click to cancel without changing the mesh. Invalid or folded
strips are rejected. A Ctrl-click without dragging keeps the existing click action.
Ctrl-dragging an interior edge, or an unselected edge, still previews a loop cut.

## Fill Hole

Open the Shift-Alt-Q pie menu and choose Fill Hole, which replaces the old
Preserve Borders pie entry. Point inside a visible hole to preview the filled
area in cyan; no selection is required. The viewport header shows the fill mode
and the quad/triangle counts. Left-click to apply the preview in one undo step.
Fill Hole stays active for the next hole. Choose Fill Hole again in the pie, choose another
mode, or press Esc/right-click to return to Retopo.

Quads Only is the default and requires an even number of rim vertices, at least
four. Hold Ctrl to temporarily switch to Quad/Tri; releasing Ctrl immediately
restores Quads Only, including when the mouse is stationary. Hold Ctrl while
clicking to apply the mixed preview. This is independent of Retopo's Quad Mode.
In Quads Only, numbers beside the rim show each side's vertex count, including
both corner vertices. The four corners are detected from the sharpest rim turns.
Counts remain visible on invalid fills so you can subdivide or dissolve rim
vertices to match opposite sides, then hover the hole again to fill it with quads.

Quad/Tri favors quad grids and searches alternative diagonals on smaller irregular
holes to minimize triangles, then chooses better-shaped quads. Odd rims retain a
triangle for parity; dense rims can use an interior grid patch. Narrow, concave,
or folded quad candidates remain triangles instead of forcing poor quads. On
larger irregular holes, triangle pairs are matched together to avoid stranding
triangles through greedy joins. The fill follows the actual hole outline,
including notches, and keeps the existing rim vertices.

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

Choose Retopo, Brush, Box Select, or Fill Hole in the Retopo sidebar. Box Select activates
Blender's native selection tool, including its normal modifier and X-Ray behavior.
Choose Retopo or Brush in the sidebar to return from box selection.
Shift-Alt-Q opens the Retopo Modes pie menu in Mesh Edit Mode, including while
Box Select is active. Change its shortcut in Preferences > Add-ons > Retopo Pool.
Enable Preserve Borders in the brush sidebar or tool settings to relax only
interior surface vertices, leaving open borders and loose geometry fixed.
Enable Prefer Quad in Retopo mode to extrude quads while allowing triangle
connections between neighboring edges that share a vertex.
Each click away from the active edge extrudes a complete quad, with the new edge
centered at the cursor and selected for the next extrusion. Clicking an existing
vertex snaps one new corner to it; clicking another boundary edge bridges to it.
New corners also snap automatically to existing vertices within four times Merge Radius.

In Brush mode, left-drag moves visible vertices inside the brush with a soft
falloff. Hold Shift while dragging to relax geometry. Both operations snap to
Reference when set. Press F and move the mouse horizontally to adjust the brush
radius; left-click confirms and Esc/right-click cancels the size change.
Esc/right-click during a brush stroke restores the original vertex positions.
Brush strokes support normal undo and do not merge vertices.

- Ctrl-left-drag an interior or unselected edge to preview a loop cut through its connected quad strip.
  New loop vertices snap to the nearest evaluated Reference surface with Offset;
  the preview uses the same snapped positions. Without Reference they stay on
  the edited edges. Existing vertices are not moved.
  Drag along the starting edge to slide the cut, then release to confirm.
  Esc or right-click cancels without changing the mesh. The cut stops at
  boundaries, non-quad faces, hidden geometry, and non-manifold edges.
- A Ctrl-click without dragging subdivides the clicked edge on release unless
  a different edge is selected, in which case it bridges. Ctrl-drag cuts a loop
  unless it starts from the selected boundary or loose edge, which draws a face strip.

- Ctrl-click an edge to insert a vertex at the cursor position. Adjacent faces
  retain their surface and gain the inserted corner.
- Ctrl-click another edge to split it and connect from the previous cut point.
- Select a vertex, then Ctrl-click another vertex or edge to cut between them.
- Ctrl-drag from one vertex to another also connects them, splitting the shared
  n-gon or a supported face path. Release over the destination vertex to commit;
  Esc or right-click cancels. Adjacent vertices reuse their existing edge.
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
Release to confirm, or Esc/right-click to restore both endpoints. Edge dragging
does not auto-merge vertices. Ctrl-drag on the selected boundary or loose edge
draws a face strip; on interior or unselected edges it remains the loop-cut gesture.
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
Esc/right-click during dragging restores the vertex position. Ctrl-Z uses
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

Reference Tolerance allows picking vertices and edges slightly beneath
Reference. It defaults to a depth equivalent to 12 screen pixels and scales with
zoom. Increase it for buried retopo geometry, or set it to zero for strict
reference occlusion. Deeply hidden geometry remains blocked. This tolerance does
not relax drag-to-merge or surface-snapping visibility checks.

Retopology View controls Blender's native retopology overlay in the current
viewport. View Offset is Blender's overlay offset, independent of the geometry
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
Tests cover registration, selected-mesh activation, projection, dragging,
progressive strips, bridges, edge subdivision, cuts across faces, previews,
crossed-quad rejection, and persistence of pending topology in mesh data.
Ctrl-drag tests cover curved face strips, event-independent spacing, projection,
release, cancellation, selection, rollback of invalid strokes, click compatibility,
accidental-start protection, status messages, and connections to existing edges.
Fill Hole tests cover hole detection, hover previews, reference snapping, fixed
rim vertices, winding and material preservation, invalid-fill rollback, the pie
menu entry, mode switching, applying the fill to a real Edit Mode mesh, Quads Only
by default, Ctrl press/release preview switching, concave and odd-sided holes,
quad dominance, sliver rejection, non-greedy triangle pairing, and side vertex
counts on unequal rims and invalid fills.
