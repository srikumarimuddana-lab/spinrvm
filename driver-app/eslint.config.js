// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');
const globals = require('globals');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ['dist/*'],
  },
  {
    // eslint-config-expo's flat/default.js only sets no-undef: 'off' for
    // TypeScript files (TS's own compiler already catches undefined
    // globals there); plain .js jest mocks/setup files still use core.js's
    // no-undef: 'error' with no jest global defined. scripts/*.js are
    // plain Node CommonJS build-time scripts, so they need Node globals
    // (__dirname, require, module) too. Scoped narrowly rather than
    // repo-wide so a real undefined global elsewhere still gets caught.
    files: ['__mocks__/**/*.js', 'jest.setup.js', 'scripts/**/*.js'],
    languageOptions: {
      globals: {
        ...globals.jest,
        ...globals.node,
      },
    },
  },
  {
    // Raw error.message is transport noise ("JSON Parse error: …",
    // "Request failed with status code 500") — never show it to users.
    files: ['app/**/*.{ts,tsx}', 'store/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: "MemberExpression[object.name=/^(e|err|error)$/][property.name='message']",
          message:
            'Do not surface raw error.message — route user-visible strings through getApiErrorMessage(err, fallback) from @shared/api/client. For logging, pass the whole error object.',
        },
      ],
    },
  },
]);
