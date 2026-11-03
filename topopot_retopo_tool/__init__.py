import bpy
import bmesh
import blf
import gpu
import importlib
import time
import tomllib
from math import cos, sin, tau
from pathlib import Path
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from bpy.props import PointerProperty, FloatProperty, IntProperty, EnumProperty, FloatVectorProperty, BoolProperty
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
GRID_HINT = "Fill Hole | Drag vertices / edges | Click edge: collapse | Ctrl-click edge: triangle | Hold Ctrl inside: Quad/Tri | Esc / right-click returns to Draw"


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
    tag_view3d_redraw(context)


def update_retopo_color(self, context):
    preset_color = COLOR_PRESETS.get(self.retopo_color_preset)
    if preset_color and any(abs(a - b) > 1e-4 for a, b in zip(self.retopo_color, preset_color)):
        self.retopo_color_preset = 'CUSTOM'
    tag_view3d_redraw(context)


def apply_overlay_preferences(prefs=None, context=None):
    prefs = prefs or addon_preferences(context)
    color = tuple(prefs.overlay_color) if prefs else DEFAULT_OVERLAY_COLOR
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
    return tuple(prefs.retopo_color) if prefs else COLOR_PRESETS['BLUE']


def update_mode(self, context):
    global _grid_preview_cache
    _grid_preview_cache = None
    if context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D':
        topology.clear_pending(bmesh.from_edit_mesh(context.edit_object.data))
        enable_retopology_view(context)
        bpy.ops.wm.tool_set_by_id(name='builtin.select_box' if self.mode == 'BOX' else RP_Tool.bl_idname)
        context.area.header_text_set(GRID_HINT if self.mode == 'GRID' else None)
        context.area.tag_redraw()


class RP_Settings(bpy.types.PropertyGroup):
    preserve_borders: bpy.props.BoolProperty(name="Preserve Borders", description="Relax only interior surface vertices, keeping open borders and loose geometry fixed", default=False)
    mode: EnumProperty(name="Mode", items=(('RETOPO', "Draw", "Create and move topology"), ('BRUSH', "Brush", "Move vertices; Shift relaxes"), ('BOX', "Box Select", "Blender's native box selection"), ('GRID', "Fill Hole", "Drag vertices or edges; click an edge to collapse; Ctrl-click an edge for a triangle; click inside to fill")), default='RETOPO', update=update_mode)
    auto_reselect: BoolProperty(name="Auto Reselect", description="Automatically switch the Draw starting edge while holding Ctrl", default=True)
    quad_only: BoolProperty(name="Quad Mode", description="Extrude quads with the new edge centered at the cursor; allow triangles when connecting neighboring edges", default=False)
    snapping: BoolProperty(name="Snapping", description="Snap to the reference surface and automatically merge nearby vertices and dragged edge endpoints", default=True)
    fill_merge: BoolProperty(name="Merge on Drag", description="In Fill Hole, snap dragged vertices or edge endpoints onto another vertex and merge on release", default=True)
    brush_radius: IntProperty(name="Brush Radius", default=75, min=5, max=500, subtype='PIXEL')
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
_screen_cache = None
_hole_screen_cache = None
_hover_preview_cache = None
_UNSET = object()
SCREEN_CELL = 16
_hover_view = None
_hover_view_time = 0.0
HOVER_NAV_DELAY = 0.12


def navigation_changed(context):
    """True while the view keeps changing (orbit/pan/zoom), to pause hover work."""
    global _hover_view, _hover_view_time
    view = getattr(context.region_data, 'perspective_matrix', None)
    if view is None:
        return False
    key = tuple(value for row in view for value in row)
    if _hover_view is not None and key != _hover_view:
        _hover_view = key
        _hover_view_time = time.perf_counter()
        return True
    _hover_view = key
    return (_hover_view_time > 0.0
            and (time.perf_counter() - _hover_view_time) < HOVER_NAV_DELAY)


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
    vert_cells = {}
    positions = {}
    for position, vertex in enumerate(bm.verts):
        positions[vertex] = position
        if vertex.hide:
            points.append(None)
            continue
        point = projector(vertex.co)
        points.append(point)
        if point is not None:
            cell = (int(point.x // SCREEN_CELL), int(point.y // SCREEN_CELL))
            vert_cells.setdefault(cell, []).append(position)
    edge_cells = {}
    for position, edge in enumerate(bm.edges):
        if edge.hide:
            continue
        a_index, b_index = positions.get(edge.verts[0]), positions.get(edge.verts[1])
        if a_index is None or b_index is None:
            continue
        a, b = points[a_index], points[b_index]
        if a is None or b is None:
            continue
        entry = (position, a_index, b_index)
        x0, x1 = int(min(a.x, b.x) // SCREEN_CELL), int(max(a.x, b.x) // SCREEN_CELL)
        y0, y1 = int(min(a.y, b.y) // SCREEN_CELL), int(max(a.y, b.y) // SCREEN_CELL)
        if x0 == x1 and y0 == y1:
            edge_cells.setdefault((x0, y0), []).append(entry)
        else:
            for cx in range(x0, x1 + 1):
                for cy in range(y0, y1 + 1):
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
    global _grid_preview_cache, _screen_cache, _hole_screen_cache, _hover_preview_cache
    _grid_preview_cache = None
    if depsgraph is None:
        _screen_cache = None
        _hole_screen_cache = None
        _visibility_cache.clear()
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
                return BVHTree.FromPolygons([evaluated.matrix_world @ v.co for v in mesh.vertices],
                                           [tuple(p.vertices) for p in mesh.polygons])
            finally:
                evaluated.to_mesh_clear()
        trees.append(cached_tree('reference', reference, build))
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


def pick(context, bm, mouse, exclude=None, vertices_only=False, radius=None, edges_only=False, relaxed_reference=False):
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
            trees = (cached_edit_tree(context, bm), reference_tree(context))
        edit_tree, ref_tree = trees
        return (visible_from_view(context, co, edit_tree) and
                (ref_tree is None or visible_from_view(context, co, ref_tree,
                 max(1e-5, context.scene.retopo_tool.offset * 2) if exclude is not None and not relaxed_reference
                 else reference_pick_tolerance(context, co))))
    index = screen_index(context, bm)
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
        auto_reselect=getattr(settings, 'auto_reselect', True))


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


def preview(bm, anchor, hit, point, edge_factor=0.5, bridge_mode=False, with_kind=False, strict=False, project=None, quad_only=False, snap_distance=None, snapped_points=None, auto_reselect=True):
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
        result = topology.apply_click(copy, anchor, hit, point, edge_factor, bridge_mode, project, quad_only, snap_distance, snapped_points, auto_reselect=auto_reselect)
        if isinstance(result, list):
            kind = 'LINES'
            coords = [v.co.copy() for edge in result for v in edge.verts]
        else:
            verts = [result] if isinstance(result, bmesh.types.BMVert) else result.verts
            kind = 'FACE' if isinstance(result, bmesh.types.BMFace) else ('POINTS' if len(verts) == 1 else 'LINES')
            coords = [v.co.copy() for v in verts]
        return (kind, coords) if with_kind else coords
    except ValueError:
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
    global _grid_preview_cache, _screen_cache, _hole_screen_cache, _hover_preview_cache
    _grid_preview_cache = None
    _screen_cache = None
    _hole_screen_cache = None
    _hover_preview_cache = None
    _visibility_cache.pop(('edit', object_key(obj)), None)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.normal_update()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=destructive)


def strip_preview(bm, edge, points, snap=None, target=None, snap_distance=None, snapped_points=None, triangle=False):
    bm.edges.index_update()
    copy = bm.copy()
    try:
        copy.edges.ensure_lookup_table()
        source = copy.edges[edge.index]
        destination = copy.edges[target.index] if target is not None else None
        faces = topology.quad_strip(copy, source, points, snap, destination, snap_distance, snapped_points, triangle)
        return [[v.co.copy() for v in face.verts] for face in faces]
    finally:
        copy.free()


def loop_cut_preview(bm, edge, factor, project=None):
    """Run the actual loop-cut operation on a copy for hover/click previews."""
    bm.edges.index_update()
    copy = bm.copy()
    try:
        copy.edges.ensure_lookup_table()
        cuts = topology.loop_cut(copy, copy.edges[edge.index], factor, project)
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


def knife_preview(bm, anchor, hit, factor=0.5, project=None, strict=False):
    if hit is None:
        if strict:
            raise ValueError("Alt-click a vertex or edge to cut")
        return 'POINTS', []
    return preview(bm, anchor, hit, None, factor, with_kind=True, strict=strict, project=project)


class RP_OT_alt_cut(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_alt_cut"
    bl_label = "TopoPot Knife / Loop Cut"
    bl_description = "Alt-click inserts/connects knife points; Alt-drag slides a loop cut along its source edge"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW'
                and context.scene.retopo_tool.mode == 'RETOPO')

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
        kind, coords = knife_preview(bm, anchor, hit, self.cut_factor, self.cut_projector)
        _knife_preview = (self.region.as_pointer(), kind, coords)
        self.area.header_text_set("Knife | Release to insert/connect | Drag over an edge: loop cut | Esc cancels")
        self.area.tag_redraw()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global _knife_preview, _loop_preview
        if (context.mode != 'EDIT_MESH' or context.edit_object != self.obj
                or context.scene.retopo_tool.mode != 'RETOPO'
                or event.type in {'ESC', 'RIGHTMOUSE'}):
            RP_OT_alt_cut.end(self)
            return {'CANCELLED'}
        track_mouse(context, event)
        bm = bmesh.from_edit_mesh(self.obj.data)
        if (len(bm.verts), len(bm.edges), len(bm.faces)) != self.cut_counts:
            self.report({'WARNING'}, "Cut canceled because mesh topology changed")
            RP_OT_alt_cut.end(self)
            return {'CANCELLED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        if event.type not in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and not release:
            return {'RUNNING_MODAL'}
        mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
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
                    coords = loop_cut_preview(bm, edge, self.loop_factor, self.cut_projector)
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
                if self.dragging:
                    if self.loop_edge_index is None or self.cut_error is not None:
                        raise ValueError(self.cut_error or "Drag over an edge of a quad strip")
                    edge = bm.edges[self.loop_edge_index]
                    topology.loop_cut(bm, edge, self.loop_factor, self.cut_projector)
                else:
                    anchor = bm.verts[self.cut_anchor_index] if self.cut_anchor_index is not None else None
                    hit = (bm.verts[self.cut_hit_index] if self.cut_hit_kind == 'VERT' else
                           bm.edges[self.cut_hit_index] if self.cut_hit_kind == 'EDGE' else None)
                    knife_preview(bm, anchor, hit, self.cut_factor, self.cut_projector, strict=True)
                    apply_knife_cut(bm, anchor, hit, self.cut_factor, self.cut_projector)
                sync(self.obj)
            except (ValueError, RuntimeError) as error:
                self.report({'WARNING'}, str(error))
                RP_OT_alt_cut.end(self)
                return {'CANCELLED'}
            RP_OT_alt_cut.end(self)
            return {'FINISHED'}
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def end(self):
        global _knife_preview, _loop_preview
        _knife_preview = _loop_preview = None
        self.area.header_text_set(None)
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


def boundary_screen_loops(context, bm):
    """Cache screen/world boundary loops until mesh, object, or view changes."""
    global _hole_screen_cache
    region = context.region
    matrix = context.edit_object.matrix_world
    view = getattr(context.region_data, 'perspective_matrix', None)
    region_key = region.as_pointer() if hasattr(region, 'as_pointer') else id(region)
    signature = (object_key(context.edit_object), object_key(context.edit_object.data),
                 tuple(value for row in matrix for value in row),
                 tuple(value for row in view for value in row) if view is not None else None,
                 region_key, getattr(region, 'width', None), getattr(region, 'height', None),
                 len(bm.verts), len(bm.edges), len(bm.faces))
    if _hole_screen_cache is not None and _hole_screen_cache[0] == signature:
        return _hole_screen_cache[1]
    bm.verts.index_update()
    loops = []
    for loop in topology.boundary_loops(bm):
        indices = tuple(vertex.index for vertex in loop)
        world = [matrix @ vertex.co for vertex in loop]
        screen = [view3d_utils.location_3d_to_region_2d(region, context.region_data, co)
                  for co in world]
        normal = polygon_normal(world)
        if normal.length < 1e-10:
            continue
        adjacent = {bm.edges.get((a, b)).link_faces[0]
                    for a, b in zip(loop, loop[1:] + loop[:1])}
        surrounding = sum((polygon_normal([matrix @ vertex.co for vertex in face.verts])
                           for face in adjacent), Vector())
        loops.append((indices, world, screen, normal, surrounding))
    _hole_screen_cache = signature, loops
    return loops


def hole_at_mouse(context, bm, mouse):
    """Choose the nearest front-facing rim containing the cursor in screen space."""
    matrix = context.edit_object.matrix_world
    candidates = []
    origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, mouse)
    direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, mouse)
    loops = boundary_screen_loops(context, bm)
    tree = None
    reference = None
    bm.verts.ensure_lookup_table()
    for loop_indices, world, screen, normal, surrounding in loops:
        if any(p is None for p in screen) or not point_in_polygon(mouse, screen):
            continue
        if normal.dot(surrounding) < -1e-5:
            continue
        denominator = normal.dot(direction)
        # Outer borders traverse the other way; they must not cover existing faces.
        if denominator >= -1e-6:
            continue
        center = sum(world, Vector()) / len(world)
        distance = (center - origin).dot(normal) / denominator
        if distance <= 0:
            continue
        if tree is None:
            tree = cached_edit_tree(context, bm)
            reference = reference_tree(context)
        location, _, _, hit_distance = tree.ray_cast(origin, direction)
        if location is not None and hit_distance <= distance + 1e-5:
            continue
        if any(not visible_from_view(context, co, tree) or
               (reference is not None and not visible_from_view(context, co, reference,
                reference_pick_tolerance(context, co))) for co in world):
            continue
        area = abs(sum(a.x * b.y - b.x * a.y for a, b in zip(screen, screen[1:] + screen[:1])))
        candidates.append((distance, area, [bm.verts[index] for index in loop_indices]))
    return min(candidates, key=lambda item: item[:2])[2] if candidates else None


def grid_preview(bm, vertices, snap=None, quad_only=False):
    copy = bm.copy()
    try:
        copy.verts.ensure_lookup_table()
        indices = {v: i for i, v in enumerate(bm.verts)}
        rim = [copy.verts[indices[v]] for v in vertices]
        faces = topology.fill_hole(copy, rim, snap, quad_only)
        return [[v.co.copy() for v in face.verts] for face in faces]
    finally:
        copy.free()


_grid_preview_cache = None


def fill_quads_only(context):
    return not getattr(context.window_manager, 'retopo_tool_ctrl', False)


def grid_hover_preview(context, bm, mouse):
    global _grid_preview_cache
    settings = context.scene.retopo_tool
    quad_only = fill_quads_only(context)
    matrix = context.edit_object.matrix_world
    view = context.region_data.perspective_matrix
    signature = (object_key(context.edit_object), context.region.as_pointer(),
                 tuple(value for row in matrix for value in row),
                 tuple(value for row in view for value in row), context.region.width, context.region.height,
                 len(bm.verts), len(bm.edges), len(bm.faces), settings.snapping,
                 object_key(settings.target) if settings.target else None, settings.offset,
                 REFERENCE_TOLERANCE, quad_only)
    if (_grid_preview_cache is not None and _grid_preview_cache[0] == signature
            and _grid_preview_cache[1] == tuple(mouse)):
        return _grid_preview_cache[3]
    vertices = hole_at_mouse(context, bm, mouse)
    rim_key = tuple(hash(v) for v in vertices) if vertices is not None else None
    polygons, border, error = [], [], None
    if vertices is not None:
        border = [v.co.copy() for v in vertices]
        if (_grid_preview_cache is not None and _grid_preview_cache[0] == signature
                and _grid_preview_cache[2] == rim_key):
            polygons, _, error = _grid_preview_cache[3]
        else:
            try:
                polygons = grid_preview(bm, vertices, reference_projector(context), quad_only)
            except (ValueError, RuntimeError) as exception:
                error = str(exception)
    result = polygons, border, error
    _grid_preview_cache = signature, tuple(mouse), rim_key, result
    return result


def fill_drag_target(context, bm, mouse, vertex, original):
    # The moving vertex can temporarily cover the target with its own faces.
    # Test visibility against the shape before the drag, including reference tolerance.
    current = vertex.co.copy()
    key = ('edit', object_key(context.edit_object))
    try:
        vertex.co = original
        _visibility_cache.pop(key, None)
        return pick(context, bm, mouse, exclude=vertex, vertices_only=True,
                    radius=context.scene.retopo_tool.merge_radius, relaxed_reference=True)
    finally:
        vertex.co = current
        _visibility_cache.pop(key, None)


def edge_merge_targets(context, bm, verts, originals, positions, merge=True):
    """Find a vertex within Merge Radius for each dragged edge endpoint."""
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
        for vertex, position in zip(verts, positions):
            screen = view3d_utils.location_3d_to_region_2d(
                context.region, context.region_data, matrix @ position)
            if screen is None:
                continue
            target = pick(context, bm, screen, exclude=verts, vertices_only=True,
                          radius=settings.merge_radius, relaxed_reference=True)
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
    return {source: target for source, target in targets.items() if source not in duplicates}


def merge_edge_targets(bm, verts, targets):
    """Weld dragged edge endpoints into their highlighted merge targets."""
    claimed = set()
    for vertex in verts:
        target = targets.get(vertex)
        if (target is None or target == vertex or not vertex.is_valid
                or not target.is_valid or target.hide or target in claimed):
            continue
        claimed.add(target)
        topology.merge_vertices(bm, vertex, target)


def edge_merge_positions(context, bm, verts, originals, positions, merge=True):
    """Snap edge endpoints onto visible merge targets and return the highlights."""
    targets = edge_merge_targets(context, bm, verts, originals, positions, merge)
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
    bl_description = "Drag vertices or edges to reshape; click an edge to collapse; Ctrl-click an edge to split and create a triangle; click inside to fill"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW' and context.scene.retopo_tool.mode == 'GRID')

    def invoke(self, context, event):
        global _merge_target, _edge_merge_targets
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
            self.area.header_text_set("Reshape hole | Drag edge | Click: collapse | Ctrl-click: triangle | Esc / right-click cancels")
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        vertices = hole_at_mouse(context, bm, mouse)
        if vertices is None:
            self.report({'WARNING'}, "Point inside a visible closed hole")
            return {'CANCELLED'}
        try:
            snap = reference_projector(context)
            quad_only = fill_quads_only(context)
            grid_preview(bm, vertices, snap, quad_only)
            topology.fill_hole(bm, vertices, snap, quad_only)
            sync(context.edit_object)
        except (ValueError, RuntimeError) as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        context.area.header_text_set(GRID_HINT)
        context.area.tag_redraw()
        return {'FINISHED'}

    def modal(self, context, event):
        global _merge_target
        if getattr(self, 'edge_drag', False):
            return RP_OT_grid_fill.modal_edge(self, context, event)
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            _merge_target = None
            self.area.header_text_set(None)
            return {'CANCELLED'}
        # Blender may rebuild the edit BMesh between invoke and modal events.
        # Reacquire the vertex rather than retaining an invalid BMVert handle.
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if self.mesh_counts != (len(bm.verts), len(bm.edges), len(bm.faces)) or self.vertex_index >= len(bm.verts):
            _merge_target = None
            self.area.header_text_set(GRID_HINT if context.scene.retopo_tool.mode == 'GRID' else None)
            return {'CANCELLED'}
        vertex = bm.verts[self.vertex_index]
        if context.scene.retopo_tool.mode != 'GRID' or event.type in {'ESC', 'RIGHTMOUSE'}:
            _merge_target = None
            vertex.co = self.original
            sync(self.obj, destructive=False)
            self.area.header_text_set(GRID_HINT if context.scene.retopo_tool.mode == 'GRID' else None)
            self.area.tag_redraw()
            # Consume the cancel click so it cannot also invoke Leave Fill Hole.
            return {'FINISHED'} if event.type in {'ESC', 'RIGHTMOUSE'} else {'CANCELLED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or release:
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            self.dragging |= (mouse - self.start).length > 3
            with context.temp_override(area=self.area, region=self.region):
                track_mouse(context, event)
                if self.dragging:
                    settings = context.scene.retopo_tool
                    target = fill_drag_target(context, bm, mouse, vertex, self.original) if settings.fill_merge else None
                    _merge_target = (self.region.as_pointer(), target.co.copy()) if target is not None else None
                    point = self.obj.matrix_world @ target.co if target is not None else project(
                        context, mouse + self.grab_offset, self.obj.matrix_world @ self.original)
                    if point is not None:
                        vertex.co = self.obj.matrix_world.inverted_safe() @ point
                        if release and target is not None:
                            topology.merge_vertices(bm, vertex, target)
                            sync(self.obj)
                        else:
                            sync(self.obj, destructive=False)
                    self.area.header_text_set("Reshape hole | Release to merge vertices | Esc / right-click cancels" if target is not None
                                              else "Reshape hole | Drag vertex | Release to keep | Esc / right-click cancels")
            self.area.tag_redraw()
        if release:
            _merge_target = None
            self.area.header_text_set(GRID_HINT)
            return {'FINISHED'} if self.dragging else {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal_edge(self, context, event):
        global _edge_merge_targets
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            _edge_merge_targets = None
            self.area.header_text_set(None)
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if self.mesh_counts != (len(bm.verts), len(bm.edges), len(bm.faces)):
            _edge_merge_targets = None
            self.area.header_text_set(GRID_HINT)
            return {'CANCELLED'}
        verts = [bm.verts[i] for i in self.move_indices]
        if context.scene.retopo_tool.mode != 'GRID' or event.type in {'ESC', 'RIGHTMOUSE'}:
            for vertex, original in zip(verts, self.move_originals):
                vertex.co = original
            _edge_merge_targets = None
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
                        positions = edge_drag_positions(context, self.move_originals, self.start, mouse,
                                                        reference_projector(context))
                        positions, targets = edge_merge_positions(
                            context, bm, verts, self.move_originals, positions,
                            context.scene.retopo_tool.fill_merge)
                        for vertex, position in zip(verts, positions):
                            vertex.co = position
                        _edge_merge_targets = ((self.region.as_pointer(),
                                                [target.co.copy() for target in targets.values()])
                                               if targets else None)
                        if release and targets:
                            merge_edge_targets(bm, verts, targets)
                            _edge_merge_targets = None
                            sync(self.obj)
                        else:
                            sync(self.obj, destructive=False)
                    elif release:
                        edge = bm.edges.get(verts)
                        if self.edge_ctrl:
                            factor = self.edge_factor if edge.verts[0] == verts[0] else 1 - self.edge_factor
                            target = fill_triangle_target(context, bm, edge, factor)
                            topology.split_edge_triangle(bm, edge, factor, target)
                        else:
                            topology.collapse_edge_midpoint(bm, edge)
                        sync(self.obj)
            except (ValueError, RuntimeError) as error:
                if release:
                    self.report({'WARNING'}, str(error))
                    self.area.header_text_set(GRID_HINT)
                    return {'FINISHED'}
            self.area.tag_redraw()
        if release:
            _edge_merge_targets = None
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
        if (event.ctrl and not event.shift and getattr(settings, 'auto_reselect', True)
                and isinstance(anchor, bmesh.types.BMVert)
                and not topology.pending(bm) and (hit is None or hit == anchor)
                and len([v for v in bm.verts if v.select and not v.hide]) == 1):
            # A single selected vertex starts from its closest full edge, so the
            # click/drag builds a face instead of chaining another vertex.
            start_edge = vertex_start_edge(context, anchor, mouse)
            if start_edge is not None:
                topology.clear_pending(bm, discard=settings.quad_only)
                topology.select(bm, start_edge)
                sync(obj, destructive=False)
                if hit == anchor:
                    return {'FINISHED'}
                anchor = start_edge
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
                # Validate on a copy before splitting any live edges.
                snap = reference_projector(context)
                preview(bm, anchor, hit, point, factor, event.shift, strict=True,
                        project=snap, quad_only=settings.quad_only,
                        snap_distance=quad_snap_distance(context),
                        auto_reselect=getattr(context.scene.retopo_tool, 'auto_reselect', True))
                apply_draw_click(context, bm, anchor, hit, point, factor, event.shift, snap)
                sync(obj)
            except ValueError as error:
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
            vertex.co = self.original
            sync(self.obj, destructive=False)
            return {'FINISHED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self.dragging:
                mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
                with context.temp_override(area=self.area, region=self.region):
                    target = pick(context, bm, mouse, exclude=vertex, vertices_only=True,
                                  radius=settings.merge_radius) if settings.snapping else None
                if target is not None:
                    topology.merge_vertices(bm, vertex, target, settings.quad_only)
                    sync(self.obj)
            _merge_target = None
            self.area.tag_redraw()
            return {'FINISHED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            self.dragging |= (mouse - self.start).length > 3
            if self.dragging:
                obj = self.obj
                with context.temp_override(area=self.area, region=self.region):
                    target = pick(context, bm, mouse, exclude=vertex, vertices_only=True,
                                  radius=settings.merge_radius) if settings.snapping else None
                    point = obj.matrix_world @ target.co if target is not None else project(
                        context, mouse, obj.matrix_world @ self.original)
                _merge_target = (self.region.as_pointer(), target.co.copy()) if target is not None else None
                if point is not None:
                    vertex.co = obj.matrix_world.inverted_safe() @ point
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
                            free_polygons = strip_preview(bm, edge, free_points, self.strip_snap, None, snap_dist)
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
                                triangle=triangle)
                    else:
                        center = (target.verts[0].co + target.verts[1].co) * 0.5
                        path = self.strip_path.copy()
                        path.add(center)
                        points = path.positions()
                        while points and (points[-1] - center).length < path.spacing * 0.6:
                            points.pop()
                        try:
                            polygons = strip_preview(bm, edge, points, self.strip_snap, target, snap_dist)
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
                        topology.quad_strip(bm, edge, points, self.strip_snap, target, snap_dist,
                                            triangle=triangle)
                        sync(self.obj)
                    elif release and not self.had_strip_faces:
                        raise ValueError("Drag farther from the starting edge to draw a strip")
                elif release:
                    hit = edge if self.strip_on_edge else None
                    point = placement(context, self.start, edge) if hit is None else None
                    point = self.obj.matrix_world.inverted_safe() @ point if point is not None else None
                    factor = edge_parameter(context, edge, self.start) if hit is not None else 0.5
                    settings = context.scene.retopo_tool
                    preview(bm, edge, hit, point, factor, strict=True, project=self.strip_snap,
                            quad_only=settings.quad_only,
                            snap_distance=quad_snap_distance(context),
                        auto_reselect=getattr(context.scene.retopo_tool, 'auto_reselect', True))
                    apply_draw_click(context, bm, edge, hit, point, factor, project=self.strip_snap)
                    sync(self.obj)
        except ValueError as error:
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
        if any(i >= len(bm.verts) for i in self.move_indices):
            _edge_merge_targets = None
            return {'CANCELLED'}
        verts = [bm.verts[i] for i in self.move_indices]
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            for vert, original in zip(verts, self.move_originals):
                vert.co = original
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
                            self.move_snap = reference_projector(context)
                        positions = edge_drag_positions(context, self.move_originals, self.start, mouse, self.move_snap)
                        positions, targets = edge_merge_positions(
                            context, bm, verts, self.move_originals, positions,
                            context.scene.retopo_tool.snapping)
                    for vert, position in zip(verts, positions):
                        vert.co = position
                    _edge_merge_targets = ((self.region.as_pointer(),
                                            [target.co.copy() for target in targets.values()])
                                           if targets else None)
                    if release and targets:
                        merge_edge_targets(bm, verts, targets)
                        _edge_merge_targets = None
                        sync(self.obj)
                    else:
                        sync(self.obj, destructive=False)
                except ValueError as error:
                    if release:
                        self.report({'WARNING'}, str(error))
            self.area.tag_redraw()
        if release:
            _edge_merge_targets = None
        return {'FINISHED'} if release else {'RUNNING_MODAL'}

    def modal_edge(self, context, event):
        """Ctrl-click splits/bridges; dragging here never starts a loop cut."""
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
                    snap = reference_projector(context)
                    preview(bm, anchor, edge, None, factor, strict=True, project=snap,
                            quad_only=context.scene.retopo_tool.quad_only,
                            snap_distance=quad_snap_distance(context),
                        auto_reselect=getattr(context.scene.retopo_tool, 'auto_reselect', True))
                    apply_draw_click(context, bm, anchor, edge, edge_factor=factor, project=snap)
                    sync(self.obj)
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
        if relaxing:
            weighted = brush_vertices(context, bm, mouse, self.brush_tree, self.brush_reference)
            positions = relax_positions(weighted, context.scene.retopo_tool.preserve_borders)
        else:
            matrix = self.obj.matrix_world
            inverse = matrix.inverted_safe().to_3x3()
            positions = {}
            # Edit-mesh updates can invalidate BMVert wrappers between modal events.
            for index, weight in self.weighted:
                vert = bm.verts[index]
                depth = matrix @ vert.co
                a = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, self.last, depth)
                b = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, mouse, depth)
                positions[vert] = vert.co + inverse @ (b - a) * weight
        positions = {vert: (snap(co) if snap else co) for vert, co in positions.items()}
        for vert, co in positions.items():
            vert.co = co
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
    retopo_color_preset: EnumProperty(name="Draw Color Preset", items=COLOR_PRESET_ITEMS, default='BLUE', update=update_retopo_color_preset)
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
        colors.label(text="Draw Colors")
        colors.prop(self, "retopo_color_preset")
        colors.prop(self, "retopo_color")

        overlay = layout.box()
        overlay.label(text="Blender Retopology Overlay")
        overlay.prop(self, "overlay_color")
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
        if context.mode == 'EDIT_MESH' and context.scene.retopo_tool.mode == 'GRID':
            bm = bmesh.from_edit_mesh(context.edit_object.data)
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            polygons, border, error = grid_hover_preview(context, bm, mouse)
            quads = sum(len(polygon) == 4 for polygon in polygons)
            triangles = sum(len(polygon) == 3 for polygon in polygons)
            counts = ', '.join(text for number, text in ((quads, f"{quads} quads"), (triangles, f"{triangles} triangles")) if number)
            fill_mode = 'Quads Only' if fill_quads_only(context) else 'Quad/Tri (Ctrl)'
            hint = 'Hold Ctrl: Quad/Tri' if fill_quads_only(context) else 'Release Ctrl: Quads Only'
            message = (f"Fill Hole | {fill_mode} | {error} | Drag vertices / edges to reshape | {hint}" if error else
                       f"Fill Hole | {fill_mode} | {counts or 'Hover a hole'} | Drag vertices / edges | Click edge: collapse | Ctrl-click edge: triangle | {hint}")
            context.area.header_text_set(message)
        elif (context.mode == 'EDIT_MESH' and context.scene.retopo_tool.mode == 'RETOPO'
              and getattr(context.window_manager, 'retopo_tool_alt', False)
              and not context.window_manager.retopo_tool_ctrl and not context.window_manager.retopo_tool_shift):
            # Alt hover only previews. Selection belongs to the Draw workflow.
            context.area.header_text_set("Knife | Alt-click inserts/connects | Alt-drag over an edge: loop cut")
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
                # the key press switches, so a split vertex stays selected for
                # cutting while Ctrl remains held.
                nearest = pick(context, bm, mouse, edges_only=True)
                start_edge = (closest_border_edge(context, bm, mouse)
                              if nearest is None or len(nearest.link_faces) <= 1 else None)
                if start_edge is not None:
                    topology.clear_pending(bm, discard=context.scene.retopo_tool.quad_only)
                    topology.select(bm, start_edge)
                    sync(context.edit_object, destructive=False)
                    anchor, switched = start_edge, True
            if (not switched and isinstance(anchor, bmesh.types.BMEdge)
                    and len(anchor.link_faces) == 1 and not topology.pending(bm)):
                hit = pick(context, bm, mouse)
                if reselect_draw_edge(context, bm, anchor, mouse, hit) != anchor:
                    sync(context.edit_object, destructive=False)
        context.area.tag_redraw()
        return {'PASS_THROUGH'}


class RP_Tool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "retopo_tool.tool"
    bl_label = ADDON_NAME
    bl_description = "Select and drag; Ctrl-click creates/cuts/bridges; Ctrl-drag draws strips; Alt-click is knife; Alt-drag cuts a quad loop"
    bl_icon = TOOL_ICON
    bl_keymap = (
        ("mesh.retopo_tool_grid_fill", {"type": 'LEFTMOUSE', "value": 'PRESS', "any": True}, None),
        ("mesh.retopo_tool_grid_stop", {"type": 'ESC', "value": 'PRESS'}, None),
        ("mesh.retopo_tool_grid_stop", {"type": 'RIGHTMOUSE', "value": 'PRESS'}, None),
        ("mesh.retopo_tool_brush", {"type": 'LEFTMOUSE', "value": 'PRESS', "any": True}, None),
        ("mesh.retopo_tool_brush_size", {"type": 'F', "value": 'PRESS'}, None),
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
        layout.prop(context.scene.retopo_tool, "mode", expand=True)
        layout.prop(context.scene.retopo_tool, "snapping")
        if context.scene.retopo_tool.mode == 'BRUSH':
            layout.prop(context.scene.retopo_tool, "brush_radius")
            layout.prop(context.scene.retopo_tool, "preserve_borders")
        elif context.scene.retopo_tool.mode == 'RETOPO':
            layout.prop(context.scene.retopo_tool, "quad_only")
            layout.prop(context.scene.retopo_tool, "auto_reselect")
            if context.scene.retopo_tool.snapping:
                layout.prop(context.scene.retopo_tool, "merge_radius")
        elif context.scene.retopo_tool.mode == 'GRID':
            layout.label(text="Quads Only · Hold Ctrl: Quad/Tri")
            layout.prop(context.scene.retopo_tool, "fill_merge")
            if context.scene.retopo_tool.fill_merge:
                layout.prop(context.scene.retopo_tool, "merge_radius")


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


class RP_PT_panel(bpy.types.Panel):
    bl_label = f"{ADDON_NAME} {ADDON_VERSION}"
    bl_idname = "RP_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "TopoPot"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        running = tool and tool.idname == RP_Tool.bl_idname
        layout.operator("mesh.retopo_tool_stop" if running else "mesh.retopo_tool_activate", icon='PAUSE' if running else 'PLAY')
        settings = context.scene.retopo_tool
        layout.prop(settings, "mode", expand=True)
        layout.prop(settings, "snapping")
        if settings.mode == 'BRUSH':
            layout.prop(settings, "brush_radius")
            layout.prop(settings, "preserve_borders")
        elif settings.mode == 'RETOPO':
            layout.prop(settings, "quad_only")
            layout.prop(settings, "auto_reselect")
        elif settings.mode == 'GRID':
            layout.label(text="Quads Only · Hold Ctrl: Quad/Tri")
            layout.prop(settings, "fill_merge")
        layout.prop(settings, "target")
        layout.operator("mesh.retopo_tool_add_reference", icon='OUTLINER_COLLECTION')
        layout.prop(settings, "offset")
        layout.prop(settings, "radius")
        layout.prop(settings, "merge_radius")
        layout.prop(context.space_data.overlay, "show_retopology", text="Retopology View")
        if context.space_data.overlay.show_retopology:
            layout.prop(context.space_data.overlay, "retopology_offset", text="View Offset")


def draw_grid_side_counts(context, border, matrix, color):
    if not fill_quads_only(context):
        return
    projected = [view3d_utils.location_3d_to_region_2d(context.region, context.region_data, matrix @ co)
                 for co in border]
    if not projected or any(point is None for point in projected):
        return
    center = sum(projected, Vector((0, 0))) / len(projected)
    scale = context.preferences.system.ui_scale
    blf.size(0, 14 * scale)
    blf.color(0, *color)
    blf.enable(0, blf.SHADOW)
    blf.shadow(0, 3, 0.0, 0.0, 0.0, 1.0)
    try:
        for side in topology.rim_sides(border):
            points = [view3d_utils.location_3d_to_region_2d(context.region, context.region_data, matrix @ co)
                      for co in side]
            lengths = [(b - a).length for a, b in zip(points, points[1:])]
            remaining = sum(lengths) / 2
            position = points[0].copy()
            for a, b, length in zip(points, points[1:], lengths):
                if remaining <= length:
                    position = a.lerp(b, remaining / length) if length else a.copy()
                    break
                remaining -= length
            position += (position - center).normalized() * (16 * scale)
            label = str(len(side))
            width, height = blf.dimensions(0, label)
            blf.position(0, position.x - width / 2, position.y - height / 2, 0)
            blf.draw(0, label)
    finally:
        blf.disable(0, blf.SHADOW)


def draw_hover():
    context = bpy.context
    if context.mode != 'EDIT_MESH' or not context.region_data or not context.edit_object:
        return
    tool = context.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
    if not tool or tool.idname != RP_Tool.bl_idname:
        return
    if context.region.as_pointer() != _mouse_region:
        return
    mouse = Vector(context.window_manager.retopo_tool_mouse) - Vector((context.region.x, context.region.y))
    if not (0 <= mouse.x < context.region.width and 0 <= mouse.y < context.region.height):
        return
    settings = context.scene.retopo_tool
    if settings.mode == 'BRUSH':
        radius = settings.brush_radius
        points = [(mouse.x + radius * cos(tau * i / 64), mouse.y + radius * sin(tau * i / 64), 0) for i in range(65)]
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        shader.uniform_float("color", (1.0, 0.65, 0.15, 1.0) if context.window_manager.retopo_tool_shift else (0.15, 0.9, 0.65, 1.0))
        batch_for_shader(shader, 'LINE_STRIP', {"pos": points}).draw(shader)
        return
    if navigation_changed(context):
        return
    bm = bmesh.from_edit_mesh(context.edit_object.data)
    alt_hover = (getattr(context.window_manager, 'retopo_tool_alt', False)
                  and not context.window_manager.retopo_tool_ctrl and not context.window_manager.retopo_tool_shift)
    hit = knife_target(context, bm, mouse) if alt_hover else pick(context, bm, mouse)
    matrix = context.edit_object.matrix_world
    polygons = []
    kind = 'POINTS'
    preview_color = retopo_color(context)
    snapped_points = []
    grid_border = []
    if settings.mode == 'GRID':
        polygons, grid_border, error = grid_hover_preview(context, bm, mouse)
        kind = 'STRIP'
        preview_color = GRID_ERROR_COLOR if error else GRID_COLOR
    elif _strip_preview is not None and _strip_preview[0] == context.region.as_pointer():
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
                     object_key(settings.target) if settings.target else None)

        def compute_knife_preview():
            return knife_preview(bm, anchor, hit, factor, reference_projector(context))

        kind, polygons = cached_hover_preview(cache_key, compute_knife_preview)
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
                     object_key(settings.target) if settings.target else None)

        def compute_preview():
            return preview(bm, anchor, hit, local_point, factor,
                           context.window_manager.retopo_tool_shift, with_kind=True,
                           project=reference_projector(context), quad_only=settings.quad_only,
                           snap_distance=quad_snap_distance(context), snapped_points=snapped_points,
                           auto_reselect=getattr(settings, 'auto_reselect', True))

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
        points = [] if kind == 'STRIP' else screen(polygons)
        if kind == 'STRIP':
            # One batched fill and one batched outline for the whole strip;
            # per-face draw calls made long strips lag badly.
            triangles, outline_edges = [], []
            for polygon in polygons:
                outline = screen(polygon)
                if len(outline) == 3:
                    triangles.extend(outline)
                elif len(outline) == 4:
                    triangles.extend((outline[0], outline[1], outline[2],
                                      outline[0], outline[2], outline[3]))
                else:
                    continue
                for start, end in zip(outline, outline[1:] + outline[:1]):
                    outline_edges.extend((start, end))
            draw('TRIS', triangles, (*preview_color[:3], preview_color[3] * 0.25))
            draw('LINES', outline_edges, preview_color)
        elif kind == 'FACE' and len(points) >= 3:
            triangles = [points[i] for i in ((0, 1, 2) if len(points) == 3 else (0, 1, 2, 0, 2, 3))]
            draw('TRIS', triangles, (*preview_color[:3], preview_color[3] * 0.25))
            draw('LINE_STRIP', points + [points[0]], preview_color)
        else:
            draw(kind, points, preview_color)
        if snapped_points:
            gpu.state.point_size_set(15)
            draw('POINTS', screen(snapped_points), (*preview_color[:3], 1.0))
            gpu.state.point_size_set(9)
        if grid_border:
            border = screen(grid_border)
            if border:
                draw('LINE_STRIP', border + [border[0]], preview_color)
                draw_grid_side_counts(context, grid_border, matrix, preview_color)
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


classes = (RP_Settings, RP_OT_alt_cut, RP_OT_grid_fill, RP_OT_grid_stop, RP_OT_click, RP_OT_brush, RP_OT_brush_size, RP_OT_mode, RP_MT_modes, RP_Preferences, RP_OT_hover, RP_OT_activate, RP_OT_stop, RP_OT_add_reference, RP_PT_panel)
_draw_handle = None
_addon_keymaps = []


def register():
    global _draw_handle
    invalidate_visibility()
    bpy.app.handlers.depsgraph_update_post.append(invalidate_visibility)
    bpy.app.handlers.undo_post.append(invalidate_visibility)
    bpy.app.handlers.redo_post.append(invalidate_visibility)
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
    for keymap, item in _addon_keymaps:
        keymap.keymap_items.remove(item)
    _addon_keymaps.clear()
    for handlers in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.undo_post,
                     bpy.app.handlers.redo_post, bpy.app.handlers.load_post):
        if invalidate_visibility in handlers:
            handlers.remove(invalidate_visibility)
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
