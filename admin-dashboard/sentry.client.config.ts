/**
 * Sentry client-side configuration (SPR-03/3b).
 * This file is loaded by Next.js automatically when @sentry/nextjs is installed.
 * Set NEXT_PUBLIC_SENTRY_DSN in Vercel / CI environment variables.
 */
import * as Sentry from '@sentry/nextjs';

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,

  // Capture 20% of transactions for performance monitoring.
  // Raise to 1.0 in staging; lower to 0.05 in production if volume is high.
  tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.2 : 1.0,

  // Replays: 10% of sessions, 100% of sessions with errors.
  replaysSessionSampleRate: 0.1,
  replaysOnErrorSampleRate: 1.0,

  environment: process.env.NODE_ENV ?? 'development',

  // Only initialize when a DSN is present — keeps local dev clean.
  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,

  integrations: [
    Sentry.replayIntegration({
      // Mask PII in session replays.
      maskAllText: true,
      blockAllMedia: false,
    }),
  ],
});
