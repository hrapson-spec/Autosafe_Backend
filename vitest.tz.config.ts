import { defineConfig } from 'vitest/config';

// Dedicated config for the timezone-pinned date-rendering regression matrix
// (components/formatDateGB.tzspec.ts). Run explicitly, never as part of the
// default `npx vitest run`:
//
//     TZ='Europe/London' npx vitest run --config vitest.tz.config.ts
//
// The tzspec file is named '*.tzspec.ts' so it sits outside vite.config.ts's
// default test.include glob; this config includes ONLY it. environment 'node'
// is sufficient (formatDateGB is pure JS/Intl, no DOM) and avoids any jsdom
// timezone caching -- the process-level TZ env var is the single source of
// truth for date parsing here.
export default defineConfig({
  test: {
    include: ['components/formatDateGB.tzspec.ts'],
    environment: 'node',
  },
});
