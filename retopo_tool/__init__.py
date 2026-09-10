bl_info = {
    "name": "TopoPot - Retopology tool for Blender",
    "author": "ArtVit",
    "version": (1, 14, 12),
    "blender": (5, 0, 0),
    "location": "3D View > Sidebar > TopoPot",
    "description": "Minimalistic retopology with quality tools. Cook your topology.",
    "category": "Mesh",
}

import bpy
import bmesh
import gpu
import importlib
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
TOOL_ICON = str(Path(__file__).resolve().parent / "assets" / "retopo_icon")
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


def retopo_color(context):
    prefs = addon_preferences(context)
    return tuple(prefs.retopo_color) if prefs else COLOR_PRESETS['BLUE']


def update_mode(self, context):
    if context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D':
        topology.clear_pending(bmesh.from_edit_mesh(context.edit_object.data), discard=self.quad_only)
        bpy.ops.wm.tool_set_by_id(name='builtin.select_box' if self.mode == 'BOX' else RP_Tool.bl_idname)
        context.area.tag_redraw()


class RP_Settings(bpy.types.PropertyGroup):
    preserve_borders: bpy.props.BoolProperty(name="Preserve Borders", description="Relax only interior surface vertices, keeping open borders and loose geometry fixed", default=False)
    mode: EnumProperty(name="Mode", items=(('RETOPO', "Retopo", "Create and move topology"), ('BRUSH', "Brush", "Move vertices; Shift relaxes"), ('BOX', "Box Select", "Blender's native box selection")), default='RETOPO', update=update_mode)
    quad_only: BoolProperty(name="Quad Mode", description="Extrude quads with the new edge centered at the cursor; allow triangles when connecting neighboring edges", default=False)
    brush_radius: IntProperty(name="Brush Radius", default=75, min=5, max=500, subtype='PIXEL')
    target: PointerProperty(name="Reference", description="Optional mesh used for surface projection", type=bpy.types.Object, poll=mesh_filter)
    offset: FloatProperty(name="Offset", default=0.001, min=0, soft_max=0.1, subtype='DISTANCE')
    radius: IntProperty(name="Pick Radius", default=14, min=4, max=48, subtype='PIXEL')
    merge_radius: IntProperty(name="Merge Radius", description="Snap and merge a dragged vertex within this screen distance", default=10, min=3, max=32, subtype='PIXEL')
    reference_tolerance: IntProperty(name="Reference Tolerance", description="Allow selection slightly below Reference, using this many screen pixels as a depth allowance; zero uses strict occlusion", default=32, min=0, max=64, subtype='PIXEL')


def project(context, mouse, depth):
    region, rv3d = context.region, context.region_data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
    settings = context.scene.retopo_tool
    target = settings.target
    if target and target != context.edit_object:
        evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
        matrix = evaluated.matrix_world
        inverse = matrix.inverted_safe()
        hit, location, normal, _ = evaluated.ray_cast(inverse @ origin, (inverse.to_3x3() @ direction).normalized())
        if hit:
            normal = (inverse.transposed().to_3x3() @ normal).normalized()
            if normal.dot(direction) >= -1e-6:
                return None
            return matrix @ location + normal * settings.offset
        return None
    return view3d_utils.region_2d_to_location_3d(region, rv3d, mouse, depth)


_visibility_cache = {}


def object_key(obj):
    return obj.as_pointer() if hasattr(obj, 'as_pointer') else id(obj)


def cached_tree(kind, obj, build, counts=()):
    data = getattr(obj, 'data', None)
    signature = (object_key(data), tuple(value for row in obj.matrix_world for value in row), counts)
    key = (kind, object_key(obj))
    entry = _visibility_cache.get(key)
    if entry is None or entry[0] != signature:
        entry = (signature, build())
        _visibility_cache[key] = entry
    return entry[1]


@bpy.app.handlers.persistent
def invalidate_visibility(scene=None, depsgraph=None):
    if depsgraph is None:
        _visibility_cache.clear()
        return
    changed = {object_key(update.id.original) for update in depsgraph.updates
               if update.is_updated_geometry or update.is_updated_transform}
    for key, (signature, _) in list(_visibility_cache.items()):
        if key[1] in changed or signature[0] in changed:
            del _visibility_cache[key]


def reference_tree(context):
    reference = context.scene.retopo_tool.target
    if reference is None or reference == context.edit_object:
        return None
    def build():
        evaluated = reference.evaluated_get(context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            return BVHTree.FromPolygons([evaluated.matrix_world @ v.co for v in mesh.vertices],
                                       [tuple(p.vertices) for p in mesh.polygons])
        finally:
            evaluated.to_mesh_clear()
    return cached_tree('reference', reference, build)


def visible_from_view(context, co, tree, tolerance=1e-5):
    screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, co)
    if screen is None:
        return False
    origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, screen)
    direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, screen)
    distance = (co - origin).dot(direction)
    if distance <= 0:
        return False
    location, _, _, hit_distance = tree.ray_cast(origin, direction)
    return location is None or hit_distance >= distance - tolerance


def reference_pick_tolerance(context, co):
    settings = context.scene.retopo_tool
    tolerance = max(1e-5, settings.offset * 2)
    pixels = getattr(settings, 'reference_tolerance', 12)
    if pixels:
        screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, co)
        if screen is not None:
            a = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, screen, co)
            b = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, screen + Vector((pixels, 0)), co)
            tolerance = max(tolerance, (b - a).length)
    return tolerance


def pick(context, bm, mouse, exclude=None, vertices_only=False, radius=None):
    matrix = context.edit_object.matrix_world
    def build_edit_tree():
        verts = list(bm.verts)
        indices = {v: i for i, v in enumerate(verts)}
        return BVHTree.FromPolygons([matrix @ v.co for v in verts],
                                   [tuple(indices[v] for v in f.verts) for f in bm.faces if not f.hide])
    trees = None

    def visible(co):
        nonlocal trees
        if trees is None:
            trees = (cached_tree('edit', context.edit_object, build_edit_tree,
                                 (len(bm.verts), len(bm.edges), len(bm.faces))), reference_tree(context))
        edit_tree, ref_tree = trees
        return (visible_from_view(context, co, edit_tree) and
                (ref_tree is None or visible_from_view(context, co, ref_tree,
                 max(1e-5, context.scene.retopo_tool.offset * 2) if exclude is not None
                 else reference_pick_tolerance(context, co))))
    coords = {}
    best_v, best_e = None, None
    vr = er = context.scene.retopo_tool.radius if radius is None else radius
    for vert in bm.verts:
        if vert.hide or vert == exclude:
            continue
        point = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, matrix @ vert.co)
        if point is not None:
            coords[vert] = point
            distance = (point - mouse).length
            if distance < vr and visible(matrix @ vert.co):
                best_v, vr = vert, distance
    if vertices_only:
        return best_v
    for edge in bm.edges:
        if edge.hide or any(v not in coords for v in edge.verts):
            continue
        a, b = (coords[v] for v in edge.verts)
        delta = b - a
        t = max(0, min(1, (mouse - a).dot(delta) / max(delta.length_squared, 1e-12)))
        distance = (mouse - (a + t * delta)).length
        if distance < er and visible(matrix @ edge.verts[0].co.lerp(edge.verts[1].co, t)):
            best_e, er = edge, distance
    return best_v if best_v is not None else best_e


def placement(context, mouse, anchor):
    obj = context.edit_object
    if isinstance(anchor, bmesh.types.BMVert):
        depth = obj.matrix_world @ anchor.co
    elif isinstance(anchor, bmesh.types.BMEdge):
        depth = obj.matrix_world @ ((anchor.verts[0].co + anchor.verts[1].co) / 2)
    else:
        depth = context.scene.cursor.location
    return project(context, mouse, depth)


def reference_projector(context):
    reference = context.scene.retopo_tool.target
    obj = context.edit_object
    if reference is None or reference == obj:
        return None
    tree = reference_tree(context)
    matrix = obj.matrix_world.copy()
    inverse = matrix.inverted_safe()
    offset = context.scene.retopo_tool.offset

    def snap(co):
        location, normal, _, _ = tree.find_nearest(matrix @ co)
        if location is None:
            raise ValueError("Reference mesh has no surface to snap to")
        screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, location)
        if screen is None:
            return co.copy()
        direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, screen)
        if normal.dot(direction) >= -1e-6 or not visible_from_view(context, location, tree):
            return co.copy()
        return inverse @ (location + normal * offset)
    return snap


def edge_parameter(context, edge, mouse):
    matrix = context.edit_object.matrix_world
    a, b = [matrix @ v.co for v in edge.verts]
    screen_a, screen_b = [view3d_utils.location_3d_to_region_2d(context.region, context.region_data, p) for p in (a, b)]
    if screen_a is None or screen_b is None:
        return 0.5
    delta = screen_b - screen_a
    factor = max(0.001, min(0.999, (mouse - screen_a).dot(delta) / max(delta.length_squared, 1e-12)))
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


def quad_snap_distance(context):
    matrix = context.edit_object.matrix_world.copy()
    inverse = matrix.inverted_safe()
    def distance(co):
        world = matrix @ co
        screen = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, world)
        if screen is None:
            return 0.0
        displaced = view3d_utils.region_2d_to_location_3d(
            context.region, context.region_data,
            screen + Vector((context.scene.retopo_tool.merge_radius * 4, 0)), world)
        return (inverse @ displaced - co).length
    return distance


def preview(bm, anchor, hit, point, edge_factor=0.5, bridge_mode=False, with_kind=False, strict=False, project=None, quad_only=False, snap_distance=None, snapped_points=None):
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
        result = topology.apply_click(copy, anchor, hit, point, edge_factor, bridge_mode, project, quad_only, snap_distance, snapped_points)
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


def sync(obj, destructive=True):
    _visibility_cache.pop(('edit', object_key(obj)), None)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.normal_update()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=destructive)


class RP_OT_click(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_click"
    bl_label = "Retopo tool Stroke"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW' and context.scene.retopo_tool.mode == 'RETOPO')

    def invoke(self, context, event):
        track_mouse(context, event)
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        hit = pick(context, bm, mouse)
        anchor = topology.active(bm)
        settings = context.scene.retopo_tool
        if event.ctrl and isinstance(hit, bmesh.types.BMVert):
            bm.verts.index_update()
            self.vertex_index = hit.index
            self.obj, self.area, self.region = obj, context.area, context.region
            self.start = mouse
            self.dragging = False
            self.connect_gesture = True
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        if event.ctrl and not event.shift and isinstance(hit, bmesh.types.BMEdge):
            bm.edges.index_update()
            self.edge_index = hit.index
            self.obj, self.area, self.region = obj, context.area, context.region
            self.start = mouse
            self.dragging = False
            self.edge_gesture = True
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        if event.ctrl:
            try:
                point = placement(context, mouse, anchor) if hit is None else None
                point = obj.matrix_world.inverted_safe() @ point if point is not None else None
                factor = edge_parameter(context, hit, mouse) if isinstance(hit, bmesh.types.BMEdge) else 0.5
                # Validate on a copy before splitting any live edges.
                snap = reference_projector(context)
                preview(bm, anchor, hit, point, factor, event.shift, strict=True, project=snap, quad_only=settings.quad_only, snap_distance=quad_snap_distance(context))
                topology.apply_click(bm, anchor, hit, point, factor, event.shift, project=snap, quad_only=settings.quad_only, snap_distance=quad_snap_distance(context))
                sync(obj)
            except ValueError as error:
                self.report({'WARNING'}, str(error))
                return {'CANCELLED'}
            return {'FINISHED'}
        topology.clear_pending(bm, discard=settings.quad_only)
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
            self.start = mouse
            self.dragging = False
            sync(obj, destructive=False)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        sync(obj, destructive=False)
        return {'FINISHED'}

    def modal(self, context, event):
        global _merge_target
        if getattr(self, 'move_edge', False):
            return RP_OT_click.modal_move_edge(self, context, event)
        if getattr(self, 'connect_gesture', False):
            return RP_OT_click.modal_connect(self, context, event)
        if getattr(self, 'edge_gesture', False):
            return RP_OT_click.modal_edge(self, context, event)
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            _merge_target = None
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if self.vertex_index >= len(bm.verts):
            _merge_target = None
            return {'CANCELLED'}
        vertex = bm.verts[self.vertex_index]
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
                                  radius=context.scene.retopo_tool.merge_radius)
                if target is not None:
                    topology.merge_vertices(bm, vertex, target, context.scene.retopo_tool.quad_only)
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
                                  radius=context.scene.retopo_tool.merge_radius)
                    point = obj.matrix_world @ target.co if target is not None else project(context, mouse, obj.matrix_world @ self.original)
                _merge_target = (self.region.as_pointer(), target.co.copy()) if target is not None else None
                if point is not None:
                    vertex.co = obj.matrix_world.inverted_safe() @ point
                    sync(obj, destructive=False)
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal_move_edge(self, context, event):
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if any(i >= len(bm.verts) for i in self.move_indices):
            return {'CANCELLED'}
        verts = [bm.verts[i] for i in self.move_indices]
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            for vert, original in zip(verts, self.move_originals):
                vert.co = original
            sync(self.obj, destructive=False)
            self.area.tag_redraw()
            return {'FINISHED'}
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or release:
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            self.dragging |= (mouse - self.start).length > 3
            if self.dragging:
                try:
                    with context.temp_override(area=self.area, region=self.region):
                        track_mouse(context, event)
                        if not hasattr(self, 'move_snap'):
                            self.move_snap = reference_projector(context)
                        positions = edge_drag_positions(context, self.move_originals, self.start, mouse, self.move_snap)
                    for vert, position in zip(verts, positions):
                        vert.co = position
                    sync(self.obj, destructive=False)
                except ValueError as error:
                    if release:
                        self.report({'WARNING'}, str(error))
            self.area.tag_redraw()
        return {'FINISHED'} if release else {'RUNNING_MODAL'}

    def modal_edge(self, context, event):
        global _loop_preview
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            _loop_preview = None
            return {'CANCELLED'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            _loop_preview = None
            self.area.tag_redraw()
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.edges.ensure_lookup_table()
        edge = bm.edges[self.edge_index]
        mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        self.dragging |= (mouse - self.start).length > 4
        with context.temp_override(area=self.area, region=self.region):
            track_mouse(context, event)
            factor = edge_parameter(context, edge, mouse if self.dragging else self.start)
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        try:
            if self.dragging:
                if not hasattr(self, 'loop_projector'):
                    self.loop_projector = reference_projector(context)
                coords = topology.loop_preview(edge, factor, self.loop_projector)
                _loop_preview = (self.region.as_pointer(), coords)
                if release:
                    topology.loop_cut(bm, edge, factor, self.loop_projector, context.scene.retopo_tool.quad_only)
            elif release:
                anchor = topology.active(bm)
                snap = reference_projector(context)
                preview(bm, anchor, edge, None, factor, strict=True, project=snap, quad_only=context.scene.retopo_tool.quad_only, snap_distance=quad_snap_distance(context))
                topology.apply_click(bm, anchor, edge, edge_factor=factor, project=snap, quad_only=context.scene.retopo_tool.quad_only, snap_distance=quad_snap_distance(context))
            if release:
                sync(self.obj)
        except ValueError as error:
            _loop_preview = (self.region.as_pointer(), [])
            if release:
                self.report({'WARNING'}, str(error))
                _loop_preview = None
                self.area.tag_redraw()
                return {'CANCELLED'}
        self.area.tag_redraw()
        if release:
            _loop_preview = None
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def modal_connect(self, context, event):
        global _loop_preview
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj or event.type in {'ESC', 'RIGHTMOUSE'}:
            _loop_preview = None
            self.area.tag_redraw()
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        start = bm.verts[self.vertex_index]
        mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        self.dragging |= (mouse - self.start).length > 4
        with context.temp_override(area=self.area, region=self.region):
            track_mouse(context, event)
            hit = pick(context, bm, mouse) if self.dragging else start
        release = event.type == 'LEFTMOUSE' and event.value == 'RELEASE'
        try:
            if self.dragging and not isinstance(hit, bmesh.types.BMVert):
                raise ValueError("Release over a vertex to connect")
            anchor = start if self.dragging else topology.active(bm)
            snap = reference_projector(context)
            kind, coords = preview(bm, anchor, hit, None, with_kind=True, strict=True, project=snap, quad_only=context.scene.retopo_tool.quad_only, snap_distance=quad_snap_distance(context))
            if self.dragging:
                _loop_preview = (self.region.as_pointer(), coords if kind == 'LINES' else [])
            if release:
                topology.apply_click(bm, anchor, hit, project=snap, quad_only=context.scene.retopo_tool.quad_only, snap_distance=quad_snap_distance(context))
                sync(self.obj)
        except ValueError as error:
            _loop_preview = (self.region.as_pointer(), [])
            if release:
                self.report({'WARNING'}, str(error))
                _loop_preview = None
                self.area.tag_redraw()
                return {'CANCELLED'}
        self.area.tag_redraw()
        if release:
            _loop_preview = None
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


_mouse_region = None
_loop_preview = None
_merge_target = None


def track_mouse(context, event):
    global _mouse_region
    context.window_manager.retopo_tool_mouse = (event.mouse_x, event.mouse_y)
    context.window_manager.retopo_tool_ctrl = event.ctrl
    context.window_manager.retopo_tool_shift = event.shift
    if event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
        context.window_manager.retopo_tool_ctrl = event.value != 'RELEASE'
    if event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
        context.window_manager.retopo_tool_shift = event.value != 'RELEASE'
    _mouse_region = context.region.as_pointer()


def brush_vertices(context, bm, mouse):
    matrix = context.edit_object.matrix_world
    verts = list(bm.verts)
    indices = {v: i for i, v in enumerate(verts)}
    tree = BVHTree.FromPolygons([matrix @ v.co for v in verts],
                              [tuple(indices[v] for v in f.verts) for f in bm.faces if not f.hide])
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
        if not visible_from_view(context, co, tree):
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
    bl_label = "Retopo Brush Stroke"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW' and context.scene.retopo_tool.mode == 'BRUSH')

    def invoke(self, context, event):
        self.obj = context.edit_object
        self.area, self.region = context.area, context.region
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.index_update()
        self.originals = [v.co.copy() for v in bm.verts]
        self.counts = (len(bm.verts), len(bm.edges), len(bm.faces))
        self.last = Vector((event.mouse_region_x, event.mouse_region_y))
        self.weighted = [(v.index, weight) for v, weight in brush_vertices(context, bm, self.last)]
        self.relaxing = event.shift
        track_mouse(context, event)
        if event.shift:
            self.apply(context, self.last, True)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def apply(self, context, mouse, relaxing):
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        if relaxing != self.relaxing:
            self.weighted = [(v.index, weight) for v, weight in brush_vertices(context, bm, self.last)]
        snap = reference_projector(context)
        if relaxing:
            positions = relax_positions(brush_vertices(context, bm, mouse), context.scene.retopo_tool.preserve_borders)
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
        for vert, co in positions.items():
            vert.co = snap(co) if snap else co
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
                self.apply(context, mouse, event.shift)
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
    bl_label = "Set Retopo Mode"
    mode: EnumProperty(items=(('RETOPO', "Retopo", ""), ('BRUSH', "Brush", ""), ('BOX', "Box Select", "")))

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.area and context.area.type == 'VIEW_3D'

    def execute(self, context):
        context.scene.retopo_tool.mode = self.mode
        return {'FINISHED'}


class RP_MT_modes(bpy.types.Menu):
    bl_label = "Retopo Modes"
    bl_idname = "RP_MT_modes"

    def draw(self, context):
        pie = self.layout.menu_pie()
        for mode, label, icon in (('RETOPO', "Retopo", 'EDITMODE_HLT'), ('BRUSH', "Brush", 'BRUSH_DATA'), ('BOX', "Box Select", 'SELECT_SET')):
            pie.operator("mesh.retopo_tool_mode", text=label, icon=icon).mode = mode
        pie.prop(context.scene.retopo_tool, "preserve_borders")


class RP_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    retopo_color_preset: EnumProperty(name="Retopo Color Preset", items=COLOR_PRESET_ITEMS, default='BLUE', update=update_retopo_color_preset)
    retopo_color: FloatVectorProperty(name="Retopo Color", subtype='COLOR', size=4, min=0, max=1, default=COLOR_PRESETS['BLUE'], update=update_retopo_color)
    overlay_color: FloatVectorProperty(name="Overlay Color", subtype='COLOR', size=3, min=0, max=1, default=DEFAULT_OVERLAY_COLOR, update=update_overlay_preferences)
    overlay_opacity: FloatProperty(name="Overlay Opacity", subtype='FACTOR', min=0, max=1, default=DEFAULT_OVERLAY_OPACITY, update=update_overlay_preferences)

    def draw(self, context):
        import rna_keymap_ui
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        colors = layout.box()
        colors.label(text="Retopo Colors")
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
    bl_label = "Retopo tool Preview"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        track_mouse(context, event)
        context.area.tag_redraw()
        return {'PASS_THROUGH'}


class RP_Tool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "retopo_tool.tool"
    bl_label = "Retopo tool"
    bl_description = "Select and drag; Ctrl-click creates or cuts; Ctrl-Shift-click bridges edges"
    bl_icon = TOOL_ICON
    bl_keymap = (
        ("mesh.retopo_tool_brush", {"type": 'LEFTMOUSE', "value": 'PRESS', "any": True}, None),
        ("mesh.retopo_tool_brush_size", {"type": 'F', "value": 'PRESS'}, None),
        ("mesh.retopo_tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'LEFT_CTRL', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'RIGHT_CTRL', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'LEFT_SHIFT', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_tool_hover", {"type": 'RIGHT_SHIFT', "value": 'ANY', "any": True}, None),
    )

    def draw_settings(context, layout, tool):
        layout.prop(context.scene.retopo_tool, "mode", expand=True)
        if context.scene.retopo_tool.mode == 'BRUSH':
            layout.prop(context.scene.retopo_tool, "brush_radius")
            layout.prop(context.scene.retopo_tool, "preserve_borders")
        elif context.scene.retopo_tool.mode == 'RETOPO':
            layout.prop(context.scene.retopo_tool, "quad_only")


class RP_OT_activate(bpy.types.Operator):
    bl_idname = "mesh.retopo_tool_activate"
    bl_label = "Activate Retopo tool"
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
        if context.scene.retopo_tool.target == context.edit_object:
            context.scene.retopo_tool.target = None
        context.tool_settings.mesh_select_mode = (True, False, False)
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
        return {'FINISHED'}


class RP_PT_panel(bpy.types.Panel):
    bl_label = "Retopo tool"
    bl_idname = "RP_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Retopo"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        running = tool and tool.idname == RP_Tool.bl_idname
        layout.operator("mesh.retopo_tool_stop" if running else "mesh.retopo_tool_activate", icon='PAUSE' if running else 'PLAY')
        settings = context.scene.retopo_tool
        layout.prop(settings, "mode", expand=True)
        if settings.mode == 'BRUSH':
            layout.prop(settings, "brush_radius")
            layout.prop(settings, "preserve_borders")
        elif settings.mode == 'RETOPO':
            layout.prop(settings, "quad_only")
        layout.prop(settings, "target")
        layout.prop(settings, "offset")
        layout.prop(settings, "radius")
        layout.prop(settings, "merge_radius")
        layout.prop(settings, "reference_tolerance")
        layout.prop(context.space_data.overlay, "show_retopology", text="Retopology View")
        if context.space_data.overlay.show_retopology:
            layout.prop(context.space_data.overlay, "retopology_offset", text="View Offset")


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
    bm = bmesh.from_edit_mesh(context.edit_object.data)
    hit = pick(context, bm, mouse)
    matrix = context.edit_object.matrix_world
    polygons = []
    kind = 'POINTS'
    preview_color = retopo_color(context)
    snapped_points = []
    if _loop_preview is not None and _loop_preview[0] == context.region.as_pointer():
        kind, polygons = 'LINES', _loop_preview[1]
    elif context.window_manager.retopo_tool_ctrl:
        anchor = topology.active(bm)
        point = placement(context, mouse, anchor) if hit is None else None
        factor = edge_parameter(context, hit, mouse) if isinstance(hit, bmesh.types.BMEdge) else 0.5
        kind, polygons = preview(bm, anchor, hit, matrix.inverted_safe() @ point if point is not None else None,
                                 factor, context.window_manager.retopo_tool_shift, with_kind=True,
                                 project=reference_projector(context), quad_only=settings.quad_only, snap_distance=quad_snap_distance(context), snapped_points=snapped_points)

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
        points = screen(polygons)
        if kind == 'FACE' and len(points) >= 3:
            triangles = [points[i] for i in ((0, 1, 2) if len(points) == 3 else (0, 1, 2, 0, 2, 3))]
            draw('TRIS', triangles, (*preview_color[:3], preview_color[3] * 0.25))
            draw('LINE_STRIP', points + [points[0]], preview_color)
        else:
            draw(kind, points, preview_color)
        if snapped_points:
            gpu.state.point_size_set(15)
            draw('POINTS', screen(snapped_points), (*preview_color[:3], 1.0))
            gpu.state.point_size_set(9)
        if hit is not None:
            verts = [hit] if isinstance(hit, bmesh.types.BMVert) else hit.verts
            draw('POINTS' if len(verts) == 1 else 'LINES', screen([v.co for v in verts]), preview_color)
        if _merge_target is not None and _merge_target[0] == context.region.as_pointer():
            gpu.state.point_size_set(13)
            draw('POINTS', screen([_merge_target[1]]), preview_color)
    finally:
        gpu.state.point_size_set(1)
        gpu.state.line_width_set(1)
        gpu.state.blend_set('NONE')


classes = (RP_Settings, RP_OT_click, RP_OT_brush, RP_OT_brush_size, RP_OT_mode, RP_MT_modes, RP_Preferences, RP_OT_hover, RP_OT_activate, RP_OT_stop, RP_PT_panel)
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
    apply_overlay_preferences()
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
    del bpy.types.Scene.retopo_tool
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
