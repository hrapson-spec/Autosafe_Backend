// ESLint flat config (ESM). Scoped to exactly the app + new tooling files
// (same allowlist as tsconfig.json's "include") so generated/vendored output
// (static/, dist/) and root-level build config files (postcss.config.js,
// tailwind.config.js, this file) are never swept into linting.
import tseslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';

const appFiles = [
  'App.tsx',
  'index.tsx',
  'types.ts',
  'vite.config.ts',
  'vitest.setup.ts',
  'playwright.config.ts',
  'components/**/*.ts',
  'components/**/*.tsx',
  'services/**/*.ts',
  'utils/**/*.ts',
  'hooks/**/*.ts',
  'hooks/**/*.tsx',
  'e2e/**/*.ts',
];

export default [
  {
    ignores: ['node_modules/**', 'static/**', 'dist/**', 'playwright-report/**'],
  },
  {
    files: appFiles,
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
    },
    settings: {
      react: { version: 'detect' },
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react/no-unstable-nested-components': ['error', { allowAsProps: false }],
    },
  },
];
