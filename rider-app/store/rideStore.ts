import { create } from 'zustand';
import { Linking } from 'react-native';
import { globalAlert } from './alertStore';
import api, { SpinrApiError } from '@shared/api/client';
import { useAuthStore, registerLogoutCallback } from '@shared/store/authStore';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { RideStatus } from '../constants/rideStatus';
import { recordNonFatal } from '../utils/crashlytics';

const ACTIVE_RIDE_KEY = '@spinr:active_ride';
const TERMINAL_STATUSES: Set<string> = new Set([RideStatus.COMPLETED, RideStatus.CANCELLED, RideStatus.FAILED]);

function isErrorLike(e: unknown): e is { message: string } {
  return typeof e === 'object' && e !== null && 'message' in e;
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
}

interface VehicleType {
  id: string;
  name: string;
  description: string;
  icon: string;
  capacity: number;
  image_url?: string;
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

interface Ride {
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
  // Per-component fare breakdown (PR #664 stringified Decimal in API
  // response). Optional because some legacy / partially-migrated rides
  // may not have all components set.
  distance_fare?: string; // MoneyString
  time_fare?: string; // MoneyString
  booking_fee?: string; // MoneyString
  total_fare: string; // MoneyString
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
  discount_type: string;
  discount_value: number;
  [key: string]: unknown;
}

export interface ChatMessage {
  id: string;
  text: string;
  sender: string;
  [key: string]: unknown;
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
  isLoading: boolean;
  error: string | null;

  // Actions
  setPickup: (location: Location | null) => void;
  setDropoff: (location: Location | null) => void;
  addStop: (location: Location) => void;
  removeStop: (index: number) => void;
  updateStop: (index: number, location: Location) => void;
  fetchActiveRide: () => Promise<{ active: boolean; ride: Ride } | null>;
  fetchEstimates: () => Promise<void>;
  fetchNearbyDrivers: () => Promise<void>;
  selectVehicle: (vehicle: VehicleType) => void;
  createRide: (paymentMethod: string, corporateAccountId?: string | null, paymentMethodId?: string) => Promise<Ride>;
  fetchRide: (rideId: string) => Promise<void>;
  cancelRide: () => Promise<void>;
  simulateDriverArrival: () => Promise<void>;
  fetchSavedAddresses: () => Promise<void>;
  addSavedAddress: (address: Omit<SavedAddress, 'id' | 'user_id'>) => Promise<void>;
  deleteSavedAddress: (id: string) => Promise<void>;
  startRide: () => Promise<void>;
  completeRide: () => Promise<Ride | undefined>;
  clearRide: () => void;
  clearError: () => void;
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
  /**
   * Update the rider_notes for the currently-in-flight ride. Used by the
   * post-confirm "Add note for driver" chip on ride-status. Backend
   * accepts edits only while the ride is pre-pickup.
   */
  updateRideNotes: (notes: string) => Promise<void>;
  fetchScheduledRides: () => Promise<void>;
  cancelScheduledRide: (rideId: string) => Promise<void>;
  setUserLocation: (loc: { latitude: number; longitude: number } | null) => void;
  fetchAvailablePromos: (rideFare?: number) => Promise<void>;
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
  scheduledTime: null,
  scheduledRides: [],
  userLocation: null,
  isLoading: false,
  error: null,

  setPickup: (location) => set({ pickup: location }),
  setDropoff: (location) => set({ dropoff: location }),
  setUserLocation: (loc) => set({ userLocation: loc }),

  addStop: (location) => set((state) => ({ stops: [...state.stops, location] })),
  removeStop: (index) => set((state) => ({ stops: state.stops.filter((_, i) => i !== index) })),
  updateStop: (index, location) => set((state) => {
    const newStops = [...state.stops];
    newStops[index] = location;
    return { stops: newStops };
  }),

  fetchActiveRide: async () => {
    try {
      const response = await api.get<{ active?: boolean; ride?: Ride & { driver?: Driver | null } }>('/rides/active');
      if (response.data?.active && response.data.ride) {
        const ride = response.data.ride;
        const driver = ride.driver ?? null;
        const rideWithoutDriver: Ride = (({ driver: _d, ...rest }) => rest)(ride);
        set({ currentRide: rideWithoutDriver, currentDriver: driver, _clearedRideId: null });
        _persistRide(rideWithoutDriver, driver);
        return { active: true, ride: rideWithoutDriver };
      }
      // No active ride on server — clear any stale local state
      get().clearRide();
      return null;
    } catch (err: unknown) {
      if (isErrorLike(err) && (err as { response?: { status?: number } }).response?.status === 404) {
        get().clearRide();
      }
      return null;
    }
  },

  fetchEstimates: async () => {
    const { pickup, dropoff, stops, estimates: existing } = get();
    if (!pickup || !dropoff) return;

    try {
      // Only show loading skeleton on the initial fetch. Subsequent
      // auto-refreshes update silently so vehicle cards don't flash.
      if (existing.length === 0) {
        set({ isLoading: true, error: null });
      }
      const response = await api.post<RideEstimate[]>('/rides/estimate', {
        pickup_lat: pickup.lat,
        pickup_lng: pickup.lng,
        dropoff_lat: dropoff.lat,
        dropoff_lng: dropoff.lng,
        stops: stops,
      });
      const fresh = Array.isArray(response.data) ? response.data as RideEstimate[] : [];
      // Never replace a populated list with an empty one during a background
      // refresh — the rider should always see vehicle types even when the
      // backend returns none transiently (cache miss, rate-limit, etc.).
      if (fresh.length > 0 || existing.length === 0) {
        set({ estimates: fresh, isLoading: false });
      } else {
        set({ isLoading: false });
      }
    } catch (error: unknown) {
      console.error('fetchEstimates error:', error);
      set({ isLoading: false, error: isErrorLike(error) ? error.message : 'Failed to fetch estimates' });
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

  fetchAvailablePromos: async (rideFare?: number) => {
    try {
      const fare = rideFare ?? 0;
      const response = await api.get<Promo[]>(`/promo/available?ride_fare=${fare}`);
      const promos = response.data || [];
      set({ availablePromos: promos });
      // Auto-apply best promo (first one, already sorted by biggest discount)
      if (promos.length > 0 && !get().appliedPromo) {
        set({ appliedPromo: promos[0] });
      }
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
        globalAlert(
          'Sync Failed',
          `${failedPermanently.length} offline action(s) could not be synced and were dropped.`,
          'warning',
        );
      }
      if (successfulSyncs.length > 0) {
        console.log(`[Offline] Synced ${successfulSyncs.length} requests`);
      }
    } catch (error) {
      console.error('Failed to sync offline requests:', error);
    }
  },

  createRide: async (paymentMethod, corporateAccountId, paymentMethodId) => {
    const { pickup, dropoff, selectedVehicle, stops, scheduledTime, estimates, requiresWav, quietMode, riderNotes } = get();
    if (!pickup || !dropoff || !selectedVehicle) {
      throw new Error('Missing ride details');
    }
    if (get().currentRide) {
      const serverCheck = await get().fetchActiveRide();
      if (serverCheck?.active) {
        throw new Error('A ride is already active');
      }
    }

    try {
      set({ isLoading: true, error: null });
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
        stops: stops,
        payment_method: paymentMethod,
        payment_method_id: paymentMethodId ?? null,
        corporate_account_id: corporateAccountId || null,
        estimate_token: selectedEstimate?.estimate_token,
        requires_wav: requiresWav,
        quiet_mode: quietMode,
        rider_notes: riderNotes || null,
        created_at: new Date().toISOString(),
      };

      if (scheduledTime) {
        rideData.is_scheduled = true;
        rideData.scheduled_time = scheduledTime.toISOString();
      }

      const userId = useAuthStore.getState().user?.id ?? 'anon';
      const idempotencyKey = `ride-${userId}-${Date.now()}`;
      const response = await api.post<Ride>('/rides', rideData, {
        headers: { 'Idempotency-Key': idempotencyKey },
      });
      set({ currentRide: response.data, isLoading: false, scheduledTime: null, requiresWav: false, quietMode: false, riderNotes: '', _clearedRideId: null });
      _persistRide(response.data, null);
      return response.data;
    } catch (error: unknown) {
      recordNonFatal(error, { store: 'rideStore', action: 'createRide' });
      set({ isLoading: false, error: isErrorLike(error) ? error.message : 'Failed to create ride' });
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
      console.log('fetchRide error:', isErrorLike(error) ? error.message : error);
      // Don't clear currentRide on poll errors — keep showing last known state
      set({ isLoading: false });
    }
  },

  cancelRide: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      set({ isLoading: true });
      await api.post(`/rides/${currentRide.id}/cancel`);
      set({ currentRide: null, currentDriver: null, isLoading: false });
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
        set({ currentRide: null, currentDriver: null, isLoading: false });
        AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
        return;
      }
      recordNonFatal(error, { store: 'rideStore', action: 'cancelRide' });
      set({ isLoading: false, error: isErrorLike(error) ? error.message : 'Failed to cancel ride' });
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
      set({ error: isErrorLike(error) ? error.message : 'Failed to simulate arrival' });
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
      set({ error: isErrorLike(error) ? error.message : 'Failed to start ride' });
    }
  },

  completeRide: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      const response = await api.post<Ride>(`/rides/${currentRide.id}/complete`);
      set({ currentRide: response.data });
      AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
      return response.data;
    } catch (error: unknown) {
      recordNonFatal(error, { store: 'rideStore', action: 'completeRide' });
      set({ error: isErrorLike(error) ? error.message : 'Failed to complete ride' });
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
      set({ error: isErrorLike(error) ? error.message : 'Failed to rate ride' });
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
      set({ error: isErrorLike(error) ? error.message : 'Failed to add address' });
      throw error;
    }
  },

  deleteSavedAddress: async (id) => {
    try {
      await api.delete(`/addresses/${id}`);
      set({ savedAddresses: get().savedAddresses.filter((a) => a.id !== id) });
    } catch (error: unknown) {
      set({ error: isErrorLike(error) ? error.message : 'Failed to delete address' });
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
    const clearedId = get().currentRide?.id;
    set({ currentRide: null, currentDriver: null, chatMessages: [], error: null, _clearedRideId: clearedId ?? null });
    AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
  },

  clearError: () => set({ error: null }),

  addRecentSearch: (location) => {
    const { recentSearches } = get();
    // Avoid duplicates (by address)
    const filtered = recentSearches.filter(s => s.address !== location.address);
    const updated = [location, ...filtered].slice(0, 10); // Keep max 10
    set({ recentSearches: updated });
    AsyncStorage.setItem('recent_searches', JSON.stringify(updated)).catch(() => { });
  },

  loadRecentSearches: async () => {
    try {
      const stored = await AsyncStorage.getItem('recent_searches');
      if (stored) {
        set({ recentSearches: JSON.parse(stored) });
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
    AsyncStorage.removeItem('recent_searches').catch(() => { });
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
      console.log('Error fetching scheduled rides:', isErrorLike(error) ? error.message : error);
    }
  },

  cancelScheduledRide: async (rideId) => {
    try {
      await api.delete(`/rides/scheduled/${rideId}`);
      set((state) => ({
        scheduledRides: state.scheduledRides.filter((r) => r.id !== rideId),
      }));
    } catch (error: unknown) {
      set({ error: isErrorLike(error) ? error.message : 'Failed to cancel scheduled ride' });
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
      set({ currentRide: updated, currentDriver: null, driverEtaSeconds: null });
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
  });
  AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
});
