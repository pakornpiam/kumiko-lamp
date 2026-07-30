# Kumiko Lamp — parametric, print-ready STL set

A square four-panel kumiko lantern for an E27 LED bulb. Every part prints flat with
**no supports**, and the whole thing assembles with sliding mortise-and-groove joints —
no glue, no screws, no fasteners of any kind.

Assembled: **190 × 190 × 224 mm**. Designed for the 256 mm bed of the A1 / P1S / X1C.

![assembled lamp](preview/assembly.png)

## Two ways to build it

**[`web/index.html`](web/index.html) — the configurator.** Open it in a browser: drag
sliders for size, pattern, joint clearances and E27 sizing, turn the lamp in 3D, and
download the STLs (individually or as a zip). No install, no network, one self-contained
file. It re-solves the joinery live and refuses to export a lamp that will not go together.

**`kumiko_lamp.py` — the generator.** Same lamp, same numbers, built with real CSG so
every part is a strictly manifold shell. Use this if your slicer is fussy, or for the
pre-generated set in `stl/`.

The two agree: for identical settings the base, posts and adapter ring come out of the
browser watertight and volume-identical to the Python parts. The difference is in the
lattices — see [Manifold-ness](#manifold-ness) below.

---

## Print list

| STL | Qty | Size (mm) | Orientation on the plate |
|---|---|---|---|
| `panel_asanoha.stl` | **4** | 155.7 × 210 × 4 | Flat, **lattice face down**. As exported — drop it on the plate. |
| `post.stl` | **4** | 18 × 18 × 210 | Standing upright, as exported. Use a brim. |
| `base.stl` | **1** | 190 × 190 × 16 | Flat, as exported. Underside is dead flat. |
| `top_cap.stl` | **1** | 190 × 190 × 10 | Flat, as exported — **already flipped** into print orientation. |
| `socket_adapter_ring.stl` | **1** | Ø49.6 × 4 | Flat. See *E27 holder* below. |

Swap in `panel_kikkou.stl`, `panel_mitsukude.stl` or `panel_kawari_asanoha.stl` for a
different motif — they are dimensionally identical and interchangeable.

`post_lower.stl` + `post_upper.stl` are an **optional** two-piece post joined by an 8 mm
pin (117 mm and 105 mm tall). Use them only if you would rather not print a 210 mm tall
slender tower, or you are on a 180 mm-Z machine. Print 4 of each *instead of* `post.stl`.

`assembly_preview.stl` is **not for printing** — it is the assembled lamp, for checking
fit in a viewer.

![patterns](preview/patterns.png)

## Slicer settings (Bambu Studio)

| | |
|---|---|
| Nozzle / layer | 0.4 mm / 0.2 mm — use 0.16 mm on the panels for a crisper lattice |
| Wall loops | **4** — slats are 1.6 mm wide, exactly 4 × 0.4, so they come out fully solid with no infill inside them |
| Infill | 15 % gyroid (only the frames, posts, base and cap ever see it) |
| Supports | **Off** for every part |
| Brim | 5 mm on the posts and panels; optional elsewhere |

Rough filament: one full set is 1309 cm³ of solid volume (1624 g if it were 100 % dense).
The panels and posts are effectively solid; the base and cap are slabs that infill
hollows out considerably. At 15 % infill expect very roughly **700–800 g** — your
slicer's estimate is the one to trust.

## Assembly

1. Drop the four **posts** into the corner sockets of the **base**.
2. Slide each **panel** down between two posts — the grooves in the posts catch its
   side edges, and its bottom edge lands in the groove in the base. Lattice faces out.
3. Fit the **top cap** over the four post tops and the four panel top edges.

It is a friction fit throughout, so the cap lifts off to change the bulb. Joint
clearances are 0.4 mm on the grooves and 0.3 mm on panel width. If your printer runs
tight and the panels bind, raise `slot_clear` and reprint the posts — that is the one
part you would need to redo.

## Diffuser

The back of every panel is pocketed 0.6 mm deep, and the pocket runs out through the top
edge. Slide a sheet of **shoji paper or vellum** down into it before you fit the cap.
The pocket holds it on the other three sides; the top is hidden inside the cap groove.
Cut the sheet to **139.7 × 202 mm**. Traditionally this is glued — a little PVA around
the edge is plenty, but friction alone works.

Leave it out entirely if you want hard-edged shadows thrown on the wall instead.

## E27 holder

The base has a Ø40 mm through-bore, a Ø50 × 4 mm counterbore around it, and a cord tunnel
out through one side wall. The tunnel is enclosed, not open at the bottom, so the lamp
sits flat and cannot rock on its own cord.

`socket_adapter_ring.stl` seats in the counterbore and is what your holder clamps
against. **Its Ø26.5 mm bore is a guess at your hardware** — E27 holders vary a lot.
Measure the threaded neck on yours and reprint just the ring:

```bash
python3 kumiko_lamp.py --socket-neck 23.5
```

It is a 5 g, few-minute print, which is exactly why the fit lives in a separate part
rather than in the base.

## Heat — please read

**LED bulb only, 9 W maximum.** A filament or halogen bulb will soften and deform this
lamp. PLA starts to go at around 60 °C.

Ventilation is built in: 1640 mm² of open area through the cap grille and a 16-slot ring
in the base under the bulb. That is sized for a cool-running LED and nothing more.

I would **print the base and top cap in PETG** — they are the parts nearest the bulb, and
PETG holds up to roughly 80 °C. PLA is fine for the panels and posts. All-PETG is fine too.

Mains wiring is your responsibility. If you are not confident terminating a lamp holder,
use a ready-made corded E27 socket rather than making one up.

## Customising

Every dimension lives in the `Params` dataclass at the top of `kumiko_lamp.py`. The ones
worth reaching for are on the command line:

```bash
python3 kumiko_lamp.py --all                    # every pattern + both post styles
python3 kumiko_lamp.py --pattern kikkou         # one pattern's part set
python3 kumiko_lamp.py --grid 20                # finer lattice
python3 kumiko_lamp.py --size 150 --height 170  # smaller lamp (fits an A1 mini)
python3 kumiko_lamp.py --slat 2.0               # chunkier slats (keep it a multiple of 0.4)
```

Adding a pattern means writing one `f(w, h, s) -> [segments]` function and registering it
in `PATTERNS`. Two rules, both learned the hard way:

- Periodic patterns must **overshoot** the opening they are given; the caller clips.
- Anything that lands on another slat must **overlap** it, never merely touch. Two slats
  that abut along a single tangent edge union into geometry that does not survive the
  float32 STL round-trip, and the part comes back with unpaired edges.

```bash
pip install -r requirements.txt
python3 kumiko_lamp.py --all
python3 render_preview.py     # optional, needs matplotlib
```

On Windows the interpreter is normally `python`, not `python3` — substitute it throughout.

## Manifold-ness

The browser has no CSG engine, so `web/index.html` builds geometry a different way — a
winding-rule slab decomposition, with T-junctions welded afterwards. The result:

| Part | From the browser |
|---|---|
| `base`, `post`, `socket_adapter_ring` | Single **watertight** shell, volume identical to the Python part |
| `panel_*`, `top_cap` | One closed solid per slat, overlapping where slats cross |

There are **no open edges** in any of it — every edge is shared by an even number of
faces. But in the lattices the shared ones sit on four faces rather than two, so a strict
manifold check calls them non-manifold and your slicer may offer to repair them. Every
slicer unions overlapping closed solids correctly, so they print as drawn.

That is a deliberate trade. Decomposing the crossings into a single shell is exact, but
costs eighteen times the triangles and takes seconds per change — measured, not guessed —
which is no way to drive a slider. `kumiko_lamp.py` is the strictly-manifold path.

One consequence worth knowing: the volume the configurator reports for the panel and cap
counts overlapping slats twice, so it reads a few percent high. The other three parts are
exact.

## How this was checked

`kumiko_lamp.py` fails loudly rather than writing a bad file. Per run it asserts:

- **Parameters**, before any geometry: panel width actually matches the post groove span,
  groove clearances come out as intended, slat width is a whole multiple of the nozzle,
  the cord tunnel does not break into the groove above it, post sockets do not burst out
  of the side of the base.
- **Every exported part, reloaded from its STL** — not the mesh in memory. Watertight,
  consistent winding, a single connected body, and inside the build volume. The round-trip
  through float32 with no shared-vertex index is exactly where defects show up.
- **Assembled clearance**: the parts are transformed into place and booleaned against each
  other. Any shared volume at all is an error, so a binding joint fails the build.

The configurator is checked the same way, headlessly: its geometry core runs in Node and
is compared against the Python generator's measured volumes (`base` matches to 0.00%,
`post` 0.06%, ring 0.25%) and against its pattern segment counts, slat for slat. The page
itself is then driven in headless Chromium — sliders, pattern switches, bed-fit warnings,
a deliberately unassemblable configuration to confirm it blocks rather than exports, and a
real download whose bytes are loaded back as a mesh. The core is extracted from the
shipped HTML for those tests, so what is verified is what ships.

What that does **not** cover is a test print — I have not run one. Shrinkage and your
printer's dimensional accuracy are the remaining unknowns, and the joints are where they
would show. If something binds, `slot_clear` / `panel_clear` / `socket_clear` are the
three numbers to adjust — the configurator exposes all three.

## Licence

MIT — see [LICENSE](LICENSE). Print it, sell prints of it, fork it, change it.
