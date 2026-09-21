/**
 * Human sentences for the backend's bare `ERR_*` sentinels.
 *
 * Some routes raise `HTTPException(detail="ERR_SESSION_REVOKED")` rather than
 * a sentence. That is deliberate on the backend side: `utils/error_handling.py`
 * only lets a 5xx detail through its sanitiser when it matches the short
 * ALL-CAPS `ERR_*` shape, so the pattern is load-bearing and must not be
 * rewritten into prose there.
 *
 * The client already refuses to render those tokens (`isMachineErrorSentinel`
 * in shared/api/client.ts), which is correct — but with nothing mapping them,
 * a precise reason collapsed into the call site's generic fallback. A rider
 * who double-tapped Tip read "Something went wrong. Please try again."
 * instead of "You've already added a tip for this ride."
 *
 * ── What belongs in this table ──────────────────────────────────────────
 *
 * Only a sentinel that is BOTH:
 *   (a) raised on a route rider-app or driver-app actually calls, and
 *   (b) more useful than the copy its call sites already pass as a fallback.
 *
 * `getApiErrorMessage` consults this table unconditionally, so an entry here
 * OVERRIDES every call site's own fallback and none of them can opt out. That
 * is why the bar is "more useful than the caller's copy", not "accurate".
 *
 * Deliberately absent:
 *   • The OTP sentinels — their screens already pass better copy ("That code
 *     didn't match. Check the SMS and try again.").
 *   • ERR_FORBIDDEN / ERR_FARE_EXCEEDED / ERR_FARE_UNDERPAID — raised on the
 *     wallet-pay path (routes/wallet.py:243/249/251), where the rider never
 *     typed an amount. A generic "That's less than the fare for this ride."
 *     is vaguer and more accusatory than walletStore's own "Wallet payment
 *     failed. Please try again."
 *   • ERR_DATABASE, ERR_AUTH_UNAVAILABLE, ERR_IDLE_TIMEOUT,
 *     ERR_ACCOUNT_INACTIVE — raised ONLY on admin routes (routes/admin/*, and
 *     the `admin_staff` branch of dependencies/__init__.py). This module is
 *     imported by shared/api/client.ts, which opens with
 *     `import { Platform } from 'react-native'`; admin-dashboard is Next.js
 *     with its own client and no central error-message helper, so entries for
 *     them could never render. An admin therefore still sees `ERR_DATABASE`
 *     raw — a real gap, but one that needs an admin-side error layer rather
 *     than a dead row here.
 *
 * Kept dependency-free so the API client can import it without pulling
 * anything else into its module graph.
 */
export const SENTINEL_MESSAGES: Readonly<Record<string, string>> = Object.freeze({
  // Covers both a purged account and one still inside its deletion grace
  // window, because the backend raises the same sentinel for both
  // (`_enforce_account_active`, dependencies/__init__.py). The wording must
  // not assert permanence: a `pending_deletion` user can still self-restore
  // through the reactivate-account screen both apps ship.
  ERR_ACCOUNT_DELETED: 'This account is no longer active. Contact support if you need it restored.',
  ERR_DRIVER_ONLY: 'Only the driver assigned to this ride can do that.',
  ERR_EMAIL_UNVERIFIED: 'Please verify your email address first — check your inbox for the link.',
  ERR_IDENTITY_INELIGIBLE: "This account isn't eligible for that yet. Please contact support.",
  ERR_REACTIVATION_EXPIRED: 'That reactivation link has expired. Please request a new one.',
  ERR_RIDE_NOT_PAYABLE: "This ride can't be paid for right now.",
  ERR_SESSION_REVOKED: "You've been signed out. Please sign in again.",
  ERR_TIP_DUPLICATE: "You've already added a tip for this ride.",
  ERR_TOKEN_AUDIENCE: 'Please sign in again to continue.',
  ERR_TOKEN_REVOKED: "You've been signed out. Please sign in again.",
});

/**
 * The sentence for a sentinel, or `undefined` when the string is not a
 * sentinel we have copy for — in which case the caller keeps its own
 * fallback, exactly as before.
 *
 * The `hasOwnProperty` guard is load-bearing, not defensive noise: a plain
 * object literal inherits from `Object.prototype`, so a bare `[key]` lookup
 * returns a *function* for `"constructor"`, `"toString"`, `"valueOf"` or
 * `"hasOwnProperty"`, and an object for `"__proto__"`. Any of those would be
 * truthy, reach `clampToastMessage`, and throw `message.trim is not a
 * function` out of the one helper whose contract is to always return a safe
 * string. `Object.freeze` does not null the prototype, and the
 * `Record<string, string>` type makes TypeScript believe the result is a
 * string, so nothing else catches this. The `typeof` check is the belt to
 * that braces.
 */
export function messageForSentinel(raw: string | undefined | null): string | undefined {
  if (!raw) return undefined;
  const key = raw.trim();
  if (!Object.prototype.hasOwnProperty.call(SENTINEL_MESSAGES, key)) return undefined;
  const value = SENTINEL_MESSAGES[key];
  return typeof value === 'string' ? value : undefined;
}
