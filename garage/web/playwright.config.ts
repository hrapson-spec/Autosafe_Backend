import { defineConfig, devices } from '@playwright/test';

// Runs against a MOCK build (MSW service worker, VITE_MOCK_API=1). No real
// API, email or DVSA call is possible: the mock layer answers every
// /api/garage/v1 request in the browser.
// Port 4174 avoids the consumer app's preview port (4173).
// Browser: the shared ~/Library/Caches/ms-playwright/chromium-1228 was an empty stub on
// 2026-10-25. Install a matching headless shell into a private directory with
//   PLAYWRIGHT_BROWSERS_PATH=<dir> npx playwright install chromium --only-shell
// and run the tests with the same variable set. Or set PW_CHROMIUM_PATH to any Chromium binary.
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], launchOptions: { executablePath } } },
  ],
  webServer: {
    command: 'npm run build:mock && npx vite preview --outDir dist-mock --host 127.0.0.1 --port 4174 --strictPort',
    url: 'http://127.0.0.1:4174',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  use: { baseURL: 'http://127.0.0.1:4174', serviceWorkers: 'allow' },
});
