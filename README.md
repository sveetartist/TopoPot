# Retopo Pool

Minimal retopology add-on for Blender 5.0.

## Install

In Blender, open Edit > Preferences > Add-ons, use the menu's Install from Disk,
and select `zips/retopo_pool-1.3.0.zip`. Enable Retopo Pool. Restart Blender after
updating an already enabled older version.

## Use

1. Select the mesh you want to edit in Object Mode. Open the 3D View sidebar
   with N, choose Retopo, and press Activate Retopo Pool. The selected mesh
   enters Edit Mode. With no mesh selected, activation creates an empty mesh.
   Set Reference separately when you want projection onto another mesh.
2. Ctrl-click empty space (or the Reference surface) to place a vertex.
   Ctrl-click again to create an edge.
3. From a selected edge, Ctrl-click a third corner to create a triangle.
   Ctrl-click a fourth corner to replace that triangle with a quad. The new
   outer edge becomes selected, ready for the next triangle/quad pair.
4. To keep a triangle, left-click its next starting edge. A plain selection
   commits the triangle and resets the pending quad.
5. Ctrl-Shift-click another boundary edge to bridge it to the selected edge. Edges
   with a shared endpoint form a triangle; separate edges form a quad.
   Finish or commit a pending triangle before bridging.

## Cuts and Subdivision

- Ctrl-left-drag an edge to preview a loop cut through its connected quad strip.
  Drag along the starting edge to slide the cut, then release to confirm.
  Esc or right-click cancels without changing the mesh. The cut stops at
  boundaries, non-quad faces, hidden geometry, and non-manifold edges.
- A Ctrl-click without dragging still subdivides only the clicked edge on release.

- Ctrl-click an edge to insert a vertex at the cursor position. Adjacent faces
  retain their surface and gain the inserted corner.
- Ctrl-click another edge to split it and connect from the previous cut point.
- Select a vertex, then Ctrl-click another vertex or edge to cut between them.
- Cuts split existing faces, including a path across multiple faces where
  Blender can resolve it. The last point stays selected for continued cutting.
- Click empty space without Ctrl to clear the cut starting point. Ctrl-click
  an edge after that to subdivide it without connecting to an earlier point.

Hold Ctrl for the insertion-point or cut-line preview. Ctrl-Shift previews
bridges. Connections that cannot form a face path are rejected before editing
the mesh; use intermediate edge clicks to define a path in complex topology.

Left-click selects a vertex or edge; drag a vertex to move it on the surface.
Hold Ctrl to see a translucent polygon and outline before clicking. The preview
includes triangle-to-quad conversion and edge bridges; invalid faces show no fill.
Shift-click toggles selection. Vertices take pick priority near endpoints;
click away from an edge's endpoints to select or split it. Hovered geometry is green.
Ctrl-click an existing vertex to reuse it. Empty left-click clears selection.
Esc/right-click during dragging restores the vertex position. Ctrl-Z uses
Blender's normal undo history. Middle-mouse navigation and wheel zoom remain
available. Deactivate returns to box selection.

Reference projects onto the evaluated reference mesh, including its modifiers.
Offset is measured in world units. New-point clicks outside the reference do nothing.
Edge subdivision stays on the edited edge and does not project to Reference.
With no Reference selected, placement uses a view-aligned plane through the
active element, or the 3D cursor for the first vertex.

Retopology View controls Blender's native retopology overlay in the current
viewport. View Offset is Blender's overlay offset, independent of the geometry
projection Offset. Existing object display settings are preserved on activation.

## Scope

Works on the active edit mesh; use single-object Edit Mode. Picking is in
screen space and includes back-side vertices, suitable for an in-front retopo
mesh. Use Retopology View to enable Blender's native retopo display. This is a manual tool:
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
