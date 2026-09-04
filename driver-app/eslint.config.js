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
  {
    // Enforce design-token usage: colors must come from useTheme()
    // (shared/theme/index.ts), not hardcoded hex literals scattered
    // through screens/components. A hardcoded hex bypasses the single
    // source of truth for the palette, so a rebrand or dark-mode pass has
    // to hunt every occurrence instead of changing one token. 'warn' (not
    // 'error') because ~53 files predate this rule — same
    // pre-existing-violations posture as admin-dashboard/eslint.config.mjs;
    // tighten to 'error' once cleaned up.
    //
    // (Originally pointed this message at SpinrConfig.theme.colors — that
    // was the deprecated, drifted source shared/theme/index.ts's own
    // header comment says never to import directly; it's since been
    // deleted entirely (2026-09-04), so useTheme() is now correctly the
    // only path anyway.)
    //
    // Second selector pair (below): enforce spacing/font-scale token usage
    // — padding/margin/fontSize values should come from SPACING/FONT
    // (shared/utils/responsive.ts), not hardcoded numeric literals.
    // shared/utils/responsive.ts is a genuinely well-built utility (dynamic
    // font scaling clamped +/-30%, responsive breakpoints, tablet-aware
    // sheet snaps) that almost nothing in the app actually imports — this
    // is the same "lint enforcement + opportunistic migration" fix as the
    // hex-color rule above, not a redesign. Also 'warn' for the same
    // pre-existing-violations reason (hundreds of pre-existing call sites).
    // Scoped to the padding/margin/fontSize property family specifically
    // (not all numeric literals) because numbers are used for many
    // legitimate non-spacing things (array indices, opacity, zIndex,
    // durations, dimensions unrelated to spacing) that would otherwise
    // drown real hits in false positives. Two selector variants cover both
    // `paddingTop: 8` (plain Literal) and `marginLeft: -8` (negative
    // numbers parse as a UnaryExpression wrapping a Literal, not a Literal
    // themselves).
    //
    // NOTE: both rule pairs live in one `no-restricted-syntax` array
    // deliberately — ESLint flat config does not merge an array-valued
    // rule option across two config objects that match the same files;
    // the later object's array fully replaces the earlier one. A separate
    // second block here (each setting its own `no-restricted-syntax`)
    // silently disabled the hex-color rule entirely (verified: 729 → 0
    // warnings in rider-app; same mechanism applies here). Add any further
    // selector to this same array, not a new block, unless its `files`
    // glob is truly disjoint from this one.
    files: ['app/**/*.{ts,tsx}', 'store/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'warn',
        {
          selector: "Literal[value=/^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/]",
          message:
            'Do not hardcode hex colors — call useTheme() from shared/theme and use its colors.* design tokens instead.',
        },
        {
          selector:
            "Property[key.name=/^(padding|margin|fontSize)/][value.type='Literal'][value.raw=/^[0-9]+(\\.[0-9]+)?$/]",
          message:
            'Do not hardcode padding/margin/fontSize values — use SPACING/FONT from shared/utils/responsive.ts.',
        },
        {
          selector:
            "Property[key.name=/^(padding|margin|fontSize)/][value.type='UnaryExpression'][value.operator='-'][value.argument.type='Literal'][value.argument.raw=/^[0-9]+(\\.[0-9]+)?$/]",
          message:
            'Do not hardcode padding/margin/fontSize values — use SPACING/FONT from shared/utils/responsive.ts.',
        },
      ],
    },
  },
]);
