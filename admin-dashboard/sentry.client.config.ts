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
    event.request.cookies = {};
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

  beforeSend,

  integrations: [
    Sentry.replayIntegration({
      // Mask PII in session replays.
      maskAllText: true,
      blockAllMedia: false,
    }),
  ],
});
