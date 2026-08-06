/**
 * PIPEDA-safe redaction helpers for log/Sentry/analytics emission.
 *
 * CLAUDE.md prohibits raw phone numbers, full emails, full names, raw GPS, and
 * government IDs in logs, Sentry breadcrumbs, audit-log payloads, or analytics
 * calls. Use these helpers at every emission site on every TS surface
 * (rider-app, driver-app, admin-dashboard, shared).
 *
 * These helpers are pure (no I/O, no React Native deps) and intentionally
 * mirror the API of `backend/utils/pii.py` so call-sites read identically
 * across the Python + TypeScript boundary.
 */

/**
 * Mask a phone number to last-4 form (`****1234`).
 *
 * Strips all non-digit characters before extracting the last four digits, so
 * `"+1 (306) 555-1234"`, `"3065551234"`, `"306.555.1234"` all collapse to
 * `"****1234"`. Empty / nullish / shorter-than-4-digit input returns `"****"`.
 */
export function redactPhone(phone: string | null | undefined): string {
  if (!phone) return '****';
  const digits = phone.replace(/\D/g, '');
  if (digits.length < 4) return '****';
  return `****${digits.slice(-4)}`;
}

/**
 * Mask an email's local part (`alice@example.com` -> `a***@example.com`).
 *
 * Empty / nullish / no-`@` input returns `"****"`. An empty local part (e.g.
 * `"@nobody.com"`) returns `"***@nobody.com"`.
 */
export function redactEmail(email: string | null | undefined): string {
  if (!email || !email.includes('@')) return '****';
  const atIndex = email.indexOf('@');
  const local = email.slice(0, atIndex);
  const domain = email.slice(atIndex + 1);
  if (!local) return `***@${domain}`;
  return `${local[0]}***@${domain}`;
}

/**
 * Round GPS coordinates to ~11km precision (1 decimal place) for log-safe
 * geo-context. CLAUDE.md forbids raw lat/lng floats in logs/analytics; even
 * geohashed area is acceptable, full coordinates are not.
 *
 * Returns the form `"52.1,-106.6"`. Non-finite inputs are emitted as `"?"`.
 */
export function redactCoords(lat: number, lng: number): string {
  const fmt = (n: number): string => {
    if (!Number.isFinite(n)) return '?';
    // Round half AWAY FROM ZERO, which a plain Math.round does not do:
    // Math.round rounds half toward +Infinity, so Math.round(-1066.5) is -1066.
    // `Math.round(n * 10) / 10` therefore redacted -106.65 to -106.6 while
    // redacting +106.65 to +106.7 — a sign-dependent rule. Every Saskatchewan
    // longitude is negative, so that asymmetry applied to essentially every
    // coordinate this function exists to redact.
    //
    // Privacy is unaffected either way: one decimal place is ~11km whichever
    // direction the half case breaks. This is about the rule being statable in
    // one sentence and holding for both signs — which is what the test asserted
    // all along, before anything ran it.
    return ((Math.sign(n) * Math.round(Math.abs(n) * 10)) / 10).toFixed(1);
  };
  return `${fmt(lat)},${fmt(lng)}`;
}
