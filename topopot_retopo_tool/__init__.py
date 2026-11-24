import bpy
import bmesh
import blf
import gpu
import importlib
import time
import tomllib
from array import array
from hashlib import blake2b
from math import cos, sin, tau
from pathlib import Path
from typing import NamedTuple
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from bpy.props import PointerProperty, FloatProperty, IntProperty, EnumProperty, FloatVectorProperty, BoolProperty, BoolVectorProperty
from . import topology

# Blender can reload the package after installation while retaining its submodules.
topology = importlib.reload(topology)
ADDON_NAME = "TopoPot - Retopology Tool"
with (Path(__file__).resolve().parent / "blender_manifest.toml").open("rb") as _manifest_file:
    ADDON_VERSION = tomllib.load(_manifest_file)["version"]
TOOL_ICON = str(Path(__file__).resolve().parent / "assets" / "retopo_icon")
REFERENCE_COLLECTION_NAME = "Retopology Reference"
COLOR_PRESETS = {
    'BLUE': (0.2, 0.65, 1.0, 1.0),
    'GREEN': (0.15, 0.9, 0.65, 1.0),
    'ORANGE': (1.0, 0.55, 0.15, 1.0),
    'PURPLE': (0.75, 0.35, 1.0, 1.0),
    'PINK': (1.0, 0.25, 0.45, 1.0),
    'WHITE': (1.0, 1.0, 1.0, 1.0),
}
COLOR_PRESET_ITEMS = (
    ('BLUE', "Blue", ""),
    ('GREEN', "Green", ""),
    ('ORANGE', "Orange", ""),
    ('PURPLE', "Purple", ""),
    ('PINK', "Pink", ""),
    ('WHITE', "White", ""),
    ('CUSTOM', "Custom", ""),
)
DEFAULT_OVERLAY_COLOR = COLOR_PRESETS['BLUE'][:3]
DEFAULT_OVERLAY_OPACITY = 0.25
REFERENCE_TOLERANCE = 64
STRIP_START_DISTANCE = 16
ALT_DRAG_DISTANCE = 6
STRIP_COLOR = (1.0, 0.55, 0.1, 1.0)
STRIP_CONNECT_COLOR = (0.2, 1.0, 0.45, 1.0)
GRID_COLOR = (0.1, 0.9, 1.0, 1.0)
GRID_ERROR_COLOR = (1.0, 0.25, 0.15, 1.0)
GRID_HINT = "Fill Hole | Click: select | Drag vertices / edges | Ctrl-click edge: triangle | Alt-click: knife / Alt-drag: loop cut | Hold Ctrl: Quad/Tri | Esc / right-click returns to Draw"


def mesh_filter(self, obj):
    return obj.type == 'MESH'


def tag_view3d_redraw(context=None):
    window_manager = (context or bpy.context).window_manager if (context or bpy.context) else None
    if not window_manager:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def addon_preferences(context=None):
    prefs = (context or bpy.context).preferences
    addon = prefs.addons.get(__package__)
    return addon.preferences if addon else None


def update_retopo_color_preset(self, context):
    if self.retopo_color_preset in COLOR_PRESETS:
        self.retopo_color = COLOR_PRESETS[self.retopo_color_preset]
    apply_overlay_preferences(self, context)


def update_retopo_color(self, context):
    preset_color = COLOR_PRESETS.get(self.retopo_color_preset)
    if preset_color and any(abs(a - b) > 1e-4 for a, b in zip(self.retopo_color, preset_color)):
        self.retopo_color = preset_color
    tag_view3d_redraw(context)


def apply_overlay_preferences(prefs=None, context=None):
    prefs = prefs or addon_preferences(context)
    color = tuple(prefs.overlay_color) if prefs else DEFAULT_OVERLAY_COLOR
    preset_color = COLOR_PRESETS.get(prefs.retopo_color_preset) if prefs else None
    if preset_color is not None:
        color = preset_color[:3]
        if any(abs(a - b) > 1e-4 for a, b in zip(prefs.overlay_color, color)):
            prefs.overlay_color = color
    opacity = prefs.overlay_opacity if prefs else DEFAULT_OVERLAY_OPACITY
    bpy.context.preferences.themes[0].view_3d.face_retopology = (*color, opacity)
    tag_view3d_redraw(context)


def update_overlay_preferences(self, context):
    apply_overlay_preferences(self, context)


def enable_retopology_view(context=None):
    """Enable the native overlay when the add-on or a retopology tool starts."""
    if context is None:
        spaces = [area.spaces.active for window in bpy.context.window_manager.windows
                  for area in window.screen.areas if area.type == 'VIEW_3D']
    else:
        spaces = [getattr(context, 'space_data', None)]
    for space in spaces:
        if space is not None and space.type == 'VIEW_3D':
            space.overlay.show_retopology = True


def retopo_color(context):
    prefs = addon_preferences(context)
    if prefs is None:
        return COLOR_PRESETS['BLUE']
    return COLOR_PRESETS.get(prefs.retopo_color_preset, tuple(prefs.retopo_color))


def update_mode(self, context):
    global _grid_preview_cache, _grid_drag_region
    _grid_preview_cache = None
    _grid_drag_region = None
    if context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D':
        topology.clear_pending(bmesh.from_edit_mesh(context.edit_object.data))
        enable_retopology_view(context)
        bpy.ops.wm.tool_set_by_id(name='builtin.select_box' if self.mode == 'BOX' else RP_Tool.bl_idname)
        context.area.header_text_set(GRID_HINT if self.mode == 'GRID' else None)
        context.area.tag_redraw()


SYMMETRY_AXES = tuple((axis, axis, f"Use the mesh's local {axis} = 0 plane") for axis in 'XYZ')


class SymmetryOptions(NamedTuple):
    enabled: bool = False
    axes: tuple = (0,)
    center_distance: float = 0.0


NO_SYMMETRY = SymmetryOptions()


def get_symmetry_axes(settings):
    stored = settings.get("symmetry_axes")
    if stored is not None:
        return tuple(bool(value) for value in stored)
    return tuple(axis == settings.symmetry_axis for axis in 'XYZ')


def set_symmetry_axes(settings, axes):
    settings["symmetry_axes"] = tuple(int(value) for value in axes)


class RP_Settings(bpy.types.PropertyGroup):
    x_symmetry: BoolProperty(name="Symmetry", description="Mirror TopoPot edits across the enabled local planes", default=False, update=lambda self, context: tag_view3d_redraw(context))
    symmetry_axis: EnumProperty(name="Symmetry Axis", items=SYMMETRY_AXES, default='X', options={'HIDDEN'})
    symmetry_axes: BoolVectorProperty(name="Symmetry Axes", description="Choose the local planes used by Symmetry and Snap to Center", size=3, default=(True, False, False), get=get_symmetry_axes, set=set_symmetry_axes, update=lambda self, context: tag_view3d_redraw(context))
    snap_to_center: BoolProperty(name="Snap to Center", description="Snap new and moved vertices to the enabled center planes", default=False, update=lambda self, context: tag_view3d_redraw(context))
    center_distance: FloatProperty(name="Center Distance", description="Snap distance from the center plane in mesh-local units", default=0.01, min=0.00001, soft_max=0.1, precision=4, update=lambda self, context: tag_view3d_redraw(context))
    preserve_borders: bpy.props.BoolProperty(name="Preserve Borders", description="Relax only interior surface vertices, keeping open borders and loose geometry fixed", default=False)
    mode: EnumProperty(name="Mode", items=(('RETOPO', "Draw", "Create and move topology"), ('BRUSH', "Brush", "Move vertices; Shift relaxes"), ('BOX', "Box Select", "Blender's native box selection"), ('GRID', "Fill Hole", "Click vertices or edges to select; drag to reshape; Ctrl-click an edge for a triangle; click inside to fill")), default='RETOPO', update=update_mode)
    auto_reselect: BoolProperty(name="Auto Reselect", description="Automatically switch the Draw starting edge while holding Ctrl", default=True)
    quad_only: BoolProperty(name="Quad Mode", description="Extrude quads with the new edge centered at the cursor; allow triangles when connecting neighboring edges", default=False)
    snapping: BoolProperty(name="Snapping", description="Snap to the reference surface and automatically merge nearby vertices and dragged edge endpoints", default=True)
    fill_merge: BoolProperty(name="Merge on Drag", description="In Fill Hole, snap dragged vertices or edge endpoints onto another vertex and merge on release", default=True)
    brush_radius: IntProperty(name="Brush Radius", default=75, min=5, max=500, subtype='PIXEL')
    brush_strength: FloatProperty(name="Brush Strength", description="Influence of the Move and Relax brushes; Shift-F adjusts strength", default=1.0, min=0, max=1, subtype='FACTOR')
    target: PointerProperty(name="Reference", description="Mesh objects in this collection and its child collections provide snapping surfaces", type=bpy.types.Collection)
    offset: FloatProperty(name="Offset", default=0.001, min=0, soft_max=0.1, subtype='DISTANCE')
    radius: IntProperty(name="Pick Radius", default=14, min=4, max=48, subtype='PIXEL')
    merge_radius: IntProperty(name="Merge Radius", description="Snap and merge dragged vertices or edge endpoints, and weld strip corners, within this screen distance", default=10, min=3, max=32, subtype='PIXEL')


def project(context, mouse, depth):
    region, rv3d = context.region, context.region_data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
    settings = context.scene.retopo_tool
    if settings.snapping and settings.target:
        tree = reference_tree(context)
        if tree is None:
            return None
        location, normal, _, _ = tree.ray_cast(origin, direction)
        if location is not None and normal.dot(direction) < -1e-6:
            return location + normal * settings.offset
        return None
    return view3d_utils.region_2d_to_location_3d(region, rv3d, mouse, depth)


_visibility_cache = {}
_undo_reference_cache = {}
_screen_cache = None
_hole_geometry_cache = None
_hole_screen_cache = None
_hover_preview_cache = None
_UNSET = object()
SCREEN_CELL = 16
_hover_views = {}
HOVER_NAV_DELAY = 0.12


def navigation_changed(context):
    """True while the view keeps changing (orbit/pan/zoom), to pause hover work."""
    view = getattr(context.region_data, 'perspective_matrix', None)
    if view is None:
        return False
    region_key = object_key(context.region)
    matrix = tuple(value for row in view for value in row)
    old, changed_at = _hover_views.get(region_key, (matrix, 0))
    now = time.perf_counter()
    if old != matrix:
        changed_at = now
    _hover_views[region_key] = matrix, changed_at
    return now - changed_at < HOVER_NAV_DELAY


def redraw_after_navigation():
    tag_view3d_redraw()


def queue_navigation_redraw():
    if not bpy.app.timers.is_registered(redraw_after_navigation):
        bpy.app.timers.register(redraw_after_navigation, first_interval=HOVER_NAV_DELAY)


def object_key(obj):
    return obj.as_pointer() if hasattr(obj, 'as_pointer') else id(obj)


def fast_screen_projector(context, matrix):
    """Direct matrix projection for real viewports; None for fake test contexts."""
    view = context.region_data
    region = context.region
    if not isinstance(view, bpy.types.RegionView3D) or region is None:
        return None
    m = [value for row in (view.perspective_matrix @ matrix) for value in row]
    half_width, half_height = region.width / 2.0, region.height / 2.0

    def project(co):
        x, y, z = co.x, co.y, co.z
        w = m[12] * x + m[13] * y + m[14] * z + m[15]
        if w <= 0.0:
            return None
        return Vector((
            half_width + half_width * ((m[0] * x + m[1] * y + m[2] * z + m[3]) / w),
            half_height + half_height * ((m[4] * x + m[5] * y + m[6] * z + m[7]) / w)))
    return project


def screen_index(context, bm):
    """Cached screen-space bucket index for fast hover picking.

    Stores element indices rather than BMesh wrappers: Blender can rebuild the
    edit BMesh between calls, which would invalidate cached wrappers.
    """
    global _screen_cache
    region = context.region
    matrix = context.edit_object.matrix_world
    view = getattr(context.region_data, 'perspective_matrix', None)
    signature = (object_key(context.edit_object), object_key(getattr(context.edit_object, 'data', None)),
                 tuple(value for row in matrix for value in row),
                 tuple(value for row in view for value in row) if view is not None else None,
                 getattr(region, 'width', None), getattr(region, 'height', None),
                 len(bm.verts), len(bm.edges), len(bm.faces))
    if _screen_cache is not None and _screen_cache[0] == signature:
        return _screen_cache[1]
    projector = fast_screen_projector(context, matrix)
    if projector is None:
        def projector(co):
            return view3d_utils.location_3d_to_region_2d(region, context.region_data, matrix @ co)
    points = []
    point_cells = []
    vert_cells = {}
    positions = {}
    for position, vertex in enumerate(bm.verts):
        positions[vertex] = position
        point = None if vertex.hide else projector(vertex.co)
        points.append(point)
        cell = None
        if point is not None:
            cell = (int(point.x // SCREEN_CELL), int(point.y // SCREEN_CELL))
            vert_cells.setdefault(cell, []).append(position)
        point_cells.append(cell)
    edge_cells = {}
    for position, edge in enumerate(bm.edges):
        if edge.hide:
            continue
        start, end = edge.verts
        a_index, b_index = positions[start], positions[end]
        a, b = point_cells[a_index], point_cells[b_index]
        if a is None or b is None:
            continue
        entry = (position, a_index, b_index)
        if a == b:
            edge_cells.setdefault(a, []).append(entry)
            continue
        for cx in range(min(a[0], b[0]), max(a[0], b[0]) + 1):
            for cy in range(min(a[1], b[1]), max(a[1], b[1]) + 1):
                edge_cells.setdefault((cx, cy), []).append(entry)
    result = points, vert_cells, edge_cells
    _screen_cache = signature, result
    return result


def screen_candidates(cells, mouse, radius):
    """Element indices from the index buckets near the cursor."""
    reach = max(1, int(radius // SCREEN_CELL) + 1)
    cx, cy = int(mouse.x // SCREEN_CELL), int(mouse.y // SCREEN_CELL)
    verts, edges = set(), set()
    for x in range(cx - reach, cx + reach + 1):
        for y in range(cy - reach, cy + reach + 1):
            verts.update(cells[1].get((x, y), ()))
            edges.update(cells[2].get((x, y), ()))
    return verts, edges


def cached_tree(kind, obj, build, counts=()):
    data = getattr(obj, 'data', None)
    signature = (object_key(data), tuple(value for row in obj.matrix_world for value in row), counts)
    key = (kind, object_key(obj))
    entry = _visibility_cache.get(key)
    if entry is None or entry[0] != signature:
        entry = (signature, build())
        _visibility_cache[key] = entry
    return entry[1]


def edit_surface_geometry(context, bm):
    """Build the visible edit-surface BVH and its face-index mapping."""
    matrix = context.edit_object.matrix_world
    verts = list(bm.verts)
    indices = {vert: index for index, vert in enumerate(verts)}
    faces = [face for face in bm.faces
             if not face.hide and all(not vert.hide for vert in face.verts)]
    tree = BVHTree.FromPolygons(
        [matrix @ vert.co for vert in verts],
        [tuple(indices[vert] for vert in face.verts) for face in faces])
    return tree, faces


def cached_edit_tree(context, bm):
    return cached_tree(
        'edit', context.edit_object,
        lambda: edit_surface_geometry(context, bm)[0],
        (len(bm.verts), len(bm.edges), len(bm.faces)))


@bpy.app.handlers.persistent
def invalidate_visibility(scene=None, depsgraph=None):
    global _grid_preview_cache, _grid_drag_region, _screen_cache, _hole_geometry_cache, _hole_screen_cache, _hover_preview_cache
    if depsgraph is None:
        _grid_preview_cache = None
        _grid_drag_region = None
        _screen_cache = None
        _hole_geometry_cache = None
        _hole_screen_cache = None
        _hover_preview_cache = None
        _hover_views.clear()
        _visibility_cache.clear()
        _undo_reference_cache.clear()
        return
    updates = {object_key(update.id.original) for update in depsgraph.updates}
    changed = {object_key(update.id.original) for update in depsgraph.updates
               if update.is_updated_geometry or update.is_updated_transform}
    if updates:
        _hover_preview_cache = None
    for key, (signature, _) in list(_visibility_cache.items()):
        if key[1] in changed or signature[0] in changed:
            del _visibility_cache[key]
    if _screen_cache is not None and any(key in updates for key in _screen_cache[0][:2]):
        _screen_cache = None
    if _hole_screen_cache is not None and any(key in updates for key in _hole_screen_cache[0][:2]):
        _hole_screen_cache = None
    if _hole_geometry_cache is not None and any(key in updates for key in _hole_geometry_cache[0][:2]):
        _hole_geometry_cache = None
    if _grid_preview_cache is not None and (
            updates.intersection(_grid_preview_cache['signature'][:2])
            or changed.intersection(_grid_preview_cache['reference_ids'])):
        _grid_preview_cache = None


@bpy.app.handlers.persistent
def invalidate_after_undo(scene):
    # Retain only BVHs that can be checked against the restored evaluated mesh.
    # Blender can replace IDs and restore a different mesh with the same counts.
    references = {key: entry[1] for (kind, key), entry in _visibility_cache.items()
                  if kind == 'reference'}
    invalidate_visibility()
    _undo_reference_cache.update(references)


def reference_mesh_signature(mesh, matrix):
    """Fingerprint exactly the evaluated coordinates and face winding used by the BVH."""
    digest = blake2b(digest_size=16)
    # Read the underlying mesh attributes in bulk when Blender exposes them.
    positions = mesh.attributes.get('position')
    position_data, position_property = (positions.data, 'vector') if positions else (mesh.vertices, 'co')
    corners = mesh.attributes.get('.corner_vert')
    corner_data, corner_property = (corners.data, 'value') if corners else (mesh.loops, 'vertex_index')
    # Face sizes follow from consecutive loop starts and the total corner count.
    for elements, attribute, typecode, width in (
            (position_data, position_property, 'f', 3),
            (corner_data, corner_property, 'i', 1),
            (mesh.polygons, 'loop_start', 'i', 1)):
        values = array(typecode, [0]) * (len(elements) * width)
        elements.foreach_get(attribute, values)
        digest.update(values)
    counts = len(mesh.vertices), len(mesh.loops), len(mesh.polygons)
    return counts, tuple(value for row in matrix for value in row), digest.digest()


class ReferenceSurface:
    """Query the closest hit across world-space mesh trees."""
    def __init__(self, trees):
        self.trees = trees

    def ray_cast(self, origin, direction):
        hits = [tree.ray_cast(origin, direction) for tree in self.trees]
        return min((hit for hit in hits if hit[0] is not None),
                   key=lambda hit: hit[3], default=(None, None, None, None))

    def find_nearest(self, point):
        hits = [tree.find_nearest(point) for tree in self.trees]
        return min((hit for hit in hits if hit[0] is not None),
                   key=lambda hit: hit[3], default=(None, None, None, None))


def reference_tree(context):
    collection = context.scene.retopo_tool.target
    if collection is None:
        return None
    trees = []
    for reference in collection.all_objects:
        if reference.type != 'MESH' or reference == context.edit_object:
            continue
        def build():
            evaluated = reference.evaluated_get(context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                matrix = evaluated.matrix_world
                signature = reference_mesh_signature(mesh, matrix)
                previous = _undo_reference_cache.pop(object_key(reference), None)
                if previous is not None and previous[0] == signature:
                    return previous
                tree = BVHTree.FromPolygons([matrix @ v.co for v in mesh.vertices],
                                            [tuple(p.vertices) for p in mesh.polygons])
                return signature, tree
            finally:
                evaluated.to_mesh_clear()
        _, tree = cached_tree('reference', reference, build)
        trees.append(tree)
    _undo_reference_cache.clear()
    return ReferenceSurface(trees) if trees else None


def visible_from_view(context, co, tree, tolerance=1e-5):
    screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, co)
    if screen is None:
        return False
    origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, screen)
    direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, screen)
    distance = (co - origin).dot(direction)
    if distance <= 0:
        return False
    # Orthographic origins can be hundreds of units away. Account for the
    # float precision of BVH hits so a surface vertex does not occlude itself.
    tolerance = max(tolerance, distance * 5e-7)
    location, _, _, hit_distance = tree.ray_cast(origin, direction)
    return location is None or hit_distance >= distance - tolerance


def reference_pick_tolerance(context, co):
    settings = context.scene.retopo_tool
    tolerance = max(1e-5, settings.offset * 2)
    pixels = max(REFERENCE_TOLERANCE, getattr(addon_preferences(), 'reference_tolerance', REFERENCE_TOLERANCE))
    screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, co)
    if screen is not None:
        a = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, screen, co)
        b = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, screen + Vector((pixels, 0)), co)
        tolerance = max(tolerance, (b - a).length)
    return tolerance


def vertex_start_edge(context, vertex, mouse):
    """Closest visible boundary or loose edge at a vertex, used as the start edge."""
    matrix = context.edit_object.matrix_world
    screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, matrix @ vertex.co)
    if screen is None:
        return None
    best, best_distance = None, None
    for edge in vertex.link_edges:
        other = edge.other_vert(vertex)
        if edge.hide or other.hide or len(edge.link_faces) > 1:
            continue
        other_screen = view3d_utils.location_3d_to_region_2d(
            context.region, context.region_data, matrix @ other.co)
        if other_screen is None:
            continue
        delta = other_screen - screen
        t = max(0.0, min(1.0, (mouse - screen).dot(delta) / max(delta.length_squared, 1e-12)))
        distance = (mouse - (screen + t * delta)).length
        if best is None or distance < best_distance:
            best, best_distance = edge, distance
    return best


def pick(context, bm, mouse, exclude=None, vertices_only=False, radius=None, edges_only=False,
         relaxed_reference=False, snapshot=None):
    excluded = set()
    if isinstance(exclude, bmesh.types.BMVert):
        excluded.add(exclude)
    elif exclude is not None:
        excluded.update(exclude)
    matrix = context.edit_object.matrix_world
    trees = None

    def visible(co):
        nonlocal trees
        if trees is None:
            trees = (snapshot[1] if snapshot is not None else cached_edit_tree(context, bm), reference_tree(context))
        edit_tree, ref_tree = trees
        return (visible_from_view(context, co, edit_tree) and
                (ref_tree is None or visible_from_view(context, co, ref_tree,
                 max(1e-5, context.scene.retopo_tool.offset * 2) if exclude is not None and not relaxed_reference
                 else reference_pick_tolerance(context, co))))
    index = snapshot[0] if snapshot is not None else screen_index(context, bm)
    points = index[0]
    vr = er = context.scene.retopo_tool.radius if radius is None else radius
    vert_indices, edge_entries = screen_candidates(index, mouse, vr)
    best_v, best_e = None, None
    if vert_indices:
        bm.verts.ensure_lookup_table()
    for vert_index in vert_indices:
        if vert_index >= len(bm.verts):
            continue
        vert = bm.verts[vert_index]
        if vert.hide or vert in excluded:
            continue
        point = points[vert_index]
        if point is None:
            continue
        distance = (point - mouse).length
        if not edges_only and distance < vr and visible(matrix @ vert.co):
            best_v, vr = vert, distance
    if vertices_only:
        return best_v
    if edge_entries:
        bm.edges.ensure_lookup_table()
    for edge_index, a_index, b_index in edge_entries:
        if edge_index >= len(bm.edges):
            continue
        edge = bm.edges[edge_index]
        if edge.hide:
            continue
        a, b = points[a_index], points[b_index]
        if a is None or b is None:
            continue
        delta = b - a
        t = max(0, min(1, (mouse - a).dot(delta) / max(delta.length_squared, 1e-12)))
        distance = (mouse - (a + t * delta)).length
        if distance < er and visible(matrix @ edge.verts[0].co.lerp(edge.verts[1].co, t)):
            best_e, er = edge, distance
    return best_e if edges_only else (best_v if best_v is not None else best_e)



def closest_border_edge(context, bm, mouse, radius=None):
    """Closest visible boundary or loose edge under the cursor, for Ctrl starts."""
    matrix = context.edit_object.matrix_world
    limit = context.scene.retopo_tool.radius if radius is None else radius
    trees = []

    def visible(co):
        if not trees:
            trees.append(cached_edit_tree(context, bm))
            trees.append(reference_tree(context))
        edit_tree, reference = trees
        return (visible_from_view(context, co, edit_tree) and
                (reference is None or visible_from_view(
                    context, co, reference, reference_pick_tolerance(context, co))))

    index = screen_index(context, bm)
    points = index[0]
    _, edge_entries = screen_candidates(index, mouse, limit)
    bm.edges.ensure_lookup_table()
    best, best_distance = None, limit
    for edge_index, a_index, b_index in edge_entries:
        if edge_index >= len(bm.edges):
            continue
        edge = bm.edges[edge_index]
        if edge.hide or len(edge.link_faces) > 1:
            continue
        a, b = points[a_index], points[b_index]
        if a is None or b is None:
            continue
        delta = b - a
        t = max(0.0, min(1.0, (mouse - a).dot(delta) / max(delta.length_squared, 1e-12)))
        distance = (mouse - (a + t * delta)).length
        if distance < best_distance and visible(matrix @ edge.verts[0].co.lerp(edge.verts[1].co, t)):
            best, best_distance = edge, distance
    return best


def placement(context, mouse, anchor):
    obj = context.edit_object
    if isinstance(anchor, bmesh.types.BMVert):
        depth = obj.matrix_world @ anchor.co
    elif isinstance(anchor, bmesh.types.BMEdge):
        depth = obj.matrix_world @ ((anchor.verts[0].co + anchor.verts[1].co) / 2)
    else:
        depth = context.scene.cursor.location
    return project(context, mouse, depth)


def reselect_draw_edge(context, bm, anchor, mouse, hit=None):
    """Use one reselection rule for Ctrl hover and the following Draw press."""
    if (getattr(context.scene.retopo_tool, 'auto_reselect', True)
            and (not isinstance(anchor, bmesh.types.BMEdge)
                 or sum(v.select and not v.hide for v in bm.verts) > 2)
            and any(v.select and not v.hide for v in bm.verts)):
        start_edge = closest_border_edge(context, bm, mouse)
        if start_edge is None and isinstance(anchor, bmesh.types.BMVert):
            start_edge = vertex_start_edge(context, anchor, mouse)
        if start_edge is not None:
            topology.clear_pending(bm)
            topology.select(bm, start_edge)
            return start_edge
    if (not getattr(context.scene.retopo_tool, 'auto_reselect', True)
            or not isinstance(anchor, bmesh.types.BMEdge) or len(anchor.link_faces) != 1
            or topology.pending(bm) or hit == anchor):
        return anchor
    if hit is not None:
        # Existing, valid vertex connections and bridges remain drawing targets.
        if isinstance(hit, bmesh.types.BMEdge) and len(hit.link_faces) > 1:
            return anchor
        if preview(bm, anchor, hit, None):
            return anchor
        if isinstance(hit, bmesh.types.BMVert):
            point = hit.co.copy()
        else:
            point = hit.verts[0].co.lerp(hit.verts[1].co, edge_parameter(context, hit, mouse))
    else:
        world = placement(context, mouse, anchor)
        if world is None:
            return anchor
        point = context.edit_object.matrix_world.inverted_safe() @ world
    return topology.hover_select_edge(bm, anchor, point)


def reference_projector(context):
    if not context.scene.retopo_tool.snapping:
        return None
    reference = context.scene.retopo_tool.target
    obj = context.edit_object
    if reference is None:
        return None
    tree = reference_tree(context)
    if tree is None:
        return None
    matrix = obj.matrix_world.copy()
    inverse = matrix.inverted_safe()
    offset = context.scene.retopo_tool.offset

    def snap(co):
        location, normal, _, _ = tree.find_nearest(matrix @ co)
        if location is None:
            raise ValueError("Reference collection has no surface to snap to")
        screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, location)
        if screen is None:
            return co.copy()
        direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, screen)
        if normal.dot(direction) >= -1e-6 or not visible_from_view(context, location, tree):
            return co.copy()
        return inverse @ (location + normal * offset)
    return snap


def apply_draw_click(context, bm, anchor, hit, point=None, edge_factor=0.5,
                     bridge_mode=False, project=None, snapped_points=None):
    """Apply one Draw click using the same operation as its preview."""
    settings = context.scene.retopo_tool
    return topology.apply_click(
        bm, anchor, hit, point, edge_factor, bridge_mode, project,
        settings.quad_only, quad_snap_distance(context), snapped_points,
        auto_reselect=getattr(settings, 'auto_reselect', True), allow_cut=False)


def commit_draw_click(context, bm, anchor, hit, point=None, factor=0.5, bridge_mode=False, snap=None):
    settings = context.scene.retopo_tool
    symmetry = symmetry_options(context)
    preview(bm, anchor, hit, point, factor, bridge_mode, strict=True, project=snap,
            quad_only=settings.quad_only, snap_distance=quad_snap_distance(context),
            auto_reselect=getattr(settings, 'auto_reselect', True), allow_cut=False, symmetry=symmetry)
    sides = topology.preferred_sides(click_sides(anchor, hit, point, factor), topology.last_edit_sides(bm))
    counts = mesh_counts(bm)
    snap = symmetry_projector(snap, symmetry, sides)
    result = topology_edit(bm, lambda: apply_draw_click(
        context, bm, anchor, hit, point, factor, bridge_mode, snap), symmetry, sides=sides)
    changed = mesh_counts(bm) != counts or isinstance(result, bmesh.types.BMFace)
    sync_edit(context.edit_object, bm, sides if changed else topology.NO_EDIT_SIDES)


def edge_parameter(context, edge, mouse):
    matrix = context.edit_object.matrix_world
    a, b = [matrix @ v.co for v in edge.verts]
    screen_a, screen_b = [view3d_utils.location_3d_to_region_2d(context.region, context.region_data, p) for p in (a, b)]
    if screen_a is None or screen_b is None:
        return 0.5
    delta = screen_b - screen_a
    factor = max(0.001, min(0.999, (mouse - screen_a).dot(delta) / max(delta.length_squared, 1e-12)))
    if context.region_data is None:
        return factor
    wa, wb = [(context.region_data.perspective_matrix @ p.to_4d()).w for p in (a, b)]
    denominator = (1 - factor) * wb + factor * wa
    return factor * wa / denominator if abs(denominator) > 1e-10 else factor


def edge_drag_positions(context, originals, start, mouse, snap=None):
    matrix = context.edit_object.matrix_world
    depth = matrix @ ((originals[0] + originals[1]) / 2)
    origin = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, start, depth)
    destination = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, mouse, depth)
    delta = matrix.inverted_safe().to_3x3() @ (destination - origin)
    positions = [co + delta for co in originals]
    return [snap(co) for co in positions] if snap is not None else positions


def screen_distance(context, pixels):
    """World-space distance covered by screen pixels at each point's depth."""
    matrix = context.edit_object.matrix_world.copy()
    inverse = matrix.inverted_safe()

    def distance(co):
        if context.region is None:
            return 0.0
        world = matrix @ co
        try:
            screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, world)
            if screen is None:
                return 0.0
            displaced = view3d_utils.region_2d_to_location_3d(
                context.region, context.region_data, screen + Vector((pixels, 0)), world)
        except (AttributeError, TypeError, ValueError, ReferenceError):
            return 0.0
        return (inverse @ displaced - co).length
    return distance


def quad_snap_distance(context):
    if not context.scene.retopo_tool.snapping:
        return None
    return screen_distance(context, context.scene.retopo_tool.merge_radius * 4)


def weld_distance(context):
    """Weld new strip corners only within the Merge Radius used by vertex dragging."""
    if not context.scene.retopo_tool.snapping:
        return None
    return screen_distance(context, context.scene.retopo_tool.merge_radius)


def symmetry_options(context):
    settings = getattr(getattr(context, 'scene', None), 'retopo_tool', None)
    axes = getattr(settings, 'symmetry_axes', None)
    if axes is None:
        axes = tuple(axis == getattr(settings, 'symmetry_axis', 'X') for axis in 'XYZ')
    axes = tuple(i for i, enabled in enumerate(axes) if enabled)
    return SymmetryOptions(
        bool(axes and getattr(settings, 'x_symmetry', False)), axes,
        settings.center_distance if axes and getattr(settings, 'snap_to_center', False) else 0.0)


def element_sides(element, factor=0.5):
    if isinstance(element, bmesh.types.BMVert):
        return topology.coordinate_sides(element.co)
    if isinstance(element, bmesh.types.BMEdge):
        return topology.coordinate_sides(element.verts[0].co.lerp(element.verts[1].co, factor))
    if isinstance(element, bmesh.types.BMFace):
        return topology.coordinate_sides(element.calc_center_median())
    return topology.NO_EDIT_SIDES


def click_sides(anchor, hit, point=None, factor=0.5):
    sides = topology.coordinate_sides(point) if point is not None else element_sides(hit, factor)
    return topology.preferred_sides(sides, element_sides(anchor))


def mesh_counts(bm):
    return len(bm.verts), len(bm.edges), len(bm.faces)


def symmetry_projector(project, symmetry, sides):
    if project is None or not symmetry.enabled:
        return project

    def snap(co):
        center, reflected = [], []
        for axis in symmetry.axes:
            half = topology.plane_side(co[axis])
            if not half:
                center.append(axis)
            elif half != (sides[axis] or 1):
                reflected.append(axis)
                co = topology.mirrored_coordinate(co, axis)
        snapped = project(co)
        for axis in reflected:
            snapped = topology.mirrored_coordinate(snapped, axis)
        return topology.center_coordinates(snapped, center, float('inf'))
    return snap


def topology_edit(bm, edit, symmetry=NO_SYMMETRY, *, pairs=None, sides=topology.NO_EDIT_SIDES):
    state = topology.edit_side_state(bm)

    def edit_with_center_snap():
        previous = set(bm.verts)
        result = edit()
        topology.snap_new_vertices(bm, previous, symmetry.axes, symmetry.center_distance)
        return result

    def apply(axes):
        if not axes:
            return edit_with_center_snap() if symmetry.center_distance else edit()
        axis = axes[0]
        partners = pairs.get(axis) if pairs is not None else None
        return topology.mirror_edit(bm, lambda: apply(axes[1:]), partners, sides[axis], axis)

    result = apply(symmetry.axes if symmetry.enabled else ())
    topology.restore_edit_side_state(bm, state)
    return result


def checked_topology_edit(bm, edit, elements, *, symmetry, sides=topology.NO_EDIT_SIDES, pairs=None):
    """Check a mirrored weld or edge split on a copy before committing either side."""
    if symmetry.enabled or symmetry.center_distance:
        bm.verts.index_update()
        bm.edges.index_update()
        scratch = bm.copy()
        try:
            scratch.verts.ensure_lookup_table()
            scratch.edges.ensure_lookup_table()
            mapped = [None if elem is None else scratch.verts[elem.index]
                      if isinstance(elem, bmesh.types.BMVert) else scratch.edges[elem.index]
                      for elem in elements]
            topology_edit(scratch, lambda: edit(scratch, *mapped), symmetry, pairs=pairs, sides=sides)
        finally:
            scratch.free()
    return topology_edit(bm, lambda: edit(bm, *elements), symmetry, pairs=pairs, sides=sides)


def remember_mirror_move(stroke, context, bm, element=None, factor=0.5):
    stroke.move_symmetry = symmetry_options(context)
    stroke.move_sides = element_sides(element, factor)
    if stroke.move_symmetry.enabled:
        stroke.mirror_move_originals = {i: v.co.copy() for i, v in enumerate(bm.verts)}
        stroke.mirror_move_pairs = {
            axis: topology.mirror_vertex_indices(stroke.mirror_move_originals, axis)
            for axis in stroke.move_symmetry.axes}
        stroke.center_candidates = set()


def move_symmetry(stroke):
    return getattr(stroke, 'move_symmetry', NO_SYMMETRY)


def set_move_positions(stroke, bm, positions, center_exempt=()):
    symmetry = move_symmetry(stroke)
    if symmetry.center_distance:
        positions = {vertex: co if vertex in center_exempt else
                     topology.center_coordinates(co, symmetry.axes, symmetry.center_distance)
                     for vertex, co in positions.items()}
    pairs = getattr(stroke, 'mirror_move_pairs', None)
    if pairs is None:
        for vertex, co in positions.items():
            vertex.co = co
        return
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    indexed = {vertex.index: co for vertex, co in positions.items()}
    for axis, partners in pairs.items():
        indexed = topology.mirrored_positions(indexed, partners, stroke.mirror_move_originals,
                                              stroke.move_sides[axis], axis)
    for index, co in indexed.items():
        bm.verts[index].co = co
        if symmetry.center_distance and any(
                co[axis] == 0 and partners.get(index, index) != index for axis, partners in pairs.items()):
            stroke.center_candidates.add(index)


def mirror_indices(stroke, indices):
    indices = set(indices)
    for partners in getattr(stroke, 'mirror_move_pairs', {}).values():
        indices.update(partners[index] for index in tuple(indices) if index in partners)
    return indices


def mirror_exclusions(stroke, bm, verts):
    pairs = getattr(stroke, 'mirror_move_pairs', None)
    if not pairs:
        return ()
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    return {bm.verts[index] for index in mirror_indices(stroke, (v.index for v in verts))} - set(verts)


def finish_position_move(obj, bm, stroke, sides):
    candidates = set(getattr(stroke, 'center_candidates', ()))
    weld = {}
    if candidates:
        bm.verts.ensure_lookup_table()
        pairs = stroke.mirror_move_pairs
        originals = stroke.mirror_move_originals
        while candidates:
            start = candidates.pop()
            co = bm.verts[start].co
            group = [start]
            for index in group:
                for axis, partners in pairs.items():
                    partner = partners.get(index)
                    if co[axis] == 0 and partner in candidates and bm.verts[partner].co == co:
                        candidates.remove(partner)
                        group.append(partner)
            if len(group) > 1:
                target = max(group, key=lambda index: (
                    sum(topology.plane_side(originals[index][axis]) == (sides[axis] or 1)
                        for axis in pairs), -index))
                weld.update((bm.verts[index], bm.verts[target]) for index in group if index != target)
    if weld:
        state = topology.edit_side_state(bm)
        bmesh.ops.weld_verts(bm, targetmap=weld)
        topology.restore_edit_side_state(bm, state)
    sync_edit(obj, bm, sides, destructive=bool(weld))


def restore_move(stroke, bm, positions):
    pairs = getattr(stroke, 'mirror_move_pairs', {})
    originals = getattr(stroke, 'mirror_move_originals', {})
    for vertex, co in positions.items():
        vertex.co = co
    if pairs:
        bm.verts.index_update()
        for index in mirror_indices(stroke, (vertex.index for vertex in positions)):
            bm.verts[index].co = originals[index]


def movement_sides(stroke, originals):
    if hasattr(stroke, 'move_sides'):
        return stroke.move_sides
    return topology.coordinate_sides(sum(originals, Vector()) / len(originals))


def sync_edit(obj, bm, sides, destructive=True):
    topology.record_edit_sides(bm, sides)
    if destructive:
        sync(obj)
    else:
        sync(obj, destructive=False)


def preview(bm, anchor, hit, point, edge_factor=0.5, bridge_mode=False, with_kind=False, strict=False, project=None, quad_only=False, snap_distance=None, snapped_points=None, auto_reselect=True, allow_cut=True, symmetry=NO_SYMMETRY):
    """Run the actual operation on a disposable mesh so preview and commit agree."""
    copy = bm.copy()
    try:
        def mapped(element):
            if element is None:
                return None
            source, destination = (bm.verts, copy.verts) if isinstance(element, bmesh.types.BMVert) else (bm.edges, copy.edges)
            destination.ensure_lookup_table()
            return destination[list(source).index(element)]

        anchor, hit = mapped(anchor), mapped(hit)
        sides = topology.preferred_sides(click_sides(anchor, hit, point, edge_factor),
                                          topology.last_edit_sides(copy))
        project = symmetry_projector(project, symmetry, sides)
        result = topology_edit(copy, lambda: topology.apply_click(
            copy, anchor, hit, point, edge_factor, bridge_mode, project, quad_only, snap_distance,
            snapped_points, auto_reselect=auto_reselect, allow_cut=allow_cut), symmetry, sides=sides)
        if isinstance(result, list):
            kind = 'LINES'
            coords = [v.co.copy() for edge in result for v in edge.verts]
        else:
            verts = [result] if isinstance(result, bmesh.types.BMVert) else result.verts
            kind = 'FACE' if isinstance(result, bmesh.types.BMFace) else ('POINTS' if len(verts) == 1 else 'LINES')
            coords = [v.co.copy() for v in verts]
        return (kind, coords) if with_kind else coords
    except (ValueError, RuntimeError):
        if snapped_points is not None:
            snapped_points.clear()
        if strict:
            raise
        return ('POINTS', []) if with_kind else []
    finally:
        copy.free()


def copy_preview_data(value):
    if isinstance(value, Vector):
        return value.copy()
    if isinstance(value, (list, tuple)):
        return [copy_preview_data(item) for item in value]
    return value


def cached_hover_preview(key, compute, snapped_points=None):
    """Reuse the Ctrl-click preview while the pointer, view, and mesh are stable."""
    global _hover_preview_cache
    if _hover_preview_cache is None or _hover_preview_cache[0] != key:
        kind, coords = compute()
        _hover_preview_cache = (key, kind, copy_preview_data(coords),
                                [co.copy() for co in (snapped_points or ())])
        return kind, copy_preview_data(coords)
    _, kind, coords, snapped = _hover_preview_cache
    if snapped_points is not None:
        snapped_points.extend(co.copy() for co in snapped)
    return kind, copy_preview_data(coords)


def preview_element_key(element):
    if isinstance(element, bmesh.types.BMVert):
        return 'VERT', tuple(element.co)
    if isinstance(element, bmesh.types.BMEdge):
        return 'EDGE', tuple(sorted(tuple(vertex.co) for vertex in element.verts))
    return None


def sync(obj, destructive=True):
    global _grid_preview_cache, _screen_cache, _hole_geometry_cache, _hole_screen_cache, _hover_preview_cache
    _grid_preview_cache = None
    _screen_cache = None
    _hole_geometry_cache = None
    _hole_screen_cache = None
    _hover_preview_cache = None
    _visibility_cache.pop(('edit', object_key(obj)), None)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.normal_update()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=destructive)


def strip_sides(bm, edge, points, target):
    end = topology.coordinate_sides(points[-1]) if points else topology.NO_EDIT_SIDES
    return topology.preferred_sides(element_sides(edge), end, element_sides(target),
                                    topology.last_edit_sides(bm))


def strip_preview(bm, edge, points, snap=None, target=None, snap_distance=None, snapped_points=None, triangle=False, symmetry=NO_SYMMETRY):
    bm.edges.index_update()
    copy = bm.copy()
    try:
        copy.edges.ensure_lookup_table()
        source = copy.edges[edge.index]
        destination = copy.edges[target.index] if target is not None else None
        sides = strip_sides(copy, source, points, destination)
        snap = symmetry_projector(snap, symmetry, sides)
        faces = topology_edit(copy, lambda: topology.quad_strip(
            copy, source, points, snap, destination, snap_distance, snapped_points, triangle),
            symmetry, sides=sides)
        return [[v.co.copy() for v in face.verts] for face in faces]
    finally:
        copy.free()


def loop_cut_preview(bm, edge, factor, project=None, symmetry=NO_SYMMETRY):
    """Run the actual loop-cut operation on a copy for hover/click previews."""
    bm.edges.index_update()
    copy = bm.copy()
    try:
        copy.edges.ensure_lookup_table()
        source = copy.edges[edge.index]
        sides = topology.preferred_sides(element_sides(source, factor), topology.last_edit_sides(copy))
        project = symmetry_projector(project, symmetry, sides)
        cuts = topology_edit(copy, lambda: topology.loop_cut(copy, source, factor, project),
                             symmetry, sides=sides)
        return [vertex.co.copy() for cut in cuts for vertex in cut.verts]
    finally:
        copy.free()


def knife_target(context, bm, mouse):
    """Snap precise knife clicks to vertices; otherwise favor the edge itself."""
    vertex = pick(context, bm, mouse, vertices_only=True,
                  radius=min(4.0, context.scene.retopo_tool.radius))
    return vertex if vertex is not None else pick(context, bm, mouse, edges_only=True)


def knife_anchor(bm):
    anchor = topology.active(bm)
    return anchor if isinstance(anchor, bmesh.types.BMVert) else None


def crossed_cut_edge(context, bm, start, end):
    """First visible edge crossed between events, even during a fast drag."""
    delta = end - start
    if delta.length_squared <= 1e-12:
        return None, None
    index = screen_index(context, bm)
    _, entries = screen_candidates(index, (start + end) * 0.5, delta.length * 0.5 + 1.0)
    points = index[0]
    candidates = []
    for edge_index, a_index, b_index in entries:
        a, b = points[a_index], points[b_index]
        if a is None or b is None:
            continue
        span = b - a
        denominator = delta.x * span.y - delta.y * span.x
        if abs(denominator) <= 1e-10:
            continue
        offset = a - start
        t = (offset.x * span.y - offset.y * span.x) / denominator
        u = (offset.x * delta.y - offset.y * delta.x) / denominator
        if 1e-6 < t <= 1.0 and 0.0 <= u <= 1.0:
            candidates.append((t, edge_index, start + delta * t))
    bm.edges.ensure_lookup_table()
    for _, edge_index, point in sorted(candidates, key=lambda candidate: candidate[:2]):
        hit = pick(context, bm, point, edges_only=True, radius=1.0)
        if hit is not None and hit == bm.edges[edge_index]:
            return hit, point
    return None, None


def apply_knife_cut(bm, anchor, hit, factor=0.5, project=None):
    if hit is None:
        raise ValueError("Alt-click a vertex or edge to cut")
    return topology.apply_click(bm, anchor, hit, edge_factor=factor, project=project)


def knife_preview(bm, anchor, hit, factor=0.5, project=None, strict=False, symmetry=NO_SYMMETRY):
    if hit is None:
        if strict:
            raise ValueError("Alt-click a vertex or edge to cut")
        return 'POINTS', []
    return preview(bm, anchor, hit, None, factor, with_kind=True, strict=strict, project=project,
                   symmetry=symmetry)


class RP_OT_alt_cut(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_alt_cut"
    bl_label = "TopoPot Knife / Loop Cut"
    bl_description = "Alt-click inserts/connects knife points; Alt-drag slides a loop cut along its source edge"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW'
                and context.scene.retopo_tool.mode in {'RETOPO', 'GRID'})

    def invoke(self, context, event):
        global _knife_preview, _loop_preview
        track_mouse(context, event)
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        hit = knife_target(context, bm, mouse)
        edge = hit if isinstance(hit, bmesh.types.BMEdge) else pick(context, bm, mouse, edges_only=True)
        anchor = knife_anchor(bm)
        bm.verts.index_update()
        bm.edges.index_update()
        self.obj, self.area, self.region = context.edit_object, context.area, context.region
        self.cut_mode = context.scene.retopo_tool.mode
        self.cut_start = mouse.copy()
        self.cut_last = mouse.copy()
        self.cut_counts = (len(bm.verts), len(bm.edges), len(bm.faces))
        self.cut_anchor_index = anchor.index if anchor is not None else None
        self.cut_hit_kind = ('VERT' if isinstance(hit, bmesh.types.BMVert) else
                             'EDGE' if isinstance(hit, bmesh.types.BMEdge) else None)
        self.cut_hit_index = hit.index if hit is not None else None
        self.cut_factor = edge_parameter(context, hit, mouse) if isinstance(hit, bmesh.types.BMEdge) else 0.5
        self.press_edge_index = edge.index if edge is not None else None
        self.cut_projector = reference_projector(context)
        self.cut_gesture, self.dragging = True, False
        self.loop_edge_index, self.loop_factor, self.cut_error = None, None, None
        _loop_preview = None
        self.cut_symmetry = symmetry_options(context)
        kind, coords = knife_preview(bm, anchor, hit, self.cut_factor, self.cut_projector,
                                     symmetry=self.cut_symmetry)
        _knife_preview = (self.region.as_pointer(), kind, coords)
        self.area.header_text_set("Knife | Release to insert/connect | Drag over an edge: loop cut | Esc cancels")
        self.area.tag_redraw()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global _knife_preview, _loop_preview
        if (context.mode != 'EDIT_MESH' or context.edit_object != self.obj
                or context.scene.retopo_tool.mode != self.cut_mode
                or event.type in {'ESC', 'RIGHTMOUSE'}):
            RP_OT_alt_cut.end(self, context)
            if self.cut_mode == 'GRID' and event.type in {'ESC', 'RIGHTMOUSE'}:
                return {'FINISHED'}
            return {'CANCELLED'}
        track_mouse(context, event)
        bm = bmesh.from_edit_mesh(self.obj.data)
        if (len(bm.verts), len(bm.edges), len(bm.faces)) != self.cut_counts:
            self.report({'WARNING'}, "Cut canceled because mesh topology changed")
            RP_OT_alt_cut.end(self, context)
            return {'CANCELLED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        if event.type not in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and not release:
            return {'RUNNING_MODAL'}
        mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        symmetry = self.cut_symmetry
        self.dragging |= (mouse - self.cut_start).length >= ALT_DRAG_DISTANCE
        if self.dragging:
            _knife_preview = None
            if self.loop_edge_index is None and self.cut_error is None:
                bm.edges.ensure_lookup_table()
                # Prefer the pressed edge. A drag begun off an edge can acquire
                # its first crossed edge; after acquisition it never retargets.
                if self.press_edge_index is not None:
                    edge = bm.edges[self.press_edge_index]
                else:
                    edge, _ = crossed_cut_edge(context, bm, self.cut_last, mouse)
                    if edge is None:
                        edge = pick(context, bm, mouse, edges_only=True)
                if edge is not None:
                    self.loop_edge_index = edge.index
            if self.loop_edge_index is not None:
                bm.edges.ensure_lookup_table()
                edge = bm.edges[self.loop_edge_index]
                try:
                    # Keep the source edge, but slide freely with the current
                    # mouse position, including the final release event.
                    self.loop_factor = edge_parameter(context, edge, mouse)
                    coords = loop_cut_preview(bm, edge, self.loop_factor, self.cut_projector, symmetry)
                    _loop_preview = (self.region.as_pointer(), coords)
                    self.cut_error = None
                except (ValueError, RuntimeError) as error:
                    _loop_preview = None
                    self.cut_error = str(error)
            self.area.header_text_set("Loop Cut | Move mouse to slide | Release to cut | Esc / right-click cancels")
        self.cut_last = mouse.copy()
        if release:
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            try:
                counts = mesh_counts(bm)
                previous_sides = topology.last_edit_sides(bm)
                if self.dragging:
                    if self.loop_edge_index is None or self.cut_error is not None:
                        raise ValueError(self.cut_error or "Drag over an edge of a quad strip")
                    edge = bm.edges[self.loop_edge_index]
                    sides = topology.preferred_sides(element_sides(edge, self.loop_factor), previous_sides)
                    project = symmetry_projector(self.cut_projector, symmetry, sides)
                    topology_edit(bm, lambda: topology.loop_cut(
                        bm, edge, self.loop_factor, project), symmetry, sides=sides)
                else:
                    anchor = bm.verts[self.cut_anchor_index] if self.cut_anchor_index is not None else None
                    hit = (bm.verts[self.cut_hit_index] if self.cut_hit_kind == 'VERT' else
                           bm.edges[self.cut_hit_index] if self.cut_hit_kind == 'EDGE' else None)
                    sides = topology.preferred_sides(click_sides(anchor, hit, factor=self.cut_factor), previous_sides)
                    knife_preview(bm, anchor, hit, self.cut_factor, self.cut_projector, strict=True,
                                  symmetry=symmetry)
                    project = symmetry_projector(self.cut_projector, symmetry, sides)
                    topology_edit(bm, lambda: apply_knife_cut(
                        bm, anchor, hit, self.cut_factor, project), symmetry, sides=sides)
                sync_edit(self.obj, bm, sides if mesh_counts(bm) != counts else topology.NO_EDIT_SIDES)
            except (ValueError, RuntimeError) as error:
                self.report({'WARNING'}, str(error))
                RP_OT_alt_cut.end(self, context)
                return {'CANCELLED'}
            RP_OT_alt_cut.end(self, context)
            return {'FINISHED'}
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def end(self, context):
        global _knife_preview, _loop_preview
        _knife_preview = _loop_preview = None
        self.area.header_text_set(GRID_HINT if context.scene.retopo_tool.mode == 'GRID' else None)
        self.area.tag_redraw()


def point_in_polygon(point, polygon):
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a.y > point.y) != (b.y > point.y):
            crossing = a.x + (point.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if point.x < crossing:
                inside = not inside
    return inside


def polygon_normal(coords):
    normal = sum((a.cross(b) for a, b in zip(coords, coords[1:] + coords[:1])), Vector())
    return normal.normalized() if normal.length > 1e-10 else normal


def fill_mesh_signature(context, bm):
    obj = context.edit_object
    return (object_key(obj), object_key(obj.data), tuple(value for row in obj.matrix_world for value in row),
            mesh_counts(bm))


def fill_view_signature(context):
    region = context.region
    view = getattr(context.region_data, 'perspective_matrix', None)
    return (object_key(region), getattr(region, 'width', None), getattr(region, 'height', None),
            tuple(value for row in view for value in row) if view is not None else None)


def boundary_geometry(context, bm, signature):
    global _hole_geometry_cache
    if _hole_geometry_cache is not None and _hole_geometry_cache[0] == signature:
        return _hole_geometry_cache[1]
    matrix = context.edit_object.matrix_world
    bm.verts.index_update()
    loops = []
    for loop in topology.boundary_loops(bm):
        indices = tuple(vertex.index for vertex in loop)
        world = [matrix @ vertex.co for vertex in loop]
        normal = polygon_normal(world)
        if normal.length < 1e-10:
            continue
        adjacent = {bm.edges.get((a, b)).link_faces[0]
                    for a, b in zip(loop, loop[1:] + loop[:1])}
        surrounding = sum((polygon_normal([matrix @ vertex.co for vertex in face.verts])
                           for face in adjacent), Vector())
        if normal.dot(surrounding) >= -1e-5:
            loops.append((indices, world, normal, sum(world, Vector()) / len(world)))
    _hole_geometry_cache = signature, loops
    return loops


class ProjectedRim(NamedTuple):
    indices: tuple
    world: list
    normal: Vector
    center: Vector
    screen: list
    bounds: tuple
    area: float


def boundary_screen_loops(context, bm):
    """Project cached mesh rims, keeping bounds and area for cursor queries."""
    global _hole_screen_cache
    mesh_signature = fill_mesh_signature(context, bm)
    signature = (*mesh_signature, fill_view_signature(context))
    if _hole_screen_cache is not None and _hole_screen_cache[0] == signature:
        return _hole_screen_cache[1]
    loops = []
    for indices, world, normal, center in boundary_geometry(context, bm, mesh_signature):
        screen = [view3d_utils.location_3d_to_region_2d(context.region, context.region_data, co) for co in world]
        if any(point is None for point in screen):
            continue
        xs, ys = zip(*screen)
        area = abs(sum(a.x * b.y - b.x * a.y for a, b in zip(screen, screen[1:] + screen[:1])))
        loops.append(ProjectedRim(indices, world, normal, center, screen, (min(xs), max(xs), min(ys), max(ys)), area))
    _hole_screen_cache = signature, loops
    return loops


def grid_visibility_trees(context, bm, cache):
    if 'trees' not in cache:
        cache['trees'] = cached_edit_tree(context, bm), reference_tree(context)
    return cache['trees']


def grid_rim_visible(context, rim, tree, reference, cache):
    visibility = cache['rim_visibility']
    if rim.indices not in visibility:
        visibility[rim.indices] = all(
            visible_from_view(context, co, tree) and
            (reference is None or visible_from_view(context, co, reference, reference_pick_tolerance(context, co)))
            for co in rim.world)
    return visibility[rim.indices]


def hole_at_mouse(context, bm, mouse, cache=None):
    """Choose the nearest front-facing rim containing the cursor in screen space."""
    cache = grid_preview_cache(context, bm) if cache is None else cache
    candidates = []
    origin, direction = None, None
    loops = boundary_screen_loops(context, bm)
    for rim in loops:
        x0, x1, y0, y1 = rim.bounds
        if not (x0 <= mouse.x <= x1 and y0 <= mouse.y <= y1) or not point_in_polygon(mouse, rim.screen):
            continue
        if origin is None:
            origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, mouse)
            direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, mouse)
        denominator = rim.normal.dot(direction)
        # Outer borders traverse the other way; they must not cover existing faces.
        if denominator >= -1e-6:
            continue
        distance = (rim.center - origin).dot(rim.normal) / denominator
        if distance <= 0:
            continue
        tree, reference = grid_visibility_trees(context, bm, cache)
        location, _, _, hit_distance = tree.ray_cast(origin, direction)
        if location is not None and hit_distance <= distance + 1e-5:
            continue
        if not grid_rim_visible(context, rim, tree, reference, cache):
            continue
        candidates.append((distance, rim.area, rim.indices))
    if not candidates:
        return None
    bm.verts.ensure_lookup_table()
    return [bm.verts[index] for index in min(candidates, key=lambda item: item[:2])[2]]


def grid_preview(bm, vertices, snap=None, quad_only=False, symmetry=NO_SYMMETRY, scratch=None):
    if scratch is None:
        copy = bm.copy()
        copy_verts, originals = tuple(copy.verts), None
    else:
        copy, copy_verts, originals = scratch
    try:
        bm.verts.index_update()
        rim = [copy_verts[v.index] for v in vertices]
        sides = rim_edit_sides(copy, rim)
        snap = symmetry_projector(snap, symmetry, sides)
        faces = topology_edit(
            copy, lambda: topology.fill_hole(copy, rim, snap, quad_only, finalize=False, originals=originals),
            symmetry, sides=sides)
        try:
            return [[v.co.copy() for v in face.verts] for face in faces]
        finally:
            if scratch is not None:
                topology.remove_preview_fill(copy, faces, originals)
    finally:
        if scratch is None:
            copy.free()


_grid_preview_cache = None
_grid_drag_region = None


def fill_quads_only(context):
    return not getattr(context.window_manager, 'retopo_tool_ctrl', False)


def rim_edit_sides(bm, vertices):
    center = sum((v.co for v in vertices), Vector()) / len(vertices)
    return topology.preferred_sides(topology.coordinate_sides(center), topology.last_edit_sides(bm))


def grid_preview_cache(context, bm):
    global _grid_preview_cache
    settings = context.scene.retopo_tool
    target = settings.target
    reference_ids = {key for obj in getattr(target, 'all_objects', ()) if obj.type == 'MESH'
                     for key in (object_key(obj), object_key(obj.data))}
    view = fill_view_signature(context)
    # Snapping checks facing and occlusion from the current view. Without
    # reference snapping, the fill geometry stays valid through camera moves.
    snap_view = view if settings.snapping and reference_ids else None
    signature = (*fill_mesh_signature(context, bm), settings.snapping,
                 object_key(target), frozenset(reference_ids), settings.offset,
                 symmetry_options(context), snap_view)
    if _grid_preview_cache is None or _grid_preview_cache['signature'] != signature:
        _grid_preview_cache = {'signature': signature, 'reference_ids': reference_ids, 'rims': {}}
    cache = _grid_preview_cache
    if cache.get('view') != view:
        for key in ('trees', 'visible_rims', 'visible', 'visible_mode', 'overlay'):
            cache.pop(key, None)
        cache['view'] = view
        cache['rim_visibility'] = {}
    return cache


def cached_grid_rim(context, bm, vertices, cache, scratch=None, snap=_UNSET):
    bm.verts.index_update()
    quad_only = fill_quads_only(context)
    key = quad_only, tuple(sorted(v.index for v in vertices))
    if key not in cache['rims']:
        polygons, error = [], None
        try:
            if snap is _UNSET:
                snap = reference_projector(context)
            polygons = grid_preview(bm, vertices, snap, quad_only, symmetry_options(context), scratch)
        except (ValueError, RuntimeError) as exception:
            error = str(exception)
        cache['rims'][key] = polygons, [v.co.copy() for v in vertices], error
    return cache['rims'][key]


def grid_hover_preview(context, bm, mouse):
    cache = grid_preview_cache(context, bm)
    vertices = hole_at_mouse(context, bm, mouse, cache)
    return cached_grid_rim(context, bm, vertices, cache) if vertices is not None else ([], [], None)


def grid_visible_rims(context, bm, cache):
    if 'visible_rims' in cache:
        return cache['visible_rims']
    rims = []
    for rim in boundary_screen_loops(context, bm):
        x0, x1, y0, y1 = rim.bounds
        if x1 < 0 or x0 > context.region.width or y1 < 0 or y0 > context.region.height:
            continue
        center = sum(rim.screen, Vector((0, 0))) / len(rim.screen)
        direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, center)
        denominator = rim.normal.dot(direction)
        if denominator >= -1e-6:
            continue
        tree, reference = grid_visibility_trees(context, bm, cache)
        if not grid_rim_visible(context, rim, tree, reference, cache):
            continue
        if point_in_polygon(center, rim.screen):
            origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, center)
            distance = (rim.center - origin).dot(rim.normal) / denominator
            location, _, _, hit_distance = tree.ray_cast(origin, direction)
            if distance <= 0 or (location is not None and hit_distance <= distance + 1e-5):
                continue
        rims.append(rim)
    cache['visible_rims'] = rims
    return rims


def grid_preview_scratch(bm, rims):
    """Copy once and keep the geometry Blender can consult for the pending fills."""
    copy = bm.copy()
    try:
        vertices = tuple(copy.verts)
        rim_vertices = {index: vertices[index] for rim in rims for index in rim.indices}
        keep = set(rim_vertices.values())
        rail = list(keep)
        while rail:
            vertex = rail.pop()
            # Keep every face at each rail vertex. Cutting a face along a rail
            # would turn an interior edge into a new boundary shortcut.
            for edge in vertex.link_edges:
                other = edge.other_vert(vertex)
                if not edge.hide and len(edge.link_faces) <= 1 and other not in keep:
                    keep.add(other)
                    rail.append(other)
        keep.update(corner for vertex in tuple(keep) for face in vertex.link_faces for corner in face.verts)
        outside = [vertex for vertex in vertices if vertex not in keep]
        if outside:
            bmesh.ops.delete(copy, geom=outside, context='VERTS')
        originals = set(copy.verts), set(copy.edges), set(copy.faces)
        return copy, rim_vertices, originals
    except Exception:
        copy.free()
        raise


def grid_visible_previews(context, bm):
    """All front-facing, unobscured holes, independent of the mouse position."""
    cache = grid_preview_cache(context, bm)
    quad_only = fill_quads_only(context)
    if cache.get('visible_mode') == quad_only:
        return cache['visible']
    previews = []
    rims = [(rim, (quad_only, tuple(sorted(rim.indices)))) for rim in grid_visible_rims(context, bm, cache)]
    symmetry = symmetry_options(context)
    scratch = None
    snap = _UNSET
    bm.verts.ensure_lookup_table()
    try:
        if not symmetry.enabled and not symmetry.center_distance:
            pending = [rim for rim, key in rims if key not in cache['rims']]
            if pending:
                scratch = grid_preview_scratch(bm, pending)
        for rim, key in rims:
            if key in cache['rims']:
                previews.append(cache['rims'][key])
                continue
            if snap is _UNSET:
                snap = reference_projector(context)
            previews.append(cached_grid_rim(context, bm, [bm.verts[i] for i in rim.indices], cache, scratch, snap))
    finally:
        if scratch is not None:
            scratch[0].free()
    cache['visible'] = previews
    cache['visible_mode'] = quad_only
    cache.pop('overlay', None)
    return previews


def fill_drag_snapshot(stroke, context, bm):
    matrix = tuple(value for row in context.edit_object.matrix_world for value in row)
    signature = fill_view_signature(context), matrix
    if not hasattr(stroke, 'fill_pick_snapshot'):
        stroke.fill_pick_snapshot = signature, (screen_index(context, bm), cached_edit_tree(context, bm))
    view, snapshot = stroke.fill_pick_snapshot
    return snapshot if view == signature else None


def fill_drag_target(context, bm, mouse, vertex, original, mirrored=(), snapshot=None):
    # The moving vertex can temporarily cover the target with its own faces.
    # Test visibility against the shape before the drag, including reference tolerance.
    excluded = {vertex, *mirrored} if mirrored else vertex
    radius = context.scene.retopo_tool.merge_radius
    if snapshot is not None:
        return pick(context, bm, mouse, exclude=excluded, vertices_only=True,
                    radius=radius, relaxed_reference=True, snapshot=snapshot)
    current = vertex.co.copy()
    key = ('edit', object_key(context.edit_object))
    try:
        vertex.co = original
        _visibility_cache.pop(key, None)
        return pick(context, bm, mouse, exclude=excluded, vertices_only=True, radius=radius, relaxed_reference=True)
    finally:
        vertex.co = current
        _visibility_cache.pop(key, None)


def validated_edge_targets(bm, source, target, positions):
    mapping = topology.edge_merge_map(bm, source, target, positions)
    bm.edges.index_update()
    scratch = bm.copy()
    try:
        scratch.edges.ensure_lookup_table()
        topology.merge_edges(scratch, scratch.edges[source.index], scratch.edges[target.index], positions)
    finally:
        scratch.free()
    return mapping


def neighboring_edge_targets(context, bm, verts, positions, excluded=(), snapshot=None):
    """Acquire a whole neighboring edge by the dragged edge's screen center."""
    source = bm.edges.get(verts)
    if source is None:
        return {}
    by_vertex = dict(zip(verts, positions))
    ordered_positions = [by_vertex[v] for v in source.verts]
    matrix = context.edit_object.matrix_world
    center = view3d_utils.location_3d_to_region_2d(
        context.region, context.region_data, matrix @ ((positions[0] + positions[1]) * 0.5))
    if center is None:
        return {}
    candidates = {e for v in verts for link in v.link_edges for w in link.verts for e in w.link_edges}
    candidates.update(e for face in source.link_faces for e in face.edges)
    ranked = []
    for edge in candidates:
        if any(v in excluded for v in edge.verts):
            continue
        try:
            topology.edge_merge_map(bm, source, edge, ordered_positions)
        except ValueError:
            continue
        a, b = [view3d_utils.location_3d_to_region_2d(context.region, context.region_data, matrix @ v.co)
                for v in edge.verts]
        if a is None or b is None:
            continue
        delta = b-a
        factor = max(0, min(1, (center-a).dot(delta) / max(delta.length_squared, 1e-12)))
        distance = (center-a-factor*delta).length
        if distance < context.scene.retopo_tool.merge_radius:
            ranked.append((distance, edge, factor))
    if not ranked:
        return {}
    tree = snapshot[1] if snapshot is not None else cached_edit_tree(context, bm)
    reference = reference_tree(context)
    for _, edge, factor in sorted(ranked, key=lambda item: item[0]):
        co = matrix @ edge.verts[0].co.lerp(edge.verts[1].co, factor)
        if (not visible_from_view(context, co, tree) or
                (reference is not None and not visible_from_view(
                    context, co, reference, reference_pick_tolerance(context, co)))):
            continue
        try:
            return validated_edge_targets(bm, source, edge, ordered_positions)
        except (ValueError, RuntimeError):
            continue
    return {}


def edge_merge_targets(context, bm, verts, originals, positions, merge=True, mirrored=(), snapshot=None):
    """Prefer a neighboring edge, then individual vertices within Merge Radius."""
    settings = context.scene.retopo_tool
    if not merge:
        return {}
    matrix = context.edit_object.matrix_world
    key = ('edit', object_key(context.edit_object))
    current = [vertex.co.copy() for vertex in verts]
    targets = {}
    try:
        # The moving edge can cover the target with faces at its dragged shape.
        # Test visibility against the mesh before the drag.
        for vertex, original in zip(verts, originals):
            vertex.co = original
        _visibility_cache.pop(key, None)
        targets = neighboring_edge_targets(context, bm, verts, positions, mirrored, snapshot)
        if targets:
            return targets
        for vertex, position in zip(verts, positions):
            screen = view3d_utils.location_3d_to_region_2d(
                context.region, context.region_data, matrix @ position)
            if screen is None:
                continue
            target = pick(context, bm, screen, exclude={*verts, *mirrored} if mirrored else verts, vertices_only=True,
                          radius=settings.merge_radius, relaxed_reference=True, snapshot=snapshot)
            if target is not None:
                targets[vertex] = target
    finally:
        for vertex, co in zip(verts, current):
            vertex.co = co
        _visibility_cache.pop(key, None)
    # Two ends snapped to one vertex would collapse the dragged edge and its
    # attached faces. Leave both ends at their dragged positions in that case.
    owners = {}
    duplicates = set()
    for source, target in targets.items():
        if target in owners:
            duplicates.update((source, owners[target]))
        else:
            owners[target] = source
    targets = {source: target for source, target in targets.items() if source not in duplicates}
    if len(targets) == 2:
        source, target = bm.edges.get(verts), bm.edges.get([targets[v] for v in verts])
        if source is not None and target is not None:
            try:
                by_vertex = dict(zip(verts, positions))
                return validated_edge_targets(bm, source, target, [by_vertex[v] for v in source.verts])
            except (ValueError, RuntimeError):
                return {}
    return targets


def merge_edge_targets(bm, verts, targets):
    """Weld dragged edge endpoints into their highlighted merge targets."""
    if len(targets) == 2:
        source = bm.edges.get(verts)
        target = bm.edges.get([targets[v] for v in verts])
        if source is not None and target is not None:
            topology.merge_edges(bm, source, target)
            return
    claimed = set()
    for vertex in verts:
        target = targets.get(vertex)
        if (target is None or target == vertex or not vertex.is_valid
                or not target.is_valid or target.hide or target in claimed):
            continue
        claimed.add(target)
        topology.merge_vertices(bm, vertex, target)


def commit_move_weld(bm, stroke, verts, targets, sides, quad_only=False):
    pairs = getattr(stroke, 'mirror_move_pairs', None)
    if len(verts) == 1:
        def weld(mesh, vertex, target):
            topology.merge_vertices(mesh, vertex, target, quad_only)
    else:
        def weld(mesh, first, second, first_target, second_target):
            edge_targets = {vertex: target for vertex, target in
                            ((first, first_target), (second, second_target)) if target is not None}
            merge_edge_targets(mesh, [first, second], edge_targets)
    checked_topology_edit(bm, weld, [*verts, *(targets.get(v) for v in verts)],
                          symmetry=move_symmetry(stroke)._replace(center_distance=0),
                          pairs=pairs, sides=sides)


def edge_merge_positions(context, bm, verts, originals, positions, merge=True, mirrored=(), snapshot=None):
    """Snap edge endpoints onto visible merge targets and return the highlights."""
    targets = edge_merge_targets(context, bm, verts, originals, positions, merge, mirrored, snapshot)
    snapped = [targets[vertex].co.copy() if vertex in targets else position
               for vertex, position in zip(verts, positions)]
    return snapped, targets


def fill_triangle_target(context, bm, edge, factor):
    point = context.edit_object.matrix_world @ edge.verts[0].co.lerp(edge.verts[1].co, factor)
    candidates = sorted((v for v in bm.verts if v not in edge.verts and not v.hide),
                        key=lambda v: ((context.edit_object.matrix_world @ v.co) - point).length_squared)
    for vertex in candidates:
        mouse = view3d_utils.location_3d_to_region_2d(context.region, context.region_data,
                                                    context.edit_object.matrix_world @ vertex.co)
        if mouse is not None and pick(context, bm, mouse, vertices_only=True, radius=1) == vertex:
            try:
                topology.edge_triangle_plan(bm, edge, factor, vertex)
                return vertex
            except ValueError:
                pass
    raise ValueError("No nearby visible vertex can form a triangle with this edge")


class RP_OT_grid_fill(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_grid_fill"
    bl_label = "Fill Hole"
    bl_description = "Click vertices or edges to select; drag to reshape; Alt-click for knife, Alt-drag for loop cuts; click inside to fill"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW' and context.scene.retopo_tool.mode == 'GRID')

    def invoke(self, context, event):
        global _merge_target, _edge_merge_targets
        if getattr(event, 'alt', False) and not event.ctrl and not event.shift:
            return RP_OT_alt_cut.invoke(self, context, event)
        _edge_merge_targets = None
        track_mouse(context, event)
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        vertex = pick(context, bm, mouse, vertices_only=True)
        self.edge_drag = False
        if vertex is not None:
            _merge_target = None
            bm.verts.index_update()
            self.obj, self.area, self.region = context.edit_object, context.area, context.region
            self.vertex_index, self.original = vertex.index, vertex.co.copy()
            self.mesh_counts = len(bm.verts), len(bm.edges), len(bm.faces)
            remember_mirror_move(self, context, bm, vertex)
            self.start, self.dragging = mouse, False
            screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data,
                                                        self.obj.matrix_world @ self.original)
            self.grab_offset = screen - mouse if screen is not None else Vector((0, 0))
            self.area.header_text_set("Reshape hole | Drag vertex | Release to keep | Esc / right-click cancels")
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        edge = pick(context, bm, mouse, edges_only=True)
        if edge is not None:
            _merge_target = None
            bm.verts.index_update()
            self.obj, self.area, self.region = context.edit_object, context.area, context.region
            self.edge_drag = True
            self.move_indices = tuple(v.index for v in edge.verts)
            self.move_originals = [v.co.copy() for v in edge.verts]
            self.mesh_counts = len(bm.verts), len(bm.edges), len(bm.faces)
            self.start, self.dragging = mouse, False
            self.edge_ctrl = event.ctrl
            self.edge_factor = edge_parameter(context, edge, mouse)
            remember_mirror_move(self, context, bm, edge, self.edge_factor)
            self.area.header_text_set("Reshape hole | Drag edge | Click: select | Ctrl-click: triangle | Esc / right-click cancels")
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        vertices = hole_at_mouse(context, bm, mouse)
        if vertices is None:
            self.report({'WARNING'}, "Point inside a visible closed hole")
            return {'CANCELLED'}
        try:
            snap = reference_projector(context)
            quad_only = fill_quads_only(context)
            symmetry = symmetry_options(context)
            sides = rim_edit_sides(bm, vertices)
            if symmetry.enabled or symmetry.center_distance:
                _, _, error = cached_grid_rim(context, bm, vertices, grid_preview_cache(context, bm), snap=snap)
                if error:
                    raise ValueError(error)
            snap = symmetry_projector(snap, symmetry, sides)
            topology_edit(bm, lambda: topology.fill_hole(bm, vertices, snap, quad_only),
                          symmetry, sides=sides)
            sync_edit(context.edit_object, bm, sides)
        except (ValueError, RuntimeError) as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        context.area.header_text_set(GRID_HINT)
        context.area.tag_redraw()
        return {'FINISHED'}

    def modal(self, context, event):
        global _merge_target, _grid_drag_region
        if getattr(self, 'cut_gesture', False):
            return RP_OT_alt_cut.modal(self, context, event)
        if getattr(self, 'edge_drag', False):
            return RP_OT_grid_fill.modal_edge(self, context, event)
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            _merge_target = None
            _grid_drag_region = None
            self.area.header_text_set(None)
            return {'CANCELLED'}
        # Blender may rebuild the edit BMesh between invoke and modal events.
        # Reacquire the vertex rather than retaining an invalid BMVert handle.
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if self.mesh_counts != (len(bm.verts), len(bm.edges), len(bm.faces)) or self.vertex_index >= len(bm.verts):
            _merge_target = None
            _grid_drag_region = None
            self.area.header_text_set(GRID_HINT if context.scene.retopo_tool.mode == 'GRID' else None)
            return {'CANCELLED'}
        vertex = bm.verts[self.vertex_index]
        if context.scene.retopo_tool.mode != 'GRID' or event.type in {'ESC', 'RIGHTMOUSE'}:
            _merge_target = None
            _grid_drag_region = None
            restore_move(self, bm, {vertex: self.original})
            sync(self.obj, destructive=False)
            self.area.header_text_set(GRID_HINT if context.scene.retopo_tool.mode == 'GRID' else None)
            self.area.tag_redraw()
            # Consume the cancel click so it cannot also invoke Leave Fill Hole.
            return {'FINISHED'} if event.type in {'ESC', 'RIGHTMOUSE'} else {'CANCELLED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        welded = False
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or release:
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            self.dragging |= (mouse - self.start).length > 3
            with context.temp_override(area=self.area, region=self.region):
                track_mouse(context, event)
                if self.dragging:
                    _grid_drag_region = self.region.as_pointer()
                    settings = context.scene.retopo_tool
                    target = None
                    if settings.fill_merge:
                        snapshot = fill_drag_snapshot(self, context, bm)
                        target = fill_drag_target(context, bm, mouse, vertex, self.original,
                                                  mirror_exclusions(self, bm, [vertex]), snapshot)
                    _merge_target = (self.region.as_pointer(), target.co.copy()) if target is not None else None
                    point = self.obj.matrix_world @ target.co if target is not None else project(
                        context, mouse + self.grab_offset, self.obj.matrix_world @ self.original)
                    if point is not None:
                        set_move_positions(self, bm, {vertex: self.obj.matrix_world.inverted_safe() @ point},
                                           (vertex,) if target is not None else ())
                        if release and target is not None:
                            sides = movement_sides(self, [self.original])
                            try:
                                commit_move_weld(bm, self, [vertex], {vertex: target}, sides)
                            except (ValueError, RuntimeError) as error:
                                restore_move(self, bm, {vertex: self.original})
                                sync(self.obj, destructive=False)
                                self.report({'WARNING'}, str(error))
                            else:
                                sync_edit(self.obj, bm, sides)
                                welded = True
                        elif not release:
                            sync(self.obj, destructive=False)
                    self.area.header_text_set("Reshape hole | Release to merge vertices | Esc / right-click cancels" if target is not None
                                              else "Reshape hole | Drag vertex | Release to keep | Esc / right-click cancels")
            self.area.tag_redraw()
        if release:
            _merge_target = None
            _grid_drag_region = None
            if not self.dragging:
                topology.select(bm, vertex)
                sync(self.obj, destructive=False)
            elif not welded:
                sides = movement_sides(self, [self.original]) if vertex.co != self.original else topology.NO_EDIT_SIDES
                finish_position_move(self.obj, bm, self, sides)
            self.area.header_text_set(GRID_HINT)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def modal_edge(self, context, event):
        global _edge_merge_targets, _grid_drag_region
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            _edge_merge_targets = None
            _grid_drag_region = None
            self.area.header_text_set(None)
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if self.mesh_counts != (len(bm.verts), len(bm.edges), len(bm.faces)):
            _edge_merge_targets = None
            _grid_drag_region = None
            self.area.header_text_set(GRID_HINT)
            return {'CANCELLED'}
        verts = [bm.verts[i] for i in self.move_indices]
        if context.scene.retopo_tool.mode != 'GRID' or event.type in {'ESC', 'RIGHTMOUSE'}:
            restore_move(self, bm, dict(zip(verts, self.move_originals)))
            _edge_merge_targets = None
            _grid_drag_region = None
            sync(self.obj, destructive=False)
            self.area.header_text_set(GRID_HINT if context.scene.retopo_tool.mode == 'GRID' else None)
            self.area.tag_redraw()
            return {'FINISHED'} if event.type in {'ESC', 'RIGHTMOUSE'} else {'CANCELLED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or release:
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            self.dragging |= (mouse - self.start).length > 3
            targets = {}
            try:
                with context.temp_override(area=self.area, region=self.region):
                    track_mouse(context, event)
                    if self.dragging:
                        _grid_drag_region = self.region.as_pointer()
                        merge = context.scene.retopo_tool.fill_merge
                        snapshot = fill_drag_snapshot(self, context, bm) if merge else None
                        snap = symmetry_projector(reference_projector(context), move_symmetry(self),
                                                  movement_sides(self, self.move_originals))
                        positions = edge_drag_positions(context, self.move_originals, self.start, mouse, snap)
                        positions, targets = edge_merge_positions(
                            context, bm, verts, self.move_originals, positions,
                            merge, mirror_exclusions(self, bm, verts), snapshot)
                        set_move_positions(self, bm, dict(zip(verts, positions)), targets)
                        _edge_merge_targets = ((self.region.as_pointer(),
                                                [target.co.copy() for target in targets.values()])
                                               if targets else None)
                        if release and targets:
                            sides = movement_sides(self, self.move_originals)
                            commit_move_weld(bm, self, verts, targets, sides)
                            _edge_merge_targets = None
                            sync_edit(self.obj, bm, sides)
                        elif release:
                            sides = movement_sides(self, self.move_originals) if any(
                                vertex.co != co for vertex, co in zip(verts, self.move_originals)) else topology.NO_EDIT_SIDES
                            finish_position_move(self.obj, bm, self, sides)
                        else:
                            sync(self.obj, destructive=False)
                    elif release:
                        edge = bm.edges.get(verts)
                        if self.edge_ctrl:
                            factor = self.edge_factor if edge.verts[0] == verts[0] else 1 - self.edge_factor
                            target = fill_triangle_target(context, bm, edge, factor)
                            symmetry = move_symmetry(self)
                            sides = topology.preferred_sides(element_sides(edge, factor), element_sides(target),
                                                              topology.last_edit_sides(bm))
                            checked_topology_edit(bm, lambda mesh, edge, target: topology.split_edge_triangle(
                                mesh, edge, factor, target), (edge, target), symmetry=symmetry,
                                sides=sides)
                            sync_edit(self.obj, bm, sides)
                        else:
                            topology.select(bm, edge)
                            sync(self.obj, destructive=False)
            except (ValueError, RuntimeError) as error:
                if release:
                    if targets and getattr(self, 'mirror_move_pairs', None) is not None:
                        restore_move(self, bm, dict(zip(verts, self.move_originals)))
                        sync(self.obj, destructive=False)
                    _edge_merge_targets = None
                    _grid_drag_region = None
                    self.report({'WARNING'}, str(error))
                    self.area.header_text_set(GRID_HINT)
                    return {'FINISHED'}
            self.area.tag_redraw()
        if release:
            _edge_merge_targets = None
            _grid_drag_region = None
            self.area.header_text_set(GRID_HINT)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class RP_OT_grid_stop(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_grid_stop"
    bl_label = "Leave Fill Hole"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return RP_OT_grid_fill.poll(context)

    def execute(self, context):
        context.scene.retopo_tool.mode = 'RETOPO'
        return {'FINISHED'}



class RP_OT_click(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_click"
    bl_label = "Draw Tool Stroke"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW' and context.scene.retopo_tool.mode == 'RETOPO')

    def invoke(self, context, event):
        global _edge_merge_targets
        _edge_merge_targets = None
        track_mouse(context, event)
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        if event.alt and not event.ctrl and not event.shift:
            return RP_OT_alt_cut.invoke(self, context, event)
        hit = pick(context, bm, mouse)
        anchor = topology.active(bm)
        settings = context.scene.retopo_tool
        if event.ctrl and not event.shift:
            new_anchor = reselect_draw_edge(context, bm, anchor, mouse, hit)
            if new_anchor != anchor:
                anchor = new_anchor
                sync(obj, destructive=False)
        if (event.ctrl and not event.shift and isinstance(anchor, bmesh.types.BMEdge)
                and len(anchor.link_faces) <= 1 and (hit is None or hit == anchor)):
            bm.edges.index_update()
            self.edge_index = anchor.index
            self.obj, self.area, self.region = obj, context.area, context.region
            self.start = mouse
            self.dragging = False
            self.strip_click_only = False
            self.strip_gesture = True
            self.strip_on_edge = hit == anchor
            self.had_strip_faces = False
            try:
                self.strip_path = topology.StripPath(
                    (anchor.verts[0].co + anchor.verts[1].co) * 0.5, anchor.calc_length())
            except ValueError as error:
                self.report({'WARNING'}, str(error))
                return {'CANCELLED'}
            self.strip_snap = reference_projector(context)
            self.strip_counts = (len(bm.verts), len(bm.edges), len(bm.faces))
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        if (event.ctrl and not event.shift and getattr(settings, 'auto_reselect', True)
                and isinstance(hit, bmesh.types.BMVert)
                and (anchor is None or anchor == hit
                     or (isinstance(anchor, bmesh.types.BMEdge) and hit in anchor.verts))
                and not topology.pending(bm)):
            # Starting from a vertex picks the closest full edge instead of
            # leaving a lone vertex selected and chaining more vertices.
            start_edge = vertex_start_edge(context, hit, mouse)
            if start_edge is not None:
                topology.clear_pending(bm, discard=settings.quad_only)
                topology.select(bm, start_edge)
                sync(obj, destructive=False)
                return {'FINISHED'}
        if event.ctrl and not event.shift and isinstance(hit, bmesh.types.BMEdge):
            bm.edges.index_update()
            self.edge_index = hit.index
            self.obj, self.area, self.region = obj, context.area, context.region
            self.start = mouse
            self.dragging = False
            self.edge_gesture = True
            self.edge_counts = (len(bm.verts), len(bm.edges), len(bm.faces))
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        if event.ctrl:
            try:
                point = placement(context, mouse, anchor) if hit is None else None
                point = obj.matrix_world.inverted_safe() @ point if point is not None else None
                factor = edge_parameter(context, hit, mouse) if isinstance(hit, bmesh.types.BMEdge) else 0.5
                commit_draw_click(context, bm, anchor, hit, point, factor, event.shift,
                                  reference_projector(context))
            except (ValueError, RuntimeError) as error:
                self.report({'WARNING'}, str(error))
                return {'CANCELLED'}
            return {'FINISHED'}
        # Moving/selecting geometry commits pending triangles. Discarding them
        # here removes faces before a plain vertex or edge drag even starts.
        topology.clear_pending(bm)
        if event.shift and hit is not None:
            hit.select_set(not hit.select)
            if hit.select:
                bm.select_history.add(hit)
            sync(obj)
            return {'FINISHED'}
        topology.select(bm, hit)
        if isinstance(hit, bmesh.types.BMEdge):
            bm.verts.index_update()
            self.move_indices = [v.index for v in hit.verts]
            self.move_originals = [v.co.copy() for v in hit.verts]
            self.move_counts = (len(bm.verts), len(bm.edges), len(bm.faces))
            remember_mirror_move(self, context, bm, hit, edge_parameter(context, hit, mouse))
            self.obj, self.area, self.region = obj, context.area, context.region
            self.start = mouse
            self.dragging = False
            self.move_edge = True
            sync(obj, destructive=False)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        if isinstance(hit, bmesh.types.BMVert):
            bm.verts.index_update()
            self.obj = obj
            self.region = context.region
            self.area = context.area
            self.vertex_index = hit.index
            self.original = hit.co.copy()
            self.vertex_move_counts = (len(bm.verts), len(bm.edges), len(bm.faces))
            remember_mirror_move(self, context, bm, hit)
            self.start = mouse
            self.dragging = False
            sync(obj, destructive=False)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        sync(obj, destructive=False)
        return {'FINISHED'}

    def modal(self, context, event):
        global _merge_target
        if getattr(self, 'cut_gesture', False):
            return RP_OT_alt_cut.modal(self, context, event)
        if getattr(self, 'strip_gesture', False):
            return RP_OT_click.modal_strip(self, context, event)
        if getattr(self, 'move_edge', False):
            return RP_OT_click.modal_move_edge(self, context, event)
        if getattr(self, 'edge_gesture', False):
            return RP_OT_click.modal_edge(self, context, event)
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            _merge_target = None
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if (self.vertex_index >= len(bm.verts) or
                (hasattr(self, 'vertex_move_counts') and
                 (len(bm.verts), len(bm.edges), len(bm.faces)) != self.vertex_move_counts)):
            _merge_target = None
            return {'CANCELLED'}
        vertex = bm.verts[self.vertex_index]
        settings = context.scene.retopo_tool
        track_mouse(context, event)
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            _merge_target = None
            restore_move(self, bm, {vertex: self.original})
            sync(self.obj, destructive=False)
            return {'FINISHED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self.dragging:
                sides = movement_sides(self, [self.original])
                mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
                mirrored = mirror_exclusions(self, bm, [vertex])
                with context.temp_override(area=self.area, region=self.region):
                    target = pick(context, bm, mouse, exclude={vertex, *mirrored} if mirrored else vertex, vertices_only=True,
                                  radius=settings.merge_radius) if settings.snapping else None
                    if target is None and move_symmetry(self).center_distance:
                        point = project(context, mouse, self.obj.matrix_world @ self.original)
                        if point is not None:
                            set_move_positions(self, bm, {vertex: self.obj.matrix_world.inverted_safe() @ point})
                if target is not None:
                    try:
                        commit_move_weld(bm, self, [vertex], {vertex: target}, sides, settings.quad_only)
                    except (ValueError, RuntimeError) as error:
                        restore_move(self, bm, {vertex: self.original})
                        sync(self.obj, destructive=False)
                        self.report({'WARNING'}, str(error))
                    else:
                        sync_edit(self.obj, bm, sides)
                elif vertex.co != self.original:
                    finish_position_move(self.obj, bm, self, sides)
            _merge_target = None
            self.area.tag_redraw()
            return {'FINISHED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            self.dragging |= (mouse - self.start).length > 3
            if self.dragging:
                obj = self.obj
                mirrored = mirror_exclusions(self, bm, [vertex])
                with context.temp_override(area=self.area, region=self.region):
                    target = pick(context, bm, mouse, exclude={vertex, *mirrored} if mirrored else vertex, vertices_only=True,
                                  radius=settings.merge_radius) if settings.snapping else None
                    point = obj.matrix_world @ target.co if target is not None else project(
                        context, mouse, obj.matrix_world @ self.original)
                _merge_target = (self.region.as_pointer(), target.co.copy()) if target is not None else None
                if point is not None:
                    set_move_positions(self, bm, {vertex: obj.matrix_world.inverted_safe() @ point},
                                       (vertex,) if target is not None else ())
                    sync(obj, destructive=False)
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


    def modal_strip(self, context, event):
        global _strip_preview, _strip_target
        if (context.mode != 'EDIT_MESH' or context.edit_object != self.obj
                or context.scene.retopo_tool.mode != 'RETOPO'
                or event.type in {'ESC', 'RIGHTMOUSE'}):
            RP_OT_click.end_strip(self)
            return {'CANCELLED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        symmetry = symmetry_options(context)
        if not self.dragging and not event.ctrl:
            self.strip_click_only = True
        if event.type not in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and not release:
            return {'RUNNING_MODAL'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        if ((len(bm.verts), len(bm.edges), len(bm.faces)) != self.strip_counts
                or self.edge_index >= len(bm.edges)):
            RP_OT_click.end_strip(self)
            return {'CANCELLED'}
        edge = bm.edges[self.edge_index]
        mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        was_dragging = self.dragging
        self.dragging |= (not release and event.ctrl and not self.strip_click_only
                          and (mouse - self.start).length >= STRIP_START_DISTANCE)
        try:
            with context.temp_override(area=self.area, region=self.region):
                track_mouse(context, event)
                if self.dragging:
                    self.area.header_text_set("Drawing strip | Release to create | Hover an edge to connect | Esc / right-click cancels")
                    _strip_target = None
                    hit = pick(context, bm, mouse, edges_only=True,
                               radius=context.scene.retopo_tool.merge_radius)
                    target = (hit if hit is not None and hit != edge
                              and not hit.hide and len(hit.link_faces) <= 1 else None)
                    snap_dist = weld_distance(context)
                    samples = [mouse] if was_dragging or self.strip_on_edge else [self.start, mouse]
                    trail = self.strip_path.copy()
                    for sample in samples:
                        point = placement(context, sample, edge)
                        if point is None:
                            if target is not None and sample is mouse:
                                continue
                            raise ValueError("Cursor is outside the reference surface")
                        trail.add(self.obj.matrix_world.inverted_safe() @ point)
                    free_points = trail.positions()
                    self.strip_path = trail
                    free_polygons = None
                    triangle = False
                    if target is not None and free_points:
                        try:
                            free_polygons = strip_preview(bm, edge, free_points, self.strip_snap, None, snap_dist,
                                                          symmetry=symmetry)
                        except ValueError:
                            free_polygons = None
                        if free_polygons:
                            target_key = {tuple(v.co) for v in target.verts}
                            outer_key = {tuple(free_polygons[-1][2]), tuple(free_polygons[-1][3])}
                            if target_key != outer_key and any(target_key.issubset({tuple(co) for co in poly}) for poly in free_polygons):
                                target = None
                    if target is None:
                        self.strip_path = trail
                        points = free_points
                        if (not points and not edge.link_faces
                                and (trail.trail[-1] - (edge.verts[0].co + edge.verts[1].co) * 0.5).length
                                > edge.calc_length() * 0.1):
                            # Starting new topology from a single loose edge: the
                            # stroke is too short for a quad, so the first face
                            # after the edge is a pending triangle at the cursor.
                            points = [trail.trail[-1].copy()]
                            triangle = True
                        if free_polygons is not None:
                            polygons = free_polygons
                        else:
                            polygons = strip_preview(
                                bm, edge, points, self.strip_snap, None, snap_dist,
                                triangle=triangle, symmetry=symmetry)
                    else:
                        center = (target.verts[0].co + target.verts[1].co) * 0.5
                        path = self.strip_path.copy()
                        path.add(center)
                        points = path.positions()
                        while points and (points[-1] - center).length < path.spacing * 0.6:
                            points.pop()
                        try:
                            polygons = strip_preview(bm, edge, points, self.strip_snap, target, snap_dist,
                                                     symmetry=symmetry)
                        except ValueError:
                            if free_polygons is None:
                                raise
                            target = None
                            self.strip_path = trail
                            points = free_points
                            polygons = free_polygons
                    if polygons:
                        self.had_strip_faces = True
                    _strip_preview = (self.region.as_pointer(), polygons)
                    if target is not None:
                        _strip_target = (self.region.as_pointer(), [v.co.copy() for v in target.verts])
                        self.area.header_text_set("Connect strip to edge | Release to connect | Esc / right-click cancels")
                    if release and polygons:
                        sides = strip_sides(bm, edge, points, target)
                        snap = symmetry_projector(self.strip_snap, symmetry, sides)
                        topology_edit(bm, lambda: topology.quad_strip(
                            bm, edge, points, snap, target, snap_dist, triangle=triangle),
                            symmetry, sides=sides)
                        sync_edit(self.obj, bm, sides)
                    elif release and not self.had_strip_faces:
                        raise ValueError("Drag farther from the starting edge to draw a strip")
                elif release:
                    hit = edge if self.strip_on_edge else None
                    point = placement(context, self.start, edge) if hit is None else None
                    point = self.obj.matrix_world.inverted_safe() @ point if point is not None else None
                    factor = edge_parameter(context, edge, self.start) if hit is not None else 0.5
                    commit_draw_click(context, bm, edge, hit, point, factor, snap=self.strip_snap)
        except (ValueError, RuntimeError) as error:
            _strip_preview = (self.region.as_pointer(), [])
            _strip_target = None
            if self.dragging:
                self.area.header_text_set(f"Drawing strip | {error} | Esc / right-click cancels")
            if release:
                self.report({'WARNING'}, str(error))
                RP_OT_click.end_strip(self)
                return {'CANCELLED'}
        self.area.tag_redraw()
        if release:
            RP_OT_click.end_strip(self)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def end_strip(self):
        global _strip_preview, _strip_target
        _strip_preview = None
        _strip_target = None
        self.area.header_text_set(None)
        self.area.tag_redraw()

    def modal_move_edge(self, context, event):
        global _edge_merge_targets
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            _edge_merge_targets = None
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if (mesh_counts(bm) != getattr(self, 'move_counts', mesh_counts(bm))
                or any(i >= len(bm.verts) for i in self.move_indices)):
            _edge_merge_targets = None
            return {'CANCELLED'}
        verts = [bm.verts[i] for i in self.move_indices]
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            restore_move(self, bm, dict(zip(verts, self.move_originals)))
            _edge_merge_targets = None
            sync(self.obj, destructive=False)
            self.area.tag_redraw()
            return {'FINISHED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or release:
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            self.dragging |= (mouse - self.start).length > 3
            if self.dragging:
                targets = {}
                try:
                    with context.temp_override(area=self.area, region=self.region):
                        track_mouse(context, event)
                        if not hasattr(self, 'move_snap'):
                            self.move_snap = symmetry_projector(
                                reference_projector(context), move_symmetry(self),
                                movement_sides(self, self.move_originals))
                        positions = edge_drag_positions(context, self.move_originals, self.start, mouse, self.move_snap)
                        positions, targets = edge_merge_positions(
                            context, bm, verts, self.move_originals, positions,
                            context.scene.retopo_tool.snapping, mirror_exclusions(self, bm, verts))
                    set_move_positions(self, bm, dict(zip(verts, positions)), targets)
                    _edge_merge_targets = ((self.region.as_pointer(),
                                            [target.co.copy() for target in targets.values()])
                                           if targets else None)
                    if release and targets:
                        sides = movement_sides(self, self.move_originals)
                        commit_move_weld(bm, self, verts, targets, sides)
                        _edge_merge_targets = None
                        sync_edit(self.obj, bm, sides)
                    elif release:
                        sides = movement_sides(self, self.move_originals) if any(
                            vertex.co != co for vertex, co in zip(verts, self.move_originals)) else topology.NO_EDIT_SIDES
                        finish_position_move(self.obj, bm, self, sides)
                    else:
                        sync(self.obj, destructive=False)
                except (ValueError, RuntimeError) as error:
                    if release:
                        if targets and getattr(self, 'mirror_move_pairs', None) is not None:
                            restore_move(self, bm, dict(zip(verts, self.move_originals)))
                            sync(self.obj, destructive=False)
                        self.report({'WARNING'}, str(error))
            self.area.tag_redraw()
        if release:
            _edge_merge_targets = None
        return {'FINISHED'} if release else {'RUNNING_MODAL'}

    def modal_edge(self, context, event):
        """Ctrl-click selects/bridges; dragging here never starts a loop cut."""
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            return {'CANCELLED'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self.area.tag_redraw()
            return {'CANCELLED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        if event.type not in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and not release:
            return {'RUNNING_MODAL'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        if ((len(bm.verts), len(bm.edges), len(bm.faces)) != self.edge_counts
                or self.edge_index >= len(bm.edges)):
            return {'CANCELLED'}
        mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        if (mouse - self.start).length > 4:
            return {'CANCELLED'}
        if release:
            bm.edges.ensure_lookup_table()
            edge = bm.edges[self.edge_index]
            try:
                with context.temp_override(area=self.area, region=self.region):
                    track_mouse(context, event)
                    factor = edge_parameter(context, edge, self.start)
                    anchor = topology.active(bm)
                    commit_draw_click(context, bm, anchor, edge, factor=factor,
                                      snap=reference_projector(context))
            except (ValueError, RuntimeError) as error:
                self.report({'WARNING'}, str(error))
                return {'CANCELLED'}
            self.area.tag_redraw()
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


_mouse_region = None
_loop_preview = None
_knife_preview = None
_strip_preview = None
_strip_target = None
_merge_target = None
_edge_merge_targets = None


def track_mouse(context, event):
    global _mouse_region
    context.window_manager.retopo_tool_mouse = (event.mouse_x, event.mouse_y)
    context.window_manager.retopo_tool_ctrl = event.ctrl
    context.window_manager.retopo_tool_shift = event.shift
    context.window_manager.retopo_tool_alt = getattr(event, 'alt', False)
    if event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
        context.window_manager.retopo_tool_ctrl = event.value != 'RELEASE'
    if event.type in {'LEFT_ALT', 'RIGHT_ALT'}:
        context.window_manager.retopo_tool_alt = event.value != 'RELEASE'
    if event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
        context.window_manager.retopo_tool_shift = event.value != 'RELEASE'
    _mouse_region = context.region.as_pointer()


def brush_vertices(context, bm, mouse, edit_tree=None, reference=_UNSET):
    matrix = context.edit_object.matrix_world
    verts = list(bm.verts)
    if edit_tree is None:
        edit_tree, _ = edit_surface_geometry(context, bm)
    if reference is _UNSET:
        reference = reference_tree(context)
    radius = context.scene.retopo_tool.brush_radius
    result = []
    for v in verts:
        if v.hide:
            continue
        co = matrix @ v.co
        screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, co)
        if screen is None or (screen - mouse).length >= radius:
            continue
        if not visible_from_view(context, co, edit_tree):
            continue
        if reference is not None and not visible_from_view(context, co, reference, reference_pick_tolerance(context, co)):
            continue
        t = 1 - (screen - mouse).length / radius
        result.append((v, t * t * (3 - 2 * t)))
    return result


def relax_positions(weighted, preserve_borders=False):
    positions = {}
    for vert, weight in weighted:
        if preserve_borders and (not vert.link_faces or any(not edge.is_manifold for edge in vert.link_edges)):
            continue
        edges = [edge for edge in vert.link_edges if not edge.hide and not edge.other_vert(vert).hide]
        if vert.is_boundary:
            edges = [edge for edge in edges if edge.is_boundary]
        if edges:
            average = sum((edge.other_vert(vert).co for edge in edges), Vector()) / len(edges)
            positions[vert] = vert.co.lerp(average, 0.25 * weight)
    return positions


class RP_OT_brush(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_brush"
    bl_label = "Draw Tool Brush Stroke"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW' and context.scene.retopo_tool.mode == 'BRUSH')

    def invoke(self, context, event):
        self.obj = context.edit_object
        self.area, self.region = context.area, context.region
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        # Keep a visibility snapshot for the stroke. Rebuilding a full edit BVH
        # on every Shift-relax mousemove is expensive; topology is fixed during
        # the brush gesture and the original surface is the stable occluder.
        self.brush_tree, _ = edit_surface_geometry(context, bm)
        self.brush_reference = reference_tree(context)
        self.originals = [v.co.copy() for v in bm.verts]
        self.counts = (len(bm.verts), len(bm.edges), len(bm.faces))
        remember_mirror_move(self, context, bm)
        self.last = Vector((event.mouse_region_x, event.mouse_region_y))
        self.weighted = [(v.index, weight) for v, weight in brush_vertices(
            context, bm, self.last, self.brush_tree, self.brush_reference)]
        self.relaxing = event.shift
        track_mouse(context, event)
        if event.shift:
            RP_OT_brush.apply(self, context, self.last, True)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def apply(self, context, mouse, relaxing):
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        if relaxing != self.relaxing:
            self.weighted = [(v.index, weight) for v, weight in brush_vertices(
                context, bm, self.last, self.brush_tree, self.brush_reference)]
        snap = reference_projector(context)
        settings = getattr(getattr(context, 'scene', None), 'retopo_tool', None)
        strength = getattr(settings, 'brush_strength', 1.0)
        if relaxing:
            weighted = brush_vertices(context, bm, mouse, self.brush_tree, self.brush_reference)
            weighted = [(vert, weight * strength) for vert, weight in weighted]
            positions = relax_positions(weighted, context.scene.retopo_tool.preserve_borders)
        else:
            matrix = self.obj.matrix_world
            inverse = matrix.inverted_safe().to_3x3()
            positions = {}
            weighted = []
            # Edit-mesh updates can invalidate BMVert wrappers between modal events.
            for index, weight in self.weighted:
                vert = bm.verts[index]
                depth = matrix @ vert.co
                a = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, self.last, depth)
                b = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, mouse, depth)
                positions[vert] = vert.co + inverse @ (b - a) * weight * strength
                weighted.append((vert, weight))
        if strength == 0:
            positions = {}
        symmetry = move_symmetry(self)
        originals = getattr(self, 'mirror_move_originals', {})
        candidates = [(weight, topology.coordinate_sides(originals.get(vert.index, vert.co)))
                      for vert, weight in weighted if vert in positions]
        sides = tuple(max(((weight, signs[axis]) for weight, signs in candidates if signs[axis]),
                          default=(0, 0))[1] for axis in range(3))
        move_sides = topology.preferred_sides(sides, getattr(self, 'move_sides', topology.NO_EDIT_SIDES))
        self.move_sides = move_sides
        snap = symmetry_projector(snap, symmetry, move_sides)
        if snap is not None:
            positions = {vert: snap(co) for vert, co in positions.items()}
        if any(sides) and any(
                vertex.co != topology.center_coordinates(co, symmetry.axes, symmetry.center_distance)
                for vertex, co in positions.items()):
            self.brush_last_sides = topology.preferred_sides(
                sides, getattr(self, 'brush_last_sides', topology.NO_EDIT_SIDES))
        set_move_positions(self, bm, positions)
        self.last, self.relaxing = mouse, relaxing
        sync(self.obj, destructive=False)

    def modal(self, context, event):
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        if (len(bm.verts), len(bm.edges), len(bm.faces)) != self.counts:
            self.report({'WARNING'}, "Brush stopped because mesh topology changed")
            return {'CANCELLED'}
        track_mouse(context, event)
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            for vert, co in zip(bm.verts, self.originals):
                vert.co = co
            sync(self.obj, destructive=False)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            sides = getattr(self, 'brush_last_sides', topology.NO_EDIT_SIDES)
            if (any(sides) or getattr(self, 'center_candidates', ())) and any(
                    vert.co != co for vert, co in zip(bm.verts, self.originals)):
                finish_position_move(self.obj, bm, self, sides)
            return {'FINISHED'}
        if event.type == 'MOUSEMOVE':
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            with context.temp_override(area=self.area, region=self.region):
                RP_OT_brush.apply(self, context, mouse, event.shift)
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}


class RP_OT_brush_size(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_brush_size"
    bl_label = "Adjust Brush Radius"
    bl_options = {'INTERNAL', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return RP_OT_brush.poll(context)

    def invoke(self, context, event):
        self.original = context.scene.retopo_tool.brush_radius
        self.start_x = event.mouse_x
        track_mouse(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            context.scene.retopo_tool.brush_radius = self.original
            context.area.tag_redraw()
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            return {'FINISHED'}
        if event.type == 'MOUSEMOVE':
            context.scene.retopo_tool.brush_radius = max(5, min(500, self.original + event.mouse_x - self.start_x))
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


class RP_OT_brush_strength(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_brush_strength"
    bl_label = "Adjust Brush Strength"
    bl_description = "Move the mouse right/left to increase/decrease strength; click to confirm, Esc to cancel"
    bl_options = {'INTERNAL', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return RP_OT_brush.poll(context)

    def invoke(self, context, event):
        self.original = context.scene.retopo_tool.brush_strength
        self.start_x = event.mouse_x
        track_mouse(context, event)
        context.area.header_text_set(f"Brush Strength: {self.original:.0%} | Move left / right | Click: confirm | Esc: cancel")
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if (event.type in {'ESC', 'RIGHTMOUSE'} or context.mode != 'EDIT_MESH'
                or context.scene.retopo_tool.mode != 'BRUSH'):
            context.scene.retopo_tool.brush_strength = self.original
            context.area.header_text_set(None)
            context.area.tag_redraw()
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            context.area.header_text_set(None)
            return {'FINISHED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            strength = max(0.0, min(1.0, self.original + (event.mouse_x - self.start_x) / 200))
            context.scene.retopo_tool.brush_strength = strength
            context.area.header_text_set(f"Brush Strength: {strength:.0%} | Click: confirm | Esc: cancel")
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


class RP_OT_mode(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_mode"
    bl_label = "Set Draw Tool Mode"
    mode: EnumProperty(items=(('RETOPO', "Draw", ""), ('BRUSH', "Brush", ""), ('BOX', "Box Select", ""), ('GRID', "Fill Hole", "")))

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'

    def execute(self, context):
        settings = context.scene.retopo_tool
        settings.mode = 'RETOPO' if self.mode == 'GRID' and settings.mode == 'GRID' else self.mode
        return {'FINISHED'}


class RP_MT_modes(bpy.types.Menu):
    bl_label = "Draw Tool Modes"
    bl_idname = "RP_MT_modes"

    def draw(self, context):
        pie = self.layout.menu_pie()
        for mode, label, icon in (('RETOPO', "Draw", 'EDITMODE_HLT'), ('BRUSH', "Brush", 'BRUSH_DATA'), ('BOX', "Box Select", 'SELECT_SET'), ('GRID', "Fill Hole", 'MESH_GRID')):
            pie.operator("mesh.retopo_tool_mode", text=label, icon=icon,
                         depress=context.scene.retopo_tool.mode == mode).mode = mode


class RP_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    reference_tolerance: IntProperty(name="Reference Tolerance", description="Fixed depth allowance of 64 screen pixels for selecting geometry slightly below the Reference", default=64, min=64, max=64, subtype='PIXEL')
    retopo_color_preset: EnumProperty(name="Topology Color Preset", items=COLOR_PRESET_ITEMS, default='BLUE', update=update_retopo_color_preset)
    retopo_color: FloatVectorProperty(name="Draw Color", subtype='COLOR', size=4, min=0, max=1, default=COLOR_PRESETS['BLUE'], update=update_retopo_color)
    overlay_color: FloatVectorProperty(name="Overlay Color", subtype='COLOR', size=3, min=0, max=1, default=DEFAULT_OVERLAY_COLOR, update=update_overlay_preferences)
    overlay_opacity: FloatProperty(name="Overlay Opacity", subtype='FACTOR', min=0, max=1, default=DEFAULT_OVERLAY_OPACITY, update=update_overlay_preferences)

    def draw(self, context):
        import rna_keymap_ui
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        picking = layout.box()
        picking.label(text="Picking")
        picking.label(text="Reference tolerance: 64 px")

        colors = layout.box()
        colors.label(text="Topology Colors")
        colors.prop(self, "retopo_color_preset")
        custom_color = colors.row()
        custom_color.enabled = self.retopo_color_preset == 'CUSTOM'
        custom_color.prop(self, "retopo_color")

        overlay = layout.box()
        overlay.label(text="Blender Retopology Overlay")
        custom_overlay = overlay.row()
        custom_overlay.enabled = self.retopo_color_preset == 'CUSTOM'
        custom_overlay.prop(self, "overlay_color")
        overlay.prop(self, "overlay_opacity")

        keyconfig = context.window_manager.keyconfigs.user
        keymap = keyconfig.keymaps.get('Mesh')
        if keymap:
            for item in keymap.keymap_items:
                if item.idname == 'wm.call_menu_pie' and item.properties.name == RP_MT_modes.bl_idname:
                    layout.context_pointer_set("keymap", keymap)
                    rna_keymap_ui.draw_kmi([], keyconfig, keymap, item, layout, 0)


class RP_OT_hover(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_hover"
    bl_label = "Draw Tool Preview"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        track_mouse(context, event)
        if navigation_changed(context):
            return {'PASS_THROUGH'}
        if context.scene.retopo_tool.mode == 'GRID' and _grid_drag_region == context.region.as_pointer():
            return {'PASS_THROUGH'}
        if (context.mode == 'EDIT_MESH' and context.scene.retopo_tool.mode in {'RETOPO', 'GRID'}
              and getattr(context.window_manager, 'retopo_tool_alt', False)
              and not context.window_manager.retopo_tool_ctrl and not context.window_manager.retopo_tool_shift):
            # Alt hover only previews. Selection belongs to the editing workflow.
            context.area.header_text_set("Knife | Alt-click inserts/connects | Alt-drag over an edge: loop cut")
        elif context.mode == 'EDIT_MESH' and context.scene.retopo_tool.mode == 'GRID':
            bm = bmesh.from_edit_mesh(context.edit_object.data)
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            polygons, border, error = grid_hover_preview(context, bm, mouse)
            quads = sum(len(polygon) == 4 for polygon in polygons)
            triangles = sum(len(polygon) == 3 for polygon in polygons)
            counts = ', '.join(text for number, text in ((quads, f"{quads} quads"), (triangles, f"{triangles} triangles")) if number)
            fill_mode = 'Quads Only' if fill_quads_only(context) else 'Quad/Tri (Ctrl)'
            hint = 'Hold Ctrl: Quad/Tri' if fill_quads_only(context) else 'Release Ctrl: Quads Only'
            message = (f"Fill Hole | {fill_mode} | {error} | Drag vertices / edges to reshape | {hint}" if error else
                       f"Fill Hole | {fill_mode} | {counts or 'Click a preview to fill'} | Click: select | Drag vertices / edges | Ctrl-click edge: triangle | Alt: knife / loop cut | {hint}")
            context.area.header_text_set(message)
        elif (context.mode == 'EDIT_MESH' and context.scene.retopo_tool.mode == 'RETOPO'
              and event.type in {'LEFT_ALT', 'RIGHT_ALT'} and event.value == 'RELEASE'):
            context.area.header_text_set(None)
        elif (context.mode == 'EDIT_MESH' and context.scene.retopo_tool.mode == 'RETOPO'
              and context.window_manager.retopo_tool_ctrl and not context.window_manager.retopo_tool_shift
              and not getattr(context.window_manager, 'retopo_tool_alt', False)):
            bm = bmesh.from_edit_mesh(context.edit_object.data)
            anchor = topology.active(bm)
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            switched = False
            if (getattr(context.scene.retopo_tool, 'auto_reselect', True) and not topology.pending(bm)
                    and event.type in {'LEFT_CTRL', 'RIGHT_CTRL'} and event.value == 'PRESS'):
                # Pressing Ctrl anchors to the closest border edge under the
                # cursor so a triangle can be drawn from it immediately. Only
                # the key press switches a regular edge anchor immediately.
                nearest = pick(context, bm, mouse, edges_only=True)
                start_edge = (closest_border_edge(context, bm, mouse)
                              if nearest is None or len(nearest.link_faces) <= 1 else None)
                if start_edge is not None:
                    topology.clear_pending(bm, discard=context.scene.retopo_tool.quad_only)
                    topology.select(bm, start_edge)
                    sync(context.edit_object, destructive=False)
                    anchor, switched = start_edge, True
            if not switched:
                hit = pick(context, bm, mouse)
                if reselect_draw_edge(context, bm, anchor, mouse, hit) != anchor:
                    sync(context.edit_object, destructive=False)
        context.area.tag_redraw()
        return {'PASS_THROUGH'}


def draw_mode_options(layout, settings):
    if settings.mode == 'BRUSH':
        layout.prop(settings, "brush_radius")
        layout.prop(settings, "brush_strength")
        layout.prop(settings, "preserve_borders")
    elif settings.mode == 'RETOPO':
        layout.prop(settings, "quad_only")
        layout.prop(settings, "auto_reselect")
    elif settings.mode == 'GRID':
        layout.label(text="Quads Only · Hold Ctrl: Quad/Tri")
        layout.prop(settings, "fill_merge")


class RP_Tool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "retopo_tool.tool"
    bl_label = ADDON_NAME
    bl_description = "Select and drag; Ctrl-click creates/bridges; Ctrl-drag draws strips; Alt-click is knife; Alt-drag cuts a quad loop"
    bl_icon = TOOL_ICON
    bl_keymap = (
        ("mesh.retopo_tool_grid_fill", {"type": 'LEFTMOUSE', "value": 'PRESS', "any": True}, None),
        ("mesh.retopo_tool_grid_stop", {"type": 'ESC', "value": 'PRESS'}, None),
        ("mesh.retopo_tool_grid_stop", {"type": 'RIGHTMOUSE', "value": 'PRESS'}, None),
        ("mesh.retopo_tool_brush", {"type": 'LEFTMOUSE', "value": 'PRESS', "any": True}, None),
        ("mesh.retopo_tool_brush_size", {"type": 'F', "value": 'PRESS'}, None),
        ("mesh.retopo_tool_brush_strength", {"type": 'F', "value": 'PRESS', "shift": True}, None),
        ("mesh.retopo_tool_alt_cut", {"type": 'LEFTMOUSE', "value": 'PRESS', "alt": True}, None),
        ("mesh.retopo_tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'LEFT_CTRL', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'RIGHT_CTRL', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'LEFT_ALT', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'RIGHT_ALT', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'LEFT_SHIFT', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'RIGHT_SHIFT', "value": 'ANY', "any": True}, None),
    )

    def draw_settings(context, layout, tool):
        settings = context.scene.retopo_tool
        layout.prop(settings, "mode", expand=True)
        layout.prop(settings, "snapping")
        draw_symmetry_settings(layout, settings)
        draw_mode_options(layout, settings)
        if (settings.mode == 'RETOPO' and settings.snapping
                or settings.mode == 'GRID' and settings.fill_merge):
            layout.prop(settings, "merge_radius")


class RP_OT_activate(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_activate"
    bl_label = "Activate Draw Tool"
    bl_description = "Edit the selected mesh, or create an empty mesh when none is selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D' and context.mode in {'OBJECT', 'EDIT_MESH'}

    def execute(self, context):
        if context.mode == 'OBJECT':
            obj = context.active_object
            if not obj or not obj.select_get() or obj.type != 'MESH':
                obj = next((item for item in context.selected_objects if item.type == 'MESH'), None)
            if obj and (not obj.is_editable or not obj.data.is_editable):
                self.report({'WARNING'}, "The selected mesh is not editable")
                return {'CANCELLED'}
            for selected in context.selected_objects:
                selected.select_set(False)
            if obj is None:
                mesh = bpy.data.meshes.new("Retopo")
                obj = bpy.data.objects.new("Retopo", mesh)
                context.collection.objects.link(obj)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
        context.tool_settings.mesh_select_mode = (True, False, False)
        enable_retopology_view(context)
        bpy.ops.wm.tool_set_by_id(name='builtin.select_box' if context.scene.retopo_tool.mode == 'BOX' else RP_Tool.bl_idname)
        return {'FINISHED'}


class RP_OT_stop(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_stop"
    bl_label = "Deactivate"
    bl_description = "Return to Blender's selection tool"

    def execute(self, context):
        if context.edit_object:
            topology.clear_pending(bmesh.from_edit_mesh(context.edit_object.data), discard=context.scene.retopo_tool.quad_only)
            sync(context.edit_object)
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
        context.area.header_text_set(None)
        return {'FINISHED'}


class RP_OT_add_reference(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_add_reference"
    bl_label = "Move Selection to Reference"
    bl_description = "Move selected meshes into the Reference collection, creating it if needed"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        settings = context.scene.retopo_tool
        collection = settings.target or bpy.data.collections.get(REFERENCE_COLLECTION_NAME)
        if collection is None:
            collection = bpy.data.collections.new(REFERENCE_COLLECTION_NAME)

        if collection != context.scene.collection and collection not in context.scene.collection.children_recursive:
            context.scene.collection.children.link(collection)

        for obj in tuple(context.selected_objects):
            if obj.type != 'MESH':
                continue
            if obj.name not in collection.objects:
                collection.objects.link(obj)
            for source in tuple(obj.users_collection):
                if source != collection:
                    source.objects.unlink(obj)

        settings.target = collection
        return {'FINISHED'}


def draw_symmetry_settings(layout, settings, sidebar=False):
    if sidebar:
        layout.prop(settings, "x_symmetry")
    row = layout.row(align=True)
    row.use_property_split = False
    if not sidebar:
        row.prop(settings, "x_symmetry")
    for axis, label in enumerate('XYZ'):
        row.prop(settings, "symmetry_axes", index=axis, text=label, toggle=True)

    snap = layout if sidebar else layout.row(align=True)
    snap.use_property_split = False
    snap.prop(settings, "snap_to_center")
    if settings.snap_to_center:
        distance = layout.row() if sidebar else snap
        distance.use_property_split = sidebar
        distance.use_property_decorate = False
        distance.prop(settings, "center_distance")
    if sidebar:
        operator = layout.operator("mesh.retopo_tool_resymmetrize", icon='MOD_MIRROR')
        operator.axis = 'CURRENT'
        operator.source = 'LAST'
        operator.source_x = operator.source_y = operator.source_z = 'LAST'


RESYMMETRIZE_SOURCES = (
    ('LAST', "Last Edit", "Use the side of the last TopoPot geometry edit"),
    ('NEGATIVE', "Negative Side", "Copy the negative side to the positive side"),
    ('POSITIVE', "Positive Side", "Copy the positive side to the negative side"),
)
RESYMMETRIZE_AXES = (
    ('CURRENT', "Enabled Axes", "Use the enabled TopoPot symmetry axes"),
    *SYMMETRY_AXES,
    *((axes, ', '.join(axes), f"Rebuild across {', '.join(axes)}") for axes in ('XY', 'XZ', 'YZ', 'XYZ')),
)


class RP_OT_resymmetrize(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_resymmetrize"
    bl_label = "Resymmetrize"
    bl_description = "Rebuild across all enabled symmetry planes using the last edited side of each axis"
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(name="Source", items=RESYMMETRIZE_SOURCES, default='LAST')
    source_x: EnumProperty(name="X Source", items=RESYMMETRIZE_SOURCES, default='LAST')
    source_y: EnumProperty(name="Y Source", items=RESYMMETRIZE_SOURCES, default='LAST')
    source_z: EnumProperty(name="Z Source", items=RESYMMETRIZE_SOURCES, default='LAST')
    axis: EnumProperty(name="Axes", items=RESYMMETRIZE_AXES, default='CURRENT')

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.edit_object is not None

    def invoke(self, context, event):
        if self.axis == 'CURRENT':
            axes = symmetry_options(context).axes
            if axes:
                self.axis = ''.join('XYZ'[axis] for axis in axes)
        return RP_OT_resymmetrize.apply(self, context, prompt=True)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'axis')
        axes = (symmetry_options(context).axes if self.axis == 'CURRENT'
                else tuple('XYZ'.index(axis) for axis in self.axis))
        for axis in axes:
            layout.prop(self, 'source_' + 'xyz'[axis])

    def execute(self, context):
        return RP_OT_resymmetrize.apply(self, context, prompt=False)

    def apply(self, context, prompt):
        axes = (symmetry_options(context).axes if self.axis == 'CURRENT'
                else tuple('XYZ'.index(axis) for axis in self.axis))
        if not axes:
            self.report({'WARNING'}, "Enable a symmetry axis")
            return {'CANCELLED'}
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        sides = topology.last_edit_sides(bm)
        sources = {}
        try:
            for axis in axes:
                available = {topology.plane_side(v.co[axis]) for v in bm.verts} - {0}
                if not available:
                    continue
                source = getattr(self, 'source_' + 'xyz'[axis])
                if source == 'LAST':
                    source = self.source
                side = {'NEGATIVE': -1, 'POSITIVE': 1}.get(source, sides[axis])
                if not side and len(available) == 1:
                    side = next(iter(available))
                if side not in available:
                    raise ValueError(f"Choose a source side with geometry on {'XYZ'[axis]}")
                sources[axis] = side
            if not sources:
                self.report({'INFO'}, "The mesh lies on the enabled center planes")
                return {'CANCELLED'}
            scratch = bm.copy()
            try:
                topology.resymmetrize_axes(scratch, sources)
            finally:
                scratch.free()
        except (ValueError, RuntimeError) as error:
            self.report({'WARNING'}, str(error))
            return context.window_manager.invoke_props_dialog(self) if prompt else {'CANCELLED'}
        topology.resymmetrize_axes(bm, sources)
        sync(obj)
        tag_view3d_redraw(context)
        return {'FINISHED'}


class RP_PT_panel(bpy.types.Panel):
    bl_label = f"{ADDON_NAME} {ADDON_VERSION}"
    bl_idname = "RP_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "TopoPot"

    def draw(self, context):
        pass


class RP_SidebarSection:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "TopoPot"
    bl_parent_id = "RP_PT_panel"


class RP_PT_tools(RP_SidebarSection, bpy.types.Panel):
    bl_label = "Tools"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        running = tool and tool.idname == RP_Tool.bl_idname
        layout.operator("mesh.retopo_tool_stop" if running else "mesh.retopo_tool_activate",
                        icon='PAUSE' if running else 'PLAY')
        settings = context.scene.retopo_tool
        modes = layout.column(align=True)
        row = modes.row(align=True)
        row.prop_enum(settings, "mode", 'RETOPO')
        row.prop_enum(settings, "mode", 'BRUSH')
        row = modes.row(align=True)
        row.prop_enum(settings, "mode", 'BOX')
        row.prop_enum(settings, "mode", 'GRID')


class RP_PT_tool_options(RP_SidebarSection, bpy.types.Panel):
    bl_label = "Tool Options"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        layout.use_property_decorate = False
        settings = context.scene.retopo_tool
        options = layout.column(align=True)
        draw_mode_options(options, settings)
        options.prop(settings, "snapping")
        radii = layout.column(align=True)
        radii.use_property_split = True
        radii.prop(settings, "radius")
        radii.prop(settings, "merge_radius")


class RP_PT_symmetry(RP_SidebarSection, bpy.types.Panel):
    bl_label = "Symmetry"
    bl_order = 2
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        draw_symmetry_settings(self.layout, context.scene.retopo_tool, sidebar=True)


class RP_PT_reference(RP_SidebarSection, bpy.types.Panel):
    bl_label = "Reference"
    bl_order = 3
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        settings = context.scene.retopo_tool
        layout.prop(settings, "target", text="Collection")
        layout.prop(settings, "offset")
        layout.operator("mesh.retopo_tool_add_reference", icon='OUTLINER_COLLECTION')


class RP_PT_display(RP_SidebarSection, bpy.types.Panel):
    bl_label = "Display"
    bl_order = 4
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        overlay = context.space_data.overlay
        toggle = layout.row()
        toggle.use_property_split = False
        toggle.prop(overlay, "show_retopology", text="Retopology View")
        prefs = addon_preferences(context)
        if prefs is not None:
            colors = layout.split(factor=0.8, align=True)
            colors.use_property_split = False
            colors.prop(prefs, "retopo_color_preset", text="Colors")
            swatches = colors.row(align=True)
            swatches.enabled = prefs.retopo_color_preset == 'CUSTOM'
            swatches.prop(prefs, "retopo_color", text="")
            swatches.prop(prefs, "overlay_color", text="")
        if overlay.show_retopology:
            layout.prop(overlay, "retopology_offset", text="View Offset")


def grid_side_corners(context, border):
    """Use the side labels' corner choices for the four rim separators."""
    if not fill_quads_only(context):
        return []
    sides = topology.rim_sides(border)
    return [side[0] for side in sides] if len(sides) == 4 else []


def grid_side_label_positions(context, border, matrix):
    if not fill_quads_only(context):
        return []
    projected = [view3d_utils.location_3d_to_region_2d(context.region, context.region_data, matrix @ co)
                 for co in border]
    if not projected or any(point is None for point in projected):
        return []
    center = sum(projected, Vector((0, 0))) / len(projected)
    scale = context.preferences.system.ui_scale
    projected_by_point = {tuple(co): point for co, point in zip(border, projected)}
    labels = []
    for side in topology.rim_sides(border):
        points = [projected_by_point[tuple(co)] for co in side]
        lengths = [(b - a).length for a, b in zip(points, points[1:])]
        remaining = sum(lengths) / 2
        position = points[0].copy()
        for a, b, length in zip(points, points[1:], lengths):
            if remaining <= length:
                position = a.lerp(b, remaining / length) if length else a.copy()
                break
            remaining -= length
        position += (position - center).normalized() * (16 * scale)
        labels.append((str(len(side)), position))
    return labels


def draw_grid_side_counts(context, labels):
    if not labels:
        return
    scale = context.preferences.system.ui_scale
    blf.size(0, 18 * scale)
    blf.enable(0, blf.SHADOW)
    blf.shadow(0, 3, 0.0, 0.0, 0.0, 1.0)
    try:
        dimensions = {label: blf.dimensions(0, label) for label in {label for label, _ in labels}}
        for label, position in labels:
            width, height = dimensions[label]
            x, y = position.x - width / 2, position.y - height / 2
            blf.color(0, 0.0, 0.0, 0.0, 1.0)
            for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0),
                           (1, 0), (-1, 1), (0, 1), (1, 1)):
                blf.position(0, x + dx * 2 * scale, y + dy * 2 * scale, 0)
                blf.draw(0, label)
            blf.color(0, 1.0, 1.0, 1.0, 1.0)
            blf.position(0, x, y, 0)
            blf.draw(0, label)
    finally:
        blf.disable(0, blf.SHADOW)


def mirrored_preview(kind, polygons, axis=0):
    if not polygons:
        return kind, polygons
    if kind in {'FACE', 'STRIP'}:
        groups = [polygons] if kind == 'FACE' else polygons
    elif kind in {'LINES', 'POINTS'}:
        size = 2 if kind == 'LINES' else 1
        groups = [polygons[i:i + size] for i in range(0, len(polygons), size)]
    else:
        return kind, polygons
    keys = {frozenset(map(tuple, group)) for group in groups}
    copies = []
    for group in groups:
        mirrored = [topology.mirrored_coordinate(co, axis) for co in reversed(group)]
        key = frozenset(map(tuple, mirrored))
        if key not in keys:
            copies.append(mirrored)
            keys.add(key)
    if not copies:
        return kind, polygons
    if kind in {'FACE', 'STRIP'}:
        return 'STRIP', groups + copies
    return kind, polygons + [co for group in copies for co in group]


def preview_reflections(kind, polygons, axes):
    """Return only newly reflected geometry, excluding shared seam geometry."""
    if not polygons:
        return kind, []
    if kind == 'FACE':
        kind, polygons = 'STRIP', [polygons]
    count = len(polygons)
    for axis in axes:
        kind, polygons = mirrored_preview(kind, polygons, axis)
    return kind, polygons[count:]


def preview_batches(kind, polygons, project=None):
    if kind == 'STRIP':
        triangles, lines = [], []
        for polygon in polygons:
            outline = project(polygon) if project is not None else polygon
            if len(outline) not in (3, 4):
                continue
            triangles.extend(outline[i] for i in ((0, 1, 2) if len(outline) == 3 else (0, 1, 2, 0, 2, 3)))
            for start, end in zip(outline, outline[1:] + outline[:1]):
                lines.extend((start, end))
        return (('TRIS', triangles, 0.25), ('LINES', lines, 1.0))
    points = project(polygons) if project is not None else polygons
    if kind == 'FACE':
        if len(points) < 3:
            return ()
        triangles = [points[i] for i in ((0, 1, 2) if len(points) == 3 else (0, 1, 2, 0, 2, 3))]
        return (('TRIS', triangles, 0.25), ('LINE_STRIP', points + [points[0]], 1.0))
    return ((kind, points, 1.0),)


def grid_overlay_geometry(context, previews, matrix, project):
    cache = _grid_preview_cache
    scale = context.preferences.system.ui_scale
    entry = cache.get('overlay')
    if entry is not None and entry[0] == scale:
        return entry[1]
    groups, labels, corners = {}, [], []
    for polygons, border_coords, error in previews:
        color = GRID_ERROR_COLOR if error else GRID_COLOR
        triangles, lines = groups.setdefault(color, ([], []))
        fill, outline = preview_batches('STRIP', polygons, project)
        triangles.extend(fill[1])
        lines.extend(outline[1])
        border = project(border_coords)
        if border:
            for start, end in zip(border, border[1:] + border[:1]):
                lines.extend((start, end))
            labels.extend(grid_side_label_positions(context, border_coords, matrix))
            corners.extend(project(grid_side_corners(context, border_coords)))
    result = groups, labels, corners
    cache['overlay'] = scale, result
    return result


def draw_reflected_preview(context, matrix, shader, kind, polygons, snapped_points, color):
    if not polygons and not snapped_points:
        return
    batches = [(batch_for_shader(shader, primitive, {"pos": points}), opacity)
               for primitive, points, opacity in preview_batches(kind, polygons) if points]
    markers = (batch_for_shader(shader, 'POINTS', {"pos": snapped_points})
               if snapped_points else None)
    projection = context.region_data.perspective_matrix.copy()
    # Bias coplanar fragments slightly toward the view to avoid depth flicker.
    projection[2] -= projection[3] * 1e-6
    previous_depth = gpu.state.depth_test_get()
    previous_mask = gpu.state.depth_mask_get()
    with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
        gpu.matrix.load_matrix(matrix)
        gpu.matrix.load_projection_matrix(projection)
        gpu.state.depth_mask_set(False)
        try:
            shader.bind()
            for depth, tint in (('GREATER', 0.35), ('LESS_EQUAL', 1.0)):
                gpu.state.depth_test_set(depth)
                rgb = tuple(channel * tint for channel in color[:3])
                for batch, opacity in batches:
                    shader.uniform_float("color", (*rgb, color[3] * opacity))
                    batch.draw(shader)
                if markers is not None:
                    gpu.state.point_size_set(15)
                    shader.uniform_float("color", (*rgb, 1.0))
                    markers.draw(shader)
                    gpu.state.point_size_set(9)
        finally:
            gpu.state.depth_test_set(previous_depth)
            gpu.state.depth_mask_set(previous_mask)
            gpu.state.point_size_set(9)


def draw_hover():
    context = bpy.context
    if context.mode != 'EDIT_MESH' or not context.region_data or not context.edit_object:
        return
    tool = context.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
    if not tool or tool.idname != RP_Tool.bl_idname:
        return
    settings = context.scene.retopo_tool
    symmetry = symmetry_options(context)
    if settings.mode != 'GRID' and context.region.as_pointer() != _mouse_region:
        return
    mouse = Vector(context.window_manager.retopo_tool_mouse) - Vector((context.region.x, context.region.y))
    if settings.mode != 'GRID' and not (0 <= mouse.x < context.region.width and 0 <= mouse.y < context.region.height):
        return
    if navigation_changed(context):
        if settings.mode == 'GRID':
            queue_navigation_redraw()
        return
    if settings.mode == 'BRUSH':
        radius = settings.brush_radius
        points = [(mouse.x + radius * cos(tau * i / 64), mouse.y + radius * sin(tau * i / 64), 0) for i in range(65)]
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        color = (1.0, 0.65, 0.15, 1.0) if context.window_manager.retopo_tool_shift else (0.15, 0.9, 0.65, 1.0)
        shader.uniform_float("color", color)
        batch_for_shader(shader, 'LINE_STRIP', {"pos": points}).draw(shader)
        # Leave space between the strength ring and the radius even at 100%.
        strength_radius = max(1.5, radius * 0.75 * settings.brush_strength)
        strength_points = [(mouse.x + strength_radius * cos(tau * i / 64),
                            mouse.y + strength_radius * sin(tau * i / 64), 0) for i in range(65)]
        ring = batch_for_shader(shader, 'LINE_STRIP', {"pos": strength_points})
        try:
            gpu.state.blend_set('ALPHA')
            gpu.state.line_width_set(1)
            shader.uniform_float("color", (*color[:3], 0.45))
            ring.draw(shader)
        finally:
            gpu.state.line_width_set(1)
            gpu.state.blend_set('NONE')
        return
    bm = bmesh.from_edit_mesh(context.edit_object.data)
    mouse_inside = (context.region.as_pointer() == _mouse_region
                    and 0 <= mouse.x < context.region.width and 0 <= mouse.y < context.region.height)
    reshaping = settings.mode == 'GRID' and _grid_drag_region == context.region.as_pointer()
    grid_previews = grid_visible_previews(context, bm) if settings.mode == 'GRID' and not reshaping else []
    alt_hover = (mouse_inside and getattr(context.window_manager, 'retopo_tool_alt', False)
                  and not context.window_manager.retopo_tool_ctrl and not context.window_manager.retopo_tool_shift)
    hit = (knife_target(context, bm, mouse) if alt_hover else pick(context, bm, mouse)) if mouse_inside and not reshaping else None
    matrix = context.edit_object.matrix_world
    polygons = []
    kind = 'POINTS'
    preview_color = retopo_color(context)
    snapped_points = []
    if _strip_preview is not None and _strip_preview[0] == context.region.as_pointer():
        kind, polygons = 'STRIP', _strip_preview[1]
        preview_color = STRIP_CONNECT_COLOR if _strip_target is not None else STRIP_COLOR
    elif _knife_preview is not None and _knife_preview[0] == context.region.as_pointer():
        kind, polygons = _knife_preview[1], _knife_preview[2]
    elif _loop_preview is not None and _loop_preview[0] == context.region.as_pointer():
        kind, polygons = 'LINES', _loop_preview[1]
    elif alt_hover:
        anchor = knife_anchor(bm)
        view = context.region_data.perspective_matrix
        factor = edge_parameter(context, hit, mouse) if isinstance(hit, bmesh.types.BMEdge) else 0.5
        cache_key = ('KNIFE', object_key(context.edit_object), object_key(context.edit_object.data),
                     len(bm.verts), len(bm.edges), len(bm.faces), context.region.as_pointer(), tuple(mouse),
                     tuple(value for row in matrix for value in row),
                     tuple(value for row in view for value in row), preview_element_key(anchor),
                     preview_element_key(hit), factor, settings.snapping, settings.offset,
                     object_key(settings.target) if settings.target else None, symmetry)

        def compute_knife_preview():
            return knife_preview(bm, anchor, hit, factor, reference_projector(context), symmetry=symmetry)

        kind, polygons = cached_hover_preview(cache_key, compute_knife_preview)
    elif settings.mode == 'GRID':
        kind = 'STRIP'
    elif context.window_manager.retopo_tool_ctrl:
        anchor = topology.active(bm)
        point = placement(context, mouse, anchor) if hit is None else None
        local_point = matrix.inverted_safe() @ point if point is not None else None
        factor = edge_parameter(context, hit, mouse) if isinstance(hit, bmesh.types.BMEdge) else 0.5
        view = context.region_data.perspective_matrix
        cache_key = (object_key(context.edit_object), object_key(context.edit_object.data),
                     len(bm.verts), len(bm.edges), len(bm.faces),
                     context.region.as_pointer(), tuple(mouse),
                     tuple(value for row in matrix for value in row),
                     tuple(value for row in view for value in row),
                     preview_element_key(anchor), preview_element_key(hit),
                     tuple(local_point) if local_point is not None else None, factor,
                     context.window_manager.retopo_tool_shift, settings.snapping,
                     settings.offset, settings.quad_only, settings.merge_radius, getattr(settings, 'auto_reselect', True),
                     object_key(settings.target) if settings.target else None, symmetry)

        def compute_preview():
            return preview(bm, anchor, hit, local_point, factor,
                           context.window_manager.retopo_tool_shift, with_kind=True,
                           project=reference_projector(context), quad_only=settings.quad_only,
                           snap_distance=quad_snap_distance(context), snapped_points=snapped_points,
                           auto_reselect=getattr(settings, 'auto_reselect', True), allow_cut=False,
                           symmetry=symmetry)

        kind, polygons = cached_hover_preview(cache_key, compute_preview, snapped_points)

    def screen(coords):
        result = [view3d_utils.location_3d_to_region_2d(context.region, context.region_data, matrix @ co) for co in coords]
        return [] if any(p is None for p in result) else [(p.x, p.y, 0) for p in result]

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    def draw(mode, points, color):
        if points:
            shader.bind()
            shader.uniform_float("color", color)
            batch_for_shader(shader, mode, {"pos": points}).draw(shader)

    gpu.state.blend_set('ALPHA')
    gpu.state.point_size_set(9)
    gpu.state.line_width_set(3)
    try:
        if grid_previews:
            groups, labels, corners = grid_overlay_geometry(context, grid_previews, matrix, screen)
            for color, (triangles, lines) in groups.items():
                draw('TRIS', triangles, (*color[:3], color[3] * 0.25))
                draw('LINES', lines, color)
            draw_grid_side_counts(context, labels)
            if corners:
                scale = context.preferences.system.ui_scale
                gpu.state.point_size_set(15 * scale)
                draw('POINTS', corners, (0.0, 0.0, 0.0, 1.0))
                gpu.state.point_size_set(9 * scale)
                draw('POINTS', corners, (1.0, 1.0, 1.0, 1.0))
                gpu.state.point_size_set(9)
        if symmetry.enabled:
            reflected_kind, reflected = preview_reflections(kind, polygons, symmetry.axes)
            _, reflected_points = preview_reflections('POINTS', snapped_points, symmetry.axes)
            draw_reflected_preview(context, matrix, shader, reflected_kind, reflected, reflected_points, preview_color)
        for primitive, points, opacity in preview_batches(kind, polygons, screen):
            draw(primitive, points, (*preview_color[:3], preview_color[3] * opacity))
        if snapped_points:
            gpu.state.point_size_set(15)
            draw('POINTS', screen(snapped_points), (*preview_color[:3], 1.0))
            gpu.state.point_size_set(9)
        if hit is not None:
            verts = [hit] if isinstance(hit, bmesh.types.BMVert) else hit.verts
            draw('POINTS' if len(verts) == 1 else 'LINES', screen([v.co for v in verts]), preview_color)
        if _strip_target is not None and _strip_target[0] == context.region.as_pointer():
            gpu.state.line_width_set(5)
            draw('LINES', screen(_strip_target[1]), STRIP_CONNECT_COLOR)
            gpu.state.line_width_set(3)
        if _merge_target is not None and _merge_target[0] == context.region.as_pointer():
            gpu.state.point_size_set(13)
            draw('POINTS', screen([_merge_target[1]]), preview_color)
        if (_edge_merge_targets is not None
                and _edge_merge_targets[0] == context.region.as_pointer()):
            gpu.state.point_size_set(13)
            draw('POINTS', screen(_edge_merge_targets[1]), preview_color)
    finally:
        gpu.state.point_size_set(1)
        gpu.state.line_width_set(1)
        gpu.state.blend_set('NONE')


classes = (RP_Settings, RP_OT_alt_cut, RP_OT_grid_fill, RP_OT_grid_stop, RP_OT_click, RP_OT_brush, RP_OT_brush_size, RP_OT_brush_strength, RP_OT_mode, RP_MT_modes, RP_Preferences, RP_OT_hover, RP_OT_activate, RP_OT_stop, RP_OT_add_reference, RP_OT_resymmetrize, RP_PT_panel, RP_PT_tools, RP_PT_tool_options, RP_PT_symmetry, RP_PT_reference, RP_PT_display)
_draw_handle = None
_addon_keymaps = []


def register():
    global _draw_handle
    invalidate_visibility()
    bpy.app.handlers.depsgraph_update_post.append(invalidate_visibility)
    bpy.app.handlers.undo_post.append(invalidate_after_undo)
    bpy.app.handlers.redo_post.append(invalidate_after_undo)
    bpy.app.handlers.load_post.append(invalidate_visibility)
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.retopo_tool = PointerProperty(type=RP_Settings)
    bpy.types.WindowManager.retopo_tool_mouse = bpy.props.IntVectorProperty(size=2, default=(-100, -100))
    bpy.types.WindowManager.retopo_tool_ctrl = bpy.props.BoolProperty(default=False)
    bpy.types.WindowManager.retopo_tool_shift = bpy.props.BoolProperty(default=False)
    bpy.types.WindowManager.retopo_tool_alt = bpy.props.BoolProperty(default=False)
    apply_overlay_preferences()
    enable_retopology_view()
    bpy.utils.register_tool(RP_Tool, after={"builtin.poly_build"}, separator=True)
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        keymap = keyconfig.keymaps.new(name='Mesh', space_type='EMPTY')
        item = keymap.keymap_items.new('wm.call_menu_pie', 'Q', 'PRESS', alt=True, shift=True)
        item.properties.name = RP_MT_modes.bl_idname
        _addon_keymaps.append((keymap, item))
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_hover, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _draw_handle
    if bpy.app.timers.is_registered(redraw_after_navigation):
        bpy.app.timers.unregister(redraw_after_navigation)
    for keymap, item in _addon_keymaps:
        keymap.keymap_items.remove(item)
    _addon_keymaps.clear()
    for handlers, handler in (
            (bpy.app.handlers.depsgraph_update_post, invalidate_visibility),
            (bpy.app.handlers.undo_post, invalidate_after_undo),
            (bpy.app.handlers.redo_post, invalidate_after_undo),
            (bpy.app.handlers.load_post, invalidate_visibility)):
        if handler in handlers:
            handlers.remove(handler)
    invalidate_visibility()
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    bpy.utils.unregister_tool(RP_Tool)
    del bpy.types.WindowManager.retopo_tool_mouse
    del bpy.types.WindowManager.retopo_tool_ctrl
    del bpy.types.WindowManager.retopo_tool_shift
    del bpy.types.WindowManager.retopo_tool_alt
    del bpy.types.Scene.retopo_tool
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
