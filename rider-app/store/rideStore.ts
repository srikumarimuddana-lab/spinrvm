import { create } from 'zustand';
import { Alert, Linking } from 'react-native';
import api from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { RideStatus } from '../constants/rideStatus';
import { recordNonFatal } from '../utils/crashlytics';

const ACTIVE_RIDE_KEY = '@spinr:active_ride';
const TERMINAL_STATUSES = new Set([RideStatus.COMPLETED, RideStatus.CANCELLED, RideStatus.FAILED]);

// Write currentRide + currentDriver to AsyncStorage. Clears key when ride is
// terminal so stale data never survives across sessions.
const _persistRide = (currentRide: any, currentDriver: any) => {
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
  base_fare: number;
  distance_fare: number;
  time_fare: number;
  booking_fee: number;
  surge_multiplier?: number;
  total_fare: number;
  available: boolean;
  eta_minutes: number;
  driver_count: number;
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
  base_fare: number;
  total_fare: number;
  payment_method: string;
  payment_status?: string;
  card_last4?: string;
  status: string;
  pickup_otp: string;
  tip_amount?: number;
  corporate_account_id?: string | null;
  is_scheduled?: boolean;
  scheduled_time?: string;
  created_at: string;
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

interface RideState {
  pickup: Location | null;
  dropoff: Location | null;
  stops: Location[]; // Intermediate stops
  estimates: RideEstimate[];
  nearbyDrivers: NearbyDriver[];
  selectedVehicle: VehicleType | null;
  currentRide: Ride | null;
  currentDriver: Driver | null;
  savedAddresses: SavedAddress[];
  recentSearches: Location[];
  scheduledTime: Date | null;
  scheduledRides: Ride[];
  userLocation: { latitude: number; longitude: number } | null;
  availablePromos: any[];
  appliedPromo: any | null;
  isLoading: boolean;
  error: string | null;

  // Actions
  setPickup: (location: Location | null) => void;
  setDropoff: (location: Location | null) => void;
  addStop: (location: Location) => void;
  removeStop: (index: number) => void;
  updateStop: (index: number, location: Location) => void;
  fetchActiveRide: () => Promise<{ active: boolean; ride: any } | null>;
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
  fetchScheduledRides: () => Promise<void>;
  cancelScheduledRide: (rideId: string) => Promise<void>;
  setUserLocation: (loc: { latitude: number; longitude: number } | null) => void;
  fetchAvailablePromos: (rideFare?: number) => Promise<void>;
  applyPromo: (promo: any | null) => void;

  // WebSocket-driven updates (see rider-app/hooks/useRiderSocket.ts).
  updateDriverLocation: (lat: number, lng: number, speed?: number | null, heading?: number | null) => void;
  applyRideStatusFromWS: (rideId: string, status: string, extra?: Record<string, any>) => void;

  // Chat
  chatMessages: any[];
  addChatMessage: (msg: any) => void;
  setChatMessages: (msgs: any[]) => void;
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
  chatMessages: [],
  savedAddresses: [],
  recentSearches: [],
  availablePromos: [],
  appliedPromo: null,
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
      const response = await api.get('/rides/active');
      if (response.data?.active && response.data.ride) {
        const ride = response.data.ride;
        const driver = ride.driver || null;
        set({ currentRide: ride, currentDriver: driver });
        _persistRide(ride, driver);
        return response.data;
      }
      // No active ride on server — clear any stale local state
      get().clearRide();
      return null;
    } catch (err: any) {
      if (err?.response?.status === 404) {
        get().clearRide();
      }
      return null;
    }
  },

  fetchEstimates: async () => {
    console.log('fetchEstimates store action started');
    const { pickup, dropoff, stops } = get();
    if (!pickup || !dropoff) return;

    try {
      set({ isLoading: true, error: null });
      const response = await api.post('/rides/estimate', {
        pickup_lat: pickup.lat,
        pickup_lng: pickup.lng,
        dropoff_lat: dropoff.lat,
        dropoff_lng: dropoff.lng,
        stops: stops, // Send stops to backend
      });
      console.log('Ride API Response:', response.data);
      set({ estimates: response.data, isLoading: false });
    } catch (error: any) {
      console.error('fetchEstimates error:', error);
      set({ isLoading: false, error: error.message });
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
      const response = await api.get(url);
      set({ nearbyDrivers: response.data });
    } catch (error) {
      console.log('Error fetching nearby drivers', error);
    }
  },

  selectVehicle: (vehicle) => set({ selectedVehicle: vehicle }),

  fetchAvailablePromos: async (rideFare?: number) => {
    try {
      const fare = rideFare ?? 0;
      const response = await api.get(`/promo/available?ride_fare=${fare}`);
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

      const queue: Array<{
        id: string;
        type: 'create_ride' | 'cancel_ride' | 'rate_ride' | 'tip' | 'emergency';
        rideId?: string;
        data?: any;
        retryCount?: number;
        timestamp?: number;
      }> = JSON.parse(queueStr);
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
        Alert.alert(
          'Sync Failed',
          `${failedPermanently.length} offline action(s) could not be synced and were dropped.`,
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
    const { pickup, dropoff, selectedVehicle, stops, scheduledTime, estimates } = get();
    if (!pickup || !dropoff || !selectedVehicle) {
      throw new Error('Missing ride details');
    }
    if (get().currentRide) {
      throw new Error('A ride is already active');
    }

    try {
      set({ isLoading: true, error: null });
      // P0-4: echo back the estimate_token from the currently-selected
      // vehicle's estimate so the backend can reuse the surge we showed
      // in the UI (instead of re-reading current surge and bait-and-switching).
      const selectedEstimate = estimates.find(
        (e) => e.vehicle_type?.id === selectedVehicle.id
      );
      const rideData: any = {
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
        created_at: new Date().toISOString(),
      };

      if (scheduledTime) {
        rideData.is_scheduled = true;
        rideData.scheduled_time = scheduledTime.toISOString();
      }

      const userId = useAuthStore.getState().user?.id ?? 'anon';
      const idempotencyKey = `ride-${userId}-${Date.now()}`;
      const response = await api.post('/rides', rideData, {
        headers: { 'Idempotency-Key': idempotencyKey },
      });
      set({ currentRide: response.data, isLoading: false, scheduledTime: null });
      _persistRide(response.data, null);
      return response.data;
    } catch (error: any) {
      recordNonFatal(error, { store: 'rideStore', action: 'createRide' });
      set({ isLoading: false, error: error.message });
      throw error;
    }
  },

  fetchRide: async (rideId) => {
    try {
      // Only set isLoading on first fetch (when no ride data yet)
      if (!get().currentRide) {
        set({ isLoading: true });
      }
      const response = await api.get(`/rides/${rideId}`);
      const ride = response.data;
      const driver = ride.driver || null;
      set({ currentRide: ride, currentDriver: driver, isLoading: false });
      _persistRide(ride, driver);
    } catch (error: any) {
      console.log('fetchRide error:', error.message);
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
    } catch (error: any) {
      recordNonFatal(error, { store: 'rideStore', action: 'cancelRide' });
      set({ isLoading: false, error: error.message });
    }
  },

  simulateDriverArrival: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      const response = await api.post(`/rides/${currentRide.id}/simulate-arrival`);
      set({
        currentRide: { ...currentRide, status: RideStatus.DRIVER_ARRIVED, pickup_otp: response.data.pickup_otp },
      });
    } catch (error: any) {
      set({ error: error.message });
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
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  completeRide: async () => {
    const { currentRide } = get();
    if (!currentRide) return;

    try {
      const response = await api.post(`/rides/${currentRide.id}/complete`);
      set({ currentRide: response.data });
      AsyncStorage.removeItem(ACTIVE_RIDE_KEY).catch(() => {});
      return response.data;
    } catch (error: any) {
      recordNonFatal(error, { store: 'rideStore', action: 'completeRide' });
      set({ error: error.message });
    }
  },

  rateRide: async (rideId: string, rating: number, comment?: string, tipAmount?: number) => {
    try {
      await api.post(`/rides/${rideId}/rate`, {
        rating,
        comment,
        tip_amount: tipAmount || 0,
      });
    } catch (error: any) {
      recordNonFatal(error, { store: 'rideStore', action: 'rateRide' });
      set({ error: error.message });
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
    let lastError: any;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        await api.post(`/rides/${rideId}/emergency`, payload);
        return; // success
      } catch (error: any) {
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
      const response = await api.get('/addresses');
      set({ savedAddresses: response.data });
    } catch (error: any) {
      console.log('Error fetching addresses:', error.message);
    }
  },

  addSavedAddress: async (address) => {
    try {
      const response = await api.post('/addresses', address);
      set({ savedAddresses: [...get().savedAddresses, response.data] });
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  deleteSavedAddress: async (id) => {
    try {
      await api.delete(`/addresses/${id}`);
      set({ savedAddresses: get().savedAddresses.filter((a) => a.id !== id) });
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  addChatMessage: (msg) => {
    const { chatMessages } = get();
    // Deduplicate by id (WS + poll can deliver the same message).
    if (chatMessages.some((m: any) => m.id === msg.id)) return;
    set({ chatMessages: [...chatMessages, msg] });
  },

  setChatMessages: (msgs) => set({ chatMessages: msgs }),

  // Clear ONLY the live-ride fields so the rider can immediately re-book
  // using the same trip inputs after a cancellation. We deliberately keep
  // pickup / dropoff / estimates / selectedVehicle / scheduledTime because
  // those represent "what the user wants to do next", not "the in-flight
  // trip". Wiping them caused the post-cancel flow to land on ride-options
  // with no pickup → stuck loading → bounce back to home.
  clearRide: () => {
    set({ currentRide: null, currentDriver: null, chatMessages: [], error: null });
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
    } catch { }
  },

  clearRecentSearches: () => {
    set({ recentSearches: [] });
    AsyncStorage.removeItem('recent_searches').catch(() => { });
  },

  setScheduledTime: (time) => set({ scheduledTime: time }),

  fetchScheduledRides: async () => {
    try {
      const response = await api.get('/rides/scheduled');
      set({ scheduledRides: response.data });
    } catch (error: any) {
      console.log('Error fetching scheduled rides:', error.message);
    }
  },

  cancelScheduledRide: async (rideId) => {
    try {
      await api.delete(`/rides/scheduled/${rideId}`);
      set((state) => ({
        scheduledRides: state.scheduledRides.filter((r) => r.id !== rideId),
      }));
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  // ── WebSocket-driven updates ────────────────────────────────────

  updateDriverLocation: (lat, lng, speed, heading) => {
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
    set({ currentDriver: updated });
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
    set({ currentRide: updated });
    _persistRide(updated, currentDriver);
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
        const live = await api.get('/rides/active');
        if (!live.data?.active || live.data.ride?.id !== stored.id) {
          await AsyncStorage.removeItem(ACTIVE_RIDE_KEY);
          return; // stale — do not route
        }
        const liveRide = live.data.ride;
        const liveDriver = liveRide.driver || currentDriver || null;
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
