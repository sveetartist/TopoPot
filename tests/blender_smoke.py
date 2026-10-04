import sys
sys.dont_write_bytecode = True
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import ExitStack, nullcontext
from math import cos, sin, tau

import bpy
import bmesh
from mathutils import Vector, Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import topopot_retopo_tool as retopo_tool
from topopot_retopo_tool import topology as t


class TopologyTests(unittest.TestCase):
    def setUp(self):
        retopo_tool.invalidate_visibility()
        retopo_tool._strip_preview = None
        retopo_tool._strip_target = None
        self.bm = bmesh.new()

    def tearDown(self):
        self.bm.free()

    def v(self, x, y):
        return self.bm.verts.new((x, y, 0))

    def hole_ring(self, count=8, center=(0, 0), reverse=False):
        inner = [self.v(center[0] + cos(tau * i / count), center[1] + sin(tau * i / count)) for i in range(count)]
        outer = [self.v(center[0] + 2 * cos(tau * i / count), center[1] + 2 * sin(tau * i / count)) for i in range(count)]
        for i in range(count):
            verts = [outer[i], outer[(i + 1) % count], inner[(i + 1) % count], inner[i]]
            face = self.bm.faces.new(verts[::-1] if reverse else verts)
            face.material_index = 2
        return inner

    def grid_context(self):
        headers = []
        region = SimpleNamespace(x=0, y=0, type='WINDOW', width=400, height=400, as_pointer=lambda: 321)
        return SimpleNamespace(mode='EDIT_MESH',
            edit_object=SimpleNamespace(data=None, matrix_world=Matrix.Identity(4)),
            area=SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: None, header_text_set=headers.append, headers=headers),
            region=region, region_data=SimpleNamespace(perspective_matrix=Matrix.Identity(4)),
            scene=SimpleNamespace(retopo_tool=SimpleNamespace(mode='GRID', target=None, snapping=True,
                offset=0.001, reference_tolerance=12, quad_only=False, radius=0.1)),
            window_manager=SimpleNamespace(modal_handler_add=lambda op: None),
            temp_override=lambda **kwargs: nullcontext())

    def hole_polygon(self, coords, center=(0.5, 0.5)):
        rim = [self.v(x, y) for x, y in coords]
        outer = [self.v(center[0] + (x - center[0]) * 1.5, center[1] + (y - center[1]) * 1.5) for x, y in coords]
        for i in range(len(rim)):
            following = (i + 1) % len(rim)
            face = self.bm.faces.new((outer[i], outer[following], rim[following], rim[i]))
            face.material_index = 5
            face.smooth = True
        return rim

    def test_fill_hole_defaults_to_quads_independently_of_retopo_setting(self):
        retopo_tool.register()
        try:
            self.assertFalse(bpy.context.scene.retopo_tool.quad_only)
            self.assertTrue(retopo_tool.fill_quads_only(bpy.context))
            self.assertEqual(bpy.context.scene.retopo_tool.bl_rna.properties['mode'].enum_items['GRID'].name, 'Fill Hole')
        finally:
            retopo_tool.unregister()

    def test_fill_hole_mixed_fills_odd_and_triangular_holes(self):
        for count in (3, 5, 7, 9):
            with self.subTest(count=count):
                self.bm.clear()
                rim = self.hole_ring(count=count)
                original = {v: v.co.copy() for v in self.bm.verts}
                predicted = retopo_tool.grid_preview(self.bm, rim)
                faces = t.fill_hole(self.bm, rim)
                self.assertEqual(predicted, [[v.co for v in f.verts] for f in faces])
                self.assertTrue(all(len(f.verts) in {3, 4} for f in faces))
                self.assertTrue(any(len(f.verts) == 3 for f in faces))
                self.assertEqual(original, {v: v.co for v in original})
                self.assertTrue(all(len(self.bm.edges.get((a, b)).link_faces) == 2 for a, b in zip(rim, rim[1:] + rim[:1])))
                self.bm.normal_update()
                self.assertTrue(all(f.normal.z > 0 for f in faces))

    def test_fill_hole_mixed_fills_concave_and_non_square_boundaries(self):
        cases = [([(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)], 5),
                 ([(0, 0), (2, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3), (0, 1)], 6),
                 ([(0, 0), (3, 0), (3, 3), (1.5, 1), (0, 3)], 6)]
        for coords, area in cases:
            with self.subTest(coords=coords):
                self.bm.clear()
                rim = self.hole_polygon(coords)
                originals = {v: v.co.copy() for v in self.bm.verts}
                predicted = retopo_tool.grid_preview(self.bm, rim)
                faces = t.fill_hole(self.bm, rim)
                self.assertEqual(predicted, [[v.co for v in f.verts] for f in faces])
                self.assertAlmostEqual(sum(f.calc_area() for f in faces), area, places=5)
                self.assertTrue(all(len(f.verts) in {3, 4} and f.material_index == 5 and f.smooth for f in faces))
                self.assertEqual(originals, {v: v.co for v in originals})
                self.assertTrue(all(len(e.link_faces) <= 2 for e in self.bm.edges))

    def test_fill_hole_mixed_odd_rims_use_interior_quads_with_boundary_triangles(self):
        for count in (5, 7, 9, 11, 15):
            with self.subTest(count=count):
                self.bm.clear()
                rim = self.hole_ring(count=count)
                originals = set(self.bm.verts)
                faces = t.fill_hole(self.bm, rim, quad_only=False)
                triangles = [face for face in faces if len(face.verts) == 3]
                self.assertEqual(len(triangles) % 2, 1)
                self.assertTrue(all(any(v in originals for v in face.verts) for face in triangles))
                if count >= 7:
                    interior = [face for face in faces if all(v not in originals for v in face.verts)]
                    self.assertTrue(interior)
                    self.assertTrue(all(len(face.verts) == 4 for face in interior))
                    self.assertTrue(t._square_shaped(interior))
                else:
                    self.assertEqual(len(triangles), 1)

    def test_fill_hole_mixed_concave_even_rim_uses_quads_without_triangles(self):
        rim = self.hole_polygon([(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)])
        faces = t.fill_hole(self.bm, rim, quad_only=False)
        self.assertEqual(len(faces), 2)
        self.assertTrue(all(len(face.verts) == 4 for face in faces))
        self.assertAlmostEqual(sum(face.calc_area() for face in faces), 5)

    def test_fill_hole_mixed_uses_triangles_instead_of_sliver_quad(self):
        rim = self.hole_polygon([(0, 0), (4, 0), (4, 0.01), (0, 1)])
        faces = t.fill_hole(self.bm, rim, quad_only=False)
        self.assertEqual(len(faces), 2)
        self.assertTrue(all(len(face.verts) == 3 for face in faces))
        self.assertAlmostEqual(sum(face.calc_area() for face in faces), 2.02, places=5)

    def test_fill_hole_large_rim_is_mostly_quads_without_touching_boundary(self):
        for count in (64, 65):
            with self.subTest(count=count):
                self.bm.clear()
                rim = self.hole_ring(count=count)
                original = {v: v.co.copy() for v in self.bm.verts}
                snap = lambda co: Vector((co.x, co.y, 0.1))
                predicted = retopo_tool.grid_preview(self.bm, rim, snap, quad_only=False)
                faces = t.fill_hole(self.bm, rim, snap, quad_only=False)
                self.assertEqual(predicted, [[v.co for v in f.verts] for f in faces])
                self.assertGreater(sum(len(f.verts) == 4 for f in faces), len(faces) * 0.9)
                self.assertEqual(original, {v: v.co for v in original})
                self.assertTrue(all(len(e.link_faces) <= 2 for e in self.bm.edges))
                self.assertTrue(all(abs(v.co.z - 0.1) < 1e-6 for v in self.bm.verts if v not in original))

    def test_fill_hole_triangle_pairing_avoids_greedy_stranded_triangles(self):
        rim = [self.v(x, y) for x, y in [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (0, 1)]]
        faces = [self.bm.faces.new([rim[i] for i in indices])
                 for indices in [(0, 1, 5), (1, 4, 5), (1, 2, 4), (2, 3, 4)]]
        t._pair_fill_triangles(self.bm, rim, faces)
        self.assertEqual(len(self.bm.faces), 2)
        self.assertTrue(all(len(face.verts) == 4 for face in self.bm.faces))
        self.assertAlmostEqual(sum(face.calc_area() for face in self.bm.faces), 2)

    def test_fill_hole_mixed_unequal_sides_have_uniform_square_interior_and_complete_rim(self):
        for left_segments in (4, 5):
            for scale in (0.1, 3.0):
                with self.subTest(left_segments=left_segments, scale=scale):
                    self.bm.clear()
                    coords = [(x, 0) for x in range(7)] + [(6, y) for y in range(1, 5)]
                    coords += [(x, 4) for x in (4, 2, 0)]
                    coords += [(0, 4 * i / left_segments) for i in range(left_segments - 1, 0, -1)]
                    rim = self.hole_polygon(coords, center=(3, 2))
                    transform = (Matrix.Translation((2, -3, 1)) @ Matrix.Rotation(0.4, 4, 'X')
                                 @ Matrix.Rotation(0.7, 4, 'Z') @ Matrix.Scale(scale, 4))
                    for vertex in self.bm.verts:
                        vertex.co = transform @ vertex.co
                    positions = {vertex: vertex.co.copy() for vertex in self.bm.verts}
                    original_edges = set(self.bm.edges)
                    predicted = retopo_tool.grid_preview(self.bm, rim, quad_only=False)
                    faces = t.fill_hole(self.bm, rim, quad_only=False)
                    self.assertEqual(predicted, [[v.co for v in face.verts] for face in faces])
                    interior = [face for face in faces if all(v not in positions for v in face.verts)]
                    self.assertGreaterEqual(len(interior), 8)
                    for face in interior:
                        self.assertEqual(len(face.verts), 4)
                        self.assertAlmostEqual(face.calc_area() / scale ** 2, 1, places=4)
                        vectors = [loop.link_loop_next.vert.co - loop.vert.co for loop in face.loops]
                        for a, b in zip(vectors, vectors[1:] + vectors[:1]):
                            self.assertAlmostEqual(a.length / b.length, 1, places=4)
                            self.assertAlmostEqual(a.normalized().dot(b.normalized()), 0, places=4)
                    face_set = set(faces)
                    for a, b in zip(rim, rim[1:] + rim[:1]):
                        edge = self.bm.edges.get((a, b))
                        self.assertEqual(sum(face in face_set for face in edge.link_faces), 1)
                        self.assertEqual(len(edge.link_faces), 2)
                    self.assertTrue(all(len(e.link_faces) == 2 for e in self.bm.edges if e not in original_edges))
                    connected, queue = set(), [faces[0]]
                    while queue:
                        face = queue.pop()
                        if face not in connected:
                            connected.add(face)
                            queue.extend(other for edge in face.edges for other in edge.link_faces
                                         if other in face_set and other not in connected)
                    self.assertEqual(connected, face_set)
                    self.assertEqual(positions, {v: v.co for v in positions})
                    self.assertTrue(all(face.material_index == 5 and face.smooth for face in faces))
                    self.assertAlmostEqual(sum(face.calc_area() for face in faces) / scale ** 2, 24, places=4)

    def test_fill_hole_mixed_concave_dense_rim_keeps_grid_inside_notch(self):
        coords = [(x, 0) for x in range(7)] + [(6, y) for y in range(1, 4)]
        coords += [(x, 3) for x in (5, 4, 3)] + [(3, y) for y in (4, 5, 6)]
        coords += [(x, 6) for x in (2, 1, 0)] + [(0, y) for y in (5, 4, 3, 2, 1)]
        rim = self.hole_polygon(coords, center=(1, 1))
        originals = {v: v.co.copy() for v in self.bm.verts}
        faces = t.fill_hole(self.bm, rim, quad_only=False)
        interior = [face for face in faces if all(v not in originals for v in face.verts)]
        self.assertGreaterEqual(len(interior), 6)
        self.assertTrue(all(len(face.verts) == 4 for face in interior))
        self.assertTrue(all(abs(face.calc_area() - 1) < 1e-5 for face in interior))
        self.assertTrue(all(not (face.calc_center_median().x > 3 and face.calc_center_median().y > 3)
                            for face in faces))
        self.assertAlmostEqual(sum(face.calc_area() for face in faces), 27, places=4)
        self.assertEqual(originals, {v: v.co for v in originals})
        self.assertTrue(all(len(self.bm.edges.get((a, b)).link_faces) == 2
                            for a, b in zip(rim, rim[1:] + rim[:1])))

    def test_fill_hole_mixed_connects_multiple_interior_grid_patches(self):
        corners = [(0, 0), (4, 0), (4, 1.5), (6, 1.5), (6, 0), (10, 0),
                   (10, 4), (6, 4), (6, 2.5), (4, 2.5), (4, 4), (0, 4)]
        coords = []
        for a, b in zip(corners, corners[1:] + corners[:1]):
            delta = Vector(b) - Vector(a)
            count = int(delta.length + 0.999)
            coords.extend(tuple(Vector(a) + delta * (i / count)) for i in range(count))
        rim = self.hole_polygon(coords, center=(5, 2))
        original_vertices = set(self.bm.verts)
        faces = t.fill_hole(self.bm, rim, quad_only=False)
        squares = [face for face in faces if all(v not in original_vertices for v in face.verts)
                   and len(face.verts) == 4 and abs(face.calc_area() - 1) < 1e-5]
        self.assertTrue(any(face.calc_center_median().x < 4 for face in squares))
        self.assertTrue(any(face.calc_center_median().x > 6 for face in squares))
        self.assertAlmostEqual(sum(face.calc_area() for face in faces), 34, places=4)
        self.assertTrue(all(len(self.bm.edges.get((a, b)).link_faces) == 2
                            for a, b in zip(rim, rim[1:] + rim[:1])))

    def test_band_connections_replace_far_end_fan_with_nearby_horizontal_rows(self):
        for angle in (0, 0.7):
            with self.subTest(angle=angle):
                self.bm.clear()
                transform = Matrix.Rotation(angle, 4, 'Z') @ Matrix.Rotation(0.4, 4, 'X')
                outer = [self.bm.verts.new(transform @ Vector((0, y, 0))) for y in range(7)]
                inner = [self.bm.verts.new(transform @ Vector((1, y, 0))) for y in range(7)]
                faces = [self.bm.faces.new((outer[i], outer[i + 1], inner[-1])) for i in range(6)]
                faces += [self.bm.faces.new((outer[0], inner[i + 1], inner[i])) for i in range(6)]
                positions = {v: v.co.copy() for v in self.bm.verts}
                boundary = {e for e in self.bm.edges if len(e.link_faces) == 1}
                for face in faces:
                    face.material_index, face.smooth = 3, True
                band, _ = t._align_band_triangles(self.bm, faces, outer,
                    transform.to_3x3() @ Vector((1, 0, 0)), transform.to_3x3() @ Vector((0, 1, 0)), 1)
                for a, b in zip(outer, inner):
                    self.assertIsNotNone(self.bm.edges.get((a, b)))
                connections = [e for e in self.bm.edges if (e.verts[0] in outer) != (e.verts[1] in outer)]
                self.assertTrue(all(e.calc_length() <= 2 ** 0.5 + 1e-5 for e in connections))
                self.assertEqual(len(band), 12)
                self.assertAlmostEqual(sum(face.calc_area() for face in band), 6, places=4)
                self.assertEqual(positions, {v: v.co for v in self.bm.verts})
                self.assertTrue(all(e.is_valid and len(e.link_faces) == 1 for e in boundary))
                self.assertTrue(all(face.material_index == 3 and face.smooth for face in band))

    def test_band_connections_prefer_horizontal_when_diagonals_are_equally_short(self):
        a, b, c, d = [self.v(x, y) for x, y in ((0, 0), (1, 1), (0, 2), (-1, 1))]
        faces = [self.bm.faces.new((a, b, c)), self.bm.faces.new((a, c, d))]
        band, _ = t._align_band_triangles(self.bm, faces, [a, c], Vector((1, 0, 0)), Vector((0, 1, 0)), 1)
        self.assertIsNone(self.bm.edges.get((a, c)))
        self.assertIsNotNone(self.bm.edges.get((b, d)))
        self.assertAlmostEqual(sum(face.calc_area() for face in band), 2)

    def test_fill_hole_mixed_limits_rim_connections_to_nearby_grid_vertices(self):
        corners = [Vector((0, 0)), Vector((10, 0)), Vector((10, 5)), Vector((0, 5))]
        counts = (13, 4, 7, 8)
        coords = [tuple(a.lerp(b, i / count))
                  for a, b, count in zip(corners, corners[1:] + corners[:1], counts) for i in range(count)]
        rim = self.hole_polygon(coords, center=(5, 2.5))
        original_vertices = set(self.bm.verts)
        predicted = retopo_tool.grid_preview(self.bm, rim, quad_only=False)
        faces = t.fill_hole(self.bm, rim, quad_only=False)
        self.assertEqual(predicted, [[v.co for v in face.verts] for face in faces])
        interior = [v for v in self.bm.verts if v not in original_vertices]
        for edge in self.bm.edges:
            a, b = edge.verts
            if (a in rim and b in interior) or (b in rim and a in interior):
                outer = a if a in rim else b
                nearest = min((outer.co - v.co).length for v in interior)
                self.assertLessEqual(edge.calc_length() / nearest, 1.35)

    def test_fill_hole_click_requires_ctrl_for_odd_rim(self):
        self.front_view()
        rim = self.hole_ring(count=9)
        context = self.grid_context()
        context.scene.retopo_tool.quad_only = True
        reports = []
        op = SimpleNamespace(report=lambda level, message: reports.append(message))
        event = self.strip_event('LEFTMOUSE', (0, 0), 'PRESS', ctrl=False)
        counts = len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm), patch.object(retopo_tool, 'sync'):
            self.assertEqual(retopo_tool.RP_OT_grid_fill.invoke(op, context, event), {'CANCELLED'})
            self.assertEqual(counts, (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)))
            self.assertIn('even number', reports[-1])
            event.ctrl = True
            self.assertEqual(retopo_tool.RP_OT_grid_fill.invoke(op, context, event), {'FINISHED'})
        added = [face for face in self.bm.faces if face.select]
        self.assertTrue(any(len(face.verts) == 3 for face in added))
        self.assertTrue(any(len(face.verts) == 4 and all(v not in rim for v in face.verts) for face in added))
        self.assertTrue(context.scene.retopo_tool.quad_only)

    def test_fill_hole_mixed_hover_preview_and_click_irregular_hole(self):
        self.front_view()
        rim = self.hole_polygon([(0, 0), (3, 0), (3, 3), (1.5, 1), (0, 3)])
        context = self.grid_context()
        context.window_manager.retopo_tool_ctrl = True
        mouse = Vector((1.5, 0.5))
        self.assertEqual(set(retopo_tool.hole_at_mouse(context, self.bm, mouse)), set(rim))
        polygons, border, error = retopo_tool.grid_hover_preview(context, self.bm, mouse)
        self.assertIsNone(error)
        self.assertTrue(any(len(polygon) == 3 for polygon in polygons))
        self.assertEqual(len(self.bm.faces), 5)
        event = self.strip_event('MOUSEMOVE', mouse, ctrl=True)
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm):
            retopo_tool.RP_OT_hover.invoke(None, context, event)
        self.assertIn('Fill Hole | Quad/Tri', context.area.headers[-1])
        self.assertIn('triangles', context.area.headers[-1])
        event.type, event.value = 'LEFTMOUSE', 'PRESS'
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm), \
             patch.object(retopo_tool, 'sync'):
            self.assertEqual(retopo_tool.RP_OT_grid_fill.invoke(SimpleNamespace(report=lambda *args: None), context, event), {'FINISHED'})
        self.assertEqual(len(self.bm.faces), 5 + len(polygons))

    def test_fill_hole_ctrl_press_and_release_update_stationary_preview(self):
        self.front_view()
        self.hole_ring(count=7)
        context = self.grid_context()
        event = self.strip_event('LEFT_CTRL', (0, 0), 'PRESS', ctrl=False)
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm):
            retopo_tool.RP_OT_hover.invoke(None, context, event)
        polygons, _, error = retopo_tool.grid_hover_preview(context, self.bm, Vector((0, 0)))
        self.assertTrue(polygons)
        self.assertIsNone(error)
        self.assertIn('Quad/Tri (Ctrl)', context.area.headers[-1])
        event.type, event.value, event.ctrl = 'RIGHT_CTRL', 'RELEASE', True
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm):
            retopo_tool.RP_OT_hover.invoke(None, context, event)
        polygons, border, error = retopo_tool.grid_hover_preview(context, self.bm, Vector((0, 0)))
        self.assertEqual(polygons, [])
        self.assertEqual(len(border), 7)
        self.assertIn('even number', error)
        self.assertIn('Quads Only', context.area.headers[-1])

    def test_fill_hole_mixed_projection_failure_does_not_fall_back_to_flat_faces(self):
        rim = self.hole_ring()
        t.select(self.bm, rim[0])
        counts = len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)
        with self.assertRaises(ValueError):
            t.fill_hole(self.bm, rim, lambda co: Vector((10, 10, 0)))
        self.assertEqual(counts, (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)))
        self.assertEqual(t.active(self.bm), rim[0])

    def test_grid_hover_finds_hole_without_selection_or_mesh_changes(self):
        self.front_view()
        rim = self.hole_ring()
        context = self.grid_context()
        originals = [v.co.copy() for v in self.bm.verts]
        counts = (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces))
        found = retopo_tool.hole_at_mouse(context, self.bm, Vector((0, 0)))
        self.assertEqual(set(found), set(rim))
        polygons, border, error = retopo_tool.grid_hover_preview(context, self.bm, Vector((0, 0)))
        self.assertEqual(len(polygons), 4)
        self.assertEqual(len(border), 8)
        self.assertIsNone(error)
        self.assertEqual(counts, (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)))
        self.assertEqual(originals, [v.co for v in self.bm.verts])
        self.assertFalse(any(f.select for f in self.bm.faces))

    def test_grid_hover_rejects_existing_surface_outer_border_and_backfaces(self):
        self.front_view()
        self.hole_ring()
        context = self.grid_context()
        self.assertIsNone(retopo_tool.hole_at_mouse(context, self.bm, Vector((1.5, 0))))
        self.assertIsNone(retopo_tool.hole_at_mouse(context, self.bm, Vector((3, 0))))
        self.bm.clear()
        self.hole_ring(reverse=True)
        retopo_tool.invalidate_visibility()
        self.assertIsNone(retopo_tool.hole_at_mouse(context, self.bm, Vector((0, 0))))

    def test_grid_hover_rejects_occluded_and_hidden_holes(self):
        self.front_view()
        rim = self.hole_ring()
        context = self.grid_context()
        cover = [self.bm.verts.new((x, y, 1)) for x, y in ((-2, -2), (2, -2), (2, 2), (-2, 2))]
        face = self.bm.faces.new(cover)
        self.assertIsNone(retopo_tool.hole_at_mouse(context, self.bm, Vector((0, 0))))
        self.bm.faces.remove(face)
        self.bm.edges.get((rim[0], rim[1])).hide_set(True)
        retopo_tool.invalidate_visibility()
        self.assertIsNone(retopo_tool.hole_at_mouse(context, self.bm, Vector((0, 0))))

    def test_grid_hover_selects_the_hole_under_cursor_among_multiple_holes(self):
        self.front_view()
        left = self.hole_ring(center=(-3, 0))
        right = self.hole_ring(center=(3, 0))
        context = self.grid_context()
        self.assertEqual(set(retopo_tool.hole_at_mouse(context, self.bm, Vector((-3, 0)))), set(left))
        self.assertEqual(set(retopo_tool.hole_at_mouse(context, self.bm, Vector((3, 0)))), set(right))

    def test_grid_fill_preview_commit_project_only_new_vertices(self):
        rim = self.hole_ring()
        original = set(self.bm.verts)
        positions = {v: v.co.copy() for v in original}
        snap = lambda co: Vector((co.x, co.y, 0.25))
        predicted = retopo_tool.grid_preview(self.bm, rim, snap, quad_only=True)
        faces = t.grid_fill(self.bm, rim, snap)
        self.assertEqual(predicted, [[v.co for v in f.verts] for f in faces])
        self.assertEqual(len(faces), 4)
        self.assertEqual(len(self.bm.verts), 17)
        self.assertTrue(all(len(f.verts) == 4 and f.material_index == 2 and f.select for f in faces))
        self.assertEqual(positions, {v: v.co for v in original})
        self.assertTrue(all(v.co.z == 0.25 for v in self.bm.verts if v not in original))
        self.assertTrue(all(len(self.bm.edges.get((a, b)).link_faces) == 2 for a, b in zip(rim, rim[1:] + rim[:1])))
        self.bm.normal_update()
        self.assertTrue(all(f.normal.z > 0 for f in faces))

    def test_grid_fill_handles_four_and_six_sided_holes(self):
        for count in (4, 6):
            with self.subTest(count=count):
                self.bm.clear()
                rim = self.hole_ring(count=count)
                faces = t.grid_fill(self.bm, rim)
                self.assertEqual(len(faces), count // 2 - 1)
                self.assertTrue(all(len(f.verts) == 4 for f in faces))

    def test_grid_fill_retries_layout_that_originally_extended_outside_irregular_hole(self):
        coords = [(0.817, 0), (1.193, 1.193), (0, 1.411), (-0.452, 0.452),
                  (-1.282, 0), (-1.034, -1.034), (0, -0.664), (1.02, -1.02)]
        rim = self.hole_polygon(coords, center=(0, 0))
        originals = {v: v.co.copy() for v in self.bm.verts}
        predicted = retopo_tool.grid_preview(self.bm, rim, quad_only=True)
        faces = t.grid_fill(self.bm, rim)
        self.assertEqual(predicted, [[v.co for v in f.verts] for f in faces])
        self.assertTrue(all(len(f.verts) == 4 for f in faces))
        area = abs(sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(coords, coords[1:] + coords[:1]))) / 2
        self.assertAlmostEqual(sum(f.calc_area() for f in faces), area, places=5)
        self.assertEqual(originals, {v: v.co for v in originals})
        self.assertTrue(all(len(self.bm.edges.get((a, b)).link_faces) == 2
                            for a, b in zip(rim, rim[1:] + rim[:1])))

    def test_grid_fill_uses_valid_all_quad_partition_for_concave_hole(self):
        rim = self.hole_polygon([(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)])
        originals = {v: v.co.copy() for v in self.bm.verts}
        faces = t.grid_fill(self.bm, rim)
        self.assertEqual(len(faces), 2)
        self.assertTrue(all(len(f.verts) == 4 for f in faces))
        self.assertAlmostEqual(sum(f.calc_area() for f in faces), 5)
        self.assertEqual(originals, {v: v.co for v in originals})
        self.assertTrue(all(f.material_index == 5 and f.smooth for f in faces))

    def test_grid_fill_far_from_origin_keeps_area_and_rim_connections(self):
        coords = [(x, 0) for x in range(5)] + [(4, y) for y in (1, 2, 3)]
        coords += [(x, 3) for x in (3, 2, 1, 0)] + [(0, y) for y in (2, 1)]
        rim = self.hole_polygon(coords, center=(2, 1.5))
        offset = Vector((100000, 100000, 100000))
        for v in self.bm.verts:
            v.co += offset
        originals = {v: v.co.copy() for v in self.bm.verts}
        predicted = retopo_tool.grid_preview(self.bm, rim, quad_only=True)
        faces = t.grid_fill(self.bm, rim)
        self.assertEqual(predicted, [[v.co for v in f.verts] for f in faces])
        self.assertAlmostEqual(sum(f.calc_area() for f in faces), 12)
        self.assertEqual(originals, {v: v.co for v in originals})
        self.assertTrue(all(len(self.bm.edges.get((a, b)).link_faces) == 2
                            for a, b in zip(rim, rim[1:] + rim[:1])))

    def test_fill_validation_still_rejects_faces_outside_concave_hole(self):
        rim = [self.v(x, y) for x, y in ((0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3))]
        corners = [self.v(x, y) for x, y in ((-0.5, -0.5), (3.5, -0.5), (3.5, 3.5), (-0.5, 3.5))]
        face = self.bm.faces.new(corners)
        with self.assertRaisesRegex(ValueError, 'folds or extends outside'):
            t._validate_fill(self.bm, rim, [face])

    def test_quad_grid_failure_explains_mixed_fill_for_fillable_irregular_hole(self):
        coords = [(0, 0), (4, 0), (4, 1), (3, 1), (3, 3), (2, 3), (1, 3), (0, 3)]
        rim = self.hole_polygon(coords, center=(1, 1))
        positions = {v: v.co.copy() for v in self.bm.verts}
        counts = len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)
        with self.assertRaisesRegex(ValueError, 'Hold Ctrl for Quad/Tri'):
            t.grid_fill(self.bm, rim)
        self.assertEqual(counts, (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)))
        faces = t.fill_hole(self.bm, rim, quad_only=False)
        self.assertTrue(any(len(f.verts) == 4 for f in faces))
        self.assertAlmostEqual(sum(f.calc_area() for f in faces), 10)
        self.assertEqual(positions, {v: v.co for v in positions})

    def test_grid_fill_odd_rim_is_highlighted_without_edits(self):
        self.front_view()
        rim = self.hole_ring(count=7)
        context = self.grid_context()
        context.scene.retopo_tool.quad_only = True
        polygons, border, error = retopo_tool.grid_hover_preview(context, self.bm, Vector((0, 0)))
        self.assertEqual(polygons, [])
        self.assertEqual(len(border), 7)
        self.assertIn('even number', error)
        self.assertEqual(len(self.bm.faces), 7)
        self.assertEqual(len(self.bm.verts), 14)

    def test_grid_fill_projection_failure_rolls_back_everything(self):
        rim = self.hole_ring()
        t.select(self.bm, rim[0])
        counts = (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces))
        self.bm.normal_update()
        normals = [f.normal.copy() for f in self.bm.faces]
        with self.assertRaises(ValueError):
            t.grid_fill(self.bm, rim, lambda co: Vector((10, 10, 0)))
        self.assertEqual(counts, (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)))
        self.assertEqual(normals, [f.normal for f in self.bm.faces])
        self.assertEqual(t.active(self.bm), rim[0])

    def test_grid_click_applies_preview_and_stays_in_grid_mode(self):
        self.front_view()
        rim = self.hole_ring()
        context = self.grid_context()
        stroke = SimpleNamespace(report=lambda *args: None)
        event = self.strip_event('LEFTMOUSE', (0, 0), 'PRESS', ctrl=False)
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm), \
             patch.object(retopo_tool, 'sync') as sync:
            self.assertTrue(retopo_tool.RP_OT_grid_fill.poll(context))
            self.assertFalse(retopo_tool.RP_OT_click.poll(context))
            self.assertEqual(retopo_tool.RP_OT_grid_fill.invoke(stroke, context, event), {'FINISHED'})
            sync.assert_called_once_with(context.edit_object)
        self.assertEqual(len(self.bm.faces), 12)
        self.assertEqual(context.scene.retopo_tool.mode, 'GRID')
        self.assertIsNone(retopo_tool.hole_at_mouse(context, self.bm, Vector((0, 0))))

    def grid_drag_setup(self, matrix=None):
        self.front_view()
        rim = self.hole_ring()
        for vertex in self.bm.verts:
            vertex.co *= 20
        context = self.grid_context()
        context.scene.retopo_tool.radius = 2
        if matrix is not None:
            context.edit_object.matrix_world = matrix
        vertex = rim[2]
        world = context.edit_object.matrix_world @ vertex.co
        start = world.xy + Vector((0.5, 0.5))
        stroke = SimpleNamespace(report=lambda *args: None)
        event = self.strip_event('LEFTMOUSE', start, 'PRESS', ctrl=False)
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm), \
             patch.object(retopo_tool, 'hole_at_mouse', side_effect=AssertionError('vertex press tried to fill')):
            self.assertEqual(retopo_tool.RP_OT_grid_fill.invoke(stroke, context, event), {'RUNNING_MODAL'})
        self.assertEqual(stroke.vertex, vertex)
        return context, stroke, vertex

    def test_grid_vertex_drag_moves_only_vertex_and_preserves_grip_offset_and_object_transform(self):
        matrix = Matrix.Translation((10, -5, 2)) @ Matrix.Scale(2, 4)
        context, stroke, vertex = self.grid_drag_setup(matrix)
        originals = {v: v.co.copy() for v in self.bm.verts}
        counts = len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)
        with patch.object(retopo_tool, 'sync') as sync, \
             patch.object(retopo_tool, 'project', side_effect=lambda ctx, mouse, depth: Vector((mouse.x, mouse.y, depth.z))):
            event = self.strip_event('MOUSEMOVE', stroke.start + Vector((8, 6)), ctrl=False)
            self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'RUNNING_MODAL'})
            self.assertEqual(vertex.co, originals[vertex] + Vector((4, 3, 0)))
            # Release applies the final cursor position even without another move event.
            event = self.strip_event('LEFTMOUSE', stroke.start + Vector((10, 8)), 'RELEASE', ctrl=False)
            self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'FINISHED'})
            self.assertEqual(vertex.co, originals[vertex] + Vector((5, 4, 0)))
            self.assertTrue(all(call.kwargs == {'destructive': False} for call in sync.call_args_list))
        self.assertEqual(counts, (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)))
        self.assertTrue(all(v.co == co for v, co in originals.items() if v != vertex))
        self.assertEqual(context.scene.retopo_tool.mode, 'GRID')

    def test_grid_vertex_drag_cancel_restores_shape_and_stays_in_fill_mode(self):
        for cancel in ('ESC', 'RIGHTMOUSE', 'MODE_CHANGE'):
            with self.subTest(cancel=cancel):
                self.bm.clear()
                context, stroke, vertex = self.grid_drag_setup()
                original = vertex.co.copy()
                with patch.object(retopo_tool, 'sync'), \
                     patch.object(retopo_tool, 'project', return_value=original + Vector((5, 0, 0))):
                    event = self.strip_event('MOUSEMOVE', stroke.start + Vector((5, 0)), ctrl=False)
                    self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'RUNNING_MODAL'})
                    self.assertNotEqual(vertex.co, original)
                    if cancel == 'MODE_CHANGE':
                        context.scene.retopo_tool.mode = 'RETOPO'
                    event = self.strip_event(cancel, stroke.start, 'PRESS', ctrl=False)
                    self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'CANCELLED'})
                self.assertEqual(vertex.co, original)
                if cancel != 'MODE_CHANGE':
                    self.assertEqual(context.scene.retopo_tool.mode, 'GRID')

    def test_grid_vertex_click_or_jitter_leaves_hole_unfilled(self):
        context, stroke, vertex = self.grid_drag_setup()
        original = vertex.co.copy()
        counts = len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)
        with patch.object(retopo_tool, 'project', side_effect=AssertionError('jitter moved the vertex')):
            event = self.strip_event('MOUSEMOVE', stroke.start + Vector((1, 1)), ctrl=False)
            self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'RUNNING_MODAL'})
            event.type, event.value = 'LEFTMOUSE', 'RELEASE'
            self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'CANCELLED'})
        self.assertEqual(vertex.co, original)
        self.assertEqual(counts, (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)))

    def test_grid_vertex_drag_uses_reference_projection_without_merging_or_leaving_surface(self):
        context, stroke, vertex = self.grid_drag_setup()
        original = vertex.co.copy()
        target = next(v for v in self.bm.verts if v != vertex)
        counts = len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)
        with patch.object(retopo_tool, 'sync'), \
             patch.object(retopo_tool, 'project', side_effect=[target.co.copy(), None, None]):
            event = self.strip_event('MOUSEMOVE', stroke.start + Vector((5, 0)), ctrl=True)
            self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'RUNNING_MODAL'})
            self.assertEqual(vertex.co, target.co)
            self.assertNotEqual(vertex.co, original)
            event.mouse_x += 5
            self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'RUNNING_MODAL'})
            event.type, event.value = 'LEFTMOUSE', 'RELEASE'
            self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(stroke, context, event), {'FINISHED'})
        self.assertEqual(vertex.co, target.co)
        self.assertTrue(vertex.is_valid and target.is_valid)
        self.assertEqual(counts, (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)))

    def test_grid_hover_notifies_quad_count_and_cached_preview_invalidates(self):
        self.front_view()
        self.hole_ring()
        context = self.grid_context()
        event = self.strip_event('MOUSEMOVE', (0, 0), ctrl=False)
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm):
            self.assertEqual(retopo_tool.RP_OT_hover.invoke(None, context, event), {'PASS_THROUGH'})
        self.assertIn('4 quads', context.area.headers[-1])
        with patch.object(retopo_tool, 'grid_preview', side_effect=AssertionError('preview rebuilt')):
            self.assertEqual(len(retopo_tool.grid_hover_preview(context, self.bm, Vector((0, 0)))[0]), 4)
            self.assertEqual(len(retopo_tool.grid_hover_preview(context, self.bm, Vector((0.1, 0.1)))[0]), 4)
        retopo_tool.invalidate_visibility()
        self.assertIsNone(retopo_tool._grid_preview_cache)

    def test_grid_mode_toggles_off_and_can_exit_without_editing(self):
        context = self.grid_context()
        op = SimpleNamespace(mode='GRID')
        self.assertEqual(retopo_tool.RP_OT_mode.execute(op, context), {'FINISHED'})
        self.assertEqual(context.scene.retopo_tool.mode, 'RETOPO')
        retopo_tool.RP_OT_mode.execute(op, context)
        self.assertEqual(context.scene.retopo_tool.mode, 'GRID')
        self.assertEqual(retopo_tool.RP_OT_grid_stop.execute(None, context), {'FINISHED'})
        self.assertEqual(context.scene.retopo_tool.mode, 'RETOPO')

    def test_grid_pie_replaces_preserve_borders_with_fourth_tool(self):
        entries = []
        def operator(identifier, **kwargs):
            item = SimpleNamespace(identifier=identifier, **kwargs)
            entries.append(item)
            return item
        pie = SimpleNamespace(operator=operator)
        menu = SimpleNamespace(layout=SimpleNamespace(menu_pie=lambda: pie))
        retopo_tool.RP_MT_modes.draw(menu, self.grid_context())
        self.assertEqual([entry.mode for entry in entries], ['RETOPO', 'BRUSH', 'BOX', 'GRID'])
        self.assertEqual(entries[3].text, 'Fill Hole')
        self.assertTrue(entries[3].depress)

    def test_grid_fill_rectangular_hole_uses_corner_aligned_grid(self):
        coords = [(x, 0) for x in range(3)] + [(2, y) for y in range(1, 5)]
        coords += [(x, 4) for x in (1, 0)] + [(0, y) for y in (3, 2, 1)]
        rim = [self.v(x, y) for x, y in coords]
        for a, b in zip(rim, rim[1:] + rim[:1]):
            self.bm.edges.new((a, b))
        faces = t.grid_fill(self.bm, rim)
        self.assertEqual(len(faces), 8)
        self.assertTrue(all(abs(f.calc_area() - 1) < 1e-5 for f in faces))

    def test_grid_side_counts_detect_actual_unequal_sides_including_corners(self):
        coords = [(x, 0) for x in range(3)] + [(2, y) for y in range(1, 5)]
        coords += [(x, 4) for x in (1.5, 1, 0)] + [(0, y) for y in (3, 2, 1)]
        rim = [Vector((x, y, 0)) for x, y in coords]
        for border in (rim, rim[3:] + rim[:3], list(reversed(rim))):
            sides = t.rim_sides(border)
            self.assertEqual(sorted(map(len, sides)), [3, 4, 5, 5])
            self.assertEqual(sum(len(side) - 1 for side in sides), len(rim))
            self.assertEqual({tuple(side[0]) for side in sides},
                             {(0, 0, 0), (2, 0, 0), (2, 4, 0), (0, 4, 0)})
        # Dissolving the extra top-side vertex makes the opposite counts match.
        rim.remove(Vector((1.5, 4, 0)))
        self.assertEqual(sorted(map(len, t.rim_sides(rim))), [3, 3, 5, 5])
        vertices = [self.bm.verts.new(co) for co in rim]
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            self.bm.edges.new((a, b))
        faces = t.grid_fill(self.bm, vertices)
        self.assertEqual(len(faces), 8)
        self.assertTrue(all(len(face.verts) == 4 for face in faces))

    def test_grid_side_labels_show_numbers_on_invalid_quad_fill_and_hide_with_ctrl(self):
        self.front_view()
        self.hole_ring(count=7)
        context = self.grid_context()
        context.preferences = SimpleNamespace(system=SimpleNamespace(ui_scale=1.0))
        polygons, border, error = retopo_tool.grid_hover_preview(context, self.bm, Vector((0, 0)))
        self.assertFalse(polygons)
        self.assertIsNotNone(error)
        matrix = Matrix.Translation((10, 20, 0))
        with ExitStack() as stack:
            calls = {name: stack.enter_context(patch.object(retopo_tool.blf, name))
                     for name in ('size', 'color', 'enable', 'shadow', 'dimensions', 'position', 'draw', 'disable')}
            calls['dimensions'].return_value = (8, 14)
            stack.enter_context(patch.object(retopo_tool.view3d_utils, 'location_3d_to_region_2d',
                                             side_effect=lambda region, view, co: co.xy))
            retopo_tool.draw_grid_side_counts(context, border, matrix, retopo_tool.GRID_ERROR_COLOR)
            self.assertEqual(len(calls['draw'].call_args_list), 4)
            self.assertTrue(all(call.args[1].isdigit() for call in calls['draw'].call_args_list))
            self.assertEqual(sorted(int(call.args[1]) for call in calls['draw'].call_args_list),
                             sorted(map(len, t.rim_sides(border))))
            self.assertTrue(all(Vector((call.args[1] + 4 - 10, call.args[2] + 7 - 20)).length > 10
                                for call in calls['position'].call_args_list))
            calls['disable'].assert_called_once_with(0, retopo_tool.blf.SHADOW)
            context.window_manager.retopo_tool_ctrl = True
            calls['draw'].reset_mock()
            retopo_tool.draw_grid_side_counts(context, border, matrix, retopo_tool.GRID_COLOR)
            calls['draw'].assert_not_called()

    def test_grid_side_counts_spread_corners_around_smooth_rim(self):
        rim = [v.co.copy() for v in self.hole_ring(count=12)]
        self.assertEqual([len(side) for side in t.rim_sides(rim)], [4, 4, 4, 4])

    def test_grid_fill_with_actual_reference_collection_and_object_transform(self):
        self.front_view()
        rim = self.hole_ring()
        mesh = bpy.data.meshes.new('GridReference')
        mesh.from_pydata([(-5, -5, 2), (5, -5, 2), (5, 5, 2), (-5, 5, 2)], [], [(0, 1, 2, 3)])
        reference = bpy.data.objects.new('GridReference', mesh)
        collection = bpy.data.collections.new('GridReferences')
        collection.objects.link(reference)
        bpy.context.scene.collection.children.link(collection)
        context = self.grid_context()
        context.scene.retopo_tool.target = collection
        context.scene.retopo_tool.offset = 0.1
        context.edit_object.matrix_world = Matrix.Translation((0, 0, 0.5))
        context.evaluated_depsgraph_get = bpy.context.evaluated_depsgraph_get
        original = set(self.bm.verts)
        positions = {v: v.co.copy() for v in original}
        try:
            bpy.context.view_layer.update()
            snap = retopo_tool.reference_projector(context)
            polygons = retopo_tool.grid_preview(self.bm, rim, snap, quad_only=True)
            faces = t.grid_fill(self.bm, rim, snap)
            self.assertEqual(polygons, [[v.co for v in f.verts] for f in faces])
            self.assertEqual(positions, {v: v.co for v in original})
            self.assertTrue(all(abs(v.co.z - 1.6) < 1e-5 for v in self.bm.verts if v not in original))
        finally:
            bpy.data.objects.remove(reference, do_unlink=True)
            bpy.data.collections.remove(collection)
            bpy.data.meshes.remove(mesh)

    def test_grid_click_updates_real_edit_mesh(self):
        self.front_view()
        self.hole_ring()
        mesh = bpy.data.meshes.new('GridEdit')
        self.bm.to_mesh(mesh)
        obj = bpy.data.objects.new('GridEdit', mesh)
        bpy.context.collection.objects.link(obj)
        original = bpy.context.view_layer.objects.active
        selected = list(bpy.context.selected_objects)
        retopo_tool.register()
        try:
            for item in selected:
                item.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
            area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(area=area, region=region):
                self.assertEqual(bpy.ops.mesh.retopo_tool_mode(mode='GRID'), {'FINISHED'})
                self.assertTrue(retopo_tool.RP_OT_grid_fill.poll(bpy.context))
                edit_bm = bmesh.from_edit_mesh(mesh)
                vertex = next(v for v in edit_bm.verts if abs(v.co.x - 1) < 1e-5 and abs(v.co.y) < 1e-5)
                drag_context = self.grid_context()
                drag_context.edit_object = obj
                drag = SimpleNamespace(report=lambda *args: None)
                press = self.strip_event('LEFTMOUSE', (1, 0), 'PRESS', ctrl=False)
                with patch.object(retopo_tool, 'pick', return_value=vertex):
                    self.assertEqual(retopo_tool.RP_OT_grid_fill.invoke(drag, drag_context, press), {'RUNNING_MODAL'})
                destination = vertex.co + Vector((0.2, 0, 0))
                with patch.object(retopo_tool, 'project', return_value=destination):
                    retopo_tool._grid_preview_cache = ('stale',)
                    move = self.strip_event('MOUSEMOVE', (6, 0), ctrl=False)
                    self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(drag, drag_context, move), {'RUNNING_MODAL'})
                    self.assertIsNone(retopo_tool._grid_preview_cache)
                    move.type, move.value = 'LEFTMOUSE', 'RELEASE'
                    self.assertEqual(retopo_tool.RP_OT_grid_fill.modal(drag, drag_context, move), {'FINISHED'})
                self.assertEqual(vertex.co, destination)
                stroke = SimpleNamespace(report=lambda *args: None)
                event = self.strip_event('LEFTMOUSE', (0, 0), 'PRESS', ctrl=False)
                with patch.object(retopo_tool, 'pick', return_value=None):
                    self.assertEqual(retopo_tool.RP_OT_grid_fill.invoke(stroke, bpy.context, event), {'FINISHED'})
                self.assertEqual(len(bmesh.from_edit_mesh(mesh).faces), 12)
                self.assertEqual(bpy.ops.mesh.retopo_tool_grid_stop(), {'FINISHED'})
                self.assertEqual(bpy.context.scene.retopo_tool.mode, 'RETOPO')
            bpy.ops.object.mode_set(mode='OBJECT')
            self.assertEqual(len(mesh.polygons), 12)
        finally:
            if bpy.context.mode == 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='OBJECT')
            retopo_tool.unregister()
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            bpy.context.view_layer.objects.active = original
            for item in selected:
                item.select_set(True)

    def strip_setup(self, start=(0, 0), on_edge=True, snap=None, target_y=None):
        self.front_view()
        edge = self.bm.edges.new((self.v(-10, 0), self.v(10, 0)))
        if target_y is not None:
            self.bm.edges.new((self.v(-10, target_y), self.v(10, target_y)))
        t.select(self.bm, edge)
        obj = SimpleNamespace(data=None, matrix_world=Matrix.Identity(4))
        headers = []
        area = SimpleNamespace(tag_redraw=lambda: None, header_text_set=headers.append, headers=headers)
        region = SimpleNamespace(x=0, y=0, as_pointer=lambda: 123)
        settings = SimpleNamespace(mode='RETOPO', target=None, snapping=True, quad_only=False,
                                   radius=14, merge_radius=10, offset=0.001)
        context = SimpleNamespace(mode='EDIT_MESH', edit_object=obj, area=area, region=region,
            region_data=None, scene=SimpleNamespace(retopo_tool=settings),
            window_manager=SimpleNamespace(modal_handler_add=lambda op: None),
            temp_override=lambda **kwargs: nullcontext())
        stroke = SimpleNamespace(report=lambda *args: None)
        event = self.strip_event('LEFTMOUSE', start, 'PRESS')
        with patch.object(retopo_tool, 'pick', return_value=edge if on_edge else None), \
             patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm), \
             patch.object(retopo_tool, 'reference_projector', return_value=snap):
            self.assertEqual(retopo_tool.RP_OT_click.invoke(stroke, context, event), {'RUNNING_MODAL'})
        self.assertTrue(stroke.strip_gesture)
        return context, stroke, edge

    def strip_event(self, kind, xy, value='NOTHING', ctrl=True):
        return SimpleNamespace(type=kind, value=value, ctrl=ctrl, shift=False,
            mouse_x=xy[0], mouse_y=xy[1], mouse_region_x=xy[0], mouse_region_y=xy[1])

    def strip_modal(self, context, stroke, kind, xy, value='NOTHING'):
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm), \
             patch.object(retopo_tool, 'sync'), \
             patch.object(retopo_tool, 'project',
                          side_effect=lambda ctx, mouse, depth: Vector((mouse.x, mouse.y, depth.z))):
            return retopo_tool.RP_OT_click.modal(stroke, context, self.strip_event(kind, xy, value))

    def test_ctrl_drag_strip_preview_release_and_selection(self):
        context, stroke, edge = self.strip_setup()
        self.assertEqual(self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 60)), {'RUNNING_MODAL'})
        expected = retopo_tool._strip_preview[1]
        self.assertEqual(len(expected), 3)
        self.assertEqual((len(self.bm.verts), len(self.bm.faces)), (2, 0))
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (0, 65), 'RELEASE'), {'FINISHED'})
        self.assertEqual(len(self.bm.faces), 4)
        self.assertTrue(all(len(face.verts) == 4 for face in self.bm.faces))
        self.assertTrue(all(len(edge.link_faces) <= 2 for edge in self.bm.edges))
        outer = t.active(self.bm)
        self.assertEqual(len(outer.link_faces), 1)
        self.assertAlmostEqual(sum(v.co.y for v in outer.verts) / 2, 65)
        self.assertIsNone(retopo_tool._strip_preview)

    def test_ctrl_drag_follows_curve_and_projects_new_corners(self):
        snap = lambda co: Vector((co.x, co.y, co.y * 0.05))
        context, stroke, edge = self.strip_setup(snap=snap)
        original = [v.co.copy() for v in edge.verts]
        for point in ((0, 20), (5, 40), (20, 60), (45, 80)):
            self.assertEqual(self.strip_modal(context, stroke, 'MOUSEMOVE', point), {'RUNNING_MODAL'})
        expected = retopo_tool._strip_preview[1]
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (45, 80), 'RELEASE'), {'FINISHED'})
        self.assertEqual(expected, [[v.co for v in face.verts] for face in self.bm.faces])
        self.assertEqual(original, [v.co for v in edge.verts])
        self.assertTrue(all(abs(v.co.z - v.co.y * 0.05) < 1e-5 for v in self.bm.verts))
        center = sum((v.co for v in t.active(self.bm).verts), Vector()) * 0.5
        self.assertAlmostEqual(center.x, 45)
        self.assertAlmostEqual(center.y, 80)
        self.bm.normal_update()
        self.assertTrue(all(face.normal.z > 0 for face in self.bm.faces))

    def test_ctrl_drag_from_empty_space_includes_press_position(self):
        context, stroke, edge = self.strip_setup(start=(0, 25), on_edge=False)
        self.assertEqual(self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 65)), {'RUNNING_MODAL'})
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (0, 65), 'RELEASE'), {'FINISHED'})
        self.assertEqual(len(self.bm.faces), 4)
        self.assertAlmostEqual(max(v.co.y for v in self.bm.verts), 65)

    def test_strip_click_jitter_does_not_start_drawing(self):
        context, stroke, edge = self.strip_setup(start=(0, 25), on_edge=False)
        self.strip_modal(context, stroke, 'MOUSEMOVE', (8, 30))
        self.assertFalse(stroke.dragging)
        self.assertIsNone(retopo_tool._strip_preview)
        self.assertEqual(context.area.headers, [])
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (8, 30), 'RELEASE'), {'FINISHED'})
        self.assertEqual(len(self.bm.faces), 1)
        self.assertEqual(len(next(iter(self.bm.faces)).verts), 3)
        self.assertTrue(any(v.co == Vector((0, 25, 0)) for v in self.bm.verts))

    def test_strip_cannot_start_on_button_release_alone(self):
        context, stroke, edge = self.strip_setup(start=(0, 25), on_edge=False)
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (0, 80), 'RELEASE'), {'FINISHED'})
        self.assertFalse(stroke.dragging)
        self.assertEqual(len(self.bm.faces), 1)
        self.assertEqual(len(next(iter(self.bm.faces)).verts), 3)

    def test_releasing_ctrl_before_threshold_keeps_click_action(self):
        context, stroke, edge = self.strip_setup(start=(0, 25), on_edge=False)
        event = self.strip_event('LEFT_CTRL', (0, 25), 'RELEASE', ctrl=False)
        self.assertEqual(retopo_tool.RP_OT_click.modal(stroke, context, event), {'RUNNING_MODAL'})
        self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 60))
        self.assertFalse(stroke.dragging)
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (0, 60), 'RELEASE'), {'FINISHED'})
        self.assertEqual(len(self.bm.faces), 1)
        self.assertEqual(len(next(iter(self.bm.faces)).verts), 3)

    def test_strip_status_appears_at_start_and_clears_on_cancel(self):
        context, stroke, edge = self.strip_setup()
        self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 30))
        self.assertIn('Drawing strip', context.area.headers[-1])
        self.assertIsNotNone(retopo_tool._strip_preview)
        self.strip_modal(context, stroke, 'ESC', (0, 30), 'PRESS')
        self.assertIsNone(context.area.headers[-1])
        self.assertIsNone(retopo_tool._strip_target)

    def test_drag_strip_connects_to_existing_edge_without_duplicate_vertices(self):
        context, stroke, edge = self.strip_setup(target_y=80)
        target = next(e for e in self.bm.edges if e != edge)
        originals = [v.co.copy() for v in target.verts]
        self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 30))
        self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 80))
        expected = retopo_tool._strip_preview[1]
        self.assertIsNotNone(retopo_tool._strip_target)
        self.assertIn('Connect strip', context.area.headers[-1])
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (0, 80), 'RELEASE'), {'FINISHED'})
        self.assertEqual(len(self.bm.faces), 4)
        self.assertEqual(len(self.bm.verts), 10)
        self.assertEqual(expected, [[v.co for v in face.verts] for face in self.bm.faces])
        self.assertEqual(originals, [v.co for v in target.verts])
        self.assertEqual(t.active(self.bm), target)
        self.assertEqual(len(target.link_faces), 1)
        self.assertIsNone(retopo_tool._strip_target)
        self.assertIsNone(context.area.headers[-1])

    def test_strip_can_leave_hovered_destination_before_release(self):
        context, stroke, edge = self.strip_setup(target_y=80)
        target = next(e for e in self.bm.edges if e != edge)
        self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 80))
        self.assertIsNotNone(retopo_tool._strip_target)
        self.strip_modal(context, stroke, 'MOUSEMOVE', (25, 60))
        self.assertIsNone(retopo_tool._strip_target)
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (25, 60), 'RELEASE'), {'FINISHED'})
        self.assertFalse(target.link_faces)
        self.assertNotEqual(t.active(self.bm), target)

    def test_strip_connects_near_destination_endpoint(self):
        context, stroke, edge = self.strip_setup(target_y=80)
        target = next(e for e in self.bm.edges if e != edge)
        self.strip_modal(context, stroke, 'MOUSEMOVE', (9, 80))
        self.assertIsNotNone(retopo_tool._strip_target)
        self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (9, 80), 'RELEASE'), {'FINISHED'})
        self.assertEqual(t.active(self.bm), target)
        self.assertEqual(len(target.link_faces), 1)

    def test_strip_can_connect_to_boundary_of_existing_face(self):
        source_face, verts = self.quad()
        source_face.material_index = 2
        source = self.bm.edges.get((verts[2], verts[3]))
        a, b, c, d = self.v(0, 8), self.v(2, 8), self.v(2, 10), self.v(0, 10)
        target_face = self.bm.faces.new((a, b, c, d))
        target = self.bm.edges.get((a, b))
        originals = [v.co.copy() for v in target.verts]
        predicted = retopo_tool.strip_preview(self.bm, source, [Vector((1, 4, 0)), Vector((1, 6, 0))], target=target)
        faces = t.quad_strip(self.bm, source, [Vector((1, 4, 0)), Vector((1, 6, 0))], target=target)
        self.assertEqual(predicted, [[v.co for v in face.verts] for face in faces])
        self.assertEqual(len(self.bm.verts), 12)
        self.assertEqual(len(target.link_faces), 2)
        self.assertEqual(originals, [v.co for v in target.verts])
        self.bm.normal_update()
        self.assertTrue(all(f.normal.dot(target_face.normal) > 0.99 for f in faces))
        self.assertTrue(all(f.material_index == 2 for f in faces))

    def test_strip_can_bridge_adjacent_edges_with_triangle(self):
        a, b, c = self.v(0, 0), self.v(20, 0), self.v(20, 20)
        source, target = self.bm.edges.new((a, b)), self.bm.edges.new((b, c))
        predicted = retopo_tool.strip_preview(self.bm, source, [], target=target)
        faces = t.quad_strip(self.bm, source, [], target=target)
        self.assertEqual(len(faces), 1)
        self.assertEqual(len(faces[0].verts), 3)
        self.assertEqual(predicted, [[v.co for v in faces[0].verts]])
        self.assertEqual(len(self.bm.verts), 3)
        self.assertEqual(t.active(self.bm), target)

    def test_canceling_strip_connection_preserves_destination(self):
        context, stroke, edge = self.strip_setup(target_y=80)
        self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 80))
        self.assertEqual(self.strip_modal(context, stroke, 'RIGHTMOUSE', (0, 80), 'PRESS'), {'CANCELLED'})
        self.assertEqual((len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)), (4, 2, 0))
        self.assertEqual(t.active(self.bm), edge)
        self.assertIsNone(retopo_tool._strip_target)

    def test_strip_connection_rollback_preserves_existing_normals(self):
        source_face, verts = self.quad()
        edge = self.bm.edges.get((verts[2], verts[3]))
        a, b, c, d = self.v(0, 8), self.v(2, 8), self.v(2, 10), self.v(0, 10)
        target_face = self.bm.faces.new((d, c, b, a))
        target = self.bm.edges.get((a, b))
        t.select(self.bm, edge)
        self.bm.normal_update()
        normals = [f.normal.copy() for f in self.bm.faces]
        counts = (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces))
        with self.assertRaises(ValueError):
            t.quad_strip(self.bm, edge, [Vector((1, 4, 0)), Vector((1, 6, 0))], target=target)
        self.bm.normal_update()
        self.assertEqual((len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)), counts)
        self.assertEqual(normals, [f.normal for f in self.bm.faces])
        self.assertEqual(t.active(self.bm), edge)

    def test_strip_rejects_connection_to_interior_edge_without_edits(self):
        face, verts = self.quad()
        target = self.bm.edges.get((verts[0], verts[1]))
        self.bm.faces.new((verts[1], verts[0], self.v(0, -2), self.v(2, -2)))
        source = self.bm.edges.new((self.v(0, -8), self.v(2, -8)))
        t.select(self.bm, source)
        counts = (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces))
        with self.assertRaises(ValueError):
            t.quad_strip(self.bm, source, [Vector((1, -6, 0))], target=target)
        self.assertEqual((len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)), counts)
        self.assertEqual(t.active(self.bm), source)

    def test_ctrl_drag_cancel_preserves_mesh_and_selection(self):
        for cancel in ('ESC', 'RIGHTMOUSE'):
            with self.subTest(cancel=cancel):
                self.bm.clear()
                context, stroke, edge = self.strip_setup()
                self.strip_modal(context, stroke, 'MOUSEMOVE', (20, 60))
                self.assertEqual(self.strip_modal(context, stroke, cancel, (20, 60), 'PRESS'), {'CANCELLED'})
                self.assertEqual((len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)), (2, 1, 0))
                self.assertEqual(t.active(self.bm), edge)
                self.assertIsNone(retopo_tool._strip_preview)

    def test_ctrl_click_keeps_triangle_and_quad_modes(self):
        for quad_only in (False, True):
            with self.subTest(quad_only=quad_only):
                self.bm.clear()
                context, stroke, edge = self.strip_setup(start=(0, 25), on_edge=False)
                context.scene.retopo_tool.quad_only = quad_only
                context.scene.retopo_tool.snapping = False
                self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (0, 25), 'RELEASE'), {'FINISHED'})
                self.assertEqual(len(self.bm.faces), 1)
                self.assertEqual(len(next(iter(self.bm.faces)).verts), 4 if quad_only else 3)

    def test_ctrl_click_on_selected_edge_still_subdivides(self):
        context, stroke, edge = self.strip_setup()
        with patch.object(retopo_tool, 'edge_parameter', return_value=0.5):
            self.assertEqual(self.strip_modal(context, stroke, 'LEFTMOUSE', (0, 0), 'RELEASE'), {'FINISHED'})
        self.assertEqual((len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)), (3, 2, 0))
        self.assertIsInstance(t.active(self.bm), bmesh.types.BMVert)

    def test_strip_sampling_is_independent_of_event_frequency(self):
        coarse = t.StripPath(Vector((0, 0, 0)), 20)
        fine = t.StripPath(Vector((0, 0, 0)), 20)
        coarse.add(Vector((0, 60, 0)))
        coarse.add(Vector((40, 100, 0)))
        for y in range(1, 61):
            fine.add(Vector((0, y, 0)))
        for i in range(1, 41):
            fine.add(Vector((i, 60 + i, 0)))
        self.assertEqual(len(coarse.positions()), len(fine.positions()))
        for a, b in zip(coarse.positions(), fine.positions()):
            self.assertLess((a - b).length, 1e-4)

    def test_invalid_strip_rolls_back_faces_and_preserves_pending(self):
        a, b = self.v(-10, 0), self.v(10, 0)
        edge = self.bm.edges.new((a, b))
        t.extend(self.bm, edge, self.v(10, -20))
        edge = self.bm.edges.get((a, b))
        t.select(self.bm, edge)
        counts = (len(self.bm.verts), len(self.bm.edges), len(self.bm.faces))
        with self.assertRaises(ValueError):
            t.quad_strip(self.bm, edge, [Vector((0, 20, 0)), Vector((0, 20, 0))])
        self.assertEqual((len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)), counts)
        self.assertIsNotNone(t.pending(self.bm))
        self.assertEqual(t.active(self.bm), edge)

    def test_strip_rejects_folded_projection_without_leaving_geometry(self):
        edge = self.bm.edges.new((self.v(-10, 0), self.v(10, 0)))
        t.select(self.bm, edge)
        with self.assertRaises(ValueError):
            t.quad_strip(self.bm, edge, [Vector((0, 20, 0))], lambda co: Vector((0, 0, 0)))
        self.assertEqual((len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)), (2, 1, 0))
        self.assertEqual(t.active(self.bm), edge)

    def test_strip_from_boundary_preserves_winding_and_material(self):
        face, verts = self.quad()
        face.material_index = 3
        edge = self.bm.edges.get((verts[2], verts[3]))
        faces = t.quad_strip(self.bm, edge, [Vector((1, 4, 0)), Vector((2, 6, 0))])
        self.bm.normal_update()
        self.assertTrue(all(f.normal.dot(face.normal) > 0.99 for f in faces))
        self.assertTrue(all(f.material_index == 3 for f in faces))
        self.assertEqual(len(edge.link_faces), 2)

    def test_strip_release_outside_reference_cancels_without_edits(self):
        context, stroke, edge = self.strip_setup()
        self.strip_modal(context, stroke, 'MOUSEMOVE', (0, 40))
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm), \
             patch.object(retopo_tool, 'project', return_value=None):
            event = self.strip_event('LEFTMOUSE', (0, 60), 'RELEASE')
            self.assertEqual(retopo_tool.RP_OT_click.modal(stroke, context, event), {'CANCELLED'})
        self.assertEqual((len(self.bm.verts), len(self.bm.edges), len(self.bm.faces)), (2, 1, 0))
        self.assertEqual(t.active(self.bm), edge)
        self.assertIsNone(retopo_tool._strip_preview)

    def test_strip_syncs_real_edit_mesh_and_continues_from_outer_edge(self):
        mesh = bpy.data.meshes.new('StripEdit')
        mesh.from_pydata([(-1, 0, 0), (1, 0, 0)], [(0, 1)], [])
        obj = bpy.data.objects.new('StripEdit', mesh)
        bpy.context.collection.objects.link(obj)
        original = bpy.context.view_layer.objects.active
        selected = list(bpy.context.selected_objects)
        try:
            for item in selected:
                item.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
            bm = bmesh.from_edit_mesh(mesh)
            edge = next(iter(bm.edges))
            t.quad_strip(bm, edge, [Vector((0, 2, 0)), Vector((0, 4, 0))])
            retopo_tool.sync(obj)
            bm = bmesh.from_edit_mesh(mesh)
            self.assertEqual(len(bm.faces), 2)
            t.quad_strip(bm, t.active(bm), [Vector((0, 6, 0))])
            retopo_tool.sync(obj)
            self.assertEqual(len(bmesh.from_edit_mesh(mesh).faces), 3)
            bpy.ops.object.mode_set(mode='OBJECT')
            self.assertEqual(len(mesh.polygons), 3)
        finally:
            if bpy.context.mode == 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            bpy.context.view_layer.objects.active = original
            for item in selected:
                item.select_set(True)

    def test_interior_edge_ctrl_drag_keeps_loop_cut_gesture(self):
        context, stroke, source = self.strip_setup()
        a, b = source.verts
        self.bm.faces.new((a, b, self.v(10, 20), self.v(-10, 20)))
        self.bm.faces.new((b, a, self.v(-10, -20), self.v(10, -20)))
        with patch.object(retopo_tool, 'pick', return_value=source), \
             patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=self.bm):
            op = SimpleNamespace()
            self.assertEqual(retopo_tool.RP_OT_click.invoke(op, context, self.strip_event('LEFTMOUSE', (0, 0), 'PRESS')), {'RUNNING_MODAL'})
        self.assertTrue(op.edge_gesture)
        self.assertFalse(getattr(op, 'strip_gesture', False))

    def test_brush_radius_falloff_and_hidden_vertices(self):
        self.front_view()
        center, middle, outside, hidden = [self.v(x, 0) for x in (0, 5, 11, 2)]
        hidden.hide_set(True)
        context = SimpleNamespace(region=None, region_data=None,
            edit_object=SimpleNamespace(matrix_world=Matrix.Identity(4)),
            scene=SimpleNamespace(retopo_tool=SimpleNamespace(brush_radius=10, target=None, snapping=True)))
        weights = dict(retopo_tool.brush_vertices(context, self.bm, Vector((0, 0))))
        self.assertAlmostEqual(weights[center], 1)
        self.assertAlmostEqual(weights[middle], 0.5)
        self.assertNotIn(outside, weights)
        self.assertNotIn(hidden, weights)

    def test_relax_uses_simultaneous_neighbor_positions(self):
        a, b, c = [self.v(x, 0) for x in (0, 1, 4)]
        self.bm.edges.new((a, b))
        self.bm.edges.new((b, c))
        positions = retopo_tool.relax_positions([(a, 1), (b, 1), (c, 0)])
        self.assertAlmostEqual(positions[a].x, 0.25)
        self.assertAlmostEqual(positions[b].x, 1.25)
        self.assertEqual(positions[c], c.co)
        self.assertEqual(b.co.x, 1)

    def test_relax_preserves_open_borders(self):
        grid = [[self.v(x, y) for x in range(3)] for y in range(3)]
        for y in range(2):
            for x in range(2):
                self.bm.faces.new((grid[y][x], grid[y][x+1], grid[y+1][x+1], grid[y+1][x]))
        grid[1][1].co.x = 1.5
        positions = retopo_tool.relax_positions([(v, 1) for v in self.bm.verts], True)
        self.assertEqual(set(positions), {grid[1][1]})
        self.assertLess(positions[grid[1][1]].x, 1.5)

    def test_brush_reacquires_vertices_after_mesh_rebuild(self):
        self.v(0, 0)
        brush = SimpleNamespace(obj=SimpleNamespace(data=None, matrix_world=Matrix.Identity(4)),
                                weighted=[(0, 1)], last=Vector((0, 0)), relaxing=False)
        rebuilt = self.bm.copy()
        self.bm.free()
        self.bm = rebuilt
        context = SimpleNamespace(region=None, region_data=None)
        with patch.object(retopo_tool.bmesh, 'from_edit_mesh', return_value=rebuilt), \
             patch.object(retopo_tool, 'reference_projector', return_value=None), \
             patch.object(retopo_tool, 'sync'), \
             patch.object(retopo_tool.view3d_utils, 'region_2d_to_location_3d',
                          side_effect=lambda r, d, xy, co: Vector((xy.x, xy.y, co.z))):
            retopo_tool.RP_OT_brush.apply(brush, context, Vector((2, 0)), False)
            retopo_tool.RP_OT_brush.apply(brush, context, Vector((3, 0)), False)
        self.assertAlmostEqual(rebuilt.verts[0].co.x, 3)

    def front_view(self):
        replacements = {
            'location_3d_to_region_2d': lambda r, d, co: Vector((co.x, co.y)),
            'region_2d_to_origin_3d': lambda r, d, co: Vector((co.x, co.y, 10)),
            'region_2d_to_vector_3d': lambda r, d, co: Vector((0, 0, -1)),
        }
        for name, replacement in replacements.items():
            patcher = patch.object(retopo_tool.view3d_utils, name, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_progressive_strip(self):
        a, b = self.v(0, 0), self.v(1, 0)
        edge = t.extend(self.bm, a, b)
        c = self.v(1, 1)
        t.extend(self.bm, edge, c)
        edge = t.active(self.bm)
        self.assertEqual(len(self.bm.faces), 1)
        self.assertIsNotNone(t.pending(self.bm))
        d = self.v(0, 1)
        t.extend(self.bm, edge, d)
        self.assertEqual(len(self.bm.faces), 1)
        self.assertEqual(len(self.bm.edges), 4)
        self.assertEqual(len(list(self.bm.faces)[0].verts), 4)
        self.assertIsNone(t.pending(self.bm))
        edge = t.active(self.bm)
        t.extend(self.bm, edge, self.v(0, 2))
        t.extend(self.bm, edge, self.v(1, 2))
        self.assertEqual(len(self.bm.faces), 2)
        self.bm.normal_update()
        self.assertGreater(list(self.bm.faces)[0].normal.dot(list(self.bm.faces)[1].normal), 0.99)

    def test_bridge_quad(self):
        a, b, c, d = self.v(0, 0), self.v(1, 0), self.v(1, 1), self.v(0, 1)
        e1 = self.bm.edges.new((a, b))
        e2 = self.bm.edges.new((c, d))
        face = t.bridge(self.bm, e1, e2)
        self.assertEqual(len(face.verts), 4)

    def test_bridge_triangle(self):
        a, b, c = self.v(0, 0), self.v(1, 0), self.v(1, 1)
        face = t.bridge(self.bm, self.bm.edges.new((a, b)), self.bm.edges.new((b, c)))
        self.assertEqual(len(face.verts), 3)

    def test_crossed_quad_preserves_triangle(self):
        a, b, c = self.v(0, 0), self.v(1, 0), self.v(1, 1)
        edge = self.bm.edges.new((a, b))
        t.extend(self.bm, edge, c)
        edge = t.active(self.bm)
        with self.assertRaises(ValueError):
            t.extend(self.bm, edge, self.v(2, 0))
        self.assertEqual(len(self.bm.faces), 1)
        self.assertIsNotNone(t.pending(self.bm))

    def test_pending_survives_mesh_roundtrip(self):
        a, b, c = self.v(0, 0), self.v(1, 0), self.v(1, 1)
        t.extend(self.bm, self.bm.edges.new((a, b)), c)
        mesh = bpy.data.meshes.new("Test")
        self.bm.to_mesh(mesh)
        restored = bmesh.new()
        restored.from_mesh(mesh)
        self.assertIsNotNone(t.pending(restored))
        restored.free()
        bpy.data.meshes.remove(mesh)

    def test_registration(self):
        retopo_tool.register()
        self.assertTrue(hasattr(bpy.context.scene, "retopo_tool"))
        retopo_tool.unregister()

    def test_reload_refreshes_cached_topology(self):
        cached = retopo_tool.topology
        del cached.apply_click
        importlib.reload(retopo_tool)
        self.assertIs(retopo_tool.topology, cached)
        self.assertTrue(callable(cached.apply_click))
        vertex = cached.apply_click(self.bm, None, None, Vector((0, 0, 0)))
        self.assertEqual(vertex.co, Vector((0, 0, 0)))

    def test_preview_matches_triangle_and_quad(self):
        a, b = self.v(0, 0), self.v(1, 0)
        edge = self.bm.edges.new((a, b))
        coords = retopo_tool.preview(self.bm, edge, None, Vector((1, 1, 0)))
        self.assertEqual(len(coords), 3)
        self.assertEqual(len(self.bm.faces), 0)
        self.assertEqual(len(self.bm.verts), 2)
        face = t.extend(self.bm, edge, self.v(1, 1))
        self.assertEqual(coords, [v.co for v in face.verts])
        edge = t.active(self.bm)
        coords = retopo_tool.preview(self.bm, edge, None, Vector((0, 1, 0)))
        self.assertEqual(len(coords), 4)
        self.assertIsNotNone(t.pending(self.bm))
        face = t.extend(self.bm, edge, self.v(0, 1))
        self.assertEqual(coords, [v.co for v in face.verts])

    def test_preview_bridge_and_invalid(self):
        a, b, c, d = self.v(0, 0), self.v(1, 0), self.v(1, 1), self.v(0, 1)
        source, target = self.bm.edges.new((a, b)), self.bm.edges.new((c, d))
        coords = retopo_tool.preview(self.bm, source, target, None, bridge_mode=True)
        self.assertEqual(len(coords), 4)
        self.assertEqual(len(self.bm.faces), 0)
        self.assertEqual(retopo_tool.preview(self.bm, source, source, None, bridge_mode=True), [])
        self.assertEqual(coords, [v.co for v in t.bridge(self.bm, source, target).verts])

    def test_activate_and_project(self):
        retopo_tool.register()
        try:
            source = bpy.context.active_object
            reference = source.copy()
            reference.data = source.data.copy()
            bpy.context.collection.objects.link(reference)
            bpy.context.scene.retopo_tool.target = bpy.context.collection
            count = len(bpy.data.objects)
            area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(area=area, region=region):
                self.assertEqual(bpy.ops.mesh.retopo_tool_activate(), {'FINISHED'})
                self.assertEqual(bpy.context.mode, 'EDIT_MESH')
                self.assertEqual(bpy.context.edit_object, source)
                self.assertEqual(len(bpy.data.objects), count)
                self.assertEqual(bpy.context.scene.retopo_tool.target, bpy.context.collection)
                self.assertEqual(len(bmesh.from_edit_mesh(bpy.context.edit_object.data).verts), 8)
                area.spaces.active.overlay.show_retopology = True
                self.assertTrue(area.spaces.active.overlay.show_retopology)
                # Background Blender has no GPU view matrix; supply a known view ray.
                with patch.object(retopo_tool.view3d_utils, 'region_2d_to_origin_3d', return_value=Vector((0, 0, 10))), patch.object(retopo_tool.view3d_utils, 'region_2d_to_vector_3d', return_value=Vector((0, 0, -1))):
                    point = retopo_tool.project(bpy.context, Vector((0, 0)), Vector((0, 0, 0)))
                self.assertIsNotNone(point)
                self.assertAlmostEqual(max(abs(value) for value in point), 1.001, places=3)
                obj = bpy.context.edit_object
                edit_bm = bmesh.from_edit_mesh(obj.data)
                vertex = edit_bm.verts.new((0, 0, 0))
                edit_bm.verts.index_update()
                vertex_index = vertex.index
                retopo_tool.sync(obj)
                def current_vertex():
                    bm = bmesh.from_edit_mesh(obj.data)
                    bm.verts.ensure_lookup_table()
                    return bm.verts[vertex_index].co.copy()
                stroke = SimpleNamespace(obj=obj, region=region, area=area, vertex_index=vertex_index,
                                         original=Vector((0, 0, 0)), start=Vector((10, 10)), dragging=False)
                event = SimpleNamespace(type='MOUSEMOVE', value='NOTHING', ctrl=False, shift=False,
                                        mouse_x=region.x + 30, mouse_y=region.y + 30)
                with patch.object(retopo_tool, 'project', return_value=Vector((0.5, 0.5, 1.001))):
                    self.assertEqual(retopo_tool.RP_OT_click.modal(stroke, bpy.context, event), {'RUNNING_MODAL'})
                self.assertTrue(stroke.dragging)
                self.assertAlmostEqual(current_vertex().x, 0.5)
                event.type, event.value = 'LEFTMOUSE', 'RELEASE'
                self.assertEqual(retopo_tool.RP_OT_click.modal(stroke, bpy.context, event), {'FINISHED'})
                self.assertAlmostEqual(current_vertex().x, 0.5)
                event.type, event.value = 'ESC', 'PRESS'
                self.assertEqual(retopo_tool.RP_OT_click.modal(stroke, bpy.context, event), {'FINISHED'})
                self.assertEqual(current_vertex(), Vector((0, 0, 0)))
                settings = bpy.context.scene.retopo_tool
                settings.mode = 'BOX'
                self.assertEqual(bpy.context.workspace.tools.from_space_view3d_mode('EDIT_MESH').idname, 'builtin.select_box')
                settings.mode = 'BRUSH'
                self.assertEqual(bpy.context.workspace.tools.from_space_view3d_mode('EDIT_MESH').idname, retopo_tool.RP_Tool.bl_idname)
                self.assertTrue(retopo_tool.RP_OT_brush.poll(bpy.context))
                self.assertFalse(retopo_tool.RP_OT_click.poll(bpy.context))
                bm = bmesh.from_edit_mesh(obj.data)
                bm.verts.ensure_lookup_table()
                vert = bm.verts[vertex_index]
                brush = SimpleNamespace(obj=obj, region=region, area=area,
                    originals=[v.co.copy() for v in bm.verts], counts=(len(bm.verts), len(bm.edges), len(bm.faces)))
                vert.co.x = 0.8
                self.assertEqual(retopo_tool.RP_OT_brush.modal(brush, bpy.context, event), {'CANCELLED'})
                self.assertEqual(current_vertex(), Vector((0, 0, 0)))
                settings.mode = 'RETOPO'
                self.assertEqual(bpy.ops.mesh.retopo_tool_stop(), {'FINISHED'})
                bpy.ops.object.mode_set(mode='OBJECT')
        finally:
            retopo_tool.unregister()
        retopo_tool.register()
        retopo_tool.unregister()

    def quad(self):
        verts = [self.v(0, 0), self.v(2, 0), self.v(2, 2), self.v(0, 2)]
        return self.bm.faces.new(verts), verts

    def test_subdivide_edge_at_cursor(self):
        face, verts = self.quad()
        edge = self.bm.edges.get((verts[0], verts[1]))
        coords = retopo_tool.preview(self.bm, edge, edge, None, edge_factor=0.25)
        self.assertEqual(len(self.bm.verts), 4)
        result = t.apply_click(self.bm, edge, edge, edge_factor=0.25)
        self.assertEqual(coords, [result.co])
        self.assertEqual(len(self.bm.verts), 5)
        self.assertEqual(len(face.verts), 5)
        self.assertEqual(t.active(self.bm), result)

    def test_cut_vertex_to_vertex(self):
        face, verts = self.quad()
        result = t.apply_click(self.bm, verts[0], verts[2])
        self.assertEqual(len(self.bm.faces), 2)
        self.assertTrue(all(len(f.verts) == 3 for f in self.bm.faces))
        self.assertEqual(t.active(self.bm), verts[2])
        self.assertTrue(result)

    def test_cut_edge_to_edge(self):
        face, verts = self.quad()
        start = t.apply_click(self.bm, None, self.bm.edges.get((verts[0], verts[1])))
        end_edge = self.bm.edges.get((verts[2], verts[3]))
        kind, coords = retopo_tool.preview(self.bm, start, end_edge, None, with_kind=True)
        self.assertEqual(kind, 'LINES')
        self.assertEqual(len(coords), 2)
        self.assertEqual(len(self.bm.verts), 5)
        t.apply_click(self.bm, start, end_edge)
        self.assertEqual(len(self.bm.verts), 6)
        self.assertEqual(len(self.bm.faces), 2)
        self.assertTrue(all(len(f.verts) == 4 for f in self.bm.faces))
        self.assertAlmostEqual(sum(f.calc_area() for f in self.bm.faces), 4)

    def test_cut_vertex_to_edge(self):
        face, verts = self.quad()
        t.apply_click(self.bm, verts[0], self.bm.edges.get((verts[1], verts[2])))
        self.assertEqual(len(self.bm.faces), 2)
        self.assertEqual(sorted(len(f.verts) for f in self.bm.faces), [3, 4])

    def test_cut_across_faces(self):
        a, b, c = self.v(0, 0), self.v(1, 0), self.v(2, 0)
        d, e, f = self.v(0, 1), self.v(1, 1), self.v(2, 1)
        self.bm.faces.new((a, b, e, d))
        self.bm.faces.new((b, c, f, e))
        start = t.apply_click(self.bm, None, self.bm.edges.get((a, d)))
        result = t.apply_click(self.bm, start, self.bm.edges.get((c, f)))
        self.assertEqual(len(result), 2)
        self.assertEqual(len(self.bm.faces), 4)
        self.assertAlmostEqual(sum(face.calc_area() for face in self.bm.faces), 2)

    def test_loop_cut_strip(self):
        bottom = [self.v(x, 0) for x in range(4)]
        top = [self.v(x, 1) for x in range(4)]
        for i in range(3):
            self.bm.faces.new((bottom[i], bottom[i + 1], top[i + 1], top[i]))
        edge = self.bm.edges.get((bottom[1], top[1]))
        expected = t.loop_preview(edge, 0.25)
        self.assertEqual(len(expected), 6)
        self.assertEqual(len(self.bm.verts), 8)
        cuts = t.loop_cut(self.bm, edge, 0.25)
        self.assertEqual(len(cuts), 3)
        self.assertEqual(len(self.bm.faces), 6)
        self.assertTrue(all(len(f.verts) == 4 for f in self.bm.faces))
        self.assertAlmostEqual(sum(f.calc_area() for f in self.bm.faces), 3)
        actual = [v.co for e in cuts for v in e.verts]
        self.assertTrue(all(any((p - q).length < 1e-6 for q in actual) for p in expected))

    def test_loop_cut_closed_ring(self):
        bmesh.ops.create_cube(self.bm, size=2)
        edge = next(iter(self.bm.edges))
        cuts = t.loop_cut(self.bm, edge, 0.3)
        self.assertEqual(len(cuts), 4)
        self.assertEqual(len(self.bm.faces), 10)
        self.assertTrue(all(e.is_manifold for e in self.bm.edges))

    def test_loop_cut_rejects_triangle(self):
        face = self.bm.faces.new((self.v(0, 0), self.v(1, 0), self.v(0, 1)))
        with self.assertRaises(ValueError):
            t.loop_cut(self.bm, face.edges[0], 0.5)
        self.assertEqual(len(self.bm.verts), 3)
        self.assertEqual(len(self.bm.faces), 1)

    def test_ngon_vertex_connection(self):
        verts = [self.v(0, 0), self.v(2, 0), self.v(3, 1), self.v(2, 2), self.v(0, 2), self.v(-1, 1)]
        self.bm.faces.new(verts)
        t.select(self.bm, verts[0])
        kind, coords = retopo_tool.preview(self.bm, t.active(self.bm), verts[3], None, with_kind=True)
        self.assertEqual(kind, 'LINES')
        self.assertEqual(len(coords), 2)
        self.assertEqual(len(self.bm.faces), 1)
        t.apply_click(self.bm, t.active(self.bm), verts[3])
        self.assertEqual(len(self.bm.faces), 2)
        self.assertTrue(all(len(f.verts) == 4 for f in self.bm.faces))
        self.assertEqual(len(self.bm.edges.get((verts[0], verts[3])).link_faces), 2)

    def test_loop_snaps_to_reference(self):
        self.front_view()
        reference_mesh = bpy.data.meshes.new('SnapReference')
        reference_mesh.from_pydata([(-5, -5, 2), (5, -5, 2), (5, 5, 2), (-5, 5, 2)], [], [(0, 1, 2, 3)])
        reference = bpy.data.objects.new('SnapReference', reference_mesh)
        bpy.context.collection.objects.link(reference)
        reference_collection = bpy.data.collections.new('SnapReferences')
        reference_collection.objects.link(reference)
        edit_mesh = bpy.data.meshes.new('SnapEdit')
        obj = bpy.data.objects.new('SnapEdit', edit_mesh)
        bpy.context.collection.objects.link(obj)
        try:
            obj.location.z = 0.5
            reference.scale = (2, 3, 1)
            bpy.context.view_layer.update()
            context = SimpleNamespace(edit_object=obj, region=None, region_data=None,
                scene=SimpleNamespace(retopo_tool=SimpleNamespace(target=reference_collection, offset=0.1, snapping=True)),
                evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get)
            snap = retopo_tool.reference_projector(context)
            face, verts = self.quad()
            original = [v.co.copy() for v in verts]
            edge = face.edges[0]
            predicted = t.loop_preview(edge, 0.4, snap)
            self.assertTrue(all(abs(co.z - 1.6) < 1e-5 for co in predicted))
            cuts = t.loop_cut(self.bm, edge, 0.4, snap)
            self.assertTrue(all(abs(v.co.z - 1.6) < 1e-5 for e in cuts for v in e.verts))
            self.assertEqual(original, [v.co for v in verts])
        finally:
            bpy.data.objects.remove(reference, do_unlink=True)
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.collections.remove(reference_collection)
            bpy.data.meshes.remove(reference_mesh)
            bpy.data.meshes.remove(edit_mesh)

    def test_selected_vertex_pair_bridges(self):
        a, b, c, d = self.v(0, 0), self.v(1, 0), self.v(1, 1), self.v(0, 1)
        source, target = self.bm.edges.new((a, b)), self.bm.edges.new((c, d))
        a.select_set(True)
        b.select_set(True)
        self.bm.select_history.add(b)
        self.assertEqual(t.active(self.bm), source)
        kind, coords = retopo_tool.preview(self.bm, t.active(self.bm), target, None, with_kind=True)
        self.assertEqual(kind, 'FACE')
        self.assertEqual(len(coords), 4)
        result = t.apply_click(self.bm, t.active(self.bm), target)
        self.assertEqual(len(self.bm.verts), 4)
        self.assertEqual(coords, [v.co for v in result.verts])

    def test_selected_edge_bridges_without_shift(self):
        a, b, c, d = self.v(0, 0), self.v(1, 0), self.v(1, 1), self.v(0, 1)
        source, target = self.bm.edges.new((a, b)), self.bm.edges.new((c, d))
        t.select(self.bm, source)
        result = t.apply_click(self.bm, t.active(self.bm), target)
        self.assertEqual(len(result.verts), 4)
        self.assertEqual(len(self.bm.verts), 4)

    def test_weld_joins_topology_at_target(self):
        a, source, target, b = self.v(0, 0), self.v(1, 0), self.v(1, 0.1), self.v(2, 0)
        self.bm.edges.new((a, source))
        self.bm.edges.new((target, b))
        original = target.co.copy()
        t.merge_vertices(self.bm, source, target)
        self.assertEqual(len(self.bm.verts), 3)
        self.assertEqual(target.co, original)
        self.assertEqual(len(target.link_edges), 2)
        self.assertEqual(t.active(self.bm), target)

    def test_weld_adjacent_face_vertices(self):
        face, verts = self.quad()
        t.merge_vertices(self.bm, verts[0], verts[1])
        self.assertEqual(len(self.bm.verts), 3)
        self.assertEqual(len(self.bm.faces), 1)
        self.assertEqual(len(next(iter(self.bm.faces)).verts), 3)

    def test_edge_drag_moves_both_endpoints(self):
        context = SimpleNamespace(edit_object=SimpleNamespace(matrix_world=Matrix.Diagonal((2, 3, 1, 1))),
                                  region=None, region_data=None)
        originals = [Vector((0, 0, 0)), Vector((1, 0, 0))]
        def view_position(region, data, mouse, depth):
            return Vector((mouse.x, mouse.y, depth.z))
        with patch.object(retopo_tool.view3d_utils, 'region_2d_to_location_3d', side_effect=view_position):
            positions = retopo_tool.edge_drag_positions(context, originals, Vector((10, 10)), Vector((14, 16)))
        self.assertEqual(positions, [Vector((2, 2, 0)), Vector((3, 2, 0))])
        self.assertEqual(originals, [Vector((0, 0, 0)), Vector((1, 0, 0))])

    def test_edge_drag_snaps_both_endpoints(self):
        context = SimpleNamespace(edit_object=SimpleNamespace(matrix_world=Matrix.Identity(4)),
                                  region=None, region_data=None)
        originals = [Vector((0, 0, 0)), Vector((1, 0, 0))]
        with patch.object(retopo_tool.view3d_utils, 'region_2d_to_location_3d', return_value=Vector((0, 0, 0))):
            positions = retopo_tool.edge_drag_positions(context, originals, Vector((0, 0)), Vector((1, 0)),
                                                       lambda co: Vector((co.x, co.y, 2)))
        self.assertEqual(positions, [Vector((0, 0, 2)), Vector((1, 0, 2))])

    def test_pick_rejects_far_side_vertex(self):
        self.front_view()
        bmesh.ops.create_cube(self.bm, size=2)
        back = self.bm.verts.new((0, 0, -1))
        front = self.bm.verts.new((0.1, 0, 1))
        context = SimpleNamespace(edit_object=SimpleNamespace(matrix_world=Matrix.Identity(4)),
            region=None, region_data=None,
            scene=SimpleNamespace(retopo_tool=SimpleNamespace(target=None, radius=14, offset=0.001)))
        result = retopo_tool.pick(context, self.bm, Vector((0, 0)), vertices_only=True)
        self.assertEqual(result, front)
        self.assertNotEqual(result, back)

    def test_visibility_blocks_hidden_surface(self):
        self.front_view()
        tree = retopo_tool.BVHTree.FromPolygons(
            [Vector((-2, -2, 1)), Vector((2, -2, 1)), Vector((2, 2, 1)), Vector((-2, 2, 1))], [(0, 1, 2, 3)])
        context = SimpleNamespace(region=None, region_data=None)
        self.assertTrue(retopo_tool.visible_from_view(context, Vector((0, 0, 1)), tree))
        self.assertFalse(retopo_tool.visible_from_view(context, Vector((0, 0, -1)), tree))

    def test_nearest_snap_rejects_backface(self):
        self.front_view()
        tree = retopo_tool.BVHTree.FromPolygons(
            [Vector((-2, -2, -1)), Vector((2, -2, -1)), Vector((2, 2, -1)), Vector((-2, 2, -1))], [(3, 2, 1, 0)])
        context = SimpleNamespace(region=None, region_data=None,
            edit_object=SimpleNamespace(matrix_world=Matrix.Identity(4)),
            scene=SimpleNamespace(retopo_tool=SimpleNamespace(target=object(), offset=0.001, snapping=True)))
        with patch.object(retopo_tool, 'reference_tree', return_value=tree):
            snap = retopo_tool.reference_projector(context)
        original = Vector((0, 0, -0.5))
        self.assertEqual(snap(original), original)

    def test_visibility_tree_reused_until_transform_changes(self):
        obj = SimpleNamespace(matrix_world=Matrix.Identity(4), data=object())
        builds = []
        def build():
            result = object()
            builds.append(result)
            return result
        first = retopo_tool.cached_tree('reference', obj, build)
        for _ in range(100):
            self.assertIs(retopo_tool.cached_tree('reference', obj, build), first)
        self.assertEqual(len(builds), 1)
        obj.matrix_world.translation.x = 1
        self.assertIsNot(retopo_tool.cached_tree('reference', obj, build), first)
        self.assertEqual(len(builds), 2)

    def test_visibility_invalidation_is_targeted(self):
        reference = SimpleNamespace(matrix_world=Matrix.Identity(4), data=object())
        edit = SimpleNamespace(matrix_world=Matrix.Identity(4), data=object())
        tree = retopo_tool.cached_tree('reference', reference, object)
        retopo_tool.cached_tree('edit', edit, object)
        update = SimpleNamespace(id=SimpleNamespace(original=edit.data), is_updated_geometry=True,
                                 is_updated_transform=False)
        retopo_tool.invalidate_visibility(depsgraph=SimpleNamespace(updates=[update]))
        self.assertIs(retopo_tool.cached_tree('reference', reference, object), tree)
        self.assertNotIn(('edit', retopo_tool.object_key(edit)), retopo_tool._visibility_cache)
        retopo_tool.invalidate_visibility()
        self.assertFalse(retopo_tool._visibility_cache)

    def test_reference_tolerance_allows_shallow_occlusion_only(self):
        self.front_view()
        tree = retopo_tool.BVHTree.FromPolygons(
            [Vector((-2, -2, 1)), Vector((2, -2, 1)), Vector((2, 2, 1)), Vector((-2, 2, 1))], [(0, 1, 2, 3)])
        settings = SimpleNamespace(offset=0.001, reference_tolerance=12)
        context = SimpleNamespace(region=None, region_data=None, scene=SimpleNamespace(retopo_tool=settings))
        with patch.object(retopo_tool.view3d_utils, 'region_2d_to_location_3d',
                          side_effect=lambda r, d, xy, co: Vector((xy.x * 0.01, xy.y * 0.01, co.z))):
            tolerance = retopo_tool.reference_pick_tolerance(context, Vector((0, 0, 0.95)))
        self.assertAlmostEqual(tolerance, 0.12)
        self.assertTrue(retopo_tool.visible_from_view(context, Vector((0, 0, 0.95)), tree, tolerance))
        self.assertFalse(retopo_tool.visible_from_view(context, Vector((0, 0, -1)), tree, tolerance))
        settings.reference_tolerance = 0
        self.assertAlmostEqual(retopo_tool.reference_pick_tolerance(context, Vector((0, 0, 0.95))), 0.002)

    def test_subdivision_preview_and_commit_snap(self):
        face, verts = self.quad()
        edge = face.edges[0]
        snap = lambda co: Vector((co.x, co.y, 0.25))
        expected = retopo_tool.preview(self.bm, None, edge, None, project=snap)
        result = t.apply_click(self.bm, None, edge, project=snap)
        self.assertEqual(expected, [result.co])
        self.assertAlmostEqual(result.co.z, 0.25)
        self.assertTrue(all(v.co.z == 0 for v in verts))

    def test_cut_snaps_intermediate_vertices(self):
        bottom = [self.v(x, 0) for x in range(3)]
        top = [self.v(x, 1) for x in range(3)]
        for i in range(2):
            self.bm.faces.new((bottom[i], bottom[i + 1], top[i + 1], top[i]))
        original = set(self.bm.verts)
        t.apply_click(self.bm, bottom[0], top[2], project=lambda co: Vector((co.x, co.y, 0.25)))
        inserted = [v for v in self.bm.verts if v not in original]
        self.assertTrue(inserted)
        self.assertTrue(all(abs(v.co.z - 0.25) < 1e-6 for v in inserted))
        self.assertTrue(all(v.co.z == 0 for v in original))


suite = unittest.defaultTestLoader.loadTestsFromTestCase(TopologyTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    sys.exit(1)

