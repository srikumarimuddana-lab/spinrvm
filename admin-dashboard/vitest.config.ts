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
      include: ['src/lib/**', 'src/store/**', 'src/components/**', 'src/app/dashboard/**'],
      exclude: ['src/components/ui/**'],
      // ACTION_ITEMS.md B37: these thresholds were configured but never
      // actually ran in CI (`npm test` -> `vitest run`, no `--coverage`
      // flag), so they were unreachable dead code — a live `vitest run
      // --coverage` against the current tree measures 19.94% statements /
      // 13.56% branches / 11.88% functions / 21.80% lines, 25-45 points
      // below the old 50/50/60/60 targets. Dropped to a few points below
      // that real baseline (ratchet floor, not a target) so wiring `ci.yml`
      // to pass `--coverage` in the same PR lands green instead of newly
      // red on main. Same ratchet pattern as `pytest.ini`'s backend
      // coverage history (6%->40%->50%->60%, ceiling 80) -- raise these in
      // small steps as coverage genuinely improves, never lower them.
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
