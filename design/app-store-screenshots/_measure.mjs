import { chromium } from '/home/user/spinrvm/admin-dashboard/node_modules/playwright/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

const sizes = JSON.parse(fs.readFileSync('_render/sizes.json', 'utf8'));
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });

for (const [name, [w, h]] of Object.entries(sizes)) {
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.resolve(`_render/${name}.html`), { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  const r = await page.evaluate((H) => {
    const root = document.querySelector('.sp');
    // deepest element bottom, and the phone frame specifically
    let maxBottom = 0, tag = '';
    for (const el of root.querySelectorAll('*')) {
      const b = el.getBoundingClientRect().bottom;
      if (b > maxBottom) { maxBottom = b; tag = el.tagName + '.' + (el.className || '-'); }
    }
    const phone = [...root.querySelectorAll('div')].find(d => /width:\s*858px|width:\s*644px/.test(d.getAttribute('style') || ''));
    const pr = phone ? phone.getBoundingClientRect() : null;
    return { maxBottom: Math.round(maxBottom), tag, phoneTop: pr && Math.round(pr.top), phoneBottom: pr && Math.round(pr.bottom), H };
  }, h);
  const over = r.maxBottom - h;
  console.log(`${name.padEnd(12)} h=${String(h).padStart(4)}  phone ${String(r.phoneTop).padStart(4)}→${String(r.phoneBottom).padStart(4)}  deepest=${String(r.maxBottom).padStart(4)}  ${over > 0 ? 'CLIPPED by ' + over : 'ok (' + (-over) + 'px slack)'}`);
  await page.close();
}
await browser.close();
