#!/usr/bin/env python3
"""
Parametric kumiko lamp generator.

Produces watertight, manifold STL parts for either the square four-panel
Classic lantern or the threaded two-part cylindrical Modern lantern.  Parts
are exported in their intended support-free print orientation.

    python3 kumiko_lamp.py --all              # regenerate every STL + SVG
    python3 kumiko_lamp.py --pattern kikkou   # just the default part set
    python3 kumiko_lamp.py --size 150 --height 180
    python3 kumiko_lamp.py --style modern --all

Geometry is built with real CSG (trimesh + manifold3d), not overlapping shells,
so slicers receive clean solids that need no auto-repair.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import trimesh

EPS = 1e-3          # nudge used to make through-cuts unambiguous
SNAP_BAND_H = 0.4   # two 0.2 mm layers
SNAP_TIP = 0.6      # solid lead-in left beyond the detent
SNAP_ROOT = 0.6     # solid root left below the leg's flexure cavity

# Bulb-base names do not define the holder's mounting neck.  These are the
# working sleeve presets the configurator offers; --socket-neck remains the
# authority for hardware that measures differently.
HOLDER_PRESETS = {"e27": 26.5, "e14": 27.0}

# The modern lantern is intentionally described by a small fixed mechanical
# contract.  ``size``, ``height`` and ``panel_t`` remain the user-facing outer
# diameter, shade height and radial lattice depth; these constants describe the
# two-piece connection shared by the Python and browser generators.
MODERN_DEFAULT_SIZE = 100.0
MODERN_DEFAULT_SHADE_H = 218.0
MODERN_DEFAULT_BASE_H = 90.0
MODERN_RING_H = 10.0
MODERN_BODY_WALL = 5.0
MODERN_THREAD_ENGAGEMENT = 10.0
MODERN_THREAD_PITCH = 2.0
MODERN_THREAD_DEPTH = 0.8
MODERN_THREAD_CLEAR = 0.30
MODERN_CHORD_ERROR = 0.1
MODERN_LATTICE_OVERLAP = 0.8


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

    # --- diffuser --------------------------------------------------------
    rebate_d: float = 0.6        # pocket depth on the back face
    rebate_lip: float = 8.0      # pocket inset from the panel outer edge
    # A printed clear plate sharing the groove with the lattice, behind it.
    # 0 means no plate, and slot_w collapses to the un-glazed width.  Capped
    # by check_fits at post - 2*groove_d; 1.2 is the working value.
    plate_t: float = 0.0

    # --- joinery ---------------------------------------------------------
    groove_d: float = 6.0        # how deep panels sit into posts/base/cap
    slot_clear: float = 0.4      # groove width minus panel thickness
    panel_clear: float = 0.3     # total width clearance for the panel
    socket_clear: float = 0.4    # post socket size minus post size

    # --- base and cap ----------------------------------------------------
    plinth: float = 5.0          # reveal of base/cap beyond the post faces
    base_t: float = 16.0
    cap_t: float = 10.0
    edge_chamfer: float = 2.0    # 45 deg bevel on all four horizontal
                                 # perimeter edges of the base and the cap.
                                 # Distinct from `post_chamfer`, which takes
                                 # the post's vertical arris.

    # --- legs ------------------------------------------------------------
    # Separate parts rather than moulded onto the base: hung off the underside
    # they would turn the whole slab into an unsupported ceiling, and this lamp
    # prints every part without supports.
    leg: float = 20.0            # square cross-section
    leg_h: float = 12.0          # stand-off below the base
    leg_tenon: float = 10.0      # square tenon plugging into the base
    leg_tenon_h: float = 8.0     # tenon length == socket depth
    leg_clear: float = 0.35      # socket size minus tenon size
    snap_engagement: float = 0.0 # positive detent past the socket wall; 0 disables

    # --- top fixing and finials ------------------------------------------
    # A heat-set insert in the top of each post lets the cap be screwed down
    # instead of friction-fitted, and a finial over each screw hides the head
    # and rhymes with the legs under the base.  0 disables the whole feature,
    # the way plate_t does for the diffuser plate, and the lamp ships unscrewed
    # -- with the screws in, changing a bulb means prising four finials off.
    post_insert_d: float = 0.0   # insert pilot hole; 4.0 for an M3 x 5.7
    post_insert_h: float = 6.5   # blind depth in the post top: insert + relief
    finial_h: float = 8.0        # finial body standing above the cap
    finial_tenon_h: float = 2.0  # skirt depth == socket depth in the cap top

    # --- lamp holder -----------------------------------------------------
    holder_type: str = "e27"         # named starting point; neck remains tunable
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

    # --- modern cylindrical style ---------------------------------------
    # Appended rather than inserted above so positional construction of the
    # long-standing classic Params API keeps its original field order.
    lantern_style: str = "classic"
    modern_base_h: float = MODERN_DEFAULT_BASE_H
    modern_thread_clear: float = MODERN_THREAD_CLEAR
    # None deliberately means "follow the shade diameter".  Besides keeping
    # existing --style modern --size commands coupled as before, the sentinel
    # lets callers opt into a separate body diameter without changing the
    # positional layout of any of the long-standing fields above.
    modern_base_d: float | None = None

    # ---- derived ---------------------------------------------------------
    @property
    def slot_w(self) -> float:
        """Width of every groove.  Holds the panel, and the plate behind it."""
        return self.panel_t + self.plate_t + self.slot_clear

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
    def snapped(self) -> bool:
        """Do the four feet and, when present, four finials use snap detents?"""
        return self.snap_engagement > 0

    @property
    def snap_tab_w(self) -> float:
        """Width along the face of each of the two opposing snap tabs."""
        return min(4.0, self.leg_tenon - 4 * self.nozzle)

    @property
    def snap_tab_out(self) -> float:
        """Tab reach from the tenon axis; insertion deflection is engagement."""
        return (self.leg_tenon / 2.0 + self.leg_clear / 2.0
                + self.snap_engagement)

    @property
    def snap_recess_out(self) -> float:
        """Matching socket relief with half of leg_clear left around the tab."""
        return self.snap_tab_out + self.leg_clear / 2.0

    @property
    def snap_socket_sz(self) -> float:
        """Conservative full span used by the socket boundary guards."""
        return 2 * self.snap_recess_out

    @property
    def snap_cavity_sz(self) -> float:
        """Square leg-tenon relief leaving four nozzle-width flexure walls."""
        return self.leg_tenon - 8 * self.nozzle

    @property
    def screwed(self) -> bool:
        """Is the cap screwed down?  One number switches the whole feature."""
        return self.post_insert_d > 0

    @property
    def screw_d(self) -> float:
        """
        Nominal thread the insert takes.  A heat-set insert's pilot hole runs
        about a millimetre over its thread right across the M series, so this
        inverts the one number you actually look up: 3.5 -> M2.5, 4.0 -> M3,
        5.0 -> M4.
        """
        return self.post_insert_d - 1.0

    @property
    def cap_screw_d(self) -> float:
        """Clearance hole through the cap.  3.4 at M3, the standard medium fit."""
        return self.screw_d + 0.4

    @property
    def screw_head_d(self) -> float:
        """ISO 4762 socket cap head is 1.8 d, within 0.1 across M2.5 to M4."""
        return 1.8 * self.screw_d

    @property
    def finial_cavity_d(self) -> float:
        """Bore in the finial that swallows the head, 0.55 radially clear."""
        return self.screw_head_d + 1.2

    @property
    def finial_cavity_h(self) -> float:
        """
        Depth of that bore from the skirt face, which is the plane the head
        bears on.  ISO 4762 head height is d, plus a little.
        """
        return self.screw_d + 0.4

    @property
    def cap_floor(self) -> float:
        """
        What is left of the cap over a post socket.  The sockets are cut up
        from the underside to groove_d and the finial socket eats down from the
        top; this is the material the screw clamps, and the only thing between
        the sockets and being through holes.
        """
        return self.cap_t - self.groove_d - (self.finial_tenon_h
                                             if self.screwed else 0.0)

    @property
    def post_wall(self) -> float:
        """
        Material between the insert hole and the nearest point of a panel
        groove.  The grooves reach in to post/2 - groove_d, so their closest
        approach to the post axis is that far out whatever their width -- which
        is why glazing the lamp does not move this.
        """
        return self.post / 2.0 - self.groove_d - self.post_insert_d / 2.0

    @property
    def total_height(self) -> float:
        if self.lantern_style == "modern":
            return self.modern_base_h + self.height - MODERN_THREAD_ENGAGEMENT
        return (self.leg_h + self.base_t + (self.height - 2 * self.groove_d)
                + self.cap_t + (self.finial_h if self.screwed else 0.0))

    @property
    def modern_outer_r(self) -> float:
        """Outer radius of the shade and its two rings."""
        return self.size / 2.0

    @property
    def modern_inner_r(self) -> float:
        """Inner face of the rings and wrapped lattice."""
        return self.modern_outer_r - self.panel_t

    @property
    def modern_base_diameter(self) -> float:
        """Outer body diameter, inherited from the shade unless overridden."""
        return self.size if self.modern_base_d is None else self.modern_base_d

    @property
    def modern_base_r(self) -> float:
        """Outer radius of the cylindrical body below the threaded neck."""
        return self.modern_base_diameter / 2.0

    @property
    def modern_cavity_r(self) -> float:
        """Radius of the hollow base body below the mounting deck."""
        return self.modern_base_r - MODERN_BODY_WALL

    @property
    def modern_footprint(self) -> float:
        """Maximum assembled diameter of the independently sized two parts."""
        return max(self.size, self.modern_base_diameter)

    @property
    def modern_cable_inner_y(self) -> float:
        """Inner end of the cord cutter, extended past the cavity tangent."""
        return -self.modern_cavity_r + self.cable_w

    @property
    def modern_lattice_h(self) -> float:
        return self.height - 2 * MODERN_RING_H

    @property
    def modern_thread_root_r(self) -> float:
        """Male neck radius below the 0.8 mm triangular thread."""
        return self.modern_inner_r - MODERN_THREAD_DEPTH

    @property
    def modern_thread_crest_r(self) -> float:
        return self.modern_inner_r

    @property
    def modern_thread_bore_r(self) -> float:
        """Female baseline bore: matching male root plus radial clearance."""
        return self.modern_thread_root_r + self.modern_thread_clear

    @property
    def modern_thread_groove_r(self) -> float:
        return self.modern_thread_crest_r + self.modern_thread_clear

    @property
    def modern_thread_wall(self) -> float:
        """Minimum shade wall outside the crest of the female thread."""
        return self.modern_outer_r - self.modern_thread_groove_r

    @property
    def modern_shoulder_h(self) -> float:
        """Height of the 45-degree base transition below the threaded neck."""
        return self.modern_base_r - self.modern_thread_root_r

    @property
    def modern_shoulder_z(self) -> float:
        """Assembled height where the full-diameter base starts tapering."""
        return (self.modern_base_h - MODERN_THREAD_ENGAGEMENT
                - self.modern_shoulder_h)

    def modern_base_outer_at(self, z: float) -> float:
        """Outer base radius at assembled height ``z``.

        The body contracts toward the shade-derived thread root on a printable
        45-degree shoulder.  Keeping this profile in one helper makes the
        matching inner cavity taper and its validation use identical maths.
        """
        body_h = self.modern_base_h - MODERN_THREAD_ENGAGEMENT
        if z <= self.modern_shoulder_z:
            return self.modern_base_r
        if z >= body_h:
            return self.modern_thread_root_r
        return self.modern_base_r - (z - self.modern_shoulder_z)


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


def _stroke(ss, pts, ov):
    """Add a polyline as a chain of overlapping slats."""
    for i in range(len(pts) - 1):
        ss.add(*_extend(pts[i], pts[i + 1], ov))


def _arc(cx, cy, r, a0, a1, n):
    """Circular arc centred on (cx, cy), sampled at n+1 points."""
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n),
             cy + r * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


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


def _grid_lines(ss, w, h, sx, sy):
    """
    A full-field rectangular grid of pitch sx by sy.

    Every line runs the whole overshot field rather than cell by cell, so each
    crossing is a real overlap and the whole grid comes back as one body with
    nothing to tie together afterwards.
    """
    i0 = int(math.floor(-w / 2 / sx)) - 2
    i1 = int(math.ceil(w / 2 / sx)) + 2
    j0 = int(math.floor(-h / 2 / sy)) - 2
    j1 = int(math.ceil(h / 2 / sy)) + 2
    for i in range(i0, i1 + 1):
        ss.add((i * sx, j0 * sy), (i * sx, j1 * sy))
    for j in range(j0, j1 + 1):
        ss.add((i0 * sx, j * sy), (i1 * sx, j * sy))


def pat_masu_goushi(w, h, s):
    """
    Masu-goushi (枡格子, "box lattice"): a plain square grid, nothing in the
    cells.  The simplest pattern here, and the most open.

    Distinct from `pat_masu` below, which is masu-*tsunagi* -- the same grid
    with a concentric square linked into every cell.
    """
    ss = SegSet()
    _grid_lines(ss, w, h, s, s)
    return ss.segs


def pat_senbon(w, h, s):
    """
    Senbon-goushi (千本格子, "thousand-stick lattice"): closely spaced vertical
    bars crossed by a horizontal rail every third bar.

    The bars run at s/3 so the stock pitch earns the name -- at 28 mm that is a
    9.3 mm bar spacing.  The rails are not only decoration: bars alone would be
    parallel chords with no crossings at all, which survives the panel (each
    reaches the top and bottom frame) but leaves the cap grille depending
    entirely on the rim clip.
    """
    ss = SegSet()
    _grid_lines(ss, w, h, s / 3.0, s)
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

    _grid_lines(ss, w, h, s, s)

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


# One seigaiha fan: (radius as a fraction of R, chords across the sweep).
#
# The chord counts are bounded by the boolean, not chosen for smoothness.  Two
# slats along a gently curving polyline are near coplanar, and below roughly 7
# degrees of turn per chord their union leaves a pinched edge that costs the
# part its watertightness at some pitches.  At 14/13/12 the sagitta is already
# under 0.1 mm -- a quarter of a nozzle -- so there is nothing to see for going
# finer anyway.
_SEIGAIHA_ARCS = ((1.00, 14), (0.85, 13), (0.70, 12))
_SEIGAIHA_SWEEP = 55.0


def pat_seigaiha(w, h, s):
    """
    Seigaiha (青海波, "blue sea wave"): rows of overlapping fans, each three
    concentric arcs struck about one centre.

    Fan radius is 1.5 * s; centres sit a = R apart along a row, rows are
    v = 0.6 R apart, and alternate rows are offset by a / 2.

    Concentric arcs never touch each other, so what holds the field together is
    the neighbouring fan.  Two circles of radius r1 and r2 whose centres are a
    apart cross at x = (a^2 + r1^2 - r2^2) / 2a, which for a = R puts all nine
    radius pairs inside the drawn span -- the outermost of them (1.00 R against
    the neighbour's 0.70 R) 49 degrees off vertical, and the shallowest crossing
    angle anywhere in the field is 60 degrees, so none of them is a graze.  Each
    row is therefore one continuous chain running off both sides of the opening
    and burying itself in the frame.

    +/-55 degrees is what puts that last crossing inside the arc, and it must
    not be opened much further: past 55.15 degrees (0.6 + 0.7 cos sweep < 1) the
    bottom of one row starts cutting through the top of the row below at a
    glancing angle.
    """
    ss = SegSet()
    R = 1.5 * s
    a = R
    v = 0.6 * R
    ov = s * 0.05
    a0 = math.radians(90.0 - _SEIGAIHA_SWEEP)
    a1 = math.radians(90.0 + _SEIGAIHA_SWEEP)
    i0 = int(math.floor(-w / 2 / a)) - 2
    i1 = int(math.ceil(w / 2 / a)) + 2
    j0 = int(math.floor(-h / 2 / v)) - 2
    j1 = int(math.ceil(h / 2 / v)) + 2
    for j in range(j0, j1 + 1):
        cy = j * v
        off = (j % 2) * a / 2.0
        for i in range(i0, i1 + 1):
            cx = i * a + off
            for f, n in _SEIGAIHA_ARCS:
                _stroke(ss, _arc(cx, cy, f * R, a0, a1, n), ov)
    return ss.segs


# --------------------------------------------------------------------------
# Lai Thai (ลายไทย) patterns
#
# Unlike every kumiko pattern above, these are curvilinear: they are stroked as
# polylines rather than laid out on a lattice.  That costs far more in the
# browser core than it does here -- see the note in CLAUDE.md -- so the
# tessellation counts below are deliberately frugal.
# --------------------------------------------------------------------------

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
    # whole flower into the frame.  Each one forks near the perimeter so the
    # ornament has independent side and top/bottom anchors instead of relying
    # on one diagonal contact at a corner.  Paired leaves root on the stems.
    for m in (1, -1):
        for v in (1, -1):
            stem = _cubic(p(m*0.18, v*0.08), p(m*0.30, v*0.14),
                          p(m*0.37, v*0.36), p(m*0.53, v*0.54), n + 2)
            _stroke(ss, stem, ov)
            fork = stem[round(0.72 * (len(stem) - 1))]
            # Organic-looking perimeter braces: one terminates through the
            # side rail, the other through the top or bottom rail.
            _stroke(ss, _cubic(fork, p(m*0.43, v*0.34),
                               p(m*0.49, v*0.36), p(m*0.54, v*0.38),
                               max(5, n // 2)), ov)
            _stroke(ss, _cubic(fork, p(m*0.32, v*0.43),
                               p(m*0.34, v*0.49), p(m*0.36, v*0.54),
                               max(5, n // 2)), ov)
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


# --------------------------------------------------------------------------
# Region patterns
#
# Everything above returns line segments, swept as fixed-width slats.  Imported
# artwork is a different thing: a filled region -- an outer contour plus holes,
# with stroke thickness that varies across the design, which fixed-width slats
# cannot express.  Region patterns return contours instead and are extruded
# directly.
#
# Contours are baked, not parsed at runtime: the browser core is one
# self-contained file with no file access, and the two implementations have to
# emit identical geometry.  Regenerate with tools/svg2pattern.py.
# --------------------------------------------------------------------------

# reference/laithai.svg, flattened to a chord tolerance, normalised to a unit
# box with y UP.  Flat (x, y, x, y, ...) per contour.  The SVG is y-down, so
# the flip inverts every winding -- which is what leaves the outer contours
# positive and the holes negative, as FillRule.Positive wants.
_LAITHAI_LOOPS = [
    (0,0.09375,-0.018877,0.091843,-0.036467,0.086375,-0.052391,0.077724,-0.06627,0.06627,-0.077724,0.052391,-0.086375,0.036467,-0.091843,0.018877,-0.09375,-0,-0.091843,-0.018877,-0.086375,-0.036467,-0.077724,-0.052391,-0.06627,-0.06627,-0.052391,-0.077724,-0.036467,-0.086375,-0.018877,-0.091843,0,-0.09375,0.018877,-0.091843,0.036467,-0.086375,0.052391,-0.077724,0.06627,-0.06627,0.077724,-0.052391,0.086375,-0.036467,0.091843,-0.018877,0.09375,-0,0.091843,0.018877,0.086375,0.036467,0.077724,0.052391,0.06627,0.06627,0.052391,0.077724,0.036467,0.086375,0.018877,0.091843),
    (0,-0.0625,-0.0126,-0.061231,-0.024333,-0.05759,-0.03495,-0.05183,-0.044199,-0.044199,-0.05183,-0.03495,-0.05759,-0.024333,-0.061231,-0.0126,-0.0625,-0,-0.061231,0.0126,-0.05759,0.024333,-0.05183,0.03495,-0.044199,0.044199,-0.03495,0.05183,-0.024333,0.05759,-0.0126,0.061231,0,0.0625,0.0126,0.061231,0.024333,0.05759,0.03495,0.05183,0.044199,0.044199,0.05183,0.03495,0.05759,0.024333,0.061231,0.0126,0.0625,-0,0.061231,-0.0126,0.05759,-0.024333,0.05183,-0.03495,0.044199,-0.044199,0.03495,-0.05183,0.024333,-0.05759,0.0126,-0.061231),
    (0.24359,0.012969,0.25766,0.013875,0.27141,0.016582,0.28468,0.021077,0.29734,0.027344,0.30693,0.034206,0.31649,0.043475,0.32594,0.054786,0.33523,0.067773,0.35299,0.097316,0.3692,0.12918,0.3947,0.18818,0.40719,0.22141,0.40803,0.22801,0.40594,0.23438,0.40145,0.2393,0.39531,0.24188,0.36025,0.24771,0.29637,0.25512,0.26066,0.257,0.22621,0.25638,0.21034,0.25482,0.19583,0.25228,0.18305,0.24863,0.17234,0.24375,0.15996,0.2354,0.14907,0.22559,0.13973,0.21449,0.13197,0.20229,0.12583,0.18915,0.12135,0.17526,0.11856,0.1608,0.1175,0.14594,0.106,0.15452,0.093809,0.16219,0.067656,0.17469,0.07999,0.18305,0.091118,0.19272,0.10091,0.20356,0.10922,0.21545,0.11592,0.22827,0.12087,0.2419,0.12395,0.25622,0.125,0.27109,0.12386,0.28281,0.12061,0.29571,0.11555,0.30955,0.10895,0.32408,0.092255,0.35425,0.072773,0.38424,0.034429,0.43587,0.011875,0.46328,0.0064941,0.46715,0,0.46844,-0.0064941,0.46715,-0.011875,0.46328,-0.034429,0.43587,-0.072773,0.38424,-0.092255,0.35425,-0.10895,0.32408,-0.11555,0.30955,-0.12061,0.29571,-0.12386,0.28281,-0.125,0.27109,-0.12395,0.25617,-0.12087,0.24184,-0.11592,0.2282,-0.10922,0.21539,-0.10091,0.20352,-0.091118,0.1927,-0.07999,0.18305,-0.067656,0.17469,-0.09375,0.16219,-0.1175,0.14594,-0.11856,0.1608,-0.12135,0.17526,-0.12583,0.18915,-0.13197,0.20229,-0.13973,0.21449,-0.14907,0.22559,-0.15996,0.2354,-0.17234,0.24375,-0.18308,0.24863,-0.19588,0.25228,-0.21041,0.25482,-0.2263,0.25638,-0.26077,0.257,-0.29648,0.25512,-0.36034,0.24771,-0.39531,0.24188,-0.40145,0.2393,-0.40594,0.23438,-0.40797,0.22801,-0.40719,0.22141,-0.3947,0.18816,-0.3692,0.12912,-0.35299,0.097247,-0.33523,0.067708,-0.32594,0.054728,-0.31649,0.04343,-0.30693,0.03418,-0.29734,0.027344,-0.28462,0.021077,-0.27135,0.016582,-0.25764,0.013875,-0.24359,0.012969,-0.22584,0.014375,-0.20797,0.018594,-0.19627,0.023008,-0.18516,0.028594,-0.1875,-0,-0.18516,-0.028594,-0.19627,-0.023008,-0.20797,-0.018594,-0.21954,-0.015503,-0.23114,-0.013611,-0.24269,-0.012917,-0.25412,-0.013418,-0.26537,-0.015113,-0.27637,-0.018,-0.28705,-0.022078,-0.29734,-0.027344,-0.30693,-0.034206,-0.31649,-0.043475,-0.32594,-0.054786,-0.33523,-0.067773,-0.35299,-0.097316,-0.3692,-0.12918,-0.3947,-0.18818,-0.40719,-0.22141,-0.40803,-0.22801,-0.40594,-0.23438,-0.40145,-0.2393,-0.39531,-0.24187,-0.3416,-0.25035,-0.29731,-0.2551,-0.25016,-0.25719,-0.2271,-0.2565,-0.20586,-0.25424,-0.18731,-0.25013,-0.17234,-0.24391,-0.15996,-0.23556,-0.14907,-0.22574,-0.13973,-0.21465,-0.13197,-0.20244,-0.12583,-0.18931,-0.12135,-0.17542,-0.11856,-0.16095,-0.1175,-0.14609,-0.106,-0.15467,-0.093809,-0.16234,-0.067656,-0.17484,-0.07999,-0.18321,-0.091118,-0.19287,-0.10091,-0.20371,-0.10922,-0.21561,-0.11592,-0.22843,-0.12087,-0.24206,-0.12395,-0.25637,-0.125,-0.27125,-0.12386,-0.28297,-0.12061,-0.29587,-0.11555,-0.30971,-0.10895,-0.32424,-0.092255,-0.3544,-0.072773,-0.38439,-0.034429,-0.43602,-0.011875,-0.46344,-0.0065234,-0.46746,2.2204e-16,-0.46891,0.0065234,-0.46746,0.011875,-0.46344,0.034429,-0.43602,0.072773,-0.38439,0.092255,-0.3544,0.10895,-0.32424,0.11555,-0.30971,0.12061,-0.29587,0.12386,-0.28297,0.125,-0.27125,0.12395,-0.25633,0.12087,-0.24199,0.11592,-0.22836,0.10922,-0.21555,0.10091,-0.20367,0.091118,-0.19285,0.07999,-0.1832,0.067656,-0.17484,0.09375,-0.16234,0.1175,-0.14609,0.11856,-0.16095,0.12135,-0.17542,0.12583,-0.18931,0.13197,-0.20244,0.13973,-0.21465,0.14907,-0.22574,0.15996,-0.23556,0.17234,-0.24391,0.18731,-0.2502,0.20586,-0.2543,0.2271,-0.25652,0.25016,-0.25719,0.29731,-0.2551,0.3416,-0.25035,0.39531,-0.24187,0.40145,-0.2393,0.40594,-0.23438,0.40797,-0.22801,0.40719,-0.22141,0.3947,-0.18816,0.3692,-0.12912,0.35299,-0.097247,0.33523,-0.067708,0.32594,-0.054728,0.31649,-0.04343,0.30693,-0.03418,0.29734,-0.027344,0.28705,-0.022123,0.27637,-0.018066,0.26537,-0.015182,0.25412,-0.013477,0.24269,-0.012958,0.23114,-0.013633,0.21954,-0.015509,0.20797,-0.018594,0.19627,-0.023008,0.18516,-0.028594,0.1875,-0,0.18516,0.028594,0.19627,0.023008,0.20797,0.018594,0.22578,0.014375),
    (0.18797,0.21672,0.19844,0.22093,0.21234,0.22379,0.22906,0.22542,0.24797,0.22594,0.27822,0.22501,0.3101,0.2225,0.37141,0.21437,0.35186,0.16655,0.32867,0.11814,0.31652,0.096444,0.30443,0.077859,0.29272,0.063474,0.28172,0.054375,0.27032,0.049043,0.25828,0.045703,0.24577,0.044355,0.23297,0.045,0.24203,0.049531,0.25413,0.059329,0.26486,0.07304,0.2742,0.089288,0.28213,0.1067,0.29369,0.13951,0.29938,0.16047,0.2996,0.16647,0.29768,0.17193,0.2939,0.17631,0.28859,0.17906,0.26762,0.18458,0.2334,0.19098,0.21434,0.19282,0.19561,0.19286,0.17839,0.19043,0.16391,0.18484,0.15516,0.17859,0.16083,0.18998,0.1682,0.20029,0.17722,0.20929,0.18781,0.21672),
    (-0.077031,0.22359,-0.08406,0.2342,-0.089316,0.24576,-0.09261,0.25811,-0.09375,0.27109,-0.091375,0.28514,-0.084771,0.30245,-0.074716,0.32221,-0.061992,0.34359,-0.031655,0.3879,0,0.42875,0.031655,0.3879,0.061992,0.34359,0.074716,0.32221,0.084771,0.30245,0.091375,0.28514,0.09375,0.27109,0.09261,0.25811,0.089316,0.24576,0.08406,0.2342,0.077031,0.22359,0.078125,0.23438,0.075693,0.24971,0.069185,0.26583,0.059783,0.28204,0.048672,0.29762,0.02605,0.32407,0.010781,0.33953,0.0056982,0.34278,0,0.34387,-0.0056982,0.34278,-0.010781,0.33953,-0.02605,0.32416,-0.048672,0.29773,-0.059783,0.28215,-0.069185,0.26592,-0.075693,0.24976,-0.078125,0.23438),
    (0.046875,0.23438,0.045918,0.22496,0.043176,0.21617,0.038841,0.20821,0.033105,0.20127,0.026161,0.19553,0.018201,0.1912,0.0094162,0.18846,0,0.1875,-0.0094162,0.18846,-0.018201,0.1912,-0.026161,0.19553,-0.033105,0.20127,-0.038841,0.20821,-0.043176,0.21617,-0.045918,0.22496,-0.046875,0.23438,-0.045771,0.24122,-0.042671,0.24925,-0.031758,0.26771,-0.016685,0.28744,0,0.30609,0.016685,0.28744,0.031758,0.26771,0.042671,0.24925,0.045771,0.24122),
    (-0.28172,0.054375,-0.29272,0.063474,-0.30443,0.077859,-0.31652,0.096444,-0.32867,0.11814,-0.35186,0.16655,-0.37141,0.21437,-0.3202,0.22138,-0.26668,0.22551,-0.24181,0.22584,-0.21966,0.22466,-0.20135,0.22171,-0.18797,0.21672,-0.17731,0.20929,-0.1683,0.20029,-0.16096,0.18998,-0.15531,0.17859,-0.16406,0.18484,-0.17859,0.19038,-0.19583,0.1928,-0.21457,0.19275,-0.23361,0.19092,-0.2678,0.18456,-0.28875,0.17906,-0.29406,0.17631,-0.29783,0.17193,-0.29976,0.16647,-0.29953,0.16047,-0.29385,0.13958,-0.28229,0.10676,-0.27435,0.089329,-0.26501,0.073062,-0.25428,0.059335,-0.24219,0.049531,-0.23313,0.045,-0.24593,0.044355,-0.25844,0.045703,-0.27048,0.049043,-0.28188,0.054375),
    (-0.19094,0.071875,-0.20004,0.070344,-0.20916,0.070645,-0.21807,0.072732,-0.22656,0.076563,-0.23194,0.080916,-0.23734,0.0876,-0.24785,0.10627,-0.25737,0.12919,-0.26516,0.15297,-0.24068,0.1581,-0.21609,0.16129,-0.19467,0.16149,-0.18617,0.16015,-0.17969,0.15766,-0.17969,0.15766,-0.17201,0.1521,-0.16577,0.14532,-0.16105,0.13758,-0.15791,0.12914,-0.15642,0.12026,-0.15664,0.11121,-0.15864,0.10223,-0.1625,0.093594,-0.16789,0.086089,-0.17455,0.079844,-0.1823,0.075005,-0.19094,0.071719),
    (-0.22656,-0.076562,-0.22656,-0.076562,-0.21816,-0.072732,-0.20928,-0.070645,-0.20013,-0.070344,-0.19094,-0.071875,-0.1823,-0.075139,-0.17455,-0.079941,-0.16789,-0.086179,-0.1625,-0.09375,-0.1586,-0.10239,-0.15657,-0.11136,-0.15635,-0.12042,-0.15785,-0.1293,-0.16101,-0.13773,-0.16575,-0.14547,-0.172,-0.15225,-0.17969,-0.15781,-0.18617,-0.16025,-0.19467,-0.16156,-0.21609,-0.16133,-0.24068,-0.15816,-0.26516,-0.15313,-0.25737,-0.12934,-0.24785,-0.10643,-0.23734,-0.087756,-0.23194,-0.081072,-0.22656,-0.076719),
    (-0.18812,-0.21672,-0.20151,-0.22171,-0.21982,-0.22466,-0.24196,-0.22584,-0.26684,-0.22551,-0.32036,-0.22138,-0.37156,-0.21437,-0.35201,-0.16655,-0.32883,-0.11814,-0.31668,-0.096444,-0.30459,-0.077859,-0.29288,-0.063474,-0.28187,-0.054375,-0.27036,-0.048979,-0.25818,-0.045664,-0.24551,-0.044399,-0.2325,-0.045156,-0.24219,-0.049531,-0.25428,-0.059329,-0.26501,-0.07304,-0.27435,-0.089288,-0.28229,-0.1067,-0.29385,-0.13951,-0.29953,-0.16047,-0.29976,-0.16647,-0.29783,-0.17193,-0.29406,-0.17631,-0.28875,-0.17906,-0.2557,-0.18727,-0.23074,-0.19137,-0.20437,-0.19312,-0.18234,-0.19127,-0.1726,-0.18871,-0.16406,-0.18484,-0.15531,-0.17859,-0.16099,-0.18998,-0.16836,-0.20029,-0.17737,-0.20929,-0.18797,-0.21672),
    (0.076875,-0.22359,0.083904,-0.2342,0.08916,-0.24576,0.092454,-0.25811,0.093594,-0.27109,0.091219,-0.28514,0.084614,-0.30245,0.07456,-0.32221,0.061836,-0.34359,0.031499,-0.3879,-0.00015625,-0.42875,-0.031812,-0.3879,-0.062148,-0.34359,-0.074872,-0.32221,-0.084927,-0.30245,-0.091531,-0.28514,-0.093906,-0.27109,-0.092766,-0.25811,-0.089473,-0.24576,-0.084216,-0.2342,-0.077187,-0.22359,-0.078281,-0.23437,-0.075849,-0.24971,-0.069341,-0.26583,-0.05994,-0.28204,-0.048828,-0.29762,-0.026206,-0.32407,-0.010937,-0.33953,-0.0058984,-0.3427,-0.00015625,-0.34375,0.0055859,-0.3427,0.010625,-0.33953,0.025894,-0.32416,0.048516,-0.29773,0.059627,-0.28215,0.069028,-0.26592,0.075536,-0.24976,0.077969,-0.23437),
    (-0.047031,-0.23437,-0.046075,-0.22496,-0.043333,-0.21617,-0.038997,-0.20821,-0.033262,-0.20127,-0.026317,-0.19553,-0.018357,-0.1912,-0.0095724,-0.18846,-0.00015625,-0.1875,0.0092599,-0.18846,0.018044,-0.1912,0.026005,-0.19553,0.032949,-0.20127,0.038685,-0.20821,0.04302,-0.21617,0.045762,-0.22496,0.046719,-0.23437,0.045615,-0.24122,0.042515,-0.24925,0.031602,-0.26771,0.016528,-0.28744,-0.00015625,-0.30609,-0.016841,-0.28744,-0.031914,-0.26771,-0.042827,-0.24925,-0.045927,-0.24122),
    (-0.00015625,-0.15625,-0.016099,-0.15544,-0.031588,-0.15307,-0.046546,-0.14921,-0.060891,-0.14394,-0.074545,-0.13735,-0.087429,-0.12951,-0.099462,-0.12051,-0.11057,-0.11041,-0.12066,-0.099306,-0.12967,-0.087273,-0.13751,-0.074389,-0.1441,-0.060735,-0.14936,-0.046389,-0.15322,-0.031432,-0.1556,-0.015943,-0.15641,1.1102e-16,-0.1556,0.015943,-0.15322,0.031432,-0.14936,0.046389,-0.1441,0.060735,-0.13751,0.074389,-0.12967,0.087273,-0.12066,0.099306,-0.11057,0.11041,-0.099462,0.12051,-0.087429,0.12951,-0.074545,0.13735,-0.060891,0.14394,-0.046546,0.14921,-0.031588,0.15307,-0.016099,0.15544,-0.00015625,0.15625,0.015786,0.15544,0.031276,0.15307,0.046233,0.14921,0.060579,0.14394,0.074233,0.13735,0.087116,0.12951,0.09915,0.12051,0.11025,0.11041,0.12035,0.099306,0.12936,0.087273,0.13719,0.074389,0.14379,0.060735,0.14905,0.046389,0.15291,0.031432,0.15528,0.015943,0.15609,1.1102e-16,0.15528,-0.015943,0.15291,-0.031432,0.14905,-0.046389,0.14379,-0.060735,0.13719,-0.074389,0.12936,-0.087273,0.12035,-0.099306,0.11025,-0.11041,0.09915,-0.12051,0.087116,-0.12951,0.074233,-0.13735,0.060579,-0.14394,0.046233,-0.14921,0.031276,-0.15307,0.015786,-0.15544),
    (0.24328,-0.044219,0.25328,-0.044861,0.26307,-0.046777,0.27253,-0.049954,0.28156,-0.054375,0.29257,-0.063474,0.30428,-0.077859,0.31637,-0.096444,0.32852,-0.11814,0.3517,-0.16655,0.37125,-0.21437,0.32004,-0.22138,0.26652,-0.22551,0.24165,-0.22584,0.21951,-0.22466,0.20119,-0.22171,0.18781,-0.21672,0.17715,-0.20929,0.16814,-0.20029,0.16081,-0.18998,0.15516,-0.17859,0.16391,-0.18484,0.17247,-0.18871,0.18225,-0.19127,0.20422,-0.19312,0.23052,-0.19137,0.25549,-0.18727,0.28859,-0.17906,0.2939,-0.17631,0.29768,-0.17193,0.2996,-0.16647,0.29938,-0.16047,0.29369,-0.13958,0.28213,-0.10676,0.2742,-0.089329,0.26486,-0.073062,0.25413,-0.059335,0.24203,-0.049531,0.23234,-0.045156,0.24344,-0.044219),
    (0.19063,-0.071875,0.20281,-0.070312,0.21482,-0.071914,0.22625,-0.076562,0.23163,-0.080916,0.23703,-0.0876,0.24754,-0.10627,0.25706,-0.12919,0.26484,-0.15297,0.24037,-0.1581,0.21578,-0.16129,0.19436,-0.16149,0.18585,-0.16015,0.17938,-0.15766,0.17938,-0.15766,0.1717,-0.1521,0.16546,-0.14532,0.16074,-0.13758,0.1576,-0.12914,0.1561,-0.12026,0.15633,-0.11121,0.15833,-0.10223,0.16219,-0.093594,0.16758,-0.086089,0.17424,-0.079844,0.18198,-0.075005,0.19063,-0.071719),
    (0.19063,0.071875,0.18198,0.075139,0.17424,0.079941,0.16758,0.086179,0.16219,0.09375,0.15829,0.10239,0.15626,0.11136,0.15604,0.12042,0.15754,0.1293,0.1607,0.13773,0.16544,0.14547,0.17169,0.15225,0.17938,0.15781,0.18916,0.16096,0.20281,0.16188,0.2332,0.1593,0.265,0.15297,0.25721,0.12919,0.2477,0.10627,0.23718,0.0876,0.23178,0.080916,0.22641,0.076563,0.22641,0.076563,0.21801,0.072732,0.20912,0.070645,0.19997,0.070344,0.19078,0.071875),
]


def _region_thai_rosette(w, h, s):
    """
    The Thai rosette imported from reference/laithai.svg.

    Fitted to the panel width undistorted, which leaves a plain band above and
    below.  A border ring, six radial struts and four centre spokes tie it
    together: on its own the artwork is two separate pieces -- the centre ring
    sits inside the motif's central hole touching nothing -- and neither piece
    reaches the frame, so the panel would fail the single-body check.

    The struts have to run at the petal directions: 30, 90, 150, 210, 270 and
    330 degrees.  Along the horizontal axis the motif only reaches r = 0.19, so
    a strut due east would stop in mid-air.
    """
    del s                                   # fixed artwork; no tessellation knob
    sc = w                                  # fit to width, undistorted
    out = [[(pts[i] * sc, pts[i + 1] * sc) for i in range(0, len(pts), 2)]
           for pts in _LAITHAI_LOOPS]

    hw, hh = w / 2.0, h / 2.0
    over, bt, sw = 2.0, 2.5, 2.6           # frame overshoot, border width, strut width

    # Border: outer rectangle positive, inner rectangle negative.  The outer one
    # runs past the opening so build_panel's clip buries it in the frame.
    out.append([(-hw - over, -hh - over), (hw + over, -hh - over),
                (hw + over, hh + over), (-hw - over, hh + over)])
    out.append([(-hw + bt, -hh + bt), (-hw + bt, hh - bt),
                (hw - bt, hh - bt), (hw - bt, -hh + bt)])

    def bar(x0, y0, x1, y1, half):
        """
        Solid rectangle of half-width `half` from (x0, y0) to (x1, y1).

        Wound counter-clockwise deliberately.  Under FillRule.Positive a
        clockwise bar is a *hole*: it would cut the border at every crossing
        instead of tying the artwork to it.
        """
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        nx, ny = -dy / L * half, dx / L * half
        return [(x0 - nx, y0 - ny), (x1 - nx, y1 - ny),
                (x1 + nx, y1 + ny), (x0 + nx, y0 + ny)]

    def exit_r(a):
        """Distance from the centre to the border's outer edge along angle `a`."""
        ca, sa = math.cos(a), math.sin(a)
        ts = []
        if abs(ca) > 1e-9:
            ts.append((hw + over) / abs(ca))
        if abs(sa) > 1e-9:
            ts.append((hh + over) / abs(sa))
        return min(ts)

    # Only the four diagonals get anchors.  The motif is one connected piece,
    # so a handful of ties is enough structurally, and the 90/270 struts would
    # have to cross the whole plain band -- 32 mm of stray line through the top
    # and bottom petals -- to reach a border the diagonals already reach in 12.
    for deg in (30, 150, 210, 330):
        a = math.radians(deg)
        r0 = 0.44 * sc                      # inside the petal tip at r = 0.469
        out.append(bar(r0 * math.cos(a), r0 * math.sin(a),
                       exit_r(a) * math.cos(a), exit_r(a) * math.sin(a), sw / 2.0))

    # Centre ring (r .094) out to the motif's inner disc (r .156), on the same
    # diagonals so the spokes read as radial structure rather than a stray cross.
    for deg in (30, 150, 210, 330):
        a = math.radians(deg)
        out.append(bar(0.085 * sc * math.cos(a), 0.085 * sc * math.sin(a),
                       0.175 * sc * math.cos(a), 0.175 * sc * math.sin(a), 1.1))
    return out


PATTERN_REGIONS = {
    "thai_rosette": _region_thai_rosette,
}


def extrude_region(contours, z0, z1) -> trimesh.Trimesh:
    """
    Extrude a filled region given as contours.

    manifold3d rather than trimesh.creation.extrude_polygon, which needs
    shapely (not a dependency here).  CrossSection unions on construction, so
    the struts merge into the artwork without an explicit boolean.
    """
    import manifold3d
    cs = manifold3d.CrossSection([[(float(x), float(y)) for x, y in c]
                                  for c in contours],
                                 manifold3d.FillRule.Positive)
    m = cs.extrude(z1 - z0).to_mesh()
    tm = trimesh.Trimesh(vertices=np.asarray(m.vert_properties)[:, :3].astype(np.float64),
                         faces=np.asarray(m.tri_verts), process=False)
    if z0:
        tm.apply_translation((0.0, 0.0, z0))
    return tm


PATTERNS = {
    "asanoha": pat_asanoha,
    "mitsukude": pat_mitsukude,
    "kikkou": pat_kikkou,
    "kawari_asanoha": pat_kawari,
    "kagome": pat_kagome,
    "masu": pat_masu_goushi,
    "masu_tsunagi": pat_masu,
    "senbon": pat_senbon,
    "goma_gara": pat_goma,
    "bishamon_kikkou": pat_bishamon,
    "seigaiha": pat_seigaiha,
    "kranok_kan_khot": pat_kranok_kan_khot,
    "dok_phut_tan": pat_dok_phut_tan,
}

LAITHAI = {"kranok_kan_khot", "dok_phut_tan", "thai_rosette"}
PATTERN_FAMILY = {name: "laithai" if name in LAITHAI else "kumiko"
                  for name in list(PATTERNS) + list(PATTERN_REGIONS)}


def pattern_names():
    """Every selectable pattern id, segment and region alike."""
    return sorted(list(PATTERNS) + list(PATTERN_REGIONS))


def kumiko_pattern_names():
    """The eleven line patterns that can wrap around a modern shade."""
    return sorted(name for name in PATTERNS if name not in LAITHAI)


def is_region(pattern: str) -> bool:
    return pattern in PATTERN_REGIONS

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
CAP_UNSAFE = frozenset({"kranok_kan_khot", "dok_phut_tan", "thai_rosette"})
PATTERN_CAP_SAFE = {name: name not in CAP_UNSAFE
                    for name in list(PATTERNS) + list(PATTERN_REGIONS)}


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

    ow, oh = W - 2 * b, H - 2 * b
    ocy = H / 2.0
    lat_top = T - P.rebate_d          # lattice stops short of the back face

    # region pattern: imported artwork, extruded as a filled region ---------
    if is_region(pattern):
        contours = PATTERN_REGIONS[pattern](ow, oh, P.grid)
        art = extrude_region([[(x, y + ocy) for x, y in c] for c in contours],
                             0.0, lat_top)
        # the border deliberately overshoots the opening; trim it back to the
        # same grow margin the slat path uses, so it buries in the frame
        grow = 1.0
        art = intersection(art, box(-ow / 2 - grow, ocy - oh / 2 - grow, -EPS,
                                    ow / 2 + grow, ocy + oh / 2 + grow,
                                    lat_top + EPS))
        panel = union([frame, art])
        lip = P.rebate_lip
        pocket = box(-W / 2 + lip, lip, T - P.rebate_d,
                     W / 2 - lip, H + EPS, T + EPS)
        return cleanup(difference(panel, [pocket]))

    # lattice -------------------------------------------------------------
    # clip a little past the opening so slats bury themselves in the frame
    grow = 1.0
    segs = PATTERNS[pattern](ow, oh, P.grid)
    segs = clip_rect(segs,
                     -ow / 2 - grow, -oh / 2 - grow,
                     ow / 2 + grow, oh / 2 + grow)

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


def build_diffuser_plate(P: Params) -> trimesh.Trimesh:
    """
    A clear plate sharing each groove with the lattice panel, behind it --
    where the paper diffuser sheet otherwise goes.

    Same panel_w x height footprint as a panel, so the groove holds it on all
    four edges; only the thickness differs.  The groove widens to take both
    (slot_w = panel_t + plate_t + slot_clear), and that is what caps plate_t:
    past post - 2*groove_d the two post notches meet and the corner falls off.

    Print in clear PETG or PLA.
    """
    return cleanup(box(-P.panel_w / 2, 0, 0, P.panel_w / 2, P.height, P.plate_t))


def _post_insert_cutter(P: Params):
    """
    Blind pilot hole for a heat-set insert, down from the post's top face.

    Concentric with the post, so what binds is the nearest point of either
    panel groove -- (post/2 - groove_d, 0) -- which is `post_wall` away.
    check_fits has to keep two extrusions there because nothing else can: the
    hole is blind, so the post below it holds the outer corner on and a hole
    that has already opened into a groove still reloads as one watertight body.
    """
    if not P.screwed:
        return []
    return [cyl(P.post_insert_d, P.height - P.post_insert_h, P.height + EPS,
                sections=P.arc)]


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
    # Before the two-piece split below, so "full", "lower" and "upper" all get
    # it right for free: the hole is at the top, so only "upper" keeps it.
    cutters += _post_insert_cutter(P)

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


def _cap_screw_holes(P: Params):
    """
    Plain clearance holes through the cap, one on each post axis.  No
    counterbore and no countersink: the head bears on the floor of the finial
    socket, stands proud of the cap, and the finial's cavity swallows it.
    """
    if not P.screwed:
        return []
    pc = P.post_center
    return [cyl(P.cap_screw_d, -EPS, P.cap_t + EPS, x=sx * pc, y=sy * pc,
                sections=P.arc)
            for sx in (-1, 1) for sy in (-1, 1)]


def _snap_tabs(P: Params, shoulder: float, tenon_h: float):
    """Two centred tabs on opposing tenon faces, aligned with socket reliefs."""
    if not P.snapped:
        return []
    b = P.leg_tenon / 2.0
    w = P.snap_tab_w / 2.0
    z1 = shoulder + tenon_h - SNAP_TIP
    z0 = z1 - SNAP_BAND_H
    o = P.snap_tab_out
    return [box(b - EPS, -w, z0, o, w, z1),
            box(-o, -w, z0, -b + EPS, w, z1)]


def _snap_recesses(P: Params, cx: float, cy: float, z0: float, z1: float):
    """Localised socket reliefs that receive the two expanded snap tabs."""
    if not P.snapped:
        return []
    s = P.leg_socket_sz / 2.0
    o = P.snap_recess_out
    w = P.snap_tab_w / 2.0 + P.leg_clear / 2.0
    return [box(cx + s - EPS, cy - w, z0, cx + o, cy + w, z1),
            box(cx - o, cy - w, z0, cx - s + EPS, cy + w, z1)]


def _finial_sockets(P: Params):
    """
    Square pockets in the cap's top face that take the finial skirts.  Same
    size as the leg sockets in the base underside, so one clearance serves both.

    The main pockets are concentric with and strictly inside the post sockets.
    Snap reliefs stay local to two faces, and check_fits explicitly keeps their
    wider span inside the post sockets and away from the rim and base vents.
    """
    if not P.screwed:
        return []
    s = P.leg_socket_sz / 2.0
    pc = P.post_center
    out = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = sx * pc, sy * pc
            out.append(box(cx - s, cy - s, P.cap_t - P.finial_tenon_h,
                           cx + s, cy + s, P.cap_t + EPS))
            depth0 = P.finial_tenon_h - SNAP_TIP - SNAP_BAND_H
            depth1 = depth0 + SNAP_BAND_H
            out += _snap_recesses(P, cx, cy, P.cap_t - depth1,
                                  P.cap_t - depth0)
    return out


def build_cap_finial(P: Params) -> trimesh.Trimesh:
    """
    One finial for the cap, standing skirt-up for printing.

    Same square footprint and square tenon as the leg under the base, just
    shorter, and hollowed so the proud screw head disappears inside it.
    Modelled skirt-up like the leg: printed as exported the decorative face is
    on the plate, the shoulder round the skirt faces up, and the cavity is a
    blind bore opening upward, so there is nothing to support.  place_parts
    turns it over.
    """
    a = P.leg / 2.0
    b = P.leg_tenon / 2.0
    top = P.finial_h + P.finial_tenon_h
    body = box(-a, -a, 0, a, a, P.finial_h)
    skirt = box(-b, -b, P.finial_h - EPS, b, b, top)
    cavity = cyl(P.finial_cavity_d, top - P.finial_cavity_h, top + EPS,
                 sections=P.arc)
    solid = union([body, skirt] + _snap_tabs(P, P.finial_h,
                                             P.finial_tenon_h))
    return cleanup(difference(solid, [cavity]))


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
    solid = union([body, tenon] + _snap_tabs(P, P.leg_h, P.leg_tenon_h))
    if P.snapped:
        c = P.snap_cavity_sz / 2.0
        cavity = box(-c, -c, P.leg_h + SNAP_ROOT, c, c,
                     P.leg_h + P.leg_tenon_h + EPS)
        solid = difference(solid, [cavity])
    return cleanup(solid)


def _leg_sockets(P: Params):
    """Blind sockets in the base underside, one under each corner post."""
    s = P.leg_socket_sz / 2.0
    pc = P.post_center
    out = []
    z0 = P.leg_tenon_h - SNAP_TIP - SNAP_BAND_H
    z1 = z0 + SNAP_BAND_H
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = sx * pc, sy * pc
            out.append(box(cx - s, cy - s, -EPS, cx + s, cy + s,
                           P.leg_tenon_h))
            out += _snap_recesses(P, cx, cy, z0, z1)
    return out


def _edge_chamfers(P: Params, x0, y0, x1, y1, z):
    """
    Cutters for the four horizontal perimeter edges of an axis-aligned
    rectangle at height `z`.  Each is a square prism turned 45 deg about its
    own edge direction -- the same trick `build_post` uses on the post's
    vertical arris, rotated into the horizontal.

    The prisms overshoot the corners, and the union of two perpendicular
    wedges is exactly the mitred corner, so there is no corner special case.
    """
    c = P.edge_chamfer
    if c <= 0:
        return []
    s = c * math.sqrt(2.0)
    over = 2 * c + 2 * EPS
    lx, ly = (x1 - x0) + 2 * over, (y1 - y0) + 2 * over
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    out = []
    for yy in (y0, y1):
        T = trimesh.transformations.translation_matrix((cx, yy, z))
        R = trimesh.transformations.rotation_matrix(math.radians(45), (1, 0, 0))
        out.append(trimesh.creation.box(extents=(lx, s, s), transform=T @ R))
    for xx in (x0, x1):
        T = trimesh.transformations.translation_matrix((xx, cy, z))
        R = trimesh.transformations.rotation_matrix(math.radians(45), (0, 1, 0))
        out.append(trimesh.creation.box(extents=(s, ly, s), transform=T @ R))
    return out


def build_base(P: Params) -> trimesh.Trimesh:
    """Base plate with holder bore, adapter counterbore, vents, cord channel and
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

    # 45 deg bevel top and bottom.  The underside one is a 45 deg overhang
    # printed as exported, which needs no support.
    cutters += _edge_chamfers(P, -f, -f, f, f, 0.0)
    cutters += _edge_chamfers(P, -f, -f, f, f, t)
    return cleanup(difference(base, cutters))


def build_cap(P: Params, pattern: str) -> trimesh.Trimesh:
    """Top cap with matching joinery and a kumiko-grilled vent."""
    f = P.foot / 2.0
    t = P.cap_t
    cap = box(-f, -f, 0, f, f, t)
    cap = difference(cap, _joint_cutters(P, 0.0, downward=False))

    # vent opening, plus the screw holes and finial pockets when screwed down
    r = P.cap_vent_d / 2.0
    cap = difference(cap, [cyl(P.cap_vent_d, -EPS, t + EPS, sections=P.arc)]
                          + _finial_sockets(P) + _cap_screw_holes(P))

    # coarse grille filling the vent
    g = P.grid * P.cap_grille_f
    segs = PATTERNS[cap_pattern(pattern)](P.cap_vent_d, P.cap_vent_d, g)
    segs = clip_circle(segs, 0.0, 0.0, r + 0.8)
    grille = [slat_box(p, q, P.slat_w + 0.4, 0.0, t) for p, q in segs]
    cap = union([cap] + grille)

    # 45 deg bevel top and bottom, matching the base.  The cap is flipped by
    # _rotx(180) before export, so the modelled top face lands on the plate and
    # its bevel prints as a 45 deg flare -- no support either way up.
    return cleanup(difference(cap, _edge_chamfers(P, -f, -f, f, f, 0.0)
                                 + _edge_chamfers(P, -f, -f, f, f, t)))


def build_socket_ring(P: Params) -> trimesh.Trimesh:
    """
    Adapter that seats in the base counterbore and clamps the lamp holder.
    This is the only part to reprint if your holder differs: change --socket-neck.
    """
    od = P.socket_cbore - 0.4       # slip fit in the base counterbore
    ring = cyl(od, 0, P.socket_cbore_d, sections=P.arc)
    return cleanup(difference(ring, [cyl(P.socket_neck, -EPS,
                                        P.socket_cbore_d + EPS,
                                        sections=P.arc)]))


# --------------------------------------------------------------------------
# Modern cylindrical parts
# --------------------------------------------------------------------------

def _helical_thread(r_root: float, depth: float, pitch: float,
                    z0: float, z1: float, sections: int) -> trimesh.Trimesh:
    """Closed 45-degree triangular thread ribbon for a radial union or cut.

    The centre of the triangular profile advances by one pitch per turn.  Its
    axial half-width equals its radial depth, which makes both printable flanks
    45 degrees.  The ribbon is clipped to the requested engagement interval so
    the first and last turn finish flush with the mating faces.
    """
    # Model a complete turn before and after the engagement interval.  Their
    # profile tails still cross z0/z1 at other angles; starting the centreline
    # exactly at z0 would truncate those periodic flanks around most of the rim.
    centre0, centre1 = z0 - pitch, z1 + pitch
    turns = (centre1 - centre0) / pitch
    steps = max(12, int(math.ceil(turns * max(24, sections))))
    root = r_root - EPS
    vertices = []
    for i in range(steps + 1):
        t = i / steps
        zc = centre0 + (centre1 - centre0) * t
        # Phase is anchored to the assembled engagement start, so male and
        # female helpers remain identical after the shade's z translation.
        theta = 2.0 * math.pi * (zc - z0) / pitch
        co, si = math.cos(theta), math.sin(theta)
        vertices += [
            (root * co, root * si, zc - depth),
            ((r_root + depth) * co, (r_root + depth) * si, zc),
            (root * co, root * si, zc + depth),
        ]

    faces = []
    for i in range(steps):
        a, b = 3 * i, 3 * (i + 1)
        for j in range(3):
            k = (j + 1) % 3
            faces += [(a + j, a + k, b + k), (a + j, b + k, b + j)]
    faces += [(0, 2, 1),
              (3 * steps, 3 * steps + 1, 3 * steps + 2)]
    ribbon = trimesh.Trimesh(vertices=np.asarray(vertices),
                             faces=np.asarray(faces), process=True)
    if not ribbon.is_winding_consistent:
        trimesh.repair.fix_winding(ribbon)
    if ribbon.volume < 0:
        ribbon.invert()

    # The periodic profile extends a full turn past each end before clipping,
    # retaining every angular tail that crosses the engagement boundary.
    clip = cyl(2 * (r_root + depth + 1.0), z0, z1,
               sections=max(24, sections))
    return cleanup(intersection(ribbon, clip))


def _flat_slat_contour(p, q, width):
    """CCW rectangle around a developed-pattern segment, with joined ends."""
    (u0, z0), (u1, z1) = p, q
    du, dz = u1 - u0, z1 - z0
    length = math.hypot(du, dz)
    if length < 1e-6:
        raise ValueError("zero-length wrapped slat")
    overlap = min(width * 0.20, 0.25)
    eu, ez = du / length * overlap, dz / length * overlap
    u0, z0, u1, z1 = u0 - eu, z0 - ez, u1 + eu, z1 + ez
    nu, nz = -dz / length * width / 2.0, du / length * width / 2.0
    return [(u0 + nu, z0 + nz), (u0 - nu, z0 - nz),
            (u1 - nu, z1 - nz), (u1 + nu, z1 + nz)]


def _annular_sector(r_inner, r_outer, z0, z1, a0, a1, sections):
    """Closed curved rail spanning a0..a1, used to weld the wrap seam."""
    steps = max(2, int(math.ceil(abs(a1 - a0) / (2 * math.pi)
                                 * max(24, sections))))
    vertices = []
    for i in range(steps + 1):
        a = a0 + (a1 - a0) * i / steps
        co, si = math.cos(a), math.sin(a)
        vertices += [(r_outer * co, r_outer * si, z0),
                     (r_outer * co, r_outer * si, z1),
                     (r_inner * co, r_inner * si, z1),
                     (r_inner * co, r_inner * si, z0)]
    faces = []
    for i in range(steps):
        a, b = 4 * i, 4 * (i + 1)
        for j in range(4):
            k = (j + 1) % 4
            faces += [(a + j, a + k, b + k), (a + j, b + k, b + j)]
    faces += [(0, 2, 1), (0, 3, 2),
              (4 * steps, 4 * steps + 1, 4 * steps + 2),
              (4 * steps, 4 * steps + 2, 4 * steps + 3)]
    sector = trimesh.Trimesh(vertices=np.asarray(vertices),
                             faces=np.asarray(faces), process=True)
    if not sector.is_winding_consistent:
        trimesh.repair.fix_winding(sector)
    if sector.volume < 0:
        sector.invert()
    return sector


def _conical_frustum(r0, r1, z0, z1, sections):
    """Closed circular frustum with matching facets at both radii."""
    if min(r0, r1, z1 - z0) <= 0 or sections < 3:
        raise ValueError("invalid conical frustum")
    vertices = []
    for z, radius in ((z0, r0), (z1, r1)):
        for i in range(sections):
            a = 2.0 * math.pi * i / sections
            vertices.append((radius * math.cos(a), radius * math.sin(a), z))
    vertices += [(0.0, 0.0, z0), (0.0, 0.0, z1)]
    bottom_c, top_c = 2 * sections, 2 * sections + 1
    faces = []
    for i in range(sections):
        j = (i + 1) % sections
        faces += [(i, j, sections + j),
                  (i, sections + j, sections + i),
                  (bottom_c, j, i),
                  (top_c, sections + i, sections + j)]
    return trimesh.Trimesh(vertices=np.asarray(vertices),
                           faces=np.asarray(faces), process=True)


def _wrapped_pattern_shell(P: Params, pattern: str) -> trimesh.Trimesh:
    """Union in the developed plane, extrude once, then bend into a cylinder.

    Doing every lattice crossing as a 2-D polygon union avoids the near-coplanar
    3-D slivers that otherwise open after the float32 STL round-trip.  The field
    is masked beside the wrap boundary and buried into a split vertical rail;
    its two half-rails weld at +/-pi after bending, giving a deterministic seam.
    """
    import manifold3d

    r_out = P.modern_outer_r
    circumference = 2.0 * math.pi * r_out
    lattice_h = P.modern_lattice_h
    grow = MODERN_LATTICE_OVERLAP
    segs = PATTERNS[pattern](circumference, lattice_h, P.grid)
    contours = [_flat_slat_contour(p, q, P.slat_w) for p, q in segs]
    field = manifold3d.CrossSection(contours, manifold3d.FillRule.Positive)

    # Reserve one ordinary slat width for a deliberate seam rail.  The pattern
    # overlaps each half by 0.2 mm so there can be no tangent-only attachment.
    join = min(0.2, P.slat_w / 4.0)
    pattern_clip = manifold3d.CrossSection.square(
        (circumference - P.slat_w + 2 * join,
         lattice_h + 2 * grow), center=True)
    field = field ^ pattern_clip

    rail_h = lattice_h + 2 * grow
    half_rail = P.slat_w / 2.0
    left_rail = manifold3d.CrossSection.square((half_rail, rail_h))
    left_rail = left_rail.translate((-circumference / 2.0,
                                     -rail_h / 2.0))
    right_rail = manifold3d.CrossSection.square((half_rail, rail_h))
    right_rail = right_rail.translate((circumference / 2.0 - half_rail,
                                       -rail_h / 2.0))

    lower = manifold3d.CrossSection.square((circumference,
                                             MODERN_RING_H + EPS))
    lower = lower.translate((-circumference / 2.0, -P.height / 2.0))
    upper = manifold3d.CrossSection.square((circumference,
                                             MODERN_RING_H + EPS))
    upper = upper.translate((-circumference / 2.0,
                             P.height / 2.0 - MODERN_RING_H - EPS))
    developed = field + left_rail + right_rail + lower + upper
    shade_clip = manifold3d.CrossSection.square((circumference, P.height),
                                                 center=True)
    developed = developed ^ shade_clip

    # Leave a 0.4 mm developed gap so +/-pi never create coincident boundary
    # faces.  A curved rail spans that gap below with 0.6 mm overlap per side.
    # This is the actual split-and-weld seam; unlike merely merging coincident
    # vertices it remains manifold through STL's unindexed float32 round-trip.
    gap_arc = min(0.4, P.slat_w / 4.0)
    bend_span = 2.0 * math.pi - gap_arc / r_out

    # Refinement happens while the mesh is flat and manifold, so every shared
    # edge is split conformingly.  Bounding every developed edge by max_arc
    # guarantees the bent outer sagitta remains <= 0.1 mm.
    max_angle = 2.0 * math.acos(max(-1.0, 1.0 - MODERN_CHORD_ERROR / r_out))
    max_arc = r_out * max_angle
    flat = developed.extrude(P.panel_t).refine_to_length(max_arc)

    def bend(v):
        u, z, depth = v
        theta = bend_span * u / circumference
        radius = P.modern_inner_r + depth
        return (radius * math.cos(theta), radius * math.sin(theta),
                z + P.height / 2.0)

    raw = flat.warp(bend).to_mesh()
    bent = trimesh.Trimesh(
        vertices=np.asarray(raw.vert_properties)[:, :3].astype(np.float64),
        faces=np.asarray(raw.tri_verts), process=False)
    rail_angle = P.slat_w / r_out
    seam = _annular_sector(P.modern_inner_r, r_out, 0.0, P.height,
                           math.pi - rail_angle / 2.0,
                           math.pi + rail_angle / 2.0, P.arc)
    # Keep manifold's indexed result intact for the lower-sleeve union below.
    # Rounding an intermediate boolean can collapse a micron-scale seam edge;
    # build_modern_shade performs the normal cleanup after its final cut.
    return union([bent, seam])


def build_modern_shade(P: Params, pattern: str) -> trimesh.Trimesh:
    """One threaded cylindrical shade with a continuously wrapped lattice."""
    if pattern not in kumiko_pattern_names():
        raise ValueError("modern lantern supports Kumiko line patterns only")

    wrapped = _wrapped_pattern_shell(P, pattern)
    # The main shell stops at the common lattice/ring inner radius.  This lower
    # solid adds the extra half millimetre needed by the female baseline before
    # the bore and helical groove below carve the matching internal profile.
    lower = cyl(P.size, 0.0, MODERN_RING_H + EPS, sections=P.arc)
    shade = union([wrapped, lower])

    bore = cyl(2 * P.modern_thread_bore_r, -EPS,
               MODERN_THREAD_ENGAGEMENT + EPS, sections=P.arc)
    groove = _helical_thread(P.modern_thread_bore_r, MODERN_THREAD_DEPTH,
                             MODERN_THREAD_PITCH, 0.0,
                             MODERN_THREAD_ENGAGEMENT, P.arc)
    # Manifold's final indexed mesh is already closed.  The classic cleanup's
    # 0.1-micron snap is useful for boxes, but can collapse the tiny triangulated
    # edge at a curved lattice crossing; STL's float32 conversion preserves the
    # shared indices here without that intermediate snap.
    return difference(shade, [bore, groove])


def _modern_vent_cutters(P: Params, z0: float, z1: float):
    """Classic tangential vent layout carried onto the circular modern deck."""
    r0, r1 = P.base_vent_r0, P.base_vent_r1
    rm = (r0 + r1) / 2.0
    half_len = math.pi * rm / P.base_vents * 0.60
    out = []
    for j in range(P.base_vents):
        ang = 2 * math.pi * j / P.base_vents
        cx, cy = rm * math.cos(ang), rm * math.sin(ang)
        tx, ty = -math.sin(ang), math.cos(ang)
        a = (cx - tx * half_len, cy - ty * half_len)
        b = (cx + tx * half_len, cy + ty * half_len)
        out.append(slat_box(a, b, r1 - r0, z0, z1))
    return out


def build_modern_base(P: Params) -> trimesh.Trimesh:
    """Upright hollow base; invert with ``modern_base_for_print`` for export."""
    r_out = P.modern_base_r
    shell_top = P.modern_base_h - MODERN_THREAD_ENGAGEMENT
    deck_bottom = P.modern_base_h - P.base_t
    r_root = P.modern_thread_root_r

    # In print orientation the threaded neck grows first.  A full-radius body
    # beginning abruptly above it would create a 4.8 mm horizontal cantilever
    # at the nominal dimensions, so expand at 45 degrees before continuing the
    # cylindrical wall.  Tiny overlaps at both joins keep the boolean robust.
    body = cyl(2 * r_out, 0.0, P.modern_shoulder_z + EPS,
               sections=P.arc)
    shoulder = _conical_frustum(r_out, r_root, P.modern_shoulder_z,
                                shell_top, P.arc)
    neck = cyl(2 * r_root, shell_top - EPS, P.modern_base_h,
               sections=P.arc)
    thread = _helical_thread(r_root, MODERN_THREAD_DEPTH,
                             MODERN_THREAD_PITCH, shell_top,
                             P.modern_base_h, P.arc)
    base = union([body, shoulder, neck, thread])

    # Open underside and 5 mm wall; the last ``base_t`` millimetres form the
    # holder deck.  When a wider independent body makes the deck cross the
    # shoulder, taper the cavity in parallel with the outside instead of
    # carrying a large cylinder through the narrowing wall.  The inherited
    # nominal case stays on the original one-cylinder path byte-for-byte.
    if deck_bottom <= P.modern_shoulder_z:
        cavity = [cyl(2 * P.modern_cavity_r, -EPS,
                      deck_bottom + EPS, sections=P.arc)]
    else:
        cavity = [cyl(2 * P.modern_cavity_r, -EPS,
                      P.modern_shoulder_z + EPS, sections=P.arc)]
        taper_top = min(deck_bottom + EPS, shell_top)
        if taper_top > P.modern_shoulder_z:
            inner_top = (P.modern_base_outer_at(taper_top)
                         - MODERN_BODY_WALL)
            cavity.append(_conical_frustum(
                P.modern_cavity_r, inner_top,
                P.modern_shoulder_z, taper_top, P.arc))
        if deck_bottom + EPS > shell_top:
            cavity.append(cyl(2 * (r_root - MODERN_BODY_WALL),
                               shell_top - EPS, deck_bottom + EPS,
                               sections=P.arc))

    # Inverted for printing, the deck starts on the bed and the open cavity
    # grows upward without a bridge or support.
    cutters = cavity + [cyl(P.socket_bore, deck_bottom - EPS,
                   P.modern_base_h + EPS, sections=P.arc),
               cyl(P.socket_cbore,
                   P.modern_base_h - P.socket_cbore_d,
                   P.modern_base_h + EPS, sections=P.arc)]
    cutters += _modern_vent_cutters(P, deck_bottom - EPS,
                                    P.modern_base_h + EPS)

    # Bottom-open cord notch through the wall into the hollow body.  As on the
    # Classic base, carry the cutter a full cord width past the nominal cavity
    # tangent; stopping at that tangent leaves only a sub-millimetre throat.
    cutters.append(box(-P.cable_w / 2.0, -r_out - EPS, -EPS,
                       P.cable_w / 2.0,
                       P.modern_cable_inner_y,
                       P.cable_h))
    return cleanup(difference(base, cutters))


def modern_base_for_print(P: Params, base=None) -> trimesh.Trimesh:
    """Turn the modern base deck-down so the exported STL needs no support."""
    printed = (base if base is not None else build_modern_base(P)).copy()
    printed.apply_transform(_rotx(180))
    printed.apply_translation((0.0, 0.0, P.modern_base_h))
    return printed


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def _rotz(deg):
    return trimesh.transformations.rotation_matrix(math.radians(deg), (0, 0, 1))


def _rotx(deg):
    return trimesh.transformations.rotation_matrix(math.radians(deg), (1, 0, 0))


def place_modern_parts(P: Params, shade, base, socket_ring=None):
    """Return the two cylindrical bodies (and holder ring) assembled upright."""
    out = {"modern_base": base.copy()}
    q = shade.copy()
    q.apply_translation((0.0, 0.0,
                         P.modern_base_h - MODERN_THREAD_ENGAGEMENT))
    out["modern_shade"] = q
    if socket_ring is not None:
        r = socket_ring.copy()
        r.apply_translation((0.0, 0.0,
                             P.modern_base_h - P.socket_cbore_d))
        out["socket_adapter_ring"] = r
    return out


def build_modern_assembly(P: Params, shade, base, socket_ring=None):
    parts = place_modern_parts(P, shade, base, socket_ring)
    return trimesh.util.concatenate(list(parts.values())), parts


def place_parts(P: Params, panel, post, base, cap, leg=None, plate=None,
                finial=None):
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

    # The four sides.  Each transform maps the panel's local z = 0 -- the
    # lattice face -- to `d` from the lamp axis, facing outward, so `d` is
    # always the outward face of whatever is being placed.
    def _places(d):
        return [
            (_rotz(180) @ _rotx(90), (0.0, -d, z_base)),    # front  (-Y)
            (_rotx(90), (0.0, d, z_base)),                  # back   (+Y)
            (_rotz(90) @ _rotx(90), (-d, 0.0, z_base)),     # left   (-X)
            (_rotz(-90) @ _rotx(90), (d, 0.0, z_base)),     # right  (+X)
        ]

    # Glazed, the panel and the plate sit flush against opposite groove walls,
    # so the whole slot_clear shows up as the gap between them -- the worst
    # case for check_clearances, and it keeps the two off a tangent contact.
    # Unglazed, the panel keeps its centred position.
    glazed = plate is not None and P.plate_t > 0
    panel_face = (pc + P.slot_w / 2) if glazed else (pc + t / 2)
    plate_face = pc - P.slot_w / 2 + P.plate_t

    for idx, (M, offset) in enumerate(_places(panel_face)):
        q = panel.copy()
        q.apply_transform(M)
        q.apply_translation(offset)
        out[f"panel{idx}"] = q

    if glazed:
        for idx, (M, offset) in enumerate(_places(plate_face)):
            q = plate.copy()
            q.apply_transform(M)
            q.apply_translation(offset)
            out[f"plate{idx}"] = q

    c = cap.copy()
    c.apply_translation((0, 0, z_base + P.height - P.groove_d))
    out["cap"] = c

    # Finials sit on the cap's top face, skirt down.  Modelled skirt-up like
    # the leg, so the turn-over lives here rather than in the part.
    if finial is not None and P.screwed:
        z_top = z_base + P.height - P.groove_d + P.cap_t
        for idx, (sx, sy) in enumerate([(-1, -1), (1, -1), (1, 1), (-1, 1)]):
            g = finial.copy()
            g.apply_transform(_rotx(180))
            g.apply_translation((sx * pc, sy * pc, z_top + P.finial_h))
            out[f"finial{idx}"] = g

    # Legs hang below z = 0, so the assembly's origin is the base underside and
    # its z-extent comes out as total_height.  Only ever a preview, never printed.
    if leg is not None:
        for idx, (sx, sy) in enumerate([(-1, -1), (1, -1), (1, 1), (-1, 1)]):
            g = leg.copy()
            g.apply_translation((sx * pc, sy * pc, -P.leg_h))
            out[f"leg{idx}"] = g
    return out


def build_assembly(P: Params, panel, post, base, cap, leg=None, plate=None,
                   finial=None):
    parts = place_parts(P, panel, post, base, cap, leg, plate, finial)
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


def check_modern_fits(P: Params, pattern=None):
    """Modern-only guards for the shade, thread, deck and hollow base."""
    issues = []
    if pattern is not None and pattern not in kumiko_pattern_names():
        issues.append("modern lantern supports Kumiko line patterns only")
    if P.modern_base_diameter <= 2 * (MODERN_BODY_WALL + P.nozzle):
        issues.append("modern base diameter is too small for its 5 mm body wall")
    if P.modern_base_r < P.modern_thread_crest_r - 1e-9:
        issues.append("modern base body is narrower than its threaded neck")
    if P.height <= 2 * MODERN_RING_H + 2 * P.slat_w:
        issues.append("modern shade is too short for two rings and a lattice")
    if P.modern_base_h <= MODERN_THREAD_ENGAGEMENT + P.base_t:
        issues.append("modern base is too short for its thread and mounting deck")
    if P.modern_shoulder_z <= 0:
        issues.append("modern base is too short for its 45-degree shoulder")
    if P.modern_shoulder_z < P.cable_h - 1e-9:
        issues.append("modern shoulder reaches the cable outlet")
    if P.base_t < P.socket_cbore_d + 3 * 0.2 - 1e-9:
        issues.append("modern mounting deck is too thin below the adapter seat")
    deck_bottom = P.modern_base_h - P.base_t
    # The hollow now follows the same 45-degree profile wherever it reaches the
    # shoulder, so the wall there is MODERN_BODY_WALL by construction and the
    # old "disappearing remainder" cannot happen.  What is still reachable is
    # the far end of that taper closing up: the cavity is narrowest under the
    # deck, and once it runs out the base has no hollow left to wire through.
    deck_inner_r = P.modern_base_outer_at(deck_bottom) - MODERN_BODY_WALL
    if deck_inner_r < 2 * P.nozzle - 1e-9:
        issues.append("modern hollow closes up under the mounting deck")
    if P.panel_t < 2 * P.nozzle:
        issues.append("modern lattice depth is under two extrusion widths")
    if P.slat_w < 2 * P.nozzle:
        issues.append(f"slat width {P.slat_w} is under two extrusions")
    if (P.nozzle > 0 and
            abs(P.slat_w / P.nozzle - round(P.slat_w / P.nozzle)) > 1e-6):
        issues.append(f"slat width {P.slat_w} is not a multiple of the nozzle")
    if P.grid <= P.slat_w:
        issues.append("modern pattern pitch must be wider than its slats")
    if P.modern_thread_clear < 0.1 - 1e-9:
        issues.append("thread clearance is under the 0.10 mm radial minimum")
    if P.modern_thread_wall < 2 * P.nozzle - 1e-9:
        issues.append("female thread leaves under two walls at the shade outside")
    if P.modern_thread_root_r <= P.socket_cbore / 2.0 + 2 * P.nozzle:
        issues.append("modern neck has insufficient wall outside the holder deck")
    vents_valid = 0 < P.base_vent_r0 < P.base_vent_r1
    if not vents_valid:
        issues.append("modern vent radii must satisfy 0 < inner < outer")
    if vents_valid and P.socket_cbore / 2.0 >= P.base_vent_r0:
        issues.append("adapter counterbore runs into the ventilation slots")
    if P.base_vents < 3:
        issues.append("modern base needs at least three ventilation slots")
    elif vents_valid:
        rm = (P.base_vent_r0 + P.base_vent_r1) / 2.0
        half_len = math.pi * rm / P.base_vents * 0.60
        vent_outer_corner = math.hypot(P.base_vent_r1, half_len)
        if vent_outer_corner >= P.modern_thread_root_r - 2 * P.nozzle:
            issues.append("modern ventilation slots run into the threaded neck wall")
    ring_od = P.socket_cbore - 0.4
    if P.socket_neck <= 0 or P.socket_neck >= ring_od - 4 * P.nozzle:
        issues.append("lamp holder bore leaves under two walls in the adapter ring")
    if P.socket_bore <= 0 or P.socket_cbore <= P.socket_bore:
        issues.append("adapter counterbore must be wider than the holder bore")
    if P.socket_cbore_d <= 0:
        issues.append("adapter seat depth must be positive")
    cavity_h = P.modern_base_h - P.base_t
    if P.cable_w <= 0 or P.cable_h <= 0:
        issues.append("modern cable outlet dimensions must be positive")
    if P.cable_w >= P.modern_base_diameter - 2 * MODERN_BODY_WALL:
        issues.append("modern cable outlet is wider than the hollow body")
    if P.cable_h >= cavity_h:
        issues.append("modern cable outlet reaches the mounting deck")
    if P.size > min(P.bed[0], P.bed[1]) + 1e-9:
        issues.append("modern shade diameter exceeds the printer bed")
    if P.modern_base_diameter > min(P.bed[0], P.bed[1]) + 1e-9:
        issues.append("modern base diameter exceeds the printer bed")
    if P.height > P.bed[2] + 1e-9:
        issues.append("modern shade height exceeds the printer build height")
    if P.modern_base_h > P.bed[2] + 1e-9:
        issues.append("modern base height exceeds the printer build height")
    return issues


def check_fits(P: Params, pattern=None):
    """Dimensional sanity checks that would otherwise only show up after a print."""
    issues = []
    if P.lantern_style not in ("classic", "modern"):
        issues.append(f"unknown lantern style {P.lantern_style}")
    if P.holder_type not in HOLDER_PRESETS:
        issues.append(f"unknown lamp holder type {P.holder_type}")
    if P.nozzle <= 0:
        issues.append("nozzle diameter must be positive")
    if P.arc < 24:
        issues.append("circle resolution must be at least 24 segments")
    if P.lantern_style == "modern":
        issues += check_modern_fits(P, pattern)
        return issues
    if abs((P.panel_w + P.panel_clear) - P.groove_span) > 1e-9:
        issues.append("panel width does not match the post groove span")
    if abs((P.slot_w - P.panel_t - P.plate_t) - P.slot_clear) > 1e-9:
        issues.append("groove width does not give the intended slot clearance")
    # 3 * 0.2 is 0.6000000000000001, and 0.6 is a real slider stop, so this
    # needs the epsilon or the boundary value rejects itself.
    if 0 < P.plate_t < 3 * 0.2 - 1e-9:
        issues.append("diffuser plate is under three layers thick")
    if P.groove_d >= P.post / 2:
        issues.append("groove depth would cut the post in half")
    # The post's two grooves are notches reaching in to post/2 - groove_d.  Once
    # each is half as wide as that depth leaves, they meet across the diagonal
    # and the post's outer corner falls off as a separate body.  check_part
    # catches the symptom as bodies != 1; this says why.
    if P.slot_w >= P.post - 2 * P.groove_d:
        issues.append("panel groove cuts the corner off the post")
    if P.slat_w < 2 * P.nozzle:
        issues.append(f"slat width {P.slat_w} is under two extrusions")
    if (P.nozzle > 0 and
            abs(P.slat_w / P.nozzle - round(P.slat_w / P.nozzle)) > 1e-6):
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
    leg_socket_span = P.snap_socket_sz if P.snapped else P.leg_socket_sz
    if P.edge_chamfer >= P.foot / 2 - (P.post_center
                                       + max(P.socket_sz, leg_socket_span) / 2):
        issues.append("edge chamfer cuts into the socket walls of the base/cap")
    if 2 * P.edge_chamfer >= P.cap_t:
        issues.append("top and bottom chamfers meet through the cap")
    # The post sockets are cut up from the cap's underside to groove_d and the
    # finial socket eats down from the top; what is between them is the whole
    # floor.  Nothing guarded it: past cap_t the sockets became through holes,
    # which check_part reported as "a floating slat" and the browser, having no
    # body count at all, exported in two pieces without a word.
    if P.cap_floor < 3 * 0.2:
        issues.append("less than three layers of cap left over the post sockets")
    if 2 * P.edge_chamfer >= P.base_t:
        issues.append("top and bottom chamfers meet through the base")
    if P.edge_chamfer > P.cable_floor:
        issues.append("edge chamfer breaks into the cord tunnel mouth")
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
    if P.post_center - leg_socket_span / 2 <= P.base_vent_r1:
        issues.append("leg socket runs into the ventilation slots")
    if P.leg_h < 3 * 0.2:
        issues.append("leg is under three layers tall")
    if P.snap_engagement < 0:
        issues.append("snap engagement cannot be negative")
    if P.snap_engagement > 0.4 + 1e-9:
        issues.append("snap engagement is over the 0.4 mm reusable limit")
    if P.snapped and min(P.leg_tenon_h, P.finial_tenon_h) < (SNAP_TIP
                                                              + SNAP_BAND_H):
        issues.append("snap tenon is too short for its tab and lead-in")
    if P.snapped and P.snap_tab_w < 2 * P.nozzle:
        issues.append("snap tab is under two extrusions wide")
    if P.snapped and P.snap_cavity_sz < 2 * P.nozzle:
        issues.append("leg snap cavity leaves no printable flexure opening")
    if P.snapped and 2 * P.snap_tab_out >= P.leg:
        issues.append("snap tabs reach past the leg shoulder")
    if P.snapped and P.snap_socket_sz >= P.socket_sz:
        issues.append("finial snap recess reaches outside the post socket")
    if 0 < P.post_insert_d < 3.0:
        issues.append("insert hole is smaller than any heat-set insert")
    # The only guard that can exist for this: the hole is blind, so the post
    # reloads as one watertight body even after it has opened into a groove.
    # The 1e-9 is load-bearing -- post 18 with a 4.4 hole lands on
    # 0.7999999999999998 against 2 * 0.4, and 4.4 is a slider stop.
    if P.screwed and P.post_wall < 2 * P.nozzle - 1e-9:
        issues.append("insert hole leaves under two walls to the panel grooves")
    if P.screwed and P.finial_tenon_h < 3 * 0.2:
        issues.append("finial skirt is under three layers deep")
    if P.screwed and P.leg_tenon - P.finial_cavity_d < 4 * P.nozzle - 1e-9:
        issues.append("screw cavity leaves under two walls of finial skirt")
    if P.screwed and (P.finial_h + P.finial_tenon_h
                      - P.finial_cavity_h) < 3 * 0.2:
        issues.append("screw cavity breaks out of the top of the finial")
    return issues


def check_clearances(parts, names=None):
    """
    No two assembled parts may share any volume.

    One panel per axis is enough -- all four sides are identical -- but the
    diffuser plate shares its groove with the panel, so that adjacency is the
    one this has to prove.  Plates are absent when the lamp is unglazed.
    """
    if names is None:
        names = [n for n in ("base", "cap", "post0", "panel0", "panel2",
                             "plate0", "plate2", "leg0", "finial0")
                 if n in parts]
    else:
        names = [n for n in names if n in parts]
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

def _write_region_svg(path: Path, P: Params, pattern, W, H, b, ow, oh):
    """
    Preview for a region pattern.  Filled, not stroked -- the whole point of a
    region is that its thickness varies, so drawing its outline would misreport
    what actually gets printed.  evenodd renders the holes as holes.
    """
    contours = PATTERN_REGIONS[pattern](ow, oh, P.grid)
    pad = 8
    d = []
    for c in contours:
        d.append("M" + " ".join(f"{x:.2f},{y + H / 2:.2f}" for x, y in c) + "Z")
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W + 2 * pad:.0f}" '
        f'height="{H + 2 * pad:.0f}" viewBox="{-W / 2 - pad:.1f} {-pad:.1f} '
        f'{W + 2 * pad:.1f} {H + 2 * pad:.1f}">',
        '<rect x="-1000" y="-1000" width="3000" height="3000" fill="#1b1512"/>',
        f'<rect x="{-W / 2:.2f}" y="0" width="{W:.2f}" height="{H:.2f}" '
        f'fill="#2a211c" stroke="#c8a06a" stroke-width="1.2"/>',
        f'<rect x="{-W / 2 + b:.2f}" y="{b:.2f}" width="{ow:.2f}" '
        f'height="{oh:.2f}" fill="#f6e9c9"/>',
        f'<path fill="#8a5a2b" fill-rule="evenodd" d="{" ".join(d)}"/>',
        f'<text x="{-W / 2 + 4:.1f}" y="{H + 6:.1f}" fill="#c8a06a" '
        f'font-family="monospace" font-size="7">{pattern}  '
        f'{W:.1f} x {H:.1f} x {P.panel_t} mm  imported artwork</text>',
        "</svg>",
    ]
    path.write_text("\n".join(lines))


def write_svg(path: Path, P: Params, pattern: str):
    W, H, b = P.panel_w, P.height, P.panel_border
    ow, oh = W - 2 * b, H - 2 * b
    if is_region(pattern):
        return _write_region_svg(path, P, pattern, W, H, b, ow, oh)
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
    ap.add_argument("--pattern", default="asanoha", choices=pattern_names())
    ap.add_argument("--style", choices=("classic", "modern"),
                    help="lantern style; Classic is the unchanged default")
    ap.add_argument("--size", type=float,
                    help="Classic outer square or Modern outer diameter (mm)")
    ap.add_argument("--height", type=float,
                    help="Classic panel/post or Modern shade height (mm)")
    ap.add_argument("--slat", type=float, help="lattice slat width (mm)")
    ap.add_argument("--panel-thickness", type=float,
                    help="Classic panel thickness or Modern lattice depth (mm)")
    ap.add_argument("--grid", type=float, help="pattern pitch (mm)")
    ap.add_argument("--holder", choices=tuple(HOLDER_PRESETS),
                    help="lamp holder sleeve preset; E27 is the default")
    ap.add_argument("--socket-neck", type=float,
                    help="override the adapter ring bore for your holder (mm)")
    ap.add_argument("--edge-chamfer", type=float,
                    help="bevel on the base and cap perimeter edges (mm)")
    ap.add_argument("--diffuser-plate", type=float,
                    help="clear plate behind each lattice, in the same groove "
                         "(mm); 0 for none, 1.2 is the working value")
    ap.add_argument("--post-insert", type=float,
                    help="heat-set insert pilot hole in each post top (mm); "
                         "0 for none, 4.0 is the working value for M3")
    ap.add_argument("--snap-lock", nargs="?", const=0.2, type=float,
                    metavar="MM", help="reusable foot and finial snap engagement; "
                                        "bare flag uses 0.2 mm, 0 disables")
    ap.add_argument("--modern-base-height", type=float,
                    help="Modern hollow base height (mm); default 90")
    ap.add_argument("--modern-base-diameter", type=float,
                    help="Modern base body diameter (mm); defaults to the "
                         "shade diameter")
    ap.add_argument("--thread-clearance", type=float,
                    help="Modern thread radial clearance (mm); default 0.30")
    ap.add_argument("--split-posts", action="store_true",
                    help="also export the two-piece post")
    ap.add_argument("--all", action="store_true",
                    help="export all patterns and previews available for the selected style")
    ap.add_argument("--out", default=str(Path(__file__).parent))
    args = ap.parse_args(argv)

    # trimesh divides by volume when it inspects an empty boolean result, which
    # is an expected outcome of the clearance checks below
    np.seterr(invalid="ignore", divide="ignore")

    P = Params()
    if args.style is not None:
        P.lantern_style = args.style
    if P.lantern_style == "modern":
        # Style selection applies its preset first; explicit dimensions below
        # remain authoritative in exactly the same way as holder presets.
        P.size = MODERN_DEFAULT_SIZE
        P.height = MODERN_DEFAULT_SHADE_H
    if args.holder is not None:
        P.holder_type = args.holder
        P.socket_neck = HOLDER_PRESETS[args.holder]
    for attr, val in (("size", args.size), ("height", args.height),
                      ("slat_w", args.slat),
                      ("panel_t", args.panel_thickness), ("grid", args.grid),
                      ("socket_neck", args.socket_neck),
                      ("edge_chamfer", args.edge_chamfer),
                      ("plate_t", args.diffuser_plate),
                      ("post_insert_d", args.post_insert),
                      ("snap_engagement", args.snap_lock),
                      ("modern_base_h", args.modern_base_height),
                      ("modern_base_d", args.modern_base_diameter),
                      ("modern_thread_clear", args.thread_clearance)):
        if val is not None:
            setattr(P, attr, val)

    fit_issues = check_fits(P, args.pattern)
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

    if P.lantern_style == "modern":
        print(f"kumiko lamp modern  {P.modern_footprint:.0f} diameter x "
              f"{P.total_height:.0f} mm"
              f"   shade {P.size:.1f} x {P.height:.0f} x {P.panel_t}"
              f"   base {P.modern_base_diameter:.1f} x {P.modern_base_h:.0f}"
              f"   pattern {args.pattern}")
    else:
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

    clearance_names = None
    if P.lantern_style == "modern":
        patterns = kumiko_pattern_names() if args.all else [args.pattern]
        shades = {}
        for name in patterns:
            print(f"  building modern shade: {name} ...", flush=True)
            shades[name] = emit(f"modern_shade_{name}",
                                build_modern_shade(P, name))

        print("  building modern base ...", flush=True)
        base = build_modern_base(P)
        emit("modern_base", modern_base_for_print(P, base))

        print("  building socket ring ...", flush=True)
        socket_ring = emit("socket_adapter_ring", build_socket_ring(P))

        print("  assembling ...", flush=True)
        asm, parts = build_modern_assembly(P, shades[args.pattern], base,
                                           socket_ring)
        emit("assembly_preview", asm, bodies=len(parts), printable=False)
        clearance_names = ("modern_base", "modern_shade",
                           "socket_adapter_ring")
    else:
        patterns = pattern_names() if args.all else [args.pattern]
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

        # --all exports one at the working thickness so stl/diffuser_plate.stl stays
        # in the checked-in set even though the stock lamp is unglazed.
        plate = None
        if P.plate_t > 0:
            print("  building diffuser plate ...", flush=True)
            plate = emit("diffuser_plate", build_diffuser_plate(P))
        elif args.all:
            ref = replace(P, plate_t=1.2)
            print("  building diffuser plate ...", flush=True)
            emit("diffuser_plate", build_diffuser_plate(ref))

        # --all exports one at the working diameter so stl/finial.stl stays in the
        # checked-in set even though the stock lamp is unscrewed.
        finial = None
        if P.screwed:
            print("  building finial ...", flush=True)
            finial = emit("finial", build_cap_finial(P))
        elif args.all:
            print("  building finial ...", flush=True)
            emit("finial", build_cap_finial(replace(P, post_insert_d=4.0)))

        print("  assembling ...", flush=True)
        asm, parts = build_assembly(P, panel, post, base, cap, leg, plate, finial)
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
    problems += check_clearances(parts, clearance_names)

    if P.lantern_style == "modern":
        print(f"\nassembled: {P.modern_footprint:.0f} diameter x "
              f"{P.total_height:.0f} mm"
              f"   built in {time.time() - t0:.1f} s")
    else:
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
