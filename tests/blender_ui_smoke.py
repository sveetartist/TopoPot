"""Run with Blender --factory-startup --enable-event-simulate --python this_file.

Uses a real window and real mouse events to exercise undo-backed modal drags.
Writes a JSON result under zips and closes only this isolated Blender instance.
"""
import json
import sys
sys.dont_write_bytecode = True
import traceback
from pathlib import Path
from math import cos, sin, tau

import bpy
import bmesh
from mathutils import Vector, Quaternion
from bpy_extras import view3d_utils

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import topopot_retopo_tool as addon

report = root / 'zips' / f'blender_ui_{bpy.app.version[0]}_{bpy.app.version[1]}.json'
report.parent.mkdir(exist_ok=True)
report.write_text(json.dumps({'status': 'running'}))
trace = []
def monitor(method, original):
    def wrapped(self, context, event):
        result = original(self, context, event)
        trace.append((method, event.type, list(result), context.scene.retopo_tool.mode))
        return result
    return wrapped
for method in ('invoke', 'modal'):
    setattr(addon.RP_OT_grid_fill, method, monitor(method, getattr(addon.RP_OT_grid_fill, method)))
addon.register()
bpy.context.preferences.view.show_splash = False
name = 'Fill Hole UI regression'
mesh = bpy.data.meshes.new(name)
points = [(radius * cos(tau * i / 8), radius * sin(tau * i / 8), 0)
          for radius in (1, 2) for i in range(8)]
mesh.from_pydata(points, [], [(8+i, 8+(i+1)%8, (i+1)%8, i) for i in range(8)])
obj = bpy.data.objects.new(name, mesh)
bpy.context.collection.objects.link(obj)
for selected in bpy.context.selected_objects:
    selected.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode='EDIT')
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
view = area.spaces.active.region_3d
view.view_rotation = Quaternion((1, 0, 0, 0))
view.view_location = (0, 0, 0)
view.view_distance = 8
view.view_perspective = 'ORTHO'
bpy.context.scene.retopo_tool.snapping = False
with bpy.context.temp_override(area=area, region=region):
    bpy.ops.mesh.retopo_tool_mode(mode='GRID')
    bpy.ops.ed.undo_push(message='Fill Hole UI baseline')

def geometry():
    bm = bmesh.from_edit_mesh(bpy.data.objects[name].data)
    bm.verts.ensure_lookup_table()
    return bm

def send(kind, value, point):
    bpy.context.window.event_simulate(type=kind, value=value,
        x=round(region.x + point.x), y=round(region.y + point.y))

step = 0
def tick():
    global step, start, moved, second_start, center
    try:
        trace.append((step, bpy.context.scene.retopo_tool.mode))
        if step == 0:
            assert bpy.context.scene.retopo_tool.bl_rna.properties['mode'].enum_items['RETOPO'].name == 'Draw'
            start = view3d_utils.location_3d_to_region_2d(region, view, Vector((1, 0, 0)))
            send('MOUSEMOVE', 'NOTHING', start)
        elif step == 1:
            send('LEFTMOUSE', 'PRESS', start)
        elif step == 2:
            send('MOUSEMOVE', 'NOTHING', start + Vector((40, 20)))
        elif step == 3:
            send('LEFTMOUSE', 'RELEASE', start + Vector((40, 20)))
        elif step == 4:
            moved = geometry().verts[0].co.copy()
            assert (moved - Vector((1, 0, 0))).length > 0.1, 'Vertex drag did not move'
            assert len(geometry().faces) == 8, 'Drag created faces'
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.undo()
        elif step == 5:
            assert (geometry().verts[0].co - Vector((1, 0, 0))).length < 1e-5, 'Drag undo failed'
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.redo()
        elif step == 6:
            assert (geometry().verts[0].co - moved).length < 1e-5, 'Drag redo failed'
            second_start = view3d_utils.location_3d_to_region_2d(region, view, moved)
            send('MOUSEMOVE', 'NOTHING', second_start)
        elif step == 7:
            send('LEFTMOUSE', 'PRESS', second_start)
        elif step == 8:
            send('MOUSEMOVE', 'NOTHING', second_start + Vector((40, 20)))
        elif step == 9:
            send('RIGHTMOUSE', 'PRESS', second_start + Vector((40, 20)))
        elif step == 10:
            send('RIGHTMOUSE', 'RELEASE', second_start + Vector((40, 20)))
        elif step == 11:
            assert (geometry().verts[0].co - moved).length < 1e-5, 'Cancel did not restore the vertex'
            assert bpy.context.scene.retopo_tool.mode == 'GRID', 'Cancel left Fill Hole'
            center = view3d_utils.location_3d_to_region_2d(region, view, Vector((0, 0, 0)))
            send('MOUSEMOVE', 'NOTHING', center)
        elif step == 12:
            send('LEFTMOUSE', 'PRESS', center)
        elif step == 13:
            send('LEFTMOUSE', 'RELEASE', center)
        else:
            assert len(geometry().faces) > 8, 'Interior click did not fill the reshaped hole'
            assert bpy.context.scene.retopo_tool.mode == 'GRID'
            report.write_text(json.dumps({'status': 'passed', 'version': bpy.app.version_string,
                'checks': ['real mouse drag', 'undo', 'redo', 'cancel', 'fill after reshape', 'Draw label']}))
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 0.3
    except Exception:
        report.write_text(json.dumps({'status': 'failed', 'step': step, 'trace': trace, 'error': traceback.format_exc()}))
        bpy.ops.wm.quit_blender()
        return None

bpy.app.timers.register(tick, first_interval=2)
