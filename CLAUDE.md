# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A parametric generator for a 3D-printable kumiko lantern. It ships **two independent
implementations of the same solid geometry** plus the STLs they produce. The repo root is
`kumiko-lamp/` (the git repo), not the parent directory.

## Commands

```bash
pip install -r requirements.txt

# Generator. Also the Python test suite: it validates every part and exits 1 on any problem.
python3 kumiko_lamp.py --all              # every pattern, both post styles, all previews
python3 kumiko_lamp.py --pattern kikkou   # one pattern's part set
python3 kumiko_lamp.py --size 150 --height 170 --grid 20 --slat 2.0 --socket-neck 23.5
python3 kumiko_lamp.py --diffuser-plate 1.2 --edge-chamfer 1.5
python3 render_preview.py                 # optional PNG previews, needs matplotlib

# Configurator tests (from web/)
node extract.js && node core.test.js ./extracted.js      # geometry core vs Python's numbers
npm install --no-save playwright && node page.test.js    # the real page in headless Chromium
```

There is no test framework and no single-test selector — each script is one all-or-nothing
run that prints `OK`/`FAIL` lines and exits non-zero on failure. To narrow a Python run, use
`--pattern` instead of `--all`.

Docs say `python3` (correct for Linux/macOS, and what the shebangs use). **On Windows the
interpreter is `python`** — `python3` resolves to the Microsoft Store shim and fails.

`node page.test.js` takes the browser binary from `PW_CHROMIUM` if that is set, and
otherwise uses whatever `playwright install chromium` downloaded
([page.test.js:14-18](web/page.test.js#L14-L18)), so it runs unmodified on all three
platforms.

## The two implementations must stay in sync

| | [kumiko_lamp.py](kumiko_lamp.py) | [web/index.html](web/index.html) |
|---|---|---|
| Geometry | Real CSG (`trimesh` + `manifold3d`) | Winding-rule horizontal-slab decomposition, T-junctions welded after |
| Output | Strictly manifold single shells | Watertight for base/post/ring; lattices are overlapping closed solids |
| Params | `Params` dataclass | `DEFAULTS` + `derive()` |
| Validation | `check_fits()` | `checkFits()` — a direct port, same messages |

A change to any dimension, joint, or pattern has to land in **both**. `core.test.js` is what
enforces it: it hardcodes the Python generator's measured volumes and per-pattern slat counts
([core.test.js:41](web/core.test.js#L41), [core.test.js:55](web/core.test.js#L55)). If you
intentionally change Python geometry, re-run `kumiko_lamp.py` and update those constants —
they are the contract between the two paths, not incidental fixtures.

The four sides are only *placements*, in both implementations (`place_parts`, the
browser's `assemble`). All four are identical, so per-side variation is not expressible
and adding it would fork `slot_w` per side and make the four posts non-identical — each
post serves two adjacent sides.

**`slot_w` is capped by the post, not by the panel.** The post carries its two grooves as
notches reaching in to `post/2 - groove_d`; once a notch is half as wide as the material
that depth leaves, the two meet across the diagonal and the post's outer corner comes away
as a separate body:

```
slot_w  <  post - 2 * groove_d          (6.0 at stock)
```

`check_fits` enforces it in both implementations. This is the ceiling on `plate_t`
(1.6 mm at stock), and it is reachable from the sliders by `panel_t` and `slot_clear`
alone. `slot_w` feeds only `_joint_cutters` / `joineryVoids` and `build_post` / `buildPost`
— **`panel_w` comes from `groove_d` and `post_center`, not from `slot_w`** — so widening
the groove moves no other dimension and no test constant.

The diffuser plate shares each groove with the panel, behind it. In the assembly the panel
sits flush against the outer groove wall and the plate against the inner, so the whole
`slot_clear` is the gap between them; `check_clearances` tests `plate0`/`plate2` to prove
it. `plate_t = 0` collapses `slot_w` to the un-glazed width, which is why the stock lamp
is byte-identical with the feature present.

Minimum-layer checks are written `x < 3 * 0.2`, and `3 * 0.2` is `0.6000000000000001` in
both languages — subtract an epsilon if the boundary is a reachable slider stop.

The browser's panel and cap volumes read a few percent **high** by design (overlapping slats
are double-counted by the divergence theorem), so `core.test.js` bounds them rather than
matching them, and the panel is not volume-compared at all.

## index.html structure

One self-contained file, no build step, no network. Two scripts:

- `<script id="kumiko-core">` — pure geometry, **no DOM access**. Returns the `Kumiko` object.
- `<script id="kumiko-app">` — state, WebGL viewer, sliders, STL/ZIP download.

[web/extract.js](web/extract.js) regex-extracts the core block from the shipped HTML into
`extracted.js` (gitignored) so tests exercise the file that actually ships. Keep the
`id="kumiko-core"` script tag intact and keep the core DOM-free, or extraction and every core
test break.

Adding a parameter means three places: `Params` in Python, `DEFAULTS`/`derive()` in the core,
and the `GROUPS` slider table in the app ([index.html:1378](web/index.html#L1378)).

`derive()` coerces every key with unary `+`, and the app copies `DEFAULTS` into `state`
key by key. **A non-numeric parameter needs an explicit escape hatch** next to the one
`pattern` already has, and an array default would alias `DEFAULTS` into `state` and be
mutated in place.

`GROUPS` items are positional 6-tuples rendered as `input[type=range]` — the **only**
control type in the file. Anything else is a one-off flag on the group (`style:` for the
pattern picker) handled by its own block in `buildRail`. A slider whose `0` means *off*
is how an on/off parameter is expressed without inventing a control — see `plateT`.

## Adding a pattern

Write `f(w, h, s) -> [((x0,y0),(x1,y1)), ...]` centred on the origin and register it in
`PATTERNS` — in both `kumiko_lamp.py` and the core. Two rules, both load-bearing:

- Periodic patterns must **overshoot** the opening; the caller clips.
- Slats that land on another slat must **overlap** it, never merely touch. Two slats abutting
  along a single tangent edge union into geometry that does not survive the float32 STL
  round-trip, and the part reloads with unpaired edges. Use `_extend` / `extend` — and for
  curves `_stroke` / `strokePts`, which apply it along a whole polyline. `_arc` / `arcPts`
  and `_cubic` / `cubic` sample a curve at `n+1` points, so `_stroke` lays down `n` chords.
- **Keep at least ~7° of turn between consecutive chords.** Two slats along a gently curving
  polyline are near-coplanar, and below about 7° the union leaves a pinched edge that costs
  the part its watertightness at *some* pitches. Measured on `seigaiha`'s 110° arcs: 14
  chords (7.9°) passes everywhere, 16 (6.9°) fails the cap at `--grid 12`, 20 (5.5°) fails
  it at 16 and 22, 24 (4.6°) also fails the panel. **A finer curve is not a safer one.**
  Faceting stops mattering long before that limit — at 14 chords the sagitta is already
  under 0.1 mm, a quarter of a nozzle — so nothing is lost by staying coarse. `_stroke`
  cannot see this; it is a property of the sampling.

Patterns also carry metadata in both files: `PATTERN_FAMILY` (`kumiko` | `laithai` — drives
the tab strip in the rail, which is generated, so a new family needs no UI code) and
`PATTERN_CAP_SAFE`, built from the `CAP_UNSAFE` list. The cap clips its field to a disc and
`check_part` requires one body; a lattice always survives that, a curve need not. Name a
pattern in `CAP_UNSAFE` and `cap_pattern()` / `capPattern()` swaps in `CAP_FALLBACK`.

**A curve pattern can still be a tile.** `seigaiha` is the first: a periodic field of arcs
rather than a lattice, so it overshoots and clips like any tile, and it is cap-safe. What
holds it together is worth knowing, because concentric arcs never touch each other — two
circles of radius `r1`, `r2` with centres `a` apart cross only where
`|r1 - r2| < a < r1 + r2`, so **every radius must be large enough to reach its neighbour**
or that arc is a separate body. Seigaiha's three radii all clear it, which turns each row
into one chain running off both sides into the frame.

**Not every pattern is a tile.** `kranok_kan_khot` and `dok_phut_tan` are single panel-sized
compositions laid out as fractions of the opening. Two things follow:
`s` has no pitch to set and drives tessellation instead, and it is in `CAP_UNSAFE` because
scaled into the vent it is a shrunken copy of the panel rather than a grille.

**A composition connects to nothing by default.** A tile gets frame contact and inter-unit
contact for free; a composition gets neither, and `bodies == 1` is what catches it. Every
element must reach the border, and the border must overshoot the opening so `build_panel`'s
`grow = 1.0` clip buries it in the frame. A volute merely sitting *inside* a leaf is a
separate body — root it to the outline.

**Aim spokes at edge midpoints, not vertices.** Where several cells already converge, the
boolean tends to leave a micron-scale sliver that costs the part its watertightness — this
bit `bishamon_kikkou` and is why its spokes run midpoint-to-midpoint.

**Bound spirals by their outer radius.** A log spiral grown outward from a small `r0` is
exponential in turn count and will sprawl clean out of its cell; `_coil` / `coil` wind inward
from `R` instead.

## Two kinds of pattern

`PATTERNS` holds **segment** patterns: `f(w, h, s) -> [segments]`, swept into fixed-width
slats. `PATTERN_REGIONS` holds **region** patterns: `f(w, h, s) -> [contours]`, an outer
contour plus holes, extruded directly. Use `pattern_names()` / `patternNames()` and
`is_region()` / `isRegion()` — iterating `PATTERNS` alone silently skips the regions, which
is how `render_preview.py` and the rail generator first lost `thai_rosette`.

Regions exist because imported artwork has **varying stroke thickness**, which fixed-width
slats cannot express. Python extrudes them with `manifold3d.CrossSection(...).extrude()`
(shapely is not a dependency, so `trimesh.creation.extrude_polygon` is unavailable); the JS
core feeds them straight to `extrudeStack`, whose winding rule (`> 0`) matches
`FillRule.Positive`.

**Winding is load-bearing.** Outer contours positive, holes negative. A helper that emits a
rectangle clockwise produces a *hole*: the rosette's struts did exactly that and cut the
border into pieces instead of tying the artwork to it, giving 8 disconnected components.
`CrossSection.decompose()` is the fastest way to count them in 2D before paying for a build.

**Contours are baked, never parsed at runtime.** `web/index.html` has no file access and both
implementations must emit identical geometry. Regenerate with `tools/svg2pattern.py` and
paste into both. Source artwork lives in `reference/`.

## Cost model: what is actually expensive

Non-obvious, and worth knowing before optimising the wrong thing:

- **Panel and cap-grille slats do not go through `extrudeStack`.** Each slat is an
  independent `prismFromLoop` / `quadPrism`, so their cost is linear and trivial — the
  410-slat curved kranok panel builds in ~37 ms. This is also why those two parts are
  non-manifold and why their volume double-counts crossings.
- **`extrudeStack` is where cost lives**, and it is driven by the number of *distinct vertex
  Y values*, not the loop count: it opens a band at every one. Lattices repeat their Ys and
  stay cheap; feeding it hundreds of curve loops does not (measured: 800 curve loops →
  1.7 M triangles, 49 s). It builds the frame, base and cap plate only.

So a curved pattern is cheap in a panel and would be ruinous if anyone routed it through the
slab extruder. Benchmark the path the part actually takes.

## Tapered walls in `extrudeStack`

A layer may carry a `loopsTop` profile, and `emitWalls` then interpolates each wall quad
between `loops` at `z0` and `loopsTop` at `z1`. This is what the base and cap chamfers
use. Three constraints, all load-bearing:

- **The two profiles must correspond vertex-for-vertex** — same loop count, same length
  per loop. The fallback is per loop, so an untapered loop reuses its own vertices and
  every existing part stays bit-identical.
- **Only the outer loop may actually move**, as a uniform inset. Classification — band
  cuts, crossing splits, the `windingAt` probe — all runs on the *bottom* profile, so a
  void crossing the outer boundary would cross at a different parameter top and bottom
  and the hole would not close. The base's cord tunnel is the one such void, and
  `checkFits` keeps the chamfer out of its z-range for exactly this reason.
- **The interface faces compare `Et[i]` against `Eb[i+1]`**, not one edge set against
  itself. Reusing one would emit a bogus annular shelf the size of the taper.

Python has real CSG and needs none of this: `_edge_chamfers` subtracts four square prisms
turned 45° about their own edge direction, and the union of two perpendicular wedges is
the mitred corner.

## Why the code looks the way it does

- **Everything is validated after a round-trip through the STL file, not in memory**
  (`emit()` in `main()`). STL is float32 with no shared-vertex index; that reload is exactly
  where boolean defects surface.
- **`cleanup()` snaps vertices to 1e-4 and re-welds** before export, for the same reason:
  booleans leave pairs differing by ~1e-7 that fail to weld on reload.
- **`split_bodies()` avoids `trimesh.split()`** deliberately — it skips networkx and, more
  importantly, skips the hole-filling repair that must not be applied to boolean output.
- **`check_clearances()`** booleans the assembled parts against each other; any shared volume
  at all fails the build, so a binding joint is caught before printing.
- The cap is modelled joinery-up but **exported flipped** (`_rotx(180)`), because it must
  print the other way up to avoid supports. The browser mirrors this via
  `capPrintTransform()`.
- The browser's non-manifold lattices are a **measured trade**, not a bug: a single-shell
  decomposition of the crossings costs ~18x the triangles and seconds per slider move. Do not
  "fix" it — `kumiko_lamp.py` is the strictly-manifold path.

## Generated artifacts

`stl/` and `preview/` are checked in and are outputs of `python3 kumiko_lamp.py --all`. Do not
hand-edit them; regenerate.

## Git Rules (Important — Follow every time)

- **Never commit directly to the default branch.** Always create a new branch first.
  This repo's default branch is `main`, not `master`.
- **Branch naming:** `feature/<short-name>` or `fix/<short-name>`.
- **Conventional Commits** for every commit message:
  `feat:` new feature · `fix:` bug fix · `refactor:` code restructuring ·
  `test:` adding/updating tests · `docs:` documentation updates
- **1 commit = 1 task.** Keep commits atomic; never bundle unrelated changes into one commit.
- **PR descriptions must have three sections:**
  - **What** — what was done
  - **Why** — the reason for the change
  - **Test plan** — how it was verified
