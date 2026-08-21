#!/usr/bin/env python3
"""
Flatten an SVG of filled paths into the loop table the generator bakes in.

Run once when the artwork changes; paste the output into the LAITHAI_LOOPS
tables in kumiko_lamp.py and web/index.html.  The browser core is a single
self-contained file with no file or network access, so it cannot read an SVG at
runtime -- and the two implementations have to emit identical geometry, which
only baked coordinates guarantee.

    python tools/svg2pattern.py reference/laithai.svg

Curves are flattened to a chord tolerance rather than a fixed subdivision
count: a fixed count spends as many points on a near-straight arc as on a tight
volute and roughly doubles the table for no fidelity.

Output is in a normalised box: x and y in -0.5..0.5, y pointing UP.  SVG is
y-down, so the flip inverts every winding -- which is exactly what makes the
outer contour positive and the holes negative, as FillRule.Positive wants.
"""

import argparse
import math
import re
import sys
import xml.etree.ElementTree as ET

TOKEN = re.compile(r'([MmLlHhVvCcSsQqTtAaZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)')


def _flatten_cubic(p0, p1, p2, p3, tol, depth=0):
    """Subdivide until the control points sit within `tol` of the chord."""
    if depth >= 16:
        return [p3]
    dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    d = math.hypot(dx, dy)
    if d < 1e-12:
        flat = (math.hypot(p1[0] - p0[0], p1[1] - p0[1]) +
                math.hypot(p2[0] - p0[0], p2[1] - p0[1]))
    else:
        flat = (abs((p1[0] - p3[0]) * dy - (p1[1] - p3[1]) * dx) +
                abs((p2[0] - p3[0]) * dy - (p2[1] - p3[1]) * dx)) / d
    if flat <= tol:
        return [p3]
    mid = lambda a, b: ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    p01, p12, p23 = mid(p0, p1), mid(p1, p2), mid(p2, p3)
    p012, p123 = mid(p01, p12), mid(p12, p23)
    c = mid(p012, p123)
    return (_flatten_cubic(p0, p01, p012, c, tol, depth + 1) +
            _flatten_cubic(c, p123, p23, p3, tol, depth + 1))


def parse_path(d, tol):
    """SVG path data -> list of closed polygons.  Covers M/L/H/V/C/S/Q/T/Z."""
    toks = [(m.group(1), m.group(2)) for m in TOKEN.finditer(d)]
    i = 0
    cmd = None
    cur = start = (0.0, 0.0)
    prev_c2 = prev_q = None
    loops, poly = [], []

    def num():
        nonlocal i
        while toks[i][0] is not None:
            i += 1
        v = float(toks[i][1])
        i += 1
        return v

    def close():
        nonlocal poly
        if len(poly) > 2:
            loops.append(poly)
        poly = []

    while i < len(toks):
        if toks[i][0] is not None:
            cmd = toks[i][0]
            i += 1
            if cmd in 'Zz':
                close()
                cur = start
                prev_c2 = prev_q = None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == 'M':
            x, y = num(), num()
            if rel:
                x, y = x + cur[0], y + cur[1]
            close()
            cur = start = (x, y)
            poly = [cur]
            prev_c2 = prev_q = None
            cmd = 'l' if rel else 'L'          # implicit lineto after moveto
        elif c in 'LHV':
            if c == 'L':
                x, y = num(), num()
                if rel:
                    x, y = x + cur[0], y + cur[1]
            elif c == 'H':
                x = num() + (cur[0] if rel else 0.0)
                y = cur[1]
            else:
                y = num() + (cur[1] if rel else 0.0)
                x = cur[0]
            cur = (x, y)
            poly.append(cur)
            prev_c2 = prev_q = None
        elif c in 'CS':
            if c == 'C':
                x1, y1 = num(), num()
                x2, y2 = num(), num()
                x, y = num(), num()
                if rel:
                    x1, y1 = x1 + cur[0], y1 + cur[1]
                    x2, y2 = x2 + cur[0], y2 + cur[1]
                    x, y = x + cur[0], y + cur[1]
                c1 = (x1, y1)
            else:
                x2, y2 = num(), num()
                x, y = num(), num()
                if rel:
                    x2, y2 = x2 + cur[0], y2 + cur[1]
                    x, y = x + cur[0], y + cur[1]
                c1 = ((2 * cur[0] - prev_c2[0], 2 * cur[1] - prev_c2[1])
                      if prev_c2 else cur)
            c2, p3 = (x2, y2), (x, y)
            poly.extend(_flatten_cubic(cur, c1, c2, p3, tol))
            cur, prev_c2, prev_q = p3, c2, None
        elif c in 'QT':
            if c == 'Q':
                x1, y1 = num(), num()
                x, y = num(), num()
                if rel:
                    x1, y1 = x1 + cur[0], y1 + cur[1]
                    x, y = x + cur[0], y + cur[1]
                q = (x1, y1)
            else:
                x, y = num(), num()
                if rel:
                    x, y = x + cur[0], y + cur[1]
                q = ((2 * cur[0] - prev_q[0], 2 * cur[1] - prev_q[1])
                     if prev_q else cur)
            p3 = (x, y)
            # a quadratic is a cubic with the control point pulled to 2/3
            c1 = (cur[0] + 2.0 / 3 * (q[0] - cur[0]), cur[1] + 2.0 / 3 * (q[1] - cur[1]))
            c2 = (p3[0] + 2.0 / 3 * (q[0] - p3[0]), p3[1] + 2.0 / 3 * (q[1] - p3[1]))
            poly.extend(_flatten_cubic(cur, c1, c2, p3, tol))
            cur, prev_q, prev_c2 = p3, q, None
        else:
            raise SystemExit('unsupported path command %r -- flatten it in the '
                             'editor (Path > Object to Path)' % c)
    close()
    return loops


def area(loop):
    a = 0.0
    for j in range(len(loop)):
        k = (j + 1) % len(loop)
        a += loop[j][0] * loop[k][1] - loop[k][0] * loop[j][1]
    return a / 2.0


def convert(path, tol_frac=0.001, first_path=False,
            preserve_svg_winding=False):
    tree = ET.parse(path)
    root = tree.getroot()
    vb = [float(v) for v in root.get('viewBox').split()]
    vx, vy, vw, vh = vb
    span = max(vw, vh)
    tol = span * tol_frac

    loops = []
    path_elements = [el for el in root.iter()
                     if el.tag.split('}')[-1] == 'path']
    if first_path:
        path_elements = path_elements[:1]
    for el in path_elements:
        loops.extend(parse_path(el.get('d'), tol))

    # normalise into -0.5..0.5 on the longer axis, flipping Y so it points up
    out = []
    for lp in loops:
        pts = [(((x - vx) - vw / 2.0) / span, -(((y - vy) - vh / 2.0) / span))
               for x, y in lp]
        if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
            pts.pop()
        # Flipping SVG's downward Y axis reverses every contour.  Most artwork
        # authored for this project relies on that reversal to put its outer
        # contour positive.  Some externally supplied tiles already carry the
        # positive/negative/positive nesting required by FillRule.Positive;
        # reverse once more for those so their authored winding survives.
        if preserve_svg_winding:
            pts.reverse()
        if first_path:
            # Repeated artwork commonly rounds a nominal boundary coordinate
            # by a few hundredths of an SVG unit.  Snap anything within the
            # flattening tolerance back to the repeat boundary so neighbouring
            # copies weld instead of leaving micron-scale disconnected tips.
            bounds = (-0.5, 0.5, -vh / (2 * span), vh / (2 * span))
            pts = [(next((b for b in bounds[:2] if abs(x - b) <= tol_frac), x),
                    next((b for b in bounds[2:] if abs(y - b) <= tol_frac), y))
                   for x, y in pts]
        out.append(pts)
    return out


def emit(loops, lang):
    rows = []
    for lp in loops:
        body = ','.join('%.4g,%.4g' % (x, y) for x, y in lp)
        rows.append('    [%s],' % body if lang == 'js' else '    [%s],' % body)
    open_, close_ = ('[', ']') if lang == 'js' else ('[', ']')
    return '%s\n%s\n%s' % (open_, '\n'.join(rows), close_)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', nargs='?', default='reference/laithai.svg')
    ap.add_argument('--first-path', action='store_true',
                    help='convert only the first path (one tile from repeated artwork)')
    ap.add_argument('--preserve-svg-winding', action='store_true',
                    help='preserve authored contour signs after the SVG Y-axis flip')
    ap.add_argument('--tolerance', type=float, default=0.001, metavar='FRACTION',
                    help='curve chord tolerance as a fraction of the longest viewBox axis')
    args = ap.parse_args()
    loops = convert(args.source, args.tolerance, args.first_path,
                    args.preserve_svg_winding)
    tot = sum(len(l) for l in loops)
    sys.stderr.write('%d loops, %d points\n' % (len(loops), tot))
    for j, lp in enumerate(loops):
        sys.stderr.write('  loop %2d: %4d pts  area %+9.5f\n' % (j, len(lp), area(lp)))
    print(emit(loops, 'py'))
