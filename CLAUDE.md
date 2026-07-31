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

`node page.test.js` hardcodes `executablePath: '/opt/pw-browsers/chromium'`
([page.test.js:14](web/page.test.js#L14)). On Windows/macOS that path does not exist — edit it
or drop the argument to use Playwright's own download.

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

## Adding a pattern

Write `f(w, h, s) -> [((x0,y0),(x1,y1)), ...]` centred on the origin and register it in
`PATTERNS` — in both `kumiko_lamp.py` and the core. Two rules, both load-bearing:

- Periodic patterns must **overshoot** the opening; the caller clips.
- Slats that land on another slat must **overlap** it, never merely touch. Two slats abutting
  along a single tangent edge union into geometry that does not survive the float32 STL
  round-trip, and the part reloads with unpaired edges. Use `_extend` / `extend` — and for
  curves `_stroke` / `strokePts`, which apply it along a whole polyline.

Patterns also carry metadata in both files: `PATTERN_FAMILY` (`kumiko` | `laithai` — drives
the tab strip in the rail, which is generated, so a new family needs no UI code) and
`PATTERN_CAP_SAFE`, built from the `CAP_UNSAFE` list. The cap clips its field to a disc and
`check_part` requires one body; a lattice always survives that, a curve need not. Name a
pattern in `CAP_UNSAFE` and `cap_pattern()` / `capPattern()` swaps in `CAP_FALLBACK`.

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
