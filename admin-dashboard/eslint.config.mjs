import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  // Downgrade pre-existing violations to warnings so CI doesn't block on legacy code.
  // These should be gradually fixed but must not block feature PRs.
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
      "react/no-unescaped-entities": "warn",
      "prefer-const": "warn",
      // React 19 compiler rules (shipped in eslint-config-next@16) flag
      // patterns that pre-date the compiler in ~20 legacy components.
      // Keep them visible as warnings rather than blocking CI while the
      // codebase is migrated.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/static-components": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/purity": "warn",
    },
  },
  // WCAG 2.1 AA accessibility rules (AODA compliance)
  // Note: jsx-a11y plugin is already registered by eslint-config-next/core-web-vitals,
  // so we only override the rule severity levels here (no plugins: {} block).
  {
    rules: {
      "jsx-a11y/alt-text": "warn",
      "jsx-a11y/anchor-has-content": "error",
      "jsx-a11y/anchor-is-valid": "warn",
      "jsx-a11y/aria-props": "error",
      "jsx-a11y/aria-proptypes": "error",
      "jsx-a11y/aria-unsupported-elements": "error",
      // Generic shadcn/ui heading wrappers receive content via {...props}.
      // The rule can't statically verify children and fires on components
      // that are fine in practice — downgrade to warn.
      "jsx-a11y/heading-has-content": "warn",
      "jsx-a11y/html-has-lang": "error",
      "jsx-a11y/img-redundant-alt": "warn",
      "jsx-a11y/interactive-supports-focus": "warn",
      "jsx-a11y/label-has-associated-control": "warn",
      "jsx-a11y/no-access-key": "warn",
      "jsx-a11y/no-autofocus": "warn",
      "jsx-a11y/no-redundant-roles": "warn",
      "jsx-a11y/role-has-required-aria-props": "error",
      "jsx-a11y/role-supports-aria-props": "error",
      "jsx-a11y/scope": "error",
      "jsx-a11y/tabindex-no-positive": "warn",
    },
  },
]);

export default eslintConfig;
