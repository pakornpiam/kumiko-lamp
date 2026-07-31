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
check(K.checkFits(P).length === 0, 'stock parameters pass checkFits');
check(K.checkFits(K.derive({ slatW: 1.5 })).length > 0, 'non-nozzle-multiple slat rejected');
check(K.checkFits(K.derive({ grooveD: 9 })).length > 0, 'groove deeper than half the post rejected');

console.log('\npattern segment counts (vs Python)');
const segCounts = { asanoha: 350, kawari_asanoha: 286, kikkou: 79, mitsukude: 118,
                    kagome: 248, masu_tsunagi: 292, goma_gara: 162,
                    bishamon_kikkou: 204, kranok_kan_khot: 538 };
for (const name of Object.keys(K.PATTERNS)) {
  const segs = K.clipRect(K.PATTERNS[name](P.openW, P.openH, P.grid),
                          -P.openW/2, -P.openH/2, P.openW/2, P.openH/2);
  const want = segCounts[name];
  check(Math.abs(segs.length - want) <= 2, `${name}: ${segs.length} slats`, `python ${want}`);
}

console.log('\npattern families and cap safety');
check(K.PATTERN_FAMILY.kranok_kan_khot === 'laithai', 'kranok is in the Lai Thai family');
check(Object.keys(K.PATTERNS).filter(n => K.PATTERN_FAMILY[n] === 'kumiko').length === 8,
      'eight kumiko patterns');
check(K.capPattern('asanoha') === 'asanoha', 'cap keeps a kumiko pattern');
/* kranok is a panel-sized composition, not a repeating field: squeezed into the
   vent it is a shrunken copy of the panel, so the cap falls back. */
check(K.capPattern('kranok_kan_khot') === 'kikkou', 'cap falls back off the composition');
{
  const a = K.buildCap(P, 'kranok_kan_khot').tris.length;
  const b = K.buildCap(P, 'kikkou').tris.length;
  check(a === b, 'the fallback cap is the kikkou cap', `${a/9} vs ${b/9} tris`);
}

console.log('\nparts vs Python trimesh');
/* Strictly manifold parts must match the Python volume closely.  The panel and
   cap carry lattices built as overlapping slat prisms, so the divergence
   theorem double-counts every crossing: their volume can only come out HIGH,
   and is bounded rather than matched.  The cap is additionally ~1.5 cm3 up on
   Python because the browser build omits the cosmetic top-edge chamfer. */
const PY = { post: 56.5, base: 509.0, top_cap: 318.3, socket_adapter_ring: 5.5,
             leg: 5.6 };
const LATTICE = { top_cap: 1 };
const built = {
  post: K.buildPost(P),
  base: K.buildBase(P),
  top_cap: K.buildCap(P, 'asanoha'),
  socket_adapter_ring: K.buildRing(P),
  leg: K.buildLeg(P)
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

console.log('\nvariations');
for (const v of [{ size: 150, height: 170, grid: 22 }, { pattern: 'kikkou' },
                 { pattern: 'mitsukude', slatW: 2.4 }, { grid: 20 },
                 { pattern: 'kagome' }, { pattern: 'masu_tsunagi' },
                 { pattern: 'goma_gara' }, { pattern: 'bishamon_kikkou' },
                 { pattern: 'kranok_kan_khot' },
                 { pattern: 'kranok_kan_khot', grid: 16 },
                 { legH: 30 }, { legTenonH: 4, legClear: 0.5 }]) {
  const r = K.buildAll(v);
  check(r.problems.length === 0 && r.parts && r.parts.every(p => p.vol > 0),
        'builds: ' + JSON.stringify(v));
}

console.log(fails ? `\n${fails} FAILURES` : '\nall checks passed');
process.exit(fails ? 1 : 0);
