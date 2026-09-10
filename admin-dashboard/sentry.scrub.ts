/**
 * Shared Sentry PII scrubber (F-44/PIPEDA).
 *
 * Extracted verbatim from sentry.client.config.ts / sentry.server.config.ts,
 * which carried byte-identical copies. The edge runtime needs the same logic,
 * and three hand-maintained copies of a privacy control is how one of them
 * quietly stops matching the other two. One copy, imported by all three.
 *
 * This runs as `beforeSend`, i.e. on the last line before an event leaves the
 * process. Anything not scrubbed here is what Sentry stores.
 */
import type { Breadcrumb, ErrorEvent } from '@sentry/nextjs';

/** Request headers safe to retain; everything else may carry auth or cookies. */
const HEADER_ALLOWLIST = new Set(['content-type', 'accept', 'user-agent']);

/** Keys whose values are dropped wherever they appear in extra/contexts. */
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

/** Strip PII from an event before it leaves the process. */
export function scrubEvent(event: ErrorEvent): ErrorEvent | null {
  // Strip request headers that may carry auth tokens or cookies
  if (event.request?.headers) {
    const safe: Record<string, string> = {};
    for (const [k, v] of Object.entries(event.request.headers)) {
      safe[k] = HEADER_ALLOWLIST.has(k.toLowerCase()) ? (v as string) : '[Filtered]';
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

  // Scrub dynamic entity IDs from URL paths (PIPEDA — A-PE-P1-3).
  // e.g. /dashboard/drivers/abc123ef → /dashboard/drivers/[id]
  if (event.request?.url) {
    event.request.url = event.request.url.replace(/\/[a-f0-9-]{8,}/gi, '/[id]');
  }

  if (event.extra) scrubObj(event.extra as Record<string, unknown>);
  if (event.contexts) scrubObj(event.contexts as unknown as Record<string, unknown>);
  if (event.user) {
    // Retain only the non-PII user identifier
    event.user = { id: event.user.id };
  }

  return event;
}

/**
 * Strip PII from a breadcrumb before it's attached to an event (W7,
 * 2026-09-10 RBAC/security audit). `beforeSend` above only scrubs the
 * top-level request/extra/contexts/user — it never touches
 * `event.breadcrumbs`. Sentry's default integrations (console, fetch, xhr,
 * dom/click) are all active here (`integrations: [...]` in the 3 config
 * files that import this adds to, not replaces, the defaults — none of
 * them sets `defaultIntegrations: false`), and can attach a full request
 * URL (with query string) or a console.log call's raw arguments to a
 * breadcrumb with no redaction layer of their own.
 */
export function scrubBreadcrumb(breadcrumb: Breadcrumb): Breadcrumb | null {
  if (breadcrumb.data) {
    if (typeof breadcrumb.data.url === 'string') {
      // Query strings can carry phone/email/token params, same reasoning
      // as event.request.query_string above.
      breadcrumb.data.url = breadcrumb.data.url.split('?')[0];
    }
    scrubObj(breadcrumb.data as Record<string, unknown>);
  }
  // A console breadcrumb's message is whatever arguments the code happened
  // to log — arbitrary, untrusted content by construction (unlike a
  // navigation/click/fetch breadcrumb's message, which is a short SDK-
  // generated description, not app data). Treat it the same as a raw log
  // line under CLAUDE.md's "never log PII" rule: drop rather than
  // pattern-match, since we can't know what a given console.log carried.
  if (breadcrumb.category === 'console' && typeof breadcrumb.message === 'string') {
    breadcrumb.message = '[Filtered]';
  }
  return breadcrumb;
}
