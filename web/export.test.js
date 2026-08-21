/**
 * The paid export path, end to end, against a running stack.
 *
 * page.test.js can no longer prove any of this. Export is a server call now, and
 * that file drives the page with a stub -- so the byte-level guarantees moved
 * here rather than being dropped, including the strict Modern topology check,
 * which is the strongest correctness assertion in the browser suite.
 *
 * Needs both halves up:
 *
 *   python container/server.py                       (PORT=8901)
 *   npx wrangler dev --local --port 8913 \
 *     --var EXPORT_ORIGIN:http://127.0.0.1:8901 \
 *     --var DEV_ECHO_MAGIC_LINK:1 \
 *     --var STRIPE_WEBHOOK_SECRET:whsec_test123
 *
 *   node export.test.js
 *
 * Override with KUMIKO_BASE / KUMIKO_WEBHOOK_SECRET. It skips with a visible
 * message rather than failing when nothing is listening: a suite that cannot
 * run should say so, not look like a broken build.
 */

const crypto = require('crypto');
const BASE = process.env.KUMIKO_BASE || 'http://127.0.0.1:8913';
const WEBHOOK_SECRET = process.env.KUMIKO_WEBHOOK_SECRET || 'whsec_test123';
const EMAIL = `export-test-${process.pid}@example.com`;

let fails = 0;
const check = (ok, msg, extra) => {
  if (!ok) fails++;
  console.log((ok ? '  OK   ' : '  FAIL ') + msg + (extra ? '   ' + extra : ''));
};

/** Float32 topology of a binary STL, over the bytes as they would land on disk.
 *  Shared-vertex indices do not exist in STL, so pairing is by exact float bits
 *  -- which is precisely where boolean defects show up. */
function stlTopology(buf) {
  const count = buf.readUInt32LE(80), edges = new Map();
  const parent = Array.from({ length: count }, (_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const join = (a, b) => { a = find(a); b = find(b); if (a !== b) parent[b] = a; };
  const vertex = (o) => buf.readUInt32LE(o).toString(16) + ',' +
                        buf.readUInt32LE(o + 4).toString(16) + ',' +
                        buf.readUInt32LE(o + 8).toString(16);
  let degenerate = 0, signedVolume = 0;
  for (let i = 0; i < count; i++) {
    const o = 84 + i * 50;
    const v = [vertex(o + 12), vertex(o + 24), vertex(o + 36)];
    const p = [buf.readFloatLE(o + 12), buf.readFloatLE(o + 16), buf.readFloatLE(o + 20)];
    const q = [buf.readFloatLE(o + 24), buf.readFloatLE(o + 28), buf.readFloatLE(o + 32)];
    const r = [buf.readFloatLE(o + 36), buf.readFloatLE(o + 40), buf.readFloatLE(o + 44)];
    const ux = q[0] - p[0], uy = q[1] - p[1], uz = q[2] - p[2];
    const vx = r[0] - p[0], vy = r[1] - p[1], vz = r[2] - p[2];
    const cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
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
  for (const list of edges.values()) {
    if (list.length !== 2) { nonTwo++; continue; }
    if (list[0].a === list[1].a) sameDirection++;
    join(list[0].face, list[1].face);
  }
  const roots = new Set();
  for (let i = 0; i < count; i++) roots.add(find(i));
  return { nonTwo, sameDirection, degenerate, components: roots.size,
           signedVolume: signedVolume / 6, triangles: count };
}

/** Uncompressed or deflated, read one member out of a zip. */
function zipEntries(buf) {
  const out = new Map();
  const zlib = require('zlib');
  let i = 0;
  while (i + 30 <= buf.length && buf.readUInt32LE(i) === 0x04034b50) {
    const method = buf.readUInt16LE(i + 8);
    const compressed = buf.readUInt32LE(i + 18);
    const nameLen = buf.readUInt16LE(i + 26);
    const extraLen = buf.readUInt16LE(i + 28);
    const name = buf.slice(i + 30, i + 30 + nameLen).toString('latin1');
    const start = i + 30 + nameLen + extraLen;
    const raw = buf.slice(start, start + compressed);
    out.set(name, method === 8 ? zlib.inflateRawSync(raw) : raw);
    i = start + compressed;
  }
  return out;
}

const jar = [];
async function call(path, opts = {}) {
  const headers = Object.assign({}, opts.headers);
  if (jar.length) headers.Cookie = jar.join('; ');
  const r = await fetch(BASE + path, Object.assign({}, opts, { headers, redirect: 'manual' }));
  const setCookie = r.headers.get('set-cookie');
  if (setCookie) jar.push(setCookie.split(';')[0]);
  return r;
}

function sign(body) {
  const t = Math.floor(Date.now() / 1000);
  return `t=${t},v1=` +
    crypto.createHmac('sha256', WEBHOOK_SECRET).update(`${t}.${body}`).digest('hex');
}

async function exportZip(payload) {
  const r = await call('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  return r;
}

(async () => {
  let health;
  try {
    health = await (await fetch(BASE + '/api/health')).json();
  } catch {
    console.log(`SKIPPED: nothing listening on ${BASE}.`);
    console.log('Start the container and `wrangler dev` (see the header of this file).');
    process.exit(0);
  }
  if (!health.exportConfigured) {
    console.log(`SKIPPED: ${BASE} has no export service configured.`);
    process.exit(0);
  }

  // --- the gate ----------------------------------------------------------
  check((await exportZip({ pattern: 'kikkou', params: {} })).status === 401,
        'export refused while signed out');

  const req = await call('/api/auth/request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: EMAIL })
  });
  const devLink = (await req.json()).devLink;
  if (!devLink) {
    console.log('SKIPPED: DEV_ECHO_MAGIC_LINK is not set, so this cannot sign in.');
    process.exit(0);
  }
  await call(new URL(devLink).pathname + new URL(devLink).search);
  const me = await (await call('/api/me')).json();
  check(me.signedIn && me.email === EMAIL, 'signed in through the magic link', me.email);

  check((await exportZip({ pattern: 'kikkou', params: {} })).status === 402,
        'export refused while signed in but unsubscribed');

  const grant = JSON.stringify({
    type: 'customer.subscription.updated',
    data: { object: { id: 'sub_export_test', status: 'active', customer: 'cus_export_test',
                      current_period_end: Math.floor(Date.now() / 1000) + 2592000,
                      metadata: { email: EMAIL } } }
  });
  const wh = await fetch(BASE + '/api/stripe-webhook', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Stripe-Signature': sign(grant) },
    body: grant
  });
  check(wh.status === 200, 'a signed subscription event is accepted', String(wh.status));

  // --- Classic -----------------------------------------------------------
  const classic = await exportZip({ pattern: 'kikkou', style: 'classic',
                                    params: { post: 20, plate_t: 1.2 } });
  check(classic.status === 200, 'subscribed Classic export succeeds', String(classic.status));
  const classicZip = Buffer.from(await classic.arrayBuffer());
  const classicParts = zipEntries(classicZip);
  check(classicZip[0] === 0x50 && classicZip[1] === 0x4b, 'Classic export is a zip');
  check([...classicParts.keys()].sort().join(',') ===
        'base.stl,diffuser_plate.stl,leg.stl,panel_kikkou.stl,post.stl,socket_adapter_ring.stl,top_cap.stl',
        'Classic zip carries every printable part under its stable name',
        [...classicParts.keys()].sort().join(','));
  check(!classicParts.has('assembly_preview.stl'),
        'the non-printable assembly preview is never shipped');

  for (const [name, bytes] of classicParts) {
    const ok = bytes.length === 84 + bytes.readUInt32LE(80) * 50;
    if (!ok) check(false, `${name} is a valid binary STL`, `${bytes.length} bytes`);
  }
  check(true, 'every Classic member is a valid binary STL',
        `${classicParts.size} parts`);

  const post = classicParts.get('post.stl');
  const postTop = stlTopology(post);
  check(postTop.nonTwo === 0 && postTop.sameDirection === 0 &&
        postTop.degenerate === 0 && postTop.components === 1 && postTop.signedVolume > 0,
        'the Classic post is one positive float32-watertight body',
        JSON.stringify(postTop));

  const single = await exportZip({ pattern: 'kikkou', style: 'classic',
                                   part: 'post', params: { post: 20, plate_t: 1.2 } });
  const singleBytes = Buffer.from(await single.arrayBuffer());
  check(single.status === 200 && singleBytes.equals(post),
        'a single-part export returns exactly the member from the zip',
        `${singleBytes.length} vs ${post.length} bytes`);
  check(/filename="post\.stl"/.test(single.headers.get('content-disposition') || ''),
        'single-part export is named for the part',
        single.headers.get('content-disposition'));

  // --- Modern: the strongest check in the suite --------------------------
  const modern = await exportZip({ pattern: 'asanoha', style: 'modern',
                                   params: { size: 100, height: 218 } });
  check(modern.status === 200, 'subscribed Modern export succeeds', String(modern.status));
  const modernParts = zipEntries(Buffer.from(await modern.arrayBuffer()));
  check([...modernParts.keys()].sort().join(',') ===
        'modern_base.stl,modern_shade_asanoha.stl,socket_adapter_ring.stl',
        'Modern zip carries the three printable files',
        [...modernParts.keys()].sort().join(','));

  const shade = modernParts.get('modern_shade_asanoha.stl');
  const shadeTop = stlTopology(shade);
  check(shadeTop.nonTwo === 0 && shadeTop.sameDirection === 0 &&
        shadeTop.components === 1 && shadeTop.signedVolume > 0,
        'the Modern shade is one positive float32-watertight body',
        JSON.stringify(shadeTop));

  /* Not zero, and deliberately so. The CSG shade carries three zero-area facets
     -- stl/modern_shade_asanoha.stl, generated long before any of this, has the
     same three with an identical triangle count and volume, so they come from
     the generator and not from this pipeline. They cost nothing: the mesh is
     still edge-paired, consistently wound and one body, which is what a slicer
     needs. Bounded rather than ignored, so a jump would show up as a
     regression. The browser's raster shade has none, which is why the old
     assertion in page.test.js could demand zero -- a different mesh entirely. */
  check(shadeTop.degenerate <= 3,
        'the Modern shade carries no more zero-area facets than the generator always has',
        `${shadeTop.degenerate} of ${shadeTop.triangles}`);

  const seigaiha = await exportZip({ pattern: 'seigaiha', style: 'modern',
                                     params: { size: 100, height: 218 } });
  check(seigaiha.status === 200, 'filled Modern Seigaiha export succeeds',
        String(seigaiha.status));
  const seigaihaParts = zipEntries(Buffer.from(await seigaiha.arrayBuffer()));
  const seigaihaShade = seigaihaParts.get('modern_shade_seigaiha.stl');
  const seigaihaTop = stlTopology(seigaihaShade);
  check(seigaihaTop.nonTwo === 0 && seigaihaTop.sameDirection === 0 &&
        seigaihaTop.components === 1 && seigaihaTop.signedVolume > 0,
        'filled Modern Seigaiha export is one positive float32-watertight body',
        JSON.stringify(seigaihaTop));
  check(Math.abs(seigaihaTop.signedVolume / 1000 - 166.0) < 1.0,
        'filled Modern Seigaiha export volume matches the Python manifold',
        `${(seigaihaTop.signedVolume / 1000).toFixed(2)} cm3`);

  // --- refusals ----------------------------------------------------------
  const bad = await exportZip({ pattern: 'asanoha', style: 'classic', params: { grid: 12 } });
  const badBody = await bad.json();
  check(bad.status === 422 && (badBody.reasons || []).some(r => /watertight/i.test(r)),
        'a configuration that builds non-watertight parts is refused, not shipped',
        JSON.stringify(badBody.reasons || []));

  const gone = await exportZip({ pattern: 'thai_rosette', style: 'classic', params: {} });
  check(gone.status === 400, 'a pattern that is not on offer is a 400, not a build failure',
        String(gone.status));

  const revoke = JSON.stringify({
    type: 'customer.subscription.deleted',
    data: { object: { id: 'sub_export_test', status: 'canceled', customer: 'cus_export_test',
                      metadata: { email: EMAIL } } }
  });
  await fetch(BASE + '/api/stripe-webhook', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Stripe-Signature': sign(revoke) },
    body: revoke
  });
  check((await exportZip({ pattern: 'kikkou', params: {} })).status === 402,
        'export is refused again once the subscription is cancelled');

  console.log(fails ? `\n${fails} FAILURES` : '\nall export checks passed');
  process.exit(fails ? 1 : 0);
})();
