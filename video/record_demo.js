// Records a real end-to-end Capy Care run as crisp 1080p frames plus event timestamps.
// Usage: node video/record_demo.js <outDir>   (server must be running on :8000)
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8000';
const TOKEN = process.env.CAPY_DEMO_TOKEN || '';
const REPLY = "Just called Maple Pharmacy. Dr. Alvarez increased it to 10 mg at Mom's follow-up on November 10, so 10 mg is correct.";

(async () => {
  const out = path.resolve(process.argv[2]);
  fs.mkdirSync(path.join(out, 'frames'), { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.request.post(`${BASE}/api/reset`, { headers: { 'X-Demo-Token': TOKEN } });
  await page.goto(`${BASE}/?t=${encodeURIComponent(TOKEN)}`, { waitUntil: 'networkidle' });
  const state = () => page.evaluate(async () => (await fetch('/api/state')).json());
  const waitFor = async (pred, timeoutMs = 420000) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const s = await state();
      if (pred(s)) return s;
      await page.waitForTimeout(600);
    }
    throw new Error('timed out waiting for scenario step');
  };
  await waitFor((s) => s.warm && s.warm !== 'starting' && s.warm !== 'warming', 900000);
  await page.waitForTimeout(1500);

  const frames = [];
  const events = {};
  const t0 = Date.now();
  let running = true;
  const mark = (name) => { events[name] = (Date.now() - t0) / 1000; console.log('event', name, events[name].toFixed(1)); };
  const capture = (async () => {
    let i = 0;
    while (running) {
      const t = (Date.now() - t0) / 1000;
      const file = path.join(out, 'frames', `f${String(i).padStart(5, '0')}.jpg`);
      await page.screenshot({ path: file, type: 'jpeg', quality: 92 });
      frames.push({ file, t });
      i += 1;
      await new Promise((r) => setTimeout(r, 120));
    }
  })();

  await page.waitForTimeout(2500); mark('start');
  await page.click('#btn-docs'); mark('docs');
  await waitFor((s) => s.messages.some((m) => m.sender === 'Elder Capy')); mark('elder');
  await waitFor((s) => s.conflicts.length > 0); mark('conflict');
  await waitFor((s) => s.pending && s.pending.type === 'confirm_dose'); mark('ask');
  await page.waitForTimeout(3500);
  await page.click('#reply-input'); mark('typing');
  await page.type('#reply-input', REPLY, { delay: 26 });
  await page.waitForTimeout(600);
  await page.keyboard.press('Enter'); mark('reply');
  await waitFor((s) => s.conflicts.some((c) => c.resolution_source === 'user') && !s.pending); mark('resolved');
  await page.waitForTimeout(3500);
  await page.click('#btn-voice'); mark('voice');
  await waitFor((s) => s.messages.some((m) => m.sender === 'Elder Capy' && m.content.includes("Sam's note"))); mark('temporal');
  await waitFor((s) => !!s.brief); mark('brief');
  await page.waitForTimeout(7000); mark('end');

  running = false;
  await capture;
  fs.writeFileSync(path.join(out, 'timeline.json'), JSON.stringify({ events, frames }, null, 1));
  console.log(`done: ${frames.length} frames over ${events.end.toFixed(0)}s`);
  await browser.close();
})().catch((err) => { console.error(err); process.exit(1); });
