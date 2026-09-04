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
    // no-undef: 'error' with no jest globals defined. Scoped to the actual
    // jest-only files rather than repo-wide so a real undefined global
    // elsewhere still gets caught.
    files: ['__mocks__/**/*.js', 'jest-setup*.js', 'jest.config.js'],
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
  {
    // Enforce design-token usage: colors must come from
    // SpinrConfig.theme.colors (@shared/config/spinr.config), not
    // hardcoded hex literals scattered through screens/components. A
    // hardcoded hex bypasses the single source of truth for the palette,
    // so a rebrand or dark-mode pass has to hunt every occurrence instead
    // of changing one token. 'warn' (not 'error') because ~55 files
    // predate this rule — same pre-existing-violations posture as
    // admin-dashboard/eslint.config.mjs; tighten to 'error' once cleaned up.
    files: ['app/**/*.{ts,tsx}', 'store/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'warn',
        {
          selector: "Literal[value=/^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/]",
          message:
            'Do not hardcode hex colors — use SpinrConfig.theme.colors.* design tokens from @shared/config/spinr.config instead.',
        },
      ],
    },
  },
]);
