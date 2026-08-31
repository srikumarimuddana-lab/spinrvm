import type { Preview } from '@storybook/nextjs-vite'

// Without this, every shadcn/ui component under src/components/ui renders
// unstyled — Tailwind utility classes need the same global stylesheet the
// Next.js app itself loads (src/app/layout.tsx).
import '../src/app/globals.css'

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
       color: /(background|color)$/i,
       date: /Date$/i,
      },
    },

    a11y: {
      // 'todo' - show a11y violations in the test UI only
      // 'error' - fail CI on a11y violations
      // 'off' - skip a11y checks entirely
      test: 'todo'
    }
  },
};

export default preview;