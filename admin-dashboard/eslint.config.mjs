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
  //
  // @typescript-eslint/no-explicit-any: disabled because ~424 legacy violations exist across
  // api.ts and the dashboard pages (untyped API responses). Re-enable incrementally as
  // domain-specific response types are added. Tracked: CR-2026-002.
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      // Standard TS convention: _-prefixed names are intentionally unused.
      "@typescript-eslint/no-unused-vars": ["warn", {
        "varsIgnorePattern": "^_",
        "argsIgnorePattern": "^_",
        "caughtErrorsIgnorePattern": "^_",
        "destructuredArrayIgnorePattern": "^_",
      }],
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
      "react-hooks/refs": "warn",
      "react-hooks/preserve-manual-memoization": "warn",
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
  // Defence-in-depth: require rel="noopener noreferrer" on blank-target links.
  // Modern browsers default to noopener but we don't rely on that (A-PE-P3-6).
  {
    rules: {
      "react/jsx-no-target-blank": "error",
    },
  },
  // Design-system Stage 1 (#2816, 113-file hardcoded-color backlog):
  // flag raw Tailwind color utilities so the backlog can't keep growing
  // while it's migrated to the semantic tokens in globals.css (bg-card,
  // text-foreground, text-muted-foreground, border-border, bg-primary,
  // text-warning/text-success/text-destructive, etc.).
  //
  // "warn", not "error" — same gradual-migration pattern as
  // @typescript-eslint/no-explicit-any above. Flipping to "error" is the
  // last step of the migration, once the batches in
  // docs/change-log/2026-08-21-admin-color-token-migration-plan.md land,
  // not the first.
  //
  // Legitimate exceptions (a categorical status-color map like
  // lib/utils.ts's statusColor(), already contrast-verified per-shade in
  // both themes — see its own comment) suppress with
  // `eslint-disable-next-line no-restricted-syntax` and a one-line reason,
  // not by working around the regex.
  //
  // IMPORTANT: this rule alone adds ~1,419 new project-wide warnings
  // (332 baseline -> 1,751) — package.json's `lint` script's
  // `--max-warnings` was bumped from 600 to 1751 (exact current count,
  // same ratchet-not-buffer philosophy as e2e/a11y-baseline.json) to
  // avoid silently breaking CI's admin-test job. Ratchet that number
  // DOWN as each #2816 migration batch lands — never raise it further
  // for an unrelated reason.
  {
    rules: {
      "no-restricted-syntax": [
        "warn",
        {
          selector:
            "Literal[value=/\\b(bg|text|border|ring|fill|stroke|from|to|via)-(red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|gray|grey|slate|zinc|neutral|stone)-[0-9]{2,3}\\b/]",
          message:
            "Raw Tailwind color utility — use a semantic theme token from globals.css instead (bg-card, text-foreground, text-muted-foreground, border-border, bg-primary, text-warning, text-success, text-destructive, ...). If this is a deliberate, contrast-verified exception (e.g. a categorical status-color map), suppress with eslint-disable-next-line and a one-line reason. Tracked: #2816.",
        },
      ],
    },
  },
]);

export default eslintConfig;
