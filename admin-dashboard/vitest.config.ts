import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts', './src/__tests__/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      // ACTION_ITEMS.md B37: widened 2026-08-22 from 'src/app/dashboard/**'
      // to all of 'src/app/**' -- the dashboard-only scope missed
      // login/register/company-signup/company-login/company-portal/track,
      // route groups that also matter for coverage.
      include: ['src/lib/**', 'src/store/**', 'src/components/**', 'src/app/**'],
      exclude: ['src/components/ui/**'],
      // ACTION_ITEMS.md B37: these thresholds were never actually enforced
      // (ci.yml ran `vitest run` without `--coverage`, so this block was
      // dead code) until fixed 2026-08-22, and `include` was widened the
      // same day from 'src/app/dashboard/**' to all of 'src/app/**'.
      // Tightened to a near-ceiling floor 2026-08-22 (user asked to
      // "raise the thresholds toward the real ceiling"). Fresh
      // measurement against the full `include` set: 19% statements /
      // 12.98% branches / 11.66% functions / 20.64% lines. Previously
      // carried several points of headroom (10/10/18/15); now only ~1pt
      // below measured, so this is a tight regression tripwire, not
      // slack. Raising further requires new tests, not just
      // re-measuring -- this is NOT the user's stated 100% target, only
      // the honest ceiling of what's currently tested.
      thresholds: {
        branches: 11,
        functions: 10,
        lines: 19,
        statements: 18,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
