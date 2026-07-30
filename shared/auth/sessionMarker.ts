/**
 * Key for the device-local "this session has ended" marker.
 *
 * Deliberately a leaf module with no imports: it is read from the driver app's
 * headless background-location task, which must not pull zustand, the API
 * client, or Firebase into its bundle just to learn whether anyone is signed in.
 *
 * ## Why a marker rather than "are the tokens gone?"
 *
 * The headless location task and geofence handler have no access to the auth
 * store, so they need session state from disk. Inferring it from *missing*
 * tokens is unsafe in both directions:
 *
 * - **False "signed in".** `clearAuthStorage()` — the no-valid-token path in
 *   `initialize()` — does not delete every token key, and it never runs the
 *   logout callbacks. A session that ended by expiry rather than by an explicit
 *   sign-out would still look live.
 * - **False "signed out".** `storage.setItem` swallows SecureStore write
 *   failures, and `getItemAsync` throws on a locked Android keystore. A
 *   legitimately signed-in driver would read as signed out, and the task would
 *   drop trip samples and stop itself mid-trip — losing billed distance and
 *   leaving a hole in the SGI per-insurance-period audit trail.
 *
 * So the client uses the same positive-evidence rule as the backend's
 * session-revocation tombstone (`backend/utils/session_revocation.py`): record
 * an explicit fact when a session ends, and act only on that fact. Every
 * ambiguous state — no marker, unreadable store — resolves to "keep tracking",
 * which is the safe direction for a driver who is still working.
 *
 * Written by `authStore.logout()` and by `clearAuthStorage()`; cleared by
 * `setTokens()` on a new sign-in.
 */
export const SESSION_ENDED_KEY = 'spinr_session_ended';
