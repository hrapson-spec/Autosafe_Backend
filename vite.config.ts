import path from 'path';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const rawSha = process.env.RAILWAY_GIT_COMMIT_SHA || process.env.GIT_SHA || '';
  const releaseSha = /^[0-9a-f]{7,40}$/.test(rawSha) ? rawSha : '';
  return {
    base: '/',
    server: {
      port: 3000,
      host: '0.0.0.0',
    },
    plugins: [react()],
    build: {
      outDir: 'static',
      emptyOutDir: false,
    },
    define: {
      // Release identity for first-party acquisition events (OA-005). The
      // Docker frontend stage exposes GIT_SHA / RAILWAY_GIT_COMMIT_SHA as env.
      // Anything that is not a hex SHA becomes '' and the field is omitted.
      __RELEASE_SHA__: JSON.stringify(releaseSha),
      'process.env.API_KEY': JSON.stringify(env.GEMINI_API_KEY),
      'process.env.GEMINI_API_KEY': JSON.stringify(env.GEMINI_API_KEY),
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: './vitest.setup.ts',
      include: ['App.test.tsx', 'components/**/*.test.{ts,tsx}', 'services/**/*.test.{ts,tsx}', 'utils/**/*.test.{ts,tsx}'],
      globals: false,
    },
  };
});
