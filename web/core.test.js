/* Verify the JS geometry core against the Python generator's measured output. */
const path = process.argv[2] || './core.js';
const K = require(path.startsWith('.') || path.startsWith('/') ? path : './' + path);

let fails = 0;
function check(ok, msg, extra) {
  if (!ok) fails++;
  console.log((ok ? '  OK   ' : '  FAIL ') + msg + (extra ? '   ' + extra : ''));
}

// ---------------------------------------------------------------- closure
/* For any closed surface the area-weighted normals cancel exactly:
   the integral of n dA over the boundary of a solid is zero.  A gap leaves a
   residual proportional to the size of the hole, and unlike edge pairing this
   is unaffected by the T-junctions that slab decomposition legitimately
   produces.  Reported relative to total surface area. */
function closure(tris) {
  let rx = 0, ry = 0, rz = 0, area = 0;
  for (let i = 0; i < tris.length; i += 9) {
    const ux = tris[i+3]-tris[i], uy = tris[i+4]-tris[i+1], uz = tris[i+5]-tris[i+2];
    const vx = tris[i+6]-tris[i], vy = tris[i+7]-tris[i+1], vz = tris[i+8]-tris[i+2];
    const nx = uy*vz - uz*vy, ny = uz*vx - ux*vz, nz = ux*vy - uy*vx;
    rx += nx; ry += ny; rz += nz;
    area += Math.hypot(nx, ny, nz);
  }
  const res = Math.hypot(rx, ry, rz) / (area || 1);
  return { closed: res < 1e-9, res };
}

/* Re-read the actual binary STL representation and pair edges by exact
   float32 bit patterns.  This catches axis/seam coordinates which are equal in
   intent but differ by a few machine epsilons before serialization. */
function stlTopology(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const count = dv.getUint32(80, true), edges = new Map();
  let degenerate = 0, signedVolume = 0;
  const parent = Array.from({ length: count }, (_, i) => i);
  const find = i => parent[i] === i ? i : (parent[i] = find(parent[i]));
  const join = (a, b) => { a = find(a); b = find(b); if (a !== b) parent[b] = a; };
  const vertex = o => dv.getUint32(o, true).toString(16) + ',' +
                        dv.getUint32(o + 4, true).toString(16) + ',' +
                        dv.getUint32(o + 8, true).toString(16);
  const xyz = o => [dv.getFloat32(o, true), dv.getFloat32(o + 4, true),
                    dv.getFloat32(o + 8, true)];
  for (let i = 0; i < count; i++) {
    const o = 84 + i * 50;
    const v = [vertex(o + 12), vertex(o + 24), vertex(o + 36)];
    const p = xyz(o + 12), q = xyz(o + 24), r = xyz(o + 36);
    const ux = q[0] - p[0], uy = q[1] - p[1], uz = q[2] - p[2];
    const vx = r[0] - p[0], vy = r[1] - p[1], vz = r[2] - p[2];
    const cx = uy * vz - uz * vy, cy = uz * vx - ux * vz,
          cz = ux * vy - uy * vx;
    if (cx * cx + cy * cy + cz * cz < 1e-18) degenerate++;
    signedVolume += p[0] * (q[1] * r[2] - q[2] * r[1]) -
                    p[1] * (q[0] * r[2] - q[2] * r[0]) +
                    p[2] * (q[0] * r[1] - q[1] * r[0]);
    for (let j = 0; j < 3; j++) {
      const a = v[j], b = v[(j + 1) % 3];
      const key = a < b ? a + '|' + b : b + '|' + a;
      if (!edges.has(key)) edges.set(key, []);
      edges.get(key).push({ face: i, a, b });
    }
  }
  let nonTwo = 0, sameDirection = 0;
  for (const uses of edges.values()) {
    if (uses.length !== 2) { nonTwo++; continue; }
    join(uses[0].face, uses[1].face);
    if (uses[0].a !== uses[1].b || uses[0].b !== uses[1].a)
      sameDirection++;
  }
  return { nonTwo, sameDirection, degenerate, signedVolume: signedVolume / 6,
           components: new Set(parent.map((_, i) => find(i))).size };
}

// ---------------------------------------------------------------- run
console.log('parameters');
const P = K.derive({});
check(Math.abs(P.panelW - 155.7) < 1e-9, 'panel width 155.7', P.panelW.toFixed(2));
check(Math.abs(P.foot - 190) < 1e-9, 'base/cap footprint 190', P.foot.toFixed(1));
check(Math.abs(P.totalHeight - 236) < 1e-9, 'assembled height 236', P.totalHeight.toFixed(1));
check(P.holderType === 'e27' && K.HOLDER_PRESETS.e27 === 26.5,
      'stock holder is the 26.5 mm E27 preset');
const E14 = K.derive({ holderType: 'e14' });
check(E14.holderType === 'e14' && E14.socketNeck === 27,
      'E14 preset selects a 27 mm sleeve bore');
check(K.derive({ holderType: 'e14', socketNeck: 27.5 }).socketNeck === 27.5,
      'manual holder neck overrides the E14 preset');
check(K.checkFits(P).length === 0, 'stock parameters pass checkFits');
check(K.checkFits(K.derive({ slatW: 1.5 })).length > 0, 'non-nozzle-multiple slat rejected');
check(K.checkFits(K.derive({ grooveD: 9 })).length > 0, 'groove deeper than half the post rejected');
/* Reachable from the sliders alone: panelT maxes at 7 and slotClear at 1.0, which
   gives slotW 8.0 against a 6.0 ceiling.  Python's build_post comes back as two
   bodies there -- the corner falls off. */
check(K.checkFits(K.derive({ panelT: 7, slotClear: 1.0 })).length > 0,
      'groove that cuts the post corner off rejected');
check(K.checkFits(K.derive({ panelT: 5.6 })).length > 0,
      'groove exactly at the post-corner limit rejected');
check(K.checkFits(K.derive({ panelT: 5.4 })).length === 0,
      'groove just under the post-corner limit accepted');
check(K.checkFits(K.derive({ edgeChamfer: 3 })).length > 0,
      'chamfer past the cord tunnel floor rejected');
check(K.checkFits(K.derive({ edgeChamfer: 3, cableFloor: 3 })).length === 0,
      'chamfer clears once the tunnel floor is raised');
check(K.checkFits(K.derive({ edgeChamfer: 5, cableFloor: 5 })).length > 0,
      'chamfer into the socket walls rejected');
check(Math.abs(K.derive({}).slotW - 4.4) < 1e-9 && K.derive({}).glazed === false,
      'unglazed groove is 4.4 and takes the panel alone');
const G = K.derive({ plateT: 1.2 });
check(Math.abs(G.slotW - 5.6) < 1e-9 && G.glazed === true,
      'glazed groove widens to 5.6 for panel plus plate', G.slotW.toFixed(2));
check(K.checkFits(K.derive({ plateT: 1.6 })).length > 0,
      'plate thick enough to reach the post corner rejected');
check(K.checkFits(G).length === 0, 'the working 1.2 mm plate passes');
check(K.checkFits(K.derive({ plateT: 0.4 })).length > 0,
      'plate under three layers rejected');
check(K.checkFits(K.derive({ plateT: 0.6 })).length === 0,
      'plate at exactly three layers accepted');
check(K.derive({}).screwed === false && K.derive({}).capFloor === 4,
      'the stock cap is unscrewed with 4 mm of floor over the sockets');
const M3 = K.derive({ postInsertD: 4 });
check(M3.capScrewD === 3.4 && Math.abs(M3.finialCavityD - 6.6) < 1e-9 &&
      Math.abs(M3.finialCavityH - 3.4) < 1e-9,
      'M3 derives a 3.4 clearance hole and a 6.6 x 3.4 cavity');
check(M3.totalHeight === 244, 'finials add their height to the lamp');
check(K.checkFits(M3).length === 0, 'the working M3 insert passes');
/* Both boundaries land on a float that is not quite the integer, and both are
   reachable slider stops -- 4.4 gives a wall of 0.7999999999999998. */
check(K.checkFits(K.derive({ postInsertD: 4.4 })).length === 0,
      'an insert leaving exactly two walls to the groove accepted');
check(K.checkFits(K.derive({ postInsertD: 4.6 })).length > 0,
      'an insert leaving under two walls to the groove rejected');
check(K.checkFits(K.derive({ postInsertD: 5, post: 20 })).length === 0,
      'M4 fits once the post is widened');
check(K.checkFits(K.derive({ postInsertD: 5 })).length > 0,
      'M4 does not fit an 18 mm post');
check(K.checkFits(K.derive({ postInsertD: 2.8 })).length > 0,
      'an insert hole under 3 mm rejected');
/* The guard nothing used to make: past capT the post sockets punch through and
   the cap comes off the plate in two pieces. */
check(K.checkFits(K.derive({ capT: 7, grooveD: 7, post: 20 })).length > 0,
      'a groove as deep as the cap rejected');
check(K.checkFits(K.derive({ capT: 7, grooveD: 6.5, post: 20 })).length > 0,
      'half a millimetre of cap floor rejected');
check(K.checkFits(K.derive({ capT: 7, grooveD: 6, post: 20 })).length === 0,
      'a millimetre of cap floor accepted');
check(K.checkFits(K.derive({ postInsertD: 4, capT: 8.5 })).length > 0,
      'a finial socket that eats the cap floor rejected');
const SNAP = K.derive({ snapEngagement: 0.2 });
check(SNAP.snapped && Math.abs(SNAP.snapTabOut - 5.375) < 1e-9 &&
      Math.abs(SNAP.snapRecessOut - 5.55) < 1e-9,
      '0.2 snap derives its tab interference and locked clearance');
check(K.checkFits(K.derive({ snapEngagement: 0.05 })).length === 0,
      'minimum snap engagement passes');
check(K.checkFits(K.derive({ snapEngagement: 0.4 })).length === 0,
      'maximum reusable snap engagement passes');
check(K.checkFits(K.derive({ snapEngagement: 0.45 })).length > 0,
      'snap engagement over 0.4 rejected');
check(K.checkFits(K.derive({ snapEngagement: -0.05 })).length > 0,
      'negative snap engagement rejected');
check(K.checkFits(K.derive({ snapEngagement: 0.2, legTenon: 4 })).length > 0,
      'snap tenon without a printable flexure opening rejected');

console.log('\nmodern parameters and guards');
const MODERN = K.derive({ lanternStyle: 'modern', size: 100, height: 218 });
check(MODERN.lanternStyle === 'modern' && MODERN.totalHeight === 298,
      'Modern preset assembles to 100 x 298 mm');
check(Math.abs(MODERN.modernOpenW - Math.PI * 100) < 1e-9 &&
      MODERN.modernOpenH === 198,
      'Modern field develops over the 100 mm circumference and between 10 mm rings');
check(Math.abs(MODERN.modernThreadCoreR - 45.2) < 1e-9 &&
      Math.abs(MODERN.modernThreadCrestR - 46) < 1e-9 &&
      Math.abs(MODERN.modernFemaleThreadR - 45.5) < 1e-9 &&
      Math.abs(MODERN.modernThreadWall - 3.7) < 1e-9,
      'Modern male and female thread radii carry 0.30 mm radial clearance');
check(Math.abs(MODERN.modernShoulderH - 4.8) < 1e-9 &&
      Math.abs(MODERN.modernShoulderZ - 75.2) < 1e-9 &&
      Math.abs(MODERN.modernShoulderH -
               (MODERN.modernOuterR - MODERN.modernThreadCoreR)) < 1e-9,
      'Modern base contracts to its thread root on a 45-degree shoulder');
const modernCableEdgeY = -Math.sqrt(
  MODERN.modernCavityR ** 2 - (MODERN.cableW / 2) ** 2);
check(Math.abs(MODERN.modernCavityR - 45) < 1e-9 &&
      Math.abs(MODERN.modernCableInnerY + 36) < 1e-9 &&
      MODERN.modernCableInnerY > modernCableEdgeY,
      'Modern cord outlet carries its full width into the hollow base');
check(K.checkFits(MODERN).length === 0, 'Modern nominal parameters pass checkFits');
for (const clear of [0.2, 0.3, 0.6])
  check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                               modernThreadClear: clear })).length === 0,
        `Modern ${clear.toFixed(2)} mm thread clearance passes`);
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             modernThreadClear: 0.05 })).length > 0,
      'Modern thread clearance under 0.10 mm rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             modernThreadClear: 3.3 })).length > 0,
      'Modern thread clearance that leaves a thin shade wall rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             pattern: 'kranok_kan_khot' })).length > 0,
      'Modern mode rejects Lai Thai instead of substituting a pattern');
check((() => { try { K.buildModernShade(MODERN, 'thai_rosette'); return false; }
                 catch (_) { return true; } })(),
      'Modern shade builder rejects a Lai Thai region directly');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 80, height: 218 })).length > 0,
      'Modern diameter that collides with vents and thread rejected');
const ventCorner = K.derive({ lanternStyle: 'modern', size: 85.4, height: 218 });
check(Math.abs(ventCorner.modernVentCornerR - 37.20368763798631) < 1e-9,
      'Modern vent guard derives the tangential slot corner radius');
check(K.checkFits(ventCorner).some(m => /ventilation slots run into/.test(m)),
      'Modern size 85.4 rejects the vent corner that reaches the neck wall');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 85.8,
                             height: 218 })).length === 0,
      'Modern size 85.8 clears the complete vent corner');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             baseVentR0: 38, baseVentR1: 37 })).length > 0,
      'Modern inverted ventilation radii rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             nozzle: 0 })).some(m => /Nozzle diameter/.test(m)),
      'non-positive nozzle rejected before geometry');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             arc: 23 })).some(m => /Circle resolution/.test(m)),
      'circular resolution below 24 segments rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 22 })).length > 0,
      'Modern shade too short for its rings rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             modernBaseH: 26 })).length > 0,
      'Modern base too short for its deck and thread rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             modernBaseH: 14, baseT: 1 }))
        .some(m => /too short for its 45-degree shoulder/.test(m)),
      'Modern base without room for its 45-degree shoulder rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             modernBaseH: 20, baseT: 5, panelT: 7 }))
        .some(m => /shoulder reaches the cable outlet/.test(m)),
      'Modern shoulder is kept clear of the cable outlet');
/* The cavity now follows the outer 45 deg profile, so a deck riding above the
   shoulder tapers with it instead of carrying a wide cylinder through a
   narrowing wall.  That configuration builds; what is still reachable is the
   far end of the taper closing up entirely. */
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             baseT: 5 })).length === 0,
      'Modern deck above the shoulder is carried by the tapered hollow');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 20, height: 218,
                             baseT: 5 }))
        .some(m => /hollow closes up under the mounting deck/.test(m)),
      'Modern hollow that closes up under the deck rejected');
check(!K.checkFits(K.derive({ lanternStyle: 'modern', size: 22, height: 218,
                              baseT: 5 }))
        .some(m => /hollow closes up under the mounting deck/.test(m)),
      'Modern hollow with two extrusions left accepted');

/* The base carries its own diameter; only the threaded neck stays shade-derived. */
const MLINK = K.derive({ lanternStyle: 'modern', size: 100, height: 218 });
check(MLINK.modernBaseD === 0 && MLINK.modernBaseDiameter === 100 &&
      MLINK.modernFootprint === 100,
      'Modern base diameter follows the shade until it is set');
const MWIDE = K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                         modernBaseD: 140 });
check(MWIDE.modernBaseDiameter === 140 && MWIDE.modernBaseR === 70 &&
      MWIDE.modernFootprint === 140 && MWIDE.foot === 140,
      'an independent base diameter drives the body and the footprint');
check(Math.abs(MWIDE.modernShoulderH - (70 - MWIDE.modernThreadCoreR)) < 1e-9,
      'a wider body reaches the shade-derived neck down a taller shoulder');
check(K.checkFits(MWIDE).length === 0, 'the wider base passes checkFits');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             modernBaseD: 60 }))
        .some(m => /narrower than its threaded neck/.test(m)),
      'a base narrower than its own neck rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             modernBaseD: 300 }))
        .some(m => /base diameter exceeds the printer bed/.test(m)),
      'an oversize base diameter is reported separately from the shade');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             panelT: 0.4 })).length > 0,
      'Modern lattice under two extrusions rejected');
check(K.checkFits(K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                             socketNeck: 49 })).length > 0,
      'Modern holder bore with a thin adapter wall rejected');

console.log('\npattern segment counts (vs Python)');
const segCounts = { asanoha: 350, kawari_asanoha: 286, kikkou: 79, mitsukude: 118,
                    kagome: 248, masu: 12, masu_tsunagi: 292, senbon: 22,
                    goma_gara: 162, bishamon_kikkou: 204, seigaiha: 972,
                    kranok_kan_khot: 550, dok_phut_tan: 980 };
for (const name of Object.keys(K.PATTERNS)) {
  const segs = K.clipRect(K.PATTERNS[name](P.openW, P.openH, P.grid),
                          -P.openW/2, -P.openH/2, P.openW/2, P.openH/2);
  const want = segCounts[name];
  check(Math.abs(segs.length - want) <= 2, `${name}: ${segs.length} slats`, `python ${want}`);
}

console.log('\npattern families and cap safety');
check(K.PATTERN_FAMILY.kranok_kan_khot === 'laithai', 'kranok is in the Lai Thai family');
check(Object.keys(K.PATTERNS).filter(n => K.PATTERN_FAMILY[n] === 'kumiko').length === 11,
      'eleven kumiko patterns');
check(Object.keys(K.PATTERNS).filter(n => K.PATTERN_FAMILY[n] === 'laithai').length === 2,
      'two Lai Thai patterns');
check(K.capPattern('asanoha') === 'asanoha', 'cap keeps a kumiko pattern');
/* kranok is a panel-sized composition, not a repeating field: squeezed into the
   vent it is a shrunken copy of the panel, so the cap falls back. */
check(K.capPattern('kranok_kan_khot') === 'kikkou', 'cap falls back off the composition');
check(K.capPattern('dok_phut_tan') === 'kikkou', 'Phut Tan cap falls back off the composition');
check(K.capPattern('thai_rosette') === 'kikkou', 'rosette cap falls back off the composition');

/* Region patterns: imported artwork, a filled outer contour plus holes rather
   than swept slats.  There is no slat count to compare, so the contract with
   Python is the contour count and the fact that it resolves to one piece. */
console.log('');
console.log('region patterns');
check(K.isRegion('thai_rosette'), 'thai_rosette is a region pattern');
check(!K.isRegion('asanoha'), 'asanoha is not a region pattern');
/* Lai Thai is registered and still buildable, just not offered.  The metadata
   must keep answering for it, or the Modern guards start passing because
   PATTERN_FAMILY returns undefined rather than because it returns 'laithai'. */
check(K.patternNames().length === 11, `11 selectable patterns`,
      String(K.patternNames().length));
check(K.allPatternNames().length === 14, `14 registered patterns`,
      String(K.allPatternNames().length));
check(K.patternNames().every(n => K.PATTERN_FAMILY[n] === 'kumiko'),
      'nothing Lai Thai is on offer');
check(K.PATTERN_FAMILY.kranok_kan_khot === 'laithai' &&
      K.PATTERN_FAMILY.thai_rosette === 'laithai',
      'a hidden pattern still resolves its family');
check(K.buildPanel(P, 'thai_rosette').region === true,
      'a hidden pattern still builds when asked directly');
{
  const cont = K.PATTERN_REGIONS.thai_rosette(P.openW, P.openH);
  check(cont.length === 26, `rosette contours ${cont.length}`, 'python 26');
  const m = K.buildPanel(P, 'thai_rosette');
  check(m.region === true && m.slats === 0, 'rosette panel built as a region');
  const bb = K.bbox(m.tris);
  check(Math.abs(bb.size[0] - 155.7) < 0.01 && Math.abs(bb.size[1] - 210) < 0.01,
        'rosette panel bbox 155.7 x 210',
        bb.size.map(v => v.toFixed(1)).join(' x '));
}
{
  const a = K.buildCap(P, 'kranok_kan_khot').tris.length;
  const b = K.buildCap(P, 'kikkou').tris.length;
  check(a === b, 'the fallback cap is the kikkou cap', `${a/9} vs ${b/9} tris`);
}

console.log('\nparts vs Python trimesh');
/* Strictly manifold parts must match the Python volume closely.  The panel and
   cap carry lattices built as overlapping slat prisms, so the divergence
   theorem double-counts every crossing: their volume can only come out HIGH,
   and is bounded rather than matched.  Both builds now carry the same four
   45 deg perimeter chamfers, so the cap's remaining excess is the grille
   alone -- if this drifts DOWN through the lower bound, the browser's chamfer
   is removing more than Python's. */
const PY = { post: 56.5, base: 506.0, top_cap: 318.3, socket_adapter_ring: 5.5,
             leg: 5.6, diffuser_plate: 39.2, finial: 3.3 };
const LATTICE = { top_cap: 1 };
const built = {
  post: K.buildPost(P),
  base: K.buildBase(P),
  top_cap: K.buildCap(P, 'asanoha'),
  socket_adapter_ring: K.buildRing(P),
  leg: K.buildLeg(P),
  /* Measured at the working thickness -- the stock lamp is unglazed, so P's
     own plateT is 0 and would give an empty solid. */
  diffuser_plate: K.buildDiffuserPlate(K.derive({ plateT: 1.2 })),
  /* Measured with the insert fitted — the stock lamp is unscrewed, so P's own
     postInsertD is 0 and there would be no finial to build. */
  finial: K.buildCapFinial(K.derive({ postInsertD: 4 }))
};
for (const [name, m] of Object.entries(built)) {
  const v = Math.abs(K.volume(m.tris)) / 1000;
  const want = PY[name];
  const err = (v - want) / want * 100;
  const ok = LATTICE[name] ? (err > -0.5 && err < 5) : Math.abs(err) < 1.5;
  check(ok, `${name} volume ${v.toFixed(1)} cm3`,
        `python ${want} (${err > 0 ? '+' : ''}${err.toFixed(2)}%, ${m.tris.length/9} tris` +
        (LATTICE[name] ? ', lattice bound' : '') + ')');
  const c = closure(m.tris);
  check(c.closed, `${name} surface closed`, `normal residual ${c.res.toExponential(1)}`);
}

console.log('\npanel');
const panel = K.buildPanel(P, 'asanoha');
const bb = K.bbox(panel.tris);
check(Math.abs(bb.size[0] - 155.7) < 0.01 && Math.abs(bb.size[1] - 210) < 0.01 &&
      Math.abs(bb.size[2] - 4) < 0.01, 'panel bbox 155.7 x 210 x 4',
      bb.size.map(v => v.toFixed(1)).join(' x '));
check(panel.slats === 350, `panel slat count ${panel.slats}`, 'python 350');
console.log(`  note  panel is ${panel.tris.length/9} tris; volume double-counts ` +
            `overlapping slats so it is not compared to Python`);

console.log('\nbed fit + export');
const all = K.buildAll({});
check(all.problems.length === 0, 'buildAll produced no problems');
check(all.parts.every(p => p.fits), 'every part fits the 256 bed');
const stl = K.stlBinary(built.post.tris);
check(stl.length === 84 + (built.post.tris.length/9) * 50, 'STL byte length correct',
      `${stl.length} bytes`);
const dv = new DataView(stl.buffer);
check(dv.getUint32(80, true) === built.post.tris.length/9, 'STL triangle count header');
const zip = K.zipStore([{ name: 'a.stl', data: stl }]);
check(zip[0] === 0x50 && zip[1] === 0x4b, 'ZIP magic bytes');

/* The plate row is conditional, so an unglazed lamp stays six rows long with
   post second -- which is what the page tests select on. */
check(all.parts.length === 6 && all.parts[0].id === 'panel_asanoha' &&
      all.parts[1].id === 'post', 'unglazed print list is six parts, panel then post');
check(all.assembly.filter(p => p.clear).length === 0, 'nothing translucent unglazed');
const glazed = K.buildAll({ plateT: 1.2 });
check(glazed.parts.length === 7 && glazed.parts[1].id === 'diffuser_plate',
      'glazing adds a plate row under the panel row');
check(glazed.parts[0].qty === 4 && glazed.parts[1].qty === 4,
      'four panels and four plates',
      `${glazed.parts[0].qty} + ${glazed.parts[1].qty}`);
check(glazed.assembly.filter(p => p.clear).length === 4,
      'all four plates render translucent');
check(glazed.assembly.filter(p => p.kind === 'panel').length === 8,
      'panels and plates both explode as panels');
/* The plate must sit inboard of the lattice, with the whole slotClear as the
   gap between them -- the same stacking Python's place_parts uses. */
const gp = glazed.P, front = glazed.assembly.find(p => p.name === 'panel0');
const fplate = glazed.assembly.find(p => p.name === 'plate0');
const ys = t => { let lo = Infinity, hi = -Infinity;
  for (let i = 1; i < t.length; i += 3) { lo = Math.min(lo, t[i]); hi = Math.max(hi, t[i]); }
  return [lo, hi]; };
const [pLo, pHi] = ys(front.tris), [qLo, qHi] = ys(fplate.tris);
check(Math.abs(pLo + gp.postCenter + gp.slotW / 2) < 1e-6,
      'panel sits flush against the outer groove wall', pLo.toFixed(3));
check(Math.abs((qHi - qLo) - gp.plateT) < 1e-6, 'plate is plateT thick');
check(Math.abs((qLo - pHi) - gp.slotClear) < 1e-6,
      'slotClear falls entirely between panel and plate',
      (qLo - pHi).toFixed(3));

const screwed = K.buildAll({ postInsertD: 4 });
check(screwed.parts.length === 7 && screwed.parts[5].id === 'finial' &&
      screwed.parts[1].id === 'post',
      'screwing adds a finial row under the leg row, post still second');
check(screwed.assembly.filter(p => /^finial/.test(p.name)).length === 4,
      'four finials placed');
/* The screws are bought, not printed, so they are drawn and never listed.
   Sizes are the contract with Python, which prints the same line from the same
   derivation: `4 x M3 x 8 mm socket cap screws`. */
check(screwed.P.screwName === 'M3' && screwed.P.screwLen === 8,
      'a 4.0 insert hole asks for M3 × 8 screws',
      `${screwed.P.screwName} × ${screwed.P.screwLen} ` +
      `(window ${screwed.P.screwLenMin}–${screwed.P.screwLenMax})`);
check(K.derive({ postInsertD: 3.5 }).screwName === 'M2.5' &&
      K.derive({ postInsertD: 4.4 }).screwName === '3.4 mm',
      'off-series pilot holes are quoted as a diameter, not rounded to a screw');
/* M4 wants 8 mm of thread and the blind hole gives 6.5, so nothing stock fits;
   that is reported, never refused -- the lamp still prints. */
const noStock = K.buildAll({ postInsertD: 5, post: 20 });
check(noStock.P.screwLen === null && noStock.problems.length === 0,
      'no stock length is a null recommendation, not a build failure',
      noStock.problems.join(' | '));
check(screwed.assembly.filter(p => p.kind === 'screw').length === 4 &&
      screwed.parts.every(p => p.id !== 'screw'),
      'four screws drawn in the assembly and none in the print list');
check(all.assembly.filter(p => p.kind === 'screw').length === 0,
      'an unscrewed lamp draws no screws');
/* One insert per post, filling the top of its blind hole: postInsertH is
   insert plus 0.8 of relief, so the stock 6.5 is an M3's 5.7. */
check(screwed.P.insertLen === 5.7, 'the 6.5 mm hole takes a 5.7 mm insert',
      String(screwed.P.insertLen));
const inserts = screwed.assembly.filter(p => p.kind === 'insert');
check(inserts.length === 4 && all.assembly.filter(p => p.kind === 'insert').length === 0,
      'four inserts drawn, one per post, and none when unscrewed');
const insertBox = K.bbox(inserts[0].tris);
check(Math.abs(insertBox.size[2] - screwed.P.insertLen) < 1e-6 &&
      Math.abs(insertBox.hi[2] - (screwed.P.baseT - screwed.P.grooveD +
                                  screwed.P.height)) < 1e-6,
      'each insert sits in the top of the post it threads into',
      `${insertBox.size[2].toFixed(1)} tall, top at ${insertBox.hi[2].toFixed(1)}`);
const e14 = K.buildAll({ holderType: 'e14' });
check(e14.parts[5].label === 'E14 adapter ring' && e14.P.socketNeck === 27,
      'E14 build labels and sizes the adapter ring');
const snapped = K.buildAll({ postInsertD: 4, snapEngagement: 0.2 });
check(snapped.problems.length === 0 &&
      snapped.assembly.filter(p => /^leg/.test(p.name)).length === 4 &&
      snapped.assembly.filter(p => /^finial/.test(p.name)).length === 4,
      'snap build places all four feet and four screw-head finial caps');
for (const name of ['base', 'top_cap', 'leg', 'finial']) {
  const part = snapped.parts.find(p => p.id === name);
  const c = closure(part.mesh.tris);
  check(c.closed, `snapped ${name} surface closed`, `normal residual ${c.res.toExponential(1)}`);
}
const SNAP_PY = { base: 505.9, top_cap: 317.4, leg: 5.3, finial: 3.3 };
for (const [name, want] of Object.entries(SNAP_PY)) {
  const part = snapped.parts.find(p => p.id === name);
  const got = part.vol / 1000, err = (got - want) / want * 100;
  const ok = name === 'top_cap' ? (err > -0.5 && err < 5) : Math.abs(err) < 1.5;
  check(ok, `snapped ${name} volume ${got.toFixed(1)} cm3`,
        `python ${want} (${err > 0 ? '+' : ''}${err.toFixed(2)}%)`);
}

/* The socket riser: a chimney on the base carrying the adapter seat 60 mm up,
   so the lamp holder hangs hidden inside it.  Volume is the contract here as
   everywhere else -- python kumiko_lamp.py --socket-riser 60 measures 578.3 --
   and the bbox proves the tube is actually there rather than the counterbore
   having merely moved. */
const risen = K.buildAll({ socketRiser: 60 });
check(risen.problems.length === 0, 'a 60 mm socket riser builds clean',
      risen.problems.join(' | '));
const risenBase = risen.parts.find(p => p.id === 'base');
const RISER_PY = 578.3;
const risenVol = risenBase.vol / 1000;
const risenErr = (risenVol - RISER_PY) / RISER_PY * 100;
check(Math.abs(risenErr) < 1.5, `risen base volume ${risenVol.toFixed(1)} cm3`,
      `python ${RISER_PY} (${risenErr > 0 ? '+' : ''}${risenErr.toFixed(2)}%)`);
check(Math.abs(risenBase.bbox.size[2] - 76) < 1e-6,
      'risen base stands baseT + riser tall', risenBase.bbox.size[2].toFixed(1));
check(closure(risenBase.mesh.tris).closed, 'risen base surface closed');
check(K.buildAll({ lanternStyle: 'modern', size: 100, height: 218,
                   socketRiser: 60 }).problems
       .some(m => /Classic-only/.test(m)),
      'a riser on the Modern base is refused');

/* The vent ring is a floor, not a fixture: a wide counterbore -- or the riser
   tube around it -- pushes the whole ring outward instead of being refused for
   sharing the room with slots no control in the app can move.  The counterbore
   slider reaches 74, so all of that range has to build. */
check(K.derive({}).ventR0 === 29 && K.derive({}).ventR1 === 37,
      'stock leaves the ring exactly where it was',
      `${K.derive({}).ventR0}-${K.derive({}).ventR1}`);
const wide = K.derive({ socketCbore: 74 });
check(wide.ventR0 === 39 && wide.ventR1 === 47,
      'a Ø74 counterbore steps the ring out, keeping its width',
      `${wide.ventR0}-${wide.ventR1}`);
const wideRisen = K.derive({ socketCbore: 74, socketRiser: 60 });
check(wideRisen.ventR0 === wideRisen.socketRiserOd / 2 + 2,
      'with a riser the ring clears the tube, not just the pocket',
      `${wideRisen.ventR0}-${wideRisen.ventR1}`);
const cbore74 = K.buildAll({ socketCbore: 74 });
check(cbore74.problems.length === 0, 'a Ø74 counterbore builds clean',
      cbore74.problems.join(' | '));
const CBORE74_PY = 491.8;   /* python kumiko_lamp.py --params-json {socket_cbore:74} */
const cboreBase = cbore74.parts.find(p => p.id === 'base');
const cboreVol = cboreBase.vol / 1000;
const cboreErr = (cboreVol - CBORE74_PY) / CBORE74_PY * 100;
check(Math.abs(cboreErr) < 1.5, `Ø74 base volume ${cboreVol.toFixed(1)} cm3`,
      `python ${CBORE74_PY} (${cboreErr > 0 ? '+' : ''}${cboreErr.toFixed(2)}%)`);
check(closure(cboreBase.mesh.tris).closed, 'Ø74 base surface closed');
check(K.buildAll({ socketCbore: 74, socketRiser: 60 }).problems.length === 0,
      'a Ø74 counterbore and a riser build together');
/* Pushed far enough out the ring does run out of base, and says so. */
check(K.buildAll({ size: 120, socketCbore: 74, socketRiser: 60 }).problems
       .some(m => /Leg socket runs into the ventilation slots/.test(m)),
      'on a small lamp the moved ring reaches the leg sockets, and reports it');
/* Modern's neck follows the shade diameter, so Ø74 needs a wider shade. */
check(K.buildAll({ lanternStyle: 'modern', size: 100, height: 218,
                   socketCbore: 74 }).problems
       .some(m => /threaded neck wall/.test(m)),
      'Ø74 under the stock Modern shade is refused by the neck wall');
check(K.buildAll({ lanternStyle: 'modern', size: 150, height: 218,
                   socketCbore: 74 }).problems.length === 0,
      'a wider Modern shade takes the Ø74 counterbore');

console.log('\nmodern parts and assembly');
const modern = K.buildAll({ lanternStyle: 'modern', size: 100, height: 218 });
check(modern.problems.length === 0 && modern.parts.length === 3,
      'Modern build produces shade, base, and adapter');
check(modern.parts.map(p => p.id).join(',') ===
      'modern_shade_asanoha,modern_base,socket_adapter_ring',
      'Modern printable filenames are stable', modern.parts.map(p => p.id).join(','));
check(modern.parts.every(p => p.fits), 'all nominal Modern parts fit the 256 mm bed');
check(modern.parts[0].bbox.size.every((v, i) =>
        Math.abs(v - [100, 100, 218][i]) < 1e-6),
      'Modern shade bbox is 100 x 100 x 218 mm',
      modern.parts[0].bbox.size.map(v => v.toFixed(1)).join(' x '));
check(modern.parts[1].bbox.size.every((v, i) =>
        Math.abs(v - [100, 100, 90][i]) < 1e-6),
      'Modern base bbox is 100 x 100 x 90 mm',
      modern.parts[1].bbox.size.map(v => v.toFixed(1)).join(' x '));
for (const p of modern.parts) {
  const c = closure(p.mesh.tris);
  check(c.closed, `Modern ${p.id} surface closed`, `normal residual ${c.res.toExponential(1)}`);
}
const MODERN_PY = { modern_shade_asanoha: 117.64, modern_base: 179.92,
                    socket_adapter_ring: 5.52 };
for (const p of modern.parts) {
  const got = p.vol / 1000, want = MODERN_PY[p.id], err = (got - want) / want * 100;
  /* Browser slats remain overlapping closed prisms, so only the shade is an
     intentional upper bound; the two strictly modelled solids match closely. */
  const ok = p.id.indexOf('modern_shade_') === 0
    ? err > 0 && err < 20 : Math.abs(err) < 1.5;
  check(ok, `Modern ${p.id} volume ${got.toFixed(1)} cm3`,
        `python ${want.toFixed(1)} (${err > 0 ? '+' : ''}${err.toFixed(2)}%` +
        (p.id.indexOf('modern_shade_') === 0 ? ', lattice bound' : '') + ')');
}

console.log('\nstrict Modern shade downloads');
const STRICT_MODERN_PY = {
  asanoha: 117.61, mitsukude: 72.03, kikkou: 55.91,
  kawari_asanoha: 103.79, kagome: 69.60, masu: 52.14,
  masu_tsunagi: 90.65, senbon: 77.37, goma_gara: 111.33,
  bishamon_kikkou: 69.76, seigaiha: 98.80
};
/* Mean distance from each boundary grid vertex to the true slat outline, in the
   developed plane.  A pure cell raster leaves this at roughly a quarter cell
   (~0.24 mm at the stock 0.8 mm cell); snapping the boundary vertices onto the
   outline takes it to a few microns. */
function strictBoundaryError(K, P, pattern, raster) {
  const { mask, nu, nz, du, zs, C, shiftU, shiftZ } = raster;
  const grow = 0.8, zShift = P.height / 2, ring = P.modernRingH;
  const segs = K.clipRect(K.PATTERNS[pattern](C, P.modernOpenH, P.grid),
    -C / 2, -P.modernOpenH / 2 - grow, C / 2, P.modernOpenH / 2 + grow)
    .map(s => [[s[0][0] + C / 2, s[0][1] + zShift],
               [s[1][0] + C / 2, s[1][1] + zShift]]);
  const half = P.slatW / 2, endGrow = Math.min(P.slatW * 0.20, 0.25);
  const at = (i, j) => mask[j * nu + ((i % nu) + nu) % nu];
  let sum = 0, n = 0;
  for (let j = 1; j < nz; j++) {
    if (zs[j] <= ring + 1e-9 || zs[j] >= P.height - ring - 1e-9) continue;
    for (let i = 0; i < nu; i++) {
      const around = at(i - 1, j - 1) + at(i, j - 1) + at(i - 1, j) + at(i, j);
      if (around === 0 || around === 4) continue;
      const k = j * nu + i;
      const u = i * du + shiftU[k], z = zs[j] + shiftZ[k];
      let best = Infinity;
      for (const [p, q] of segs) {
        const dx = q[0] - p[0], dz = q[1] - p[1], len = Math.hypot(dx, dz);
        if (len < 1e-9) continue;
        let t = ((u - p[0]) * dx + (z - p[1]) * dz) / len;
        t = Math.max(-endGrow, Math.min(len + endGrow, t));
        const d = Math.hypot(u - (p[0] + dx / len * t), z - (p[1] + dz / len * t)) - half;
        if (d < best) best = d;
      }
      sum += Math.abs(best); n++;
    }
  }
  return n ? sum / n : 0;
}

for (const [name, want] of Object.entries(STRICT_MODERN_PY)) {
  const started = Date.now();
  const SP = K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                        pattern: name });
  const strict = K.buildStrictModernShade(SP, name);
  const strictBox = K.bbox(strict.tris);
  const strictBytes = K.stlBinary(strict.tris);
  const topology = stlTopology(strictBytes);
  const got = topology.signedVolume / 1000;
  const err = (got - want) / want * 100;
  check(strict.manifold && topology.nonTwo === 0 &&
        topology.sameDirection === 0 && topology.degenerate === 0 &&
        topology.components === 1 && topology.signedVolume > 0,
        `strict ${name} is one positive float32-watertight body`,
        `${strict.tris.length/9} tris, ${Date.now() - started} ms`);
  check(strictBox.size.every((v, i) =>
          Math.abs(v - [100, 100, 218][i]) < 1e-5),
        `strict ${name} bbox is exactly nominal`,
        strictBox.size.map(v => v.toFixed(5)).join(' x '));
  check(strict.raster.nu % 4 === 0 &&
        strict.raster.chordError <= 0.1 + 1e-12,
        `strict ${name} wrap respects chord error`,
        `${strict.raster.nu} columns, ${strict.raster.chordError.toFixed(5)} mm`);
  check(Math.abs(err) < 3,
        `strict ${name} volume ${got.toFixed(2)} cm3`,
        `python ${want.toFixed(2)} (${err > 0 ? '+' : ''}${err.toFixed(2)}%)`);
  /* Volume alone cannot see a staircase -- an over-filled cell here cancels an
     under-filled one there.  Measure the boundary directly: every boundary
     vertex should sit on the slat outline, not up to half a cell off it. */
  check(strictBoundaryError(K, SP, name, strict.raster) < 0.05,
        `strict ${name} boundary follows the motif`,
        `mean |err| ${strictBoundaryError(K, SP, name, strict.raster).toFixed(4)} mm`);
}

for (const variant of [
  { pattern: 'masu', modernThreadClear: 0.2, label: '0.20 mm clearance' },
  { pattern: 'masu', modernThreadClear: 0.6, label: '0.60 mm clearance' },
  { pattern: 'kikkou', slatW: 0.8, label: '0.8 mm slat' }
]) {
  const VP = K.derive(Object.assign({ lanternStyle: 'modern', size: 100,
                                      height: 218 }, variant));
  const vm = K.buildStrictModernShade(VP, variant.pattern);
  const vt = stlTopology(K.stlBinary(vm.tris));
  check(K.checkFits(VP).length === 0 && vt.nonTwo === 0 &&
        vt.sameDirection === 0 && vt.degenerate === 0 &&
        vt.components === 1 && vt.signedVolume > 0,
        `strict Modern export supports ${variant.label}`,
        `${vm.tris.length/9} tris`);
}
const modernBaseTopology = stlTopology(K.stlBinary(modern.parts[1].mesh.tris));
check(modernBaseTopology.nonTwo === 0 &&
      modernBaseTopology.sameDirection === 0 &&
      modernBaseTopology.components === 1,
      'Modern base STL is one consistently wound float32 shell',
      JSON.stringify(modernBaseTopology));
for (const depth of [4.4, 7]) {
  const deepP = K.derive({ lanternStyle: 'modern', size: 100, height: 218,
                           panelT: depth });
  const deepBase = K.buildModernBase(deepP);
  const deepTopology = stlTopology(K.stlBinary(deepBase.tris));
  check(K.checkFits(deepP).length === 0 && K.volume(deepBase.tris) > 0 &&
        deepTopology.nonTwo === 0 && deepTopology.sameDirection === 0 &&
        deepTopology.components === 1,
        `Modern ${depth.toFixed(1)} mm lattice keeps a positive one-shell base`,
        JSON.stringify(deepTopology));
}
const modernBaseAsm = modern.assembly.find(p => p.name === 'modern_base');
const modernShadeAsm = modern.assembly.find(p => p.name === 'modern_shade');
const modernAdapterAsm = modern.assembly.find(p => p.name === 'adapter');
check(Math.abs(K.bbox(modernBaseAsm.tris).lo[2]) < 1e-9 &&
      Math.abs(K.bbox(modernBaseAsm.tris).hi[2] - 90) < 1e-9,
      'Modern base displays upright in assembly');
check(Math.abs(K.bbox(modernShadeAsm.tris).lo[2] - 80) < 1e-9 &&
      Math.abs(K.bbox(modernShadeAsm.tris).hi[2] - 298) < 1e-9,
      'Modern shade overlaps its base thread by 10 mm');
check(Math.abs(K.bbox(modernAdapterAsm.tris).lo[2] - 86) < 1e-9 &&
      Math.abs(K.bbox(modernAdapterAsm.tris).hi[2] - 90) < 1e-9,
      'Modern holder adapter seats flush in the top counterbore');
const modernE14 = K.buildAll({ lanternStyle: 'modern', size: 100, height: 218,
                               holderType: 'e14' });
check(modernE14.parts[2].label === 'E14 adapter ring' &&
      modernE14.P.socketNeck === 27,
      'Modern E14 uses the 27 mm sleeve preset and stable adapter part');
check(K.buildAll({ lanternStyle: 'modern', size: 100, height: 218,
                   bedZ: 200 }).parts === null,
      'Modern export blocks when the shade exceeds build height');
const modernCounts = { asanoha:982, mitsukude:348, kikkou:221,
  kawari_asanoha:774, kagome:600, masu:18, masu_tsunagi:706, senbon:40,
  goma_gara:422, bishamon_kikkou:512, seigaiha:2416 };
for (const [name, count] of Object.entries(modernCounts)) {
  const r = K.buildAll({ lanternStyle: 'modern', size: 100, height: 218,
                         pattern: name });
  check(r.problems.length === 0 && r.slats === count,
        `Modern wraps ${name}: ${r.slats} slats`, `expected ${count}`);
}

console.log('\nvariations');
for (const v of [{ size: 150, height: 170, grid: 22 }, { pattern: 'kikkou' },
                 { pattern: 'mitsukude', slatW: 2.4 }, { grid: 20 },
                 { pattern: 'kagome' }, { pattern: 'masu_tsunagi' },
                 { pattern: 'goma_gara' }, { pattern: 'bishamon_kikkou' },
                 { pattern: 'kranok_kan_khot' },
                 { pattern: 'kranok_kan_khot', grid: 16 },
                 { pattern: 'dok_phut_tan' },
                 { pattern: 'seigaiha' }, { pattern: 'seigaiha', grid: 16 },
                 { pattern: 'masu' }, { pattern: 'senbon' },
                 { pattern: 'senbon', grid: 45 },
                 { holderType: 'e14' },
                 { snapEngagement: 0.05 }, { snapEngagement: 0.4 },
                 { holderType: 'e14', postInsertD: 4, snapEngagement: 0.2 },
                 { postInsertD: 4 }, { postInsertD: 4, capT: 9 },
                 { postInsertD: 5, post: 20 }, { postInsertD: 4, plateT: 1.2 },
                 { pattern: 'thai_rosette' },
                 { pattern: 'thai_rosette', size: 150, height: 170 },
                 { legH: 30 }, { legTenonH: 4, legClear: 0.5 },
                 { edgeChamfer: 0 }, { edgeChamfer: 3.5, cableFloor: 4 },
                 /* thinnest allowed cap with a near-maximal chamfer: only 1 mm
                    of straight wall left between the two tapered bands */
                 { edgeChamfer: 3, capT: 7, baseT: 15, cableFloor: 3 },
                 { plateT: 1.2 }, { plateT: 0.6, pattern: 'kikkou' },
                 { plateT: 1.4, panelT: 3.6 }]) {
  const r = K.buildAll(v);
  check(r.problems.length === 0 && r.parts && r.parts.every(p => p.vol > 0),
        'builds: ' + JSON.stringify(v));
}

console.log(fails ? `\n${fails} FAILURES` : '\nall checks passed');
process.exit(fails ? 1 : 0);
