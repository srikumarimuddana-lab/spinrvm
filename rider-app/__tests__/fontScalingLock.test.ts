/**
 * Guard: text must follow the OS text-size setting (WCAG 2.1 SC 1.4.4).
 *
 * `allowFontScaling={false}` ignores the user's setting entirely. Use
 * `maxFontSizeMultiplier={MAX_FONT_SCALE}` (shared/utils/responsive.ts) for
 * tight layouts instead. A genuine exception must say why in a comment
 * containing `font-scale-lock:` within the 4 lines above the prop.
 *
 * Covers rider-app/app and rider-app/components (clean-sheet finding
 * UXA11Y-001; docs/audit/2026-09-25-ux-scorecard-world-class-minimal.md).
 */
import * as fs from 'fs';
import * as path from 'path';

const ROOT = path.resolve(__dirname, '..');
const DIRS = ['app', 'components'];
const LOCK = 'allowFontScaling={false}';
const JUSTIFICATION = 'font-scale-lock:';

function listTsx(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return entry.name === '__tests__' || entry.name === 'node_modules' ? [] : listTsx(full);
    }
    return entry.name.endsWith('.tsx') ? [full] : [];
  });
}

describe('OS text-size setting is respected', () => {
  it('has no unexplained allowFontScaling={false}', () => {
    const violations: string[] = [];
    for (const file of DIRS.flatMap((d) => listTsx(path.join(ROOT, d)))) {
      const lines = fs.readFileSync(file, 'utf8').split('\n');
      lines.forEach((line, i) => {
        if (!line.includes(LOCK)) return;
        const context = lines.slice(Math.max(0, i - 4), i + 1).join('\n');
        if (!context.includes(JUSTIFICATION)) {
          violations.push(`${path.relative(ROOT, file)}:${i + 1}`);
        }
      });
    }
    expect(violations).toEqual([]);
  });
});
