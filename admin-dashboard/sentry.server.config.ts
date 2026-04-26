/**
 * Sentry server-side configuration (SPR-03/3b).
 * Loaded by Next.js for Node.js runtime and Edge runtime.
 */
import * as Sentry from '@sentry/nextjs';

/** Scrub PII fields from Sentry events before they leave the server (F-44/PIPEDA). */
function beforeSend(event: Sentry.ErrorEvent): Sentry.ErrorEvent | null {
  if (event.request?.headers) {
    const safe: Record<string, string> = {};
    const allow = new Set(['content-type', 'accept', 'user-agent']);
    for (const [k, v] of Object.entries(event.request.headers)) {
      safe[k] = allow.has(k.toLowerCase()) ? (v as string) : '[Filtered]';
    }
    event.request.headers = safe;
  }
  if (event.request?.cookies) event.request.cookies = '[Filtered]';
  if (event.request?.query_string) event.request.query_string = '[Filtered]';

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
  if (event.user) event.user = { id: event.user.id };

  return event;
}

Sentry.init({
  dsn: process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN,
  tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.2 : 1.0,
  environment: process.env.NODE_ENV ?? 'development',
  enabled: !!(process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN),
  beforeSend,
});
