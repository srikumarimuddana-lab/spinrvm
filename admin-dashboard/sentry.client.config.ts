/**
 * Sentry client-side configuration (SPR-03/3b).
 * This file is loaded by Next.js automatically when @sentry/nextjs is installed.
 * Set NEXT_PUBLIC_SENTRY_DSN in Vercel / CI environment variables.
 */
import * as Sentry from '@sentry/nextjs';
import { scrubEvent as beforeSend } from './sentry.scrub';

// [22-2] PIPEDA data residency: Sentry has no Canadian region. EU
// (o<org>.ingest.de.sentry.io) is the closest compliant option and remains the
// target. Spinr currently ingests to the US region as an accepted risk — see
// sentry.server.config.ts and docs/change-log/2026-08-05-sentry-us-region-accepted.md.
Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,

  // Admin is low-traffic (<5k req/day) — 100% sampling catches all perf regressions
  // on a surface where slow loads have high operational cost ([21-2]).
  tracesSampleRate: 1.0,

  // Replays: 10% of sessions, 100% of sessions with errors.
  replaysSessionSampleRate: 0.1,
  replaysOnErrorSampleRate: 1.0,

  environment: process.env.NODE_ENV ?? 'development',

  // Release tag enables regression-tracking per deploy (Vercel injects the var).
  release: process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA,

  // Only initialize when a DSN is present — keeps local dev clean.
  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,

  beforeSend,

  // Required tags per CLAUDE.md observability conventions ([21-4]).
  initialScope: {
    tags: {
      surface: 'admin',
      env: process.env.NODE_ENV ?? 'development',
    },
  },

  integrations: [
    Sentry.replayIntegration({
      // Admin views render driver licences, payout amounts, and Stripe IDs;
      // block all media so document-review images never appear in replays ([21-1]).
      maskAllText: true,
      blockAllMedia: true,
      mask: ['[data-pii]', 'input'],
    }),
  ],
});

// Tag every event with the surface so cross-surface dashboards stay clean.
Sentry.setTag('surface', 'admin');
