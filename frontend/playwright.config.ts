import { defineConfig, devices } from '@playwright/test';

// The tests' own installation; 8899 is left for trying the app by hand (installation.py).
const PORT = Number(process.env['P42_E2E_PORT'] ?? 8898);
process.env['P42_E2E_PORT'] = String(PORT);
const FRESH = PORT - 1;
const URL = `http://localhost:${PORT}`;

/** The browser tests at four sizes, against a real installation serving the built app. */
export default defineConfig({
  testDir: 'e2e',
  // One installation shared by all sizes: tests run one after another and tidy up after
  // themselves.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env['CI'],
  retries: process.env['CI'] ? 1 : 0,
  reporter: process.env['CI'] ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: URL,
    extraHTTPHeaders: { Origin: URL },
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `uv run --directory ../backend python ../frontend/e2e/installation.py --port ${PORT}`,
      url: `${URL}/api/v1/health`,
      reuseExistingServer: !process.env['CI'],
      timeout: 120_000,
      // Stopped politely, so the installation drops its database (a kill leaves it for the
      // next run to clear).
      gracefulShutdown: { signal: 'SIGTERM', timeout: 15_000 },
    },
    {
      // Nobody has set this one up: the web installer's test.
      command: `uv run --directory ../backend python ../frontend/e2e/installation.py --port ${FRESH} --fresh`,
      url: `http://localhost:${FRESH}/api/v1/health`,
      reuseExistingServer: !process.env['CI'],
      timeout: 120_000,
      gracefulShutdown: { signal: 'SIGTERM', timeout: 15_000 },
    },
  ],
  projects: [
    { name: 'setup', testMatch: /.*\.setup\.ts/ },
    {
      name: 'desktop',
      use: { viewport: { width: 1440, height: 900 }, colorScheme: 'light' },
      dependencies: ['setup'],
    },
    {
      name: 'dark',
      use: { viewport: { width: 1440, height: 900 }, colorScheme: 'dark' },
      dependencies: ['setup'],
    },
    {
      name: 'laptop',
      use: { viewport: { width: 1280, height: 800 }, colorScheme: 'light' },
      dependencies: ['setup'],
    },
    {
      name: 'tablet',
      use: { viewport: { width: 820, height: 1180 }, hasTouch: true, colorScheme: 'light' },
      dependencies: ['setup'],
    },
    {
      name: 'phone',
      use: { ...devices['Pixel 7'], colorScheme: 'dark' },
      dependencies: ['setup'],
    },
  ],
});
