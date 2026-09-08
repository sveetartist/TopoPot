bl_info = {
    "name": "Retopo Pool",
    "author": "Retopo Pool",
    "version": (1, 2, 0),
    "blender": (5, 0, 0),
    "location": "3D View > Sidebar > Retopo",
    "description": "Minimal surface retopology with progressive triangles and quads",
    "category": "Mesh",
}

import bpy
import bmesh
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy.props import PointerProperty, FloatProperty, IntProperty
from . import topology


def mesh_filter(self, obj):
    return obj.type == 'MESH'


class RP_Settings(bpy.types.PropertyGroup):
    target: PointerProperty(name="Reference", description="Optional mesh used for surface projection", type=bpy.types.Object, poll=mesh_filter)
    offset: FloatProperty(name="Offset", default=0.001, min=0, soft_max=0.1, subtype='DISTANCE')
    radius: IntProperty(name="Pick Radius", default=14, min=4, max=48, subtype='PIXEL')


def project(context, mouse, depth):
    region, rv3d = context.region, context.region_data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
    settings = context.scene.retopo_pool
    target = settings.target
    if target and target != context.edit_object:
        evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
        matrix = evaluated.matrix_world
        inverse = matrix.inverted_safe()
        hit, location, normal, _ = evaluated.ray_cast(inverse @ origin, (inverse.to_3x3() @ direction).normalized())
        if hit:
            normal = (inverse.transposed().to_3x3() @ normal).normalized()
            return matrix @ location + normal * settings.offset
        return None
    return view3d_utils.region_2d_to_location_3d(region, rv3d, mouse, depth)


def pick(context, bm, mouse):
    matrix = context.edit_object.matrix_world
    coords = {}
    best_v, best_e = None, None
    vr = er = context.scene.retopo_pool.radius
    for vert in bm.verts:
        if vert.hide:
            continue
        point = view3d_utils.location_3d_to_region_2d(context.region, context.region_data, matrix @ vert.co)
        if point is not None:
            coords[vert] = point
            distance = (point - mouse).length
            if distance < vr:
                best_v, vr = vert, distance
    for edge in bm.edges:
        if edge.hide or any(v not in coords for v in edge.verts):
            continue
        a, b = (coords[v] for v in edge.verts)
        delta = b - a
        t = max(0, min(1, (mouse - a).dot(delta) / max(delta.length_squared, 1e-12)))
        distance = (mouse - (a + t * delta)).length
        if distance < er:
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


def preview(bm, anchor, hit, point, edge_factor=0.5, bridge_mode=False, with_kind=False, strict=False):
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
        result = topology.apply_click(copy, anchor, hit, point, edge_factor, bridge_mode)
        if isinstance(result, list):
            kind = 'LINES'
            coords = [v.co.copy() for edge in result for v in edge.verts]
        else:
            verts = [result] if isinstance(result, bmesh.types.BMVert) else result.verts
            kind = 'FACE' if isinstance(result, bmesh.types.BMFace) else ('POINTS' if len(verts) == 1 else 'LINES')
            coords = [v.co.copy() for v in verts]
        return (kind, coords) if with_kind else coords
    except ValueError:
        if strict:
            raise
        return ('POINTS', []) if with_kind else []
    finally:
        copy.free()


def sync(obj, destructive=True):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.normal_update()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=destructive)


class RP_OT_click(bpy.types.Operator):
    bl_idname = "mesh.retopo_pool_click"
    bl_label = "Retopo Pool Stroke"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.area.type == 'VIEW_3D' and context.region.type == 'WINDOW'

    def invoke(self, context, event):
        track_mouse(context, event)
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        hit = pick(context, bm, mouse)
        anchor = topology.active(bm)
        if event.ctrl:
            try:
                point = placement(context, mouse, anchor) if hit is None else None
                point = obj.matrix_world.inverted_safe() @ point if point is not None else None
                factor = edge_parameter(context, hit, mouse) if isinstance(hit, bmesh.types.BMEdge) else 0.5
                # Validate on a copy before splitting any live edges.
                preview(bm, anchor, hit, point, factor, event.shift, strict=True)
                topology.apply_click(bm, anchor, hit, point, factor, event.shift)
                sync(obj)
            except ValueError as error:
                self.report({'WARNING'}, str(error))
                return {'CANCELLED'}
            return {'FINISHED'}
        topology.clear_pending(bm)
        if event.shift and hit is not None:
            hit.select_set(not hit.select)
            if hit.select:
                bm.select_history.add(hit)
            sync(obj)
            return {'FINISHED'}
        topology.select(bm, hit)
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
        if context.mode != 'EDIT_MESH' or context.edit_object != self.obj:
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        if self.vertex_index >= len(bm.verts):
            return {'CANCELLED'}
        vertex = bm.verts[self.vertex_index]
        track_mouse(context, event)
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            vertex.co = self.original
            sync(self.obj, destructive=False)
            return {'FINISHED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            return {'FINISHED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
            self.dragging |= (mouse - self.start).length > 3
            if self.dragging:
                obj = self.obj
                with context.temp_override(area=self.area, region=self.region):
                    point = project(context, mouse, obj.matrix_world @ self.original)
                if point is not None:
                    vertex.co = obj.matrix_world.inverted_safe() @ point
                    sync(obj, destructive=False)
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


_mouse_region = None


def track_mouse(context, event):
    global _mouse_region
    context.window_manager.retopo_pool_mouse = (event.mouse_x, event.mouse_y)
    context.window_manager.retopo_pool_ctrl = event.ctrl
    context.window_manager.retopo_pool_shift = event.shift
    if event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
        context.window_manager.retopo_pool_ctrl = event.value != 'RELEASE'
    if event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
        context.window_manager.retopo_pool_shift = event.value != 'RELEASE'
    _mouse_region = context.region.as_pointer()


class RP_OT_hover(bpy.types.Operator):
    bl_idname = "mesh.retopo_pool_hover"
    bl_label = "Retopo Pool Preview"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        track_mouse(context, event)
        context.area.tag_redraw()
        return {'PASS_THROUGH'}


class RP_Tool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "retopo_pool.tool"
    bl_label = "Retopo Pool"
    bl_description = "Select and drag; Ctrl-click creates or cuts; Ctrl-Shift-click bridges edges"
    bl_icon = "ops.mesh.polybuild"
    bl_keymap = (
        ("mesh.retopo_pool_click", {"type": 'LEFTMOUSE', "value": 'PRESS', "any": True}, None),
        ("mesh.retopo_pool_hover", {"type": 'MOUSEMOVE', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_pool_hover", {"type": 'LEFT_CTRL', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_pool_hover", {"type": 'RIGHT_CTRL', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_pool_hover", {"type": 'LEFT_SHIFT', "value": 'ANY', "any": True}, None),
        ("mesh.retopo_pool_hover", {"type": 'RIGHT_SHIFT', "value": 'ANY', "any": True}, None),
    )


class RP_OT_activate(bpy.types.Operator):
    bl_idname = "mesh.retopo_pool_activate"
    bl_label = "Activate Retopo Pool"
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
        if context.scene.retopo_pool.target == context.edit_object:
            context.scene.retopo_pool.target = None
        context.tool_settings.mesh_select_mode = (True, False, False)
        bpy.ops.wm.tool_set_by_id(name=RP_Tool.bl_idname)
        return {'FINISHED'}


class RP_OT_stop(bpy.types.Operator):
    bl_idname = "mesh.retopo_pool_stop"
    bl_label = "Deactivate"
    bl_description = "Return to Blender's selection tool"

    def execute(self, context):
        if context.edit_object:
            topology.clear_pending(bmesh.from_edit_mesh(context.edit_object.data))
            sync(context.edit_object)
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
        return {'FINISHED'}


class RP_PT_panel(bpy.types.Panel):
    bl_label = "Retopo Pool"
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
        layout.operator("mesh.retopo_pool_stop" if running else "mesh.retopo_pool_activate", icon='PAUSE' if running else 'PLAY')
        settings = context.scene.retopo_pool
        layout.prop(settings, "target")
        layout.prop(settings, "offset")
        layout.prop(settings, "radius")
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
    mouse = Vector(context.window_manager.retopo_pool_mouse) - Vector((context.region.x, context.region.y))
    if not (0 <= mouse.x < context.region.width and 0 <= mouse.y < context.region.height):
        return
    bm = bmesh.from_edit_mesh(context.edit_object.data)
    hit = pick(context, bm, mouse)
    matrix = context.edit_object.matrix_world
    polygons = []
    kind = 'POINTS'
    if context.window_manager.retopo_pool_ctrl:
        anchor = topology.active(bm)
        point = placement(context, mouse, anchor) if hit is None else None
        factor = edge_parameter(context, hit, mouse) if isinstance(hit, bmesh.types.BMEdge) else 0.5
        kind, polygons = preview(bm, anchor, hit, matrix.inverted_safe() @ point if point is not None else None,
                                 factor, context.window_manager.retopo_pool_shift, with_kind=True)

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
            draw('TRIS', triangles, (0.15, 0.9, 0.65, 0.25))
            draw('LINE_STRIP', points + [points[0]], (0.15, 0.9, 0.65, 1.0))
        else:
            draw(kind, points, (0.15, 0.9, 0.65, 1.0))
        if hit is not None:
            verts = [hit] if isinstance(hit, bmesh.types.BMVert) else hit.verts
            draw('POINTS' if len(verts) == 1 else 'LINES', screen([v.co for v in verts]), (0.15, 0.9, 0.65, 1.0))
    finally:
        gpu.state.point_size_set(1)
        gpu.state.line_width_set(1)
        gpu.state.blend_set('NONE')


classes = (RP_Settings, RP_OT_click, RP_OT_hover, RP_OT_activate, RP_OT_stop, RP_PT_panel)
_draw_handle = None


def register():
    global _draw_handle
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.retopo_pool = PointerProperty(type=RP_Settings)
    bpy.types.WindowManager.retopo_pool_mouse = bpy.props.IntVectorProperty(size=2, default=(-100, -100))
    bpy.types.WindowManager.retopo_pool_ctrl = bpy.props.BoolProperty(default=False)
    bpy.types.WindowManager.retopo_pool_shift = bpy.props.BoolProperty(default=False)
    bpy.utils.register_tool(RP_Tool, after={"builtin.poly_build"}, separator=True)
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_hover, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    bpy.utils.unregister_tool(RP_Tool)
    del bpy.types.WindowManager.retopo_pool_mouse
    del bpy.types.WindowManager.retopo_pool_ctrl
    del bpy.types.WindowManager.retopo_pool_shift
    del bpy.types.Scene.retopo_pool
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
