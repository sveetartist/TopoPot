import json, sys, time, traceback
from pathlib import Path
from types import SimpleNamespace
import bpy, bmesh
from mathutils import Vector, Quaternion
from bpy_extras import view3d_utils
root = Path('.').resolve()
sys.path.insert(0, str(root))
import topopot_retopo_tool as addon
addon.register()
bpy.context.preferences.view.show_splash = False
n = 80
mesh = bpy.data.meshes.new('Perf')
mesh.from_pydata([(x*0.1, y*0.1, 0) for y in range(n+1) for x in range(n+1)], [],
                 [(y*(n+1)+x, y*(n+1)+x+1, (y+1)*(n+1)+x+1, (y+1)*(n+1)+x)
                  for y in range(n) for x in range(n)])
obj = bpy.data.objects.new('Perf', mesh)
bpy.context.collection.objects.link(obj)
for sel in bpy.context.selected_objects: sel.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode='EDIT')
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
view = area.spaces.active.region_3d
view.view_rotation = Quaternion((1,0,0,0))
view.view_location = (4, 4, 0)
view.view_distance = 12
view.view_perspective = 'ORTHO'
out = {}
def tick():
    try:
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.wm.tool_set_by_id(name='retopo_tool.tool')
            bm = bmesh.from_edit_mesh(obj.data)
            for e in bm.edges: e.select = False
            for v in bm.verts: v.select = False
            edge = next(e for e in bm.edges if len(e.link_faces) == 1)
            edge.select = True
            bm.select_history.clear(); bm.select_history.add(edge)
            mb = view3d_utils.location_3d_to_region_2d(region, view, Vector((4,4,0)))
            addon._mouse_region = region.as_pointer()
            bpy.context.window_manager.retopo_tool_mouse = (int(region.x + mb.x), int(region.y + mb.y))
            # pure preview cost
            t0 = time.perf_counter()
            for _ in range(10):
                addon.preview(bm, addon.topology.active(bm), mb, addon.context_depth := None)
            out['preview_ms'] = (time.perf_counter()-t0)/10*1000
            # pick cost
            t0 = time.perf_counter()
            for _ in range(10): addon.pick(bpy.context, bm, mb)
            out['pick_ms'] = (time.perf_counter()-t0)/10*1000
            # draw_hover idle
            bpy.context.window_manager.retopo_tool_ctrl = False
            addon.draw_hover()
            t0 = time.perf_counter()
            for _ in range(20): addon.draw_hover()
            out['draw_hover_idle_ms'] = (time.perf_counter()-t0)/20*1000
            # navigation, no ctrl
            t0 = time.perf_counter()
            for i in range(10):
                view.view_location = (4 + i*0.01, 4, 0)
                addon.draw_hover()
            out['draw_hover_nav_ms'] = (time.perf_counter()-t0)/10*1000
            # ctrl preview in draw handler
            bpy.context.window_manager.retopo_tool_ctrl = True
            addon.draw_hover()
            t0 = time.perf_counter()
            for _ in range(10): addon.draw_hover()
            out['draw_hover_ctrl_ms'] = (time.perf_counter()-t0)/10*1000
    except Exception:
        out['error'] = traceback.format_exc()
    Path('zips/perf_probe.json').write_text(json.dumps(out, indent=1))
    bpy.ops.wm.quit_blender()
    return None
bpy.app.timers.register(tick, first_interval=2)
