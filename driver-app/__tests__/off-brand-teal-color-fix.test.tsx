import fs from 'fs';
import path from 'path';

// Ranked audit blocker #23 (docs/audit/2026-08-18-full-fleet-whole-app-audit.md,
// baseline #17 / N7): an off-brand ad hoc teal (`rgba(0,212,170,...)`, plus a
// Tailwind-emerald palette on the subscription screen's free-mode celebration
// card) was used instead of the real Spinr brand token. Spinr is not a
// teal/green brand (.claude/context/brand-spinr.md) — these sites must reuse
// colors.success / colors.successBg, the same convention CustomAlert.tsx's
// fixed tokenization already established, not invent their own hex.
//
// Scope note: an unrelated Tailwind-emerald palette (#ECFDF5/#D1FAE5/etc.) is
// used elsewhere in this codebase for other badges/cards (documents.tsx,
// ride-options.tsx promo pills, ride-details.tsx status badges, and even a
// different badge a few hundred lines away in this same index.tsx file).
// Those are NOT part of this audit finding and are deliberately left
// untouched — this test only pins the specific freeCard/countdown-circle/
// service-area-polygon sites named in the audit, not a blanket ban on the
// hex values app-wide.
const OFF_BRAND_TEAL_RGB = /rgba\(\s*0\s*,\s*212\s*,\s*170/i;

describe('driver-app: off-brand teal replaced with brand success token', () => {
  it('(tabs)/index.tsx service-area polygon and countdown circle use colors.success', () => {
    const filePath = path.resolve(__dirname, '..', 'app', 'driver', '(tabs)', 'index.tsx');
    const source = fs.readFileSync(filePath, 'utf8');
    expect(source).not.toMatch(OFF_BRAND_TEAL_RGB);
    expect(source).toContain('${colors.success}12');
    expect(source).toContain('${colors.success}A6');
    expect(source).toContain('${colors.success}0F');
  });

  it('subscription.tsx free-mode celebration card uses colors.success / colors.successBg, not the ad hoc emerald palette', () => {
    const filePath = path.resolve(__dirname, '..', 'app', 'driver', 'subscription.tsx');
    const source = fs.readFileSync(filePath, 'utf8');
    const freeCardBlock = source.slice(
      source.indexOf('// Free mode celebration card'),
      source.indexOf('// Payment history'),
    );
    expect(freeCardBlock.length).toBeGreaterThan(0);
    for (const offBrandHex of ['#ECFDF5', '#A7F3D0', '#065F46', '#047857', '#D1FAE5']) {
      expect(freeCardBlock).not.toContain(offBrandHex);
    }
    expect(freeCardBlock).toContain('colors.successBg');
    // colors.success used for border tint, title, message, and badge text
    expect(freeCardBlock.match(/colors\.success\b/g)?.length).toBeGreaterThanOrEqual(4);
  });
});
