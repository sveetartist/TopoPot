import json, sys, traceback
from pathlib import Path
import bpy
from mathutils import Quaternion
out = {}
def tick():
    try:
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        view = area.spaces.active.region_3d
        view.view_rotation = Quaternion((1,0,0,0)); view.view_location = (0,0,0); view.view_distance = 8
        a = tuple(round(v, 6) for row in view.perspective_matrix for v in row)
        view.view_location = (1, 0, 0)
        b = tuple(round(v, 6) for row in view.perspective_matrix for v in row)
        out['changed_direct'] = a != b
        out['has_update'] = hasattr(view, 'update')
        if hasattr(view, 'update'):
            view.update()
            c = tuple(round(v, 6) for row in view.perspective_matrix for v in row)
            out['changed_after_update'] = b != c
    except Exception:
        out['error'] = traceback.format_exc()
    Path('zips/viewprobe.json').write_text(json.dumps(out, indent=1))
    bpy.ops.wm.quit_blender()
    return None
bpy.app.timers.register(tick, first_interval=1)
