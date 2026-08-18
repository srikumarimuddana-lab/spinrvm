import { useState, useEffect, useRef, useCallback } from 'react';
import { Animated , Platform, Vibration, Linking, AppState , Dimensions } from 'react-native';
import { showAlert } from '../components/AlertDialog';
import * as Location from 'expo-location';
import NetInfo from '@react-native-community/netinfo';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { showToast } from './useToast';
import { router } from 'expo-router';

import { useAuthStore } from '@shared/store/authStore';
import { useDriverStore } from '../store/driverStore';
import { useAlertPrefsStore } from '../store/alertPrefsStore';
import { useRideOfferSound, setOfferSoundUrl } from './useRideOfferSound';
import { tKey } from '../i18n';
import api, { getApiErrorMessage, ensureFreshToken } from '@shared/api/client';
import { useDriverConfig } from '@shared/hooks/queries';
import { API_URL } from '@shared/config';
// Keep the default import: many test files jest.mock(
// '@shared/config/spinr.config', () => ({ default: {...} })) without a
// matching named 'SpinrConfig' export, so switching to a named import
// breaks those mocks (confirmed in rider-app's utils/aiChat.ts).
// eslint-disable-next-line import/no-named-as-default
import SpinrConfig from '@shared/config/spinr.config';
import { onForegroundMessage } from '@shared/services/firebase';
import {
  startBackgroundLocation,
  stopBackgroundLocation,
  startGeofenceRecovery,
  stopGeofenceRecovery,
  setBackgroundTripActive,
  updateBackgroundLocationCadence,
  recoverTripLocation,
  TRIP_CADENCE,
  IDLE_CADENCE,
} from '../utils/backgroundLocation';
import { consumePendingRideOffer } from '../services/pendingRideOffer';
import { checkLocationIntegrity, resetLocationIntegrity } from '../utils/locationIntegrity';
import { startSensorMonitoring, stopSensorMonitoring, checkMovementConsistency } from '../utils/sensorIntegrity';
import { attestDeviceIntegrity } from '../utils/deviceIntegrity';
import {
  tripLocationRecorder,
  type TripLocationBatchAck,
  type TripLocationBatchRequest,
} from '../utils/tripLocationRecorder';
import { captureException } from '@shared/services/errorReporting';

export type ConnectionState = 'connected' | 'reconnecting' | 'disconnected';

const { height } = Dimensions.get('window');
// Each tier doubles; last-tier jitter must be large enough to disperse a
// thundering herd (many drivers reconnecting simultaneously after a server
// hiccup). ±500ms at 30 s base is only 1.7% dispersal — too tight.
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000];
const MAX_RECONNECT_ATTEMPTS = 10;
// If a socket opens but the auth handshake never completes (no auth_success),
// force-reconnect after this window instead of waiting out the server's ~30s
// auth timeout, which otherwise leaves the driver stuck on "Reconnecting…".
const AUTH_WATCHDOG_MS = 10000;

const LOCATION_CONFIGS: Record<string, { timeInterval: number; distanceInterval: number; accuracy: Location.Accuracy }> = {
  idle:                  { timeInterval: 10_000, distanceInterval: 30, accuracy: Location.Accuracy.Balanced },
  ride_offered:          { timeInterval: 10_000, distanceInterval: 30, accuracy: Location.Accuracy.Balanced },
  navigating_to_pickup:  { timeInterval: 4_000,  distanceInterval: 10, accuracy: Location.Accuracy.High },
  arrived_at_pickup:     { timeInterval: 8_000,  distanceInterval: 20, accuracy: Location.Accuracy.Balanced },
  trip_in_progress:      { timeInterval: 3_000,  distanceInterval: 8,  accuracy: Location.Accuracy.High },
  trip_completed:        { timeInterval: 10_000, distanceInterval: 30, accuracy: Location.Accuracy.Balanced },
};

// Ride states in which the durable route recorder is active.
const TRACKED_TRIP_PHASES = ['navigating_to_pickup', 'arrived_at_pickup', 'trip_in_progress'];

/**
 * Coerce a value into a finite latitude/longitude or return null.
 *
 * Used to validate the four coords on a ride offer arriving via WS
 * (numbers) or FCM (strings — FCM data values are always string-typed).
 * Reject empty strings, NaN, ±Infinity, and out-of-range values so the
 * offer panel never plots at (0,0) or off the map.
 */
const _toFiniteCoord = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? parseFloat(v) : NaN;
  if (!Number.isFinite(n)) return null;
  if (n < -180 || n > 180) return null; // valid range for both lat (-90..90) and lng (-180..180)
  return n;
};

/**
 * Resolve a backend base URL that is guaranteed to carry an http(s) scheme,
 * or null if none can be found.
 *
 * `@shared/config`'s API_URL is env-var-only and resolves to '' when
 * EXPO_PUBLIC_BACKEND_URL was not baked into the build (a known EAS / OTA
 * hazard — the var is compiled in at build time, not read at runtime). An
 * empty base produced a scheme-less WebSocket URL like "/ws/driver/123",
 * which crashed the native OkHttp layer with a FATAL EXCEPTION:
 * "Expected URL scheme 'http' or 'https' but no scheme was found".
 *
 * Defence: prefer API_URL when it has a valid scheme, otherwise fall back to
 * SpinrConfig.backendUrl (which carries the hardcoded production safety net
 * + HTTPS enforcement). Only ever return a value that starts with http(s);
 * callers treat null as "cannot connect" and surface it instead of crashing.
 */
const _resolveBackendBaseUrl = (): string | null => {
  const candidate = API_URL && /^https?:\/\//.test(API_URL) ? API_URL : SpinrConfig.backendUrl;
  if (!candidate || !/^https?:\/\//.test(candidate)) return null;
  return candidate.replace(/\/+$/, '');
};

export type LocationStatus = 'pending' | 'denied' | 'unavailable' | 'ok';

interface UseDriverDashboardReturn {
  // State
  isOnline: boolean;
  connectionState: ConnectionState;
  location: Location.LocationObject | null;
  locationStatus: LocationStatus;
  otpInput: string;
  setOtpInput: (value: string) => void;
  wsError: string | null;
  wsLatency: number | null;

  // Actions
  toggleOnline: () => Promise<void>;
  openNavigation: (lat: number, lng: number, label: string) => void;
  uploadLocationBatch: () => Promise<void>;
  refreshLocation: (useCache: boolean) => Promise<Location.LocationObject | null>;

  // Refs for external use
  mapRef: React.RefObject<any>;
  currentRegionRef: React.RefObject<{ latitudeDelta: number; longitudeDelta: number }>;
  wsRef: React.RefObject<WebSocket | null>;
  locationSubRef: React.RefObject<Location.LocationSubscription | null>;
  countdownRef: React.RefObject<ReturnType<typeof setInterval> | null>;
  reconnectTimeoutRef: React.RefObject<ReturnType<typeof setTimeout> | null>;
  reconnectAttemptRef: React.RefObject<number>;

  // Animations
  pulseAnim: any;
  slideUpAnim: any;
  fadeAnim: any;
}

// Lazy-load Notifee helpers; mirrors the gating in _layout.tsx so the
// dashboard still mounts in Expo Go / web (where the native module is absent).
let _dismissRideOfferNotification: (() => Promise<void>) | null = null;
let _displayRideOfferNotification: ((o: any, opts?: { silent?: boolean; muted?: boolean }) => Promise<void>) | null = null;
if (Platform.OS === 'android' || Platform.OS === 'ios') {
  try {
    // Guarded native-module require — notifeeService.ts statically imports
    // notifee at its own module scope, so requiring it eagerly here would
    // defeat this android/ios + try/catch guard (see _layout.tsx's matching
    // comment on the same pattern).
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const _notifee = require('../services/notifeeService');
    _dismissRideOfferNotification = _notifee.dismissRideOfferNotification;
    _displayRideOfferNotification = _notifee.displayRideOfferNotification;
  } catch {
    _dismissRideOfferNotification = null;
    _displayRideOfferNotification = null;
  }
}

// Surface the heads-up / lock-screen Notifee card for every incoming offer.
// Even when the app is foreground-active, drivers expect the OS alert to appear
// at the same moment as the in-app offer panel. The FCM background handler in
// _layout.tsx covers the fully-killed case; Notifee dedupes by notification id,
// so calling this too never yields a duplicate.
// Dismissal is handled by the rideState effect inside the hook.
function _surfaceOfferNotification(data: any): void {
  const display = _displayRideOfferNotification;
  if (!display) return;
  const _num = (v: unknown): number | undefined => {
    if (v === null || v === undefined || v === '' || v === 'None') return undefined;
    const n = typeof v === 'number' ? v : parseFloat(String(v));
    return Number.isFinite(n) ? n : undefined;
  };
  // Silent when the app is foreground-active: the in-app offer panel is
  // visible and useRideOfferSound is already looping the tone, so a channel
  // sound here would double-ring. Backgrounded-but-alive (WS still connected)
  // keeps the audible heads-up since the in-app loop can't be heard.
  const silent = AppState.currentState === 'active';
  // Settings → Sound & Haptics → Sound Effects: suppress the channel/APNs
  // sound (audio only — the card and full-screen wake still fire).
  const muted = !useAlertPrefsStore.getState().soundEffects;
  display({
    ride_id: data.ride_id,
    booking_id: data.booking_id || data.ride_id,
    pickup_address: data.pickup_address,
    dropoff_address: data.dropoff_address,
    fare: _num(data.fare) ?? 0,
    total_bonus: _num(data.total_bonus),
    distance_km: _num(data.distance_km),
    duration_minutes: _num(data.duration_minutes),
    surge_multiplier: _num(data.surge_multiplier),
    rider_name: data.rider_name || undefined,
    rider_rating: _num(data.rider_rating),
    countdown_seconds: _num(data.countdown_seconds),
    offer_expires_at: data.offer_expires_at || undefined,
    offer_card_url: data.offer_card_url || undefined,
  }, { silent, muted }).catch((e: any) => console.warn('[Offer] Notifee surface failed:', e));
}
const PENDING_ACTION_KEY = 'spinr_pending_notifee_action';

export const useDriverDashboard = (): UseDriverDashboardReturn => {
  const { user, driver: driverData, updateDriverStatus, refreshProfile } = useAuthStore();
  const {
    rideState,
    incomingRide,
    activeRide,
    setIncomingRide,
    resetRideState,
    fetchActiveRide,
    hydrateDriverRideState,
    fetchEarnings,
    applyDriverConfig,
    acceptRide: storeAcceptRide,
    declineRide: storeDeclineRide,
  } = useDriverStore();

  // Audio cue for ride offers — recurring tone every ~2.5s until
  // the offer is accepted, declined, or expired (see effect below).
  const offerSound = useRideOfferSound();

  // Consume any Accept/Decline action the user tapped from the Notifee
  // notification while the app was killed/backgrounded. The background
  // handler stashed it in AsyncStorage; we replay it so the backend call
  // actually fires.
  const consumePendingAction = useCallback(async () => {
    try {
      const raw = await AsyncStorage.getItem(PENDING_ACTION_KEY);
      if (!raw) return;
      // Remove first so concurrent runs (mount + resume firing together)
      // can't double-process the same action.
      await AsyncStorage.removeItem(PENDING_ACTION_KEY);
      const { action, ride_id, ts } = JSON.parse(raw) as {
        action: 'accept' | 'decline' | 'tap';
        ride_id: string;
        ts: number;
      };
      // Stale guard — Notifee actions older than 60s are likely from a
      // ride already resolved on another channel (WS / offer expiry).
      if (Date.now() - ts > 60_000) return;
      if (action === 'accept') {
        await storeAcceptRide(ride_id);
      } else if (action === 'decline') {
        await storeDeclineRide(ride_id);
      }
    } catch (e) {
      console.warn('[Notifee] pending action consume failed:', e);
    }
  }, [storeAcceptRide, storeDeclineRide]);

  // Run on mount AND on every foreground resume. The Accept action sets
  // launchActivity, which brings an already-mounted activity back to the
  // foreground WITHOUT remounting this hook — so a mount-only check would
  // never fire on the common background→resume path and the accepted offer
  // would time out as missed.
  useEffect(() => {
    consumePendingAction();
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') consumePendingAction();
    });
    return () => sub.remove();
  }, [consumePendingAction]);

  // Dismiss the Notifee ride-offer notification once we're no longer in
  // the offer state (accepted, declined, expired, or cancelled). Without
  // this the heads-up banner / lock-screen card would linger after the
  // panel is gone.
  useEffect(() => {
    if (rideState !== 'ride_offered' && _dismissRideOfferNotification) {
      _dismissRideOfferNotification().catch(() => undefined);
    }
  }, [rideState]);

  // State
  const [isOnline, setIsOnline] = useState(driverData?.is_online || false);
  // Guards the reconcile effect below from fighting toggleOnline's optimistic
  // update while its /status request is in flight (driverData still reads the
  // pre-toggle value during that window).
  const isTogglingRef = useRef(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const [location, setLocation] = useState<Location.LocationObject | null>(null);
  // Why a separate status: `location` stays null when permission is denied or
  // the GPS fix fails, and the dashboard used to spin on "Getting your
  // location..." forever with no way to tell those cases apart.
  const [locationStatus, setLocationStatus] = useState<LocationStatus>('pending');
  const [otpInput, setOtpInput] = useState('');

  // Clear OTP input when ride state changes away from arrived_at_pickup
  // (e.g. trip started, ride cancelled, new ride begins). This prevents
  // stale digits from the previous ride lingering in the OTP boxes.
  const prevRideStateRef = useRef(rideState);
  useEffect(() => {
    const prev = prevRideStateRef.current;
    prevRideStateRef.current = rideState;
    if (prev === 'arrived_at_pickup' && rideState !== 'arrived_at_pickup') {
      setOtpInput('');
    }
    if (rideState === 'navigating_to_pickup' && prev !== 'navigating_to_pickup') {
      setOtpInput('');
    }
  }, [rideState]);
  const [wsError, setWsError] = useState<string | null>(null);
  const [wsLatency, setWsLatency] = useState<number | null>(null);

  // Refs
  const mapRef = useRef<any>(null);
  const currentRegionRef = useRef({ latitudeDelta: 0.04, longitudeDelta: 0.04 });
  const wsRef = useRef<WebSocket | null>(null);
  const locationSubRef = useRef<Location.LocationSubscription | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Auth watchdog: a socket can open but never complete the auth handshake
  // (lost first message, half-open TLS through a flaky proxy). The server only
  // times that out after ~30s; this self-heals far faster.
  const authWatchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastServerMsgRef = useRef<number>(Date.now());
  const pongSentAtRef = useRef<number>(0);
  const lastSeqRef = useRef<number>(0);
  // Refs used inside WebSocket callbacks to avoid stale closure values.
  // Also used to stabilize the connectWebSocket useCallback — if we closed
  // over `user` / `handleWSMessage` directly, any store state change would
  // recreate the callback, re-fire the mount effect, and tear down the
  // socket (observed as a perpetual "Reconnecting…" banner).
  const isOnlineRef = useRef(isOnline);
  const locationRef = useRef<Location.LocationObject | null>(null);
  // Throttle setLocation re-renders: map updates at most every 10 s.
  // WS payloads still fire every watchPositionAsync callback (~5 s).
  const lastRenderMsRef = useRef<number>(0);
  // Phase 1 (online, no ride): throttle durable idle breadcrumbs so we persist
  // ~1 location/minute for driver history without filling the trail with the
  // dense live-marker cadence. Reset when a trip starts / driver goes offline.
  const lastIdleDurableMsRef = useRef<number>(0);
  // Tracks whether the REST /drivers/location-batch endpoint is healthy.
  // When false, the WS message is sent durable:true with v1-only fields so
  // the WS handler persists via the legacy path (simple INSERT, no unique
  // index required). When true, WS is ephemeral and only REST persists.
  const batchUploadHealthyRef = useRef(true);
  const userRef = useRef(user);
  useEffect(() => { userRef.current = user; }, [user]);

  // Animations
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const slideUpAnim = useRef(new Animated.Value(height)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // Keep refs in sync so WebSocket callbacks always see current values
  useEffect(() => { isOnlineRef.current = isOnline; }, [isOnline]);

  // ─── Animate ActiveRidePanel in/out based on rideState ──────────
  // Track whether the first active-ride state transition has already
  // happened. On mount, if the store already has an active ride (hydrated
  // or fetched from the API before this effect fires), we snap the panel
  // to its final position instead of animating from off-screen — otherwise
  // the driver sees a blank map for ~500ms while the panel slides up.
  const hasDoneInitialActiveAnim = useRef(false);

  useEffect(() => {
    const isActive = rideState === 'navigating_to_pickup' ||
                     rideState === 'arrived_at_pickup' ||
                     rideState === 'trip_in_progress';
    if (isActive) {
      if (!hasDoneInitialActiveAnim.current) {
        // First time the panel becomes active — snap into view immediately
        // so there's no blank-map window when state is restored on cold start.
        hasDoneInitialActiveAnim.current = true;
        slideUpAnim.setValue(0);
        fadeAnim.setValue(1);
      } else {
        // Subsequent transitions (user accepted a new ride) — animate in.
        Animated.parallel([
          Animated.spring(slideUpAnim, {
            toValue: 0,
            useNativeDriver: true,
            friction: 8,
          }),
          Animated.timing(fadeAnim, {
            toValue: 1,
            duration: 300,
            useNativeDriver: true,
          }),
        ]).start();
      }
    } else {
      // Reset to hidden when not in an active ride state
      hasDoneInitialActiveAnim.current = false;
      slideUpAnim.setValue(height);
      fadeAnim.setValue(0);
    }
    // slideUpAnim/fadeAnim intentionally excluded — the same verified-safe
    // stable useRef(...).current animation-driver idiom used throughout
    // this app (created once at line 344-346, never reassigned). Listing a
    // ref-derived value in a dependency array is itself a render-time ref
    // read under react-hooks/refs (confirmed this round in otp.tsx's
    // dotAnims — adding it there was tested and traded one violation for
    // another). Their identity never changes, so excluding them doesn't
    // change when this ActiveRidePanel show/hide animation fires — the only
    // real trigger, rideState, is unchanged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rideState]);

  // ─── Refresh user + driver profile on mount and on foreground ────
  // The Zustand store only pulls /auth/me on app init, so admin-side
  // changes (e.g. flipping is_verified or the derived onboarding status)
  // never reach a long-running session. That stale state silently
  // disables the GO button because canGoOnline depends on
  // driver_onboarding_status === 'verified' AND driver.is_verified.
  useEffect(() => {
    refreshProfile();
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') refreshProfile();
    });
    return () => sub.remove();
  }, [refreshProfile]);

  // ─── Driver operational config — TanStack Query owned ────────────
  // `useDriverConfig` is a long-staleTime hook: server-tuned values for
  // the ride-offer countdown and pickup-geofence radius. Persisted cache
  // means a cold start applies the last-known config without a network
  // round-trip. When a fresh value lands we mirror it into the driver
  // store so existing consumers keep reading from the same place.
  // On error the store retains its module-level fallbacks (15s countdown,
  // 100m pickup radius) so the flow never breaks on a transient hiccup.
  const driverConfigQuery = useDriverConfig();
  useEffect(() => {
    if (!driverConfigQuery.data) return;
    applyDriverConfig(driverConfigQuery.data);
    // Admin may have uploaded a custom ride-offer sound — swap the
    // player to that URL. Null/empty reverts to the bundled placeholder.
    setOfferSoundUrl((driverConfigQuery.data as { ride_offer_sound_url?: string | null }).ride_offer_sound_url ?? null);
  }, [driverConfigQuery.data, applyDriverConfig]);

  // ─── Location Tracking ───────────────────────────────────────────
  // Refresh on mount AND whenever the app returns to the foreground so
  // the map doesn't keep showing a stale fix from the previous session
  // (e.g. driver left home yesterday, opened app at work — without the
  // AppState refresh the map would still point at home until
  // watchPositionAsync starts after Go Online).
  const refreshLocation = useCallback(async (useCache: boolean) => {
    setLocationStatus(prev => (prev === 'ok' ? prev : 'pending'));
    if (useCache) {
      try {
        const saved = await AsyncStorage.getItem('spinr_driver_last_location');
        if (saved) {
          const { lat, lng } = JSON.parse(saved);
          setLocation({ coords: { latitude: lat, longitude: lng, heading: 0, speed: 0, accuracy: 100, altitude: 0 }, timestamp: Date.now() } as any);
        }
      } catch {}
    }

    let { status } = await Location.getForegroundPermissionsAsync();
    if (status !== 'granted') {
      const res = await Location.requestForegroundPermissionsAsync();
      status = res.status;
    }
    if (status !== 'granted') {
      // The dashboard gates on location, not status — a fix restored from
      // the AsyncStorage cache above would keep a stale map rendered over
      // this blocking error, and locationRef would leak the stale position
      // to the backend on WS reconnect. Clear both.
      setLocation(null);
      locationRef.current = null;
      setLocationStatus('denied');
      return null;
    }

    // Permission can be granted while device-wide location services are
    // off (Android quick-settings toggle) — getCurrentPositionAsync would
    // just throw, so detect it up front. No fix will ever arrive in this
    // state, so a stale cached map would be misleading: clear it too.
    const servicesOn = await Location.hasServicesEnabledAsync().catch(() => true);
    if (!servicesOn) {
      setLocation(null);
      locationRef.current = null;
      setLocationStatus('unavailable');
      return null;
    }

    if (useCache) {
      try {
        const lastKnown = await Location.getLastKnownPositionAsync();
        if (lastKnown) { setLocation(lastKnown); locationRef.current = lastKnown; }
      } catch {}
    }

    try {
      // getCurrentPositionAsync has no timeout and can hang indefinitely
      // waiting for a fix (indoors, weak GPS) — race it so the driver
      // never sits on the spinner forever.
      const loc = await Promise.race([
        Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }),
        new Promise<never>((_, reject) => setTimeout(() => reject(new Error('location fix timeout')), 15000)),
      ]);
      setLocation(loc);
      locationRef.current = loc;
      setLocationStatus('ok');
      try {
        AsyncStorage.setItem('spinr_driver_last_location', JSON.stringify({ lat: loc.coords.latitude, lng: loc.coords.longitude }));
      } catch {}
      return loc;
    } catch {
      // Fix failed or timed out — a coarse last-known position still lets
      // the map render; watchPositionAsync corrects it once a fix lands.
      try {
        const lastKnown = await Location.getLastKnownPositionAsync();
        if (lastKnown) {
          setLocation(lastKnown);
          locationRef.current = lastKnown;
          setLocationStatus('ok');
          return lastKnown;
        }
      } catch {}
      if (locationRef.current) {
        // A real fix from this session (lastKnown/watcher) is still on the
        // map — keep it; the watcher corrects it once a fresh fix lands.
        setLocationStatus('ok');
      } else {
        // Only the AsyncStorage-cached coordinate (if any) is showing.
        // It never reached locationRef so it can't be sent to dispatch,
        // but it would silently mask this failure — clear it so the
        // retry UI appears instead of a stale map.
        setLocation(null);
        setLocationStatus('unavailable');
      }
      return null;
    }
  }, []);

  useEffect(() => {
    // Mount-only fetch; refreshLocation (useCallback([]), stable) sets state
    // after its own await, not synchronously at the top of the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshLocation(true);
    const sub = AppState.addEventListener('change', (next) => {
      // Skip cache on resume — we want a fresh fix, not yesterday's.
      if (next === 'active') refreshLocation(false);
    });
    return () => sub.remove();
  }, [refreshLocation]);

  // ─── Durable trip-location upload ─────────────────────────────────
  // The recorder owns the only durable queue. This transport is deliberately
  // injected so background/headless code can use its own refreshed-token path.
  const foregroundLocationTransport = useCallback(async (
    request: TripLocationBatchRequest,
  ): Promise<TripLocationBatchAck> => {
    const response = await api.post<TripLocationBatchAck>('/drivers/location-batch', request);
    return response.data;
  }, []);

  const uploadLocationBatch = useCallback(async () => {
    await tripLocationRecorder.flushPending(foregroundLocationTransport, { force: true });
  }, [foregroundLocationTransport]);

  useEffect(() => {
    if (!isOnline) return;
    const interval = setInterval(() => {
      tripLocationRecorder.flushPending(foregroundLocationTransport).then(() => {
        batchUploadHealthyRef.current = true;
      }).catch(() => {
        batchUploadHealthyRef.current = false;
      });
    }, 10_000);
    return () => clearInterval(interval);
  }, [foregroundLocationTransport, isOnline]);

  // GPS heartbeat: during a trip, if the recorder has captured nothing for
  // its 30s watchdog window (standstill under distanceInterval, provider
  // hiccup, watcher silently dead), actively request one fix so the durable
  // route never develops a >60s gap the server would split into a dropped
  // segment. Also re-checks device-wide location services so the existing
  // 'unavailable' UI state surfaces mid-trip, not just on mount/resume.
  useEffect(() => {
    if (!isOnline || !TRACKED_TRIP_PHASES.includes(rideState)) return;
    const interval = setInterval(async () => {
      try {
        const rideId = useDriverStore.getState().activeRide?.ride?.id;
        if (!rideId) return;
        const servicesOn = await Location.hasServicesEnabledAsync().catch(() => true);
        if (!servicesOn) {
          setLocation(null);
          locationRef.current = null;
          setLocationStatus('unavailable');
          return;
        }
        const health = await tripLocationRecorder.getRecorderHealth(rideId);
        if (health.degradationReason !== 'no_recent_fix') return;
        const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
        const integrity = checkLocationIntegrity(loc);
        if (!integrity.trusted) return;
        await tripLocationRecorder.recordNativeFix(loc, 'foreground', rideId);
        tripLocationRecorder.flushPending(foregroundLocationTransport).then(() => {
          batchUploadHealthyRef.current = true;
        }).catch(() => {
          batchUploadHealthyRef.current = false;
        });
      } catch {
        // Best-effort: a failed heartbeat leaves the recorder-health banner
        // to surface persistent degradation; never crash the interval.
      }
    }, 30_000);
    return () => clearInterval(interval);
  }, [foregroundLocationTransport, isOnline, rideState]);

  // Flush the durable outbox on app-state transitions during a trip.
  // Foreground→background: the watcher is about to stop firing, so drain what
  // we have before the OS can suspend the JS thread. Background→foreground:
  // drain whatever the background task captured while the driver navigated in
  // Maps, so the server-visible tail never lags a whole backgrounded stretch.
  useEffect(() => {
    if (!isOnline || !TRACKED_TRIP_PHASES.includes(rideState)) return;
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active' || next === 'background') {
        tripLocationRecorder.flushPending(foregroundLocationTransport, { force: true }).then(() => {
          batchUploadHealthyRef.current = true;
        }).catch(() => {
          batchUploadHealthyRef.current = false;
        });
      }
    });
    return () => sub.remove();
  }, [foregroundLocationTransport, isOnline, rideState]);

  // Location subscription — frequency adapts to ride state
  useEffect(() => {
    if (!isOnline) {
      if (locationSubRef.current) {
        try { locationSubRef.current.remove(); } catch (e) { console.log('[Location] subscription remove error:', e); }
        locationSubRef.current = null;
      }
      // Best-effort final flush. The recorder intentionally retains any
      // unacknowledged trip sample for retry; only a server acknowledgement can
      // remove it. The non-durable map cache remains safe to clear offline.
      (async () => {
        try { await uploadLocationBatch(); } catch {}
        try {
          await AsyncStorage.removeItem('spinr_driver_last_location');
        } catch {}
      })();
      return;
    }
    const config = LOCATION_CONFIGS[rideState] ?? LOCATION_CONFIGS.idle;
    // Re-tune the *background* task to match the phase too. The foreground
    // watchPositionAsync below only fires while the app is foregrounded, so a
    // trip driven with the app backgrounded (driver in Maps / screen locked)
    // relies entirely on the background task — keep it dense during trip
    // phases, coarse when idle. No-op until go-online has started the task.
    const inTripPhase = TRACKED_TRIP_PHASES.includes(rideState);
    updateBackgroundLocationCadence(inTripPhase ? TRIP_CADENCE : IDLE_CADENCE).catch(() => {});
    // Persist the trip-active flag so a geofence wake can use trip cadence for
    // future capture; it cannot reconstruct samples missed after a force-quit.
    setBackgroundTripActive(inTripPhase).catch(() => {});
    if (inTripPhase && activeRide?.ride?.id) {
      tripLocationRecorder.startRide(activeRide.ride.id).catch(() => {
        setWsError('Trip location recording needs attention. Points will retry automatically.');
      });
    }
    (async () => {
      if (locationSubRef.current) {
        try { locationSubRef.current.remove(); } catch {}
        locationSubRef.current = null;
      }
      let sub: Location.LocationSubscription;
      try {
        sub = await Location.watchPositionAsync(
          {
            accuracy: config.accuracy,
            timeInterval: config.timeInterval,
            distanceInterval: config.distanceInterval,
          },
          (loc) => {
          const integrity = checkLocationIntegrity(loc);
          if (!integrity.trusted) {
            console.warn(`[Location] Spoofed/invalid fix rejected: ${integrity.reason}`);
            return;
          }
          // Sensor mismatch: tag the payload instead of dropping the location.
          // Dropping made the driver invisible to dispatch on false positives
          // (phone on soft mount, smooth highway, cup holder vibration).
          const sensorCheck = checkMovementConsistency(loc.coords.speed);
          if (!sensorCheck.consistent) {
            console.warn(`[Location] Sensor suspect: variance=${sensorCheck.variance.toFixed(4)}`);
          }

          locationRef.current = loc;
          const now = Date.now();
          // Tighten the render cadence during active trip phases so the driver
          // UI distance counter and map marker stay responsive; coarse throttle
          // is fine when idle to avoid unnecessary re-renders.
          const renderThrottleMs = inTripPhase ? 3000 : 10000;
          if (now - lastRenderMsRef.current >= renderThrottleMs) {
            lastRenderMsRef.current = now;
            setLocation(loc);
          }

          const currentActiveRide = useDriverStore.getState().activeRide;
          const rideId = currentActiveRide?.ride?.id;
          if (rideId && inTripPhase) {
            void tripLocationRecorder.recordNativeFix(loc, 'foreground', rideId).then((point) => {
              if (!point) return;
              if (wsRef.current?.readyState === WebSocket.OPEN) {
                // Send v1 fields only — never spread the full TripLocationPoint.
                // v2 fields (recording_session_id, sequence_number) redirect the
                // WS handler to persist_trip_location_batch, which requires a DB
                // unique index (migration 239). If that index is missing, the
                // INSERT ON CONFLICT fails and the ride records zero points.
                // The v1 persist path (plain INSERT) works unconditionally.
                //
                // durable=true when the REST batch upload is failing, so the WS
                // handler persists via the v1 path as a fallback. When REST is
                // healthy, durable=false keeps WS as a live marker only.
                wsRef.current.send(JSON.stringify({
                  type: 'driver_location',
                  durable: !batchUploadHealthyRef.current,
                  captured_at: point.captured_at,
                  lat: point.lat,
                  lng: point.lng,
                  speed: point.speed,
                  heading: point.heading,
                  accuracy: point.accuracy,
                  altitude: point.altitude,
                  ride_id: rideId,
                }));
              }
              return tripLocationRecorder.flushPending(foregroundLocationTransport);
            }).then(() => {
              batchUploadHealthyRef.current = true;
            }).catch(async () => {
              batchUploadHealthyRef.current = false;
              const health = await tripLocationRecorder.getRecorderHealth(rideId);
              if (health.degraded) {
                setWsError('Trip location recording needs attention. Points will retry automatically.');
              }
            });
          } else if (wsRef.current?.readyState === WebSocket.OPEN) {
            // Phase 1 (online, no ride). The live marker fans out on every fix
            // for a smooth map, but is ephemeral (durable:false) so it never
            // fills the trail. Separately, ~once a minute we send ONE durable
            // idle breadcrumb — the backend persists it as an online_idle point
            // (ride_id null) for driver location history. Retention: the PII
            // purge deletes driver_location_history at 90 days.
            const IDLE_DURABLE_INTERVAL_MS = 60_000;
            const persistIdle = now - lastIdleDurableMsRef.current >= IDLE_DURABLE_INTERVAL_MS;
            if (persistIdle) lastIdleDurableMsRef.current = now;
            wsRef.current.send(JSON.stringify({
              type: 'driver_location',
              // durable:true on the ~60s tick routes through buffer_ride_breadcrumb
              // → persist_ride_breadcrumbs(persist_idle=True); every other fix
              // stays an ephemeral live marker.
              durable: persistIdle,
              lat: loc.coords.latitude,
              lng: loc.coords.longitude,
              speed: loc.coords.speed ?? null,
              heading: loc.coords.heading ?? null,
              accuracy: loc.coords.accuracy ?? null,
              altitude: loc.coords.altitude ?? null,
              mocked: loc.mocked ?? false,
            }));
          }
        }
        );
      } catch (e) {
        console.error('[Location] watchPositionAsync failed — driver is GPS-blind:', e);
        setWsError('Location unavailable. Check location permissions in Settings.');
        return;
      }
      locationSubRef.current = sub;
    })();

    return () => {
      if (locationSubRef.current) {
        try { locationSubRef.current.remove(); } catch (e) { console.log('[Location] subscription remove error (cleanup):', e); }
        locationSubRef.current = null;
      }
    };
  }, [activeRide?.ride?.id, foregroundLocationTransport, isOnline, rideState, uploadLocationBatch]);

  // ─── WebSocket Message Handler ───────────────────────────────────
  const handleWSMessage = useCallback((data: any) => {
    switch (data.type) {
      case 'new_ride_assignment': {
        // Guard: drop offers that arrive after the driver already accepted.
        // Backend sends both WS + FCM for the same dispatch event; whichever
        // arrives second would otherwise overwrite navigating_to_pickup state
        // and pop the offer panel back up over the OTP screen.
        const { rideState: _wsRideState } = useDriverStore.getState();
        if (_wsRideState !== 'idle') {
          console.warn('[WS] ignoring new_ride_assignment in state:', _wsRideState, 'ride_id:', data.ride_id);
          break;
        }
        const pLat = _toFiniteCoord(data.pickup_lat);
        const pLng = _toFiniteCoord(data.pickup_lng);
        const dLat = _toFiniteCoord(data.dropoff_lat);
        const dLng = _toFiniteCoord(data.dropoff_lng);
        if (pLat === null || pLng === null || dLat === null || dLng === null) {
          console.error(
            '[WS] dropping ride offer with invalid coords',
            { ride_id: data.ride_id, pickup_lat: data.pickup_lat, pickup_lng: data.pickup_lng,
              dropoff_lat: data.dropoff_lat, dropoff_lng: data.dropoff_lng },
          );
          break;
        }
        if (useAlertPrefsStore.getState().vibration) {
          Vibration.vibrate([0, 500, 200, 500]);
        }
        offerSound.play();
        // Backgrounded-but-alive: the in-app panel is hidden, so surface the
        // heads-up Notifee card here — the WS offer is the first, most reliable
        // trigger. No-op when foreground; deduped against the FCM path by id.
        _surfaceOfferNotification(data);
        router.replace('/driver/' as any);
        setIncomingRide({
          ride_id: data.ride_id,
          pickup_address: data.pickup_address,
          dropoff_address: data.dropoff_address,
          pickup_lat: pLat,
          pickup_lng: pLng,
          dropoff_lat: dLat,
          dropoff_lng: dLng,
          fare: data.fare || 0,
          distance_km: data.distance_km,
          duration_minutes: data.duration_minutes,
          rider_name: data.rider_name,
          rider_rating: data.rider_rating,
          requires_wav: data.requires_wav === true,
          quiet_mode: data.quiet_mode === true,
          is_scheduled: data.is_scheduled === true,
          countdown_seconds: typeof data.countdown_seconds === 'number' ? data.countdown_seconds : undefined,
          offer_expires_at: data.offer_expires_at,
          surge_multiplier: typeof data.surge_multiplier === 'number' ? data.surge_multiplier : undefined,
          incentives: Array.isArray(data.incentives) ? data.incentives : undefined,
          total_bonus: typeof data.total_bonus === 'number' ? data.total_bonus : undefined,
          quest_hint: data.quest_hint || undefined,
          payment_method: data.payment_method || undefined,
          planned_route_polyline: Array.isArray(data.planned_route_polyline) ? data.planned_route_polyline : undefined,
        });
        break;
      }
      case 'ride_offer_expired': {
        const hadVisibleOffer = useDriverStore.getState().rideState === 'ride_offered';
        offerSound.stop();
        resetRideState();
        if (hadVisibleOffer) {
          showToast('info', 'Offer expired', 'Looking for the next ride...');
        }
        break;
      }
      case 'ride_taken': {
        // Guard: never tell the WINNING driver their own ride was taken. If we
        // already accepted this ride (or any ride — post-accept states), a
        // stray/stale ride_taken must not reset the navigation state or show
        // the misleading "another driver accepted" toast.
        const { rideState: _takenState, activeRide: _takenActive, incomingRide: _takenIncoming } =
          useDriverStore.getState();
        const _postAccept = ['navigating_to_pickup', 'arrived_at_pickup', 'trip_in_progress'].includes(_takenState);
        if (_postAccept) {
          console.warn('[WS] ignoring ride_taken in state:', _takenState, 'ride_id:', data.ride_id,
            'active_ride:', _takenActive?.ride?.id);
          break;
        }
        // Ignore events for a different ride than the offer on screen.
        if (data.ride_id && _takenIncoming?.ride_id && _takenIncoming.ride_id !== data.ride_id) {
          break;
        }
        offerSound.stop();
        resetRideState();
        showToast('info', 'Ride taken', 'Another driver accepted this ride.');
        break;
      }
      case 'auto_offline':
        // Backend took the driver offline after N consecutive missed offers.
        offerSound.stop();
        resetRideState();
        setIsOnline(false);
        showAlert(
          'You\'re now offline',
          data.message || 'You were taken offline because you missed several ride offers in a row. Tap "Go Online" when you\'re ready.',
        );
        break;
      case 'ride_cancelled':
        showToast(
          'error',
          'Ride Cancelled',
          data.is_auto
            ? 'No driver was available — the ride was automatically cancelled.'
            : 'The rider has cancelled this ride.'
        );
        resetRideState();
        break;

      // G20: Reply to server heartbeat so the backend doesn't mark
      // this connection as dead after HEARTBEAT_TIMEOUT (10s).
      case 'ping':
        if (pongSentAtRef.current > 0) {
          setWsLatency(Date.now() - pongSentAtRef.current);
        }
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          pongSentAtRef.current = Date.now();
          wsRef.current.send(JSON.stringify({ type: 'pong' }));
        }
        break;

      // P3.2: the backend saw this active trip stop reporting GPS (P3.1 nudge).
      // Recover the stream while the trip is still live — re-assert the dense
      // trip cadence (restarting the task if the OS killed it) and force-flush
      // the outbox — rather than only reconstructing the gap afterward.
      case 'location_health': {
        recoverTripLocation()
          .then((running) => {
            if (running) {
              tripLocationRecorder
                .flushPending(foregroundLocationTransport, { force: true })
                .catch(() => {});
            } else {
              console.warn('[Location] recovery could not restart tracking — check location permission');
            }
          })
          .catch(() => {});
        break;
      }

      // G3: Rider-to-driver chat messages. Backend websocket.py forwards
      // chat_message to `driver_{user_id}`. Push into driverStore so
      // chat.tsx updates in real-time without polling. [SPR-01]
      case 'chat_message':
        if (
          data.text !== undefined &&
          typeof data.text === 'string' &&
          typeof data.sender === 'string'
        ) {
          useDriverStore.getState().addChatMessage(data);
          Vibration.vibrate(100);
        } else {
          console.warn('[WS] Malformed chat message payload:', data);
        }
        break;

      case 'chat_typing':
        if (data.sender === 'rider') {
          useDriverStore.getState().setRiderTyping(true);
        }
        break;

      // Rider tipped a completed ride. Backend rides.py emits this right
      // after persisting the tip so the driver sees it immediately
      // instead of only after the next earnings refresh.
      case 'tip_received': {
        const amount = typeof data.amount === 'number' ? data.amount : 0;
        if (amount > 0) {
          Vibration.vibrate([0, 200, 100, 200]);
          const riderName = typeof data.rider_name === 'string' && data.rider_name
            ? data.rider_name
            : 'Your rider';
          showToast('success', 'You got a tip!', `${riderName} tipped you $${amount.toFixed(2)}.`);
          // Pull the fresh totals so the earnings chip updates on screen.
          fetchEarnings('today');
        }
        break;
      }

      // Unified status event from socket_manager.broadcast_ride_status().
      // Currently emitted to drivers on admin-initiated cancellations.
      // Handlers are idempotent: specific events (ride_cancelled, etc.)
      // may already have acted; these are reconciliation / catch-up paths.
      case 'ride_status_changed': {
        const status = data.status as string | undefined;
        // Drop stale / out-of-order events by the server-authoritative
        // monotonic ride version (V4, issue #11) — e.g. a delayed cancelled
        // arriving after the ride already advanced, or a reconnect-replay
        // duplicate. Events without a version (un-migrated backend) always
        // apply, so this is behaviour-preserving until the backend stamps one.
        const _version = typeof data.version === 'number' ? data.version : undefined;
        if (!useDriverStore.getState().shouldApplyRideEvent(data.ride_id, _version)) {
          console.warn('[WS] dropping out-of-order ride_status_changed', {
            ride_id: data.ride_id, version: _version, status,
          });
          break;
        }
        if (status === 'cancelled') {
          resetRideState();
        } else if (status === 'completed' && typeof data.total_fare === 'number') {
          fetchEarnings('today');
        }
        break;
      }

      default:
        console.warn('[WS] Unknown message type received:', data.type);
        break;
    }
    // fetchEarnings is a stable driverStore action; foregroundLocationTransport
    // is useCallback([]) (empty deps, closes over nothing reactive — just
    // calls api.post) — both genuinely stable, safe to add directly. Also
    // moot for connectWebSocket's own stability either way: the ref
    // indirection below already decouples it from handleWSMessage's identity.
  }, [setIncomingRide, resetRideState, offerSound, fetchEarnings, foregroundLocationTransport]);

  // Stable ref to the message handler so connectWebSocket's identity
  // doesn't flip every time the handler closure changes.
  const handleWSMessageRef = useRef(handleWSMessage);
  useEffect(() => { handleWSMessageRef.current = handleWSMessage; }, [handleWSMessage]);

  // Stop the recurring offer tone whenever the offer goes away — covers
  // accept, decline, system reset, ride_cancelled, and the explicit
  // ride_offer_expired path. Centralised here so every clear-path is
  // covered without sprinkling stop() calls through the store actions.
  useEffect(() => {
    if (!incomingRide) {
      offerSound.stop();
    }
  }, [incomingRide, offerSound]);

  // ─── WebSocket Connection ────────────────────────────────────────
  // Empty dep array: this callback reads everything through refs (userRef,
  // isOnlineRef, locationRef, handleWSMessageRef) so its identity is stable
  // for the lifetime of the hook. Previously this depended on [user,
  // handleWSMessage] and recreated on unrelated store changes — the mount
  // effect saw a "new" connectWebSocket, closed the socket, and reconnected,
  // leaving the banner stuck on "Reconnecting…".
  const connectWebSocket = useCallback(async () => {
    // Ensure the access token is fresh before opening the socket. Without
    // this, a driver returning from a long background period opens a WS
    // with an expired token — the server rejects auth and the socket
    // immediately closes, burning a reconnect cycle.
    try {
      await ensureFreshToken();
    } catch (err) {
      // Token refresh failed — the stored token may still be valid (short
      // background period) so we proceed. If it's truly expired, the server
      // will reject auth, onclose fires, and the reconnect loop retries
      // with a fresh ensureFreshToken() call. Previously this was an empty
      // catch that silently swallowed the error, making it invisible why
      // drivers got stuck in a "Reconnecting…" loop after long backgrounds.
      console.warn('[WS] ensureFreshToken failed, proceeding with current token:', err);
    }

    const currentUser = userRef.current;
    if (!isOnlineRef.current || !currentUser) return;

    const token = useAuthStore.getState().token;
    if (!token) {
      return;
    }

    // Defence against a misconfigured build: if no backend URL with a valid
    // http(s) scheme can be resolved, do NOT construct the socket — a
    // scheme-less URL ("/ws/driver/123") crashes the native OkHttp layer with
    // a FATAL EXCEPTION. Surface a connection error and bail gracefully so the
    // driver sees "Connection lost" instead of the app dying on tap-GO.
    const baseUrl = _resolveBackendBaseUrl();
    if (!baseUrl) {
      console.error(
        '[WS] No valid backend URL — cannot open driver socket. ' +
        'EXPO_PUBLIC_BACKEND_URL is likely missing from this build.',
      );
      setConnectionState('disconnected');
      setWsError(tKey('dashboard.connectionLost'));
      return;
    }

    // Match URL scheme: https backends (prod or DEV pointing at prod) use wss://,
    // http backends (local dev) use ws://. Checking __DEV__ is wrong — Railway's
    // edge proxy rejects plain ws:// with "Invalid Sec-WebSocket-Accept response".
    const useSecure = baseUrl.startsWith('https');
    const wsBaseUrl = `${baseUrl.replace(/^https?/, useSecure ? 'wss' : 'ws')}/ws/driver/${currentUser.id}`;
    const wsUrl = lastSeqRef.current > 0 ? `${wsBaseUrl}?last_seq=${lastSeqRef.current}` : wsBaseUrl;
    // Flip the banner to 'reconnecting' the moment we start connecting.
    // The initial state is 'disconnected', which renders as the red
    // "Connection Lost" banner — if we leave it there during the TLS +
    // auth-handshake window (easily 1–2 s on Railway cold starts), the
    // driver sees an alarming banner while the socket is actually coming
    // up fine. 'reconnecting' renders as the amber "Reconnecting…" state
    // which correctly signals work-in-progress.
    setConnectionState('reconnecting');
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    // Don't claim 'connected' at onopen — the backend requires a post-open
    // `auth` message and will close the socket if it fails. We flip to
    // 'connected' on the first non-error server message, which proves auth
    // passed. Until then, stay in 'reconnecting' (or initial 'disconnected').
    // Track per-connection auth state. Set to true only after server confirms
    // auth_success so we never send location before the session is verified.
    let wsAuthenticated = false;

    // Arm the auth watchdog. If auth_success doesn't arrive within the window
    // (lost first message / half-open socket), force-close so onclose schedules
    // a reconnect — instead of sitting on "Reconnecting…" until the server's
    // 30s auth timeout fires.
    if (authWatchdogRef.current) clearTimeout(authWatchdogRef.current);
    authWatchdogRef.current = setTimeout(() => {
      if (wsRef.current === ws && !wsAuthenticated) {
        console.warn('[WS] auth watchdog fired — no auth_success, forcing reconnect');
        try { ws.close(); } catch { }
      }
    }, AUTH_WATCHDOG_MS);

    ws.onopen = () => {
      const currentToken = useAuthStore.getState().token;
      ws.send(JSON.stringify({
        type: 'auth',
        token: currentToken,
        client_type: 'driver',
      }));
      // Location resend is deferred to after auth_success is confirmed in
      // onmessage. Sending it here (before the server has processed auth)
      // could cause the server to close the socket if the message is
      // processed in the brief window before the main receive loop starts.
    };

    ws.onmessage = (event) => {
      try {
        let data = JSON.parse(event.data);
        if (data && typeof data === 'object' && 'seq' in data && 'data' in data) {
          lastSeqRef.current = data.seq;
          data = data.data;
        }

        lastServerMsgRef.current = Date.now();
        if (data.type === 'error') {
          const msg = data.message || '';
          // Auth errors (expired/invalid token) are handled by reconnect —
          // don't scare the driver with technical messages.
          if (msg.includes('token') || msg.includes('auth') || msg.includes('user_not_found')) {
            return;
          }
          setWsError(tKey('dashboard.connectionLost'));
          return;
        }
        // auth_success is the definitive signal that the backend accepted
        // the connection. Only flip to 'connected' and reset reconnect
        // counter on this specific message — not on every ping/pong/etc.
        if (data.type === 'auth_success' && wsRef.current === ws) {
          const wasReconnect = reconnectAttemptRef.current > 0;
          reconnectAttemptRef.current = 0;
          setConnectionState('connected');
          setWsError(null);
          wsAuthenticated = true;
          if (authWatchdogRef.current) { clearTimeout(authWatchdogRef.current); authWatchdogRef.current = null; }
          // Re-sync active ride state — server buffers no WS events, so any
          // transitions that fired while disconnected are recovered via HTTP.
          // Always fetch on reconnect (even when holding an offer) so stale
          // FCM-backed offers are validated against current server state.
          // fetchActiveRide is safe to call with a live offer: it checks
          // offer_expires_at and only clobbers the panel if the offer expired.
          if (wasReconnect) {
            fetchActiveRide();
          }
          // Now that auth is confirmed, send the cached location so the
          // backend has a fresh position immediately after reconnect.
          const loc = locationRef.current;
          if (loc && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
              type: 'driver_location',
              lat: loc.coords.latitude,
              lng: loc.coords.longitude,
              speed: loc.coords.speed ?? null,
              heading: loc.coords.heading ?? null,
              accuracy: loc.coords.accuracy ?? null,
              altitude: loc.coords.altitude ?? null,
              ride_id: null,
              tracking_phase: 'online_idle',
            }));
          }
        }
        handleWSMessageRef.current(data);
      } catch (e) {
        // Not sent to Sentry: this catch spans every downstream message
        // handler, and the server pings every 10s, so a regression in any
        // handler would emit hundreds of events per driver per hour. A
        // warning keeps it visible without turning a handler bug into a
        // quota incident.
        console.warn('[WS] message handler failed:', e);
      }
    };

    ws.onerror = () => {
      // CLAUDE.md: "Degraded-but-recovered → warning log + metric (never
      // Sentry — noise)". A failed connect attempt is followed by an
      // automatic reconnect, and RN's error event carries no usable detail
      // (it is never an Error instance), so a capture here would be ~10
      // identical, information-free events per outage. Sentry reporting
      // lives on the reconnect-exhausted path below, which is user-visible.
      console.warn('[WS] socket error; reconnect will follow');
    };

    ws.onclose = (event) => {
      // Guard against stale sockets: when the app foregrounds, the AppState
      // handler resets the counter and calls connectWebSocket() before the
      // old socket finishes CLOSING. Without this guard the old socket's
      // onclose fires after the reset and creates a second socket, which
      // then has onclose fire again — producing a connection-storm.
      if (ws !== wsRef.current) return;

      if (authWatchdogRef.current) { clearTimeout(authWatchdogRef.current); authWatchdogRef.current = null; }

      if (isOnlineRef.current && userRef.current) {
        if (reconnectAttemptRef.current >= MAX_RECONNECT_ATTEMPTS) {
          setConnectionState('disconnected');
          setWsError('Unable to connect to server. Pull down to retry or toggle offline/online.');
          captureException(
            new Error(`WebSocket reconnect exhausted after ${MAX_RECONNECT_ATTEMPTS} attempts`),
            { domain: 'dispatch', ws_event: 'reconnect_exhausted', close_code: String(event.code) },
          );
          return;
        }
        setConnectionState('reconnecting');
        const tier = Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS.length - 1);
        const baseDelay = RECONNECT_DELAYS[tier];
        // Scale jitter with the base delay so at 30 s we disperse ±2 s
        // instead of ±500 ms — avoids a thundering herd when many drivers
        // reconnect after a server hiccup at the same time.
        const jitterRange = Math.max(1000, baseDelay * 0.067);
        const jitter = Math.random() * jitterRange * 2 - jitterRange;
        const delay = Math.max(500, baseDelay + jitter);
        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectAttemptRef.current++;
          connectWebSocket();
        }, delay);
      } else {
        setConnectionState('disconnected');
      }
    };
    // fetchActiveRide is a driverStore *action* (defined once inside
    // create(), never redefined by any subsequent set() call — zustand's
    // set() shallow-merges, so an action absent from a partial update keeps
    // its original reference forever), NOT reactive *state* like `user`.
    // The comment above (userRef/isOnlineRef/handleWSMessageRef) explains
    // why this callback deliberately avoids closing over reactive STATE
    // values directly — doing so would recreate this callback on every
    // store update and tear down the socket. An action's identity never
    // changes, so it carries none of that risk; verified against zustand's
    // merge semantics (grepped driverStore.ts for any full-state
    // reassignment that could redefine fetchActiveRide — none exists, only
    // shallow set({...partial}) calls throughout).
  }, [fetchActiveRide]);

  useEffect(() => {
    if (!isOnline || !user) {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (authWatchdogRef.current) {
        clearTimeout(authWatchdogRef.current);
        authWatchdogRef.current = null;
      }
      if (wsRef.current) {
        try { wsRef.current.close(); } catch (e) { console.log('[WS] close error (going offline):', e); }
        wsRef.current = null;
      }
      // Sync connection-state UI with the driver going offline / signing
      // out. Doesn't feed back into isOnline/user, so this can't loop.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setConnectionState('disconnected');
      return;
    }

    connectWebSocket();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (authWatchdogRef.current) {
        clearTimeout(authWatchdogRef.current);
        authWatchdogRef.current = null;
      }
      if (wsRef.current) {
        try { wsRef.current.close(); } catch (e) { console.log('[WS] close error (cleanup):', e); }
        wsRef.current = null;
      }
    };
    // connectWebSocket is stable (empty deps, reads state through refs), so
    // we intentionally leave it out — listing it would tear down the socket
    // on unrelated re-renders. We DO need user.id to trigger reconnect when
    // the signed-in user changes; use the id specifically, not the object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOnline, user?.id]);

  // Uber/Lyft-style presence: proactively close the WebSocket when the
  // app backgrounds so the backend clears Redis presence immediately
  // (via the disconnect handler), instead of waiting for the 90 s TTL
  // to expire. Reconnect on return to foreground. Mobile OSes suspend
  // background sockets silently — without this the driver shows as
  // online to admins and can even receive ride offers after the app
  // was swiped away.
  //
  // Background close is debounced ~3s and `inactive` is ignored, so a
  // transient transition (notification shade, control-center, system
  // permission dialog) doesn't tear down the socket and trigger a
  // reconnect storm.
  useEffect(() => {
    const BACKGROUND_CLOSE_DELAY_MS = 3000;
    let backgroundCloseTimer: ReturnType<typeof setTimeout> | null = null;
    const cancelBackgroundClose = () => {
      if (backgroundCloseTimer) {
        clearTimeout(backgroundCloseTimer);
        backgroundCloseTimer = null;
      }
    };
    const sub = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        cancelBackgroundClose();
        if (!isOnlineRef.current || !userRef.current) return;
        // Reset circuit breaker on foreground resume — the user is actively
        // present and may have regained connectivity.
        reconnectAttemptRef.current = 0;
        setWsError(null);
        const ws = wsRef.current;
        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
          if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
          }
          connectWebSocket();
        }
      } else if (nextState === 'background') {
        // Schedule the close — if the app comes back to 'active' within
        // the debounce window (notification shade pull, system dialog),
        // we cancel and keep the socket open. `inactive` is iOS partial
        // cover; never close on that transition.
        cancelBackgroundClose();
        backgroundCloseTimer = setTimeout(() => {
          backgroundCloseTimer = null;
          if (wsRef.current) {
            try { wsRef.current.close(1001, 'app_backgrounded'); } catch {}
          }
        }, BACKGROUND_CLOSE_DELAY_MS);
      }
      // 'inactive' (iOS): intentionally no-op.
    });
    return () => {
      cancelBackgroundClose();
      sub.remove();
    };
    // connectWebSocket is stable; user read via ref. Empty deps so this
    // listener is registered exactly once per hook instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Client-side heartbeat watchdog ──────────────────────────────
  // Server sends ping every 10s. If we receive no message for 15s the
  // network likely dropped silently — force-close to trigger reconnect.
  useEffect(() => {
    if (connectionState !== 'connected') return;
    const id = setInterval(() => {
      if (Date.now() - lastServerMsgRef.current > 15_000) {
        wsRef.current?.close(4001, 'heartbeat_timeout');
      }
    }, 5_000);
    return () => clearInterval(id);
  }, [connectionState]);

  // ─── No idle-time auto-offline ───────────────────────────────────
  // A 15-min "Still driving?" prompt used to force drivers offline here.
  // Removed: long offer-less stretches are normal in a low-density market,
  // and the prompt silently offlined drivers whose phone was locked in a
  // dash mount. Zombie drivers are handled where Uber/Lyft handle them:
  // the backend auto-offlines after N consecutive missed offers
  // (auto_offline_miss_threshold) and the stale-intent reconciler flips
  // intent for apps that have been unreachable for hours.

  // ─── Toggle Online/Offline ───────────────────────────────────────
  const toggleOnline = async () => {
    isTogglingRef.current = true;
    try {
      // Offline guard — only when going ONLINE. Without a connection the
      // go-online request can't reach the backend, so tell the driver with a
      // toast instead of letting it silently fail. (Going offline is always
      // allowed so a driver on a dead connection can still flip themselves off.)
      if (!isOnline) {
        const net = await NetInfo.fetch().catch(() => null);
        const connected = net ? (net.isConnected ?? net.isInternetReachable ?? true) : true;
        if (connected === false) {
          showToast('error', 'Internet offline', 'Your internet is offline. Reconnect and try again.');
          return;
        }
      }

      // Onboarding status is computed server-side on /auth/me; read it live.
      const onboardingStatus = useAuthStore.getState().user?.driver_onboarding_status;

      // Vehicle details missing → toast + send them to the vehicle form.
      if (!driverData?.vehicle_make || !driverData?.license_plate) {
        showToast('error', tKey('dashboard.addVehicle'), tKey('dashboard.profileIncompleteMsg'));
        router.push('/vehicle-info' as any);
        return;
      }

      // Documents pending / rejected / expired / under review → toast + docs page.
      if (onboardingStatus === 'documents_required') {
        showToast('error', tKey('dashboard.actionRequired'), tKey('dashboard.uploadDocs'));
        router.push('/documents' as any);
        return;
      }
      if (onboardingStatus === 'documents_rejected') {
        showToast('error', tKey('dashboard.docsRejected'), tKey('dashboard.fixDocuments'));
        router.push('/documents' as any);
        return;
      }
      if (onboardingStatus === 'documents_expired') {
        showToast('error', tKey('dashboard.docsExpired'), tKey('dashboard.updateDocs'));
        router.push('/documents' as any);
        return;
      }
      if (onboardingStatus === 'pending_review') {
        showToast('info', tKey('dashboard.underReview'), tKey('dashboard.viewStatus'));
        router.push('/documents' as any);
        return;
      }
      if (onboardingStatus === 'suspended') {
        showToast('error', tKey('dashboard.accountSuspended'), tKey('dashboard.contactSupport'));
        return;
      }

      if (!driverData?.is_verified) {
        showToast('error', tKey('dashboard.accountNotVerified'), tKey('dashboard.accountNotVerifiedMsg'));
        return;
      }

      const next = !isOnline;
      setIsOnline(next);
      try {
        // Pass the last known GPS so the backend persists it in the same
        // write as the is_online flip. Without this the driver row sits
        // at the (0, 0) registration default until the first background
        // location-batch arrives, and during that window riders and
        // admins see an empty map even though the driver is "ONLINE".
        const loc = locationRef.current;
        const locationPayload = next && loc?.coords
          ? { lat: loc.coords.latitude, lng: loc.coords.longitude }
          : undefined;
        await updateDriverStatus(next, locationPayload);
      } catch (err: any) {
        setIsOnline(!next);

        const status = err.response?.status;
        // getApiErrorMessage handles every body shape (plain `detail`,
        // structured `error.message`, validation arrays, RateLimitError) —
        // a bare `data.detail` read misses SpinrException-style rejections
        // (background check pending, inspection expired, area restriction).
        const reason = getApiErrorMessage(err, '');
        // ErrorCode.DRIVER_QUOTA_EXCEEDED (backend utils/error_handling.py)
        const errCode = err.response?.data?.error?.code;

        if (status === 402) {
          // No / expired Spinr Pass while the area requires one. Toast on every
          // attempt so the reason is unmissable, plus an alert with a Subscribe
          // shortcut.
          showToast('error', "Spinr Pass required", reason || "Activate your Spinr Pass to go online.");
          showAlert(
            "Spinr Pass Required",
            reason || "You need an active subscription to go online.",
            [
              { text: "Subscribe", onPress: () => router.push('/driver/subscription' as any) },
              { text: "Cancel", style: "cancel" },
            ]
          );
        } else if (status === 403 && errCode === 5006) {
          // Daily Spinr Pass ride allowance used up — resets at local midnight.
          showToast('info', "Daily ride limit reached", reason || "You've used today's Spinr Pass rides. They reset at midnight.");
        } else if (status === 403 && errCode === 1006) {
          // ErrorCode.AUTH_ACCOUNT_DISABLED (backend utils/error_handling.py) —
          // raised by routes/drivers/status.py for both 'suspended' and
          // 'banned' driver status. This is the authoritative, reliable
          // detection point: the client-side onboardingStatus === 'suspended'
          // pre-check earlier in this function is keyed off drivers.is_suspended,
          // which admin_driver_action never actually sets (it sets
          // drivers.status instead) — so that pre-check does not reliably
          // fire for a real admin suspension/ban. This catch block reflects
          // the real backend rejection, so it does.
          showToast('error', "Account disabled", reason || "Your account is currently suspended or banned.");
          showAlert(
            "Account Disabled",
            reason || "Your account has been suspended or banned. You can submit an appeal for review.",
            [
              { text: "Appeal", onPress: () => router.push('/appeal' as any) },
              { text: "Cancel", style: "cancel" },
            ]
          );
        } else {
          showToast('error', "Cannot Go Online", reason || "Failed to update status. Please try again.");
        }
        return;
      }

      if (next) {
        // Device integrity check — non-blocking, flags but doesn't block
        attestDeviceIntegrity().then((result) => {
          if (!result.attested) {
            console.warn('[Integrity] Device attestation failed:', result.reason);
          }
        });
        startSensorMonitoring();
        const bgStarted = await startBackgroundLocation();
        if (!bgStarted) {
          // Rollback: driver can't receive background offers without this
          // permission, so don't leave them marked online in the backend.
          setIsOnline(false);
          try { await updateDriverStatus(false); } catch {}
          stopSensorMonitoring();
          showToast('error', "Background location needed", "Enable 'Allow all the time' in Settings to go online and receive ride offers.");
        } else {
          // Arm a geofence around the current position. A wake may re-arm
          // future tracking after suspension, but cannot recover missed fixes.
          // Non-fatal: if location is not ready we skip the best-effort re-arm.
          const loc = locationRef.current;
          if (loc) {
            await startGeofenceRecovery(loc.coords.latitude, loc.coords.longitude).catch((e) => {
              console.warn('[toggleOnline] geofence arm failed:', e);
            });
          }
        }
      } else {
        stopSensorMonitoring();
        await stopBackgroundLocation();
        await stopGeofenceRecovery().catch(() => {});
        resetLocationIntegrity();
      }
    } catch (e) {
      console.error('[toggleOnline] Unexpected error:', e);
      showToast('error', "Error", "Something went wrong toggling your status. Please try again.");
    } finally {
      isTogglingRef.current = false;
    }
  };

  // ─── Navigate to pickup/dropoff ──────────────────────────────────
  const openNavigation = (lat: number, lng: number, label: string) => {
    const url = Platform.select({
      ios: `maps:0,0?q=${label}@${lat},${lng}`,
      android: `google.navigation:q=${lat},${lng}`,
    });
    if (url) Linking.openURL(url);
  };

  // Consume a ride offer stashed by the background FCM handler
  // (PENDING_OFFER_KEY) and surface it in the offer panel. Runs on mount AND
  // on foreground resume: when an offer arrives while the app is backgrounded
  // but still mounted and the driver taps the notification body, there's no
  // remount, so a mount-only check would never hydrate the panel and the
  // driver would see nothing. Guarded on rideState === 'idle' so it can't
  // clobber an active ride, and on offer_expires_at so a stale offer is dropped.
  // (Accept/Decline taps clear PENDING_OFFER_KEY in the background handler, so
  // on resume this only fires for a body tap / a still-pending offer.)
  //
  // The rules themselves now live in services/pendingRideOffer.ts, because the
  // Android Auto car session needs the identical behaviour on a car-only launch
  // where this hook never mounts. The buzz stays here: it is the phone's way of
  // announcing an offer, and the head unit raises its own alert instead.
  const consumePendingOffer = useCallback(
    () => consumePendingRideOffer({ onOffer: () => Vibration.vibrate([0, 500, 200, 500]) }),
    [],
  );

  // ─── Crash recovery + background-push hydration ──────────────────
  // 1. Surface any offer received while backgrounded/killed — on mount AND on
  //    every foreground resume (a tap on a backgrounded-but-mounted app
  //    doesn't remount this hook).
  // 2. hydrateDriverRideState() restores any persisted active-ride state.
  // 3. fetchActiveRide() confirms live server state and may override both.
  useEffect(() => {
    if (!user) return;
    consumePendingOffer();
    hydrateDriverRideState().then(() => fetchActiveRide());
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') consumePendingOffer();
    });
    return () => sub.remove();
    // hydrateDriverRideState/fetchActiveRide are stable driverStore actions
    // (same verified-stable-action reasoning as connectWebSocket's
    // fetchActiveRide fix above); adding them doesn't change when this
    // mount/foreground-resume effect fires — that's still driven entirely
    // by `user` and the AppState listener, both unchanged.
  }, [user, consumePendingOffer, hydrateDriverRideState, fetchActiveRide]);

  // ─── Hydrate the online flag from the authoritative profile (once) ──
  // isOnline is seeded at mount from driverData, which is frequently null on a
  // cold start / relaunch (auth hydrates async). Without correction, a driver
  // who is actually online — possibly mid-ride — comes back with isOnline=false:
  // the location subscription early-returns, startRide()/recordNativeFix() never
  // run, and background tracking is never re-armed (startBackgroundLocation
  // lives only in toggleOnline). The ride then records ZERO GPS points, which
  // is the "route incomplete · 0% GPS coverage" symptom.
  //
  // This is a ONE-TIME hydration, NOT continuous reactivity. Making isOnline
  // reactively follow driverData.is_online would fight server-initiated offline
  // (auto_offline sets isOnline=false locally but leaves driverData.is_online
  // true) and could kill capture mid-ride on any stale profile read. After the
  // first loaded profile, isOnline is owned solely by toggleOnline + auto_offline.
  const onlineHydratedRef = useRef(false);
  useEffect(() => {
    if (onlineHydratedRef.current) return;
    const serverOnline = driverData?.is_online;
    if (serverOnline === undefined || serverOnline === null) return; // profile not loaded yet
    if (isTogglingRef.current) return; // a toggle in flight is authoritative — let it settle
    onlineHydratedRef.current = true;
    if (!!serverOnline === isOnline) return;
    // One-time hydration, guarded by onlineHydratedRef above (set true on
    // the same pass, before this call) — every subsequent run of this
    // effect short-circuits on the first line, so this can never re-fire
    // and cascade even though `isOnline` is itself in the dep array below.
    // Only sets the driver-toggled isOnline flag, never the system-computed
    // is_available (CLAUDE.md invariant: is_available ⇒ is_online) — that
    // stays entirely backend-owned.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsOnline(!!serverOnline);
    if (serverOnline) {
      // Re-arm background tracking for a resumed-online session. Idempotent:
      // startBackgroundLocation no-ops when the task is already running.
      startBackgroundLocation().catch(() => {});
    }
  }, [driverData?.is_online, isOnline]);

  // ─── Fetch earnings when online ─────────────────────────────────
  useEffect(() => {
    if (isOnline) {
      fetchEarnings('today');
    }
    // fetchEarnings is a stable driverStore action; adding it doesn't
    // change when this effect fires.
  }, [isOnline, fetchEarnings]);

  // ─── Foreground FCM Message Handler ──────────────────────────────
  // FCM token registration + permissions live in app/_layout.tsx via
  // @shared/services/firebase. Here we only subscribe to foreground
  // messages so we can bridge them into the driver store — ride offers
  // that arrive via FCM (e.g. when the WebSocket connection is stale
  // or the device was briefly backgrounded) follow the same path as
  // the WebSocket `new_ride_assignment` handler above.
  //
  // Background / quit-state FCM messages are handled by the top-level
  // setBackgroundMessageHandler in _layout.tsx and surfaced as OS
  // notifications via the `ride-offers` Android channel.
  useEffect(() => {
    if (!isOnline || !user) return;

    const unsubscribe = onForegroundMessage((remoteMessage: any) => {
      const data = remoteMessage?.data || {};
      if (data?.type === 'new_ride_assignment' && data?.ride_id) {
        // Same guard as the WS handler: drop if driver already accepted.
        const { rideState: _fcmRideState, incomingRide: _fcmExisting } = useDriverStore.getState();
        if (_fcmRideState !== 'idle') {
          console.warn('[FCM] ignoring new_ride_assignment in state:', _fcmRideState, 'ride_id:', data.ride_id);
          return;
        }
        const pLat = _toFiniteCoord(data.pickup_lat);
        const pLng = _toFiniteCoord(data.pickup_lng);
        const dLat = _toFiniteCoord(data.dropoff_lat);
        const dLng = _toFiniteCoord(data.dropoff_lng);
        if (pLat === null || pLng === null || dLat === null || dLng === null) {
          console.error('[FCM] dropping ride offer with invalid coords', { ride_id: data.ride_id });
          return;
        }
        const existing = _fcmExisting;
        const isUpdate = existing?.ride_id === data.ride_id;
        if (!isUpdate) {
          if (useAlertPrefsStore.getState().vibration) {
            Vibration.vibrate([0, 500, 200, 500]);
          }
          offerSound.play();
          _surfaceOfferNotification(data);
          router.replace('/driver/' as any);
        }
        // FCM values are all strings. Parse JSON for arrays/objects.
        const safeParse = (v: unknown) => {
          if (!v || v === 'null' || v === 'None') return undefined;
          if (typeof v === 'string') { try { return JSON.parse(v); } catch { return undefined; } }
          return v;
        };
        const toNum = (v: unknown) => {
          if (!v || v === '' || v === 'None') return undefined;
          const n = typeof v === 'number' ? v : parseFloat(String(v));
          return Number.isFinite(n) ? n : undefined;
        };
        setIncomingRide({
          ride_id: data.ride_id,
          pickup_address: data.pickup_address || '',
          dropoff_address: data.dropoff_address || '',
          pickup_lat: pLat,
          pickup_lng: pLng,
          dropoff_lat: dLat,
          dropoff_lng: dLng,
          fare: String(data.fare ?? '0.00'),
          distance_km: toNum(data.distance_km),
          duration_minutes: toNum(data.duration_minutes),
          rider_name: data.rider_name || undefined,
          rider_rating: toNum(data.rider_rating),
          countdown_seconds: toNum(data.countdown_seconds),
          offer_expires_at: data.offer_expires_at || undefined,
          surge_multiplier: toNum(data.surge_multiplier),
          incentives: safeParse(data.incentives) ?? existing?.incentives,
          total_bonus: toNum(data.total_bonus) ?? existing?.total_bonus,
          quest_hint: safeParse(data.quest_hint) ?? existing?.quest_hint,
          payment_method: data.payment_method || existing?.payment_method || undefined,
          planned_route_polyline: safeParse(data.planned_route_polyline) ?? existing?.planned_route_polyline,
        });
      } else if (data?.type === 'auto_offline') {
        offerSound.stop();
        resetRideState();
        setIsOnline(false);
        showAlert(
          'You\'re now offline',
          data.message || 'You were taken offline because you missed several ride offers in a row.',
        );
      } else if (data?.type === 'ride_cancelled') {
        showToast('error', 'Ride Cancelled', 'The rider has cancelled this ride.');
        resetRideState();
      } else if (data?.type === 'subscription_expiring') {
        showAlert(
          'Spinr Pass Expiring',
          'Your Spinr Pass expires soon — renew to keep earning',
          [
            { text: 'Renew Now', onPress: () => router.push('/driver/subscription' as any) },
            { text: 'Later', style: 'cancel' },
          ]
        );
      } else if (data?.type === 'document_expiry_warning') {
        showAlert(
          'Document Expiring',
          'A document is expiring — tap to update it',
          [
            { text: 'Update Now', onPress: () => router.push('/driver/documents' as any) },
            { text: 'Later', style: 'cancel' },
          ]
        );
      } else if (data?.type) {
        console.warn('[Push] Unknown notification type — navigating to notifications list:', data.type);
        router.push('/driver/notifications' as any);
      }
    });

    return () => {
      if (typeof unsubscribe === 'function') unsubscribe();
    };
    // offerSound is now a stable object (useRideOfferSound was fixed to
    // memoize its returned { play, stop } — see that file's comment) so
    // it's safe to add here. Before that fix it was a fresh object every
    // render; naively adding it here would have re-subscribed
    // onForegroundMessage on every render, risking a missed FCM message in
    // the brief unsubscribe/resubscribe gap on this ride-offer delivery
    // path — fixed at the root instead of masked with a suppression.
  }, [isOnline, user, setIncomingRide, resetRideState, offerSound]);

  return {
    // State
    isOnline,
    connectionState,
    location,
    locationStatus,
    otpInput,
    setOtpInput,
    wsError,
    wsLatency,

    // Actions
    toggleOnline,
    openNavigation,
    uploadLocationBatch,
    refreshLocation,

    // Refs
    mapRef,
    currentRegionRef,
    wsRef,
    locationSubRef,
    countdownRef,
    reconnectTimeoutRef,
    reconnectAttemptRef,

    // Animations
    pulseAnim,
    slideUpAnim,
    fadeAnim,
  };
};

export default useDriverDashboard;
