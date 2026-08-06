/**
 * Next.js instrumentation hook — the server-side entry point for Sentry.
 *
 * This file is why Sentry works at all. `sentry.server.config.ts` and
 * `sentry.edge.config.ts` are plain modules: nothing imports them, so on
 * @sentry/nextjs v9+ nothing runs them unless `register()` does. Both config
 * files existed in this repo for some time with no instrumentation hook, so
 * `Sentry.init` never executed and no server error ever reached Sentry
 * regardless of whether the DSN was set.
 *
 * Lives in src/ because this project uses a src directory; Next.js looks for
 * the hook at <root>/instrumentation.ts OR <root>/src/instrumentation.ts, and
 * would not find it at the root here.
 */
import * as Sentry from '@sentry/nextjs';

export async function register() {
  // Node.js runtime — route handlers, server components, server actions.
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    await import('../sentry.server.config');
  }

  // Edge runtime — src/middleware.ts.
  if (process.env.NEXT_RUNTIME === 'edge') {
    await import('../sentry.edge.config');
  }
}

/**
 * Captures errors thrown in server components, route handlers, and server
 * actions. Without this export those errors are logged by Next.js and dropped;
 * `Sentry.init` alone does not intercept them.
 */
export const onRequestError = Sentry.captureRequestError;
