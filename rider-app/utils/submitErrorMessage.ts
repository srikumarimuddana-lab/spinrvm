/**
 * Map a caught API error from the shared client to a user-facing toast message.
 *
 * Kept dependency-free on purpose: it duck-types the client's `RateLimitError`
 * by its `name`/`retryAfterSeconds` fields (contract pinned in
 * shared/api/client.ts:383-410) instead of importing the class, so it stays
 * unit-testable without pulling the fetch/SecureStore client into the Jest
 * sandbox. Any non-rate-limit error falls back to the caller-supplied message
 * rather than leaking raw backend detail to the user.
 */
export function submitErrorMessage(e: unknown, fallback: string): string {
  if (
    e !== null &&
    typeof e === 'object' &&
    (e as { name?: unknown }).name === 'RateLimitError'
  ) {
    const retryAfter = (e as { retryAfterSeconds?: unknown }).retryAfterSeconds;
    if (typeof retryAfter === 'number' && retryAfter > 0) {
      return `Too many requests — please try again in ${retryAfter}s.`;
    }
  }
  return fallback;
}
