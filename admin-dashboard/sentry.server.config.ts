/**
 * Sentry server-side configuration (SPR-03/3b).
 * Loaded by Next.js for Node.js runtime and Edge runtime.
 */
import * as Sentry from '@sentry/nextjs';

// PIPEDA A-PE-P2-5: warn at cold-start if DSN is routing to US ingestion.
// EU hosts contain ingest.de.sentry.io; US hosts contain ingest.sentry.io only.
// Error appears in Vercel Function logs; does not throw (Sentry still initialises).
function _checkSentryRegion(dsn: string | undefined): void {
  if (!dsn || process.env.NODE_ENV !== 'production') return;
  if (!dsn.includes('ingest.de.sentry.io')) {
    console.error(
      '[spinr-admin][PIPEDA] Sentry DSN routes to US ingestion. ' +
      'Switch to EU (https://o<id>.ingest.de.sentry.io/...) or file a DPA addendum. ' +
      'See docs/vendor-register.md § Sentry and A-PE-P2-5 in the remediation plan.'
    );
  }
}
_checkSentryRegion(process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN);

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
  if (event.request?.cookies) event.request.cookies = { _filtered: '[Filtered]' };
  if (event.request?.query_string) event.request.query_string = '[Filtered]';
  if (event.request?.url) {
    event.request.url = event.request.url.replace(/\/[a-f0-9-]{8,}/gi, '/[id]');
  }

  const PII_KEYS = new Set([
    'email', 'phone', 'phone_number', 'address', 'lat', 'lng',
    'latitude', 'longitude', 'token', 'password', 'authorization',
    'full_name', 'first_name', 'last_name',
    'driver_id', 'rider_id', 'ride_id',
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

// [22-2] PIPEDA data residency: Sentry has no Canadian region.
// Use the EU-region DSN (created at sentry.io → Settings → Data Storage → EU)
// as the closest compliant option until Sentry launches a CA region.
// EU DSN host pattern: o<org>.ingest.de.sentry.io
Sentry.init({
  dsn: process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN,
  // Admin is low-traffic — 100% sampling catches all perf regressions ([21-2]).
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
