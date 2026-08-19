import fs from 'fs';
import path from 'path';

// Ranked audit blocker #23 — same off-brand teal RGB triplet
// (rgba(0,212,170,...)) was reused for the service-area boundary polygon on
// two rider-app map screens. Must reuse colors.success, not the ad hoc hex.
const OFF_BRAND_TEAL = /rgba\(\s*0\s*,\s*212\s*,\s*170/i;

describe('rider-app: off-brand teal replaced with brand success token', () => {
  it.each(['driver-arriving.tsx', 'ride-options.tsx'])(
    '%s service-area polygon uses colors.success, not the ad hoc teal',
    (file) => {
      const filePath = path.resolve(__dirname, '..', 'app', file);
      const source = fs.readFileSync(filePath, 'utf8');
      expect(source).not.toMatch(OFF_BRAND_TEAL);
      expect(source).toContain('${colors.success}A6');
      expect(source).toContain('${colors.success}12');
    },
  );
});
