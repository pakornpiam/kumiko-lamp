#!/usr/bin/env python3
"""
Parametric kumiko lamp generator.

Produces a set of watertight, manifold STL parts that assemble into a square
four-panel kumiko lantern with mortise-and-groove joinery.  Every part prints
flat on the bed with no supports.

    python3 kumiko_lamp.py --all              # regenerate every STL + SVG
    python3 kumiko_lamp.py --pattern kikkou   # just the default part set
    python3 kumiko_lamp.py --size 150 --height 180

Geometry is built with real CSG (trimesh + manifold3d), not overlapping shells,
so slicers receive clean solids that need no auto-repair.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

EPS = 1e-3          # nudge used to make through-cuts unambiguous


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------

@dataclass
class Params:
    """Every dimension of the lamp.  Millimetres throughout."""

    # --- overall ---------------------------------------------------------
    size: float = 180.0          # post-to-post outer square of the lantern body
    height: float = 210.0        # panel and post length

    # --- printer ---------------------------------------------------------
    bed: tuple = (256.0, 256.0, 256.0)
    nozzle: float = 0.4

    # --- corner posts ----------------------------------------------------
    post: float = 18.0           # square cross-section
    post_chamfer: float = 2.0    # chamfer on the outward-facing vertical edge

    # --- panels ----------------------------------------------------------
    panel_t: float = 4.0         # panel thickness
    panel_border: float = 12.0   # frame width around the lattice
    slat_w: float = 1.6          # lattice slat width == 4 walls at 0.4 nozzle
    grid: float = 28.0           # pattern pitch (triangle side / hex radius)

    # --- diffuser rebate -------------------------------------------------
    rebate_d: float = 0.6        # pocket depth on the back face
    rebate_lip: float = 8.0      # pocket inset from the panel outer edge

    # --- joinery ---------------------------------------------------------
    groove_d: float = 6.0        # how deep panels sit into posts/base/cap
    slot_clear: float = 0.4      # groove width minus panel thickness
    panel_clear: float = 0.3     # total width clearance for the panel
    socket_clear: float = 0.4    # post socket size minus post size

    # --- base and cap ----------------------------------------------------
    plinth: float = 5.0          # reveal of base/cap beyond the post faces
    base_t: float = 16.0
    cap_t: float = 10.0

    # --- legs ------------------------------------------------------------
    # Separate parts rather than moulded onto the base: hung off the underside
    # they would turn the whole slab into an unsupported ceiling, and this lamp
    # prints every part without supports.
    leg: float = 20.0            # square cross-section
    leg_h: float = 12.0          # stand-off below the base
    leg_tenon: float = 10.0      # square tenon plugging into the base
    leg_tenon_h: float = 8.0     # tenon length == socket depth
    leg_clear: float = 0.35      # socket size minus tenon size

    # --- E27 lamp holder -------------------------------------------------
    socket_bore: float = 40.0        # clear bore through the base
    socket_cbore: float = 50.0       # counterbore that seats the adapter ring
    socket_cbore_d: float = 4.0
    socket_neck: float = 26.5        # adapter ring inner diameter (measure yours)
    cable_w: float = 9.0             # cord tunnel through the wall of the base
    cable_h: float = 5.0
    cable_floor: float = 2.0         # material under the tunnel, so the base
                                     # keeps a perfectly flat underside

    # --- ventilation -----------------------------------------------------
    base_vents: int = 16
    base_vent_r0: float = 29.0
    base_vent_r1: float = 37.0
    cap_vent_d: float = 70.0
    cap_grille_f: float = 0.75   # grille pitch as a fraction of `grid`

    # --- optional two-piece post ----------------------------------------
    pin_d: float = 8.0
    pin_len: float = 12.0
    pin_clear: float = 0.25

    # --- meshing ---------------------------------------------------------
    arc: int = 96                # segments per full circle

    # ---- derived ---------------------------------------------------------
    @property
    def slot_w(self) -> float:
        """Width of every groove that receives a panel."""
        return self.panel_t + self.slot_clear

    @property
    def post_center(self) -> float:
        """|x| == |y| of a post axis."""
        return self.size / 2.0 - self.post / 2.0

    @property
    def groove_span(self) -> float:
        """
        Distance between the bottoms of the two facing post grooves.

        Each groove is cut into the post's inner face (at post/2 from the axis)
        and runs `groove_d` further inward, so its bottom sits at
        post_center - (post/2 - groove_d) from the lamp axis.
        """
        return 2.0 * (self.post_center - (self.post / 2.0 - self.groove_d))

    @property
    def panel_w(self) -> float:
        """Panel width: groove bottom to groove bottom, less clearance."""
        return self.groove_span - self.panel_clear

    @property
    def foot(self) -> float:
        """Outer square of the base and cap."""
        return self.size + 2.0 * self.plinth

    @property
    def socket_sz(self) -> float:
        return self.post + self.socket_clear

    @property
    def leg_socket_sz(self) -> float:
        """Square socket in the base underside that receives a leg tenon."""
        return self.leg_tenon + self.leg_clear

    @property
    def total_height(self) -> float:
        return (self.leg_h + self.base_t + (self.height - 2 * self.groove_d)
                + self.cap_t)


# --------------------------------------------------------------------------
# Mesh primitives
# --------------------------------------------------------------------------

def box(x0, y0, z0, x1, y1, z1) -> trimesh.Trimesh:
    """Axis-aligned box from two opposite corners."""
    extents = (x1 - x0, y1 - y0, z1 - z0)
    if min(extents) <= 0:
        raise ValueError(f"degenerate box {extents}")
    T = trimesh.transformations.translation_matrix(
        ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    return trimesh.creation.box(extents=extents, transform=T)


def cyl(d, z0, z1, x=0.0, y=0.0, sections=96) -> trimesh.Trimesh:
    """Z-aligned cylinder spanning z0..z1."""
    T = trimesh.transformations.translation_matrix((x, y, (z0 + z1) / 2))
    return trimesh.creation.cylinder(radius=d / 2.0, height=z1 - z0,
                                     sections=sections, transform=T)


def slat_box(p0, p1, w, z0, z1) -> trimesh.Trimesh:
    """A lattice slat: a box of width `w` swept from p0 to p1 in the XY plane."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        raise ValueError("zero-length slat")
    T = trimesh.transformations.translation_matrix(
        ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    R = trimesh.transformations.rotation_matrix(math.atan2(dy, dx), (0, 0, 1))
    return trimesh.creation.box(extents=(length, w, z1 - z0), transform=T @ R)


def union(meshes) -> trimesh.Trimesh:
    meshes = [m for m in meshes if m is not None]
    if len(meshes) == 1:
        return meshes[0]
    return trimesh.boolean.union(meshes, engine="manifold")


def difference(a, cutters) -> trimesh.Trimesh:
    cutters = [c for c in cutters if c is not None]
    if not cutters:
        return a
    return trimesh.boolean.difference([a] + cutters, engine="manifold")


def intersection(a, b) -> trimesh.Trimesh:
    return trimesh.boolean.intersection([a, b], engine="manifold")


def split_bodies(mesh):
    """
    Split a mesh into connected components.

    trimesh.split() routes through networkx; this needs only scipy, which
    trimesh already depends on, and skips the hole-filling repair we do not
    want applied to boolean output.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(mesh.faces)
    if n == 0:
        return []
    adj = mesh.face_adjacency
    if len(adj) == 0:
        labels = np.arange(n)
        count = n
    else:
        data = np.ones(len(adj), dtype=np.int8)
        graph = coo_matrix((data, (adj[:, 0], adj[:, 1])), shape=(n, n))
        count, labels = connected_components(graph, directed=False)

    bodies = []
    for i in range(count):
        faces = mesh.faces[labels == i]
        body = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=faces,
                               process=False)
        body.remove_unreferenced_vertices()
        bodies.append(body)
    return bodies


def cleanup(mesh, min_volume=1e-3) -> trimesh.Trimesh:
    """
    Drop the zero-volume slivers a boolean leaves behind.

    Unioning slats whose side faces land exactly coplanar produces degenerate
    zero-thickness shells.  They add no material, but they are extra shells in
    the STL and some slicers flag them, so strip anything without real volume.

    Only whole connected components are discarded.  Deleting individual
    degenerate faces would tear holes in the shell they belong to, which costs
    watertightness for no gain.
    """
    # Snap coordinates to a 0.1 micron grid and re-weld.  Booleans leave pairs
    # of vertices that should coincide differing by ~1e-7; the index-based mesh
    # is still closed, but STL carries no indices, so on reload those pairs fail
    # to weld and the part comes back with unpaired edges.  Adding 0.0 also
    # normalises the -0.0 that rounding can produce, which hashes separately
    # from +0.0.  The grid is far finer than any printable feature.
    mesh.vertices = np.round(mesh.vertices, 4) + 0.0
    mesh.merge_vertices()

    bodies = split_bodies(mesh)
    if len(bodies) <= 1:
        return mesh

    keep = []
    for b in bodies:
        v = b.volume
        if np.isfinite(v) and abs(v) > min_volume:
            keep.append(b)
    if not keep:
        return mesh
    return keep[0] if len(keep) == 1 else trimesh.util.concatenate(keep)


# --------------------------------------------------------------------------
# 2-D segment helpers
# --------------------------------------------------------------------------

def _key(p, q):
    """Order-independent, tolerance-tolerant key for a segment."""
    a = (round(p[0], 4), round(p[1], 4))
    b = (round(q[0], 4), round(q[1], 4))
    return (a, b) if a <= b else (b, a)


class SegSet:
    """Accumulates unique 2-D segments."""

    def __init__(self):
        self._seen = set()
        self.segs = []

    def add(self, p, q):
        if math.hypot(q[0] - p[0], q[1] - p[1]) < 1e-6:
            return
        k = _key(p, q)
        if k in self._seen:
            return
        self._seen.add(k)
        self.segs.append((p, q))


def clip_rect(segs, x0, y0, x1, y1):
    """Liang-Barsky clip of every segment to an axis-aligned rectangle."""
    out = []
    for (px, py), (qx, qy) in segs:
        dx, dy = qx - px, qy - py
        t0, t1 = 0.0, 1.0
        ok = True
        for p, q in ((-dx, px - x0), (dx, x1 - px), (-dy, py - y0), (dy, y1 - py)):
            if abs(p) < 1e-12:
                if q < 0:
                    ok = False
                    break
                continue
            r = q / p
            if p < 0:
                if r > t1:
                    ok = False
                    break
                t0 = max(t0, r)
            else:
                if r < t0:
                    ok = False
                    break
                t1 = min(t1, r)
        if not ok or t1 - t0 < 1e-9:
            continue
        a = (px + t0 * dx, py + t0 * dy)
        b = (px + t1 * dx, py + t1 * dy)
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-6:
            out.append((a, b))
    return out


def clip_circle(segs, cx, cy, r):
    """Clip segments to a disc."""
    out = []
    for (px, py), (qx, qy) in segs:
        dx, dy = qx - px, qy - py
        fx, fy = px - cx, py - cy
        a = dx * dx + dy * dy
        b = 2 * (fx * dx + fy * dy)
        c = fx * fx + fy * fy - r * r
        disc = b * b - 4 * a * c
        if disc <= 0:
            continue
        s = math.sqrt(disc)
        t0 = max(0.0, (-b - s) / (2 * a))
        t1 = min(1.0, (-b + s) / (2 * a))
        if t1 - t0 < 1e-9:
            continue
        p = (px + t0 * dx, py + t0 * dy)
        q = (px + t1 * dx, py + t1 * dy)
        if math.hypot(q[0] - p[0], q[1] - p[1]) > 1e-6:
            out.append((p, q))
    return out


# --------------------------------------------------------------------------
# Kumiko pattern generators
#
# Signature is f(w, h, s) -> [((x0, y0), (x1, y1)), ...], centred on the origin,
# where w x h is the *actual* opening to be filled and s is the pattern pitch.
# Periodic patterns overshoot the opening internally so the caller can clip a
# little beyond it; bordered patterns rely on w and h being exact.
# --------------------------------------------------------------------------

def _tri_vertices(i, k, s):
    h = s * math.sqrt(3.0) / 2.0
    return (i * s + (k % 2) * (s / 2.0), k * h)


def _triangles(w, h, s):
    """
    Equilateral triangles tiling a w x h region centred on the origin.

    Generation overshoots the region by a couple of cells so callers can clip
    to the exact opening (or slightly beyond it) without losing edge detail.
    """
    rh = s * math.sqrt(3.0) / 2.0
    k0 = int(math.floor(-h / 2 / rh)) - 2
    k1 = int(math.ceil(h / 2 / rh)) + 2
    i0 = int(math.floor(-w / 2 / s)) - 3
    i1 = int(math.ceil(w / 2 / s)) + 3
    for k in range(k0, k1 + 1):
        for i in range(i0, i1 + 1):
            v = lambda ii, kk: _tri_vertices(ii, kk, s)
            if k % 2 == 0:
                yield (v(i, k), v(i + 1, k), v(i, k + 1))
                yield (v(i, k + 1), v(i + 1, k + 1), v(i + 1, k))
            else:
                yield (v(i, k), v(i + 1, k), v(i + 1, k + 1))
                yield (v(i, k + 1), v(i + 1, k + 1), v(i, k))


def pat_mitsukude(w, h, s):
    """Plain three-way triangular lattice."""
    ss = SegSet()
    for tri in _triangles(w, h, s):
        for j in range(3):
            ss.add(tri[j], tri[(j + 1) % 3])
    return ss.segs


def pat_asanoha(w, h, s):
    """Asanoha: triangular grid plus vertex-to-centroid spokes."""
    ss = SegSet()
    for tri in _triangles(w, h, s):
        for j in range(3):
            ss.add(tri[j], tri[(j + 1) % 3])
        cx = sum(p[0] for p in tri) / 3.0
        cy = sum(p[1] for p in tri) / 3.0
        for p in tri:
            ss.add(p, (cx, cy))
    return ss.segs


def _hexes(w, h, r):
    """Pointy-top hexagon centres and vertices covering w x h."""
    col_w = math.sqrt(3.0) * r
    row_h = 1.5 * r
    c0 = int(math.floor(-w / 2 / col_w)) - 2
    c1 = int(math.ceil(w / 2 / col_w)) + 2
    r0 = int(math.floor(-h / 2 / row_h)) - 2
    r1 = int(math.ceil(h / 2 / row_h)) + 2
    for row in range(r0, r1 + 1):
        for col in range(c0, c1 + 1):
            cx = col * col_w + (row % 2) * col_w / 2.0
            cy = row * row_h
            verts = [(cx + r * math.cos(math.radians(90 + 60 * j)),
                      cy + r * math.sin(math.radians(90 + 60 * j)))
                     for j in range(6)]
            yield (row, col), (cx, cy), verts


def pat_kikkou(w, h, r):
    """
    Kikkou (tortoiseshell): hexagons with a Y-spoke to alternate vertices.

    Flipping the spoke orientation on alternate cells gives the traditional
    tumbling-block relief.  Far fewer, longer slats than asanoha, so this is
    the quickest and strongest of the four to print.
    """
    ss = SegSet()
    for (row, col), c, verts in _hexes(w, h, r):
        for j in range(6):
            ss.add(verts[j], verts[(j + 1) % 6])
        start = 0 if (row + col) % 2 == 0 else 1
        for j in (start, start + 2, start + 4):
            ss.add(c, verts[j % 6])
    return ss.segs


def pat_kawari(w, h, s):
    """
    Asanoha field framed by a plain ladder border band.

    Everything that meets the boundary line overlaps it by `ov` rather than
    stopping on it.  A slat that merely abuts another touches along a single
    tangent edge, and that unions into geometry which will not survive the
    float32 STL round-trip.
    """
    band = s * 0.6
    ov = s * 0.06
    ix0, iy0 = -w / 2 + band, -h / 2 + band
    ix1, iy1 = w / 2 - band, h / 2 - band
    segs = clip_rect(pat_asanoha(w, h, s),
                     ix0 - ov, iy0 - ov, ix1 + ov, iy1 + ov)

    ss = SegSet()
    # single boundary line separating the field from the band
    ss.add((ix0, iy0), (ix1, iy0))
    ss.add((ix1, iy0), (ix1, iy1))
    ss.add((ix1, iy1), (ix0, iy1))
    ss.add((ix0, iy1), (ix0, iy0))

    # ladder rungs across the band, coarse enough to contrast with the field
    pitch = s * 0.85
    nx = max(2, int(round((ix1 - ix0) / pitch)))
    for j in range(nx + 1):
        x = ix0 + (ix1 - ix0) * j / nx
        ss.add((x, iy1 - ov), (x, h / 2))
        ss.add((x, -h / 2), (x, iy0 + ov))
    ny = max(2, int(round((iy1 - iy0) / pitch)))
    for j in range(ny + 1):
        y = iy0 + (iy1 - iy0) * j / ny
        ss.add((-w / 2, y), (ix0 + ov, y))
        ss.add((ix1 - ov, y), (w / 2, y))
    return segs + ss.segs


def _extend(p, q, d):
    """
    Lengthen a segment by `d` at both ends.

    Needed wherever a pattern puts two collinear slats end to end: butted
    against each other they share a single tangent face, which is the one thing
    the float32 STL round-trip does not survive.  Overlapping them costs
    nothing and the union comes back clean.
    """
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return p, q
    ux, uy = dx / L * d, dy / L * d
    return (p[0] - ux, p[1] - uy), (q[0] + ux, q[1] + uy)


def pat_kagome(w, h, s):
    """
    Kagome (basket weave): the medial lattice of the triangular grid.

    Joining the three edge midpoints of every triangle gives the net directly --
    each midpoint is shared by two triangles, so the pieces line up into the
    three continuous straight families that read as woven bamboo.  Those
    collinear neighbours meet end to end, hence the overshoot.
    """
    ss = SegSet()
    ov = s * 0.04
    for tri in _triangles(w, h, s):
        m = [((tri[j][0] + tri[(j + 1) % 3][0]) / 2.0,
              (tri[j][1] + tri[(j + 1) % 3][1]) / 2.0) for j in range(3)]
        for j in range(3):
            ss.add(*_extend(m[j], m[(j + 1) % 3], ov))
    return ss.segs


def pat_masu(w, h, s):
    """
    Masu-tsunagi (linked boxes): a square grid with a concentric inner square
    in every cell, bridged to the cell walls at the four side midpoints.

    The bridges are what keep it in one piece once `build_cap` clips the field
    to a disc -- an inner square cut loose from the grid would be a floating
    body and fail the part check.
    """
    ss = SegSet()
    ov = s * 0.05
    inset = s * 0.28
    i0 = int(math.floor(-w / 2 / s)) - 2
    i1 = int(math.ceil(w / 2 / s)) + 2
    j0 = int(math.floor(-h / 2 / s)) - 2
    j1 = int(math.ceil(h / 2 / s)) + 2

    # grid lines run the full field, so every crossing is a real overlap
    for i in range(i0, i1 + 1):
        ss.add((i * s, j0 * s), (i * s, j1 * s))
    for j in range(j0, j1 + 1):
        ss.add((i0 * s, j * s), (i1 * s, j * s))

    for i in range(i0, i1):
        for j in range(j0, j1):
            x0, y0 = i * s, j * s
            a, b = x0 + inset, y0 + inset
            c, d = x0 + s - inset, y0 + s - inset
            ss.add((a, b), (c, b))
            ss.add((c, b), (c, d))
            ss.add((c, d), (a, d))
            ss.add((a, d), (a, b))
            mx, my = x0 + s / 2.0, y0 + s / 2.0
            ss.add((mx, y0 - ov), (mx, b + ov))
            ss.add((mx, d - ov), (mx, y0 + s + ov))
            ss.add((x0 - ov, my), (a + ov, my))
            ss.add((c - ov, my), (x0 + s + ov, my))
    return ss.segs


def pat_goma(w, h, s):
    """
    Goma-gara (sesame husk): a 45 deg diamond lattice with both axes of every
    diamond drawn in, so each cell reads as a split sesame seed.

    Cell centres sit at (u, v) * s/2 for u + v odd -- that is exactly the set of
    intersections of the two diagonal families offset by half a cell.
    """
    ss = SegSet()
    ov = s * 0.05
    half = s / 2.0
    r = max(w, h) / 2.0 + 3 * s
    n = int(math.ceil(2 * r / s)) + 2

    # the two 45 deg families, drawn long so all crossings genuinely overlap
    for k in range(-n, n + 1):
        c = k * s
        ss.add((c / 2 - r, c / 2 + r), (c / 2 + r, c / 2 - r))     # x + y = c
        ss.add((c / 2 - r, -c / 2 - r), (c / 2 + r, -c / 2 + r))   # x - y = c

    for u in range(-n, n + 1):
        for v in range(-n, n + 1):
            if (u + v) % 2 == 0:
                continue
            cx, cy = u * half, v * half
            if abs(cx) > r or abs(cy) > r:
                continue
            ss.add((cx - half - ov, cy), (cx + half + ov, cy))
            ss.add((cx, cy - half - ov), (cx, cy + half + ov))
    return ss.segs


def pat_bishamon(w, h, r):
    """
    Bishamon-kikkou: the armour pattern -- a concentric hexagon inside every
    cell, tied to the outer one by six radial spokes, so the field reads as
    overlapping scales rather than kikkou's tumbling blocks.

    This is the heavy end of the hexagon family; kikkou is the light one.

    The spokes run between edge *midpoints*, not vertices.  Aimed at a vertex
    they land where three hexagons and six slats already converge, and the
    boolean there produces a micron-long sliver that costs watertightness; a
    midpoint is a clean T-overlap with one edge and nothing else nearby.
    """
    ss = SegSet()
    ov = r * 0.06
    mid = lambda p, q: ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
    for _rc, c, verts in _hexes(w, h, r):
        inner = [(c[0] + (v[0] - c[0]) * 0.55,
                  c[1] + (v[1] - c[1]) * 0.55) for v in verts]
        for j in range(6):
            ss.add(verts[j], verts[(j + 1) % 6])
            ss.add(inner[j], inner[(j + 1) % 6])
            ss.add(*_extend(mid(inner[j], inner[(j + 1) % 6]),
                            mid(verts[j], verts[(j + 1) % 6]), ov))
    return ss.segs


# --------------------------------------------------------------------------
# Lai Thai (ลายไทย) patterns
#
# Unlike every kumiko pattern above, these are curvilinear: they are stroked as
# polylines rather than laid out on a lattice.  That costs far more in the
# browser core than it does here -- see the note in CLAUDE.md -- so the
# tessellation counts below are deliberately frugal.
# --------------------------------------------------------------------------

def _stroke(ss, pts, ov):
    """Add a polyline as a chain of overlapping slats."""
    for i in range(len(pts) - 1):
        ss.add(*_extend(pts[i], pts[i + 1], ov))


def _cubic(p0, p1, p2, p3, n):
    """Cubic Bezier, sampled at n+1 points."""
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        out.append((u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
                    u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1]))
    return out


def _coil(cx, cy, R, turns, n, sign, a0=-math.pi / 2, tight=0.15):
    """
    A volute centred on (cx, cy), starting at radius R and winding inward.

    Parametrised by the *outer* radius, not the inner one: a log spiral grown
    outward from a small r0 is exponential in the turn count and sprawls right
    out of whatever is meant to contain it.  Winding inward from R bounds the
    whole coil inside R.
    """
    T = 2.0 * math.pi * turns
    k = -math.log(tight) / T
    return [(cx + R*math.exp(-k*T*i/n)*math.cos(a0 + sign*T*i/n),
             cy + R*math.exp(-k*T*i/n)*math.sin(a0 + sign*T*i/n))
            for i in range(n + 1)]


# Proportions of one kranok leaf, as fractions of its own base-to-tip length.
LEAF_BELLY = 2.4        # how far the outer edge bulges
LEAF_SHOULDER = 0.9     # where it starts converging on the tip
LEAF_CURL = 0.26        # lateral offset of the tip, which gives it the hook


def _leaf(ss, base, tip, ov, n, bow=1, wide=0.15, eye=True, eyescale=1.0):
    """
    One kranok leaf: a closed outline with a convex outer sweep, a concave
    inner return, a sharp hooked tip, and a volute coiled in its base.

    Local frame runs base -> tip; `bow` picks which side the belly falls on, so
    the same leaf mirrors without a separate code path.
    """
    bx, by = base
    dx, dy = tip[0] - bx, tip[1] - by
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return
    uy = (dx / L, dy / L)
    ux = (-uy[1] * bow, uy[0] * bow)

    def P(a, c):
        return (bx + ux[0]*a*L + uy[0]*c*L, by + ux[1]*a*L + uy[1]*c*L)

    wd = wide
    T = P(-LEAF_CURL, 1.0)
    _stroke(ss, _cubic(P(wd, 0.05), P(wd*LEAF_BELLY, 0.30),
                       P(wd*LEAF_SHOULDER, 0.74), T, n), ov)
    _stroke(ss, _cubic(T, P(-wd*0.30, 0.62), P(-wd*0.78, 0.30),
                       P(-wd*0.70, 0.05), n), ov)
    _stroke(ss, _cubic(P(-wd*0.70, 0.05), P(-wd*0.58, -0.08),
                       P(wd*0.58, -0.08), P(wd, 0.05), max(4, n // 2)), ov)
    if eye:
        # The coil has to be tied to the leaf, not just sit inside it.  Centred
        # on its own it touches nothing and the panel comes back as two bodies.
        Rl = wd * 0.60 * eyescale
        root = P(wd * 0.12, 0.055)
        cen = P(wd * 0.12, 0.055 + Rl)
        a0 = math.atan2(root[1] - cen[1], root[0] - cen[0])
        pts = _coil(cen[0], cen[1], Rl * L, 1.7, 15, bow, a0, tight=0.15)
        _stroke(ss, pts, ov)
        _stroke(ss, [pts[0], P(wd * 0.12, -0.05)], ov)      # stem down to the base


def _mandorla(ss, cx, cy, hw, hh, ov, n, p):
    """
    A pointed oval: two arcs meeting at a sharp point top and bottom.  The
    kranok medallion's central column is a stack of these; `_leaf` cannot
    express one because a leaf has a rounded base, not a second point.
    """
    b, t = p(cx, cy - hh), p(cx, cy + hh)
    for sgn in (1, -1):
        _stroke(ss, _cubic(b, p(cx + sgn*hw, cy - hh*0.42),
                           p(cx + sgn*hw, cy + hh*0.42), t, n), ov)


def _phut_tan_petal(ss, p, a, r0, r1, half, ov, n, curl=0.0):
    """One pointed, softly asymmetric petal in a radial Phut Tan blossom."""
    def q(r, da=0.0):
        aa = a + da
        return p(r * math.cos(aa), r * math.sin(aa))

    left, tip, right = q(r0, -half), q(r1, curl), q(r0, half)
    shoulder = r0 + (r1 - r0) * 0.58
    _stroke(ss, _cubic(left, q(shoulder, -half * 1.15),
                       q(r1 * 0.92, -half * 0.42 + curl), tip, n), ov)
    _stroke(ss, _cubic(tip, q(r1 * 0.92, half * 0.42 + curl),
                       q(shoulder, half * 1.15), right, n), ov)
    # Close across, and slightly through, the supporting ring.  Merely touching
    # the ring at the two endpoints is not robust after STL float32 export.
    _stroke(ss, [right, q(r0 * 0.88), left], ov)


def pat_dok_phut_tan(w, h, s):
    """
    Dok Phut Tan (ดอกพุดตาน): a panel-sized floral composition based on the
    layered, peony-like blossom used in early-Rattanakosin Thai ornament.

    The blossom has three overlapping petal tiers.  Four diagonal kan khot
    stems bury themselves in the frame and carry paired leaves, making every
    decorative stroke part of one printable body.  Like the kranok panel this
    is a composition, so ``s`` controls curve tessellation rather than pitch.
    """
    ss = SegSet()
    ov = 0.010 * min(w, h)
    n = int(max(7, min(14, round(300.0 / s))))
    p = lambda u, v: (u * w, v * h)

    # Concentric structural rings are both the flower's layered heart and the
    # reliable common root for all petals and stems.
    for ru, rv, count, phase in ((0.245, 0.175, 12, math.pi / 12),
                                 (0.155, 0.112, 8, 0.0),
                                 (0.075, 0.055, 6, math.pi / 6)):
        ring = [p(ru * math.cos(2*math.pi*i/32),
                  rv * math.sin(2*math.pi*i/32)) for i in range(33)]
        _stroke(ss, ring, ov)
        for i in range(count):
            a = phase + 2 * math.pi * i / count
            # Work in normalised coordinates; p then gives the flower the
            # broad horizontal proportion seen in carved Phut Tan flowers.
            scale = ru / 0.245
            pp = lambda x, y: p(x, y * (rv / ru))
            _phut_tan_petal(ss, pp, a, ru * 0.82,
                            ru + (0.105 if count == 12 else 0.070 * scale),
                            math.pi / count * 0.72, ov, n,
                            (0.018 if i % 2 else -0.018))

    # Four scrolling stems reach beyond the opening and therefore weld the
    # whole flower into the frame.  Paired leaves are rooted directly on them.
    for m in (1, -1):
        for v in (1, -1):
            stem = _cubic(p(m*0.18, v*0.08), p(m*0.30, v*0.14),
                          p(m*0.37, v*0.36), p(m*0.53, v*0.54), n + 2)
            _stroke(ss, stem, ov)
            for t, side in ((0.38, 1), (0.62, -1)):
                j = min(len(stem) - 2, max(1, round(t * (len(stem) - 1))))
                base = stem[j]
                dx, dy = stem[j+1][0] - stem[j-1][0], stem[j+1][1] - stem[j-1][1]
                L = math.hypot(dx, dy)
                nx, ny = -dy / L * side, dx / L * side
                tip = (base[0] + nx * 0.105*w + dx/L * 0.035*w,
                       base[1] + ny * 0.075*h + dy/L * 0.025*h)
                _leaf(ss, base, tip, ov, max(7, n - 2), side,
                      wide=0.19, eye=False)
    return ss.segs


# One side of the medallion, mirrored about the vertical axis at emit time.
# base, tip, bow, leaf width, volute, volute scale
_KRANOK_SIDE = [
    ((0.09, -0.06), (0.35,  0.20), -1, 0.150, True,  1.30),   # waist volute flame
    ((0.26, -0.13), (0.47,  0.02), -1, 0.140, False, 1.0),    # side-vertex flame
    ((0.06,  0.22), (0.20,  0.44), -1, 0.145, False, 1.0),    # upper edge, inner
    ((0.17,  0.10), (0.33,  0.33), -1, 0.140, False, 1.0),    # upper edge, outer
    ((0.08, -0.20), (0.24, -0.38),  1, 0.148, False, 1.0),    # lower edge, inner
    ((0.21, -0.16), (0.40, -0.24),  1, 0.140, False, 1.0),    # lower edge, outer
]

# Central column on the axis: centre, half-width, half-height.
_KRANOK_COLUMN = [
    (0.0,  0.30, 0.075, 0.21),   # top spike, to the apex
    (0.0,  0.02, 0.150, 0.30),   # outer lens
    (0.0,  0.02, 0.090, 0.20),   # middle lens
    (0.0,  0.02, 0.042, 0.11),   # inner lens
    (0.0, -0.30, 0.100, 0.20),   # bottom lobe
]


def pat_kranok_kan_khot(w, h, s):
    """
    Kranok Kan Khot (กระหนกก้านขด): one composition filling the panel, not a
    repeating tile -- a diamond medallion of Thai flame work.

    Bilaterally symmetric, so only the right half is authored and mirrored.
    The diamond's four strokes overshoot their vertices, which is what buries
    the artwork in the frame; a composition connects to nothing by default and
    the single-body check on the panel is what catches that.

    There is no tile here, so `s` has no pitch to set; it drives curve
    tessellation instead, which is the honest thing left for it to do.
    """
    ss = SegSet()
    ov = 0.010 * min(w, h)
    n = int(max(8, min(16, round(360.0 / s))))
    p = lambda u, v: (u * w, v * h)

    vt, vb, vs, e = 0.50, -0.50, -0.02, 0.50
    for m in (1, -1):
        _stroke(ss, [p(0.0, vt + 0.05), p(m * (e + 0.05), vs)], ov)
        _stroke(ss, [p(m * (e + 0.05), vs), p(0.0, vb - 0.05)], ov)

    for cx, cy, hw, hh in _KRANOK_COLUMN:
        _mandorla(ss, cx, cy, hw, hh, ov, n, p)

    for m in (1, -1):
        for (bu, bv), (tu, tv), bow, wide, eye, es in _KRANOK_SIDE:
            _leaf(ss, p(m * bu, bv), p(m * tu, tv), ov, n,
                  bow * m, wide, eye, es)
    return ss.segs


PATTERNS = {
    "asanoha": pat_asanoha,
    "mitsukude": pat_mitsukude,
    "kikkou": pat_kikkou,
    "kawari_asanoha": pat_kawari,
    "kagome": pat_kagome,
    "masu_tsunagi": pat_masu,
    "goma_gara": pat_goma,
    "bishamon_kikkou": pat_bishamon,
    "kranok_kan_khot": pat_kranok_kan_khot,
    "dok_phut_tan": pat_dok_phut_tan,
}

PATTERN_FAMILY = {name: "laithai" if name in {"kranok_kan_khot", "dok_phut_tan"} else "kumiko"
                  for name in PATTERNS}

# build_cap clips the field to a disc, and top_cap has to come back as a single
# body.  A lattice always survives that; a curvilinear motif is not guaranteed
# to, since the clip can cut a curve loose from everything it touched.
#
# kranok_kan_khot is a single panel-sized composition, not a repeating field.
# Squeezed into the vent it survives the clip in one piece, but it lands 540
# slats and ~19.5k triangles in a 70 mm hole and reads as a shrunken copy of
# the panel rather than a grille, so it falls back.  The single-body assertion
# in check_part is the backstop for anything else that opts out.
CAP_FALLBACK = "kikkou"
CAP_UNSAFE = frozenset({"kranok_kan_khot", "dok_phut_tan"})
PATTERN_CAP_SAFE = {name: name not in CAP_UNSAFE for name in PATTERNS}


def cap_pattern(pattern: str) -> str:
    """The pattern the cap grille should actually be built from."""
    return pattern if PATTERN_CAP_SAFE.get(pattern, True) else CAP_FALLBACK


# --------------------------------------------------------------------------
# Parts
# --------------------------------------------------------------------------

def build_panel(P: Params, pattern: str) -> trimesh.Trimesh:
    """
    One kumiko panel, laid flat for printing.

    Local frame: x = width (centred), y = height (0..H), z = thickness (0..T).
    z = 0 is the lattice face and goes on the build plate.
    """
    W, H, T = P.panel_w, P.height, P.panel_t
    b = P.panel_border

    # frame ---------------------------------------------------------------
    outer = box(-W / 2, 0, 0, W / 2, H, T)
    opening = box(-W / 2 + b, b, -EPS, W / 2 - b, H - b, T + EPS)
    frame = difference(outer, [opening])

    # lattice -------------------------------------------------------------
    ow, oh = W - 2 * b, H - 2 * b
    ocy = H / 2.0
    # clip a little past the opening so slats bury themselves in the frame
    grow = 1.0
    segs = PATTERNS[pattern](ow, oh, P.grid)
    segs = clip_rect(segs,
                     -ow / 2 - grow, -oh / 2 - grow,
                     ow / 2 + grow, oh / 2 + grow)

    lat_top = T - P.rebate_d          # lattice stops short of the back face
    slats = [slat_box((p[0], p[1] + ocy), (q[0], q[1] + ocy),
                      P.slat_w, 0.0, lat_top)
             for p, q in segs]
    if not slats:
        raise ValueError(f"pattern {pattern!r} produced no slats")

    panel = union([frame] + slats)

    # diffuser rebate: pocket in the back face, open at the top edge -------
    lip = P.rebate_lip
    pocket = box(-W / 2 + lip, lip, T - P.rebate_d,
                 W / 2 - lip, H + EPS, T + EPS)
    panel = difference(panel, [pocket])
    return cleanup(panel)


def build_post(P: Params, part: str = "full") -> trimesh.Trimesh:
    """
    A corner post, standing up for printing.

    Grooves face +X and +Y; the post is rotated 90 deg per corner on assembly.
    `part` is "full", "lower" or "upper" for the optional two-piece version.
    """
    a = P.post / 2.0
    L = P.height
    solid = box(-a, -a, 0, a, a, L)

    sw = P.slot_w / 2.0
    g_in = a - P.groove_d
    cutters = [
        box(g_in, -sw, -EPS, a + EPS, sw, L + EPS),     # groove on +X face
        box(-sw, g_in, -EPS, sw, a + EPS, L + EPS),     # groove on +Y face
    ]

    # chamfer the outward-facing vertical edge, at (-a, -a)
    if P.post_chamfer > 0:
        c = P.post_chamfer
        T = trimesh.transformations.translation_matrix((-a, -a, L / 2))
        R = trimesh.transformations.rotation_matrix(math.radians(45), (0, 0, 1))
        cutters.append(trimesh.creation.box(
            extents=(c * math.sqrt(2), c * math.sqrt(2), L + 2 * EPS),
            transform=T @ R))

    post = difference(solid, cutters)

    if part == "full":
        return cleanup(post)

    half = L / 2.0
    w = a + 10.0
    if part == "lower":
        post = intersection(post, box(-w, -w, -EPS, w, w, half))
        pin = cyl(P.pin_d, half - EPS, half + P.pin_len, sections=P.arc)
        return cleanup(union([post, pin]))
    if part == "upper":
        post = intersection(post, box(-w, -w, half, w, w, L + EPS))
        hole = cyl(P.pin_d + 2 * P.pin_clear, half - EPS,
                   half + P.pin_len + 0.5, sections=P.arc)
        post = difference(post, [hole])
        # print it standing on its cut face: drop to z = 0
        post.apply_translation((0, 0, -half))
        return cleanup(post)
    raise ValueError(part)


def _joint_cutters(P: Params, z_top: float, downward: bool):
    """
    Post sockets and panel grooves for the base (cut down from z_top) or the
    cap (cut up from z_top).  Returns a list of cutter meshes.
    """
    d = P.groove_d
    if downward:
        z0, z1 = z_top - d, z_top + EPS
    else:
        z0, z1 = z_top - EPS, z_top + d

    cutters = []
    s = P.socket_sz / 2.0
    pc = P.post_center
    for sx in (-1, 1):
        for sy in (-1, 1):
            cutters.append(box(sx * pc - s, sy * pc - s, z0,
                               sx * pc + s, sy * pc + s, z1))

    sw = P.slot_w / 2.0
    for sy in (-1, 1):
        cutters.append(box(-pc, sy * pc - sw, z0, pc, sy * pc + sw, z1))
    for sx in (-1, 1):
        cutters.append(box(sx * pc - sw, -pc, z0, sx * pc + sw, pc, z1))
    return cutters


def build_leg(P: Params) -> trimesh.Trimesh:
    """
    One leg, standing on its foot for printing.

    A square tenon rather than a round pin like the two-piece post: a round one
    would let a square leg rotate out of line with the base edges.  The shoulder
    at the tenon faces up on the plate, so there is nothing to support.
    """
    a = P.leg / 2.0
    b = P.leg_tenon / 2.0
    body = box(-a, -a, 0, a, a, P.leg_h)
    tenon = box(-b, -b, P.leg_h - EPS, b, b, P.leg_h + P.leg_tenon_h)
    return cleanup(union([body, tenon]))


def _leg_sockets(P: Params):
    """Blind sockets in the base underside, one under each corner post."""
    s = P.leg_socket_sz / 2.0
    pc = P.post_center
    return [box(sx * pc - s, sy * pc - s, -EPS, sx * pc + s, sy * pc + s,
                P.leg_tenon_h)
            for sx in (-1, 1) for sy in (-1, 1)]


def build_base(P: Params) -> trimesh.Trimesh:
    """Base plate with E27 bore, adapter counterbore, vents, cord channel and
    the four leg sockets."""
    f = P.foot / 2.0
    t = P.base_t
    base = box(-f, -f, 0, f, f, t)

    cutters = _joint_cutters(P, t, downward=True)
    cutters += _leg_sockets(P)

    # lamp holder bore + counterbore for the adapter ring
    cutters.append(cyl(P.socket_bore, -EPS, t + EPS, sections=P.arc))
    cutters.append(cyl(P.socket_cbore, t - P.socket_cbore_d, t + EPS,
                       sections=P.arc))

    # ring of tangential ventilation slots
    r0, r1 = P.base_vent_r0, P.base_vent_r1
    rm = (r0 + r1) / 2.0
    half_len = math.pi * rm / P.base_vents * 0.60
    for j in range(P.base_vents):
        ang = 2 * math.pi * j / P.base_vents
        cx, cy = rm * math.cos(ang), rm * math.sin(ang)
        tx, ty = -math.sin(ang), math.cos(ang)
        a = (cx - tx * half_len, cy - ty * half_len)
        b = (cx + tx * half_len, cy + ty * half_len)
        cutters.append(slat_box(a, b, r1 - r0, -EPS, t + EPS))

    # Cord tunnel from the bore out through the side wall.  It is enclosed
    # rather than open at the bottom: the 9 mm ceiling is a trivial bridge,
    # and it leaves the underside dead flat so the lamp cannot rock on its own
    # cord.  It also has to run well past the bore wall -- ending flush would
    # leave the two merely tangent at a single point, giving the cord no
    # opening at all and handing the boolean a knife-edge contact.
    cutters.append(box(-P.cable_w / 2, -f - EPS, P.cable_floor,
                       P.cable_w / 2, -P.socket_bore / 2 + P.cable_w,
                       P.cable_floor + P.cable_h))
    return cleanup(difference(base, cutters))


def build_cap(P: Params, pattern: str) -> trimesh.Trimesh:
    """Top cap with matching joinery and a kumiko-grilled vent."""
    f = P.foot / 2.0
    t = P.cap_t
    cap = box(-f, -f, 0, f, f, t)
    cap = difference(cap, _joint_cutters(P, 0.0, downward=False))

    # vent opening
    r = P.cap_vent_d / 2.0
    cap = difference(cap, [cyl(P.cap_vent_d, -EPS, t + EPS, sections=P.arc)])

    # coarse grille filling the vent
    g = P.grid * P.cap_grille_f
    segs = PATTERNS[cap_pattern(pattern)](P.cap_vent_d, P.cap_vent_d, g)
    segs = clip_circle(segs, 0.0, 0.0, r + 0.8)
    grille = [slat_box(p, q, P.slat_w + 0.4, 0.0, t) for p, q in segs]
    cap = union([cap] + grille)

    # chamfer the top perimeter (becomes a printable 45 deg flare when the cap
    # is printed top-face-down)
    ch = 2.0
    ring = []
    ring.append(difference(box(-f - EPS, -f - EPS, t - ch, f + EPS, f + EPS, t + EPS),
                           [box(-f + ch, -f + ch, t - ch - EPS,
                                f - ch, f - ch, t + 2 * EPS)]))
    return cleanup(difference(cap, ring))


def build_socket_ring(P: Params) -> trimesh.Trimesh:
    """
    Adapter that seats in the base counterbore and clamps the E27 holder.
    This is the only part to reprint if your holder differs: change --socket-neck.
    """
    od = P.socket_cbore - 0.4       # slip fit in the base counterbore
    ring = cyl(od, 0, P.socket_cbore_d, sections=P.arc)
    return cleanup(difference(ring, [cyl(P.socket_neck, -EPS,
                                        P.socket_cbore_d + EPS,
                                        sections=P.arc)]))


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def _rotz(deg):
    return trimesh.transformations.rotation_matrix(math.radians(deg), (0, 0, 1))


def _rotx(deg):
    return trimesh.transformations.rotation_matrix(math.radians(deg), (1, 0, 0))


def place_parts(P: Params, panel, post, base, cap, leg=None):
    """
    Return {name: transformed copy} for every part in assembled position.

    Panel local axes are (width, height, thickness) with z = 0 the lattice face.
    Rotating +90 deg about X stands the panel up (local y -> world z); a further
    turn about Z aims the lattice face outward on each of the four sides.
    """
    pc = P.post_center
    t = P.panel_t
    z_base = P.base_t - P.groove_d          # floor of the base joinery
    out = {"base": base.copy()}

    # corner posts: modelled with grooves on +X and +Y, rotated per corner
    corner_angle = {(-1, -1): 0, (1, -1): 90, (1, 1): 180, (-1, 1): 270}
    for idx, (sx, sy) in enumerate([(-1, -1), (1, -1), (1, 1), (-1, 1)]):
        p = post.copy()
        p.apply_transform(_rotz(corner_angle[(sx, sy)]))
        p.apply_translation((sx * pc, sy * pc, z_base))
        out[f"post{idx}"] = p

    # panels, one per side, each with its lattice face outward
    panel_place = [
        (_rotz(180) @ _rotx(90), (0.0, -pc - t / 2, z_base)),    # front  (-Y)
        (_rotx(90), (0.0, pc + t / 2, z_base)),                  # back   (+Y)
        (_rotz(90) @ _rotx(90), (-pc - t / 2, 0.0, z_base)),     # left   (-X)
        (_rotz(-90) @ _rotx(90), (pc + t / 2, 0.0, z_base)),     # right  (+X)
    ]
    for idx, (M, offset) in enumerate(panel_place):
        q = panel.copy()
        q.apply_transform(M)
        q.apply_translation(offset)
        out[f"panel{idx}"] = q

    c = cap.copy()
    c.apply_translation((0, 0, z_base + P.height - P.groove_d))
    out["cap"] = c

    # Legs hang below z = 0, so the assembly's origin is the base underside and
    # its z-extent comes out as total_height.  Only ever a preview, never printed.
    if leg is not None:
        for idx, (sx, sy) in enumerate([(-1, -1), (1, -1), (1, 1), (-1, 1)]):
            g = leg.copy()
            g.apply_translation((sx * pc, sy * pc, -P.leg_h))
            out[f"leg{idx}"] = g
    return out


def build_assembly(P: Params, panel, post, base, cap, leg=None):
    parts = place_parts(P, panel, post, base, cap, leg)
    return trimesh.util.concatenate(list(parts.values())), parts


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def check_part(name, mesh, P: Params, printable=True, bodies=1):
    ext = mesh.extents
    n_bodies = mesh.body_count
    rows = {
        "part": name,
        "tris": len(mesh.faces),
        "bbox": " x ".join(f"{v:6.1f}" for v in ext),
        "vol_cm3": mesh.volume / 1000.0,
        "watertight": mesh.is_watertight,
        "winding": mesh.is_winding_consistent,
        "bodies": n_bodies,
        "fits_bed": bool(np.all(np.sort(ext)[:2] <= np.sort(P.bed[:2]))
                         and ext[2] <= P.bed[2]) if printable else None,
    }
    problems = []
    if not mesh.is_watertight:
        problems.append("NOT watertight")
    if not mesh.is_winding_consistent:
        problems.append("inconsistent winding")
    if mesh.volume <= 0:
        problems.append("non-positive volume")
    if n_bodies != bodies:
        problems.append(f"{n_bodies} disconnected bodies, expected {bodies} "
                        "(a floating slat or an unsupported grille)")
    if printable and not rows["fits_bed"]:
        problems.append("exceeds build volume")
    return rows, problems


def check_fits(P: Params):
    """Dimensional sanity checks that would otherwise only show up after a print."""
    issues = []
    if abs((P.panel_w + P.panel_clear) - P.groove_span) > 1e-9:
        issues.append("panel width does not match the post groove span")
    if abs((P.slot_w - P.panel_t) - P.slot_clear) > 1e-9:
        issues.append("groove width does not give the intended slot clearance")
    if P.groove_d >= P.post / 2:
        issues.append("groove depth would cut the post in half")
    if P.slat_w < 2 * P.nozzle:
        issues.append(f"slat width {P.slat_w} is under two extrusions")
    if abs(P.slat_w / P.nozzle - round(P.slat_w / P.nozzle)) > 1e-6:
        issues.append(f"slat width {P.slat_w} is not a multiple of the nozzle")
    if P.rebate_d >= P.panel_t:
        issues.append("rebate deeper than the panel")
    if P.cable_floor + P.cable_h > P.base_t - P.groove_d:
        issues.append("cord tunnel breaks into the panel groove above it")
    if P.cable_floor < 3 * 0.2:
        issues.append("less than three layers of floor under the cord tunnel")
    if P.socket_cbore / 2 >= P.base_vent_r0:
        issues.append("adapter counterbore runs into the ventilation slots")
    if P.foot / 2 <= P.post_center + P.socket_sz / 2:
        issues.append("post socket breaks out of the side of the base/cap")
    if P.panel_border <= P.groove_d:
        issues.append("panel frame narrower than its groove engagement")
    if P.rebate_lip >= P.panel_border:
        issues.append("rebate pocket eats the whole frame lip")
    if P.leg_tenon_h >= P.base_t - P.groove_d:
        issues.append("leg socket breaks into the panel groove above it")
    if P.leg_tenon >= P.leg:
        issues.append("leg tenon is not narrower than the leg, so it has no shoulder")
    if P.post_center + P.leg / 2 > P.foot / 2:
        issues.append("leg overhangs the edge of the base")
    if P.post_center - P.leg_socket_sz / 2 <= P.base_vent_r1:
        issues.append("leg socket runs into the ventilation slots")
    if P.leg_h < 3 * 0.2:
        issues.append("leg is under three layers tall")
    return issues


def check_clearances(parts):
    """No two assembled parts may share any volume."""
    names = ["base", "cap", "post0", "panel0", "panel2", "leg0"]
    issues = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                inter = intersection(parts[a], parts[b])
                # an empty boolean result has no faces and a nan volume
                v = abs(inter.volume) if (inter is not None
                                          and len(inter.faces) > 0) else 0.0
            except Exception:
                v = 0.0
            if v > 1.0:      # mm^3
                issues.append(f"{a} and {b} interpenetrate by {v:.1f} mm^3")
    return issues


# --------------------------------------------------------------------------
# SVG preview
# --------------------------------------------------------------------------

def write_svg(path: Path, P: Params, pattern: str):
    W, H, b = P.panel_w, P.height, P.panel_border
    ow, oh = W - 2 * b, H - 2 * b
    segs = PATTERNS[pattern](ow, oh, P.grid)
    segs = clip_rect(segs, -ow / 2, -oh / 2, ow / 2, oh / 2)

    pad = 8
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W + 2 * pad:.0f}" '
        f'height="{H + 2 * pad:.0f}" viewBox="{-W / 2 - pad:.1f} {-pad:.1f} '
        f'{W + 2 * pad:.1f} {H + 2 * pad:.1f}">',
        '<rect x="-1000" y="-1000" width="3000" height="3000" fill="#1b1512"/>',
        f'<rect x="{-W / 2:.2f}" y="0" width="{W:.2f}" height="{H:.2f}" '
        f'fill="#2a211c" stroke="#c8a06a" stroke-width="1.2"/>',
        f'<rect x="{-W / 2 + b:.2f}" y="{b:.2f}" width="{ow:.2f}" '
        f'height="{oh:.2f}" fill="#f6e9c9"/>',
        f'<g stroke="#8a5a2b" stroke-width="{P.slat_w:.2f}" '
        f'stroke-linecap="round">',
    ]
    for (px, py), (qx, qy) in segs:
        lines.append(f'<line x1="{px:.2f}" y1="{py + H / 2:.2f}" '
                     f'x2="{qx:.2f}" y2="{qy + H / 2:.2f}"/>')
    lines.append("</g>")
    lines.append(f'<text x="{-W / 2 + 4:.1f}" y="{H + 6:.1f}" fill="#c8a06a" '
                 f'font-family="monospace" font-size="7">{pattern}  '
                 f'{W:.1f} x {H:.1f} x {P.panel_t} mm  grid {P.grid} mm</text>')
    lines.append("</svg>")
    path.write_text("\n".join(lines))


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", default="asanoha", choices=sorted(PATTERNS))
    ap.add_argument("--size", type=float, help="lantern outer square (mm)")
    ap.add_argument("--height", type=float, help="panel and post length (mm)")
    ap.add_argument("--slat", type=float, help="lattice slat width (mm)")
    ap.add_argument("--grid", type=float, help="pattern pitch (mm)")
    ap.add_argument("--socket-neck", type=float,
                    help="adapter ring bore for your E27 holder (mm)")
    ap.add_argument("--split-posts", action="store_true",
                    help="also export the two-piece post")
    ap.add_argument("--all", action="store_true",
                    help="export every pattern, both post styles and all previews")
    ap.add_argument("--out", default=str(Path(__file__).parent))
    args = ap.parse_args(argv)

    # trimesh divides by volume when it inspects an empty boolean result, which
    # is an expected outcome of the clearance checks below
    np.seterr(invalid="ignore", divide="ignore")

    P = Params()
    for attr, val in (("size", args.size), ("height", args.height),
                      ("slat_w", args.slat), ("grid", args.grid),
                      ("socket_neck", args.socket_neck)):
        if val is not None:
            setattr(P, attr, val)

    fit_issues = check_fits(P)
    if fit_issues:
        print("Parameter check FAILED:")
        for m in fit_issues:
            print("  -", m)
        return 1

    out = Path(args.out)
    stl = out / "stl"
    prev = out / "preview"
    stl.mkdir(parents=True, exist_ok=True)
    prev.mkdir(parents=True, exist_ok=True)

    print(f"kumiko lamp  {P.foot:.0f} x {P.foot:.0f} x {P.total_height:.0f} mm"
          f"   panel {P.panel_w:.1f} x {P.height:.0f} x {P.panel_t}"
          f"   pattern {args.pattern}")

    results = []
    t0 = time.time()

    def emit(name, mesh, bodies=1, printable=True):
        """
        Export a part, then validate the file as reloaded rather than the mesh
        in memory.  The STL is the deliverable, and the round-trip through
        float32 with no shared-vertex index is where defects actually appear.
        """
        path = stl / f"{name}.stl"
        mesh.export(path)
        results.append(check_part(name, trimesh.load(path), P,
                                  printable=printable, bodies=bodies))
        return mesh

    patterns = sorted(PATTERNS) if args.all else [args.pattern]
    panels = {}
    for name in patterns:
        print(f"  building panel: {name} ...", flush=True)
        panels[name] = emit(f"panel_{name}", build_panel(P, name))
        write_svg(prev / f"panel_{name}.svg", P, name)

    panel = panels[args.pattern]

    print("  building post ...", flush=True)
    post = emit("post", build_post(P, "full"))

    if args.split_posts or args.all:
        for half in ("lower", "upper"):
            emit(f"post_{half}", build_post(P, half))

    print("  building base ...", flush=True)
    base = emit("base", build_base(P))

    print("  building cap ...", flush=True)
    cap = build_cap(P, args.pattern)
    # The cap is modelled joinery-side-up for assembly, but has to print the
    # other way up so those pockets face the nozzle instead of needing support.
    # Export it already flipped so the file drops straight onto the plate.
    cap_print = cap.copy()
    cap_print.apply_transform(_rotx(180))
    cap_print.apply_translation((0, 0, P.cap_t))
    emit("top_cap", cap_print)

    print("  building socket ring ...", flush=True)
    emit("socket_adapter_ring", build_socket_ring(P))

    print("  building leg ...", flush=True)
    leg = emit("leg", build_leg(P))

    print("  assembling ...", flush=True)
    asm, parts = build_assembly(P, panel, post, base, cap, leg)
    emit("assembly_preview", asm, bodies=len(parts), printable=False)

    # ---- report ---------------------------------------------------------
    print()
    hdr = (f"{'part':22} {'tris':>7} {'bbox (mm)':>26} {'vol cm3':>9}"
           f"  wt  wind  bodies  bed")
    print(hdr)
    print("-" * len(hdr))
    problems = []
    for rows, probs in results:
        bed = "-" if rows["fits_bed"] is None else ("ok" if rows["fits_bed"] else "NO")
        print(f"{rows['part']:22} {rows['tris']:7d} {rows['bbox']:>26} "
              f"{rows['vol_cm3']:9.1f}  "
              f"{'ok' if rows['watertight'] else 'NO':>3} "
              f"{'ok' if rows['winding'] else 'NO':>5} "
              f"{rows['bodies']:>7} {bed:>4}")
        problems += [f"{rows['part']}: {p}" for p in probs]

    print("\n  checking assembled clearances ...", flush=True)
    problems += check_clearances(parts)

    print(f"\nassembled: {P.foot:.0f} x {P.foot:.0f} x {P.total_height:.0f} mm"
          f"   built in {time.time() - t0:.1f} s")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  -", p)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
