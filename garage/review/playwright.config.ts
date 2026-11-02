import { defineConfig } from '../web/node_modules/@playwright/test/index.mjs';
const baseURL = process.env.GARAGE_REVIEW_URL ?? 'http://127.0.0.1:4184';
if (new URL(baseURL).hostname !== '127.0.0.1') throw new Error('Review tests are localhost-only');
export default defineConfig({
  testDir: './e2e', workers: 1, fullyParallel: false, timeout: 60_000,
  outputDir: './state/test-results', reporter: [['list']],
  use: { baseURL, serviceWorkers: 'block', viewport: { width: 1280, height: 900 }, screenshot: 'only-on-failure' },
});
