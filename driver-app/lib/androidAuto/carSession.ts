/**
 * The car's own bootstrap — what useDriverDashboard does on mount, without React.
 *
 * ─── Why this has to exist ──────────────────────────────────────────────────
 * Android Auto starts the app's JS CONTEXT, not its phone UI. index.js runs, so
 * registerAutoPlay() and the FCM background handler both execute — but
 * app/_layout.tsx and app/driver/(tabs)/index.tsx never mount. Everything that
 * loads a driver's data lives in that unmounted tree:
 *
 *   authStore.initialize()   called from app/index.tsx and app/_layout.tsx
 *   useDriverDashboard()     mounted only in app/driver/(tabs)/index.tsx
 *
 * So on a car-only launch there was no token, and therefore no active ride, no
 * earnings, no driver config — not because the requests failed, but because
 * nothing ever issued them. That is the whole of "the icons and screen are not
 * loaded properly… not refreshing or storing any data".
 *
 * Launching the phone Activity from the car is NOT the alternative: Android 10+
 * blocks background activity starts, and Play's Car App Quality guidelines
 * forbid an app driving the phone UI from the head unit. A headless bootstrap is
 * how Google Maps does it, and it is what this is.
 *
 * ─── Contract ───────────────────────────────────────────────────────────────
 * Every step is best-effort and independently caught. The map, the car marker
 * and the template buttons need no session, no store and no network beyond map
 * tiles, and they must keep rendering when all of this fails — a signed-out
 * driver should see a working map, just without their data.
 */
import { AppState, type AppStateStatus } from 'react-native';
import { useAuthStore } from '@shared/store/authStore';
import api from '@shared/api/client';
import { useDriverStore } from '../../store/driverStore';
import { consumePendingRideOffer } from '../../services/pendingRideOffer';
import {
  subscribeBackgroundDispatch,
  type BackgroundDispatchEvent,
} from '../../services/backgroundMessaging';
import { pushDebug, setDebugFact } from './carDebug';

const log = (...args: unknown[]) => {
  if (__DEV__) console.log('[car-session]', ...args);
  pushDebug('info', ...args);
};
const logError = (...args: unknown[]) => {
  console.error('[car-session]', ...args);
  pushDebug('error', ...args);
};

/**
 * Backstop refresh while a head unit is connected.
 *
 * Without a WebSocket the car cannot be TOLD that a ride changed underneath it,
 * so it asks. 60s is chosen against what it is actually covering: FCM already
 * delivers the events that matter within seconds (offer, cancellation), and this
 * exists for the cases push misses entirely — a notification permission revoked,
 * a dropped FCM connection, a state change with no push at all. A tighter poll
 * would spend a driver's battery and data on a duplicate of a channel that
 * usually works. The real fix is a headless socket, which is deliberately not
 * part of this change.
 */
const REFRESH_INTERVAL_MS = 60_000;

/** How long to wait for an initialize() already in flight before giving up. */
const AUTH_WAIT_MS = 8_000;

/**
 * Ride states in which a cancellation can legitimately arrive.
 *
 * CLAUDE.md's state machine is explicit that the only transition out of
 * `in_progress` is `completed` — "Never `cancelled` after trip start" — so a
 * cancellation push naming a trip already under way is a contract violation, not
 * an instruction. It is dropped rather than applied, because acting on it would
 * strand a driver mid-trip with an idle car screen.
 */
const CANCELLABLE_STATES = new Set(['ride_offered', 'navigating_to_pickup', 'arrived_at_pickup']);

let refreshTimer: ReturnType<typeof setInterval> | null = null;
let appStateSub: { remove: () => void } | null = null;
let unsubscribeDispatch: (() => void) | null = null;
let started = false;

/**
 * Restore the session from the car, because nothing else will.
 *
 * `initialize()` reads the stored refresh token and rehydrates the session. It
 * is safe to call from here: it performs no navigation, and a transient failure
 * deliberately KEEPS the refresh token and flags the session recoverable rather
 * than logging the driver out (authStore.ts — "Do NOT delete it — the next app
 * launch should retry"). Only a genuine 401, where the server has already
 * revoked the token, clears anything.
 *
 * Resolves to whether a usable token exists afterwards. Unlike the previous
 * fire-and-forget version in register.ts this is AWAITED, because everything
 * below it is a request that would otherwise race the token into existence and
 * 401.
 */
async function ensureSession(): Promise<boolean> {
  try {
    const auth = useAuthStore.getState();
    if (!auth.isInitialized) {
      if (auth.isLoading) {
        // The phone app is starting up concurrently. Joining its initialize is
        // right; starting a second one would race two refresh-token rotations,
        // and that credential is single-use.
        await waitForInitialized();
      } else {
        log('car-only launch — initialising session from the car');
        await auth.initialize?.();
      }
    }
    return !!useAuthStore.getState().token;
  } catch (e) {
    logError('session init from car failed:', e);
    return false;
  }
}

function waitForInitialized(): Promise<void> {
  return new Promise<void>((resolve) => {
    if (useAuthStore.getState().isInitialized) {
      resolve();
      return;
    }
    let unsub: (() => void) | null = null;
    const finish = () => {
      clearTimeout(timer);
      unsub?.();
      resolve();
    };
    // Bounded: an initialize that never settles must not hold the car's
    // bootstrap open forever. On timeout we proceed token-less, which simply
    // means the data-backed extras stay absent this session.
    const timer = setTimeout(finish, AUTH_WAIT_MS);
    unsub = useAuthStore.subscribe((s: { isInitialized?: boolean }) => {
      if (s?.isInitialized) finish();
    });
  });
}

/**
 * Put a dispatch event the headless FCM handler saw in front of the driver.
 *
 * This is the piece that makes offers work on a car-only launch. The handler has
 * always persisted the offer to AsyncStorage, but on that launch nothing reads it
 * back — useDriverDashboard is what does that on the phone, and it never mounts.
 * Writing straight into the shared store means register.ts's existing
 * `useDriverStore.subscribe(apply)` raises the head unit's Accept/Decline alert
 * with no further wiring.
 */
function onBackgroundDispatch(event: BackgroundDispatchEvent): void {
  try {
    const store = useDriverStore.getState();
    if (event.type === 'new_ride_assignment') {
      // setIncomingRide has its own guard against overwriting a non-idle state,
      // so a duplicate (push racing the phone's own hydration) is a no-op.
      store.setIncomingRide(event.offer as never);
      log('offer from background handler →', event.ride_id);
      return;
    }

    // ride_cancelled. Narrow deliberately: only for the ride the car is
    // actually showing, and only from a state a cancellation can reach.
    const activeId = (store.activeRide as { ride?: { id?: string } } | null)?.ride?.id;
    const offeredId = (store.incomingRide as { ride_id?: string } | null)?.ride_id;
    if (event.ride_id !== activeId && event.ride_id !== offeredId) {
      log('ignoring cancellation for a ride this car is not showing');
      return;
    }
    if (!CANCELLABLE_STATES.has(store.rideState)) {
      logError(
        'cancellation push for a ride in state',
        store.rideState,
        '— dropped (in_progress can only go to completed)',
      );
      return;
    }
    store.resetRideState();
    log('ride cancelled from background handler →', event.ride_id);
  } catch (e) {
    logError('background dispatch handling failed:', e);
  }
}

/** The reads a connected car wants, issued together and individually caught. */
async function refreshCarData(reason: string): Promise<void> {
  const store = useDriverStore.getState();
  await Promise.all([
    store.fetchActiveRide().catch((e) => logError('fetchActiveRide failed:', e)),
    // Feeds the earnings pill. Absent is an accepted outcome — the pill hides
    // itself — so this must never be the reason the rest of a refresh fails.
    store.fetchEarnings('today').catch((e) => logError('fetchEarnings failed:', e)),
  ]);
  log('refreshed:', reason);
}

/**
 * Fetch the server's dispatch config so the head unit's offer alert counts down
 * for the real offer window rather than the 15s fallback. The phone gets this
 * from a react-query hook that never runs car-only.
 */
async function loadDriverConfig(): Promise<void> {
  try {
    const res = await api.get('/drivers/config');
    if (res?.data) useDriverStore.getState().applyDriverConfig(res.data);
  } catch (e) {
    // Falls back to FALLBACK_COUNTDOWN in the store — degraded, not broken.
    logError('driver config fetch failed:', e);
  }
}

/**
 * Bring the car session up. Idempotent: a head unit that swaps to the reversing
 * camera and back re-fires didConnect without a disconnect, and that must not
 * stack a second refresh timer or re-run the bootstrap.
 */
export async function startCarSession(): Promise<void> {
  if (started) {
    log('session already started — refreshing instead');
    await refreshCarData('reconnect');
    return;
  }
  started = true;

  // Armed before the awaits below so a bootstrap that stalls on a slow network
  // still leaves the car refreshing on its own afterwards.
  refreshTimer = setInterval(() => {
    refreshCarData('interval').catch(() => {});
  }, REFRESH_INTERVAL_MS);

  // Subscribed before the awaits below: an offer can land during the bootstrap,
  // and with no subscriber it would only reach AsyncStorage.
  unsubscribeDispatch = subscribeBackgroundDispatch(onBackgroundDispatch);

  appStateSub = AppState.addEventListener('change', (next: AppStateStatus) => {
    // The phone app coming to the foreground is the cheapest signal that
    // something may have changed while the car was the only thing running.
    if (next === 'active') refreshCarData('app-active').catch(() => {});
  });

  // A stashed offer is time-critical and needs no token, so it goes first.
  const surfaced = await consumePendingRideOffer();
  if (surfaced) log('surfaced a ride offer stashed by the background handler');

  const authed = await ensureSession();
  setDebugFact('session', authed ? 'authenticated' : 'no token');
  if (!authed) {
    // Signed out, or the refresh token is gone. The map, marker and buttons
    // still work; this is the "logged out is fine, just show the map" case.
    log('no session — car runs map-only');
    return;
  }

  // Order matters and mirrors useDriverDashboard.ts:1552: the cached ride state
  // paints something immediately, then the server correction overrides it.
  await useDriverStore
    .getState()
    .hydrateDriverRideState()
    .catch((e) => logError('hydrateDriverRideState failed:', e));

  await Promise.all([refreshCarData('bootstrap'), loadDriverConfig()]);
}

/** Tear down. Safe to call when nothing was started. */
export function stopCarSession(): void {
  started = false;
  if (refreshTimer !== null) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  try {
    appStateSub?.remove();
  } catch {
    /* already removed */
  }
  appStateSub = null;
  // With no car connected the FCM channel must have no subscriber at all, so the
  // phone's own path is exactly what it was before any of this existed.
  unsubscribeDispatch?.();
  unsubscribeDispatch = null;
}
