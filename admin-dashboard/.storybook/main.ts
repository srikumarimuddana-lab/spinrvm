import type { StorybookConfig } from '@storybook/nextjs-vite';

// Deliberately minimal: no @chromatic-com/storybook (a paid third-party
// visual-review service — not something to wire in silently), no
// @storybook/addon-vitest / @vitest/browser-playwright browser-mode test
// runner (would require a real Playwright browser download in every CI run
// and a much larger retrofit of vitest.config.ts — a separate decision from
// "give admin-dashboard a component workshop"), no @storybook/addon-mcp.
// Add any of those back deliberately, not because `storybook init` defaults
// to them.
const config: StorybookConfig = {
  stories: ['../src/**/*.stories.@(js|jsx|ts|tsx)', '../src/**/*.mdx'],
  addons: ['@storybook/addon-a11y', '@storybook/addon-docs'],
  framework: '@storybook/nextjs-vite',
  staticDirs: ['../public'],
};

export default config;
