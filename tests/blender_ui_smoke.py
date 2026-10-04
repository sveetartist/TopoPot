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
original_triangle_target = addon.fill_triangle_target
def monitor_triangle_target(context, bm, edge, factor):
    target = original_triangle_target(context, bm, edge, factor)
    trace.append(('triangle target', factor, [tuple(v.co) for v in edge.verts], tuple(target.co),
                  addon.topology.edge_triangle_plan(bm, edge, factor, target)[0]))
    return target
addon.fill_triangle_target = monitor_triangle_target
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
bpy.context.scene.retopo_tool.fill_merge = False
with bpy.context.temp_override(area=area, region=region):
    bpy.ops.mesh.retopo_tool_mode(mode='GRID')
    bpy.ops.ed.undo_push(message='Fill Hole UI baseline')

def geometry():
    bm = bmesh.from_edit_mesh(bpy.data.objects[name].data)
    bm.verts.ensure_lookup_table()
    return bm

def send(kind, value, point, ctrl=False):
    bpy.context.window.event_simulate(type=kind, value=value,
        x=round(region.x + point.x), y=round(region.y + point.y), ctrl=ctrl)

step = 0
def tick():
    global step, start, moved, second_start, center, merge_target, target_co, edge_start, edge_originals, edge_midpoint, ctrl_point
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
            # Grab near the edge of Pick Radius; merge follows the cursor even
            # when the grab offset is larger than Merge Radius.
            second_start = view3d_utils.location_3d_to_region_2d(region, view, moved) + Vector((13, 0))
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
            bpy.context.scene.retopo_tool.fill_merge = True
            target_co = geometry().verts[1].co.copy()
            merge_target = view3d_utils.location_3d_to_region_2d(region, view, target_co)
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.undo_push(message='Merge option enabled')
            send('MOUSEMOVE', 'NOTHING', second_start)
        elif step == 12:
            send('LEFTMOUSE', 'PRESS', second_start)
        elif step == 13:
            send('MOUSEMOVE', 'NOTHING', merge_target)
        elif step == 14:
            assert len(geometry().verts) == 16, 'Merged before release'
            assert (geometry().verts[0].co - target_co).length < 1e-5, 'Merge target did not snap'
            send('LEFTMOUSE', 'RELEASE', merge_target)
        elif step == 15:
            assert len(geometry().verts) == 15, 'Drop did not weld the vertices'
            assert any((v.co - target_co).length < 1e-5 for v in geometry().verts), 'Merge moved target'
            assert bpy.context.scene.retopo_tool.mode == 'GRID'
            assert addon._merge_target is None
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.undo()
        elif step == 16:
            assert len(geometry().verts) == 16, 'Merge undo failed'
            assert (geometry().verts[0].co - moved).length < 1e-5, 'Merge undo lost source position'
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.redo()
        elif step == 17:
            assert len(geometry().verts) == 15, 'Merge redo failed'
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.undo()
        elif step == 18:
            assert len(geometry().verts) == 16
            bpy.context.scene.retopo_tool.fill_merge = False
            center = view3d_utils.location_3d_to_region_2d(region, view, Vector((0, 0, 0)))
            send('MOUSEMOVE', 'NOTHING', center)
        elif step == 19:
            send('LEFTMOUSE', 'PRESS', center)
        elif step == 20:
            send('LEFTMOUSE', 'RELEASE', center)
        elif step == 21:
            assert len(geometry().faces) > 8, 'Interior click did not fill the reshaped hole'
            assert bpy.context.scene.retopo_tool.mode == 'GRID'
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.undo()
        elif step == 22:
            assert len(geometry().faces) == 8, 'Fill undo failed'
            edge_originals = [v.co.copy() for v in geometry().verts]
            edge_midpoint = (edge_originals[1] + edge_originals[2]) * 0.5
            edge_start = view3d_utils.location_3d_to_region_2d(region, view, edge_midpoint)
            send('MOUSEMOVE', 'NOTHING', edge_start)
        elif step == 23:
            send('LEFTMOUSE', 'PRESS', edge_start)
        elif step == 24:
            send('MOUSEMOVE', 'NOTHING', edge_start + Vector((30, 20)))
        elif step == 25:
            send('LEFTMOUSE', 'RELEASE', edge_start + Vector((30, 20)))
        elif step == 26:
            assert len(geometry().verts) == 16, 'Edge drag changed vertex count'
            deltas = [v.co - original for v, original in zip(geometry().verts, edge_originals)]
            assert deltas[1].length > 0.1 and (deltas[1] - deltas[2]).length < 1e-5, 'Edge endpoints did not move together'
            assert all(d.length < 1e-5 for i, d in enumerate(deltas) if i not in (1, 2)), 'Edge drag moved other vertices'
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.undo()
        elif step == 27:
            assert all((v.co - co).length < 1e-5 for v, co in zip(geometry().verts, edge_originals)), 'Edge drag undo failed'
            send('MOUSEMOVE', 'NOTHING', edge_start)
        elif step == 28:
            send('LEFTMOUSE', 'PRESS', edge_start)
        elif step == 29:
            send('LEFTMOUSE', 'RELEASE', edge_start)
        elif step == 30:
            assert len(geometry().verts) == 15, 'Edge click did not collapse endpoints'
            assert any((v.co - edge_midpoint).length < 1e-5 for v in geometry().verts), 'Edge did not collapse to midpoint'
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.undo()
        elif step == 31:
            assert len(geometry().verts) == 16, 'Edge collapse undo failed'
            send('MOUSEMOVE', 'NOTHING', edge_start)
        elif step == 32:
            bm = geometry()
            edge = bm.edges.get((bm.verts[1], bm.verts[2]))
            with bpy.context.temp_override(area=area, region=region):
                factor = addon.edge_parameter(bpy.context, edge, Vector((round(edge_start.x), round(edge_start.y))))
            ctrl_point = edge.verts[0].co.lerp(edge.verts[1].co, factor)
            send('LEFTMOUSE', 'PRESS', edge_start, ctrl=True)
        elif step == 33:
            send('LEFTMOUSE', 'RELEASE', edge_start, ctrl=True)
        elif step == 34:
            bm = geometry()
            assert len(bm.verts) == 17, 'Ctrl-click did not split edge'
            vertex = next(v for v in bm.verts if (v.co - ctrl_point).length < 1e-5)
            assert any(len(f.verts) == 3 and vertex in f.verts for f in bm.faces), 'Ctrl-click did not create triangle'
            assert len(bm.faces) == 9
            assert all(len(e.link_faces) <= 2 for e in bm.edges)
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.ed.undo()
        else:
            assert len(geometry().verts) == 16 and len(geometry().faces) == 8, 'Ctrl-click undo failed'
            assert bpy.context.scene.retopo_tool.mode == 'GRID'
            report.write_text(json.dumps({'status': 'passed', 'version': bpy.app.version_string,
                'checks': ['real mouse drag', 'undo', 'redo', 'cancel', 'merge on release',
                           'merge with off-center grip', 'merge undo/redo', 'fill after reshape',
                           'edge drag', 'edge collapse', 'Ctrl-click edge triangle', 'edge undo', 'Draw label']}))
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 0.3
    except Exception:
        error = traceback.format_exc()
        bm = geometry()
        bm.verts.index_update()
        report.write_text(json.dumps({'status': 'failed', 'step': step, 'trace': trace, 'error': error,
                                     'vertices': [tuple(v.co) for v in bm.verts],
                                     'faces': [[v.index for v in f.verts] for f in bm.faces]}))
        bpy.ops.wm.quit_blender()
        return None

bpy.app.timers.register(tick, first_interval=2)
