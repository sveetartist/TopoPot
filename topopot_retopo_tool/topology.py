"""BMesh operations shared by the interactive tool and regression tests."""
import bmesh
from math import ceil, isfinite, sqrt
from statistics import median
from mathutils import Vector
from mathutils.kdtree import KDTree

PENDING = "retopo_tool_pending"
BASE = "retopo_tool_base"
WINDING_ERROR = "Target edge has incompatible face winding. Flip normals."
SYMMETRY_EPSILON = 1e-5
NO_EDIT_SIDES = (0, 0, 0)
LAST_EDIT_SIDE = "topopot_last_edit_side"
SYMMETRY_MATCH_ERROR = "The edited topology has no symmetry match. Use Resymmetrize first."
SYMMETRY_CROSSING_ERROR = "This edit overlaps the symmetry plane. Use Resymmetrize to create a center seam."


def mirrored_coordinate(co, axis=0):
    reflected = co.copy()
    reflected[axis] *= -1
    return reflected


def plane_side(value):
    return 1 if value > SYMMETRY_EPSILON else -1 if value < -SYMMETRY_EPSILON else 0


def coordinate_sides(co):
    return tuple(plane_side(value) for value in co)


def preferred_sides(*choices):
    return tuple(next((side for side in axis if side), 0) for axis in zip(*choices))


def center_coordinate(co, axis, distance):
    if 0 < abs(co[axis]) <= distance:
        co = co.copy()
        co[axis] = 0
    return co


def center_coordinates(co, axes, distance):
    if not distance:
        return co
    for axis in axes:
        co = center_coordinate(co, axis, distance)
    return co


def mirror_vertex_indices(coordinates, axis=0):
    """Pair matching vertices one-to-one, keeping seam vertices shared."""
    coordinates = list(coordinates.items())
    positive = [(index, co) for index, co in coordinates if plane_side(co[axis]) == 1]
    tree = KDTree(len(positive))
    for index, co in positive:
        tree.insert(co, index)
    tree.balance()
    pairs = {}
    for index, co in coordinates:
        side = plane_side(co[axis])
        if side == 0:
            pairs[index] = index
        elif side < 0:
            candidates = tree.find_range(mirrored_coordinate(co, axis), SYMMETRY_EPSILON)
            for _, partner, _ in sorted(candidates, key=lambda hit: (hit[2], hit[1])):
                if partner not in pairs:
                    pairs[index], pairs[partner] = partner, index
                    break
    return pairs


def mirrored_positions(positions, pairs, originals, side, axis=0):
    """Keep center vertices on the seam and apply each pair's movement once."""
    result = dict(positions)
    for index, co in positions.items():
        partner = pairs.get(index)
        if partner == index:
            result[index] = center_coordinate(co, axis, float('inf'))
        elif partner is not None:
            if partner not in positions or plane_side(originals[index][axis]) == (side or 1):
                result[partner] = mirrored_coordinate(co, axis)
    return result


def edit_side_state(bm):
    layer = bm.verts.layers.int.get(LAST_EDIT_SIDE)
    vertex = next(iter(bm.verts), None) if layer is not None else None
    return vertex[layer] if vertex is not None else 0


def last_edit_side(bm, axis=0):
    # A signed base-three digit per axis keeps existing X-only values valid.
    weight = 3 ** axis
    return ((edit_side_state(bm) + weight // 2) // weight + 1) % 3 - 1


def last_edit_sides(bm):
    state = edit_side_state(bm)
    return tuple(((state + weight // 2) // weight + 1) % 3 - 1 for weight in (1, 3, 9))


def restore_edit_side_state(bm, state):
    if not len(bm.verts) or edit_side_state(bm) == state:
        return
    # Edit-mode undo restores BMesh custom data alongside geometry.
    layer = bm.verts.layers.int.get(LAST_EDIT_SIDE)
    if layer is None:
        layer = bm.verts.layers.int.new(LAST_EDIT_SIDE)
    next(iter(bm.verts))[layer] = state


def record_edit_side(bm, side, axis=0):
    if side:
        previous = last_edit_side(bm, axis)
        restore_edit_side_state(bm, edit_side_state(bm) + (side - previous) * 3 ** axis)


def record_edit_sides(bm, sides):
    if any(sides):
        merged = preferred_sides(sides, last_edit_sides(bm))
        restore_edit_side_state(bm, sum(side * weight for side, weight in zip(merged, (1, 3, 9))))


def resymmetrize(bm, side, axis=0):
    if side not in {-1, 1} or not any(plane_side(v.co[axis]) == side for v in bm.verts):
        raise ValueError("The source side has no vertices away from the symmetry plane")
    state = edit_side_state(bm)
    clear_pending(bm)
    seam = {v for v in bm.verts if plane_side(v.co[axis]) == 0}
    direction = "XYZ"[axis]
    bmesh.ops.symmetrize(
        bm, input=[*bm.verts, *bm.edges, *bm.faces],
        direction=direction if side > 0 else '-' + direction, dist=SYMMETRY_EPSILON, use_shapekey=True)
    # Blender welds the cut boundary. Geometry lying entirely in the plane
    # can be duplicated without a cut, including points and loose edges.
    originals, duplicates = {}, {}
    for vertex in bm.verts:
        if plane_side(vertex.co[axis]) != 0:
            continue
        vertex.co[axis] = 0
        if all(plane_side(edge.other_vert(vertex).co[axis]) == 0 for edge in vertex.link_edges):
            group = originals if vertex in seam else duplicates
            group.setdefault(tuple(vertex.co), []).append(vertex)
    weld = {duplicate: original for key, verts in originals.items()
            for duplicate, original in zip(duplicates.get(key, ()), verts)}
    if weld:
        bmesh.ops.weld_verts(bm, targetmap=weld)
    restore_edit_side_state(bm, state)
    record_edit_side(bm, side, axis)


def resymmetrize_axes(bm, sources):
    """Rebuild the enabled halves from the region common to the source sides."""
    for axis, side in sources.items():
        available = {plane_side(v.co[axis]) for v in bm.verts} - {0}
        if available:
            if side not in available:
                raise ValueError(f"The source sides have no shared geometry on {'XYZ'[axis]}. Choose another source side.")
            resymmetrize(bm, side, axis)


def snap_new_vertices(bm, previous, axes, distance):
    moved = []
    for vertex in bm.verts:
        if vertex not in previous:
            snapped = center_coordinates(vertex.co, axes, distance)
            if snapped != vertex.co:
                vertex.co = snapped
                moved.append(vertex)
    if (any(edge.calc_length() <= SYMMETRY_EPSILON for v in moved for edge in v.link_edges)
            or any(face.calc_area() <= SYMMETRY_EPSILON ** 2 for v in moved for face in v.link_faces)):
        raise ValueError("Snap to Center would collapse the edited geometry")


def _element_keys(bm, indices):
    edges = {tuple(sorted(indices[v] for v in edge.verts)): edge for edge in bm.edges}
    faces = {tuple(sorted(indices[v] for v in face.verts)): face for face in bm.faces}
    return edges, faces


def _mirror_key(key, pairs):
    try:
        return tuple(sorted(pairs[index] for index in key))
    except KeyError:
        raise ValueError(SYMMETRY_MATCH_ERROR) from None


def mirror_edit(bm, edit, pairs=None, side=0, axis=0):
    """Apply a topology edit and reflect its changes, keeping existing opposite-side data."""
    before = {v: i for i, v in enumerate(bm.verts)}
    coordinates = {i: v.co.copy() for v, i in before.items()}
    pairs = mirror_vertex_indices(coordinates, axis) if pairs is None else dict(pairs)
    old_edges, old_faces = _element_keys(bm, before)

    result = edit()
    indices, new_verts = {}, {}
    new_index = len(before)
    for vertex in bm.verts:
        index = before.get(vertex)
        if index is None:
            index = new_index
            new_index += 1
            new_verts[index] = vertex
        indices[vertex] = index
    edges, faces = _element_keys(bm, indices)
    vertices = {index: vertex for vertex, index in indices.items()}
    removed = coordinates.keys() - vertices.keys()
    changed = {i for i in coordinates.keys() & vertices.keys() if coordinates[i] != vertices[i].co}
    if not (new_verts or removed or changed or edges.keys() != old_edges.keys() or faces.keys() != old_faces.keys()):
        return result

    pairs.update(mirror_vertex_indices({i: v.co for i, v in new_verts.items()}, axis))
    clones = {}
    for index, vertex in new_verts.items():
        if index not in pairs:
            pairs[index], pairs[new_index] = new_index, index
            clones[new_index] = vertex
            new_index += 1
        elif pairs[index] == index:
            vertex.co[axis] = 0

    def reflected_changes(original, current, faces=False):
        delete, add = set(), {}
        for key in original.keys() - current.keys():
            partner = _mirror_key(key, pairs)
            if partner not in original:
                raise ValueError(SYMMETRY_MATCH_ERROR)
            if partner in current:
                delete.add(current[partner])
        sides = set()
        for key in current.keys() - original.keys():
            partner = _mirror_key(key, pairs)
            half = {plane_side(vertices[i].co[axis]) for i in key} - {0}
            if len(half) == 1:
                sides.update(half)
            if partner not in current:
                if half == {-1, 1}:
                    raise ValueError(SYMMETRY_CROSSING_ERROR)
                add[partner] = current[key]
        if faces and add and sides == {-1, 1}:
            raise ValueError(SYMMETRY_CROSSING_ERROR)
        return delete, add

    deleted_faces, added_faces = reflected_changes(old_faces, faces, faces=True)
    deleted_edges, added_edges = reflected_changes(old_edges, edges)
    deleted_verts = {vertices[partner] for i in removed
                     for partner in _mirror_key((i,), pairs) if partner in vertices}
    if (any(face not in deleted_faces for edge in deleted_edges for face in edge.link_faces)
            or any(edge not in deleted_edges for vertex in deleted_verts for edge in vertex.link_edges)
            or any(vertices.get(i) in deleted_verts for key in (*added_edges, *added_faces) for i in key)):
        raise ValueError(SYMMETRY_CROSSING_ERROR)

    for index in changed:
        partner = _mirror_key((index,), pairs)[0]
        if partner == index:
            vertices[index].co[axis] = 0
        elif partner not in changed or plane_side(coordinates[index][axis]) == side:
            if partner in vertices and vertices[partner] not in deleted_verts:
                vertices[partner].co = mirrored_coordinate(vertices[index].co, axis)

    for face in deleted_faces:
        bm.faces.remove(face)
    for edge in deleted_edges:
        bm.edges.remove(edge)
    for vertex in deleted_verts:
        bm.verts.remove(vertex)
    for index, template in clones.items():
        vertex = bm.verts.new(mirrored_coordinate(template.co, axis), template)
        vertex.select = False
        for layer in bm.verts.layers.shape.values():
            vertex[layer] = mirrored_coordinate(template[layer], axis)
        vertices[index] = vertex
    for key, template in added_edges.items():
        edge = bm.edges.new(tuple(vertices[i] for i in key), template)
        edge.select = False
        edge.seam, edge.smooth = template.seam, template.smooth
    for template in added_faces.values():
        opposite = [vertices[pairs[indices[v]]] for v in reversed(list(template.verts))]
        face = bm.faces.new(opposite, template)
        face.select = False
        face.smooth, face.material_index = template.smooth, template.material_index
        corners = {vertices[pairs[indices[loop.vert]]]: loop for loop in template.loops}
        for loop in face.loops:
            loop.copy_from(corners[loop.vert])
        if template.hide:
            face.hide_set(True)
    for key, template in added_edges.items():
        if template.hide:
            bm.edges.get(tuple(vertices[i] for i in key)).hide_set(True)
    for index, template in clones.items():
        if template.hide:
            vertices[index].hide_set(True)
    for items, layer_name in ((bm.edges, BASE), (bm.faces, PENDING)):
        layer = items.layers.int.get(layer_name)
        if layer is not None:
            for item in list(items):
                if item[layer] and all(v in indices and indices[v] in pairs for v in item.verts):
                    opposite = [vertices.get(pairs[indices[v]]) for v in item.verts]
                    if all(v is not None and v.is_valid for v in opposite):
                        partner = items.get(opposite)
                        if partner is not None:
                            partner[layer] = 1
    return result


def boundary_loops(bm):
    """Find closed, unbranched rims on visible manifold surface geometry."""
    edges = {e for e in bm.edges if len(e.link_faces) == 1 and not e.hide
             and not e.link_faces[0].hide and all(not v.hide for v in e.verts)}
    neighbors = {}
    for edge in edges:
        for vertex in edge.verts:
            neighbors.setdefault(vertex, []).append(edge)
    remaining = set(edges)
    loops = []
    for seed in bm.edges:
        if seed not in remaining:
            continue
        component = set()
        queue = [seed]
        while queue:
            edge = queue.pop()
            if edge in component:
                continue
            component.add(edge)
            queue.extend(e for v in edge.verts for e in neighbors[v] if e not in component)
        remaining.difference_update(component)
        verts = {v for e in component for v in e.verts}
        if (len(verts) < 3 or any(len(neighbors[v]) != 2 for v in verts)
                or any(len(e.link_faces) > 2 for v in verts for e in v.link_edges)):
            continue
        # Traverse opposite to the surrounding face so the fill shares its winding.
        face_loop = next(loop for loop in seed.link_faces[0].loops if loop.edge == seed)
        start, vertex = face_loop.link_loop_next.vert, face_loop.vert
        ordered = [start]
        previous = seed
        while vertex != start:
            ordered.append(vertex)
            following = next(e for e in neighbors[vertex] if e != previous)
            vertex, previous = following.other_vert(vertex), following
        loops.append(ordered)
    return loops


def rim_sides(coords):
    """Split a rim at its four sharpest corners, including both side endpoints."""
    count = len(coords)
    if count < 3:
        return []
    turns = []
    for i, co in enumerate(coords):
        incoming = (co - coords[i - 1]).normalized()
        outgoing = (coords[(i + 1) % count] - co).normalized()
        turns.append(round(1.0 - incoming.dot(outgoing), 4))
    corners = []
    # Spread equally sharp corners around smooth rims instead of bunching them.
    for _ in range(min(4, count)):
        def score(i):
            separation = min((min((i - j) % count, (j - i) % count)
                              for j in corners), default=count)
            return turns[i], separation, -i
        corners.append(max((i for i in range(count) if i not in corners), key=score))
    corners.sort()
    return [[coords[i % count] for i in range(start, start + (end - start) % count + 1)]
            for start, end in zip(corners, corners[1:] + corners[:1])]


def _rim_normal(vertices):
    origin = vertices[0].co
    return sum(((a.co - origin).cross(b.co - origin)
                for a, b in zip(vertices, vertices[1:] + vertices[:1])), Vector())


def _grid_faces(bm, vertices, edges, material, smooth, balanced=False, originals=None):
    count = len(vertices)
    # Pick four corners with opposite sides of equal length. Prefer sharp rim
    # corners, which gives rectangular holes a regular grid instead of a fan.
    turns = []
    for i, vertex in enumerate(vertices):
        incoming = (vertex.co - vertices[i - 1].co).normalized()
        outgoing = (vertices[(i + 1) % count].co - vertex.co).normalized()
        turns.append(1.0 - incoming.dot(outgoing))
    half = count // 2
    spans = (max(1, count // 4),) if balanced else range(1, half)
    candidates = sorted(((offset, span) for offset in range(count) for span in spans),
                        key=lambda pair: (round(sum(turns[(pair[0] + step) % count]
                                                    for step in (0, pair[1], half, half + pair[1])), 4),
                                          -abs(pair[1] - count / 4), -pair[0]), reverse=True)
    if originals is None:
        originals = set(bm.verts), set(bm.edges), set(bm.faces)
    seen, layouts, first_error = set(), 0, None
    for offset, span in candidates:
        corners = tuple(sorted((offset + step) % count for step in (0, span, half, half + span)))
        if corners in seen:
            continue
        seen.add(corners)
        layouts += 1
        if layouts > 12:
            break
        arcs = [edges[(offset + step) % count] for step in range(span)]
        arcs += [edges[(offset + half + step) % count] for step in range(span)]
        for simple in (False, True):
            try:
                faces = bmesh.ops.grid_fill(bm, edges=arcs, mat_nr=material, use_smooth=smooth,
                                            use_interp_simple=simple)['faces']
                _validate_fill(bm, vertices, faces)
                return faces
            except (ValueError, RuntimeError) as error:
                first_error = first_error or error
                _remove_fill(bm, originals)
    raise first_error or ValueError("This hole cannot form a valid quad grid")


def _remove_fill(bm, originals):
    original_verts, original_edges, original_faces = originals
    for face in list(bm.faces):
        if face not in original_faces:
            bm.faces.remove(face)
    for edge in list(bm.edges):
        if edge not in original_edges and not edge.link_faces:
            bm.edges.remove(edge)
    for vertex in list(bm.verts):
        if vertex not in original_verts and not vertex.link_edges:
            bm.verts.remove(vertex)


def remove_preview_fill(bm, faces, originals):
    """Restore a shared preview BMesh, including any unused generated geometry."""
    original_verts, original_edges, original_faces = originals
    edges = {edge for face in faces for edge in face.edges if edge not in original_edges}
    verts = {vertex for face in faces for vertex in face.verts if vertex not in original_verts}
    for face in faces:
        bm.faces.remove(face)
    for edge in edges:
        if not edge.link_faces:
            bm.edges.remove(edge)
    for vertex in verts:
        if not vertex.link_edges:
            bm.verts.remove(vertex)
    if (len(bm.verts), len(bm.edges), len(bm.faces)) != tuple(map(len, originals)):
        _remove_fill(bm, originals)


def _validate_fill(bm, vertices, faces):
    """Reject folds and grid layouts which extend beyond a concave rim."""
    if not faces or any(len(f.verts) not in {3, 4} for f in faces):
        raise ValueError("This hole cannot form a valid fill")
    if any(not isfinite(value) for face in faces for vertex in face.verts for value in vertex.co):
        raise ValueError("This grid has degenerate coordinates")
    normal = _rim_normal(vertices)
    axis = max(range(3), key=lambda index: abs(normal[index]))
    axes = [index for index in range(3) if index != axis]
    origin = vertices[0].co
    def projected(vertex):
        relative = vertex.co - origin
        return Vector((relative[axes[0]], relative[axes[1]]))
    def cross(a, b):
        return a.x * b.y - a.y * b.x
    polygon = [projected(v) for v in vertices]
    for i, (a, b) in enumerate(zip(polygon, polygon[1:] + polygon[:1])):
        for j in range(i + 2, len(polygon)):
            if i == 0 and j == len(polygon) - 1:
                continue
            c, d = polygon[j], polygon[(j + 1) % len(polygon)]
            if (cross(b - a, c - a) * cross(b - a, d - a) < -1e-14
                    and cross(d - c, a - c) * cross(d - c, b - c) < -1e-14):
                raise ValueError("The boundary intersects or folds in its local projection")
    area = abs(sum(cross(a, b) for a, b in zip(polygon, polygon[1:] + polygon[:1])))
    if area < 1e-10:
        raise ValueError("The hole is too narrow or degenerate")
    filled_area = 0
    for face in faces:
        check_face(bm, list(face.verts), replacing=face)
        coords = [projected(v) for v in face.verts]
        for i in range(1, len(coords) - 1):
            a, b, c = coords[0], coords[i], coords[i + 1]
            filled_area += abs(cross(b - a, c - a))
    if abs(filled_area - area) > max(1e-8, area * 1e-4):
        raise ValueError("This grid folds or extends outside the hole")


def _fill_coordinates(vertices):
    normal = _rim_normal(vertices)
    axis = max(range(3), key=lambda index: abs(normal[index]))
    axes = [index for index in range(3) if index != axis]
    origin = vertices[0].co
    coords = [((v.co - origin)[axes[0]], (v.co - origin)[axes[1]]) for v in vertices]
    area = sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(coords, coords[1:] + coords[:1]))
    return coords, 1 if area > 0 else -1


def _turn(a, b, c):
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def _face_quality(vertices, coords, orientation, indices):
    """Reject concave/sliver quads; rank the remaining shapes by corner quality."""
    points = [coords[index] for index in indices]
    lengths = [(b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
               for a, b in zip(points, points[1:] + points[:1])]
    if any(not isfinite(length) for length in lengths) or min(lengths) < 1e-16:
        return None
    turns = [_turn(points[i - 1], point, points[(i + 1) % len(points)]) * orientation
             for i, point in enumerate(points)]
    if min(turns) <= max(lengths) * 1e-8:
        return None
    if len(indices) == 4:
        # About seven degrees minimum corner angle and an 8:1 edge limit.
        # A triangle is preferable to forcing a nearly flat or very thin quad.
        sines = [turn / (lengths[i - 1] * lengths[i]) ** 0.5 for i, turn in enumerate(turns)]
        if min(sines) < 0.12 or max(lengths) > min(lengths) * 64:
            return None
        a, b, c, d = (vertices[index].co for index in indices)
        first, second = (b - a).cross(c - a), (c - a).cross(d - a)
        if first.length < 1e-10 or second.length < 1e-10 or first.normalized().dot(second.normalized()) < 0.7:
            return None
        return sum(1 / sine - 1 for sine in sines) + max(lengths) / min(lengths) - 1
    return sum(lengths) / sum(turns)


def _quad_dominant_layout(bm, vertices, balance_areas=False):
    """Search polygon partitions, minimizing triangles before optimizing shape.

    Unlike joining a fixed triangulation, this can change diagonals to avoid
    stranding triangles. Bound the quartic search to keep large-hole hover fast.
    """
    count = len(vertices)
    if count > 48:
        return None
    coords, orientation = _fill_coordinates(vertices)
    area = abs(sum(a[0]*b[1] - a[1]*b[0] for a, b in zip(coords, coords[1:] + coords[:1]))) / 2
    target_area = area / max(1, (count-1)//2)
    scale = max((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
                for a, b in zip(coords, coords[1:] + coords[:1]))
    epsilon = max(scale * 1e-8, 1e-12)

    def inside(point):
        result = False
        for a, b in zip(coords, coords[1:] + coords[:1]):
            if (a[1] > point[1]) != (b[1] > point[1]):
                x = a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if x > point[0]:
                    result = not result
        return result

    def diagonal(i, j):
        if j == i + 1 or (i == 0 and j == count - 1):
            return True
        edge = bm.edges.get((vertices[i], vertices[j]))
        if edge is not None and (edge.hide or edge.link_faces):
            return False
        a, b = coords[i], coords[j]
        # Exclude crossings, overlapping boundary segments and skipped rim
        # vertices, so every original rim segment is used exactly once.
        for k, c in enumerate(coords):
            if k not in (i, j) and abs(_turn(a, b, c)) <= epsilon:
                if (c[0] - a[0]) * (c[0] - b[0]) + (c[1] - a[1]) * (c[1] - b[1]) <= epsilon:
                    return False
            following = (k + 1) % count
            if i in (k, following) or j in (k, following):
                continue
            d = coords[following]
            if _turn(a, b, c) * _turn(a, b, d) < 0 and _turn(c, d, a) * _turn(c, d, b) < 0:
                return False
        return inside(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))

    allowed = {(i, j): diagonal(i, j) for i in range(count) for j in range(i + 1, count)}
    # Each record stores (triangle count, shape cost), its face and subintervals.
    plans = {(i, i + 1): ((0, 0.0), (), ()) for i in range(count - 1)}
    for size in range(2, count):
        for i in range(count - size):
            j = i + size
            if not allowed[i, j]:
                continue
            best = None

            def consider(indices, children):
                nonlocal best
                if any(child not in plans for child in children):
                    return
                quality = _face_quality(vertices, coords, orientation, indices)
                if quality is None:
                    return
                if balance_areas:
                    points = [coords[index] for index in indices]
                    face_area = abs(sum(a[0]*b[1] - a[1]*b[0]
                                        for a, b in zip(points, points[1:] + points[:1]))) / 2
                    quality = 3 * quality + (face_area / max(target_area, 1e-16) - 1) ** 2
                score = (int(len(indices) == 3) + sum(plans[child][0][0] for child in children),
                         quality + sum(plans[child][0][1] for child in children))
                if best is None or score < best[0]:
                    best = score, indices, children

            for k in range(i + 1, j):
                consider((i, k, j), ((i, k), (k, j)))
                for l in range(k + 1, j):
                    consider((i, k, l, j), ((i, k), (k, l), (l, j)))
            if best is not None:
                plans[i, j] = best
    if (0, count - 1) not in plans:
        return None
    layout = []
    queue = [(0, count - 1)]
    while queue:
        _, indices, children = plans[queue.pop()]
        if indices:
            layout.append([vertices[index] for index in indices])
            queue.extend(reversed(children))
    return layout


def _pair_fill_triangles(bm, vertices, faces, normal=None, flow=None):
    """Match compatible triangle pairs across a triangulated region."""
    if not faces:
        return
    if normal is None:
        coords, orientation = _fill_coordinates(vertices)
    else:
        axis = max(range(3), key=lambda i: abs(normal[i]))
        axes = [i for i in range(3) if i != axis]
        coords = [(v.co[axes[0]], v.co[axes[1]]) for v in vertices]
        points = [(v.co[axes[0]], v.co[axes[1]]) for v in faces[0].verts]
        orientation = 1 if _turn(*points[:3]) > 0 else -1
    indices = {v: i for i, v in enumerate(vertices)}
    adjacency = {face: [] for face in faces}
    face_set = set(faces)
    for edge in list(bm.edges):
        if len(edge.link_faces) != 2 or any(face not in face_set for face in edge.link_faces):
            continue
        first, second = edge.link_faces
        following = {loop.vert: loop.link_loop_next.vert for face in (first, second)
                     for loop in face.loops if loop.edge != edge}
        if len(following) != 4:
            continue
        start = min(following, key=indices.get)
        corners = [start]
        for _ in range(3):
            corners.append(following[corners[-1]])
        quality = _face_quality(vertices, coords, orientation, [indices[v] for v in corners])
        if quality is None:
            continue
        if flow is not None:
            # Prefer removing expensive diagonals and pairing poor triangles,
            # rather than leaving the worst triangle behind to gain a quad.
            quality -= 2 * flow(*edge.verts)
            quality -= sum(_face_quality(vertices, coords, orientation, [indices[v] for v in face.verts])
                           for face in (first, second))
        pair = first, second, edge, corners
        adjacency[first].append((second, quality, pair))
        adjacency[second].append((first, quality, pair))

    visited, selected = set(), []
    for root in faces:
        if root in visited:
            continue
        # Iterative postorder also handles very large rims without recursion.
        parent, order = {root: None}, [root]
        for face in order:
            visited.add(face)
            for other, _, _ in adjacency[face]:
                if other not in parent:
                    parent[other] = face
                    order.append(other)
        states = {}
        for face in reversed(order):
            children = [(other, cost, pair) for other, cost, pair in adjacency[face] if parent.get(other) == face]
            base_score = (sum(states[child][0][0][0] for child, _, _ in children),
                          sum(states[child][0][0][1] for child, _, _ in children))
            base_pairs = [pair for child, _, _ in children for pair in states[child][0][1]]
            free = blocked = base_score, base_pairs
            for child, cost, pair in children:
                free_child, blocked_child = states[child]
                score = (base_score[0] - free_child[0][0] + blocked_child[0][0] + 1,
                         base_score[1] - free_child[0][1] + blocked_child[0][1] - cost)
                if score > free[0]:
                    pairs = [item for other, _, _ in children if other != child for item in states[other][0][1]]
                    free = score, pairs + blocked_child[1] + [pair]
            states[face] = free, blocked
        selected.extend(states[root][0][1])
    for first, second, edge, corners in selected:
        material, smooth = first.material_index, first.smooth
        bm.faces.remove(first)
        bm.faces.remove(second)
        bm.edges.remove(edge)
        quad = bm.faces.new(corners)
        quad.material_index, quad.smooth = material, smooth


def _poor_quads(faces):
    result = []
    for face in faces:
        corners = list(face.verts)
        if len(corners) == 4:
            coords, orientation = _fill_coordinates(corners)
            if _face_quality(corners, coords, orientation, range(4)) is None:
                result.append(face)
    return result


def _align_band_triangles(bm, faces, rim, axis_x, axis_y, spacing):
    """Replace long/skew band diagonals with local connections along grid rows."""
    face_set = set(faces)
    vertices = {v for face in faces for v in face.verts}
    rim_set = set(rim)
    origin = rim[0].co
    coords = {v: Vector(((v.co - origin).dot(axis_x), (v.co - origin).dot(axis_y))) for v in vertices}
    inner = vertices - rim_set
    nearest = {v: min((coords[v] - coords[other]).length_squared for other in inner)
               for v in rim_set if v in coords}
    scale = spacing * spacing
    epsilon = scale * 1e-8

    def edge_cost(a, b):
        delta = coords[b] - coords[a]
        # Keep distance dominant. Straight grid rows win over skew diagonals;
        # a small horizontal preference breaks otherwise equivalent choices.
        cost = (delta.length_squared + 2 * min(delta.x ** 2, delta.y ** 2)
                + 0.1 * delta.y ** 2) / scale
        if (a in rim_set) != (b in rim_set):
            outer = a if a in rim_set else b
            cost += delta.length_squared / max(nearest[outer], epsilon)
        return cost

    def triangle_cost(corners):
        points = [coords[v] for v in corners]
        area = abs(_turn(*points))
        if area <= epsilon:
            return float('inf')
        return sum((b - a).length_squared for a, b in zip(points, points[1:] + points[:1])) / area

    for _ in range(8):
        changed = False
        for edge in list(bm.edges):
            if not edge.is_valid or len(edge.link_faces) != 2:
                continue
            first, second = edge.link_faces
            if first not in face_set or second not in face_set:
                continue
            loop = next(loop for loop in first.loops if loop.edge == edge)
            a, b = loop.vert, loop.link_loop_next.vert
            c = next(v for v in first.verts if v not in edge.verts)
            d = next(v for v in second.verts if v not in edge.verts)
            if c == d or bm.edges.get((c, d)) is not None:
                continue
            corners = [coords[v] for v in (c, a, d, b)]
            turns = [_turn(corners[i - 1], point, corners[(i + 1) % 4])
                     for i, point in enumerate(corners)]
            if not (min(turns) > epsilon or max(turns) < -epsilon):
                continue
            old = edge_cost(a, b) + 0.2 * (triangle_cost((a, b, c)) + triangle_cost((b, a, d)))
            new = edge_cost(c, d) + 0.2 * (triangle_cost((c, d, b)) + triangle_cost((d, c, a)))
            if new >= old - 1e-5:
                continue
            material, smooth = first.material_index, first.smooth
            face_set.difference_update((first, second))
            bm.faces.remove(first)
            bm.faces.remove(second)
            bm.edges.remove(edge)
            for verts in ((c, d, b), (d, c, a)):
                face = bm.faces.new(verts)
                face.material_index, face.smooth = material, smooth
                face_set.add(face)
            changed = True
        if not changed:
            break
    return [face for face in bm.faces if face in face_set], edge_cost


def _square_grid_faces(bm, vertices, edges, material, smooth):
    """Fill the interior with square cells and stitch the remaining rim band."""
    origin = sum((v.co for v in vertices), Vector()) / len(vertices)
    normal = _rim_normal(vertices)
    if normal.length < 1e-10:
        return None
    normal.normalize()
    directions = [b.co - a.co for a, b in zip(vertices, vertices[1:] + vertices[:1])]
    direction = max(directions, key=lambda delta: delta.length_squared)
    axis_x = (direction - normal * direction.dot(normal)).normalized()
    axis_y = normal.cross(axis_x)
    polygon = [Vector(((v.co - origin).dot(axis_x), (v.co - origin).dot(axis_y))) for v in vertices]
    low = Vector((min(p.x for p in polygon), min(p.y for p in polygon)))
    high = Vector((max(p.x for p in polygon), max(p.y for p in polygon)))
    extent = high - low
    lengths = [(b - a).length for a, b in zip(polygon, polygon[1:] + polygon[:1])]
    # Match typical rim spacing, with a bounded lattice for responsive hover.
    spacing = max(median(lengths), extent.x / 128, extent.y / 128,
                  sqrt(extent.x * extent.y / 4096))
    if spacing < 1e-8:
        return None
    columns, rows = ceil(extent.x / spacing), ceil(extent.y / spacing)
    start = (low + high) / 2 - Vector((columns, rows)) * (spacing / 2)
    epsilon = spacing * spacing * 1e-8

    def inside(point):
        result = False
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            if (a.y > point.y) != (b.y > point.y):
                crossing = a.x + (point.y - a.y) * (b.x - a.x) / (b.y - a.y)
                if point.x < crossing:
                    result = not result
        return result

    def clearance(point):
        distances = []
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            delta = b - a
            factor = max(0, min(1, (point - a).dot(delta) / max(delta.length_squared, epsilon)))
            distances.append((point - a - factor * delta).length)
        return min(distances)

    cells = set()
    for x in range(columns):
        for y in range(rows):
            lower = start + Vector((x, y)) * spacing
            upper = lower + Vector((spacing, spacing))
            corners = [lower, Vector((upper.x, lower.y)), upper, Vector((lower.x, upper.y))]
            if any(not inside(p) or clearance(p) < spacing * 0.15 for p in corners):
                continue
            # Corner tests alone miss narrow concave notches crossing a cell.
            if any(lower.x <= p.x <= upper.x and lower.y <= p.y <= upper.y for p in polygon):
                continue
            if any(_turn(a, b, c) * _turn(a, b, d) < -epsilon * epsilon
                   and _turn(c, d, a) * _turn(c, d, b) < -epsilon * epsilon
                   for a, b in zip(polygon, polygon[1:] + polygon[:1])
                   for c, d in zip(corners, corners[1:] + corners[:1])):
                continue
            cells.add((x, y))
    # Two cells touching only at a corner would create a branched inner rim.
    for x in range(columns + 1):
        for y in range(rows + 1):
            neighbors = cells.intersection({(x - 1, y - 1), (x, y - 1), (x, y), (x - 1, y)})
            if len(neighbors) == 2:
                first, second = sorted(neighbors)
                if first[0] != second[0] and first[1] != second[1]:
                    cells.remove(second)
    if not cells:
        return None
    originals = set(bm.verts), set(bm.edges), set(bm.faces)
    grid_vertices = {}
    try:
        def vertex(x, y):
            key = x, y
            if key not in grid_vertices:
                point = start + Vector(key) * spacing
                grid_vertices[key] = bm.verts.new(origin + axis_x * point.x + axis_y * point.y)
            return grid_vertices[key]

        for x, y in sorted(cells):
            face = bm.faces.new((vertex(x, y), vertex(x + 1, y), vertex(x + 1, y + 1), vertex(x, y + 1)))
            face.material_index, face.smooth = material, smooth
        inner_edges = [edge for edge in bm.edges if edge not in originals[1] and len(edge.link_faces) == 1]
        result = bmesh.ops.triangle_fill(bm, edges=edges + inner_edges, normal=normal, use_beauty=True)
        band = [item for item in result['geom'] if isinstance(item, bmesh.types.BMFace)]
        for face in band:
            face.material_index, face.smooth = material, smooth
        band, flow = _align_band_triangles(bm, band, vertices, axis_x, axis_y, spacing)
        _pair_fill_triangles(bm, vertices + list(grid_vertices.values()), band, normal, flow)
        faces = [face for face in bm.faces if face not in originals[2]]
        _validate_fill(bm, vertices, faces)
        fill_faces = set(faces)
        if (any(len(edge.link_faces) != 2 for edge in inner_edges)
                or any(sum(face in fill_faces for face in edge.link_faces) != 1 for edge in edges)):
            raise ValueError("The interior grid does not connect to the whole rim")
        return faces
    except (ValueError, RuntimeError):
        _remove_fill(bm, originals)
        return None


def _square_shaped(faces, edge_ratio=1.5, corner_cosine=0.5):
    """Keep an existing regular grid only when its cells are close to squares."""
    for face in faces:
        if len(face.verts) != 4:
            return False
        corners = list(face.verts)
        directions = [b.co - a.co for a, b in zip(corners, corners[1:] + corners[:1])]
        lengths = [delta.length for delta in directions]
        if min(lengths) < 1e-8 or max(lengths) > min(lengths) * edge_ratio:
            return False
        if any(abs(a.normalized().dot(b.normalized())) > corner_cosine
               for a, b in zip(directions, directions[1:] + directions[:1])):
            return False
    return True


def _mixed_faces(bm, vertices, edges, material, smooth):
    faces = _square_grid_faces(bm, vertices, edges, material, smooth)
    if faces is not None:
        return faces
    if len(vertices) > 16 and len(vertices) % 2:
        # A dense odd rim needs interior vertices for well-shaped quads. Remove
        # one valid ear for parity, then grid the rest instead of making a fan
        # of long, thin boundary-only faces.
        coords, orientation = _fill_coordinates(vertices)
        candidates = []
        for i in range(len(vertices)):
            indices = ((i - 1) % len(vertices), i, (i + 1) % len(vertices))
            quality = _face_quality(vertices, coords, orientation, indices)
            if quality is not None:
                candidates.append((quality, i))
        originals = set(bm.verts), set(bm.edges), set(bm.faces)
        for _, i in sorted(candidates)[:8]:
            try:
                ear = bm.faces.new((vertices[i - 1], vertices[i], vertices[(i + 1) % len(vertices)]))
                ear.material_index, ear.smooth = material, smooth
                rim = vertices[:i] + vertices[i + 1:]
                rim_edges = [bm.edges.get((a, b)) for a, b in zip(rim, rim[1:] + rim[:1])]
                faces = [ear] + list(_grid_faces(bm, rim, rim_edges, material, smooth, balanced=True))
                _validate_fill(bm, vertices, faces)
                poor_quads = _poor_quads(faces)
                if poor_quads:
                    bmesh.ops.triangulate(bm, faces=poor_quads)
                    faces = [face for face in bm.faces if face not in originals[2]]
                    _validate_fill(bm, vertices, faces)
                return faces
            except (ValueError, RuntimeError):
                _remove_fill(bm, originals)
    layout = _quad_dominant_layout(bm, vertices, balance_areas=True)
    if layout is not None:
        faces = [bm.faces.new(corners) for corners in layout]
        for face in faces:
            face.material_index = material
            face.smooth = smooth
        return faces
    original_faces = set(bm.faces)
    normal = _rim_normal(vertices).normalized()
    result = bmesh.ops.triangle_fill(bm, edges=edges, normal=normal, use_beauty=True)
    faces = [item for item in result['geom'] if isinstance(item, bmesh.types.BMFace)]
    for face in faces:
        face.material_index = material
        face.smooth = smooth
    _pair_fill_triangles(bm, vertices, faces)
    faces = [face for face in bm.faces if face not in original_faces]
    return faces








def _equalize_fill_areas(bm, rim, faces, project=None):
    """Fit square quads/equilateral triangles of equal area with a fixed rim."""
    boundary = set(rim)
    movable = {v for f in faces for v in f.verts if v not in boundary}
    if not movable or len(faces) < 2:
        return
    target = sum(f.calc_area() for f in faces) / len(faces)
    if target < 1e-16:
        return
    anchors = {v: v.co.copy() for v in movable}
    poor = set(_poor_quads(faces))
    regular = [f for f in faces if len(f.verts) == 4 and all(v in movable for v in f.verts)
               and _square_shaped([f], 1.25, 0.26)]
    terms = {}
    for face in faces:
        corners = list(face.verts)
        size = sqrt(target) if len(corners) == 4 else sqrt(4*target/sqrt(3))
        pairs = [(a, b, size) for a, b in zip(corners, corners[1:] + corners[:1])]
        if len(corners) == 4:
            pairs += [(corners[0], corners[2], size*sqrt(2)),
                      (corners[1], corners[3], size*sqrt(2))]
        terms[face] = pairs

    def energy():
        variance = sum((f.calc_area() / target - 1) ** 2 for f in faces) / len(faces)
        shape = sum(sum(((a.co-b.co).length/size-1)**2 for a, b, size in pairs)/len(pairs)
                    for pairs in terms.values()) / len(faces)
        drift = sum((v.co-anchors[v]).length_squared for v in movable) / (len(movable)*target)
        return variance + 12*shape + 0.01*drift

    for _ in range(16):
        gradients = {v: Vector() for v in movable}
        weights = {v: 0.0 for v in movable}
        for face in faces:
            corners = list(face.verts)
            normal = _rim_normal(corners).normalized()
            residual = face.calc_area() / target - 1
            for i, v in enumerate(corners):
                if v in movable:
                    gradient = (corners[(i+1) % len(corners)].co - corners[i-1].co).cross(normal) * 0.5
                    gradients[v] += gradient * residual
                    weights[v] += gradient.length_squared / target
            pairs = terms[face]
            for a, b, size in pairs:
                delta = a.co-b.co
                length = max(delta.length, size*1e-8)
                gradient = delta * (12*target*(length/size-1)/(size*length*len(pairs)))
                weight = 12*target/(size*size*len(pairs))
                if a in movable:
                    gradients[a] += gradient
                    weights[a] += weight
                if b in movable:
                    gradients[b] -= gradient
                    weights[b] += weight
        positions = {v: v.co.copy() for v in movable}
        deltas = {}
        for v in movable:
            delta = -gradients[v] / max(weights[v], 1e-12)
            limit = sqrt(target)*0.2
            if delta.length > limit:
                delta *= limit/delta.length
            deltas[v] = delta
        old_energy = energy()
        accepted = False
        for attempt in range(7):
            for v in movable:
                co = positions[v] + deltas[v] * (0.5 ** attempt)
                v.co = project(co) if project is not None else co
            if (energy() < old_energy - 1e-9 and not (set(_poor_quads(faces)) - poor)
                    and _square_shaped(regular, 1.25, 0.26)):
                try:
                    _validate_fill(bm, rim, faces)
                    accepted = True
                    break
                except ValueError:
                    pass
        if not accepted:
            for v, co in positions.items():
                v.co = co
            break


def _orient_fill_faces(bm, faces, edges):
    """Make the fill internally consistent before matching the surrounding rim."""
    rim = set(edges)
    fill = set(faces)
    # A wire rim has no surrounding winding to decide which way is outward.
    if not any(len(edge.link_faces) == 2 for edge in edges):
        bmesh.ops.recalc_face_normals(bm, faces=faces)
        return
    flip = {faces[0]: False}
    pending = [faces[0]]
    while pending:
        face = pending.pop()
        for loop in face.loops:
            other = loop.link_loop_radial_next
            if other == loop or loop.edge in rim:
                continue
            if other.face not in fill:
                bmesh.ops.recalc_face_normals(bm, faces=faces)
                return
            reverse = flip[face] != (loop.vert == other.vert)
            if other.face not in flip:
                flip[other.face] = reverse
                pending.append(other.face)
            elif flip[other.face] != reverse:
                raise ValueError("The fill has inconsistent winding")
    if len(flip) != len(faces):
        bmesh.ops.recalc_face_normals(bm, faces=faces)
        return
    for face, reverse in flip.items():
        if reverse:
            face.normal_flip()
        face.normal_update()


def fill_hole(bm, vertices, project=None, quad_only=False, *, finalize=True, originals=None):
    """Prefer a grid, falling back to quads/triangles for irregular and odd rims."""
    count = len(vertices)
    if count < 3:
        raise ValueError("Fill Hole needs at least three rim vertices")
    if quad_only and (count < 4 or count % 2):
        raise ValueError("Quads Only needs an even number of rim vertices (at least four)")
    if len(set(vertices)) != count or any(not v.is_valid or v.hide for v in vertices):
        raise ValueError("Choose a visible closed hole")
    edges = [bm.edges.get((a, b)) for a, b in zip(vertices, vertices[1:] + vertices[:1])]
    if any(e is None or e.hide or len(e.link_faces) > 1 or any(f.hide for f in e.link_faces) for e in edges):
        raise ValueError("Choose a closed boundary loop")
    if originals is None:
        originals = set(bm.verts), set(bm.edges), set(bm.faces)
    original_verts, _, original_faces = originals
    reference_face = next((e.link_faces[0] for e in edges if e.link_faces), None)
    material = reference_face.material_index if reference_face is not None else 0
    smooth = reference_face.smooth if reference_face is not None else False
    try:
        faces = None
        if faces is None and count >= 4 and not count % 2:
            try:
                faces = _grid_faces(bm, vertices, edges, material, smooth, originals=originals)
                if any(len(f.verts) != 4 for f in faces):
                    raise ValueError("This hole cannot form a quad grid")
                if not quad_only:
                    areas = [face.calc_area() for face in faces]
                    if not _square_shaped(faces) or max(areas) > min(areas) * 2:
                        raise ValueError("Prefer square interior cells with a boundary transition")
                    poor_quads = _poor_quads(faces)
                    if poor_quads:
                        if count <= 16:
                            raise ValueError("This grid would force poor quad shapes")
                        bmesh.ops.triangulate(bm, faces=poor_quads)
                        faces = [face for face in bm.faces if face not in original_faces]
                        _validate_fill(bm, vertices, faces)
            except (ValueError, RuntimeError) as error:
                _remove_fill(bm, originals)
                faces = None
                if quad_only:
                    layout = _quad_dominant_layout(bm, vertices)
                    if layout is None or any(len(corners) != 4 for corners in layout):
                        raise ValueError("No valid all-quad layout found. Hold Ctrl for Quad/Tri.") from error
                    faces = [bm.faces.new(corners) for corners in layout]
                    for face in faces:
                        face.material_index, face.smooth = material, smooth
                    _validate_fill(bm, vertices, faces)
        if faces is None:
            faces = _mixed_faces(bm, vertices, edges, material, smooth)
            _validate_fill(bm, vertices, faces)
        face_set = set(faces)
        _orient_fill_faces(bm, faces, edges)
        conflicts = []
        for edge in edges:
            added = next((f for f in edge.link_faces if f in face_set), None)
            old = next((f for f in edge.link_faces if f in original_faces), None)
            if added is None:
                raise ValueError("The grid does not cover the whole hole")
            if old is not None:
                new_loop = next(loop for loop in added.loops if loop.edge == edge)
                old_loop = next(loop for loop in old.loops if loop.edge == edge)
                conflicts.append(new_loop.vert == old_loop.vert)
        if conflicts and all(conflicts):
            for face in faces:
                face.normal_flip()
        elif any(conflicts):
            raise ValueError("The hole has inconsistent winding. Flip normals first.")
        if project is not None:
            added = {v for face in faces for v in face.verts if v not in original_verts}
            for vertex in added:
                vertex.co = project(vertex.co)
        if not quad_only:
            _equalize_fill_areas(bm, vertices, faces, project)
            if finalize:
                bm.normal_update()
        _validate_fill(bm, vertices, faces)
        if any(len(e.link_faces) > 2 for f in faces for e in f.edges):
            raise ValueError("Fill Hole would create non-manifold geometry")
    except Exception:
        _remove_fill(bm, originals)
        raise
    if finalize:
        clear_pending(bm)
        select(bm, None)
        for face in faces:
            face.select_set(True)
        bm.select_history.add(faces[-1])
    return faces


def grid_fill(bm, vertices, project=None):
    """All-quad variant kept for callers requiring a regular grid."""
    return fill_hole(bm, vertices, project, quad_only=True)


def clear_pending(bm, discard=False):
    face_layer = bm.faces.layers.int.get(PENDING)
    edge_layer = bm.edges.layers.int.get(BASE)
    if discard and face_layer:
        remove_edges = []
        for face in list(bm.faces):
            if face[face_layer]:
                remove_edges.extend(edge for edge in face.edges if not (edge_layer and edge[edge_layer]))
                bm.faces.remove(face)
        for edge in remove_edges:
            if edge.is_valid and not edge.link_faces:
                bm.edges.remove(edge)
    for collection, layer in ((bm.faces, face_layer), (bm.edges, edge_layer)):
        if layer:
            for item in collection:
                item[layer] = 0


def pending(bm, anchor=None):
    layer = bm.faces.layers.int.get(PENDING)
    base = bm.edges.layers.int.get(BASE)
    if layer and base:
        for face in bm.faces:
            if face[layer] and len(face.verts) == 3:
                for loop in face.loops:
                    if loop.edge[base] and (anchor is None or loop.edge == anchor):
                        return face, loop.vert, loop.link_loop_next.vert, loop.link_loop_prev.vert
    return None


def select(bm, element):
    for items in (bm.faces, bm.edges, bm.verts):
        for item in items:
            item.select_set(False)
    bm.select_history.clear()
    if element is not None:
        element.select_set(True)
        bm.select_history.add(element)


def active(bm):
    verts = [vert for vert in bm.verts if vert.select and not vert.hide]
    if len(verts) == 2:
        edge = bm.edges.get(verts)
        if edge is not None and not edge.hide:
            return edge
    item = bm.select_history.active
    if isinstance(item, (bmesh.types.BMVert, bmesh.types.BMEdge)) and item.select:
        return item
    edges = [edge for edge in bm.edges if edge.select and not edge.hide]
    if len(edges) == 1:
        return edges[0]
    verts = [vert for vert in bm.verts if vert.select and not vert.hide]
    return verts[0] if len(verts) == 1 else None


def check_face(bm, verts, replacing=None):
    if len(set(verts)) != len(verts):
        raise ValueError("Choose a different vertex")
    normal = (verts[1].co - verts[0].co).cross(verts[2].co - verts[0].co)
    if normal.length < 1e-10:
        raise ValueError("Points are too close or collinear")
    if len(verts) == 4:
        other = (verts[2].co - verts[0].co).cross(verts[3].co - verts[0].co)
        if other.length < 1e-10 or normal.dot(other) <= 0:
            raise ValueError("Place the fourth corner without crossing the face")
    # A duplicate face shares every edge, so it is enough to inspect the faces
    # attached to this face's edges instead of scanning the whole mesh.
    wanted = set(verts)
    for a, b in zip(verts, verts[1:] + verts[:1]):
        edge = bm.edges.get((a, b))
        if edge is None:
            continue
        for face in edge.link_faces:
            if face != replacing and len(face.verts) == len(verts) and set(face.verts) == wanted:
                raise ValueError("That face already exists")
        if sum(face != replacing for face in edge.link_faces) >= 2:
            raise ValueError("Connection would create a non-manifold edge")


def oriented(edge, point):
    if edge.link_faces:
        loop = next(loop for loop in edge.link_faces[0].loops if loop.edge == edge)
        return loop.link_loop_next.vert, loop.vert
    a, b = edge.verts
    return (a, b) if (b.co - point).length <= (a.co - point).length else (b, a)


def bridge_verts(source, target):
    a, b = oriented(source, sum((v.co for v in target.verts), target.verts[0].co * 0) / 2)
    c, d = target.verts
    shared = set(source.verts) & set(target.verts)
    if shared:
        return [a, b, next(v for v in target.verts if v not in shared)]
    same_order = (b.co - c.co).length_squared + (a.co - d.co).length_squared
    flipped_order = (b.co - d.co).length_squared + (a.co - c.co).length_squared
    if flipped_order < same_order:
        c, d = d, c
    return [a, b, c, d]


def has_target_winding_conflict(target, verts):
    if not target.link_faces:
        return False
    loop = next(loop for loop in target.link_faces[0].loops if loop.edge == target)
    pairs = list(zip(verts, verts[1:] + verts[:1]))
    return (loop.vert, loop.link_loop_next.vert) in pairs


def recalculate_face_normals(bm):
    faces = list(bm.faces)
    if not faces:
        return False
    try:
        bmesh.ops.recalc_face_normals(bm, faces=faces)
        bm.normal_update()
    except Exception:
        return False
    return True


def extrude_quad(bm, anchor, point, target=None, project=None, finalize=True):
    """Place the new edge's midpoint at point, or snap one corner to target."""
    if len(anchor.link_faces) > 1:
        raise ValueError("Start from a boundary or loose edge")
    a, b = oriented(anchor, point)
    offset = point - (a.co + b.co) * 0.5
    created = []
    try:
        if target is None:
            span = b.co - a.co
            # Turn the outer edge perpendicular to the stroke in the plane
            # defined by the source edge and cursor, preserving strip width.
            direction = offset.normalized()
            turned = span - direction * span.dot(direction)
            if turned.length > 1e-10:
                turned = turned.normalized() * span.length
            else:
                turned = span.copy()
            c = bm.verts.new(point + turned * 0.5)
            created.append(c)
            d = bm.verts.new(point - turned * 0.5)
            created.append(d)
            # A sharp turn close to the source can fold the quad. Reduce
            # rotation until it fits, keeping the cursor at the midpoint.
            for attempt in range(9):
                try:
                    check_face(bm, [a, b, c, d])
                    break
                except ValueError:
                    weight = 0.5 ** (attempt + 1) if attempt < 8 else 0.0
                    adjusted = span.lerp(turned, weight).normalized() * span.length
                    c.co = point + adjusted * 0.5
                    d.co = point - adjusted * 0.5
        elif (target.co - b.co).length_squared <= (target.co - a.co).length_squared:
            c = target
            d = bm.verts.new(a.co + target.co - b.co)
            created.append(d)
        else:
            d = target
            c = bm.verts.new(b.co + target.co - a.co)
            created.append(c)
        if project is not None:
            for vertex in created:
                vertex.co = project(vertex.co)
        check_face(bm, [a, b, c, d])
        face = bm.faces.new((a, b, c, d))
    except Exception:
        for vertex in created:
            if vertex.is_valid and not vertex.link_edges:
                bm.verts.remove(vertex)
        raise
    if anchor.link_faces:
        source = next((f for f in anchor.link_faces if f != face), None)
        if source is not None:
            face.material_index = source.material_index
    if finalize:
        clear_pending(bm)
        select(bm, bm.edges.get((c, d)))
    return face


class StripPath:
    """Evenly sampled trace of a stroke. Faces are about one starting-edge width long."""
    MIN_FACE = 0.6

    def __init__(self, start, spacing):
        if spacing <= 1e-8:
            raise ValueError("The starting edge is too short")
        self.spacing = spacing
        self.trail = [start.copy()]
        self.lengths = []
        self.total = 0.0

    def length(self):
        return self.total

    def _append(self, point):
        length = (point - self.trail[-1]).length
        self.trail.append(point.copy())
        self.lengths.append(length)
        self.total += length

    def add(self, point):
        """Extend the trace, or trim it back when the cursor retraces it."""
        if len(self.trail) < 2:
            if (point - self.trail[-1]).length > 1e-9:
                self._append(point)
            return
        # Trimming is measured against the drawn strip: its faces reach half a
        # face width to either side of the stroke, plus slack for hand drift.
        tolerance = self.spacing * 1.25
        closest = None
        arc = 0.0
        for index, length in enumerate(self.lengths):
            a, b = self.trail[index], self.trail[index + 1]
            delta = b - a
            length_sq = delta.length_squared
            if length_sq <= 1e-20:
                arc += length
                continue
            t = max(0.0, min(1.0, (point - a).dot(delta) / length_sq))
            foot = a.lerp(b, t)
            distance = (point - foot).length
            if closest is None or distance < closest[0]:
                closest = (distance, index, foot, arc + t * delta.length)
            arc += length
        end = self.trail[-1]
        tangent = end - self.trail[-2]
        step_length = tangent.length
        if step_length > 1e-12:
            tangent = tangent / step_length
            delta = point - end
            along = delta.dot(tangent)
            lateral = (delta - tangent * along).length
            # Ignore mostly sideways hand jitter at the tip instead of branching
            # over the strip, while still following the stroke in small increments.
            dead = max(1e-9, min(step_length * 0.25, self.spacing * 0.02))
        else:
            along, lateral, dead = 0.0, 0.0, 1e-9
        if closest is None:
            if (point - end).length > 1e-9:
                self._append(point)
            return
        distance, index, foot, arc = closest
        if along < -dead:
            # Dragging back over the strip shortens it, even by tiny steps.
            # At or beyond the start there is nothing left to branch from.
            if distance <= tolerance or arc <= 1e-9:
                if arc < self.total - 1e-9:
                    self._trim(index, foot)
                return
            if (point - end).length > 1e-9:
                self._append(point)
            return
        if along > dead and along > lateral * 0.5:
            if (point - end).length > 1e-9:
                self._append(point)

    def _trim(self, index, foot):
        direction = self.trail[1] - self.trail[0] if len(self.trail) > 1 else None
        self.trail = self.trail[:index + 1]
        self.lengths = self.lengths[:index]
        if (foot - self.trail[-1]).length > 1e-9:
            self.lengths.append((foot - self.trail[-1]).length)
            self.trail.append(foot.copy())
        if len(self.trail) < 2:
            # Keep a tiny directional stub so the next move can still tell a
            # forward extension from dragging past the start.
            if direction is not None and direction.length > 1e-12:
                stub = self.trail[0] + direction.normalized() * 1e-6
            else:
                stub = self.trail[0].copy()
            self.lengths = [(stub - self.trail[0]).length]
            self.trail.append(stub)
        self.total = sum(self.lengths)

    def point_at(self, distance):
        for index, length in enumerate(self.lengths):
            if length > 1e-10 and distance <= length:
                return self.trail[index].lerp(self.trail[index + 1], distance / length)
            distance -= length
        return self.trail[-1].copy()

    def positions(self):
        """Fixed-distance rows plus a moving tip, without redistributing history."""
        total = self.total
        if total < self.spacing * self.MIN_FACE:
            return []
        # Only split the tip once the next face has enough length. Until then
        # it stretches from 0.6 to 1.6 widths, avoiding slivers and keeping every
        # completed row at its original arc distance as the stroke grows.
        count = max(1, int(total / self.spacing + 1.0 - self.MIN_FACE + 1e-6))
        points = []
        index, consumed = 0, 0.0
        for sample in range(1, count + 1):
            target = self.spacing * sample if sample < count else total
            while index < len(self.lengths):
                length = self.lengths[index]
                if length <= 1e-12 or consumed + length < target:
                    consumed += length
                    index += 1
                    continue
                break
            if index >= len(self.lengths):
                points.append(self.trail[-1].copy())
                continue
            length = self.lengths[index]
            t = max(0.0, min(1.0, (target - consumed) / length))
            points.append(self.trail[index].lerp(self.trail[index + 1], t))
        return points

    def copy(self):
        result = StripPath(self.trail[0], self.spacing)
        result.trail = [point.copy() for point in self.trail]
        result.lengths = list(self.lengths)
        result.total = self.total
        return result


def _has_face_winding_conflict(bm, verts, replacing=None):
    for a, b in zip(verts, verts[1:] + verts[:1]):
        edge = bm.edges.get((a, b))
        if edge is None:
            continue
        for other in edge.link_faces:
            if other == replacing or other.hide:
                continue
            loop = next(l for l in other.loops if l.edge == edge)
            if loop.vert == a and loop.link_loop_next.vert == b:
                return True
    return False


class WeldTargets:
    """Boundary/loose vertices near a strip, indexed for fast weld lookups."""

    def __init__(self, original, target=None):
        self.vertices = [
            vertex for vertex in original
            if vertex.is_valid and not vertex.hide
            and not (vertex.link_faces and not vertex.is_boundary)
            and (target is None or vertex not in target.verts)]
        tree = KDTree(len(self.vertices))
        for index, vertex in enumerate(self.vertices):
            tree.insert(vertex.co, index)
        tree.balance()
        self.tree = tree

    def near(self, co, radius):
        return [(self.vertices[index], distance)
                for _, index, distance in self.tree.find_range(co, radius)]


def _snap_strip_quad(bm, face, original, snap_distance=None, project=None, target=None,
                     snapped_points=None, weld_targets=None):
    corners = list(face.verts)
    if snap_distance is None and target is None:
        return face, bm.edges.get((corners[2], corners[3]))
    a, b = corners[0], corners[1]
    width = (b.co - a.co).length
    for corner_idx, base_idx, other_idx in ((3, 0, 2), (2, 1, 3)):
        corner = corners[corner_idx]
        base = corners[base_idx]
        other = corners[other_idx]
        if corner in original or not corner.is_valid:
            continue
        step_length = (corner.co - base.co).length
        scale = min(step_length, width) if step_length > 1e-8 and width > 1e-8 else max(step_length, width)
        if snap_distance is not None:
            # Weld within Merge Radius, but keep the face-reach of the previous
            # auto-connect so strokes drawn alongside existing topology weld.
            snap_radius = snap_distance(corner.co)
            connected_limit = max(snap_radius, max(step_length, width) * 0.75)
            unconnected_limit = max(snap_radius, scale * 0.45)
            base_limit = connected_limit
            pool = ([vertex for vertex, _ in weld_targets.near(
                corner.co, max(connected_limit, unconnected_limit))]
                if weld_targets is not None else original)
        else:
            connected_limit = unconnected_limit = base_limit = 0.0
            pool = ()
        candidates = []
        for vertex in pool:
            if not vertex.is_valid or vertex.hide or vertex in corners:
                continue
            if target is not None and vertex in target.verts:
                continue
            if vertex.link_faces and not vertex.is_boundary:
                continue
            dist = (vertex.co - corner.co).length
            side_edge = bm.edges.get((base, vertex)) if base in original else None
            if side_edge is not None and not side_edge.hide and len(side_edge.link_faces) <= 1:
                if (vertex.co - base.co).dot(corner.co - base.co) > 0 and dist <= connected_limit:
                    candidates.append((0, dist, vertex, vertex.co.copy()))
            elif dist <= unconnected_limit:
                candidates.append((1, dist, vertex, vertex.co.copy()))
        for _, _, candidate, co in sorted(candidates, key=lambda item: item[:2]):
            old_corner = corner.co.copy()
            old_other = other.co.copy()
            other_trials = [old_other]
            if other not in original:
                # Follow the neighboring row with the entire free edge. Moving
                # only the welded corner leaves the far side at the cursor and
                # can flare or shear the quad, especially on uneven boundaries.
                shifted = old_other + co - old_corner
                other_trials.insert(0, project(shifted) if project is not None else shifted)
            corner.co = co
            proposed = [candidate if v == corner else v for v in corners]
            welded = False
            for trial_other in other_trials:
                other.co = trial_other
                try:
                    check_face(bm, proposed, replacing=face)
                    if _has_face_winding_conflict(bm, proposed, replacing=face):
                        raise ValueError(WINDING_ERROR)
                except ValueError:
                    continue
                welded = True
                break
            if not welded:
                corner.co = old_corner
                other.co = old_other
                continue
            if snapped_points is not None:
                snapped_points.append(co.copy())
            corners[corner_idx] = candidate
            bmesh.ops.weld_verts(bm, targetmap={corner: candidate})
            face = next(f for f in candidate.link_faces if set(f.verts) == set(corners))
            if base not in original and base.is_valid:
                for edge in candidate.link_edges:
                    if edge.hide or len(edge.link_faces) > 1:
                        continue
                    prev_vert = edge.other_vert(candidate)
                    if (prev_vert in original and not prev_vert.hide and prev_vert not in corners
                            and (prev_vert.co - base.co).length <= base_limit):
                        old_base = base.co.copy()
                        base.co = prev_vert.co.copy()
                        try:
                            for linked in list(base.link_faces):
                                linked_verts = [prev_vert if v == base else v for v in linked.verts]
                                check_face(bm, linked_verts, replacing=linked)
                                if _has_face_winding_conflict(bm, linked_verts, replacing=linked):
                                    raise ValueError(WINDING_ERROR)
                        except ValueError:
                            base.co = old_base
                            continue
                        if snapped_points is not None:
                            snapped_points.append(prev_vert.co.copy())
                        corners[base_idx] = prev_vert
                        bmesh.ops.weld_verts(bm, targetmap={base: prev_vert})
                        face = next(f for f in candidate.link_faces if set(f.verts) == set(corners))
                        break
            break
    return face, bm.edges.get((corners[2], corners[3]))


def _strip_boundary_row(edge, points, start, original, snap_distance, target):
    """Replace width-sized samples with rows of an attached longer boundary."""
    if snap_distance is None:
        return points[start], start + 1
    center = sum((v.co for v in edge.verts), Vector()) * 0.5
    direction = points[start] - center
    if direction.length < 1e-8:
        return points[start], start + 1
    a, b = oriented(edge, points[start])
    width = edge.calc_length()
    choices = []
    for base in (a, b):
        if base not in original:
            continue
        for side in base.link_edges:
            candidate = side.other_vert(base)
            if (side == edge or side.hide or len(side.link_faces) > 1
                    or candidate not in original or candidate.hide
                    or (target is not None and candidate in target.verts)):
                continue
            delta = candidate.co - base.co
            length = delta.length
            if length <= width * 1.75 or delta.normalized().dot(direction.normalized()) < 0.75:
                continue
            if side.link_faces:
                loop = next(l for l in side.link_faces[0].loops if l.edge == side)
                if loop.vert != (base if base == a else candidate):
                    continue
            choices.append((length, delta))
    for length, delta in sorted(choices, key=lambda item: item[0]):
        tangent = delta / length
        corridor = max(width * 0.75, snap_distance(center + delta))
        previous = center
        for index in range(start, len(points)):
            point = points[index]
            advance = (point - center).dot(tangent)
            if advance >= length - 1e-6:
                before = (previous - center).dot(tangent)
                crossing = previous.lerp(point, (length - before) / max(advance - before, 1e-8))
                if (crossing - (center + delta)).length > corridor:
                    break
                # Retain a sample beyond this row for the next boundary edge.
                next_index = index + 1 if (point - crossing).length < width * 0.6 else index
                return center + delta, next_index
            if ((point - center) - tangent * advance).length > corridor:
                break
            previous = point
        else:
            # The cursor has not reached the next boundary vertex yet. Keep a
            # single moving tip instead of filling that interval with tiny quads.
            return points[-1], len(points)
    return points[start], start + 1


def quad_strip(bm, anchor, points, project=None, target=None, snap_distance=None, snapped_points=None,
               triangle=False):
    """Extrude a whole stroke, rolling back geometry if any quad is invalid."""
    if not isinstance(anchor, bmesh.types.BMEdge) or anchor.hide or len(anchor.link_faces) > 1:
        raise ValueError("Select a boundary or loose edge to draw a strip")
    original = set(bm.verts)
    original_edges = set(bm.edges)
    weld_targets = WeldTargets(original, target) if snap_distance is not None else None
    faces = []
    edge = anchor
    try:
        if triangle and len(points) == 1 and not anchor.link_faces:
            # Starting new topology from a single loose edge: the first face
            # after that edge is a pending triangle at the cursor, exactly like
            # a plain Ctrl-click. The next click completes it into a quad.
            face = extend(bm, anchor, bm.verts.new(project(points[0]) if project else points[0]))
            faces.append(face)
            return faces
        sample = 0
        while sample < len(points):
            if edge is None or len(edge.link_faces) > 1:
                break
            point, sample = _strip_boundary_row(edge, points, sample, original, snap_distance, target)
            face = extrude_quad(bm, edge, point, project=project, finalize=False)
            face, edge = _snap_strip_quad(bm, face, original, snap_distance, project, target,
                                          snapped_points, weld_targets)
            faces.append(face)
        if target is not None and edge != target:
            face = bridge(bm, edge, target, finalize=False, repair_winding=False)
            if edge.link_faces:
                source = next((f for f in edge.link_faces if f != face), None)
                if source is not None:
                    face.material_index = source.material_index
            faces.append(face)
            edge = target
    except Exception:
        if snapped_points is not None:
            snapped_points.clear()
        for face in faces:
            if face.is_valid:
                bm.faces.remove(face)
        for new_edge in list(bm.edges):
            if new_edge not in original_edges and not new_edge.link_faces:
                bm.edges.remove(new_edge)
        created = [v for v in bm.verts if v not in original]
        if created:
            bmesh.ops.delete(bm, geom=created, context='VERTS')
        raise
    if faces:
        clear_pending(bm)
        select(bm, edge)
    return faces


def extend(bm, anchor, vertex, quad_only=False):
    if quad_only and isinstance(anchor, bmesh.types.BMEdge) and not pending(bm, anchor):
        return extrude_quad(bm, anchor, vertex.co, target=vertex)
    # Adding a custom-data layer can invalidate existing wrappers in that domain.
    anchor_verts = tuple(anchor.verts) if isinstance(anchor, bmesh.types.BMEdge) else None
    face_layer = bm.faces.layers.int.get(PENDING) or bm.faces.layers.int.new(PENDING)
    edge_layer = bm.edges.layers.int.get(BASE) or bm.edges.layers.int.new(BASE)
    if anchor_verts:
        anchor = bm.edges.get(anchor_verts)
    state = pending(bm, anchor)
    if state:
        face, a, b, c = state
        verts = [a, b, c, vertex]
        diagonal_verts = (a, c)
        try:
            check_face(bm, verts, replacing=face)
        except ValueError:
            verts = [a, b, vertex, c]
            check_face(bm, verts, replacing=face)
            diagonal_verts = (b, c)
        material = face.material_index
        bm.faces.remove(face)
        result = bm.faces.new(verts)
        result.material_index = material
        diagonal = bm.edges.get(diagonal_verts)
        if diagonal and not diagonal.link_faces:
            bm.edges.remove(diagonal)
        clear_pending(bm)
        select(bm, bm.edges.get((c, vertex)))
        return result
    if isinstance(anchor, bmesh.types.BMEdge):
        if len(anchor.link_faces) > 1:
            raise ValueError("Start from a boundary or loose edge")
        a, b = oriented(anchor, vertex.co)
        check_face(bm, [a, b, vertex])
        face = bm.faces.new((a, b, vertex))
        clear_pending(bm, discard=quad_only)
        face[face_layer] = 1
        anchor[edge_layer] = 1
        select(bm, anchor)
        return face
    clear_pending(bm, discard=quad_only)
    if isinstance(anchor, bmesh.types.BMVert) and anchor != vertex:
        edge = bm.edges.get((anchor, vertex)) or bm.edges.new((anchor, vertex))
        # Select the whole edge so the next click builds a face from it
        # instead of continuing a lone vertex chain.
        select(bm, edge)
        return edge
    select(bm, vertex)
    return vertex


def boundary_outward(edge):
    if edge is None or edge.hide or len(edge.link_faces) != 1:
        return None
    face = edge.link_faces[0]
    if face.hide:
        return None
    a, b = edge.verts[0].co, edge.verts[1].co
    span = b - a
    if span.length_squared <= 1e-16:
        return None
    mid = (a + b) * 0.5
    inward = face.calc_center_median() - mid
    perp = inward - span * (inward.dot(span) / span.length_squared)
    return -perp.normalized() if perp.length > 1e-10 else None


def _edge_point_distance(edge, point):
    a, b = edge.verts[0].co, edge.verts[1].co
    span = b - a
    t = max(0.0, min(1.0, (point - a).dot(span) / max(span.length_squared, 1e-16)))
    return (point - a.lerp(b, t)).length


def hover_select_edge(bm, anchor=None, point=None):
    """When hovering behind or alongside the active boundary edge, switch to the closest boundary edge."""
    if anchor is None:
        anchor = active(bm)
    if (point is None or not isinstance(anchor, bmesh.types.BMEdge)
            or anchor.hide or len(anchor.link_faces) != 1 or pending(bm)):
        return anchor
    outward = boundary_outward(anchor)
    if outward is None:
        return anchor
    a, b = anchor.verts[0].co, anchor.verts[1].co
    span = b - a
    length = span.length
    if length <= 1e-8:
        return anchor
    mid = (a + b) * 0.5
    forward = (point - mid).dot(outward)
    along = abs((point - mid).dot(span / length))
    faces = set()
    queue = [anchor.link_faces[0]]
    while queue:
        face = queue.pop()
        if face in faces or face.hide:
            continue
        faces.add(face)
        for edge in face.edges:
            if not edge.hide:
                queue.extend(f for f in edge.link_faces if f not in faces and not f.hide)
    candidates = {e for f in faces for e in f.edges if e != anchor and not e.hide and len(e.link_faces) == 1}
    if not candidates:
        return anchor
    anchor_dist = _edge_point_distance(anchor, point)
    if forward >= -length * 0.1:
        if along <= length * 0.75:
            return anchor
        # Allow sliding along a multi-quad border to an adjacent collinear boundary edge.
        side_candidates = []
        for edge in candidates:
            e_outward = boundary_outward(edge)
            if e_outward is None:
                continue
            ea, eb = edge.verts[0].co, edge.verts[1].co
            e_span = eb - ea
            e_len = e_span.length
            if e_len <= 1e-8:
                continue
            e_mid = (ea + eb) * 0.5
            e_forward = (point - e_mid).dot(e_outward)
            e_along = abs((point - e_mid).dot(e_span / e_len))
            dist = _edge_point_distance(edge, point)
            if e_forward >= 0 and e_along <= e_len * 0.6 and dist < anchor_dist * 0.8:
                side_candidates.append((-dist, edge))
        if side_candidates:
            best_edge = max(side_candidates, key=lambda item: item[0])[1]
            select(bm, best_edge)
            return best_edge
        return anchor
    best_edge = None
    best_score = None
    for edge in candidates:
        e_outward = boundary_outward(edge)
        if e_outward is None:
            continue
        e_mid = (edge.verts[0].co + edge.verts[1].co) * 0.5
        e_forward = (point - e_mid).dot(e_outward)
        dist = _edge_point_distance(edge, point)
        # Use the same improvement margin as lateral switching. Near-equal
        # candidates should not flip the active edge as the hand drifts.
        if dist >= anchor_dist * 0.8:
            continue
        score = (-dist, e_forward >= 0, edge in anchor.link_faces[0].edges, e_forward,
                 tuple(sorted(tuple(v.co) for v in edge.verts)))
        if best_score is None or score > best_score:
            best_score = score
            best_edge = edge
    if best_edge is not None:
        select(bm, best_edge)
        return best_edge
    return anchor


def bridge(bm, source, target, quad_only=False, finalize=True, repair_winding=True):
    if source == target or len(source.link_faces) > 1 or len(target.link_faces) > 1:
        raise ValueError("Choose another boundary or loose edge")
    verts = bridge_verts(source, target)
    check_face(bm, verts)
    # Adjacent existing faces must traverse a shared edge in opposite directions.
    if has_target_winding_conflict(target, verts):
        if not repair_winding or not recalculate_face_normals(bm):
            raise ValueError(WINDING_ERROR)
        verts = bridge_verts(source, target)
        check_face(bm, verts)
        if has_target_winding_conflict(target, verts):
            raise ValueError(WINDING_ERROR)
    face = bm.faces.new(verts)
    if finalize:
        clear_pending(bm)
        select(bm, target)
    return face


def connect_cut(bm, start, end, discard_pending=False):
    if start == end:
        select(bm, end)
        return end
    existing = bm.edges.get((start, end))
    if existing:
        result = existing
    elif start.link_faces and end.link_faces:
        try:
            edges = bmesh.ops.connect_vert_pair(
                bm, verts=[start, end], faces_exclude=[f for f in bm.faces if f.hide]
            )['edges']
        except RuntimeError:
            edges = []
        if edges:
            result = edges
        elif (not start.link_faces or start.is_boundary) and (not end.link_faces or end.is_boundary):
            result = bm.edges.new((start, end))
        else:
            raise ValueError("No face path for this cut; click an intermediate edge")
    else:
        result = bm.edges.new((start, end))
    clear_pending(bm, discard=discard_pending)
    select(bm, end)
    return result


def snap_quad(bm, face, original, snap_distance, snapped_points=None):
    corners = list(face.verts)
    outer = active(bm)
    outer_verts = list(outer.verts) if isinstance(outer, bmesh.types.BMEdge) else []
    for corner in list(corners):
        if corner in original:
            continue
        radius = snap_distance(corner.co)
        candidates = []
        for vertex in original:
            if vertex.is_valid and not vertex.hide and vertex not in corners:
                candidates.append(((vertex.co - corner.co).length, vertex, vertex.co.copy()))
        candidates = [item for item in candidates if item[0] <= radius]
        for distance, target, co in sorted(candidates, key=lambda item: item[0]):
            old = corner.co.copy()
            corner.co = co
            proposed = [target if v == corner else v for v in corners]
            try:
                check_face(bm, proposed, replacing=face)
            except ValueError:
                corner.co = old
                continue
            if snapped_points is not None:
                snapped_points.append(target.co.copy())
            corners = [target if v == corner else v for v in corners]
            outer_verts = [target if v == corner else v for v in outer_verts]
            bmesh.ops.weld_verts(bm, targetmap={corner: target})
            face = next(f for f in target.link_faces if set(f.verts) == set(corners))
            break
    if len(outer_verts) == 2:
        select(bm, bm.edges.get(outer_verts))
    return face


def apply_click(bm, anchor, hit, point=None, edge_factor=0.5, bridge_mode=False, project=None, quad_only=False, snap_distance=None, snapped_points=None, auto_reselect=True, allow_cut=True):
    original = set(bm.verts)
    result = _apply_click(bm, anchor, hit, point, edge_factor, bridge_mode, quad_only, auto_reselect, allow_cut)
    if project is not None:
        positions = [(v, project(v.co)) for v in bm.verts if v not in original]
        for vertex, position in positions:
            vertex.co = position
    if quad_only and snap_distance is not None and isinstance(result, bmesh.types.BMFace):
        result = snap_quad(bm, result, original, snap_distance, snapped_points)
    elif allow_cut and isinstance(anchor, bmesh.types.BMVert) and isinstance(result, bmesh.types.BMEdge) and snap_distance is not None:
        new_vert = next((v for v in result.verts if v not in original), None)
        if new_vert is not None:
            radius = snap_distance(new_vert.co) * 0.25
            candidates = [((v.co - new_vert.co).length, v) for v in original
                          if v.is_valid and not v.hide and v != anchor and (v.co - new_vert.co).length <= radius]
            if candidates:
                _, target = min(candidates, key=lambda item: item[0])
                if snapped_points is not None:
                    snapped_points.append(target.co.copy())
                bmesh.ops.delete(bm, geom=[new_vert], context='VERTS')
                result = connect_cut(bm, anchor, target, quad_only)
    return result


def _apply_click(bm, anchor, hit, point=None, edge_factor=0.5, bridge_mode=False, quad_only=False, auto_reselect=True, allow_cut=True):
    if auto_reselect and hit is None and point is not None and not bridge_mode:
        anchor = hover_select_edge(bm, anchor, point)
    if not allow_cut:
        if isinstance(hit, bmesh.types.BMEdge) and (anchor == hit or not isinstance(anchor, bmesh.types.BMEdge)):
            clear_pending(bm)
            select(bm, hit)
            return hit
        if isinstance(anchor, bmesh.types.BMVert) and isinstance(hit, bmesh.types.BMVert):
            raise ValueError("Select a starting edge to draw; use Alt to cut")
    if isinstance(hit, bmesh.types.BMEdge):
        if bridge_mode or (isinstance(anchor, bmesh.types.BMEdge) and anchor != hit):
            if not isinstance(anchor, bmesh.types.BMEdge):
                raise ValueError("Select a starting edge to bridge")
            if pending(bm):
                raise ValueError("Complete the quad before bridging" if quad_only else "Commit the triangle before bridging")
            return bridge(bm, anchor, hit, quad_only)
        clear_pending(bm, discard=quad_only)
        _, vertex = bmesh.utils.edge_split(hit, hit.verts[0], max(0.001, min(0.999, edge_factor)))
        if isinstance(anchor, bmesh.types.BMVert):
            return connect_cut(bm, anchor, vertex, quad_only)
        select(bm, vertex)
        return vertex
    if isinstance(hit, bmesh.types.BMVert) and isinstance(anchor, bmesh.types.BMVert):
        return connect_cut(bm, anchor, hit, quad_only)
    if isinstance(hit, bmesh.types.BMVert) and isinstance(anchor, bmesh.types.BMEdge) and hit in anchor.verts:
        clear_pending(bm, discard=quad_only)
        select(bm, anchor)
        return anchor
    if hit is None and point is None:
        raise ValueError("Cursor is outside the reference surface")
    if quad_only and isinstance(anchor, bmesh.types.BMEdge) and not pending(bm, anchor):
        return extrude_quad(bm, anchor, hit.co if hit is not None else point, target=hit)
    return extend(bm, anchor, hit if hit is not None else bm.verts.new(point), quad_only)


def edge_merge_map(bm, source, target, positions=None):
    """Map a whole edge to a topological neighbor, keeping shared corners fixed."""
    if (source == target or source.hide or target.hide
            or any(v.hide for e in (source, target) for v in e.verts)
            or any(f.hide for v in source.verts for f in v.link_faces)
            or any(len(e.link_faces) > 2 for e in (source, target))):
        raise ValueError("Choose a different visible neighboring edge")
    a, b = source.verts
    c, d = target.verts
    shared = set(source.verts) & set(target.verts)
    if not (shared or set(source.link_faces) & set(target.link_faces)
            or any(bm.edges.get((v, w)) for v in source.verts for w in target.verts)):
        raise ValueError("Only neighboring edges can merge")
    if shared:
        corner = next(iter(shared))
        return {corner: corner, source.other_vert(corner): target.other_vert(corner)}
    positions = positions if positions is not None else [a.co, b.co]
    direct = sum((p-v.co).length_squared for p, v in zip(positions, (c, d)))
    reverse = sum((p-v.co).length_squared for p, v in zip(positions, (d, c)))
    return dict(zip((a, b), (c, d) if direct <= reverse else (d, c)))


def merge_edges(bm, source, target, positions=None):
    """Validate the simultaneous weld on a copy before changing live topology."""
    mapping = edge_merge_map(bm, source, target, positions)
    bm.verts.index_update()
    bm.edges.index_update()
    pairs = [(v.index, w.index) for v, w in mapping.items() if v != w]
    scratch = bm.copy()
    try:
        scratch.verts.ensure_lookup_table()
        targetmap = {scratch.verts[i]: scratch.verts[j] for i, j in pairs}
        survivors = list(targetmap.values())
        bmesh.ops.weld_verts(scratch, targetmap=targetmap)
        for v in survivors:
            if not v.is_valid:
                raise ValueError("Edge merge would remove a target corner")
            if any(len(e.link_faces) > 2 for e in v.link_edges):
                raise ValueError("Edge merge would create non-manifold geometry")
            for face in v.link_faces:
                check_face(scratch, list(face.verts), replacing=face)
    finally:
        scratch.free()
    endpoints = list(target.verts)
    clear_pending(bm)
    bmesh.ops.weld_verts(bm, targetmap={v: w for v, w in mapping.items() if v != w})
    target = bm.edges.get(endpoints)
    select(bm, target)
    bm.normal_update()
    return target


def merge_vertices(bm, source, target, discard_pending=False):
    if source != target:
        clear_pending(bm, discard=discard_pending)
        bmesh.ops.weld_verts(bm, targetmap={source: target})
    select(bm, target)
    return target


def collapse_edge_midpoint(bm, edge):
    if edge is None or edge.hide or len(edge.link_faces) > 2:
        raise ValueError("Choose a visible manifold or loose edge")
    source, target = edge.verts
    target.co = (source.co + target.co) * 0.5
    return merge_vertices(bm, source, target)






def edge_triangle_plan(bm, edge, factor, target):
    if edge is None or edge.hide or len(edge.link_faces) > 2 or target.hide or target in edge.verts:
        raise ValueError("Choose a different visible vertex")
    factor = max(0.001, min(0.999, factor))
    point = edge.verts[0].co.lerp(edge.verts[1].co, factor)
    if (edge.verts[1].co - edge.verts[0].co).cross(target.co - point).length < 1e-10:
        raise ValueError("The closest vertex is collinear with this edge")
    common = next((f for f in edge.link_faces if not f.hide and target in f.verts), None)
    if common is not None:
        if not any(bm.edges.get((v, target)) in common.edges for v in edge.verts):
            raise ValueError("Choose a neighboring corner to create a triangle")
        return 'FACE', common, None
    if len(edge.link_faces) > 1:
        raise ValueError("Choose a vertex on a face beside this edge")
    candidates = []
    for endpoint in edge.verts:
        side = bm.edges.get((endpoint, target))
        if side is None or (not side.hide and len(side.link_faces) < 2):
            candidates.append((side is None, (endpoint.co - target.co).length_squared, endpoint))
    if not candidates:
        raise ValueError("Connection would create a non-manifold edge")
    return 'FILL', None, min(candidates, key=lambda item: item[:2])[2]


def split_edge_triangle(bm, edge, factor, target):
    """Split at the clicked position and connect to a nearby vertex with a triangle."""
    kind, face, endpoint = edge_triangle_plan(bm, edge, factor, target)
    factor = max(0.001, min(0.999, factor))
    material = edge.link_faces[0].material_index if edge.link_faces else 0
    smooth = edge.link_faces[0].smooth if edge.link_faces else False
    _, vertex = bmesh.utils.edge_split(edge, edge.verts[0], factor)
    if kind == 'FACE':
        bmesh.utils.face_split(face, vertex, target)
    else:
        segment = bm.edges.get((endpoint, vertex))
        a, b = oriented(segment, target.co)
        face = bm.faces.new((a, b, target))
        face.material_index, face.smooth = material, smooth
    clear_pending(bm)
    select(bm, vertex)
    return vertex


def loop_plan(edge, factor, project=None):
    if edge.hide or len(edge.link_faces) > 2:
        raise ValueError("Choose an edge in a manifold quad strip")
    factor = max(0.001, min(0.999, factor))
    starts = {edge: edge.verts[0]}
    queue = [edge]
    faces = {}
    while queue:
        current = queue.pop()
        for face in current.link_faces:
            if face in faces or face.hide or len(face.verts) != 4:
                continue
            opposite = next(e for e in face.edges if not set(e.verts) & set(current.verts))
            if opposite.hide or len(opposite.link_faces) > 2:
                continue
            side = next(e for e in face.edges if e != current and starts[current] in e.verts)
            start = side.other_vert(starts[current])
            if opposite in starts and starts[opposite] != start:
                raise ValueError("This quad strip twists back onto itself")
            faces[face] = (current, opposite)
            if opposite not in starts:
                starts[opposite] = start
                queue.append(opposite)
    if not faces:
        raise ValueError("Loop cuts need a connected quad face")
    points = {e: v.co.lerp(e.other_vert(v).co, factor) for e, v in starts.items()}
    if project is not None:
        points = {e: project(co) for e, co in points.items()}
    return starts, faces, points


def loop_preview(edge, factor, project=None):
    _, faces, points = loop_plan(edge, factor, project)
    return [points[e].copy() for pair in faces.values() for e in pair]


def loop_cut(bm, edge, factor, project=None, discard_pending=False):
    starts, faces, points = loop_plan(edge, factor, project)
    clear_pending(bm, discard=discard_pending)
    vertices = {e: bmesh.utils.edge_split(e, start, max(0.001, min(0.999, factor)))[1]
                for e, start in starts.items()}
    cuts = []
    for face, (a, b) in faces.items():
        _, loop = bmesh.utils.face_split(face, vertices[a], vertices[b])
        cuts.append(loop.edge)
    for original_edge, vertex in vertices.items():
        vertex.co = points[original_edge]
    select(bm, None)
    for cut in cuts:
        cut.select_set(True)
    bm.select_history.add(cuts[-1])
    return cuts
