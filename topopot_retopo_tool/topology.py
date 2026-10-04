"""BMesh operations shared by the interactive tool and regression tests."""
import bmesh
from mathutils import Vector

PENDING = "retopo_tool_pending"
BASE = "retopo_tool_base"
WINDING_ERROR = "Target edge has incompatible face winding. Flip normals."


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


def _grid_faces(bm, vertices, edges, material, smooth, balanced=False):
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
    offset, span = max(((offset, span) for offset in range(count) for span in spans),
                       key=lambda pair: (round(sum(turns[(pair[0] + step) % count]
                                                   for step in (0, pair[1], half, half + pair[1])), 4),
                                         -abs(pair[1] - count / 4), -pair[0]))
    arcs = [edges[(offset + step) % count] for step in range(span)]
    arcs += [edges[(offset + half + step) % count] for step in range(span)]
    return bmesh.ops.grid_fill(bm, edges=arcs, mat_nr=material, use_smooth=smooth)['faces']


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


def _validate_fill(bm, vertices, faces):
    """Reject folds and grid layouts which extend beyond a concave rim."""
    if not faces or any(len(f.verts) not in {3, 4} for f in faces):
        raise ValueError("This hole cannot form a valid fill")
    normal = sum((a.co.cross(b.co) for a, b in zip(vertices, vertices[1:] + vertices[:1])), Vector())
    axis = max(range(3), key=lambda index: abs(normal[index]))
    axes = [index for index in range(3) if index != axis]
    def projected(vertex):
        return Vector((vertex.co[axes[0]], vertex.co[axes[1]]))
    def cross(a, b):
        return a.x * b.y - a.y * b.x
    polygon = [projected(v) for v in vertices]
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
    normal = sum((a.co.cross(b.co) for a, b in zip(vertices, vertices[1:] + vertices[:1])), Vector())
    axis = max(range(3), key=lambda index: abs(normal[index]))
    axes = [index for index in range(3) if index != axis]
    coords = [(v.co[axes[0]], v.co[axes[1]]) for v in vertices]
    area = sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(coords, coords[1:] + coords[:1]))
    return coords, 1 if area > 0 else -1


def _turn(a, b, c):
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def _face_quality(vertices, coords, orientation, indices):
    """Reject concave/sliver quads; rank the remaining shapes by corner quality."""
    points = [coords[index] for index in indices]
    lengths = [(b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
               for a, b in zip(points, points[1:] + points[:1])]
    if min(lengths) < 1e-16:
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


def _quad_dominant_layout(bm, vertices):
    """Search polygon partitions, minimizing triangles before optimizing shape.

    Unlike joining a fixed triangulation, this can change diagonals to avoid
    stranding triangles. Bound the quartic search to keep large-hole hover fast.
    """
    count = len(vertices)
    if count > 48:
        return None
    coords, orientation = _fill_coordinates(vertices)
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


def _pair_fill_triangles(bm, vertices, faces):
    """Maximum quality-constrained matching on a triangulated polygon's dual tree."""
    coords, orientation = _fill_coordinates(vertices)
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


def _mixed_faces(bm, vertices, edges, material, smooth):
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
    layout = _quad_dominant_layout(bm, vertices)
    if layout is not None:
        faces = [bm.faces.new(corners) for corners in layout]
        for face in faces:
            face.material_index = material
            face.smooth = smooth
        return faces
    original_faces = set(bm.faces)
    normal = sum((a.co.cross(b.co) for a, b in zip(vertices, vertices[1:] + vertices[:1])), Vector()).normalized()
    result = bmesh.ops.triangle_fill(bm, edges=edges, normal=normal, use_beauty=True)
    faces = [item for item in result['geom'] if isinstance(item, bmesh.types.BMFace)]
    for face in faces:
        face.material_index = material
        face.smooth = smooth
    _pair_fill_triangles(bm, vertices, faces)
    faces = [face for face in bm.faces if face not in original_faces]
    return faces


def fill_hole(bm, vertices, project=None, quad_only=False):
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
    originals = set(bm.verts), set(bm.edges), set(bm.faces)
    original_verts, _, original_faces = originals
    reference_face = next((e.link_faces[0] for e in edges if e.link_faces), None)
    material = reference_face.material_index if reference_face is not None else 0
    smooth = reference_face.smooth if reference_face is not None else False
    try:
        faces = None
        if count >= 4 and not count % 2:
            try:
                faces = _grid_faces(bm, vertices, edges, material, smooth)
                if any(len(f.verts) != 4 for f in faces):
                    raise ValueError("This hole cannot form a quad grid")
                _validate_fill(bm, vertices, faces)
                if not quad_only:
                    poor_quads = _poor_quads(faces)
                    if poor_quads:
                        if count <= 16:
                            raise ValueError("This grid would force poor quad shapes")
                        bmesh.ops.triangulate(bm, faces=poor_quads)
                        faces = [face for face in bm.faces if face not in original_faces]
                        _validate_fill(bm, vertices, faces)
            except (ValueError, RuntimeError):
                if quad_only:
                    raise
                _remove_fill(bm, originals)
                faces = None
        if faces is None:
            faces = _mixed_faces(bm, vertices, edges, material, smooth)
            _validate_fill(bm, vertices, faces)
        face_set = set(faces)
        bmesh.ops.recalc_face_normals(bm, faces=faces)
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
            for vertex in bm.verts:
                if vertex not in original_verts:
                    vertex.co = project(vertex.co)
        _validate_fill(bm, vertices, faces)
        if any(len(e.link_faces) > 2 for f in faces for e in f.edges):
            raise ValueError("Fill Hole would create non-manifold geometry")
    except Exception:
        _remove_fill(bm, originals)
        raise
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


def pending(bm):
    layer = bm.faces.layers.int.get(PENDING)
    base = bm.edges.layers.int.get(BASE)
    if layer and base:
        for face in bm.faces:
            if face[layer] and len(face.verts) == 3:
                for loop in face.loops:
                    if loop.edge[base]:
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
    for face in bm.faces:
        if face != replacing and set(face.verts) == set(verts):
            raise ValueError("That face already exists")
    for a, b in zip(verts, verts[1:] + verts[:1]):
        edge = bm.edges.get((a, b))
        if edge and sum(face != replacing for face in edge.link_faces) >= 2:
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
    """Resample a cursor polyline at the starting edge's width."""
    def __init__(self, start, spacing):
        if spacing <= 1e-8:
            raise ValueError("The starting edge is too short")
        self.spacing = spacing
        self.cursor = start.copy()
        self.remaining = spacing
        self.points = []

    def add(self, point):
        start = self.cursor
        delta = point - start
        length = delta.length
        if length <= 1e-10:
            return
        direction = delta / length
        while length >= self.remaining:
            start = start + direction * self.remaining
            self.points.append(start.copy())
            length -= self.remaining
            self.remaining = self.spacing
        self.remaining -= length
        self.cursor = point.copy()

    def positions(self):
        points = [point.copy() for point in self.points]
        # Avoid a tiny final face when release is very close to a sample.
        if self.spacing - self.remaining > self.spacing * 0.1:
            if not points or (points[-1] - self.cursor).length > self.spacing * 0.1:
                points.append(self.cursor.copy())
        return points

    def copy(self):
        result = StripPath(self.cursor, self.spacing)
        result.remaining = self.remaining
        result.points = [point.copy() for point in self.points]
        return result


def quad_strip(bm, anchor, points, project=None, target=None):
    """Extrude a whole stroke, rolling back geometry if any quad is invalid."""
    if not isinstance(anchor, bmesh.types.BMEdge) or anchor.hide or len(anchor.link_faces) > 1:
        raise ValueError("Select a boundary or loose edge to draw a strip")
    original = set(bm.verts)
    original_edges = set(bm.edges)
    faces = []
    edge = anchor
    try:
        for point in points:
            face = extrude_quad(bm, edge, point, project=project, finalize=False)
            faces.append(face)
            corners = list(face.verts)
            edge = bm.edges.get((corners[2], corners[3]))
        if target is not None:
            face = bridge(bm, edge, target, finalize=False, repair_winding=False)
            if edge.link_faces:
                source = next((f for f in edge.link_faces if f != face), None)
                if source is not None:
                    face.material_index = source.material_index
            faces.append(face)
            edge = target
    except Exception:
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
    if quad_only and isinstance(anchor, bmesh.types.BMEdge) and not pending(bm):
        return extrude_quad(bm, anchor, vertex.co, target=vertex)
    # Adding a custom-data layer can invalidate existing wrappers in that domain.
    anchor_verts = tuple(anchor.verts) if isinstance(anchor, bmesh.types.BMEdge) else None
    face_layer = bm.faces.layers.int.get(PENDING) or bm.faces.layers.int.new(PENDING)
    edge_layer = bm.edges.layers.int.get(BASE) or bm.edges.layers.int.new(BASE)
    if anchor_verts:
        anchor = bm.edges.get(anchor_verts)
    state = pending(bm)
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
        select(bm, edge)
        return edge
    select(bm, vertex)
    return vertex


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
        edges = bmesh.ops.connect_vert_pair(
            bm, verts=[start, end], faces_exclude=[f for f in bm.faces if f.hide]
        )['edges']
        if not edges:
            raise ValueError("No face path for this cut; click an intermediate edge")
        result = edges
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


def apply_click(bm, anchor, hit, point=None, edge_factor=0.5, bridge_mode=False, project=None, quad_only=False, snap_distance=None, snapped_points=None):
    original = set(bm.verts)
    result = _apply_click(bm, anchor, hit, point, edge_factor, bridge_mode, quad_only)
    if project is not None:
        positions = [(v, project(v.co)) for v in bm.verts if v not in original]
        for vertex, position in positions:
            vertex.co = position
    if quad_only and snap_distance is not None and isinstance(result, bmesh.types.BMFace):
        result = snap_quad(bm, result, original, snap_distance, snapped_points)
    return result


def _apply_click(bm, anchor, hit, point=None, edge_factor=0.5, bridge_mode=False, quad_only=False):
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
        select(bm, hit)
        return hit
    if hit is None and point is None:
        raise ValueError("Cursor is outside the reference surface")
    if quad_only and isinstance(anchor, bmesh.types.BMEdge) and not pending(bm):
        return extrude_quad(bm, anchor, hit.co if hit is not None else point, target=hit)
    return extend(bm, anchor, hit if hit is not None else bm.verts.new(point), quad_only)


def merge_vertices(bm, source, target, discard_pending=False):
    if source != target:
        clear_pending(bm, discard=discard_pending)
        bmesh.ops.weld_verts(bm, targetmap={source: target})
    select(bm, target)
    return target


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
