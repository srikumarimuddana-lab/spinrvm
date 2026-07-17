// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ['dist/*'],
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
