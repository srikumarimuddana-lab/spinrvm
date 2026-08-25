import { chromium } from '/home/user/spinrvm/admin-dashboard/node_modules/playwright/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

const sizes = JSON.parse(fs.readFileSync('_render/sizes.json', 'utf8'));
const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  args: ['--force-color-profile=srgb', '--disable-lcd-text'],
});

for (const [name, [w, h]] of Object.entries(sizes)) {
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.resolve(`_render/${name}.html`), { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  const out = `png/${name}.png`;
  await page.screenshot({ path: out, clip: { x: 0, y: 0, width: w, height: h } });
  const kb = (fs.statSync(out).size / 1024).toFixed(0);
  console.log(`${name.padEnd(12)} ${String(w).padStart(4)}x${String(h).padStart(4)}  ${kb.padStart(4)} KB`);
  await page.close();
}
await browser.close();
console.log('\ndone');
