/* Drive the real page in headless Chromium.
 *
 * Served over http rather than opened from disk. Export is a server call now,
 * and Chromium refuses fetch('/api/...') from a file:// page before it becomes
 * a request at all -- so from disk there is nothing to intercept and nothing to
 * assert. This also matches how the page is actually delivered.
 *
 * The stub answers only what the page needs to come up and to prove it asks for
 * the right thing. Real bytes are export.test.js's job, against a real stack. */
const { chromium } = require('playwright');
const fs = require('fs');
const http = require('http');
const OUT = require('os').tmpdir();

let lastExportRequest = null;
let stubSubscribed = false;

const server = http.createServer((req, res) => {
  const send = (code, type, body) => {
    res.writeHead(code, { 'Content-Type': type, 'Content-Length': body.length });
    res.end(body);
  };
  if (req.url === '/api/me') {
    return send(200, 'application/json', Buffer.from(JSON.stringify({
      signedIn: stubSubscribed, email: stubSubscribed ? 'tester@example.com' : null,
      subscribed: stubSubscribed, priceConfigured: true
    })));
  }
  if (req.url === '/api/export') {
    let body = '';
    req.on('data', (c) => { body += c; });
    return req.on('end', () => {
      try { lastExportRequest = JSON.parse(body); } catch { lastExportRequest = null; }
      if (!stubSubscribed) {
        return send(402, 'application/json',
          Buffer.from(JSON.stringify({ error: 'subscription required', reason: 'not_subscribed' })));
      }
      /* Smallest legal empty zip: end-of-central-directory and nothing else. */
      const zip = Buffer.concat([Buffer.from([0x50, 0x4b, 0x05, 0x06]), Buffer.alloc(18)]);
      res.writeHead(200, {
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="probe.zip"',
        'Content-Length': zip.length
      });
      res.end(zip);
    });
  }
  if (req.url === '/' || req.url.startsWith('/index.html')) {
    const html = fs.readFileSync(__dirname + '/index.html');
    return send(200, 'text/html; charset=utf-8', html);
  }
  send(404, 'application/json', Buffer.from('{"error":"not found"}'));
});

const PORT = 8788 + (process.pid % 900);
const PAGE = `http://127.0.0.1:${PORT}/`;

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
  await new Promise((r) => server.listen(PORT, '127.0.0.1', r));
  /* PW_CHROMIUM overrides the browser binary for sandboxes that ship their own;
     unset, Playwright uses the one `playwright install chromium` downloaded, so
     this runs on Linux, macOS and Windows alike. */
  const browser = await chromium.launch(
    process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();

  const errors = [];
  /* Chromium logs a console error for any non-2xx fetch, and this suite drives
     the refusal paths on purpose. Those two statuses ARE the assertion just
     above them, so counting them here would make a passing gate look like a
     regression. Everything else, including any other status, still counts. */
  const deliberate = /Failed to load resource.*status of (401|402)\b/;
  page.on('console', m => {
    if (m.type() === 'error' && !deliberate.test(m.text())) errors.push(m.text());
  });
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

  // Lai Thai is disabled: one family tab, and none of its three ids on offer
  const fams = await page.$$eval('.famtabs button', b => b.map(x => x.textContent.trim()));
  check(fams.join(',') === 'Kumiko', 'only the Kumiko family tab is offered', fams.join(','));
  check(await page.$('button[data-family="laithai"]') === null,
        'no Lai Thai family tab exists');
  const hidden = await Promise.all(['kranok_kan_khot', 'dok_phut_tan', 'thai_rosette']
    .map(id => page.$(`button[data-pattern="${id}"]`)));
  check(hidden.every(h => h === null), 'no Lai Thai pattern button exists');
  const offered = await page.$$eval('.styles button[data-pattern]', b => b.length);
  check(offered === 11, 'eleven Kumiko patterns on offer', String(offered));

  // the picker lives beside the preview, not in the rail -- its ~280px there
  // was pushing the Pattern group's own sliders under the rail's scroll fold
  const where = await page.evaluate(() => ({
    picker: document.querySelectorAll('#picker button[data-pattern]').length,
    rail: document.querySelectorAll('#rail button[data-pattern]').length,
    tabs: document.querySelectorAll('#picker .famtabs button').length
  }));
  check(where.picker === 11 && where.rail === 0,
        'the pattern picker sits outside the rail',
        `${where.picker} in the picker, ${where.rail} in the rail`);
  check(where.tabs === 1, 'the family tabs moved with the buttons', String(where.tabs));

  /* A worst-case reachable slider change used to run buildAll, four assembly
     transforms and the WebGL upload on the main thread.  The 60 ms timer then
     arrived hundreds of milliseconds late, which is the preview/input freeze
     a person feels while dragging.  Model completion is checked separately so
     a responsive no-op cannot satisfy this regression. */
  await page.click('button[data-pattern="seigaiha"]');
  await page.waitForTimeout(300);
  const sliderRun = await page.evaluate(() => new Promise(resolve => {
    const size = document.getElementById('r-size');
    const grid = document.getElementById('r-grid');
    const dimensions = document.getElementById('d-overall');
    const started = performance.now();
    let last = started, maxGap = 0, done = false;
    const sample = () => {
      const now = performance.now();
      maxGap = Math.max(maxGap, now - last);
      last = now;
    };
    const pulse = setInterval(sample, 16);
    const finish = completed => {
      if (done) return;
      done = true;
      setTimeout(() => {
        sample(); clearInterval(pulse); observer.disconnect();
        resolve({ maxGap, total: performance.now() - started, completed });
      }, 32);
    };
    const observer = new MutationObserver(() => {
      if (dimensions.textContent.trim() === '240 × 240 × 236 mm') finish(true);
    });
    observer.observe(dimensions, { childList: true, characterData: true, subtree: true });
    size.value = '230'; grid.value = '12';
    size.dispatchEvent(new Event('input', { bubbles: true }));
    grid.dispatchEvent(new Event('input', { bubbles: true }));
    setTimeout(() => finish(false), 10000);
  }));
  /* Loose enough for shared CI machines, but well below the 500+ ms main-thread
     stall this exact interaction produced before the worker path. */
  check(sliderRun.maxGap < 300, 'dense slider changes keep the UI responsive',
        `${sliderRun.maxGap.toFixed(0)} ms longest event-loop gap`);
  check(sliderRun.completed && (await page.textContent('#sw-name')).trim() === 'Seigaiha',
        'the responsive slider change still completes the preview rebuild',
        `${sliderRun.total.toFixed(0)} ms total`);

  /* Restore the setup expected by the dimension checks below. */
  await page.evaluate(() => {
    const size = document.getElementById('r-size');
    const grid = document.getElementById('r-grid');
    size.value = '180'; grid.value = '28';
    size.dispatchEvent(new Event('input', { bubbles: true }));
    grid.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(500);

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

  // the socket riser: off at stock, and echoed as a flag once raised
  check(!/--socket-riser/.test(await page.textContent('#cli')),
        'no riser flag while the slider is at 0');
  await page.fill('#r-socketRiser', '60');
  await page.dispatchEvent('#r-socketRiser', 'input');
  await page.waitForTimeout(600);
  check(/--socket-riser 60/.test(await page.textContent('#cli')),
        'raising the riser echoes it on the command line',
        await page.textContent('#cli'));
  check(!(await page.isDisabled('#dl-all')) &&
        (await page.$$eval('#parts tr', r => r.length)) === 6,
        'a risen lamp still builds its six parts');
  /* The counterbore slider reaches 74 and every bit of that has to build: the
     vent ring steps outward to clear the holder -- and the riser tube around it
     -- rather than refusing to share the room. */
  await page.fill('#r-socketCbore', '74');
  await page.dispatchEvent('#r-socketCbore', 'input');
  await page.waitForTimeout(600);
  check(!(await page.isDisabled('#dl-all')) && !(await page.$('#problems .problems')),
        'the counterbore reaches its full 74 mm with a riser fitted');
  await page.fill('#r-socketRiser', '0'); await page.dispatchEvent('#r-socketRiser', 'input');
  await page.waitForTimeout(600);
  check(!(await page.isDisabled('#dl-all')) &&
        (await page.$$eval('#parts tr', r => r.length)) === 6,
        'and on its own, still six parts');
  await page.fill('#r-socketCbore', '50'); await page.dispatchEvent('#r-socketCbore', 'input');
  await page.waitForTimeout(500);

  /* The panel toggle is a way of looking at the lamp, not a property of it: the
     render has to change and the print list must not. Sampled off the canvas
     rather than trusted from a flag -- the whole point is what is drawn. */
  const sampleView = () => page.evaluate(() => {
    const c = document.getElementById('gl');
    const gl = c.getContext('webgl');
    const px = new Uint8Array(c.width * c.height * 4);
    gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const rows = [];
    for (let i = 0; i < px.length; i += 4 * 97)
      rows.push(px[i] + ',' + px[i + 1] + ',' + px[i + 2]);
    return { rows: rows, colours: new Set(rows).size };
  });
  const differing = (a, b) =>
    a.rows.reduce((n, v, i) => n + (v === b.rows[i] ? 0 : 1), 0) / a.rows.length;

  const withPanels = await sampleView();
  check(await page.getAttribute('#v-panels', 'aria-pressed') === 'true',
        'panels start shown');
  await page.click('#v-panels');
  await page.waitForTimeout(350);
  const without = await sampleView();
  check(await page.getAttribute('#v-panels', 'aria-pressed') === 'false',
        'the toggle records itself as off');
  check(differing(withPanels, without) > 0.1,
        'hiding the panels visibly changes the render',
        (differing(withPanels, without) * 100).toFixed(1) + '% of samples');
  check(without.colours > 8, 'the frame, base and cap are still drawn',
        `${without.colours} distinct sampled colours`);
  check(!(await page.isDisabled('#dl-all')) &&
        (await page.$$eval('#parts tr', r => r.length)) === 6,
        'hiding panels changes nothing about what prints');
  /* A view setting, so a style switch must not quietly put them back: only the
     label follows the style. */
  await page.click('button[data-lantern-style="modern"]');
  await page.waitForTimeout(700);
  const heldInModern = await page.getAttribute('#v-panels', 'aria-pressed') === 'false';
  await page.click('button[data-lantern-style="classic"]');
  await page.waitForTimeout(700);
  check(heldInModern && await page.getAttribute('#v-panels', 'aria-pressed') === 'false',
        'hidden panels survive a style round trip');
  await page.click('#v-panels');
  await page.waitForTimeout(350);
  check(differing(withPanels, await sampleView()) === 0,
        'switching them back restores the view exactly');

  /* Export is a server call now, so this file can no longer produce the bytes:
     what it CAN prove offline is that the page asks for the right thing. The
     bytes themselves are checked in export.test.js against a running stack --
     including the strict Modern topology guard, which is the strongest check in
     the suite and must not be quietly lost.

     The route is intercepted rather than reached: page.test.js runs from
     file://, and keeping it runnable with nothing installed is worth more than
     testing the transport twice. */
  /* Unsubscribed: the stub answers 402 and the page must say so rather than
     produce a file. This is the gate, from the outside. */
  stubSubscribed = false;
  await page.reload();
  await page.waitForFunction(() => window.__kumikoReady === true, null, { timeout: 30000 });
  /* A reload puts the rail back to its default, and later assertions drive
     sliders in groups that start collapsed. */
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => { d.open = true; }));
  await page.waitForTimeout(600);
  await page.click('#dl-all');
  await page.waitForTimeout(900);
  const refused = await page.$eval('#account', a => a.innerText.replace(/\s+/g, ' '));
  check(/subscription/i.test(refused), 'an unsubscribed download is refused, with a reason',
        refused.slice(-70));

  stubSubscribed = true;
  await page.reload();
  await page.waitForFunction(() => window.__kumikoReady === true, null, { timeout: 30000 });
  /* A reload puts the rail back to its default, and later assertions drive
     sliders in groups that start collapsed. */
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => { d.open = true; }));
  await page.waitForTimeout(600);
  await page.click('button[data-pattern="kikkou"]');
  await page.waitForTimeout(500);

  const [partDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 20000 }),
    page.click('#parts tr:nth-child(2) button.dl')
  ]);
  let seen = lastExportRequest;
  check(seen && seen.part === 'post', 'a part button asks the server for that part',
        seen && String(seen.part));
  check(partDl.suggestedFilename() === 'post.stl', 'single-part download filename',
        partDl.suggestedFilename());
  check(!!seen && seen.params && seen.params.post === 18 && seen.params.groove_d === 6,
        'the request carries the slider set under the generator\'s own names',
        seen && seen.params && `${Object.keys(seen.params).length} params`);
  check(!!seen && seen.params && !('modern_base_d' in seen.params),
        'the linked-diameter sentinel is omitted rather than sent as 0');

  const [zipDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.click('#dl-all')
  ]);
  seen = lastExportRequest;
  check(seen && seen.part === null, 'the zip button asks for the whole set',
        seen && String(seen.part));
  check(seen && seen.pattern === 'kikkou' && seen.style === 'classic',
        'the request carries pattern and style',
        seen && `${seen.style}/${seen.pattern}`);
  check(/^kumiko-lamp-.*\.zip$/.test(zipDl.suggestedFilename()),
        'zip download filename', zipDl.suggestedFilename());

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

  /* The cap screws and finials.  Off by default, so this runs last.  A choice
     rather than a dimension now: Off or M3, no insert-hole slider to drag. */
  check(await page.$('#r-postInsertD') === null,
        'the insert hole is a choice, not a slider');
  await page.click('button[data-cap-screws="m3"]');
  await page.waitForFunction(() => document.querySelectorAll('#parts tr').length === 7,
    null, { timeout: 10000 });
  /* The toggle rebuilds the rail, which puts every group back to collapsed. */
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => { d.open = true; }));
  check(await page.getAttribute('button[data-cap-screws="m3"]', 'aria-pressed') === 'true' &&
        await page.getAttribute('button[data-cap-screws="off"]', 'aria-pressed') === 'false',
        'the toggle records which way it is set');
  check(await page.$$eval('#parts tr', r => r.length) === 7,
        'the insert adds a finial print row');
  const fnotes = await page.$$eval('#parts .note', n => n.map(x => x.textContent.split(' ')[0]));
  check(fnotes[5] === 'finial.stl', 'finial row sits under the leg row', fnotes.join(','));
  const tall = await page.textContent('#d-overall');
  check(tall.trim() === '190 × 190 × 244 mm', 'finials add to the overall height',
        tall.trim());
  check(/--post-insert 4/.test(await page.textContent('#cli')),
        'CLI echo carries the insert', await page.textContent('#cli'));
  /* The screws are the one thing in this lamp you have to buy, and the finial
     caps hide them once it is together -- so the page has to say what they are. */
  check((await page.textContent('#s-screw')).trim() === '4 × M3 × 8 mm',
        'the page names the screw to buy', (await page.textContent('#s-screw')).trim());
  check(/4 × M3/.test(await page.textContent('#s-insert')) &&
        /4\.0 × 6\.5 mm/.test(await page.textContent('#s-insert')),
        'and the insert that receives it', (await page.textContent('#s-insert')).trim());
  check(await page.isVisible('#a-screw'),
        'the assembly gains its screwing step');
  /* Thai replaces the whole list and the whole dl, so the step's id and its
     hidden state have to survive that round trip. */
  await page.click('#lang-label'); await page.waitForTimeout(500);
  check((await page.textContent('#a-screw')).includes('อินเสิร์ต') &&
        await page.isVisible('#a-screw'),
        'the screwing step survives the Thai list replacement',
        (await page.textContent('#a-screw')).slice(0, 40));
  check((await page.textContent('#s-screw')).trim() === '4 × M3 × 8 mm' &&
        (await page.textContent('.block:nth-of-type(2) .cols > div:nth-child(2) dt'))
          .includes('สกรู'),
        'the hardware rows localize while the sizes stay as printed');
  await page.click('#lang-label'); await page.waitForTimeout(500);
  /* buildRail runs on a language switch, which puts every group back to its
     default collapsed state -- the sliders below are unreachable until they are
     opened again. */
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => { d.open = true; }));
  await page.waitForTimeout(200);
  await page.fill('#r-snapEngagement', '0.2');
  await page.dispatchEvent('#r-snapEngagement', 'input');
  await page.waitForFunction(() => /--snap-lock 0\.2/.test(
    document.getElementById('cli').textContent), null, { timeout: 10000 });
  check((await page.textContent('#parts tr:nth-child(6) .part')).trim() ===
        'Screw-head finial cap', 'finial is presented as the screw-head cap');
  check(/--snap-lock 0.2/.test(await page.textContent('#cli')),
        'CLI echo carries snap engagement', await page.textContent('#cli'));
  /* The insert has to leave two walls between its hole and the panel grooves.
     With M3 fixed that guard is reached from the other side -- a 16 mm post
     leaves nothing -- and it is the size the lamp ships with, not a notch on a
     slider nobody would choose. */
  await page.fill('#r-post', '17');
  await page.dispatchEvent('#r-post', 'input');
  await page.waitForFunction(() => /leaves under two walls/.test(
    document.getElementById('problems').textContent), null, { timeout: 10000 });
  check(/leaves under two walls/.test(await page.textContent('#problems')) &&
        await page.isDisabled('#dl-all'),
        'a post too slim for the insert blocks export, and says which wall',
        (await page.textContent('#problems')).replace(/\s+/g, ' ').slice(0, 90));
  await page.fill('#r-post', '18');
  await page.dispatchEvent('#r-post', 'input');
  await page.waitForFunction(() => !document.getElementById('dl-all').disabled,
    null, { timeout: 10000 });
  check(!(await page.isDisabled('#dl-all')), 'the stock post carries it again');
  await page.click('button[data-cap-screws="off"]');
  await page.waitForTimeout(700);
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => { d.open = true; }));
  check(await page.$$eval('#parts tr', r => r.length) === 6,
        'back to six rows once the insert is off');
  check(!(await page.isVisible('#a-screw')) &&
        /friction fit/.test(await page.textContent('#s-screw')),
        'the step and the screw spec go away with it');
  check(!(await page.isDisabled('#dl-all')), 'lower-foot snaps remain exportable without finials');

  /* One number breaks the arrises of all eight blocks -- four feet, four
     finials -- so it has to reach the generator, not just the preview. */
  await page.fill('#r-legChamfer', '2');
  await page.dispatchEvent('#r-legChamfer', 'input');
  await page.waitForTimeout(600);
  check(/--leg-chamfer 2/.test(await page.textContent('#cli')),
        'CLI echo carries the leg chamfer', await page.textContent('#cli'));
  check(!(await page.isDisabled('#dl-all')) &&
        (await page.$$eval('#parts tr', r => r.length)) === 6,
        'a chamfered lamp still builds its six parts');
  const [chamferDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 20000 }),
    page.click('#parts tr:nth-child(5) button.dl')
  ]);
  check(chamferDl.suggestedFilename() === 'leg.stl' &&
        lastExportRequest && lastExportRequest.params.leg_chamfer === 2,
        'the leg request carries the chamfer under the generator\'s name',
        lastExportRequest && String(lastExportRequest.params.leg_chamfer));
  await page.fill('#r-legChamfer', '0');
  await page.dispatchEvent('#r-legChamfer', 'input');
  await page.waitForTimeout(500);
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
  const modernGroupOrder = await page.$$eval('#rail details.grp > summary', s =>
    s.map(x => x.textContent.trim()));
  check(modernGroupOrder.slice(0, 3).join(',') === 'Lantern,Pattern,Lamp holder',
        'Modern pins Lamp holder directly after Pattern', modernGroupOrder.slice(0, 3).join(','));
  check(await page.$eval('#r-socketNeck', x => x.closest('details').classList.contains('fixed') &&
        x.closest('details').open), 'Modern lamp-holder controls start pinned open');
  check(await page.$('#r-post') === null && await page.$('#r-snapEngagement') === null &&
        await page.$('#r-modernThreadClear') !== null && await page.$('#r-plateT') !== null,
        'Modern hides Classic frame/snap controls and shows thread and diffuser controls');
  check(await page.getAttribute('#r-plateT', 'max') === '4',
        'Modern diffuser control reaches 4 mm');
  /* The Modern base prints deck-down, so a riser above that deck would grow
     into the bed; the slider is Classic-only rather than merely ignored. */
  check(await page.$('#r-socketRiser') === null,
        'Modern does not offer the socket riser');
  check(!(await page.isVisible('#s-hardware')) &&
        await page.$('button[data-cap-screws]') === null,
        'Modern offers neither the cap-screw toggle nor its hardware rows');
  const modernRows = await page.$$eval('#parts .note', n =>
    n.map(x => x.textContent.split(' ')[0]));
  check(modernRows.join(',') ===
        'modern_shade_asanoha.stl,modern_base.stl,socket_adapter_ring.stl',
        'Modern print list has the two bodies and stable adapter filename',
        modernRows.join(','));
  check((await page.textContent('#cli')).trim() ===
        'python3 kumiko_lamp.py --style modern',
        'Modern preset has a clean CLI echo', (await page.textContent('#cli')).trim());
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.fill('#r-plateT', '1.2');
  await page.dispatchEvent('#r-plateT', 'input');
  await page.waitForFunction(() => document.querySelectorAll('#parts tr').length === 4,
    null, { timeout: 10000 });
  const modernGlazedRows = await page.$$eval('#parts .note', n =>
    n.map(x => x.textContent.split(' ')[0]));
  check(modernGlazedRows.join(',') ===
        'modern_shade_asanoha.stl,diffuser_plate.stl,modern_base.stl,socket_adapter_ring.stl',
        'Modern diffuser adds one stable closed-cup print row',
        modernGlazedRows.join(','));
  check(/lid down/.test(await page.textContent('#parts tr:nth-child(2) .note')),
        'Modern diffuser row gives its support-free lid-down orientation');
  check(await page.textContent('#parts tr:nth-child(2) td.num') === '1' &&
        /--diffuser-plate 1\.2/.test(await page.textContent('#cli')),
        'Modern diffuser quantity and CLI echo use the existing option',
        await page.textContent('#cli'));
  check(/printed cup/.test(await page.textContent('#s-diff')) &&
        /level with the shade top/.test(await page.textContent(
          '.block:nth-of-type(2) .cols > div:nth-child(3) p')) &&
        /prints lid-down without supports/.test(await page.textContent(
          '.block:nth-of-type(2) .cols > div:nth-child(3) p')),
        'Modern diffuser copy explains its flush top and support-free print');
  await page.fill('#r-plateT', '0');
  await page.dispatchEvent('#r-plateT', 'input');
  await page.waitForFunction(() => document.querySelectorAll('#parts tr').length === 3,
    null, { timeout: 10000 });
  const modernFamilies = await page.$$eval('.famtabs button', b =>
    b.map(x => x.textContent.trim()));
  check(modernFamilies.join(',') === 'Kumiko' &&
        await page.$('button[data-family="laithai"]') === null,
        'Modern offers the eleven Kumiko patterns only', modernFamilies.join(','));
  const seigaihaStarted = Date.now();
  await page.click('button[data-pattern="seigaiha"]');
  await page.waitForFunction(() =>
    document.getElementById('sw-meta').textContent ===
      'filled repeat · continuously wrapped', null, { timeout: 10000 });
  check((await page.textContent('#sw-meta')).trim() ===
        'filled repeat · continuously wrapped',
        'Modern Seigaiha swatch describes filled artwork, not slats');
  check((await page.textContent('#parts tr:first-child .note')).includes(
          'modern_shade_seigaiha.stl'),
        'Modern filled Seigaiha keeps its stable shade filename');
  check(Date.now() - seigaihaStarted < 10000,
        'Modern filled Seigaiha live preview remains responsive',
        `${Date.now() - seigaihaStarted} ms`);
  const swatchInk = await page.$eval('#flat', c => {
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    const colours = new Set();
    for (let i = 0; i < d.length; i += 64)
      colours.add(`${d[i]},${d[i + 1]},${d[i + 2]},${d[i + 3]}`);
    return colours.size;
  });
  check(swatchInk > 2, 'Modern Seigaiha swatch contains filled material and openings',
        `${swatchInk} sampled colours`);
  await page.click('button[data-pattern="asanoha"]');
  await page.waitForFunction(() =>
    document.querySelector('#parts tr:first-child .note').textContent.includes(
      'modern_shade_asanoha.stl'), null, { timeout: 10000 });
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

  /* The watertightness of these bytes is export.test.js's job now -- the server
     builds them and this file only has a stub. What is still provable here is
     that the page asks for the right part under its stable name. */
  const [modernShadeDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.click('#parts tr:first-child button.dl')
  ]);
  check(modernShadeDl.suggestedFilename() === 'modern_shade_asanoha.stl',
        'direct Modern shade keeps its stable filename', modernShadeDl.suggestedFilename());
  check(lastExportRequest && lastExportRequest.part === 'modern_shade_asanoha' &&
        lastExportRequest.style === 'modern',
        'the Modern shade request names the part and the style',
        lastExportRequest && `${lastExportRequest.style}/${lastExportRequest.part}`);

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
  check(e14RingDl.suggestedFilename() === 'socket_adapter_ring.stl',
        'Modern E14 adapter keeps the stable adapter-ring filename',
        e14RingDl.suggestedFilename());
  check(lastExportRequest && lastExportRequest.params &&
        lastExportRequest.params.socket_neck === 27 &&
        !('holder_type' in lastExportRequest.params),
        'the E14 preset reaches the server as a neck dimension, not a label',
        lastExportRequest && lastExportRequest.params &&
        String(lastExportRequest.params.socket_neck));
  await page.fill('#r-socketNeck', '27.5');
  await page.dispatchEvent('#r-socketNeck', 'input');
  await page.waitForFunction(() => /--socket-neck 27\.5/.test(
    document.getElementById('cli').textContent), null, { timeout: 10000 });
  const [customRingDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 20000 }),
    page.click('#parts tr:last-child button.dl')
  ]);
  check(customRingDl.suggestedFilename() === 'socket_adapter_ring.stl' &&
        lastExportRequest && lastExportRequest.params.socket_neck === 27.5,
        'Modern manual holder bore reaches CLI and export',
        `${await page.textContent('#cli')} / ${lastExportRequest.params.socket_neck}`);
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.click('button[data-holder="e27"]');
  await page.waitForTimeout(550);
  check(await page.inputValue('#r-socketNeck') === '26.5',
        'Modern E27 selector restores its holder preset');
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
  check(modernBaseDl.suggestedFilename() === 'modern_base.stl',
        'Modern base keeps its stable filename', modernBaseDl.suggestedFilename());
  const [modernZipDl] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.click('#dl-all')
  ]);
  check(modernZipDl.suggestedFilename() === 'kumiko-lamp-modern-100mm-asanoha.zip',
        'Modern ZIP is style-and-diameter named', modernZipDl.suggestedFilename());
  check(lastExportRequest && lastExportRequest.style === 'modern' &&
        lastExportRequest.params && lastExportRequest.params.size === 100 &&
        lastExportRequest.params.modern_base_h === 90,
        'the Modern request carries the shade and base dimensions',
        lastExportRequest && lastExportRequest.params &&
        JSON.stringify({ size: lastExportRequest.params.size,
                         h: lastExportRequest.params.modern_base_h }));

  await page.click('#v-exp'); await page.waitForTimeout(250);
  check(await page.getAttribute('#v-exp', 'aria-pressed') === 'true',
        'Modern exploded view activates');
  await page.click('#v-asm');
  /* One wrapped lattice here, and the page calls it a shade everywhere else. */
  check((await page.textContent('#v-panels')).trim() === 'Shade',
        'Modern labels the lattice toggle Shade',
        (await page.textContent('#v-panels')).trim());
  await page.click('#lang-label'); await page.waitForTimeout(250);
  check((await page.textContent('.styletabs')).includes('โมเดิร์น') &&
        (await page.textContent('.block:nth-of-type(2) .cols > div:nth-child(2) p'))
          .includes('ระยะเผื่อเกลียว'),
        'Modern controls and assembly guidance localize to Thai');
  check((await page.textContent('#v-panels')).trim() === 'โป๊ะ',
        'the lattice toggle localizes to Thai in Modern',
        (await page.textContent('#v-panels')).trim());
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  await page.click('button[data-pattern="seigaiha"]');
  await page.waitForFunction(() =>
    document.getElementById('sw-meta').textContent ===
      'ลายทึบซ้ำ · พันรอบต่อเนื่อง', null, { timeout: 10000 });
  check((await page.textContent('#sw-meta')).trim() ===
        'ลายทึบซ้ำ · พันรอบต่อเนื่อง',
        'Modern filled-repeat metadata localizes to Thai');
  await page.click('button[data-pattern="asanoha"]');
  await page.waitForFunction(() =>
    document.querySelector('#parts tr:first-child .note').textContent.includes(
      'modern_shade_asanoha.stl'), null, { timeout: 10000 });
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
  /* The old "shoulder leaves under two walls" guard went vacuous once the
     cavity started following the outer 45 deg profile: the wall there is
     modernWall by construction.  The reachable failure is now the far end of
     that taper closing up. */
  check(shippedHtml.includes("'Modern base is too short for its 45-degree shoulder.':'ฐานโมเดิร์นสั้นเกินไปสำหรับบ่าลาด 45 องศา'") &&
        shippedHtml.includes("'Modern hollow closes up under the mounting deck.':'ช่องกลวงโมเดิร์นตันใต้แท่นติดตั้ง'") &&
        shippedHtml.includes("'Modern shoulder reaches the cable outlet.':'บ่าลาดโมเดิร์นชนช่องสายไฟ'"),
        'Modern shoulder safety guards carry Thai translations');
  check(shippedHtml.includes("'Modern base diameter is too small for its 5 mm body wall.':") &&
        shippedHtml.includes("'Modern base body is narrower than its threaded neck.':") &&
        shippedHtml.includes("'Modern base diameter exceeds the printer bed.':") &&
        shippedHtml.includes("'Modern shade diameter exceeds the printer bed.':"),
        'independent base-diameter guards carry Thai translations');
  await page.fill('#r-size', '80'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(400);
  check(/คอฐานโมเดิร์น|ช่องระบายอากาศโมเดิร์น/.test(await page.textContent('#problems')),
        'Modern validation localizes to Thai');
  await page.fill('#r-size', '100'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(500);
  await page.click('#lang-label'); await page.waitForTimeout(250);

  // the two diameters start linked, part when the base is dragged, and relink
  check(await page.inputValue('#r-modernBaseD') === '100',
        'base diameter starts showing the shade diameter');
  await page.fill('#r-size', '120'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(500);
  check(await page.inputValue('#r-modernBaseD') === '120',
        'a linked base follows the shade');
  check(!/--modern-base-diameter/.test(await page.textContent('#cli')),
        'a linked base emits no CLI flag', await page.textContent('#cli'));
  await page.fill('#r-modernBaseD', '150');
  await page.dispatchEvent('#r-modernBaseD', 'input');
  await page.waitForTimeout(600);
  check(/--modern-base-diameter 150/.test(await page.textContent('#cli')),
        'editing the base unlinks it', await page.textContent('#cli'));
  const wide = await page.textContent('#d-overall');
  check(/^150 /.test(wide.trim()), 'the wider base drives the assembled footprint',
        wide.trim());
  await page.fill('#r-size', '110'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(600);
  check(await page.inputValue('#r-modernBaseD') === '150',
        'an unlinked base ignores later shade changes');
  await page.fill('#r-modernBaseD', '110');
  await page.dispatchEvent('#r-modernBaseD', 'input');
  await page.waitForTimeout(600);
  check(!/--modern-base-diameter/.test(await page.textContent('#cli')),
        'matching the shade relinks them', await page.textContent('#cli'));
  await page.fill('#r-size', '100'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(600);

  await page.click('button[data-lantern-style="classic"]');
  await page.waitForTimeout(650);
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));
  check(await page.inputValue('#r-size') === '185',
        'Classic setting also survives the Modern round trip');
  await page.fill('#r-size', '180'); await page.dispatchEvent('#r-size', 'input');
  await page.waitForTimeout(500);

  // the rail is its own scroll container above 940px, so reaching a slider
  // never moves the page
  const railBox = await page.evaluate(() => {
    const r = document.getElementById('rail');
    document.querySelectorAll('details.grp').forEach(d => { d.open = true; });
    return { scrollH: r.scrollHeight, clientH: r.clientHeight,
             bottom: Math.round(r.getBoundingClientRect().bottom),
             vh: window.innerHeight };
  });
  check(railBox.scrollH > railBox.clientH, 'the rail scrolls inside itself',
        `${railBox.scrollH} of content in ${railBox.clientH}`);
  check(railBox.bottom <= railBox.vh, 'the whole rail fits on screen unscrolled',
        `bottom ${railBox.bottom} vs viewport ${railBox.vh}`);
  const stillTop = await page.evaluate(() => {
    const r = document.getElementById('rail');
    r.scrollTop = r.scrollHeight;
    return window.scrollY;
  });
  check(stillTop === 0, 'scrolling to the last slider does not move the page',
        `scrollY ${stillTop}`);

  // The square ratio remains the preview's natural minimum. Its backing store
  // must track the real box even when the Pattern column later stretches it.
  const sq = await page.evaluate(() => {
    const v = document.querySelector('.view').getBoundingClientRect();
    const c = document.getElementById('gl');
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    return { w: Math.round(v.width), h: Math.round(v.height),
             cw: c.clientWidth, ch: c.clientHeight, bw: c.width, bh: c.height,
             dpr: dpr, bottom: Math.round(v.bottom), vh: window.innerHeight };
  });
  check(Math.abs(sq.w - sq.h) <= 1, 'the preview is square',
        `${sq.w} x ${sq.h}`);
  check(sq.bw === sq.cw * sq.dpr && sq.bh === sq.ch * sq.dpr,
        'the canvas backing store matches its box',
        `${sq.bw}x${sq.bh} for ${sq.cw}x${sq.ch} at dpr ${sq.dpr}`);

  // Fixed cards are permanently open -- a click must not collapse them, and
  // neither may a style or language switch. Classic has two; Modern adds Holder.
  const fixedOpen = () => page.$$eval('details.grp.fixed',
    d => ({ n: d.length, names: d.map(x => x.querySelector('summary').textContent.trim()),
            allOpen: d.every(x => x.open),
            marker: d.every(x => getComputedStyle(x.querySelector('summary'), '::after')
                                   .content === 'none') }));
  await page.click('details.grp.fixed > summary');
  await page.waitForTimeout(200);
  let fx = await fixedOpen();
  check(fx.n === 2 && fx.allOpen, 'clicking a fixed group does not collapse it',
        `${fx.n} fixed, all open ${fx.allOpen}`);
  check(fx.marker, 'a fixed group shows no +/- marker');
  await page.click('#lang-label'); await page.waitForTimeout(400);
  fx = await fixedOpen();
  check(fx.n === 2 && fx.allOpen, 'fixed groups survive a language switch');
  await page.click('#lang-label'); await page.waitForTimeout(400);
  await page.click('button[data-lantern-style="modern"]'); await page.waitForTimeout(900);
  await page.$eval('#r-socketNeck', x => x.closest('details').querySelector('summary').click());
  await page.waitForTimeout(200);
  fx = await fixedOpen();
  check(fx.n === 3 && fx.allOpen && fx.names.join(',') === 'Lantern,Pattern,Lamp holder',
        'Modern adds a fixed holder card that cannot be collapsed',
        `${fx.n} fixed: ${fx.names.join(',')}`);
  await page.click('#lang-label'); await page.waitForTimeout(400);
  fx = await fixedOpen();
  check(fx.n === 3 && fx.allOpen, 'Modern fixed holder survives a language switch');
  await page.click('#lang-label'); await page.waitForTimeout(400);
  await page.click('button[data-lantern-style="classic"]'); await page.waitForTimeout(900);
  await page.evaluate(() => document.querySelectorAll('details.grp').forEach(d => d.open = true));

  // screenshots, both themes -- collapse the rail back to its default state.
  // window.scrollTo does not reset the rail now that it scrolls independently.
  await page.evaluate(() => {
    document.querySelectorAll('details.grp').forEach(d => {
      d.open = d.classList.contains('fixed');
    });
    document.getElementById('rail').scrollTop = 0;
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

  /* A laptop viewport is where the rail's own scroll fold used to hide the
     pinned groups' controls, which reads as "the setting is missing" rather
     than "scroll for more".  Collapsed headers below the fold are fine; a
     slider or a button below it is the bug. */
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.waitForTimeout(500);
  const belowFold = () => page.evaluate(() => {
    const rail = document.getElementById('rail');
    const rb = rail.getBoundingClientRect();
    rail.scrollTop = 0;
    const cut = [];
    document.querySelectorAll('#rail .ctl, #rail .styletabs').forEach(c => {
      const d = c.closest('details');
      if (!d.classList.contains('fixed')) return;
      if (c.getBoundingClientRect().bottom > rb.bottom + 1) {
        const lab = c.querySelector('label span, button');
        cut.push(d.querySelector('summary').textContent.trim() + '/' +
                 (lab ? lab.textContent.trim() : '?'));
      }
    });
    return cut;
  });
  let cut = await belowFold();
  check(cut.length === 0, 'no pinned Classic control below the rail fold at 1366x768',
        cut.join(', '));

  /* Modern intentionally has a third pinned card. It cannot all fit above the
     fold, so prove the rail owns that scroll and the holder's last control is
     reachable without moving the document or collapsing the card. */
  await page.click('button[data-lantern-style="modern"]');
  await page.waitForTimeout(1000);
  await page.setViewportSize({ width: 1536, height: 864 });
  await page.waitForTimeout(500);
  const holderReach = await page.evaluate(() => {
    const rail = document.getElementById('rail');
    const input = document.getElementById('r-cableH');
    const group = input.closest('details');
    const pageY = window.scrollY;
    input.scrollIntoView({ block: 'nearest' });
    const rb = rail.getBoundingClientRect(), ib = input.getBoundingClientRect();
    return { scrollable: rail.scrollHeight > rail.clientHeight,
      inRail: ib.top >= rb.top - 1 && ib.bottom <= rb.bottom + 1,
      pageStill: window.scrollY === pageY, open: group.open,
      fixed: group.classList.contains('fixed') };
  });
  check(holderReach.scrollable && holderReach.inRail && holderReach.pageStill &&
        holderReach.open && holderReach.fixed,
        'expanded Modern holder stays reachable in the rail scroll',
        JSON.stringify(holderReach));

  // the picker follows the style switch: Modern wraps kumiko only
  const modernPicker = await page.evaluate(() => ({
    n: document.querySelectorAll('#picker button[data-pattern]').length,
    rail: document.querySelectorAll('#rail button[data-pattern]').length
  }));
  check(modernPicker.n === 11 && modernPicker.rail === 0,
        'the picker rebuilds beside the preview on a style switch',
        `${modernPicker.n} in the picker, ${modernPicker.rail} in the rail`);
  /* Below 940px the bench is one column and the rail spans the page.  As a flex
     column that stretched every card to full width -- an 820px tablet gave a
     772px card with a 742px slider, a label at the far left and its value at the
     far right.  It tiles now. */
  const railGrid = () => page.evaluate(() => {
    const rail = document.getElementById('rail');
    const cards = [...rail.querySelectorAll('details.grp')].map(d => d.getBoundingClientRect());
    return { rail: Math.round(rail.getBoundingClientRect().width),
             card: Math.round(cards[0].width),
             cols: new Set(cards.map(c => Math.round(c.left))).size };
  });
  /* Each card in a packed column must follow the previous card by the ordinary
     10px rail gap.  The old shared-row grid produced gaps over 100px here when
     the pinned holder sat beside Pattern or Thread. */
  const packedRail = () => page.evaluate(() => {
    const byX = new Map();
    document.querySelectorAll('#rail details.grp').forEach(d => {
      const r = d.getBoundingClientRect(), x = Math.round(r.left);
      if (!byX.has(x)) byX.set(x, []);
      byX.get(x).push({ name: d.querySelector('summary').textContent.trim(),
                       top: r.top, bottom: r.bottom });
    });
    const gaps = [];
    byX.forEach(cards => {
      cards.sort((a, b) => a.top - b.top);
      for (let i = 1; i < cards.length; i++)
        gaps.push({ pair: cards[i - 1].name + '/' + cards[i].name,
                    gap: cards[i].top - cards[i - 1].bottom });
    });
    const bottoms = [...byX.values()].map(cards => Math.max(...cards.map(c => c.bottom)));
    return { cols: byX.size, maxGap: gaps.length ? Math.max(...gaps.map(g => g.gap)) : 0,
             bottomSpread: bottoms.length ? Math.max(...bottoms) - Math.min(...bottoms) : 0,
             gaps: gaps };
  });
  await page.setViewportSize({ width: 820, height: 1180 });
  await page.waitForTimeout(500);
  let rg = await railGrid();
  check(rg.cols > 1, 'the expanded Modern rail tiles on a tablet',
        `${rg.cols} columns of ${rg.card}px in ${rg.rail}px`);
  check(rg.card <= rg.rail * 0.6, 'a tiled card is not a full-width bar',
        `${rg.card} of ${rg.rail}`);
  let packed = await packedRail();
  check(packed.cols === 2 && packed.maxGap <= 12,
        'two-column customization cards have no empty grid holes',
        JSON.stringify(packed));
  check(packed.bottomSpread <= 35,
        'two-column customization cards leave no large empty tail',
        JSON.stringify(packed));

  await page.locator('#rail details.grp').filter({ hasText: /^Thread/ }).locator('summary').click();
  await page.locator('#rail details.grp').filter({ hasText: /^Printer/ }).locator('summary').click();
  await page.waitForTimeout(300);
  packed = await packedRail();
  check(packed.maxGap <= 12, 'expanded cards repack without gaps', JSON.stringify(packed));

  await page.click('#lang-label'); await page.waitForTimeout(400);
  packed = await packedRail();
  check(packed.maxGap <= 12, 'translated customization cards remain packed', JSON.stringify(packed));
  await page.click('#lang-label'); await page.waitForTimeout(400);
  await page.click('button[data-lantern-style="classic"]'); await page.waitForTimeout(700);
  packed = await packedRail();
  check(packed.cols === 2 && packed.maxGap <= 12 && packed.bottomSpread <= 35,
        'Classic customization cards also stay compact and balanced', JSON.stringify(packed));
  await page.click('button[data-lantern-style="modern"]'); await page.waitForTimeout(900);
  packed = await packedRail();
  check(packed.cols === 2 && packed.maxGap <= 12,
        'style rebuild keeps customization cards packed', JSON.stringify(packed));

  await page.setViewportSize({ width: 940, height: 1180 });
  await page.waitForTimeout(400);
  packed = await packedRail();
  check(packed.cols === 3 && packed.maxGap <= 12,
        'three-column customization cards have no empty grid holes', JSON.stringify(packed));
  check(packed.bottomSpread <= 90,
        'three-column customization cards balance their total height', JSON.stringify(packed));

  /* Above 760px the Pattern panel and preview share one grid row.  The panel is
     taller at these constrained widths, so the interactive canvas must reach
     the same bottom instead of leaving a blank rectangle beneath it. */
  const previewFit = () => page.evaluate(() => {
    const view = document.querySelector('.view').getBoundingClientRect();
    const side = document.querySelector('.side').getBoundingClientRect();
    const stage = document.querySelector('.stage').getBoundingClientRect();
    const canvas = document.getElementById('gl');
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    return { viewBottom: view.bottom, sideBottom: side.bottom, stageBottom: stage.bottom,
      viewWidth: Math.round(view.width), viewHeight: Math.round(view.height),
      cssWidth: canvas.clientWidth, cssHeight: canvas.clientHeight,
      storeWidth: canvas.width, storeHeight: canvas.height, dpr: dpr,
      viewportHeight: window.innerHeight };
  });
  for (const style of ['classic', 'modern']) {
    await page.click(`button[data-lantern-style="${style}"]`);
    await page.waitForTimeout(800);
    for (const width of [761, 820, 940, 941, 1024, 1366]) {
      await page.setViewportSize({ width, height: width === 1366 ? 768 : 1180 });
      await page.waitForTimeout(250);
      const fit = await previewFit();
      check(Math.abs(fit.viewBottom - fit.sideBottom) <= 1 &&
            Math.abs(fit.viewBottom - fit.stageBottom) <= 1,
            `${style} preview fills the Pattern-panel row at ${width}px`, JSON.stringify(fit));
      check(fit.storeWidth === fit.cssWidth * fit.dpr &&
            fit.storeHeight === fit.cssHeight * fit.dpr,
            `${style} stretched canvas backing store matches at ${width}px`, JSON.stringify(fit));
      if (width === 1366 && style === 'modern')
        check(fit.viewBottom > fit.viewportHeight,
              'the short desktop viewport allows the preview to fill below the fold',
              JSON.stringify(fit));
    }
  }
  await page.setViewportSize({ width: 820, height: 1180 });
  await page.click('#lang-label'); await page.waitForTimeout(400);
  let fit = await previewFit();
  check(Math.abs(fit.viewBottom - fit.sideBottom) <= 1,
        'translated preview still fills the Pattern-panel row', JSON.stringify(fit));
  await page.click('#lang-label'); await page.waitForTimeout(400);
  const stretchedRender = await page.evaluate(() => {
    const c = document.getElementById('gl'), gl = c.getContext('webgl');
    const px = new Uint8Array(c.width * c.height * 4);
    gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const seen = new Set();
    for (let i = 0; i < px.length; i += 4 * 997)
      seen.add(px[i] + ',' + px[i + 1] + ',' + px[i + 2]);
    return seen.size;
  });
  check(stretchedRender > 8, 'the stretched preview still renders the lamp',
        `${stretchedRender} distinct sampled colours`);

  // narrow viewport must not scroll sideways
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  await page.setViewportSize({ width: 420, height: 900 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  const phoneStage = await page.evaluate(() => {
    const stage = document.querySelector('.stage');
    const view = document.querySelector('.view').getBoundingClientRect();
    const side = document.querySelector('.side').getBoundingClientRect();
    return { columns: getComputedStyle(stage).gridTemplateColumns.split(/\s+/).length,
      gap: side.top - view.bottom, viewWidth: view.width, viewHeight: view.height };
  });
  check(phoneStage.columns === 1 && phoneStage.gap >= 15 && phoneStage.gap <= 17,
        'the phone keeps separate stacked preview and Pattern rows', JSON.stringify(phoneStage));
  rg = await railGrid();
  check(rg.cols === 1, 'a phone still gets a single column',
        `${rg.cols} columns of ${rg.card}px in ${rg.rail}px`);
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(overflow <= 1, 'expanded Modern rail has no horizontal overflow at 420px', `${overflow}px`);
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
  await new Promise((r) => server.close(r));
  console.log(fails ? `\n${fails} FAILURES` : '\nall browser checks passed');
  process.exit(fails ? 1 : 0);
})();
