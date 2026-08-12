# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A parametric generator for a 3D-printable kumiko lantern with two styles: the original
square **Classic** and a threaded two-part cylindrical **Modern**. It ships **two
independent implementations of the same solid geometry** plus the STLs they produce. The
repo root is `kumiko-lamp/` (the git repo), not the parent directory.

## Commands

```bash
pip install -r requirements.txt

# Generator. Also the Python test suite: it validates every part and exits 1 on any problem.
python3 kumiko_lamp.py --all              # Classic: every pattern, both post styles, previews
python3 kumiko_lamp.py --pattern kikkou   # one pattern's part set
python3 kumiko_lamp.py --size 150 --height 170 --grid 20 --slat 2.0 --socket-neck 23.5
python3 kumiko_lamp.py --panel-thickness 5      # Classic panel / Modern radial depth
python3 kumiko_lamp.py --diffuser-plate 1.2 --edge-chamfer 1.5
python3 kumiko_lamp.py --post-insert 4.0        # M3 inserts, screws, finials
python3 kumiko_lamp.py --holder e14             # 27 mm E14 sleeve preset
python3 kumiko_lamp.py --snap-lock              # 0.2 mm foot + finial detents
python3 kumiko_lamp.py --style modern            # reference cylindrical lamp
python3 kumiko_lamp.py --style modern --all      # 11 shades + base/ring + assembly preview
python3 kumiko_lamp.py --style modern --size 120 --height 240 --modern-base-height 100
python3 kumiko_lamp.py --style modern --modern-base-diameter 140  # Ø100 shade, wider body
python3 kumiko_lamp.py --style modern --holder e14 --thread-clearance 0.35
python3 render_preview.py                 # optional PNG previews, needs matplotlib
python3 -m py_compile kumiko_lamp.py
git diff --check

# Configurator tests (from web/)
node extract.js && node core.test.js ./extracted.js      # geometry core vs Python's numbers
npm install --no-save playwright && node page.test.js    # the real page, over http, stubbed API

# Paid export path (needs both halves up; export.test.js skips loudly if not)
python container/server.py                               # PORT=8901
npx wrangler dev --local --port 8913 --var EXPORT_ORIGIN:http://127.0.0.1:8901 \
  --var DEV_ECHO_MAGIC_LINK:1 --var STRIPE_WEBHOOK_SECRET:whsec_test123
node export.test.js                                      # byte-level guarantees

# Deploy (Cloudflare Worker kumiko-lamp, built from GitHub on push to main)
mkdir -p dist && cp web/index.html dist/                 # the entire build
npx wrangler deploy --dry-run                            # validate wrangler.toml first
```

There is no test framework and no single-test selector — each script is one all-or-nothing
run that prints `OK`/`FAIL` lines and exits non-zero on failure. To narrow a Python run, use
`--pattern` instead of `--all`; add `--style modern` to exercise the cylindrical path.

Docs say `python3` (correct for Linux/macOS, and what the shebangs use). **On Windows the
interpreter is `python`** — `python3` resolves to the Microsoft Store shim and fails.

**`wrangler dev` will not start on Windows while `[[containers]]` is configured**, whatever
`EXPORT_ORIGIN` says: it refuses during "Preparing container image(s)" with *"Local
development with containers is currently not supported on Windows"*, before any of your
code loads. Docker running changes nothing, and `wrangler dev` has no `--enable-containers`
flag to turn it off — the switch is config-only. Either run the dev half under WSL, or point
`-c` at a scratch copy of `wrangler.toml` with the `[[containers]]`, `[[durable_objects]]`
and `[[migrations]]` blocks dropped. The second is not a lesser test: `callExportService`
returns on `EXPORT_ORIGIN` before it ever reads `EXPORT_CONTAINER`
([worker/index.js:100](worker/index.js#L100)), so the exercised path is identical, and
`export.test.js` passes against it. Keep that copy out of the repo — it duplicates the KV id
and `[vars]`, and a stale one deploying is a worse failure than an inconvenient test.

## The paid export path

**The browser no longer produces STLs.** Export posts the slider set to `/api/export`; the
CSG generator builds it and the server refuses anything `check_part` rejects. That refusal
is the product: the page's own Classic lattices are non-manifold by design, so they were
never what anyone should print.

Three surfaces, and the split is load-bearing. `container/` knows geometry and nothing
about payment; `worker/` knows entitlement and nothing about geometry; the container is
never routed publicly, so the Worker is the only way to reach it.

The container is a **Cloudflare Container behind a Durable Object** (`ExportContainer` in
[worker/index.js](web/../worker/index.js)), written against the raw `ctx.container` API
rather than the `@cloudflare/containers` helper so the repo keeps its no-package.json
shape. `image_build_context = "."` is required: the image needs `requirements.txt` and
`kumiko_lamp.py` from the repo root, and the Dockerfile's `COPY` paths assume it.
**`wrangler deploy` needs a running Docker daemon** to build that image, even for
`--dry-run`; `--containers-rollout=none` skips it when you only want to check the config.
Port 8080 appears in three places — the Dockerfile `EXPOSE`, `container/server.py`, and
`getTcpPort` — and they have to agree.

`callExportService` prefers `EXPORT_ORIGIN` over the binding, which is what lets
`wrangler dev` drive a container on localhost. Unset it in production.

- **The container shells out to `kumiko_lamp.py`** rather than importing it, so the whole
  tested sequence — `check_fits`, `emit`, `check_part`, the clearance pass, the exit code —
  is the one a local CLI run takes. **`--params-json` exists because the CLI's sixteen flags
  cannot express the app's thirty sliders**, so without it a customer's actual
  configuration is unbuildable.

**STL bytes are reproducible per platform, not across them.** Same machine, same
parameters gives byte-identical output — but the Linux container and native Windows differ
on `base.stl` and `top_cap.stl`, the two heaviest boolean parts, while `post`, `leg`,
`panel_*`, `socket_adapter_ring` and `diffuser_plate` match exactly. Measured: volumes
agree to full printed precision and both meshes are watertight, single-body and
degenerate-free; `base` merely comes out 1404 triangles on Linux against 1402 on Windows.
That is manifold3d tessellating a boolean differently on a different libm, not a geometry
difference, and no slicer can tell.

It matters for one instruction only: **"verify all stock hashes are unchanged after
regeneration" silently assumes the same platform as last time.** The checked-in `stl/`
artifacts were generated on Windows, so regenerating them in the container or in CI will
show `base` and `top_cap` as changed when nothing is wrong. Compare volume and topology
before believing a hash.
- **The offered pattern list is probed at startup, not hardcoded.** `LAITHAI_ENABLED` hides
  a family and Modern takes kumiko only, so a stale copy would refuse a pattern the app
  offers, or accept one argparse rejects **on stderr with exit 2** — a usage error that
  would reach a customer as "cannot be built" with no reason.
- **Entitlement is written only by the Stripe webhook.** A success redirect proves someone
  came back from a Stripe page, not that a payment settled, and anyone can type that URL.
  Verify over the **raw** body — `json()` then stringify reorders keys — and keep the
  timestamp tolerance, or a captured request can be replayed to restore a cancelled
  subscription.
- **Magic tokens are random and stored in KV**, not signed and self-describing. A signed
  token validates without a lookup but cannot be made single-use, and these links end up in
  mail logs and forwarded threads.
- **Cloudflare `send_email` only delivers to addresses already verified in the account**,
  which is useless for signing up strangers. `sendMagicLink` is a seam for that reason, and
  returns false rather than reporting success when nothing is configured.

**Reachable configurations still fail.** `asanoha` at `grid 12` — the minimum slider stop —
builds a panel and cap that are not watertight, and `post 22` with `groove_d 7` hits the
pre-existing post-by-groove case. Roughly 1 in 18 sampled combinations. The server gate is
what stops those being sold, so never bypass `check_part` to make a build "succeed".

`page.test.js` **serves the page over http from a stub**: Chromium refuses
`fetch('/api/…')` from a `file://` page before it is a request at all, so from disk there is
nothing to intercept. It proves the page asks for the right thing; `export.test.js` proves
the bytes, against a running stack, and skips loudly when nothing is listening.

## Deploying

Cloudflare **Worker with static assets** named `kumiko-lamp`, connected to this GitHub
repo: a push to `main` deploys. The build **must** assemble `dist/` rather than serve
`web/`, which also carries `extract.js` and the two test scripts.

**It is a Worker, not a Pages project** — the dashboard's "import a repository" flow
creates one, which is why the build runs its own deploy command (`npx wrangler deploy`).
That command reads `[assets] directory` from `wrangler.toml`; the Pages spelling
`pages_build_output_dir` makes it fail with *"Missing entry-point to Worker script or to
assets directory"*. There is no `main`, so every request is served from `dist/`. Validate
any change to that file with `npx wrangler deploy --dry-run` before pushing — it reports
the asset count without deploying.

The build environment installs `requirements.txt` (numpy, scipy, trimesh, manifold3d,
~53 MB) because it detects the file at the repo root, even though copying one HTML file
needs no Python at all. Harmless but wasteful, and it couples the deploy to those wheels
still building; pointing the project's root directory at `web/` would end it, at the cost
of moving `wrangler.toml` there too.

`web/index.html` loads with **no network request but its own document** — no CDN, no
webfont, no external image, and a `data:` URI favicon. Keep it that way. The one call it
ever makes is to its own `/api/`, and only on a download.

**There is no doctype, on purpose.** The page renders in quirks mode (`document.compatMode`
is `BackCompat`) and the entire stylesheet was written and measured there; adding one flips
it to standards mode and moves the layout. The `<head>` does carry `charset` and `viewport`
— without the first the Thai UI depends on the server sending a charset, and without the
second a phone lays out at ~980px and none of the responsive CSS below 940px ever runs.

`node page.test.js` takes the browser binary from `PW_CHROMIUM` if that is set, and
otherwise uses whatever `playwright install chromium` downloaded
([page.test.js:14-18](web/page.test.js#L14-L18)), so it runs unmodified on all three
platforms.

## The two implementations must stay in sync

| | [kumiko_lamp.py](kumiko_lamp.py) | [web/index.html](web/index.html) |
|---|---|---|
| Geometry | Real CSG (`trimesh` + `manifold3d`) | Winding-rule horizontal-slab decomposition, T-junctions welded after |
| Output | Strictly manifold single shells | Watertight structural parts and strict Modern shade downloads; Classic/live-preview lattices are overlapping closed solids |
| Params | `Params` dataclass | `DEFAULTS` + `derive()` |
| Validation | `check_fits()` | `checkFits()` — a direct port, same messages |

A change to any dimension, joint, or pattern has to land in **both**. `core.test.js` is what
enforces it: it hardcodes the Python generator's measured volumes and per-pattern slat counts
([core.test.js:41](web/core.test.js#L41), [core.test.js:55](web/core.test.js#L55)). If you
intentionally change Python geometry, re-run `kumiko_lamp.py` and update those constants —
they are the contract between the two paths, not incidental fixtures.

## Classic and Modern styles

`lantern_style` / `lanternStyle` selects the geometry and defaults to `classic`. Keep the
entire Classic path conditional and byte-identical: selecting Modern must not change any
Classic default, part, filename or checked-in artifact. In the configurator the two style
tabs keep separate in-session settings; switching styles hides irrelevant groups without
discarding their values. Every tab, group, part label, note and validation message needs
both English and Thai text.

Modern reuses `size` as **shade** diameter, `height` as shade height and `panel_t` /
`panelT` as radial lattice depth. `--panel-thickness` controls that shared value: Classic
panel thickness in Classic mode and radial lattice depth in Modern mode. The browser
relabels the same `panelT` slider for the active style and its CLI echo must emit
`--panel-thickness` for any non-default value. Its additional public parameters are
`modern_base_d` / `modernBaseD` (omitted or zero means inherit `size`), `modern_base_h` /
`modernBaseH` (90 mm) and `modern_thread_clear` / `modernThreadClear` (0.30 mm), exposed as
`--modern-base-diameter`, `--modern-base-height` and `--thread-clearance`.
`--style {classic,modern}` is the only
style selector. `--style modern --all` emits one Modern base, the stable adapter ring, all
eleven Kumiko shades and the non-printable assembly preview; Classic `--all` continues to
emit all eleven offered patterns and both post styles.

The reference Modern geometry is a Ø100 × 218 mm shade over a Ø100 × 90 mm hollow base.
Shade and base radii are separate derived values: the rings, lattice and both halves of the
thread follow `size`, while the lower hollow body, circular deck, cable outlet and bed
footprint follow `modern_base_d`. The 45° shoulder spans from that body radius to the
shade-sized thread root. The base
has a 5 mm wall, circularly adapted ventilation, the existing Ø40/50 mm holder bore and
counterbore, and a bottom cable outlet. The shade has 10 mm upper and lower rings and wraps
the selected periodic line pattern around the circumference. Subdivide mapped segments as
needed to keep the cylindrical chord error at or below 0.1 mm, split at the angular seam
and weld the seam vertices. Only patterns whose family is `kumiko` are valid: the three
panel-sized Lai Thai compositions
must produce a validation error, never a fallback pattern.

The base's full-radius body ends at `modern_shoulder_z`; from there it contracts at 45
degrees to `modern_thread_root_r` at the bottom of the 10 mm threaded neck. Derive
`modern_shoulder_h` from the **base body radius** minus `modern_thread_root_r`, so the
inverted print grows
from the neck to the body without a horizontal cantilever. Validate both positive shoulder
height and at least two nozzle-width walls wherever the hollow cavity reaches the taper.

The removable joint is a 10 mm-long printed thread with 2 mm pitch, 0.8 mm radial depth,
45° flanks and the configurable radial clearance. It places the shade 10 mm over the base
neck, making the reference assembly 298 mm high. Keep the base model upright in assembly
space but export `modern_base.stl` inverted for support-free printing. Modern outputs are
`modern_base.stl` and `modern_shade_<pattern>.stl`; the reference base and all eleven
reference shades are checked in, but the source comparison STLs are not. Stable part names
do not encode either diameter. Browser ZIP names retain
`kumiko-lamp-modern-100mm-<pattern>.zip` for equal diameters and use the unambiguous
`kumiko-lamp-modern-shade100mm-base140mm-<pattern>.zip` form when they differ.

Both implementations must reject unsafe thread walls/clearance/engagement, a thin mounting
deck, holder conflicts, an impossible shade-to-base shoulder, bad wrapped seams and each
diameter's bed overflow before export. Test the thread at
0.20, 0.30 and 0.60 mm, E27 and E14, single-pattern and `--all` generation, assembled and
exploded placement, and STL round-trips. Hash all existing Classic artifacts before and
after regeneration: adding the style switch is not permission to rewrite them.

The Classic lamp's four sides are only *placements*, in both implementations
(`place_parts`, the browser's `assemble`). All four are identical, so per-side variation
is not expressible and adding it would fork `slot_w` per side and make the four posts
non-identical — each post serves two adjacent sides.

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

**`socket_riser` is Classic only, and its outer diameter is not a slider.** It lifts the
adapter counterbore on a chimney grown out of the base so the lamp holder hangs hidden
inside it instead of standing exposed in the lantern; the tube exists to carry
`socket_cbore` up, so its OD can only be that plus `SOCKET_RISER_WALL` either side — Ø56 at
stock, radius 28, which fits inside the vent ring at `base_vent_r0 = 29` by one millimetre.
Both guards are needed, because `socket_cbore` *is* a slider. `socket_seat_z` is exactly
`base_t` when the riser is 0, so the stock base's cutters are unchanged and `stl/base.stl`
stays byte-identical — the same trick `plate_t` uses. The Modern base prints deck-down
under `modern_base_for_print`, so a riser above that deck would grow into the bed:
`check_modern_fits` rejects it rather than building a base that silently ignores it, and
`MODERN_GROUPS` does not offer the slider.

Minimum-layer checks are written `x < 3 * 0.2`, and `3 * 0.2` is `0.6000000000000001` in
both languages — subtract an epsilon if the boundary is a reachable slider stop. The same
trap bites the insert guards: `post 18` with a 4.4 hole lands on `0.7999999999999998`
against `2 * nozzle`, and `legTenon 10` against a 5.0 insert on `1.5999999999999996`.
Both are reachable stops, so both guards carry `- 1e-9`.

**The cap's floor over the post sockets is `cap_floor`, and nothing used to guard it.**
`_joint_cutters(P, 0.0, downward=False)` cuts up from the underside to `groove_d`; past
`cap_t` the sockets are through holes and the cap comes off the plate in two pieces.
Python's `check_part` reported that as `bodies != 1` *with the wrong reason* ("a floating
slat or an unsupported grille"); the browser has no body count at all and exported it
silently. `cap_floor < 3 * 0.2` is the guard, and it also carries the finial socket's bite
out of the top. Minimal repro was `capT 7, grooveD 7, post 20`.

**The insert hole is the one cut `check_part` cannot audit.** It is blind, so the post
below it holds the outer corner on and the part reloads watertight, one body, at *every*
diameter — including diameters that have already opened into a panel groove. `post_wall >=
2 * nozzle` in `check_fits` is the only thing standing there. Note the binding dimension is
`post/2 - groove_d`, the grooves' nearest approach to the post axis, which does not depend
on `slot_w` — so glazing the lamp does not move it.

**The finial sockets are bridges, not overhangs.** They are pockets in the modelled top
face, and `_rotx(180)` puts that face on the plate, so their floors become four 10.35 mm
dead-flat ceilings 2 mm up. Anchored on all four sides — easier than the cord tunnel's
existing 9 mm bridge, which is anchored on two. Say *bridges* in the docs, not "no
overhang".

**Snap engagement is radial interference, not tab height.** The tab first clears the
tenon by `leg_clear / 2`, then reaches `snap_engagement` past the normal socket wall; its
locked recess restores `leg_clear / 2` around it. Two opposing tabs are 0.4 mm high and
end 0.6 mm before the tip. The lower tenon is hollow only when snapping, while the finial
already flexes around its screw cavity. Keep every snap branch conditional so
`snap_engagement = 0` stays byte-identical with the old parts.

A new **part id** is two more places than you expect: `partLabels` and `partNotes` in
`renderParts` are keyed by it, and a miss renders `undefined.stl · undefined` in Thai. A
new **slider group** is a third: its name and every label need a `TH` entry.

The browser's panel, cap and live Modern-preview volumes read a few percent **high** by
design (overlapping slats are double-counted by the divergence theorem), so
`core.test.js` bounds them rather than matching them. The strict Modern download mesh has
its own Float32 topology and Python-volume checks.

## index.html structure

One self-contained file, no build step, no network. Two scripts:

- `<script id="kumiko-core">` — pure geometry, **no DOM access**. Returns the `Kumiko` object.
- `<script id="kumiko-app">` — state, WebGL viewer, sliders, STL/ZIP download.

[web/extract.js](web/extract.js) regex-extracts the core block from the shipped HTML into
`extracted.js` (gitignored) so tests exercise the file that actually ships. Keep the
`id="kumiko-core"` script tag intact and keep the core DOM-free, or extraction and every core
test break.

Adding a parameter means three places: `Params` in Python, `DEFAULTS`/`derive()` in the core,
and the `GROUPS` slider table in the app ([index.html:1378](web/index.html#L1378)). Style
visibility, CLI echo, dimensions, preview assembly, part downloads and ZIP contents also
branch on `lanternStyle`; cover each branch when adding a style-specific parameter.

Modern diameter controls have one app-only interaction contract. The preset starts with
`size = modernBaseD = 100` and `modernBaseLinked = true`; while linked, Shade diameter
input mirrors into Base diameter. Any unequal base-slider input unlinks them, subsequent
shade changes preserve the chosen base, and setting the base equal to the shade relinks the
pair and restores the clean CLI without `--modern-base-diameter`. Keep the boolean inside
the copied per-style state so a Classic/Modern round trip preserves both the values and
their link state. The controls are `#r-size` and `#r-modernBaseD`, visibly labelled Shade
diameter/Base diameter in both English and Thai.

`derive()` coerces every key with unary `+`, and the app copies `DEFAULTS` into `state`
key by key. **A non-numeric parameter needs an explicit escape hatch** next to the ones
`pattern`, `holderType`, `lanternStyle` and the app-only `modernBaseLinked` have, and an
array default would alias `DEFAULTS` into `state` and be mutated in place.

`holderType` is metadata plus a starting point, not a claim that Edison screw size fixes
the mounting neck. `HOLDER_PRESETS` changes only `socket_neck` / `socketNeck`; the Classic
and Modern bases share the bore, counterbore and stable adapter-ring filename, and the
manual neck override always wins.

`GROUPS` items are positional 6-tuples rendered as `input[type=range]` — the **only**
control type in the file. Anything else is a one-off flag on the group (`style:` for the
pattern picker, plus the Lantern style tabs) handled by its own block in `buildRail`. A
slider whose `0` means *off* is how an on/off parameter is expressed without inventing a
control — see `plateT`.

**`style:` renders nowhere near its group.** The flag stays on the Pattern entry, because
the table is the one place that says which style offers what (`kumikoOnly:` beside it), but
`buildRail` hands it to `buildPicker()`, which draws the family tabs and the pattern buttons
into `#picker` in the `.side` column next to the preview. That is not decoration: the rail
is capped to the viewport and scrolls inside itself, and 280px of buttons in the rail pushed
the Pattern group's **own sliders** under that fold, where a control reads as missing rather
than as scrolled away. Keep `buildPicker()` called from `buildRail()` — that is what makes
the style switch, holder change, language toggle and init paths all refresh it for free.

The `.side` column carries the picker **above** the swatch, and `drawFlat()` asks
`flatBudget()` how tall the artwork may be. The stage row takes the taller of that column
and the preview, so an unbounded swatch pushes the print list below the fold. `flatBudget()`
returns `Infinity` under about a 1000px-tall window, where the picker and the swatch's text
already exceed the preview and clamping would shrink the artwork to a thumbnail without
buying the fit. Any group-height or side-column change wants re-measuring against
`page.test.js`'s "below the rail fold" checks.

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

**The Lai Thai family is currently disabled** by `LAITHAI_ENABLED` — `kumiko_lamp.py`
beside `LAITHAI`, and the same flag in the core. Set both to true to re-enable; they must
agree. It is a switch rather than a deletion because `thai_rosette` is the only *region*
pattern, so removing the family would take `PATTERN_REGIONS`, `extrude_region` and the
imported-artwork extension point with it.

**Metadata is built from `all_pattern_names()` / `allPatternNames()`, selection from
`pattern_names()` / `patternNames()`** — and that split is load-bearing. Point
`PATTERN_FAMILY` or `PATTERN_CAP_SAFE` at the filtered list and a hidden pattern resolves
to `undefined`, which makes every `PATTERN_FAMILY[p] !== 'kumiko'` guard pass for the wrong
reason and `capPattern` stop falling back. Python already builds both maps from
`PATTERNS + PATTERN_REGIONS` directly, so only the browser needed the split.

Patterns also carry metadata in both files: `PATTERN_FAMILY` (`kumiko` | `laithai` — drives
the tab strip in the rail, which is generated, so a new family needs no UI code) and
`PATTERN_CAP_SAFE`, built from the `CAP_UNSAFE` list. The cap clips its field to a disc and
`check_part` requires one body; a lattice always survives that, a curve need not. Name a
pattern in `CAP_UNSAFE` and `cap_pattern()` / `capPattern()` swaps in `CAP_FALLBACK`.

`PATTERN_FAMILY` also gates Modern: only `kumiko` segment patterns can wrap around the
cylinder. A `laithai` selection in Modern is an invalid configuration, not a request to
use `CAP_FALLBACK`. A new Kumiko pattern must therefore pass both the flat Classic panel
and cylindrical seam tests. Subdivide the circumferential mapping for 0.1 mm maximum chord
error, clip/split at the seam and weld after tessellation so the exported shade has no open
edge there.

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
slab extruder. Keep the Modern live preview on its dedicated wrapped-slat path. Its STL/ZIP
export uses a separate cached periodic theta/Z cell complex: slats are rasterized at no
more than half their width (0.8 mm at the reference settings), diagonal-only cell
contacts are conservatively filled, and only the boundary of the connected radial shell is
emitted. The circumferential grid must also satisfy the 0.1 mm chord-error limit and reuse
the same cached vertices across its periodic seam.

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

## The strict Modern shade is a raster, and its boundary is snapped

The browser cannot do a 2-D polygon boolean, so `buildStrictModernShade` rasterizes the
developed pattern onto a θ/z cell grid and emits cell borders — which is what makes it
watertight for free, and what made every diagonal member a **staircase** up to half a cell
off the true edge.

`modernStrictRaster` therefore also computes a per-grid-vertex shift that snaps **boundary
vertices only** onto the slat outline, which `modernStrictMesh` applies inside its vertex
cache. The mask, the cell topology and the shared-vertex cache are untouched, so
watertightness still follows from the same argument; only positions move. Three rules:

- **The union's distance is the smallest *signed* distance, not the smallest magnitude.**
  Picking by magnitude lets a vertex deep inside one slat be claimed by a barely-missed
  neighbour and snapped the wrong way. That erodes every crossing, and it cost seigaiha
  −0.86% → −3.68% against Python before it was caught.
- **Clamp the shift under half a cell per axis**, or two neighbouring vertices can swap
  order and fold a cell.
- **Leave the ring rows alone.** Their outline is the ring itself, `z = ring` is the
  junction the lattice hands off to, and the lower ring's inner face carries the thread.

**Volume cannot see a staircase** — an over-filled cell here cancels an under-filled one
there. `core.test.js`'s `strictBoundaryError` measures the boundary directly instead:
~0.24 mm for a plain raster at the stock 0.8 mm cell, under 0.016 mm once snapped.

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
- The Modern base follows the same separation of concerns: model and assemble it upright,
  then apply its print transform only when exporting so the mounting deck and male thread
  print support-free. Never reuse the print transform in assembled clearance checks.
- The browser's non-manifold Classic/live-preview lattices are a **measured trade**, not a
  bug: a slab decomposition of the crossings costs ~18x the triangles and seconds per
  slider move. Modern downloads solve that separately with the periodic cell-complex
  exporter; `kumiko_lamp.py` remains the exact CSG path.

## Generated artifacts

`stl/` and `preview/` are checked in generated outputs. Classic artifacts come from
`python3 kumiko_lamp.py --all`. Regenerate Modern with `--style modern --all --out` pointed
at a temporary directory, then bring back **only** `modern_base.stl` and the eleven
`modern_shade_<pattern>.stl` files. Its stable adapter ring should match the existing file,
and its style-specific `assembly_preview.stl` is for inspection, not for replacing the
checked-in Classic preview. Do not hand-edit generated files. Verify all stock Classic
hashes are unchanged and the twelve checked-in Modern STLs byte-match fresh output.

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
