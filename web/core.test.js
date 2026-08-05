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
check(K.patternNames().length === 14, `14 selectable patterns`,
      String(K.patternNames().length));
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
const e14 = K.buildAll({ holderType: 'e14' });
check(e14.parts[5].label === 'E14 adapter ring' && e14.P.socketNeck === 27,
      'E14 build labels and sizes the adapter ring');

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
