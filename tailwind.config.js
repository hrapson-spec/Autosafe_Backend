/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './*.tsx',
    './*.ts',
    './components/**/*.tsx',
    './services/**/*.ts',
    './utils/**/*.ts',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
