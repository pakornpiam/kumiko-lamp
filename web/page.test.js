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
