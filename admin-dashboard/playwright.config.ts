import { defineConfig, devices } from '@playwright/test';
import path from 'path';

const STORAGE_STATE = path.join(__dirname, 'playwright/.auth/admin.json');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  // 'github' alone gives inline annotations in the Actions UI but never
  // writes playwright-report/ — the path ci.yml's "Upload Playwright
  // report" step expects, so every CI run warned "No files were found"
  // and failing runs had no downloadable HTML report to debug from.
  // Keep the annotations, add 'html' so the artifact actually exists.
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'html',
  timeout: 30_000,
  expect: {
    timeout: 8_000,
    // Small tolerance for anti-aliasing/font-rendering jitter between runs
    // on the same CI runner — visual-regression.spec.ts is the only spec
    // using toHaveScreenshot today.
    toHaveScreenshot: { maxDiffPixelRatio: 0.01 },
  },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    // Global auth setup — runs auth.setup.ts once and saves cookie state
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: 'chromium',
      testIgnore: /visual-regression\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        storageState: STORAGE_STATE,
      },
      dependencies: ['setup'],
    },
    // Kept as its own project (run explicitly via --project=visual-regression,
    // never picked up by a bare `playwright test`/`npm run test:e2e`) because
    // it has no committed baselines yet — see
    // .github/workflows/update-visual-baselines.yml. Mixing it into the
    // `chromium` project would make the already-meaningful e2e-test job
    // fail on every run for a reason unrelated to what that job checks.
    {
      name: 'visual-regression',
      testMatch: /visual-regression\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        storageState: STORAGE_STATE,
      },
      dependencies: ['setup'],
    },
  ],
  // No webServer — tests run against a pre-started Next.js server in CI
});
