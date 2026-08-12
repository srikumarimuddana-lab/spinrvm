import { create } from 'zustand';
import { showToast } from './toastStore';
import api, { SpinrApiError, hasAuthToken, getApiErrorMessage } from '@shared/api/client';
import { useAuthStore, registerLogoutCallback } from '@shared/store/authStore';
import { dropoffLikelyMisresolved } from '@shared/utils/bookingDistanceGuard';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { RideStatus } from '../constants/rideStatus';
import { recordNonFatal } from '../utils/crashlytics';

const ACTIVE_RIDE_KEY = '@spinr:active_ride';
const TERMINAL_STATUSES: Set<string> = new Set([RideStatus.COMPLETED, RideStatus.CANCELLED]);

function isErrorLike(e: unknown): e is { message: string } {
  return typeof e === 'object' && e !== null && 'message' in e;
}

// Format a Date's LOCAL wall-clock fields as a naive ISO-8601 string (no
// 'Z', no offset) -- e.g. "2026-11-01T01:30:00". Deliberately NOT
// `date.toISOString()`, which converts to the true UTC instant: the backend's
// DST-gap/ambiguity guard (backend/schemas.py CreateRideRequest.
// validate_scheduled_time) can only detect a spring-forward gap or a
// fall-back repeated hour from the RAW local digits the rider picked, before
// any timezone math has silently resolved (or fabricated, for a gap time)
// which real-world instant was meant -- by the time you have a true UTC
// instant that ambiguity is already gone with no record of the choice. Paired
// with `scheduled_timezone` below, which tells the backend which zone these
// digits are in; the backend converts back to a true UTC instant itself
// once it has confirmed the local time is valid and unambiguous.
function formatLocalNaiveIso(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

// Write currentRide + currentDriver to AsyncStorage. Clears key when ride is
// terminal so stale data never survives across sessions.
const _persistRide = (currentRide: Ride | null, currentDriver: Driver | null) => {
  if (!currentRide || TERMINAL_STATUSES.has(currentRide.status)) {
    AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
  } else {
    AsyncStorage.setItem(ACTIVE_RIDE_KEY, JSON.stringify({ currentRide, currentDriver })).catch(() => {});
  }
};

interface Location {
  address: string;
  lat: number;
  lng: number;
  /** Google place_id when this point came from a Places selection — lets a
   * later tap re-resolve fresh coordinates instead of replaying stored ones. */
  place_id?: string;
  /** Epoch ms when a recents entry was stored; entries expire after
   * RECENT_SEARCH_TTL_MS. */
  saved_at?: number;
}

// v2: the v1 key stored unversioned, never-expiring address+coord pairs; a
// backend defect wrote pairs whose coordinate did not match the address, and
// they replayed verbatim on every tap. Bumping the key abandons all v1 data.
const RECENT_SEARCHES_KEY = 'recent_searches_v2';
const RECENT_SEARCH_TTL_MS = 90 * 24 * 60 * 60 * 1000; // 90 days

const normalizeAddressKey = (address: string): string =>
  address.trim().toLowerCase().replace(/\s+/g, ' ');

/** Entry must be a plausible point on Earth with a non-empty address — a
 * malformed or (0,0) entry booked verbatim becomes a ride to the Gulf of
 * Guinea. */
const isValidRecentSearch = (e: unknown): e is Location => {
  if (typeof e !== 'object' || e === null) return false;
  const { address, lat, lng } = e as Record<string, unknown>;
  return (
    typeof address === 'string' && address.trim().length > 0 &&
    typeof lat === 'number' && Number.isFinite(lat) && Math.abs(lat) <= 90 &&
    typeof lng === 'number' && Number.isFinite(lng) && Math.abs(lng) <= 180 &&
    !(lat === 0 && lng === 0)
  );
};

interface VehicleType {
  id: string;
  name: string;
  description: string;
  icon: string;
  capacity: number;
  image_url?: string;
}

export interface FareLineItem {
  label: string;
  amount: number | string | null;
  type: 'ride' | 'fare' | 'fee' | 'tax' | 'tip' | 'modifier';
}

interface RideEstimate {
  vehicle_type: VehicleType;
  distance_km: number;
  duration_minutes: number;
  base_fare: string; // MoneyString: "3.50"
  distance_fare: string; // MoneyString
  time_fare: string; // MoneyString
  booking_fee: string; // MoneyString
  surge_multiplier?: number; // ratio, not money — stays number
  total_fare: string; // MoneyString
  fare_breakdown?: FareLineItem[];
  grand_total?: string; // MoneyString — includes area fees + taxes
  available: boolean;
  eta_minutes: number;
  driver_count: number;
  // WAV (wheelchair-accessible vehicle) driver count nearby — populated by
  // backend GET /rides/estimate. Used to gate the WAV toggle in ride-options.tsx
  // (Saskatchewan Transportation Act s.22). Optional because older backends
  // may not return it.
  wav_available?: number;
  // P0-4: signed token that locks the surge shown in this estimate.
  // Sent back on POST /rides so the confirmed fare matches what the
  // rider saw, even if service-area surge changes between estimate + confirm.
  estimate_token?: string;
}

export interface NearbyDriver {
  id: string;
  lat: number;
  lng: number;
  vehicle_type_id: string;
  // Resolved server-side from vehicle_types.name (Economy / Premium / Van /
  // XL) so the map can pick the matching CarMarker variant. Optional because
  // older backends may not return it.
  vehicle_type_name?: string;
  // Admin-configured marker (vehicle_types.marker_variant): standard | xl |
  // premium. Preferred over name matching when present.
  marker_variant?: string;
  vehicle_make?: string;
  vehicle_model?: string;
}

interface Driver {
  id: string;
  name: string;
  phone: string;
  photo_url: string;
  vehicle_make: string;
  vehicle_model: string;
  vehicle_color: string;
  license_plate: string;
  rating: number;
  total_rides: number;
  lat: number;
  lng: number;
  speed?: number | null;
  heading?: number | null;
}

export interface Ride {
  id: string;
  rider_id: string;
  driver_id?: string;
  vehicle_type_id: string;
  pickup_address: string;
  pickup_lat: number;
  pickup_lng: number;
  dropoff_address: string;
  dropoff_lat: number;
  dropoff_lng: number;
  distance_km: number;
  duration_minutes: number;
  base_fare: string; // MoneyString
  // Per-component fare breakdown. Optional because some legacy rides
  // may not have all components set.
  distance_fare?: string; // MoneyString
  time_fare?: string; // MoneyString
  booking_fee?: string; // MoneyString
  total_fare: string; // MoneyString
  fare_breakdown?: FareLineItem[];
  grand_total?: string; // MoneyString
  payment_method: string;
  payment_status?: string;
  card_last4?: string;
  status: string;
  pickup_otp: string;
  tip_amount?: string; // MoneyString
  corporate_account_id?: string | null;
  is_scheduled?: boolean;
  scheduled_time?: string;
  created_at: string;
  ride_code?: string;
  ride_completed_at?: string;
  share_token?: string;
  /** v2 durable route contract. Segments intentionally remain separate to avoid gap chords. */
  actual_route_segments?: unknown;
  actual_completion_point?: { latitude: number; longitude: number };
  planned_route_polyline?: [number, number][];
  route_quality?: { coverage_ratio?: number; coverage_pct?: number; missing_tail?: boolean; [key: string]: unknown };
  route_revision?: number;
  snapshot_revision?: number;
  route_snapshot_url?: string;
  route_schema_version?: number;
  route_geometry_status?: string;
  actual_duration_minutes?: number;
  /** Kilometres — GPS-measured trip distance; distance_km stays the billed basis. */
  actual_distance_km?: number;
}

interface SavedAddress {
  id: string;
  user_id: string;
  name: string;
  address: string;
  lat: number;
  lng: number;
  icon: string;
}

interface Promo {
  id: string;
  code: string;
  free_ride?: boolean;
  discount_type: string;
  discount_value: number;
  max_discount?: number;
  /** Server-computed discount amount for this promo against the current fare. */
  discount_amount?: number;
  [key: string]: unknown;
}

/** SCA two-step: create_ride returns this (instead of a Ride) when the card
 *  hold needs an on-device 3DS / Apple Pay confirm before booking can proceed. */
export interface RideRequiresAction {
  requires_action: true;
  payment_authorization: { client_secret: string; payment_intent_id: string };
}

export interface ChatMessage {
  id: string;
  ride_id: string;
  text: string;
  sender: 'rider' | 'driver';
  timestamp: string;
}

interface RideState {
  pickup: Location | null;
  dropoff: Location | null;
  stops: Location[]; // Intermediate stops
  estimates: RideEstimate[];
  nearbyDrivers: NearbyDriver[];
  selectedVehicle: VehicleType | null;
  currentRide: Ride | null;
  currentDriver: Driver | null;
  driverEtaSeconds: number | null; // road-network ETA from last WS location_update
  _lastWsDriverPositionAt: number; // epoch ms of last WS-driven position update
  // Monotonic ride-event ordering (V3, issue #11). Highest ride_status_changed
  // version applied, plus the ride it belongs to. rides.version is per-ride
  // (bumped by the migration-225 trigger), so a new ride resets the baseline.
  // Internal — only applyRideStatusFromWS reads/writes these.
  _lastEventRideId: string | null;
  _lastEventVersion: number;
  savedAddresses: SavedAddress[];
  recentSearches: Location[];
  scheduledTime: Date | null;
  scheduledRides: Ride[];
  requiresWav: boolean;
  // Whether the rider has opted into seeing WAV (wheelchair-accessible
  // vehicle) options on the booking screen. Default false so the booking
  // screen stays uncluttered for the 99% who don't need it; the WAV
  // dispatch path (backend filter, fare estimate, etc.) is unchanged.
  // Toggled from the Accessibility screen under profile.
  showWavOption: boolean;
  userLocation: { latitude: number; longitude: number } | null;
  availablePromos: Promo[];
  appliedPromo: Promo | null;
  quietMode: boolean;
  riderNotes: string;
  routePolyline: [number, number][];
  // Route coords cached across screen transitions so each phase reuses the
  // already-fetched Directions result instead of making a new API call.
  activeRideRouteCoords: { latitude: number; longitude: number }[] | null;
  activeDriverRouteCoords: { latitude: number; longitude: number }[] | null;
  // Last ETA in minutes — shown as fallback when WebSocket updates stop.
  lastEtaMin: number | null;
  isLoading: boolean;
  error: string | null;

  // Actions
  setPickup: (location: Location | null) => void;
  setDropoff: (location: Location | null) => void;
  addStop: (location: Location) => void;
  clearStops: () => void;
  removeStop: (index: number) => void;
  updateStop: (index: number, location: Location) => void;
  fetchActiveRide: () => Promise<{ active: boolean; ride: Ride } | null>;
  fetchEstimates: () => Promise<void>;
  clearEstimates: () => void;
  fetchNearbyDrivers: () => Promise<void>;
  selectVehicle: (vehicle: VehicleType) => void;
  createRide: (
    paymentMethod: string,
    corporateAccountId?: string | null,
    paymentMethodId?: string,
    preauthorizedPaymentIntentId?: string,
    opts?: { allowSamePlace?: boolean },
  ) => Promise<Ride | RideRequiresAction>;
  fetchRide: (rideId: string) => Promise<void>;
  cancelRide: (reason?: string) => Promise<void>;
  simulateDriverArrival: () => Promise<void>;
  fetchSavedAddresses: () => Promise<void>;
  addSavedAddress: (address: Omit<SavedAddress, 'id' | 'user_id'>) => Promise<void>;
  deleteSavedAddress: (id: string) => Promise<void>;
  startRide: () => Promise<void>;
  completeRide: () => Promise<Ride | undefined>;
  clearRide: () => void;
  clearError: () => void;
  setActiveRideRouteCoords: (coords: { latitude: number; longitude: number }[]) => void;
  setActiveDriverRouteCoords: (coords: { latitude: number; longitude: number }[] | null) => void;
  setLastEtaMin: (min: number) => void;
  rateRide: (rideId: string, rating: number, comment?: string, tipAmount?: number) => Promise<void>;
  hydrateActiveRide: () => Promise<void>;
  triggerEmergency: (rideId: string, latitude?: number, longitude?: number) => Promise<void>;
  addRecentSearch: (location: Location) => void;
  loadRecentSearches: () => Promise<void>;
  syncOfflineRequests: () => Promise<void>;
  clearRecentSearches: () => void;
  setScheduledTime: (time: Date | null) => void;
  setRequiresWav: (value: boolean) => void;
  setShowWavOption: (value: boolean) => void;
  setQuietMode: (v: boolean) => void;
  setRiderNotes: (v: string) => void;
  setRoutePolyline: (coords: { latitude: number; longitude: number }[]) => void;
  /**
   * Update the rider_notes for the currently-in-flight ride. Used by the
   * post-confirm "Add note for driver" chip on ride-status. Backend
   * accepts edits only while the ride is pre-pickup.
   */
  updateRideNotes: (notes: string) => Promise<void>;
  fetchScheduledRides: () => Promise<void>;
  cancelScheduledRide: (rideId: string) => Promise<void>;
  setUserLocation: (loc: { latitude: number; longitude: number } | null) => void;
  fetchAvailablePromos: (rideFare?: number, ridePortion?: number) => Promise<void>;
  applyPromo: (promo: Promo | null) => void;

  // WebSocket-driven updates (see rider-app/hooks/useRiderSocket.ts).
  updateDriverLocation: (lat: number, lng: number, speed?: number | null, heading?: number | null, etaSeconds?: number | null) => void;
  applyRideStatusFromWS: (rideId: string, status: string, extra?: Record<string, unknown>) => void;

  _clearedRideId: string | null;
  wsConnected: boolean;
  setWsConnected: (v: boolean) => void;

  // Chat
  chatMessages: ChatMessage[];
  addChatMessage: (msg: ChatMessage) => void;
  setChatMessages: (msgs: ChatMessage[]) => void;
}

export const useRideStore = create<RideState>((set, get) => ({
  pickup: null,
  dropoff: null,
  stops: [], // Init empty
  estimates: [],
  nearbyDrivers: [],
  selectedVehicle: null,
  currentRide: null,
  currentDriver: null,
  driverEtaSeconds: null,
  _lastWsDriverPositionAt: 0,
  // Monotonic ride-event ordering baseline (V3). -1 so version 0 (a ride's
  // first bump) is strictly greater and applies.
  _lastEventRideId: null,
  _lastEventVersion: -1,
  chatMessages: [],
  _clearedRideId: null,
  wsConnected: false,
  savedAddresses: [],
  recentSearches: [],
  availablePromos: [],
  appliedPromo: null,
  requiresWav: false,
  showWavOption: false,
  quietMode: false,
  riderNotes: '',
  routePolyline: [],
  activeRideRouteCoords: null,
  activeDriverRouteCoords: null,
  lastEtaMin: null,
  scheduledTime: null,
  scheduledRides: [],
  userLocation: null,
  isLoading: false,
  error: null,

  // Changing pickup/dropoff/stops invalidates the current route, so the
  // previously-fetched estimates, the cached list of available promos, and
  // the auto-applied promo all need to clear. Otherwise the rider sees the
  // OLD promo's discount applied to the NEW route's fare until the next
  // estimates poll lands — visually wrong and economically wrong (the
  // server-side validation rejects the booking but the screen looks
  // committed). fetchAvailablePromos re-runs from the ride-options effect
  // and will auto-apply the best eligible promo for the new route.
  // routePolyline is cleared alongside estimates here: changing any waypoint
  // invalidates the drawn route, and a stale polyline would otherwise linger
  // on the ride-options map (e.g. the previous destination's route trace still
  // showing after the rider goes back and searches a new one). The next
  // estimate fetch repopulates it for the new route.
  setPickup: (location) => set({
    pickup: location,
    estimates: [],
    availablePromos: [],
    appliedPromo: null,
    routePolyline: [],
  }),
  setDropoff: (location) => set({
    dropoff: location,
    estimates: [],
    availablePromos: [],
    appliedPromo: null,
    routePolyline: [],
  }),
  setUserLocation: (loc) => set({ userLocation: loc }),

  addStop: (location) => set((state) => ({
    stops: [...state.stops, location],
    estimates: [],
    availablePromos: [],
    appliedPromo: null,
    routePolyline: [],
  })),
  // Same invalidation block as addStop/removeStop. Stops were never reset
  // anywhere, so a leftover manual-flow stop silently inflated every later
  // /rides/estimate and /rides payload (the AI booking card sends whatever
  // is in the store).
  clearStops: () => set({
    stops: [],
    estimates: [],
    availablePromos: [],
    appliedPromo: null,
    routePolyline: [],
  }),
  removeStop: (index) => set((state) => ({
    stops: state.stops.filter((_, i) => i !== index),
    estimates: [],
    availablePromos: [],
    appliedPromo: null,
    routePolyline: [],
  })),
  updateStop: (index, location) => set((state) => {
    const newStops = [...state.stops];
    newStops[index] = location;
    return {
      stops: newStops,
      estimates: [],
      availablePromos: [],
      appliedPromo: null,
      routePolyline: [],
    };
  }),

  fetchActiveRide: async () => {
    try {
      const response = await api.get<{ active?: boolean; ride?: Ride & { driver?: Driver | null } }>('/rides/active');
      if (response.data?.active && response.data.ride) {
        const ride = response.data.ride;
        // The rider just cancelled this ride locally; the server may not have
        // committed the cancel yet, so /rides/active can still report it as
        // active for a moment (read-after-write lag). Treat it as inactive so
        // the home useFocusEffect doesn't re-push into the searching screen,
        // which restarted the cancel toast (the flicker). _clearedRideId is
        // reset to null when the rider books a new ride.
        if (get()._clearedRideId === ride.id) {
          return null;
        }
        const driver = ride.driver ?? null;
        const rideWithoutDriver: Ride = (({ driver: _d, ...rest }) => rest)(ride);
        set({ currentRide: rideWithoutDriver, currentDriver: driver, _clearedRideId: null });
        _persistRide(rideWithoutDriver, driver);
        return { active: true, ride: rideWithoutDriver };
      }
      // No active ride on server — clear any stale local state
      if (get().currentRide) get().clearRide();
      return null;
    } catch (err: unknown) {
      if (isErrorLike(err) && (err as { response?: { status?: number } }).response?.status === 404) {
        if (get().currentRide) get().clearRide();
      }
      return null;
    }
  },

  // Wipe the previous route's quote so the next estimate fetch starts from a
  // clean slate. Called when the rider taps "Search Ride" so a recompute is
  // unconditional — no stale price, route line, or promo can survive into the
  // new search even if the pickup/dropoff coordinates happen to be unchanged.
  clearEstimates: () => set({
    estimates: [],
    routePolyline: [],
    availablePromos: [],
    appliedPromo: null,
    isLoading: true,
    error: null,
  }),

  fetchEstimates: async () => {
    const { pickup, dropoff, stops, estimates: existing } = get();
    if (!pickup || !dropoff) return;
    // Drop unfilled stop rows (the rider added a stop field but hasn't picked a
    // place yet — lat/lng are still 0). Sending those would geofence-reject the
    // quote or bend the route to (0,0).
    const validStops = stops.filter((s) => s.lat && s.lng);

    try {
      // Only show loading skeleton on the initial fetch. Subsequent
      // auto-refreshes update silently so vehicle cards don't flash.
      if (existing.length === 0) {
        set({ isLoading: true, error: null });
      }
      const response = await api.post<{ estimates: RideEstimate[]; route_polyline?: number[][] } | RideEstimate[]>('/rides/estimate', {
        pickup_lat: pickup.lat,
        pickup_lng: pickup.lng,
        dropoff_lat: dropoff.lat,
        dropoff_lng: dropoff.lng,
        stops: validStops,
      });
      // Backend returns {estimates, route_polyline} — handle both old (array) and new (object) shapes.
      const raw = response.data;
      const fresh = Array.isArray(raw) ? raw as RideEstimate[] : (raw?.estimates ?? []) as RideEstimate[];
      const polyline = !Array.isArray(raw) && raw?.route_polyline
        ? raw.route_polyline.map((p: number[]) => [p[0], p[1]] as [number, number])
        : undefined;
      // Never replace a populated list with an empty one during a background
      // refresh — the rider should always see vehicle types even when the
      // backend returns none transiently (cache miss, rate-limit, etc.).
      if (fresh.length > 0 || existing.length === 0) {
        set({ estimates: fresh, isLoading: false, ...(polyline ? { routePolyline: polyline } : {}) });
      } else {
        set({ isLoading: false, ...(polyline ? { routePolyline: polyline } : {}) });
      }
    } catch (error: unknown) {
      console.error('fetchEstimates error:', error);
      set({ isLoading: false, error: getApiErrorMessage(error, 'Failed to fetch estimates') });
    }
  },

  fetchNearbyDrivers: async () => {
    const { pickup, selectedVehicle } = get();
    if (!pickup) return;
    try {
      let url = `/drivers/nearby?lat=${pickup.lat}&lng=${pickup.lng}`;
      if (selectedVehicle?.id) {
        url += `&vehicle_type=${selectedVehicle.id}`;
      }
      const response = await api.get<NearbyDriver[]>(url);
      set({ nearbyDrivers: response.data as NearbyDriver[] });
    } catch (error) {
      console.log('Error fetching nearby drivers', error);
    }
  },

  selectVehicle: (vehicle) => set({ selectedVehicle: vehicle }),

  fetchAvailablePromos: async (rideFare?: number, ridePortion?: number) => {
    try {
      const fare = rideFare ?? 0;
      const pickup = get().pickup;
      const coords = pickup ? `&pickup_lat=${pickup.lat}&pickup_lng=${pickup.lng}` : '';
      const portionParam = ridePortion !== undefined ? `&ride_portion=${ridePortion}` : '';
      const response = await api.get<Promo[]>(`/promo/available?ride_fare=${fare}${portionParam}${coords}`);
      const promos = response.data || [];

      // Re-validate any promo that's currently applied against the freshly-
      // returned list. If the user picked a new destination (different
      // service area, fare dropped below min_ride_fare, etc.) the old promo
      // may no longer apply — drop it and let the auto-pick below choose
      // a replacement.
      const current = get().appliedPromo;
      let stillValid: Promo | null = null;
      if (current) {
        const match = promos.find(
          (p: any) =>
            (current.promo_id && p.promo_id === current.promo_id) ||
            (current.code && p.code === current.code),
        );
        if (match && (match as any).eligible !== false) {
          // Refresh the discount_amount to the new route's value.
          stillValid = match;
        }
      }

      let nextApplied: Promo | null = stillValid;
      // Auto-apply best eligible promo if no valid one is currently applied.
      if (!nextApplied && promos.length > 0) {
        nextApplied = promos.find((p: any) => p.eligible !== false) ?? null;
      }
      set({ availablePromos: promos, appliedPromo: nextApplied });
    } catch (error) {
      console.log('Error fetching promos:', error);
      set({ availablePromos: [] });
    }
  },

  applyPromo: (promo) => set({ appliedPromo: promo }),

  // Sync offline queued requests when back online.
  // Queue entry shape: { id, type, rideId, data, retryCount, timestamp }
  // Supported types: create_ride | cancel_ride | rate_ride | tip | emergency
  syncOfflineRequests: async () => {
    try {
      const queueStr = await AsyncStorage.getItem('offline_queue');
      if (!queueStr) return;

      // Corrupted AsyncStorage (partial write, app upgrade migration miss,
      // device disk full mid-write) used to crash the entire sync flow.
      // Treat a bad payload as an empty queue and discard it.
      type OfflineRequest = {
        id: string;
        type: 'create_ride' | 'cancel_ride' | 'rate_ride' | 'tip' | 'emergency';
        rideId?: string;
        data?: Record<string, unknown>;
        retryCount?: number;
        timestamp?: number;
      };
      let queue: OfflineRequest[];
      try {
        const parsed = JSON.parse(queueStr);
        queue = Array.isArray(parsed) ? (parsed as OfflineRequest[]) : [];
      } catch (parseErr) {
        if (__DEV__) console.warn('[offline_queue] discarding corrupted queue:', parseErr);
        await AsyncStorage.removeItem('offline_queue');
        showToast(
          'Offline Actions Lost',
          'Some queued actions could not be recovered. Please rebook if needed.',
          'warning',
          6000,
        );
        return;
      }
      if (queue.length === 0) return;

      const successfulSyncs: string[] = [];
      const failedPermanently: string[] = [];

      for (const request of queue) {
        try {
          switch (request.type) {
            case 'create_ride':
              await api.post('/rides', request.data);
              break;
            case 'cancel_ride':
              if (request.rideId) await api.post(`/rides/${request.rideId}/cancel`);
              break;
            case 'rate_ride':
              if (request.rideId) await api.post(`/rides/${request.rideId}/rate`, request.data);
              break;
            case 'tip':
              if (request.rideId) await api.post(`/rides/${request.rideId}/tip`, request.data);
              break;
            case 'emergency':
              if (request.rideId) await api.post(`/rides/${request.rideId}/emergency`, request.data);
              break;
          }
          successfulSyncs.push(request.id);
        } catch {
          request.retryCount = (request.retryCount || 0) + 1;
          if (request.retryCount >= 3) {
            failedPermanently.push(request.id);
          }
        }
      }

      const idsToRemove = new Set([...successfulSyncs, ...failedPermanently]);
      const updatedQueue = queue.filter(req => !idsToRemove.has(req.id));
      await AsyncStorage.setItem('offline_queue', JSON.stringify(updatedQueue));

      if (failedPermanently.length > 0) {
        showToast(
          'Sync Failed',
          `${failedPermanently.length} offline action(s) could not be synced.`,
          'warning',
          5000,
        );
      }
      if (successfulSyncs.length > 0) {
        console.log(`[Offline] Synced ${successfulSyncs.length} requests`);
      }
    } catch (error) {
      console.error('Failed to sync offline requests:', error);
    }
  },

  createRide: async (paymentMethod, corporateAccountId, paymentMethodId, preauthorizedPaymentIntentId, opts) => {
    const { pickup, dropoff, selectedVehicle, stops, scheduledTime, estimates, requiresWav, quietMode, riderNotes, routePolyline } = get();
    if (!pickup || !dropoff || !selectedVehicle) {
      throw new Error('Missing ride details');
    }
    // Guard a mis-resolved dropoff (a stale/wrong saved-place coordinate that
    // lands on the pickup while the address differs) BEFORE creating a ride
    // priced on a bogus coordinate. All booking entry points funnel here.
    // allowSamePlace: the rider already explicitly confirmed a same-place trip
    // (assistant guardrail) — honour that one confirmed booking.
    if (!opts?.allowSamePlace && dropoffLikelyMisresolved(pickup, dropoff)) {
      throw new Error(
        "Your destination looks too close to your pickup. Please re-select it so we price the trip correctly.",
      );
    }
    if (get().currentRide) {
      const serverCheck = await get().fetchActiveRide();
      if (serverCheck?.active) {
        throw new Error('A ride is already active');
      }
    }

    try {
      // Clear any cached route coords from the previous ride so the new
      // ride's map screens always fetch a fresh route rather than skipping
      // MapViewDirections because the cache is non-null.
      set({
        isLoading: true,
        error: null,
        activeRideRouteCoords: null,
        activeDriverRouteCoords: null,
        lastEtaMin: null,
      });
      // P0-4: echo back the estimate_token from the currently-selected
      // vehicle's estimate so the backend can reuse the surge we showed
      // in the UI (instead of re-reading current surge and bait-and-switching).
      const selectedEstimate = estimates.find(
        (e) => e.vehicle_type?.id === selectedVehicle.id
      );
      const rideData: Record<string, unknown> = {
        vehicle_type_id: selectedVehicle.id,
        pickup_address: pickup.address,
        pickup_lat: pickup.lat,
        pickup_lng: pickup.lng,
        dropoff_address: dropoff.address,
        dropoff_lat: dropoff.lat,
        dropoff_lng: dropoff.lng,
        // Only send stops the rider actually resolved to a place — never the
        // (0,0) placeholders of empty stop fields.
        stops: stops.filter((s) => s.lat && s.lng),
        payment_method: paymentMethod,
        payment_method_id: paymentMethodId ?? null,
        // SCA two-step: a hold the app already confirmed on-device after a prior
        // create_ride returned requires_action. Backend verifies + attaches it.
        preauthorized_payment_intent_id: preauthorizedPaymentIntentId ?? null,
        corporate_account_id: corporateAccountId || null,
        // A corporate booking must be flagged work_profile so the backend
        // reclassifies it to company_allowance and settles against the COMPANY
        // (settle_corporate) instead of silently charging the rider's personal
        // card. Without this flag the ride stays payment_method='card' and bills
        // the rider — corporate billing never reaches the company account.
        work_profile: corporateAccountId ? true : null,
        estimate_token: selectedEstimate?.estimate_token,
        requires_wav: requiresWav,
        quiet_mode: quietMode,
        rider_notes: riderNotes || null,
        promo_code: get().appliedPromo?.code || null,
        planned_route_polyline: routePolyline.length >= 2 ? routePolyline : null,
        created_at: new Date().toISOString(),
      };

      if (scheduledTime) {
        rideData.is_scheduled = true;
        // Local wall-clock digits + zone name, NOT scheduledTime.toISOString()
        // -- see formatLocalNaiveIso's docstring for why. scheduled_timezone
        // defaults to the device's own IANA zone: correct for the normal
        // case (rider books a pickup where they currently are), and the only
        // part of Spinr's service area where this actually matters
        // (Lloydminster observes DST; the rest of Saskatchewan does not).
        rideData.scheduled_time = formatLocalNaiveIso(scheduledTime);
        rideData.scheduled_timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      }

      const userId = useAuthStore.getState().user?.id ?? 'anon';
      const idempotencyKey = `ride-${userId}-${Date.now()}`;
      const response = await api.post<Ride | RideRequiresAction>('/rides', rideData, {
        headers: { 'Idempotency-Key': idempotencyKey },
      });
      // SCA two-step, first leg: the card hold needs an on-device confirm. No
      // ride was created — surface it to the caller (it runs the Stripe sheet
      // then re-books with the confirmed PI). Do NOT set currentRide.
      if ((response.data as RideRequiresAction).requires_action) {
        set({ isLoading: false });
        return response.data as RideRequiresAction;
      }
      const ride = response.data as Ride;
      set({ currentRide: ride, isLoading: false, scheduledTime: null, requiresWav: false, quietMode: false, riderNotes: '', routePolyline: [], appliedPromo: null, _clearedRideId: null });
      _persistRide(ride, null);
      return ride;
    } catch (error: unknown) {
      recordNonFatal(error, { store: 'rideStore', action: 'createRide' });
      set({ isLoading: false, error: getApiErrorMessage(error, 'Failed to create ride') });
      throw error;
    }
  },

  fetchRide: async (rideId) => {
    try {
      // Only set isLoading on first fetch (when no ride data yet)
      if (!get().currentRide) {
        set({ isLoading: true });
      }
      const response = await api.get<Ride & { driver?: Driver | null }>(`/rides/${rideId}`);
      // If clearRide() ran while this fetch was in-flight, discard the
      // response so we don't re-populate the store with the old ride.
      if (get()._clearedRideId === rideId) {
        set({ isLoading: false });
        return;
      }
      // Don't overwrite a DIFFERENT ride's data. This prevents stale
      // in-flight fetches (from WS reconnect or poll intervals) from
      // clobbering a newly created ride with old ride data — the root
      // cause of the "second ride loops between searching and home" bug.
      const current = get().currentRide;
      if (current && current.id !== rideId) {
        set({ isLoading: false });
        return;
      }
      const ride = response.data;
      let driver = ride.driver ?? null;
      // R-P2-29: if a WS position update arrived within the last 10 s, the DB
      // value is stale — preserve the WS-sourced lat/lng so the map doesn't jump.
      const { _lastWsDriverPositionAt, currentDriver } = get();
      if (driver && currentDriver && Date.now() - _lastWsDriverPositionAt < 10_000) {
        driver = { ...driver, lat: currentDriver.lat, lng: currentDriver.lng };
      }
      set({ currentRide: ride, currentDriver: driver, isLoading: false });
      _persistRide(ride, driver);
    } catch (error: unknown) {
      console.log('fetchRide error:', error);
      // Don't clear currentRide on poll errors — keep showing last known state
      set({ isLoading: false });
    }
  },

  cancelRide: async (reason?: string) => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      set({ isLoading: true });
      // Send the reason in the body (not the URL) so a free-text note doesn't
      // leak into proxy/access logs. Omit the body entirely when there's no
      // reason so the call stays a plain single-arg POST.
      const trimmed = reason?.trim();
      if (trimmed) {
        await api.post(`/rides/${currentRide.id}/cancel`, { reason: trimmed });
      } else {
        await api.post(`/rides/${currentRide.id}/cancel`);
      }
      // Record the cancelled ride as locally-cleared so (a) in-flight fetchRide
      // responses for it are discarded and (b) the WS ride_cancelled echo the
      // server sends back is recognised as already-handled and skipped — without
      // this the echo re-toasts + re-navigates, restarting the toast animation
      // (the cancel-during-search flicker).
      set({ currentRide: null, currentDriver: null, isLoading: false, _clearedRideId: currentRide.id });
      AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
    } catch (error: unknown) {
      // 409 with a terminal current_status means the backend already cancelled/
      // completed the ride (offer timeout, system cancel, etc.) while the local
      // store still showed it as active. Clear local state so the rider can
      // navigate home — the ride is already done on the server.
      if (
        error instanceof SpinrApiError &&
        error.status === 409 &&
        TERMINAL_STATUSES.has(String(error.details?.current_status ?? ''))
      ) {
        set({ currentRide: null, currentDriver: null, isLoading: false, _clearedRideId: currentRide.id });
        AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
        return;
      }
      recordNonFatal(error, { store: 'rideStore', action: 'cancelRide' });
      set({ isLoading: false, error: getApiErrorMessage(error, 'Failed to cancel ride') });
      throw error;
    }
  },

  simulateDriverArrival: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      const response = await api.post<{ pickup_otp?: string }>(`/rides/${currentRide.id}/simulate-arrival`);
      set({
        currentRide: { ...currentRide, status: RideStatus.DRIVER_ARRIVED, pickup_otp: response.data.pickup_otp ?? currentRide.pickup_otp },
      });
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Failed to simulate arrival') });
    }
  },

  startRide: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      await api.post(`/rides/${currentRide.id}/start`);
      set({
        currentRide: { ...currentRide, status: RideStatus.IN_PROGRESS },
      });
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Failed to start ride') });
    }
  },

  completeRide: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      const response = await api.post<Ride>(`/rides/${currentRide.id}/complete`);
      set({
        currentRide: response.data,
        activeRideRouteCoords: null,
        activeDriverRouteCoords: null,
        lastEtaMin: null,
      });
      AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
      return response.data;
    } catch (error: unknown) {
      recordNonFatal(error, { store: 'rideStore', action: 'completeRide' });
      set({ error: getApiErrorMessage(error, 'Failed to complete ride') });
    }
  },

  rateRide: async (rideId: string, rating: number, comment?: string, tipAmount?: number) => {
    try {
      await api.post(`/rides/${rideId}/rate`, {
        rating,
        comment,
        tip_amount: tipAmount || 0,
      });
    } catch (error: unknown) {
      recordNonFatal(error, { store: 'rideStore', action: 'rateRide' });
      set({ error: getApiErrorMessage(error, 'Failed to rate ride') });
      throw error;
    }
  },

  triggerEmergency: async (rideId: string, latitude?: number, longitude?: number) => {
    const payload = {
      message: 'Emergency assistance requested via app button',
      latitude,
      longitude,
    };
    const MAX_ATTEMPTS = 3;
    let lastError: unknown;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        await api.post(`/rides/${rideId}/emergency`, payload);
        return; // success
      } catch (error: unknown) {
        lastError = error;
        if (attempt < MAX_ATTEMPTS) {
          await new Promise(resolve => setTimeout(resolve, 1000 * attempt));
        }
      }
    }
    // All attempts failed — rethrow so SOSButton can set backendOk=false and
    // show its own "Alert Not Sent" UI with the 911 prompt.
    throw lastError;
  },

  fetchSavedAddresses: async () => {
    try {
      const response = await api.get<SavedAddress[]>('/addresses');
      set({ savedAddresses: response.data as SavedAddress[] });
    } catch (err: unknown) {
      // PIPEDA: never spread the raw error body into logs — backend error
      // payloads can contain saved-address strings or user identifiers.
      const e = err as { code?: unknown; status?: unknown } | undefined;
      console.error('Error fetching addresses', { code: e?.code, status: e?.status });
    }
  },

  addSavedAddress: async (address) => {
    try {
      const response = await api.post<SavedAddress>('/addresses', address);
      set({ savedAddresses: [...get().savedAddresses, response.data as SavedAddress] });
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Failed to add address') });
      throw error;
    }
  },

  deleteSavedAddress: async (id) => {
    try {
      await api.delete(`/addresses/${id}`);
      set({ savedAddresses: get().savedAddresses.filter((a) => a.id !== id) });
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Failed to delete address') });
    }
  },

  setWsConnected: (v) => set({ wsConnected: v }),

  addChatMessage: (msg) => {
    const { chatMessages } = get();
    // Deduplicate by id (WS + poll can deliver the same message).
    if (chatMessages.some((m) => m.id === msg.id)) return;
    set({ chatMessages: [...chatMessages, msg] });
  },

  setChatMessages: (msgs) => set({ chatMessages: msgs }),

  // Clear ONLY the live-ride fields so the rider can immediately re-book
  // using the same trip inputs after a cancellation. We deliberately keep
  // pickup / dropoff / estimates / selectedVehicle / scheduledTime because
  // those represent "what the user wants to do next", not "the in-flight
  // trip". Wiping them caused the post-cancel flow to land on ride-options
  // with no pickup → stuck loading → bounce back to home.
  //
  // _clearedRideId: when clearRide runs, we record which ride was cleared
  // so that in-flight fetchRide calls for that ride are ignored. Without
  // this, the race (clearRide → fetchRide response arrives → currentRide
  // re-populated) traps the rider on ride-completed after paying.
  clearRide: () => {
    // Fall back to the existing _clearedRideId when currentRide is already null:
    // performCancel() calls cancelRide() (which nulls currentRide and records
    // _clearedRideId) and THEN clearRide(), so without this fallback the second
    // call would reset _clearedRideId to null and re-enable the duplicate WS
    // ride_cancelled handling we're suppressing.
    const clearedId = get().currentRide?.id ?? get()._clearedRideId;
    set({
      currentRide: null,
      currentDriver: null,
      chatMessages: [],
      error: null,
      _clearedRideId: clearedId ?? null,
      activeRideRouteCoords: null,
      activeDriverRouteCoords: null,
      lastEtaMin: null,
    });
    AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
  },

  clearError: () => set({ error: null }),

  setActiveRideRouteCoords: (coords) => set({ activeRideRouteCoords: coords }),
  setActiveDriverRouteCoords: (coords) => set({ activeDriverRouteCoords: coords }),
  setLastEtaMin: (min) => set({ lastEtaMin: min }),

  addRecentSearch: (location) => {
    const { recentSearches } = get();
    // Dedupe on normalized address so "4500 Gordon Rd" and "4500 gordon rd "
    // collapse — the old byte-exact match let differently-formatted copies of
    // one place accumulate, and a stale copy could outlive its correction.
    const key = normalizeAddressKey(location.address);
    const filtered = recentSearches.filter(s => normalizeAddressKey(s.address) !== key);
    const updated = [{ ...location, saved_at: Date.now() }, ...filtered].slice(0, 10);
    set({ recentSearches: updated });
    AsyncStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(updated)).catch(() => { });
  },

  loadRecentSearches: async () => {
    try {
      // Versioned key: v1 entries carried address+coord pairs written by code
      // that could bind a wrong coordinate to a correct address string (and a
      // backend bug did exactly that — the pair then replayed on every tap,
      // forever, with no expiry and no re-validation). Abandoning the old key
      // purges any poisoned pair in the wild; the one-time cost is an empty
      // recents list.
      const stored = await AsyncStorage.getItem(RECENT_SEARCHES_KEY);
      if (stored) {
        const cutoff = Date.now() - RECENT_SEARCH_TTL_MS;
        const entries = (JSON.parse(stored) as unknown[])
          .filter(isValidRecentSearch)
          .filter((e) => (e.saved_at ?? 0) >= cutoff);
        set({ recentSearches: entries });
      }
      // Also hydrate the Accessibility setting at the same time — the
      // home screen calls loadRecentSearches on mount, so this is the
      // earliest moment we can populate it without adding a new effect.
      const showWav = await AsyncStorage.getItem('rider_show_wav_option');
      if (showWav === '1') set({ showWavOption: true });
    } catch { }
  },

  clearRecentSearches: () => {
    set({ recentSearches: [] });
    AsyncStorage.removeItem(RECENT_SEARCHES_KEY).catch(() => { });
  },

  setScheduledTime: (time) => set({ scheduledTime: time }),
  setRequiresWav: (value) => set({ requiresWav: value }),
  setShowWavOption: (value) => {
    set({ showWavOption: value });
    AsyncStorage.setItem('rider_show_wav_option', value ? '1' : '0').catch(() => { });
    // If the user just turned the option off, also clear any in-flight
    // WAV selection so we don't keep sending requires_wav=true silently.
    if (!value) set({ requiresWav: false });
  },
  setQuietMode: (v) => set({ quietMode: v }),
  setRiderNotes: (v) => set({ riderNotes: v }),
  setRoutePolyline: (coords) => set({
    routePolyline: coords.map(c => [c.latitude, c.longitude] as [number, number]),
  }),

  updateRideNotes: async (notes) => {
    const { currentRide } = get();
    if (!currentRide?.id) throw new Error('No active ride to update');
    const response = await api.patch<{ success: boolean; notes: string | null }>(
      `/rides/${currentRide.id}/notes`,
      { notes },
    );
    // Optimistic in-memory update so the chip's preview text updates
    // immediately without waiting for the next ride fetch.
    set({ currentRide: { ...currentRide, rider_notes: response.data?.notes ?? null } as Ride });
  },

  fetchScheduledRides: async () => {
    try {
      const response = await api.get<Ride[]>('/rides/scheduled');
      set({ scheduledRides: response.data as Ride[] });
    } catch (error: unknown) {
      console.log('Error fetching scheduled rides:', error);
    }
  },

  cancelScheduledRide: async (rideId) => {
    try {
      await api.delete(`/rides/scheduled/${rideId}`);
      set((state) => ({
        scheduledRides: state.scheduledRides.filter((r) => r.id !== rideId),
      }));
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Failed to cancel scheduled ride') });
    }
  },

  // ── WebSocket-driven updates ────────────────────────────────────

  updateDriverLocation: (lat, lng, speed, heading, etaSeconds) => {
    const driver = get().currentDriver;
    if (!driver) return;
    // Only update the coordinate fields — leave everything else (name,
    // rating, vehicle info) untouched.
    const updated = {
      ...driver,
      lat,
      lng,
      ...(speed !== null && speed !== undefined ? { speed } : {}),
      ...(heading !== null && heading !== undefined ? { heading } : {}),
    };
    // R-P2-29: record timestamp so fetchRide doesn't overwrite this WS position.
    // Also store the road-network ETA so the driver-arriving screen can show
    // a live countdown instead of a static default.
    set({
      currentDriver: updated,
      _lastWsDriverPositionAt: Date.now(),
      ...(etaSeconds !== null && etaSeconds !== undefined ? { driverEtaSeconds: etaSeconds } : {}),
    });
    _persistRide(get().currentRide, updated);
  },

  applyRideStatusFromWS: (rideId, status, extra) => {
    const { currentRide, currentDriver } = get();
    if (!currentRide || currentRide.id !== rideId) return;

    // Drop stale / out-of-order events by the server-authoritative monotonic
    // ride version (V3, issue #11) — e.g. a delayed driver_arrived arriving
    // after in_progress, or a reconnect-replay duplicate. rides.version
    // (migration-225 trigger) is stamped into the payload and forwarded here in
    // `extra`. Events without a numeric version (un-migrated backend, or a
    // specific event type that doesn't carry one) always apply and leave the
    // baseline untouched, so this is behaviour-preserving. Versions are
    // per-ride; a different ride resets the baseline (single-slot tracker — a
    // driver/rider has one active ride at a time, so events for two rides never
    // interleave here, and the side-effects below are idempotent regardless).
    const rawVersion = extra?.version;
    if (typeof rawVersion === 'number') {
      const { _lastEventRideId, _lastEventVersion } = get();
      if (_lastEventRideId === rideId && rawVersion <= _lastEventVersion) {
        return; // stale or duplicate — drop without mutating ride state
      }
      set({ _lastEventRideId: rideId, _lastEventVersion: rawVersion });
    }

    // Apply the status transition and any extra fields (like total_fare
    // on ride_completed). This provides an instant in-app state change
    // while the next poll (reduced to 15 s via the WS fallback) fills
    // in any remaining details the WS message doesn't carry.
    const updated = { ...currentRide, status, ...(extra || {}) };
    // Clear currentDriver and driverEtaSeconds on terminal states so stale
    // driver location data doesn't persist into the next booking session.
    // The receipt screen reads driver info from currentRide.driver, not
    // currentDriver, so clearing here doesn't break the post-trip flow.
    if (TERMINAL_STATUSES.has(status)) {
      set({
        currentRide: updated,
        currentDriver: null,
        driverEtaSeconds: null,
        activeRideRouteCoords: null,
        activeDriverRouteCoords: null,
        lastEtaMin: null,
      });
      _persistRide(updated, null);
    } else {
      set({ currentRide: updated });
      _persistRide(updated, currentDriver);
    }
  },

  // ── Offline hydration ────────────────────────────────────────────
  // Called once on app mount (before fetchActiveRide). Restores the last
  // known active ride from AsyncStorage so the UI is immediately populated
  // even before the API responds (or when the device is offline).
  hydrateActiveRide: async () => {
    try {
      const raw = await AsyncStorage.getItem(ACTIVE_RIDE_KEY);
      if (!raw) return;
      const { currentRide: stored, currentDriver } = JSON.parse(raw);
      if (!stored || TERMINAL_STATUSES.has(stored.status)) {
        await AsyncStorage.removeItem(ACTIVE_RIDE_KEY);
        return;
      }
      // Validate against backend — if the ride completed or was cancelled
      // while the app was closed the stored data would be stale.
      // Skip if no auth token yet — avoids 401 → double refresh → session kill.
      if (!hasAuthToken()) {
        if (!get().currentRide) {
          set({ currentRide: stored, currentDriver: currentDriver || null });
        }
        return;
      }
      try {
        const live = await api.get<{ active?: boolean; ride?: Ride & { driver?: Driver | null } }>('/rides/active');
        if (!live.data?.active || live.data.ride?.id !== stored.id) {
          await AsyncStorage.removeItem(ACTIVE_RIDE_KEY);
          return; // stale — do not route
        }
        const liveRide = live.data.ride;
        const liveDriver = liveRide?.driver ?? currentDriver ?? null;
        if (!get().currentRide) {
          set({ currentRide: liveRide, currentDriver: liveDriver });
        }
      } catch {
        // Offline or server error — restore from cache optimistically but
        // fetchActiveRide on mount will correct any stale state.
        if (!get().currentRide) {
          set({ currentRide: stored, currentDriver: currentDriver || null });
        }
      }
    } catch {
      AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
    }
  },
}));

// Register a logout callback so that when authStore.logout() fires, all
// per-session ride state is wiped. This prevents ghost data (currentRide,
// currentDriver, chatMessages, estimates) from leaking to the next user who
// logs in on the same device.
registerLogoutCallback(() => {
  useRideStore.setState({
    currentRide: null,
    currentDriver: null,
    driverEtaSeconds: null,
    chatMessages: [],
    estimates: [],
    nearbyDrivers: [],
    appliedPromo: null,
    error: null,
    activeRideRouteCoords: null,
    activeDriverRouteCoords: null,
    lastEtaMin: null,
    // Recents are per-person, not per-device: the next account on this phone
    // must not inherit (or book to) the previous rider's destinations.
    recentSearches: [],
  });
  AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
  AsyncStorage.removeItem(RECENT_SEARCHES_KEY).catch(() => {});
});
