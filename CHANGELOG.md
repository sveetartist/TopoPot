# Changelog

## 1.3.0

- Added independent X, Y, and Z Symmetry toggles for simultaneous symmetry in Draw, strips, knives, loop cuts, Fill Hole, vertex and edge movement, welds, and Brush Move/Relax.
- Added Snap to Center with an adjustable distance. New and moved vertices snap to the enabled planes, and coincident mirrored vertices weld on release.
- Added Resymmetrize across every enabled plane in one undo step. It rebuilds from the last edited side on each axis, with manual source choices when needed.
- Organized the TopoPot sidebar into collapsible Tools, Tool Options, Symmetry, Reference, and Display sections.
- Darkened the hidden parts of mirrored previews behind viewport geometry or the reference surface.
- Reduced Fill Hole pauses on meshes with many holes. Fill previews reuse a working copy of the relevant boundary geometry, cache Quads Only and Quad/Tri layouts, and refresh after viewport navigation or rim dragging settles.

## 1.2.1

- Reduced the redraw pause after undo and redo by reusing verified reference-surface caches and speeding up edit-mesh picking rebuilds.

## 1.2.0

### Added & Improved

- Renamed the extension to "TopoPot - Retopology Tool" and added the installed version to the N-panel header.
- Added Auto Reselect for Draw, enabled by default. Ctrl selects a nearby boundary or loose edge from single or multiple selected vertices, and hover reselection avoids flickering between equally close edges.
- Moved cutting to Alt: Alt-click inserts or connects knife points; Alt-drag creates a sliding loop cut. Both have live previews and undo/redo in Draw and Fill Hole. Ctrl now handles drawing, bridging, and strips without cutting or inserting vertices.
- Improved quad strips with stable completed rows, backward dragging to shorten the strip, and automatic connections along neighboring topology. Connected rows preserve strip width and follow longer neighboring edges without adding unnecessary small faces. Short strokes from a loose edge start with a triangle that the next click completes into a quad.
- Added neighboring-edge snapping and welding in Draw and Fill Hole, including unequal lengths and shared corners. Dragged endpoints can also merge into nearby vertices; invalid merges are rejected.
- Fixed pending triangles disappearing when starting a vertex/edge move or switching modes.
- Fill Hole now previews all visible holes without hovering, including when the pointer leaves the viewport. Larger outlined side-count labels and corner markers make Quads Only guides easier to read.
- Improved Quad/Tri fill layouts for more regular quads, well-shaped triangles, and balanced face areas while preserving the rim and reference snapping.
- Fill Hole clicks now select vertices or edges without collapsing or merging them. Drag-to-merge remains controlled by Merge on Drag.
- Added Shift+F brush strength adjustment for Move and Relax, with a live percentage and an inner cursor circle. Both brush circles hide during viewport navigation; F still adjusts radius.
- Added topology color controls below Retopology View in the N-panel. Presets fix both Draw and face overlay colors; Custom enables editing either color. Overlay opacity remains adjustable.
- Retopology View now starts enabled when the add-on or tool is activated or the mode changes.
- Reference Tolerance is fixed at 64 px and appears only in add-on Preferences.
- Improved responsiveness on large meshes and long strips through cached picking, fill previews, and brush visibility checks. Fixed preview cache invalidation after mesh changes and undo/redo.

## 1.1.0

### Added

- Quad strip drawing in Draw mode. Ctrl-drag from a selected boundary or loose edge to draw a strip of quads along the stroke.
- Fill Hole mode with Quads Only and Quad/Tri options, a live preview, and reference surface snapping.
- Move Selection to Reference button. In Object Mode, it moves selected meshes into the chosen reference collection, or creates and assigns one when needed.

## 1.1.1

Quick name fix.

## 1.1.2

Quick name fix.
