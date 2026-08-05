/* Drive the real page in headless Chromium. */
const { chromium } = require('playwright');
const fs = require('fs');
const PAGE = 'file://' + require('path').join(__dirname, 'index.html');
const OUT = require('os').tmpdir();

let fails = 0;
const check = (ok, msg, extra) => {
  if (!ok) fails++;
  console.log((ok ? '  OK   ' : '  FAIL ') + msg + (extra ? '   ' + extra : ''));
};

function stlTopology(buf) {
  const count = buf.readUInt32LE(80), edges = new Map();
  const parent = Array.from({ length: count }, (_, i) => i);
  const find = i => parent[i] === i ? i : (parent[i] = find(parent[i]));
  const join = (a, b) => { a = find(a); b = find(b); if (a !== b) parent[b] = a; };
  const vertex = o => buf.readUInt32LE(o).toString(16) + ',' +
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
    if (uses[0].a !== uses[1].b || uses[0].b !== uses[1].a) sameDirection++;
  }
  return { nonTwo, sameDirection, degenerate, signedVolume: signedVolume / 6,
    components: new Set(parent.map((_, i) => find(i))).size };
}

/* zipStore uses uncompressed local entries, so extract the exact bytes that a
   browser download placed in its named Modern shade member. */
function zipEntry(buf, wanted) {
  let o = 0;
  while (o + 30 <= buf.length && buf.readUInt32LE(o) === 0x04034b50) {
    const size = buf.readUInt32LE(o + 18);
    const nameLen = buf.readUInt16LE(o + 26), extraLen = buf.readUInt16LE(o + 28);
    const name = buf.toString('utf8', o + 30, o + 30 + nameLen);
    const dataAt = o + 30 + nameLen + extraLen;
    if (name === wanted) return buf.subarray(dataAt, dataAt + size);
    o = dataAt + size;
  }
  return null;
}

(async () => {
  /* PW_CHROMIUM overrides the browser binary for sandboxes that ship their own;
     unset, Playwright uses the one `playwright install chromium` downloaded, so
     this runs on Linux, macOS and Windows alike. */
  const browser = await chromium.launch(
    process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();

  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push(String(e)));

  await page.goto(PAGE);
  await page.waitForFunction(() => window.__kumikoReady === true, null, { timeout: 20000 });
  await page.waitForTimeout(600);

  check(errors.length === 0, 'no console errors', errors.slice(0, 3).join(' | '));

  // every control group open so the sliders are reachable
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.waitForTimeout(200);

  // header reflects the stock design
  const overall = await page.textContent('#d-overall');
  check(overall.trim() === '190 × 190 × 236 mm', 'header dimensions', overall.trim());

  // part table populated
  const rows = await page.$$eval('#parts tr', r => r.length);
  check(rows === 6, 'six parts listed', String(rows));
  const chips = await page.$$eval('#parts .chip', c => c.map(x => x.textContent));
  check(chips.every(c => c === 'fits'), 'all parts fit the default bed', chips.join(','));

  // E14 is a named starting point for the adapter sleeve; its neck remains tunable.
  await page.click('button[data-holder="e14"]');
  await page.waitForTimeout(500);
  check(await page.inputValue('#r-socketNeck') === '27',
        'E14 preset selects the 27 mm sleeve bore');
  check((await page.textContent('#parts tr:last-child .part')).trim() === 'E14 adapter ring',
        'E14 adapter row is named for the selected holder');
  check(/--holder e14/.test(await page.textContent('#cli')) &&
        !/--socket-neck/.test(await page.textContent('#cli')),
        'CLI echo uses the clean E14 preset', await page.textContent('#cli'));
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.fill('#r-socketNeck', '27.5');
  await page.dispatchEvent('#r-socketNeck', 'input');
  await page.waitForTimeout(400);
  check(/--holder e14/.test(await page.textContent('#cli')) &&
        /--socket-neck 27.5/.test(await page.textContent('#cli')),
        'manual E14 neck override is retained', await page.textContent('#cli'));
  await page.click('button[data-holder="e27"]');
  await page.waitForTimeout(500);
  check(await page.inputValue('#r-socketNeck') === '26.5',
        'E27 selector restores the stock neck');
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));

  // WebGL actually drew something (canvas is not a flat fill)
  const drew = await page.evaluate(() => {
    const c = document.getElementById('gl');
    const gl = c.getContext('webgl');
    const px = new Uint8Array(c.width * c.height * 4);
    gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const seen = new Set();
    for (let i = 0; i < px.length; i += 4 * 997) seen.add(px[i] + ',' + px[i+1] + ',' + px[i+2]);
    return seen.size;
  });
  check(drew > 8, 'WebGL rendered geometry', `${drew} distinct sampled colours`);

  // language switch: keyboard-accessible, state-safe, and persistent
  const sizeBeforeLanguage = await page.inputValue('#r-size');
  await page.focus('#lang-toggle');
  await page.keyboard.press('Space');
  await page.waitForTimeout(200);
  check(await page.getAttribute('html', 'lang') === 'th', 'Thai switch updates document language');
  check((await page.textContent('.mast h1')).includes('ออกแบบโคม'),
        'Thai switch translates static interface');
  check((await page.textContent('#sw-name')).includes('(Asanoha)'),
        'Thai pattern label retains original name', await page.textContent('#sw-name'));
  check(await page.inputValue('#r-size') === sizeBeforeLanguage,
        'language switch preserves configurator state');
  check(await page.evaluate(() => localStorage.getItem('kumiko-language')) === 'th',
        'Thai choice saved');
  await page.reload();
  await page.waitForFunction(() => window.__kumikoReady === true, null, { timeout: 20000 });
  await page.waitForTimeout(500);
  check(await page.getAttribute('html', 'lang') === 'th', 'Thai choice restored after reload');
  await page.click('#lang-label');
  await page.waitForTimeout(200);
  check(await page.getAttribute('html', 'lang') === 'en' &&
        (await page.textContent('.mast h1')).includes('Design your lamp'),
        'switch returns the full interface to English');
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));

  // pattern switch
  await page.click('button[data-pattern="kikkou"]');
  await page.waitForTimeout(400);
  const swName = await page.textContent('#sw-name');
  check(swName.trim() === 'Kikkou', 'pattern switch updates swatch', swName.trim());
  const meaning = await page.textContent('#sw-meaning');
  check(meaning.includes('Tortoiseshell'), 'pattern switch updates meaning', meaning.trim());

  // family tabs: Lai Thai is a separate tab, and its patterns are hidden until picked
  check(await page.$('button[data-pattern="kranok_kan_khot"]') === null,
        'Lai Thai patterns hidden while the Kumiko tab is active');
  const fams = await page.$$eval('.famtabs button', b => b.map(x => x.textContent));
  check(fams.join(',') === 'Kumiko,Lai Thai', 'both family tabs present', fams.join(','));
  await page.click('.famtabs button[data-family="laithai"]');
  await page.waitForTimeout(200);
  check(await page.$('button[data-pattern="dok_phut_tan"]') !== null,
        'Dok Phut Tan appears in the Lai Thai family');
  await page.click('button[data-pattern="kranok_kan_khot"]');
  await page.waitForTimeout(900);
  const thai = await page.textContent('#sw-name');
  check(thai.trim() === 'Kranok Kan Khot', 'Lai Thai tab selects the vine', thai.trim());
  const meta = await page.textContent('#sw-meta');
  check(/^550 slats/.test(meta.trim()), 'kranok slat count matches Python', meta.trim());
  // the cap must still build (off the kikkou fallback) rather than block export
  check(!(await page.isDisabled('#dl-all')), 'kranok still exports');
  await page.click('.famtabs button[data-family="kumiko"]');
  await page.waitForTimeout(200);
  await page.click('button[data-pattern="kikkou"]');
  await page.waitForTimeout(400);

  // size slider drives the joinery
  await page.fill('#r-size', '230');
  await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(500);
  const grown = await page.textContent('#d-overall');
  check(grown.trim() === '240 × 240 × 236 mm', 'size slider re-solves the lamp', grown.trim());

  // exceed the bed and confirm it is reported, not hidden
  await page.fill('#r-bedX', '150'); await page.dispatchEvent('#r-bedX', 'input');
  await page.fill('#r-bedY', '150'); await page.dispatchEvent('#r-bedY', 'input');
  await page.waitForTimeout(500);
  const chips2 = await page.$$eval('#parts .chip', c => c.map(x => x.textContent));
  check(chips2.some(c => c === 'too big'), 'oversize parts flagged against the bed',
        chips2.join(','));
  await page.fill('#r-bedX', '256'); await page.dispatchEvent('#r-bedX', 'input');
  await page.fill('#r-bedY', '256'); await page.dispatchEvent('#r-bedY', 'input');
  await page.fill('#r-size', '180'); await page.dispatchEvent('#r-size', 'input');
  await page.click('button[data-pattern="asanoha"]');
  await page.waitForTimeout(500);

  // invalid clearance must block export rather than emit a broken lamp
  await page.fill('#r-grooveD', '9'); await page.dispatchEvent('#r-grooveD', 'input');
  await page.waitForTimeout(450);
  const blocked = await page.$('#problems .problems');
  const btnOff = await page.isDisabled('#dl-all');
  check(!!blocked && btnOff, 'unassemblable settings block the download');
  await page.fill('#r-grooveD', '6'); await page.dispatchEvent('#r-grooveD', 'input');
  await page.waitForTimeout(450);
  check(!(await page.isDisabled('#dl-all')), 'download re-enabled once valid');

  // the edge chamfer is capped by the cord tunnel floor, and says so
  await page.fill('#r-edgeChamfer', '3.5');
  await page.dispatchEvent('#r-edgeChamfer', 'input');
  await page.waitForTimeout(450);
  check(!!(await page.$('#problems .problems')) && await page.isDisabled('#dl-all'),
        'a chamfer past the cord tunnel floor blocks export');
  await page.fill('#r-cableFloor', '4'); await page.dispatchEvent('#r-cableFloor', 'input');
  await page.waitForTimeout(450);
  check(!(await page.isDisabled('#dl-all')), 'raising the tunnel floor frees the chamfer');
  await page.fill('#r-edgeChamfer', '2'); await page.dispatchEvent('#r-edgeChamfer', 'input');
  await page.fill('#r-cableFloor', '2'); await page.dispatchEvent('#r-cableFloor', 'input');
  await page.waitForTimeout(450);

  // download a single STL and validate the bytes
  const [dl] = await Promise.all([
    page.waitForEvent('download', { timeout: 20000 }),
    page.click('#parts tr:nth-child(2) button.dl')
  ]);
  const p = await dl.path();
  const buf = fs.readFileSync(p);
  const n = buf.readUInt32LE(80);
  check(buf.length === 84 + n * 50, `post.stl is a valid binary STL`,
        `${n} triangles, ${buf.length} bytes`);
  check(dl.suggestedFilename() === 'post.stl', 'download filename',
        dl.suggestedFilename());

  // zip of the whole set
  const zdl = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.click('#dl-all')
  ]);
  const zbuf = fs.readFileSync(await zdl[0].path());
  check(zbuf[0] === 0x50 && zbuf[1] === 0x4b, 'zip magic', zdl[0].suggestedFilename());
  check(zbuf.length > 100000, 'zip carries the whole set',
        (zbuf.length / 1048576).toFixed(2) + ' MB');

  // the diffuser plate.  Left until after the export checks so nothing above
  // sees a glazed lamp, and switched off again before the screenshots.
  await page.fill('#r-plateT', '1.2');
  await page.dispatchEvent('#r-plateT', 'input');
  await page.waitForTimeout(600);
  check(await page.$$eval('#parts tr', r => r.length) === 7,
        'glazing adds a print row');
  const notes = await page.$$eval('#parts .note', n => n.map(x => x.textContent.split(' ')[0]));
  check(notes[1] === 'diffuser_plate.stl', 'plate row sits under the panel row',
        notes.join(','));
  const qtys = await page.$$eval('#parts tr', rows =>
    rows.map(r => r.querySelector('td.num').textContent));
  check(qtys[0] === '4' && qtys[1] === '4', 'four panels and four plates',
        qtys.slice(0, 2).join('+'));
  const chips3 = await page.$$eval('#parts .chip', c => c.map(x => x.textContent));
  check(chips3.every(c => c === 'fits'), 'plates fit the bed', chips3.join(','));
  check(/--diffuser-plate 1\.2/.test(await page.textContent('#cli')),
        'CLI echo carries the plate', await page.textContent('#cli'));
  check(/printed plate/.test(await page.textContent('#s-diff')),
        'diffuser spec says the plate supersedes the sheet',
        await page.textContent('#s-diff'));
  // the top slider notch is past the post-corner limit, and must say so
  await page.fill('#r-plateT', '1.6');
  await page.dispatchEvent('#r-plateT', 'input');
  await page.waitForTimeout(500);
  check(!!(await page.$('#problems .problems')) && await page.isDisabled('#dl-all'),
        'a plate that reaches the post corner blocks export');
  await page.fill('#r-plateT', '0');
  await page.dispatchEvent('#r-plateT', 'input');
  await page.waitForTimeout(600);
  check(await page.$$eval('#parts tr', r => r.length) === 6,
        'back to six rows once the plate is off');
  check(!(await page.isDisabled('#dl-all')), 'export re-enabled unglazed');

  // the cap screws and finials.  Off by default, so this runs last.
  await page.fill('#r-postInsertD', '4');
  await page.dispatchEvent('#r-postInsertD', 'input');
  await page.waitForTimeout(600);
  check(await page.$$eval('#parts tr', r => r.length) === 7,
        'the insert adds a finial print row');
  const fnotes = await page.$$eval('#parts .note', n => n.map(x => x.textContent.split(' ')[0]));
  check(fnotes[5] === 'finial.stl', 'finial row sits under the leg row', fnotes.join(','));
  const tall = await page.textContent('#d-overall');
  check(tall.trim() === '190 × 190 × 244 mm', 'finials add to the overall height',
        tall.trim());
  check(/--post-insert 4/.test(await page.textContent('#cli')),
        'CLI echo carries the insert', await page.textContent('#cli'));
  await page.fill('#r-snapEngagement', '0.2');
  await page.dispatchEvent('#r-snapEngagement', 'input');
  await page.waitForTimeout(600);
  check((await page.textContent('#parts tr:nth-child(6) .part')).trim() ===
        'Screw-head finial cap', 'finial is presented as the screw-head cap');
  check(/--snap-lock 0.2/.test(await page.textContent('#cli')),
        'CLI echo carries snap engagement', await page.textContent('#cli'));
  // the top notch is past what an 18 mm post takes, and must say so
  await page.fill('#r-postInsertD', '5.6');
  await page.dispatchEvent('#r-postInsertD', 'input');
  await page.waitForTimeout(500);
  check(!!(await page.$('#problems .problems')) && await page.isDisabled('#dl-all'),
        'an insert that reaches the panel groove blocks export');
  await page.fill('#r-postInsertD', '0');
  await page.dispatchEvent('#r-postInsertD', 'input');
  await page.waitForTimeout(600);
  check(await page.$$eval('#parts tr', r => r.length) === 6,
        'back to six rows once the insert is off');
  check(!(await page.isDisabled('#dl-all')), 'lower-foot snaps remain exportable without finials');
  await page.fill('#r-snapEngagement', '0');
  await page.dispatchEvent('#r-snapEngagement', 'input');
  await page.waitForTimeout(500);

  // Modern is a separate two-part preset and retains its own settings.
  await page.click('button[data-lantern-style="modern"]');
  await page.waitForTimeout(1000);
  check((await page.textContent('#d-overall')).trim() === '100 × 100 × 298 mm',
        'Modern preset dimensions are 100 x 100 x 298 mm',
        (await page.textContent('#d-overall')).trim());
  check(await page.inputValue('#r-size') === '100' &&
        await page.inputValue('#r-height') === '218' &&
        await page.inputValue('#r-modernBaseH') === '90',
        'Modern preset restores diameter, shade height, and base height');
  check(await page.$('#r-post') === null && await page.$('#r-snapEngagement') === null &&
        await page.$('#r-modernThreadClear') !== null,
        'Modern hides Classic frame/snap controls and shows thread clearance');
  const modernRows = await page.$$eval('#parts .note', n =>
    n.map(x => x.textContent.split(' ')[0]));
  check(modernRows.join(',') ===
        'modern_shade_asanoha.stl,modern_base.stl,socket_adapter_ring.stl',
        'Modern print list has the two bodies and stable adapter filename',
        modernRows.join(','));
  check((await page.textContent('#cli')).trim() ===
        'python3 kumiko_lamp.py --style modern',
        'Modern preset has a clean CLI echo', (await page.textContent('#cli')).trim());
  const modernFamilies = await page.$$eval('.famtabs button', b =>
    b.map(x => x.textContent.trim()));
  check(modernFamilies.join(',') === 'Kumiko' &&
        await page.$('button[data-family="laithai"]') === null,
        'Modern offers the eleven Kumiko patterns only', modernFamilies.join(','));
  check((await page.textContent('.block:nth-of-type(2) .cols > div:nth-child(2) p'))
          .includes('0.30 mm thread clearance'),
        'Modern assembly copy explains thread tuning');
  const modernAssembly = await page.textContent(
    '.block:nth-of-type(2) .cols > div:nth-child(2) ol');
  check(modernAssembly.includes('full 10 mm thread engagement') &&
        modernAssembly.includes('do not force it'),
        'Modern assembly stops at full thread engagement without a false shoulder seat');
  const modernManifoldCopy = await page.textContent(
    '.block:nth-of-type(3) .cols > div:nth-child(1)');
  check(modernManifoldCopy.includes('float32') &&
        modernManifoldCopy.includes('lightweight') &&
        modernManifoldCopy.includes('strict single-body export mesh'),
        'Modern copy distinguishes lightweight preview from watertight download');

  const previewShade = await page.evaluate(() => {
    const m = Kumiko.buildAll({ lanternStyle: 'modern', size: 100, height: 218,
                                pattern: 'asanoha' });
    const p = m.parts[0];
    return { triangles: p.mesh.tris.length / 9,
             bytes: Kumiko.stlBinary(p.mesh.tris).length };
  });
  const [modernShadeDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.click('#parts tr:first-child button.dl')
  ]);
  const modernShadeBytes = fs.readFileSync(await modernShadeDl.path());
  const modernShadeTopology = stlTopology(modernShadeBytes);
  check(modernShadeDl.suggestedFilename() === 'modern_shade_asanoha.stl' &&
        modernShadeBytes.length === 84 + modernShadeBytes.readUInt32LE(80) * 50,
        'direct Modern shade keeps its stable binary-STL filename');
  check(modernShadeTopology.nonTwo === 0 &&
        modernShadeTopology.sameDirection === 0 &&
        modernShadeTopology.degenerate === 0 &&
        modernShadeTopology.components === 1 &&
        modernShadeTopology.signedVolume > 0,
        'direct Modern shade download is one positive float32-watertight body',
        JSON.stringify(modernShadeTopology));
  check(modernShadeBytes.length !== previewShade.bytes &&
        modernShadeBytes.readUInt32LE(80) !== previewShade.triangles,
        'direct Modern shade substitutes strict export geometry for preview bytes',
        `${previewShade.triangles} preview vs ${modernShadeBytes.readUInt32LE(80)} export tris`);

  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.click('button[data-holder="e14"]');
  await page.waitForTimeout(650);
  check((await page.textContent('#parts tr:last-child .part')).trim() === 'E14 adapter ring' &&
        /--style modern --holder e14/.test(await page.textContent('#cli')),
        'Modern E14 updates part naming and CLI echo', await page.textContent('#cli'));
  const [e14RingDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 20000 }),
    page.click('#parts tr:last-child button.dl')
  ]);
  const e14RingBytes = fs.readFileSync(await e14RingDl.path());
  check(e14RingDl.suggestedFilename() === 'socket_adapter_ring.stl' &&
        e14RingBytes.length === 84 + e14RingBytes.readUInt32LE(80) * 50,
        'Modern E14 adapter downloads as a valid stable-name STL');
  const [e14ZipDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.click('#dl-all')
  ]);
  const e14ZipBytes = fs.readFileSync(await e14ZipDl.path());
  check(e14ZipBytes.includes(e14RingBytes),
        'Modern E14 ZIP carries the selected adapter geometry');
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.click('button[data-holder="e27"]');
  await page.waitForTimeout(550);
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));

  await page.fill('#r-size', '110'); await page.dispatchEvent('#r-size', 'input');
  await page.fill('#r-modernBaseH', '95'); await page.dispatchEvent('#r-modernBaseH', 'input');
  await page.fill('#r-modernThreadClear', '0.4');
  await page.dispatchEvent('#r-modernThreadClear', 'input');
  await page.fill('#r-panelT', '4.4'); await page.dispatchEvent('#r-panelT', 'input');
  await page.waitForTimeout(700);
  check(/--size 110/.test(await page.textContent('#cli')) &&
        /--modern-base-height 95/.test(await page.textContent('#cli')) &&
        /--thread-clearance 0.4/.test(await page.textContent('#cli')) &&
        /--panel-thickness 4.4/.test(await page.textContent('#cli')),
        'Modern CLI carries changed dimensions and fit', await page.textContent('#cli'));
  await page.click('button[data-lantern-style="classic"]');
  await page.waitForTimeout(650);
  check(await page.inputValue('#r-size') === '180' && await page.$('#r-post') !== null,
        'switching back restores untouched Classic settings');
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.fill('#r-size', '185'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(300);
  await page.click('button[data-lantern-style="modern"]');
  await page.waitForTimeout(700);
  check(await page.inputValue('#r-size') === '110' &&
      await page.inputValue('#r-modernBaseH') === '95' &&
        await page.inputValue('#r-modernThreadClear') === '0.4' &&
        await page.inputValue('#r-panelT') === '4.4',
        'Modern settings survive a round trip through Classic');
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.fill('#r-size', '100'); await page.dispatchEvent('#r-size', 'input');
  await page.fill('#r-modernBaseH', '90'); await page.dispatchEvent('#r-modernBaseH', 'input');
  await page.fill('#r-modernThreadClear', '0.3');
  await page.dispatchEvent('#r-modernThreadClear', 'input');
  await page.fill('#r-panelT', '4'); await page.dispatchEvent('#r-panelT', 'input');
  await page.waitForTimeout(700);

  await page.fill('#r-size', '80'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(450);
  check(!!(await page.$('#problems .problems')) && await page.isDisabled('#dl-all'),
        'unsafe Modern diameter blocks export');
  await page.fill('#r-size', '100'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(600);
  await page.fill('#r-bedZ', '200'); await page.dispatchEvent('#r-bedZ', 'input');
  await page.waitForTimeout(450);
  check(await page.isDisabled('#dl-all'),
        'Modern shade above printer height blocks export');
  await page.fill('#r-bedZ', '256'); await page.dispatchEvent('#r-bedZ', 'input');
  await page.waitForTimeout(650);
  check(!(await page.isDisabled('#dl-all')), 'valid Modern export re-enabled');

  const [modernBaseDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 20000 }),
    page.click('#parts tr:nth-child(2) button.dl')
  ]);
  const modernBaseBytes = fs.readFileSync(await modernBaseDl.path());
  check(modernBaseDl.suggestedFilename() === 'modern_base.stl' &&
        modernBaseBytes.length === 84 + modernBaseBytes.readUInt32LE(80) * 50,
        'Modern base download is a valid binary STL', modernBaseDl.suggestedFilename());
  const [modernZipDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.click('#dl-all')
  ]);
  const modernZip = fs.readFileSync(await modernZipDl.path());
  check(modernZipDl.suggestedFilename() === 'kumiko-lamp-modern-100mm-asanoha.zip' &&
        modernZip.includes(Buffer.from('modern_shade_asanoha.stl')) &&
        modernZip.includes(Buffer.from('modern_base.stl')) &&
        modernZip.includes(Buffer.from('socket_adapter_ring.stl')),
        'Modern ZIP is style-named and contains all three printable files',
        modernZipDl.suggestedFilename());
  const zippedShade = zipEntry(modernZip, 'modern_shade_asanoha.stl');
  const zippedShadeTopology = zippedShade && stlTopology(zippedShade);
  check(!!zippedShade && zippedShade.equals(modernShadeBytes),
        'Modern ZIP reuses the cached strict shade bytes');
  check(!!zippedShadeTopology && zippedShadeTopology.nonTwo === 0 &&
        zippedShadeTopology.sameDirection === 0 &&
        zippedShadeTopology.degenerate === 0 &&
        zippedShadeTopology.components === 1 &&
        zippedShadeTopology.signedVolume > 0 &&
        zippedShade.readUInt32LE(80) !== previewShade.triangles,
        'Modern ZIP shade is strict topology rather than preview geometry',
        zippedShadeTopology && JSON.stringify(zippedShadeTopology));

  await page.click('#v-exp'); await page.waitForTimeout(250);
  check(await page.getAttribute('#v-exp', 'aria-pressed') === 'true',
        'Modern exploded view activates');
  await page.click('#v-asm');
  await page.click('#lang-label'); await page.waitForTimeout(250);
  check((await page.textContent('.styletabs')).includes('โมเดิร์น') &&
        (await page.textContent('.block:nth-of-type(2) .cols > div:nth-child(2) p'))
          .includes('ระยะเผื่อเกลียว'),
        'Modern controls and assembly guidance localize to Thai');
  const thaiManifoldCopy = await page.textContent(
    '.block:nth-of-type(3) .cols > div:nth-child(1)');
  check(thaiManifoldCopy.includes('float32') &&
        thaiManifoldCopy.includes('น้ำหนักเบา') &&
        thaiManifoldCopy.includes('ชิ้นเดียว'),
        'Modern lightweight-preview and watertight-download copy localizes to Thai');
  check((await page.textContent('.block:nth-of-type(2) .cols > div:nth-child(2) ol'))
          .includes('อย่าฝืนขัน'),
        'Modern full-engagement warning localizes to Thai');
  const shippedHtml = fs.readFileSync(require('path').join(__dirname, 'index.html'), 'utf8');
  check(shippedHtml.includes("'Modern base is too short for its 45-degree shoulder.':'ฐานโมเดิร์นสั้นเกินไปสำหรับบ่าลาด 45 องศา'") &&
        shippedHtml.includes("'Modern shoulder leaves under two walls above the hollow body.':'บ่าลาดโมเดิร์นเหลือผนังเหนือโพรงฐานน้อยกว่าสองเส้น'") &&
        shippedHtml.includes("'Modern shoulder reaches the cable outlet.':'บ่าลาดโมเดิร์นชนช่องสายไฟ'"),
        'Modern shoulder safety guards carry Thai translations');
  await page.fill('#r-size', '80'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(400);
  check(/คอฐานโมเดิร์น|ช่องระบายอากาศโมเดิร์น/.test(await page.textContent('#problems')),
        'Modern validation localizes to Thai');
  await page.fill('#r-size', '100'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(500);
  await page.click('#lang-label'); await page.waitForTimeout(250);

  await page.click('button[data-lantern-style="classic"]');
  await page.waitForTimeout(650);
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  check(await page.inputValue('#r-size') === '185',
        'Classic setting also survives the Modern round trip');
  await page.fill('#r-size', '180'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(500);

  // screenshots, both themes -- collapse the rail back to its default state
  await page.evaluate(() => {
    document.querySelectorAll('details.grp').forEach((d, i) => { d.open = i < 2; });
    window.scrollTo(0, 0);
  });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/app-light.png`, fullPage: false });
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForTimeout(500);
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/app-dark.png`, fullPage: false });

  // narrow viewport must not scroll sideways
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  await page.setViewportSize({ width: 420, height: 900 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(overflow <= 1, 'no horizontal overflow at 420px', `${overflow}px`);
  await page.screenshot({ path: `${OUT}/app-narrow.png`, fullPage: false });
  await page.click('#lang-label');
  await page.waitForTimeout(250);
  const thaiOverflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(thaiOverflow <= 1, 'Thai UI has no horizontal overflow at 420px', `${thaiOverflow}px`);
  await page.screenshot({ path: `${OUT}/app-narrow-th.png`, fullPage: false });

  check(errors.length === 0, 'still no console errors at the end',
        errors.slice(0, 3).join(' | '));

  await browser.close();
  console.log(fails ? `\n${fails} FAILURES` : '\nall browser checks passed');
  process.exit(fails ? 1 : 0);
})();
