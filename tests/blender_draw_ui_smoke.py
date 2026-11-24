"""Real Draw events, stable strip rows, local movement and undo in an isolated window.

Run Blender --factory-startup --enable-event-simulate --python this_file.
Check zips/blender_draw_ui_<major>_<minor>.json: Blender may exit 0 on failure.
"""
import json
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
import bpy
import bmesh
from mathutils import Vector, Quaternion
from bpy_extras import view3d_utils

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import topopot_retopo_tool as addon

report = root / 'zips' / f'blender_draw_ui_{bpy.app.version[0]}_{bpy.app.version[1]}.json'
report.parent.mkdir(exist_ok=True)
report.write_text(json.dumps({'status': 'running'}))
addon.register()
assert all(area.spaces.active.overlay.show_retopology for window in bpy.context.window_manager.windows
           for area in window.screen.areas if area.type == 'VIEW_3D'), 'Retopology View default is off'
bpy.context.preferences.view.show_splash = False
name = 'Draw UI regression'
mesh = bpy.data.meshes.new(name)
mesh.from_pydata([(1, 0, 0), (2, 0, 0)], [(0, 1)], [])
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
view.view_location = (0, 2, 0)
view.view_distance = 10
view.view_perspective = 'ORTHO'
settings = bpy.context.scene.retopo_tool
settings.snapping = False
assert not settings.x_symmetry
assert tuple(settings.symmetry_axes) == (True, False, False) and not settings.snap_to_center
assert addon.RP_PT_panel.bl_label == f"TopoPot - Retopology Tool {addon.ADDON_VERSION}"
# Old scenes can retain raw values from removed RNA properties. They must have
# no effect on ordinary Draw or Brush behavior.
settings['symmetry'] = True
settings.quad_only = False


def geometry():
    return bmesh.from_edit_mesh(bpy.data.objects[name].data)


def send(kind, value, co, ctrl=True, alt=False, shift=False):
    point = view3d_utils.location_3d_to_region_2d(region, view, Vector((*co, 0)))
    assert point is not None
    bpy.context.window.event_simulate(type=kind, value=value,
        x=round(region.x + point.x), y=round(region.y + point.y), ctrl=ctrl, alt=alt, shift=shift)


def undo(redo=False):
    with bpy.context.temp_override(area=area, region=region):
        (bpy.ops.ed.redo if redo else bpy.ops.ed.undo)()


def preview():
    assert addon._strip_preview is not None, 'Strip gesture did not produce a preview'
    return [[co.copy() for co in face] for face in addon._strip_preview[1]]


def same_rows(first, second):
    return len(first) == len(second) and all(
        len(a) == len(b) and all((x - y).length < 1e-5 for x, y in zip(a, b))
        for a, b in zip(first, second))


def connectivity():
    bm = geometry()
    bm.verts.index_update()
    return (len(bm.verts), sorted(tuple(sorted(v.index for v in e.verts)) for e in bm.edges),
            sorted(tuple(sorted(v.index for v in f.verts)) for f in bm.faces))


bm = geometry()
addon.topology.select(bm, next(iter(bm.edges)))
addon.sync(obj)
with bpy.context.temp_override(area=area, region=region):
    bpy.ops.mesh.retopo_tool_mode(mode='RETOPO')
    bpy.ops.ed.undo_push(message='Draw UI baseline')


def scenario():
    send('LEFT_CTRL', 'PRESS', (2, 1))
    yield
    for co in ((2, 1), (1, 1)):
        send('MOUSEMOVE', 'NOTHING', co)
        yield
        send('LEFTMOUSE', 'PRESS', co)
        yield
        send('LEFTMOUSE', 'RELEASE', co)
        yield
        assert len(geometry().faces) == 1, 'Draw click did not create exactly one face'
        assert all(len(f.verts) == (3 if co == (2, 1) else 4) for f in geometry().faces)
    undo()
    yield
    assert all(len(f.verts) == 3 for f in geometry().faces), 'Pending triangle undo failed'
    undo(redo=True)
    yield
    assert all(len(f.verts) == 4 for f in geometry().faces), 'Quad redo failed'
    send('MOUSEMOVE', 'NOTHING', (1.5, 1))
    yield
    send('LEFTMOUSE', 'PRESS', (1.5, 1))
    yield
    send('MOUSEMOVE', 'NOTHING', (1.5, 3.9))
    yield
    rows = preview()[:2]
    assert len(preview()) == 3, 'Expected three rows'
    send('MOUSEMOVE', 'NOTHING', (1.5, 4.1))
    yield
    assert same_rows(rows, preview()[:2]), 'Completed rows moved as the tip grew'
    send('MOUSEMOVE', 'NOTHING', (1.5, 4.8))
    yield
    assert len(preview()) == 4 and same_rows(rows, preview()[:2]), 'New row redistributed earlier rows'
    send('MOUSEMOVE', 'NOTHING', (1.5, 3.7))
    yield
    expected = preview()
    assert len(expected) == 3 and same_rows(rows, expected[:2]), 'Backtracking moved completed rows'
    send('LEFTMOUSE', 'RELEASE', (1.5, 3.7))
    yield
    bm = geometry()
    assert len(bm.faces) == 4, 'Strip release did not commit its three faces'
    bm.normal_update()
    assert all(f.normal.z > 0.99 for f in bm.faces), 'Strip normals face inward'
    actual = [[v.co.copy() for v in f.verts] for f in list(bm.faces)[1:]]
    assert all(any(same_rows([face], [other]) for other in actual) for face in expected), 'Preview and commit differ'
    undo()
    yield
    assert len(geometry().faces) == 1, 'Strip must undo in one step'
    undo(redo=True)
    yield
    assert len(geometry().faces) == 4, 'Strip redo failed'
    send('MOUSEMOVE', 'NOTHING', (1.5, 3.7))
    yield
    send('LEFTMOUSE', 'PRESS', (1.5, 3.7))
    yield
    send('MOUSEMOVE', 'NOTHING', (1.5, 5.2))
    yield
    assert preview(), 'Canceled stroke had no preview'
    send('ESC', 'PRESS', (1.5, 5.2))
    yield
    send('LEFTMOUSE', 'RELEASE', (1.5, 5.2))
    yield
    assert len(geometry().faces) == 4 and addon._strip_preview is None, 'Cancel changed the mesh'
    send('LEFT_CTRL', 'RELEASE', (0, 0.5), ctrl=False)
    yield
    bm = geometry()
    bm.clear()
    source = [bm.verts.new((x, y, 0)) for x, y in ((1, 0), (2, 0), (2, 1), (1, 1))]
    other = [bm.verts.new((-v.co.x, v.co.y, 0)) for v in source]
    bm.faces.new(source)
    bm.faces.new((other[2], other[1], other[0]))
    bm.faces.new((other[3], other[2], other[0]))
    addon.topology.select(bm, source[0])
    addon.sync(bpy.data.objects[name])
    original = connectivity()
    untouched = [tuple(v.co) for v in bm.verts if v.co.x < 0]
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.ed.undo_push(message='Different face layouts movement baseline')
    send('MOUSEMOVE', 'NOTHING', (1, 0), ctrl=False)
    yield
    send('LEFTMOUSE', 'PRESS', (1, 0), ctrl=False)
    yield
    send('MOUSEMOVE', 'NOTHING', (1, 0.3), ctrl=False)
    yield
    assert connectivity() == original, 'Vertex movement created cuts on the opposite side'
    send('LEFTMOUSE', 'RELEASE', (1, 0.3), ctrl=False)
    yield
    assert connectivity() == original, 'Vertex release changed face connectivity'
    assert [tuple(v.co) for v in geometry().verts if v.co.x < 0] == untouched, 'Vertex drag moved other geometry'
    undo()
    yield
    assert connectivity() == original, 'Vertex undo changed connectivity'
    assert sum(abs(v.co.y) < 1e-5 for v in geometry().verts) == 4, 'Vertex undo did not restore positions'
    undo(redo=True)
    yield
    assert connectivity() == original, 'Vertex redo changed connectivity'
    send('MOUSEMOVE', 'NOTHING', (1.5, 0.15), ctrl=False)
    yield
    send('LEFTMOUSE', 'PRESS', (1.5, 0.15), ctrl=False)
    yield
    send('MOUSEMOVE', 'NOTHING', (1.5, 0.45), ctrl=False)
    yield
    assert connectivity() == original, 'Edge movement created cuts on the opposite side'
    send('LEFTMOUSE', 'RELEASE', (1.5, 0.45), ctrl=False)
    yield
    assert connectivity() == original, 'Edge release changed face connectivity'
    assert [tuple(v.co) for v in geometry().verts if v.co.x < 0] == untouched, 'Edge drag moved other geometry'
    undo()
    yield
    assert connectivity() == original, 'Edge undo changed connectivity'
    bm = geometry()
    bm.clear()
    bottom = [bm.verts.new((x, 0, 0)) for x in (0, 1, 2)]
    top = [bm.verts.new((x, 2, 0)) for x in (0, 1, 2)]
    for i in range(2):
        bm.faces.new((bottom[i], bottom[i + 1], top[i + 1], top[i]))
    addon.topology.select(bm, bm.edges.get((bottom[0], top[0])))
    addon.sync(bpy.data.objects[name])
    original = connectivity()
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.ed.undo_push(message='Alt loop cut baseline')
    send('MOUSEMOVE', 'NOTHING', (1, 0.6), ctrl=False)
    yield
    send('LEFT_ALT', 'PRESS', (1, 0.6), ctrl=False, alt=True)
    yield
    assert connectivity() == original, 'Alt hover changed the mesh'
    active = addon.topology.active(geometry())
    assert isinstance(active, bmesh.types.BMEdge) and all(abs(v.co.x) < 1e-6 for v in active.verts), 'Alt hover reselected an edge'
    cache = addon._hover_preview_cache
    assert cache and cache[0][0] == 'KNIFE' and cache[1] == 'POINTS' and len(cache[2]) == 1, 'Alt hover did not preview a knife point'
    hover = [co.copy() for co in cache[2]]
    send('LEFTMOUSE', 'PRESS', (1, 0.6), ctrl=False, alt=True)
    yield
    assert addon._knife_preview is not None and addon._loop_preview is None, 'Alt press must begin as a knife click'
    assert same_rows([hover], [addon._knife_preview[2]]), 'Click differs from the knife hover preview'
    assert connectivity() == original, 'Knife press changed the mesh before release'
    send('MOUSEMOVE', 'NOTHING', (1.2, 0.6), ctrl=False, alt=True)
    yield
    assert addon._loop_preview is not None and addon._knife_preview is None, 'Alt-drag did not switch to loop cut'
    loop_preview = [co.copy() for co in addon._loop_preview[1]]
    assert any((co - hover[0]).length < 1e-5 for co in loop_preview), 'Loop changed the pressed cut position'
    send('MOUSEMOVE', 'NOTHING', (2, 1.6), ctrl=False, alt=True)
    yield
    assert not same_rows([loop_preview], [addon._loop_preview[1]]), 'Loop preview did not follow the mouse'
    loop_preview = [co.copy() for co in addon._loop_preview[1]]
    assert all(abs(co.y - 1.6) < 0.03 for co in loop_preview), 'Loop preview did not slide to the mouse position'
    assert {round(co.x) for co in loop_preview} == {0, 1, 2}, 'Loop switched its source edge'
    send('LEFTMOUSE', 'RELEASE', (2, 1.6), ctrl=False, alt=True)
    yield
    bm = geometry()
    assert len(bm.verts) == 9 and len(bm.faces) == 4, 'Alt-drag did not cut both quads'
    assert all(len(face.verts) == 4 for face in bm.faces), 'Alt-drag deleted or dissolved a face'
    cut_points = [tuple(v.co) for edge in bm.edges if edge.select for v in edge.verts]
    assert sorted(cut_points) == sorted(tuple(co) for co in loop_preview), 'Loop commit differs from preview'
    assert addon._loop_preview is None
    send('LEFT_ALT', 'RELEASE', (2, 1.6), ctrl=False)
    yield
    assert not bpy.context.window_manager.retopo_tool_alt, 'Alt preview stayed active after release'
    undo()
    yield
    assert connectivity() == original, 'Loop cut did not undo in one step'
    undo(redo=True)
    yield
    assert len(geometry().faces) == 4 and len(geometry().verts) == 9, 'Loop redo failed'
    after_cut = connectivity()
    send('LEFT_ALT', 'PRESS', (0, 1.2), ctrl=False, alt=True)
    yield
    send('MOUSEMOVE', 'NOTHING', (0, 1.2), ctrl=False, alt=True)
    yield
    send('LEFTMOUSE', 'PRESS', (0, 1.2), ctrl=False, alt=True)
    yield
    send('MOUSEMOVE', 'NOTHING', (0.2, 1.2), ctrl=False, alt=True)
    yield
    assert addon._loop_preview is not None, 'Selected/boundary edge did not preview a loop'
    send('ESC', 'PRESS', (0, 1.2), ctrl=False, alt=True)
    yield
    send('LEFTMOUSE', 'RELEASE', (0, 1.2), ctrl=False, alt=True)
    yield
    send('LEFT_ALT', 'RELEASE', (0, 1.2), ctrl=False)
    yield
    assert connectivity() == after_cut and addon._loop_preview is None, 'Loop cancel changed the mesh'
    send('LEFT_CTRL', 'PRESS', (1, 1.2))
    yield
    send('MOUSEMOVE', 'NOTHING', (1, 1.2))
    yield
    send('LEFTMOUSE', 'PRESS', (1, 1.2))
    yield
    send('MOUSEMOVE', 'NOTHING', (1, 1.6))
    yield
    send('LEFTMOUSE', 'RELEASE', (1, 1.6))
    yield
    send('LEFT_CTRL', 'RELEASE', (1, 1.6), ctrl=False)
    yield
    assert connectivity() == after_cut, 'Ctrl-drag unexpectedly cut the mesh'
    bm = geometry()
    bm.clear()
    bottom = [bm.verts.new((x, 0, 0)) for x in (0, 1, 2)]
    top = [bm.verts.new((x, 2, 0)) for x in (0, 1, 2)]
    for i in range(2):
        bm.faces.new((bottom[i], bottom[i + 1], top[i + 1], top[i]))
    addon.topology.select(bm, bm.edges.get((bottom[0], top[0])))
    addon.sync(bpy.data.objects[name])
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.ed.undo_push(message='Knife click baseline')
    send('LEFT_ALT', 'PRESS', (1, 0.8), ctrl=False, alt=True)
    yield
    for co, expected_verts, expected_faces in (((1, 0.8), 7, 2), ((0, 0.8), 8, 3)):
        send('MOUSEMOVE', 'NOTHING', co, ctrl=False, alt=True)
        yield
        cache = addon._hover_preview_cache
        assert cache and cache[0][0] == 'KNIFE', 'Knife hover cache missing'
        send('LEFTMOUSE', 'PRESS', co, ctrl=False, alt=True)
        yield
        assert addon._knife_preview is not None and addon._loop_preview is None, 'Knife click promoted without a drag'
        send('LEFTMOUSE', 'RELEASE', co, ctrl=False, alt=True)
        yield
        assert len(geometry().verts) == expected_verts and len(geometry().faces) == expected_faces, 'Knife click did not insert/connect its point'
        assert isinstance(addon.topology.active(geometry()), bmesh.types.BMVert), 'Knife did not keep its endpoint selected'
    assert not any(abs(v.co.x - 2) < 1e-5 and abs(v.co.y - 0.8) < 0.03 for v in geometry().verts), 'Knife click unexpectedly cut the whole loop'
    send('LEFT_ALT', 'RELEASE', (0, 0.8), ctrl=False)
    yield
    undo()
    yield
    assert len(geometry().verts) == 7 and len(geometry().faces) == 2, 'Knife connection undo failed'
    undo(redo=True)
    yield
    assert len(geometry().verts) == 8 and len(geometry().faces) == 3, 'Knife connection redo failed'
    bm = geometry()
    bm.clear()
    verts = [bm.verts.new(co) for co in ((0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0))]
    bm.faces.new(verts)
    addon.topology.select(bm, bm.edges.get(verts[:2]))
    addon.sync(bpy.data.objects[name])
    original = connectivity()
    send('LEFT_CTRL', 'PRESS', (1, 1))
    yield
    for point, expected_y in (((1, 1.02), 0), ((1, 1.8), 2), ((1, -0.4), 0)):
        send('MOUSEMOVE', 'NOTHING', point)
        yield
        active = addon.topology.active(geometry())
        assert isinstance(active, bmesh.types.BMEdge) and all(abs(v.co.y - expected_y) < 1e-5 for v in active.verts), 'Ctrl hover did not choose the expected border'
        assert connectivity() == original, 'Ctrl hover modified topology'
    send('LEFT_CTRL', 'RELEASE', (1, -0.4), ctrl=False)
    yield
    bpy.context.scene.retopo_tool.auto_reselect = False
    send('LEFT_CTRL', 'PRESS', (1, 1.8))
    yield
    send('MOUSEMOVE', 'NOTHING', (1, 1.8))
    yield
    active = addon.topology.active(geometry())
    assert all(abs(v.co.y) < 1e-5 for v in active.verts), 'Disabled Auto Reselect changed the anchor'
    bpy.context.scene.retopo_tool.auto_reselect = True
    send('MOUSEMOVE', 'NOTHING', (1, 1.8))
    yield
    active = addon.topology.active(geometry())
    assert all(abs(v.co.y - 2) < 1e-5 for v in active.verts), 'Re-enabled Auto Reselect did not switch'
    send('LEFT_CTRL', 'RELEASE', (1, 1.8), ctrl=False)
    yield

    original = connectivity()
    for selected_count in (1, 3):
        bm = geometry()
        addon.topology.select(bm, None)
        selected = list(bm.verts)[:selected_count]
        for vertex in selected:
            vertex.select_set(True)
        bm.select_history.add(selected[-1])
        addon.sync(bpy.data.objects[name])
        send('LEFT_CTRL', 'PRESS', (1, 0))
        yield
        active = addon.topology.active(geometry())
        assert isinstance(active, bmesh.types.BMEdge), 'Ctrl did not reselect vertices to an edge'
        send('LEFTMOUSE', 'PRESS', (1, 0))
        yield
        send('LEFTMOUSE', 'RELEASE', (1, 0))
        yield
        send('LEFT_CTRL', 'RELEASE', (1, 0), ctrl=False)
        yield
        assert connectivity() == original, 'Ctrl-click inserted a cut instead of keeping the edge'

    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.mesh.retopo_tool_mode(mode='BRUSH')
    settings = bpy.context.scene.retopo_tool
    settings.brush_strength = 0.5
    radius = settings.brush_radius
    send('F', 'PRESS', (0, 1), ctrl=False, shift=True)
    yield
    send('F', 'RELEASE', (0, 1), ctrl=False, shift=True)
    yield
    send('MOUSEMOVE', 'NOTHING', (1, 1), ctrl=False)
    yield
    assert settings.brush_strength > 0.5, 'Shift-F did not increase strength'
    assert settings.brush_radius == radius, 'Shift-F changed radius'
    send('LEFTMOUSE', 'PRESS', (1, 1), ctrl=False)
    yield
    send('LEFTMOUSE', 'RELEASE', (1, 1), ctrl=False)
    yield
    confirmed = settings.brush_strength
    send('F', 'PRESS', (1, 1), ctrl=False, shift=True)
    yield
    send('F', 'RELEASE', (1, 1), ctrl=False, shift=True)
    yield
    send('MOUSEMOVE', 'NOTHING', (0, 1), ctrl=False)
    yield
    assert settings.brush_strength < confirmed, 'Shift-F did not decrease strength'
    send('ESC', 'PRESS', (0, 1), ctrl=False)
    yield
    assert abs(settings.brush_strength - confirmed) < 1e-5, 'Strength cancel lost original value'
    send('F', 'PRESS', (0, 1), ctrl=False)
    yield
    send('F', 'RELEASE', (0, 1), ctrl=False)
    yield
    send('MOUSEMOVE', 'NOTHING', (1, 1), ctrl=False)
    yield
    assert settings.brush_radius > radius, 'F no longer adjusts radius'
    assert abs(settings.brush_strength - confirmed) < 1e-5, 'F changed strength'
    send('ESC', 'PRESS', (1, 1), ctrl=False)
    yield
    assert settings.brush_radius == radius, 'Radius cancel failed'
    assert connectivity() == original, 'Brush adjustment changed mesh'
    send('MOUSEMOVE', 'NOTHING', (1, 1), ctrl=False)
    yield
    bpy.ops.screen.screenshot(filepath=str(report.with_name('blender_brush_strength_cursor.png')))

    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.mesh.retopo_tool_mode(mode='RETOPO')
        settings.x_symmetry = True
        settings.auto_reselect = False
        bm = geometry()
        bm.clear()
        left = [bm.verts.new((x, 0, 0)) for x in (-2, -1)]
        right = [bm.verts.new((x, 0, 0)) for x in (1, 2)]
        bm.edges.new(left)
        addon.topology.select(bm, bm.edges.new(right))
        addon.sync(bpy.context.edit_object)
        bpy.ops.ed.undo_push(message='X symmetry baseline')
    yield
    for co in ((2, 1), (1, 1)):
        send('MOUSEMOVE', 'NOTHING', co)
        yield
        send('LEFTMOUSE', 'PRESS', co)
        yield
        send('LEFTMOUSE', 'RELEASE', co)
        yield
    assert [len(face.verts) for face in geometry().faces] == [4, 4], 'Draw did not create mirrored quads'
    assert addon.topology.last_edit_side(geometry()) == 1, 'Draw did not record the right side'

    def height(x):
        return next(v.co.y for v in geometry().verts if abs(v.co.x - x) < 0.08 and v.co.y > 0.5)

    start = height(2)
    send('LEFTMOUSE', 'PRESS', (2, 1), ctrl=False)
    yield
    send('MOUSEMOVE', 'NOTHING', (2, 1.6), ctrl=False)
    yield
    assert height(2) > 1.5 and abs(height(2) - height(-2)) < 1e-5, 'Drag did not mirror live'
    send('ESC', 'PRESS', (2, 1.6), ctrl=False)
    yield
    assert all(abs(height(x) - start) < 1e-5 for x in (-2, 2)), 'Canceled drag did not restore both sides'

    send('LEFTMOUSE', 'PRESS', (2, 1), ctrl=False)
    yield
    send('MOUSEMOVE', 'NOTHING', (2, 1.6), ctrl=False)
    yield
    send('LEFTMOUSE', 'RELEASE', (2, 1.6), ctrl=False)
    yield
    moved_height = height(2)
    assert moved_height > 1.5 and abs(height(-2) - moved_height) < 1e-5, 'Drag did not commit both sides'
    undo()
    yield
    assert all(abs(height(x) - start) < 1e-5 for x in (-2, 2)), 'Undo did not restore both sides'
    undo(redo=True)
    yield
    assert all(abs(height(x) - moved_height) < 1e-5 for x in (-2, 2)), 'Redo did not move both sides'
    undo()
    yield

    settings.x_symmetry = False
    send('LEFTMOUSE', 'PRESS', (-2, 1), ctrl=False)
    yield
    send('MOUSEMOVE', 'NOTHING', (-2, 1.7), ctrl=False)
    yield
    send('LEFTMOUSE', 'RELEASE', (-2, 1.7), ctrl=False)
    yield
    left_height = height(-2)
    assert left_height > 1.6 and abs(height(2) - start) < 1e-5, 'Symmetry-off drag changed both sides'
    assert addon.topology.last_edit_side(geometry()) == -1, 'Drag did not record the left side'
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.mesh.retopo_tool_resymmetrize('INVOKE_DEFAULT', True, source='LAST') == {'FINISHED'}
    yield
    assert all(abs(height(x) - left_height) < 1e-5 for x in (-2, 2)), 'Resymmetrize did not use the left side'
    undo()
    yield
    assert abs(height(2) - start) < 1e-5 and abs(height(-2) - left_height) < 1e-5, 'Resymmetrize undo failed'
    undo(redo=True)
    yield
    assert abs(height(2) - left_height) < 1e-5, 'Resymmetrize redo failed'
    undo()
    yield
    undo()
    yield
    assert addon.topology.last_edit_side(geometry()) == 1, 'Undo did not restore the previous source side'

    with bpy.context.temp_override(area=area, region=region):
        settings.symmetry_axis = 'Y'
        assert tuple(settings.symmetry_axes) == (False, True, False), 'Legacy axis was not restored'
        settings.symmetry_axes = (False, True, False)
        bm = geometry()
        bm.clear()
        bm.verts.new((1, 1, 0))
        bm.verts.new((1, -1, 0))
        addon.sync(bpy.context.edit_object)
        bpy.ops.ed.undo_push(message='Y symmetry baseline')
    yield
    send('LEFTMOUSE', 'PRESS', (1, 1), ctrl=False)
    yield
    send('MOUSEMOVE', 'NOTHING', (2, 1), ctrl=False)
    yield
    send('LEFTMOUSE', 'RELEASE', (2, 1), ctrl=False)
    yield
    assert addon.topology.last_edit_side(geometry(), 1) == 1, 'Y edit used the wrong source axis'
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.mesh.retopo_tool_resymmetrize('INVOKE_DEFAULT', True, source='LAST', axis='Y') == {'FINISHED'}
    yield
    assert all(abs(v.co.x - 2) < 0.08 for v in geometry().verts), 'Resymmetrize missed the Y counterpart'

    settings.x_symmetry = True
    settings.snap_to_center = True
    settings.center_distance = 0.15
    send('LEFTMOUSE', 'PRESS', (2, 1), ctrl=False)
    yield
    send('MOUSEMOVE', 'NOTHING', (2, 0.06), ctrl=False)
    yield
    assert len(geometry().verts) == 2 and all(v.co.y == 0 for v in geometry().verts), 'Drag did not snap to Y = 0'
    send('LEFTMOUSE', 'RELEASE', (2, 0.06), ctrl=False)
    yield
    assert len(geometry().verts) == 1 and next(iter(geometry().verts)).co.y == 0, 'Snap did not weld the Y pair'
    undo()
    yield
    assert len(geometry().verts) == 2 and all(abs(v.co.y) > 0.9 for v in geometry().verts), 'Center weld undo failed'
    undo(redo=True)
    yield
    assert len(geometry().verts) == 1 and next(iter(geometry().verts)).co.y == 0, 'Center weld redo failed'

    with bpy.context.temp_override(area=area, region=region):
        settings.symmetry_axes = (True, True, True)
        settings.x_symmetry = True
        settings.snap_to_center = False
        assert addon.symmetry_options(bpy.context).axes == (0, 1, 2), 'Axis toggles were not independent'
        bm = geometry()
        bm.clear()
        bm.faces.new([bm.verts.new(co) for co in ((1, 1, 1), (2, 1, 1), (1, 2, 1))])
        bm.verts.new((-3, -3, -3))
        addon.topology.record_edit_sides(bm, (1, 1, 1))
        addon.sync(bpy.context.edit_object)
        bpy.ops.ed.undo_push(message='XYZ resymmetrize baseline')
        assert bpy.ops.mesh.retopo_tool_resymmetrize('INVOKE_DEFAULT', True, axis='CURRENT') == {'FINISHED'}
    yield
    assert addon.mesh_counts(geometry()) == (24, 24, 8), 'Resymmetrize missed enabled axes'
    assert len({addon.topology.coordinate_sides(v.co) for v in geometry().verts}) == 8, 'Resymmetrize missed octants'
    undo()
    yield
    assert addon.mesh_counts(geometry()) == (4, 3, 1), 'XYZ Resymmetrize must undo in one step'
    undo(redo=True)
    yield
    assert addon.mesh_counts(geometry()) == (24, 24, 8), 'XYZ Resymmetrize redo failed'


steps = scenario()
step = 0


def tick():
    global step
    try:
        next(steps)
        step += 1
        return 0.3
    except StopIteration:
        report.write_text(json.dumps({'status': 'passed', 'version': bpy.app.version_string,
            'checks': ['pending triangle', 'repeated Draw clicks', 'triangle undo/redo',
                       'stable strip rows', 'backtracking', 'preview matches commit',
                       'strip undo/redo', 'consistent normals', 'cancel', 'symmetry defaults', 'versioned panel title',
                       'Retopology View default', 'vertex movement stays local',
                       'edge movement stays local', 'movement undo/redo',
                       'Alt hover knife preview without reselection', 'Alt-drag loop cut',
                       'stable loop source', 'mouse-controlled loop sliding', 'loop preview matches commit', 'loop undo/redo',
                       'loop cancel', 'Ctrl-drag no longer cuts loops',
                       'Alt-click knife insertion', 'chained knife cut', 'knife undo/redo',
                       'stable Ctrl border reselection without topology edits', 'Auto Reselect toggle',
                       'Ctrl reselects single/multiple vertices', 'Ctrl-click does not cut',
                       'Shift-F strength increase/decrease/confirm/cancel', 'F still adjusts radius',
                       'mirrored Draw and drag', 'mirrored drag undo/redo', 'cancel restores both sides',
                       'Resymmetrize from last edit', 'Resymmetrize undo/redo', 'source side follows undo',
                       'Y resymmetrize', 'Y center snap and weld', 'center weld undo/redo',
                       'XYZ uses all enabled axes', 'XYZ resymmetrize undo/redo']}))
    except Exception:
        report.write_text(json.dumps({'status': 'failed', 'step': step,
                                      'vertices': [tuple(v.co) for v in geometry().verts],
                                      'error': traceback.format_exc()}))
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=2)
