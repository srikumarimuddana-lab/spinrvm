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
  // `Math.round` rounds .5 ties toward +Infinity, not away from zero — for a
  // negative half-integer tie (e.g. -106.65 * 10 === -1066.5) that rounds
  // -106.65 to -106.6 instead of the expected -106.7. Round the magnitude
  // and reapply the sign so ties round away from zero on both sides.
  const fmt = (n: number): string =>
    Number.isFinite(n)
      ? (Math.sign(n) * (Math.round(Math.abs(n) * 10) / 10)).toFixed(1)
      : '?';
  return `${fmt(lat)},${fmt(lng)}`;
}
