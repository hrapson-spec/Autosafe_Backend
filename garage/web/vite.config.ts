import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The garage app is a standalone Vite project (garage/web). It is built
// separately from the consumer SPA at the repository root.
export default defineConfig({
  base: '/',
  server: { port: 3100, host: '127.0.0.1' },
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: './vitest.setup.ts',
    include: ['src/**/*.test.{ts,tsx}'],
    globals: false,
  },
});
