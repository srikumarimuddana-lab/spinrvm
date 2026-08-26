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
      // ACTION_ITEMS.md B37 milestone ratchet (see
      // docs/testing/coverage-ratchet-plan.md): re-measured 2026-08-24
      // (`npx vitest run --coverage`, 36/36 files, 351/351 tests):
      // 19.11% statements / 13.05% branches / 11.71% functions / 20.75%
      // lines -- essentially unchanged from the 2026-08-22 measurement
      // this threshold was tightened against (19%/12.98%/11.66%/20.64%).
      // Left as-is this round: already ~1-2pts below current measured on
      // every metric, matching the plan doc's target gap, and admin-
      // dashboard hasn't had the same screen-by-screen test-authoring
      // pass rider-app/driver-app got. Raising further requires new
      // tests, not just re-measuring.
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
