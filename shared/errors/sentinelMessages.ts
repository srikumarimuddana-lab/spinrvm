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
 * Only sentinels whose reason is *more* useful than the caller's own fallback
 * belong here. The OTP sentinels are deliberately absent: their call sites
 * already pass screen-specific copy ("That code didn't match. Check the SMS
 * and try again."), which beats anything generic this table could offer.
 *
 * Kept dependency-free so the API client can import it without pulling
 * anything else into its module graph.
 */
export const SENTINEL_MESSAGES: Readonly<Record<string, string>> = Object.freeze({
  ERR_ACCOUNT_DELETED: 'This account has been deleted. Contact support if you need it restored.',
  ERR_ACCOUNT_INACTIVE: "This account isn't active right now. Please contact support.",
  ERR_AUTH_UNAVAILABLE: "We can't sign you in right now. Please try again in a moment.",
  ERR_DATABASE: "We couldn't load that right now. Please try again in a moment.",
  ERR_DRIVER_ONLY: 'Only the driver assigned to this ride can do that.',
  ERR_EMAIL_UNVERIFIED: 'Please verify your email address first — check your inbox for the link.',
  ERR_FARE_EXCEEDED: "That's more than the fare for this ride.",
  ERR_FARE_UNDERPAID: "That's less than the fare for this ride.",
  ERR_FORBIDDEN: "You don't have access to that.",
  ERR_IDENTITY_INELIGIBLE: "This account isn't eligible for that yet. Please contact support.",
  ERR_IDLE_TIMEOUT: 'You were signed out after a while without activity. Please sign in again.',
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
 */
export function messageForSentinel(raw: string | undefined | null): string | undefined {
  if (!raw) return undefined;
  return SENTINEL_MESSAGES[raw.trim()];
}
