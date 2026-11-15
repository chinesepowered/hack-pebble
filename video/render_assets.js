// Renders the video's title/close cards (2x), demo frame background, caption overlays,
// and pitch slides 1-3 at 2x for crisp slow zooms.  Usage: node video/render_assets.js <outDir>
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const out = path.resolve(process.argv[2]);
  fs.mkdirSync(out, { recursive: true });
  const root = path.resolve(__dirname, '..');
  const browser = await chromium.launch();

  const hi = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 });
  await hi.goto('file://' + path.join(root, 'video', 'cards.html'), { waitUntil: 'networkidle' });
  await hi.evaluate(() => document.fonts.ready);
  for (const id of ['title', 'close']) await (await hi.$('#' + id)).screenshot({ path: path.join(out, `${id}.png`) });

  const lo = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await lo.goto('file://' + path.join(root, 'video', 'cards.html'), { waitUntil: 'networkidle' });
  await lo.evaluate(() => document.fonts.ready);
  await (await lo.$('#frame')).screenshot({ path: path.join(out, 'frame.png') });
  for (const id of ['cap-ff', 'cap-1a', 'cap-1b', 'cap-1c', 'cap-2a', 'cap-2b', 'cap-3a', 'cap-3b', 'cap-3c']) {
    await (await lo.$('#' + id)).screenshot({ path: path.join(out, `${id}.png`), omitBackground: true });
  }

  await hi.goto('file://' + path.join(root, 'pitch.html'), { waitUntil: 'networkidle' });
  await hi.evaluate(() => document.fonts.ready);
  await hi.emulateMedia({ media: 'print' });
  const slides = await hi.$$('.slide');
  for (let i = 0; i < 3; i++) await slides[i].screenshot({ path: path.join(out, `slide${i + 1}.png`) });

  await browser.close();
  console.log('assets written to', out);
})();
