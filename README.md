# TopoPot - Retopo Tool for Blender 

Minimal retopology add-on for Blender 5.0 or newer.

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
blender --factory-startup --command extension validate zips/topopot_retopo_tool-1.16.13.zip
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

## Cuts and Subdivision

Choose Retopo, Brush, or Box Select in the Retopo sidebar. Box Select activates
Blender's native selection tool, including its normal modifier and X-Ray behavior.
Choose Retopo or Brush in the sidebar to return from box selection.
Shift-Alt-Q opens the Retopo Modes pie menu in Mesh Edit Mode, including while
Box Select is active. Change its shortcut in Preferences > Add-ons > Retopo Pool.
Enable Preserve Borders in the pie, sidebar, or brush tool settings to relax only
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

- Ctrl-left-drag an edge to preview a loop cut through its connected quad strip.
  New loop vertices snap to the nearest evaluated Reference surface with Offset;
  the preview uses the same snapped positions. Without Reference they stay on
  the edited edges. Existing vertices are not moved.
  Drag along the starting edge to slide the cut, then release to confirm.
  Esc or right-click cancels without changing the mesh. The cut stops at
  boundaries, non-quad faces, hidden geometry, and non-manifold edges.
- A Ctrl-click without dragging subdivides the clicked edge on release unless
  a different edge is selected, in which case it bridges. Ctrl-drag still cuts a loop.

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
does not auto-merge vertices. Ctrl-drag on an edge remains the loop-cut gesture.
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
