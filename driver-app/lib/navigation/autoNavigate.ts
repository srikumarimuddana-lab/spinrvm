/**
 * "Has this ride leg already auto-launched navigation?" — the guard that keeps
 * the automatic hand-off to exactly one launch per leg.
 *
 * An in-memory ref would be enough while the app stays alive, but the auto-nav
 * case is precisely the one where it doesn't: the driver leaves Spinr for Google
 * Maps for the whole drive to pickup, and Android reclaims the backgrounded
 * process. Without a durable marker, the cold start that happens when they
 * reopen Spinr to tap "Arrived" would re-fire the hand-off and bounce them
 * straight back out to Maps — at the exact moment they need the Spinr screen.
 *
 * A route key distinguishes successive intermediate stops in the dropoff leg;
 * without it, a rider's live stop edit would keep the old Maps destination.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

export type NavLeg = 'pickup' | 'dropoff';

const AUTO_NAV_MARKER_KEY = '@spinr_auto_nav_launched';

/** Guards the same-process double-invoke (React re-render or StrictMode) that
 *  would otherwise let two callers both read the marker before either writes. */
let claimedInProcess: string | null = null;

/** Test seam — the module-level guard would otherwise leak between test cases. */
export function _resetAutoNavClaimForTest(): void {
  claimedInProcess = null;
}

/**
 * Claim the auto-launch for one ride leg. Returns true exactly once per leg —
 * the caller should launch navigation only on a true.
 */
export async function claimAutoNavLeg(rideId: string, leg: NavLeg, destinationKey?: string): Promise<boolean> {
  const marker = `${rideId}:${leg}${destinationKey ? `:${destinationKey}` : ''}`;
  if (claimedInProcess === marker) return false;
  claimedInProcess = marker;

  try {
    if ((await AsyncStorage.getItem(AUTO_NAV_MARKER_KEY)) === marker) return false;
    await AsyncStorage.setItem(AUTO_NAV_MARKER_KEY, marker);
  } catch {
    // Storage is unavailable. Fall through and launch: the in-process guard
    // still prevents a repeat this session, and launching once more after a
    // cold start is a better failure than never launching at all.
  }
  return true;
}
