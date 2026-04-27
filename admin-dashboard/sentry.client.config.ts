/**
 * Sentry client-side configuration (SPR-03/3b).
 * This file is loaded by Next.js automatically when @sentry/nextjs is installed.
 * Set NEXT_PUBLIC_SENTRY_DSN in Vercel / CI environment variables.
 */
import * as Sentry from '@sentry/nextjs';

/** Scrub PII fields from Sentry events before they leave the browser (F-44/PIPEDA). */
function beforeSend(event: Sentry.ErrorEvent): Sentry.ErrorEvent | null {
  // Strip request headers that may carry auth tokens or cookies
  if (event.request?.headers) {
    const safe: Record<string, string> = {};
    const allow = new Set(['content-type', 'accept', 'user-agent']);
    for (const [k, v] of Object.entries(event.request.headers)) {
      safe[k] = allow.has(k.toLowerCase()) ? (v as string) : '[Filtered]';
    }
    event.request.headers = safe;
  }

  // Scrub cookies entirely
  if (event.request?.cookies) {
    event.request.cookies = { _filtered: '[Filtered]' };
  }

  // Remove URL query strings (may contain phone, email, or token params)
  if (event.request?.query_string) {
    event.request.query_string = '[Filtered]';
  }

  // Strip known PII keys from all extra/contexts
  const PII_KEYS = new Set([
    'email', 'phone', 'phone_number', 'address', 'lat', 'lng',
    'latitude', 'longitude', 'token', 'password', 'authorization',
    'full_name', 'first_name', 'last_name',
  ]);

  function scrubObj(obj: Record<string, unknown>): void {
    for (const key of Object.keys(obj)) {
      if (PII_KEYS.has(key.toLowerCase())) {
        obj[key] = '[Filtered]';
      } else if (obj[key] && typeof obj[key] === 'object') {
        scrubObj(obj[key] as Record<string, unknown>);
      }
    }
  }

  if (event.extra) scrubObj(event.extra as Record<string, unknown>);
  if (event.contexts) scrubObj(event.contexts as unknown as Record<string, unknown>);
  if (event.user) {
    // Retain only the non-PII user identifier
    event.user = { id: event.user.id };
  }

  return event;
}

// [22-2] PIPEDA data residency: Sentry has no Canadian region.
// Use the EU-region DSN (sentry.io → Settings → Data Storage → EU) as the
// closest compliant option. EU DSN host pattern: o<org>.ingest.de.sentry.io
Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,

  // Admin is low-traffic (<5k req/day) — 100% sampling catches all perf regressions
  // on a surface where slow loads have high operational cost ([21-2]).
  tracesSampleRate: 1.0,

  // Replays: 10% of sessions, 100% of sessions with errors.
  replaysSessionSampleRate: 0.1,
  replaysOnErrorSampleRate: 1.0,

  environment: process.env.NODE_ENV ?? 'development',

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
