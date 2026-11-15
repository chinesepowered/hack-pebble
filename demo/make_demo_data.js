// Renders the synthetic demo documents for Capy Care (fictional patient).
// Usage: node demo/make_demo_data.js   (from the repo root)
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname);
  const out = path.join(root, 'data');
  const browser = await chromium.launch();

  const pdfPage = await browser.newPage();
  await pdfPage.goto('file://' + path.join(root, 'sources', 'discharge_summary.html'));
  await pdfPage.pdf({ path: path.join(out, 'discharge_summary.pdf'), format: 'Letter', printBackground: true });

  const imgPage = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 1 });
  await imgPage.goto('file://' + path.join(root, 'sources', 'pill_bottle.html'));
  await imgPage.screenshot({ path: path.join(out, 'pill_bottle.jpg'), type: 'jpeg', quality: 88 });

  await browser.close();
  console.log('wrote', path.join(out, 'discharge_summary.pdf'), 'and', path.join(out, 'pill_bottle.jpg'));
})();
