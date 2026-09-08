"""BMesh operations shared by the interactive tool and regression tests."""
import bmesh

PENDING = "retopo_pool_pending"
BASE = "retopo_pool_base"


def clear_pending(bm):
    for collection, name in ((bm.faces, PENDING), (bm.edges, BASE)):
        layer = collection.layers.int.get(name)
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


def extend(bm, anchor, vertex):
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
        clear_pending(bm)
        face[face_layer] = 1
        anchor[edge_layer] = 1
        select(bm, anchor)
        return face
    clear_pending(bm)
    if isinstance(anchor, bmesh.types.BMVert) and anchor != vertex:
        edge = bm.edges.get((anchor, vertex)) or bm.edges.new((anchor, vertex))
        select(bm, edge)
        return edge
    select(bm, vertex)
    return vertex


def bridge(bm, source, target):
    if source == target or len(source.link_faces) > 1 or len(target.link_faces) > 1:
        raise ValueError("Choose another boundary or loose edge")
    a, b = oriented(source, sum((v.co for v in target.verts), target.verts[0].co * 0) / 2)
    c, d = target.verts
    shared = set(source.verts) & set(target.verts)
    if shared:
        verts = [a, b, next(v for v in target.verts if v not in shared)]
    else:
        if (b.co - d.co).length_squared + (a.co - c.co).length_squared < (b.co - c.co).length_squared + (a.co - d.co).length_squared:
            c, d = d, c
        verts = [a, b, c, d]
    check_face(bm, verts)
    # Adjacent existing faces must traverse a shared edge in opposite directions.
    if target.link_faces:
        loop = next(loop for loop in target.link_faces[0].loops if loop.edge == target)
        pairs = list(zip(verts, verts[1:] + verts[:1]))
        if (loop.vert, loop.link_loop_next.vert) in pairs:
            raise ValueError("Target edge has incompatible face winding")
    face = bm.faces.new(verts)
    clear_pending(bm)
    select(bm, target)
    return face


def connect_cut(bm, start, end):
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
    clear_pending(bm)
    select(bm, end)
    return result


def apply_click(bm, anchor, hit, point=None, edge_factor=0.5, bridge_mode=False, project=None):
    original = set(bm.verts) if project is not None else None
    result = _apply_click(bm, anchor, hit, point, edge_factor, bridge_mode)
    if project is not None:
        positions = [(v, project(v.co)) for v in bm.verts if v not in original]
        for vertex, position in positions:
            vertex.co = position
    return result


def _apply_click(bm, anchor, hit, point=None, edge_factor=0.5, bridge_mode=False):
    if isinstance(hit, bmesh.types.BMEdge):
        if bridge_mode or (isinstance(anchor, bmesh.types.BMEdge) and anchor != hit):
            if not isinstance(anchor, bmesh.types.BMEdge):
                raise ValueError("Select a starting edge to bridge")
            if pending(bm):
                raise ValueError("Commit the triangle before bridging")
            return bridge(bm, anchor, hit)
        clear_pending(bm)
        _, vertex = bmesh.utils.edge_split(hit, hit.verts[0], max(0.001, min(0.999, edge_factor)))
        if isinstance(anchor, bmesh.types.BMVert):
            return connect_cut(bm, anchor, vertex)
        select(bm, vertex)
        return vertex
    if isinstance(hit, bmesh.types.BMVert) and isinstance(anchor, bmesh.types.BMVert):
        return connect_cut(bm, anchor, hit)
    if isinstance(hit, bmesh.types.BMVert) and isinstance(anchor, bmesh.types.BMEdge) and hit in anchor.verts:
        clear_pending(bm)
        select(bm, hit)
        return hit
    if hit is None and point is None:
        raise ValueError("Cursor is outside the reference surface")
    return extend(bm, anchor, hit if hit is not None else bm.verts.new(point))


def merge_vertices(bm, source, target):
    if source != target:
        clear_pending(bm)
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


def loop_cut(bm, edge, factor, project=None):
    starts, faces, points = loop_plan(edge, factor, project)
    clear_pending(bm)
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
