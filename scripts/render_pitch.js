// Renders pitch.html to pitch.pdf (one 1920x1080 page per slide) and optional PNG previews.
// Usage: node scripts/render_pitch.js [previewDir]
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const previewDir = process.argv[2];
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.join(root, 'pitch.html'), { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.emulateMedia({ media: 'print' });
  await page.pdf({ path: path.join(root, 'pitch.pdf'), width: '1920px', height: '1080px',
                   printBackground: true, margin: { top: 0, right: 0, bottom: 0, left: 0 } });
  const slides = await page.$$('.slide');
  if (previewDir) {
    for (let i = 0; i < slides.length; i++) await slides[i].screenshot({ path: path.join(previewDir, `slide${i + 1}.png`) });
  }
  console.log(`pitch.pdf written (${slides.length} slides)`);
  await browser.close();
})();
