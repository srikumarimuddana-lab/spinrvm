/**
 * Sentry Edge-runtime configuration.
 *
 * Loaded by src/instrumentation.ts when NEXT_RUNTIME === 'edge'. This covers
 * src/middleware.ts, which is where admin auth, the IP allowlist, the CSP nonce,
 * and the tracking-host router all run — a throw there fails the request before
 * any page renders, so it is the one runtime we least want reporting blind.
 *
 * No replay/browser integrations here: the edge runtime has no DOM.
 */
import * as Sentry from '@sentry/nextjs';
import { scrubEvent as beforeSend } from './sentry.scrub';

// Region note: see sentry.server.config.ts. The cold-start region check lives
// there only — it would run on every middleware invocation if duplicated here.
Sentry.init({
  dsn: process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN,
  tracesSampleRate: 1.0,
  environment: process.env.NODE_ENV ?? 'development',
  release: process.env.VERCEL_GIT_COMMIT_SHA || process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA,
  enabled: !!(process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN),
  beforeSend,
  // Required tags per CLAUDE.md observability conventions ([21-4]).
  initialScope: {
    tags: {
      surface: 'admin',
      env: process.env.NODE_ENV ?? 'development',
    },
  },
});

Sentry.setTag('surface', 'admin');
