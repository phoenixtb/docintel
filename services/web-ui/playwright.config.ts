import { defineConfig, devices } from '@playwright/test';

/**
 * E2E tests for the static SPA. The IdP and the API gateway are faked with
 * Playwright request routing (tests/support), so no backend is needed.
 */
export default defineConfig({
  testDir: './tests',

  fullyParallel: true,

  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,

  /* No retries: a flaky test is a bug to fix, not to retry (CLAUDE.md §6). */
  retries: 0,

  workers: process.env.CI ? 2 : undefined,

  reporter: process.env.CI
    ? [['github'], ['html', { outputFolder: 'playwright-report', open: 'never' }]]
    : [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],

  /* Bound every wait so a broken page fails in seconds, not minutes. */
  timeout: 20_000,
  expect: { timeout: 5_000 },

  use: {
    baseURL: 'http://localhost:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-chrome', use: { ...devices['Pixel 5'] } },
  ],

  webServer: {
    command: 'npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
