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
      // dead code). Measured real coverage on 2026-08-22, the first time it
      // was ever run in anger: 13.56% branches / 11.88% functions / 21.80%
      // lines / 19.94% statements — nowhere near the previously-configured
      // 50/50/60/60. Dropped to a real, currently-passing floor (measured
      // minus a few points of headroom) so turning this gate on doesn't
      // instant-fail every PR; ratchet up from here as coverage improves,
      // same pattern as backend/pytest.ini's coverage ratchet history.
      // `include` widened same day from 'src/app/dashboard/**' to all of
      // 'src/app/**' (picks up login/register/company-* route groups);
      // re-measured at 12.98% branches / 11.66% functions / 20.63% lines /
      // 19% statements — close enough to the dashboard-only numbers above
      // that these thresholds still hold without further lowering.
      thresholds: {
        branches: 10,
        functions: 10,
        lines: 18,
        statements: 15,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
