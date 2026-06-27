import { create } from 'zustand';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { recordNonFatal } from '../utils/crashlytics';

function isAxiosError(e: unknown): e is { response?: { status?: number; data?: { detail?: string } }; message?: string } {
  return typeof e === 'object' && e !== null;
}

const DRIVER_RIDE_KEY = '@spinr:driver_active_ride';
const DRIVER_TERMINAL_STATES = new Set<string>(['idle', 'trip_completed']);

// Write activeRide + rideState to AsyncStorage so the driver can resume
// after an app restart mid-trip. Clears the key when the ride is over.
const _persistDriverState = (rideState: string, activeRide: ActiveRide | null) => {
  if (!activeRide || DRIVER_TERMINAL_STATES.has(rideState)) {
    AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
  } else {
    AsyncStorage.setItem(DRIVER_RIDE_KEY, JSON.stringify({ rideState, activeRide })).catch(() => {});
  }
};

// These are fallbacks used ONLY until the driver-app pulls
// `GET /drivers/config` from the backend on mount (see
// useDriverDashboard.ts). The authoritative values then live in the
// store's `configuredCountdownSeconds` / `configuredPickupRadiusMeters`
// fields and can be tuned by admins without shipping a new app build.
const FALLBACK_COUNTDOWN = SpinrConfig.rideOffer?.countdownSeconds || 15;
const FALLBACK_PICKUP_RADIUS_METERS = 100;

// Helper function to calculate distance between two coordinates (Haversine formula)
const calculateDistance = (lat1: number, lng1: number, lat2: number, lng2: number): number => {
    const R = 6371e3; // Earth's radius in meters
    const φ1 = (lat1 * Math.PI) / 180;
    const φ2 = (lat2 * Math.PI) / 180;
    const Δφ = ((lat2 - lat1) * Math.PI) / 180;
    const Δλ = ((lng2 - lng1) * Math.PI) / 180;

    const a = Math.sin(Δφ / 2) * Math.sin(Δφ / 2) +
        Math.cos(φ1) * Math.cos(φ2) *
        Math.sin(Δλ / 2) * Math.sin(Δλ / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

    return R * c; // Distance in meters
};

export interface ChatMessage {
    id: string;
    ride_id: string;
    text: string;
    sender: 'driver' | 'rider';
    timestamp: string;
    read?: boolean;
}

export type RideState =
    | 'idle'
    | 'ride_offered'
    | 'navigating_to_pickup'
    | 'arrived_at_pickup'
    | 'trip_in_progress'
    | 'trip_completed';

export interface RideInfo {
    id: string;
    status: string;
    pickup_address: string;
    dropoff_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_lat: number;
    dropoff_lng: number;
    total_fare: string;
    distance_km: number;
    duration_minutes: number;
    rider_id: string;
    driver_id?: string;
    pickup_otp?: string;
    surge_multiplier?: number;
    payment_method?: string;
    // Present on a dispatched offer (status driver_assigned). Typed explicitly
    // so consumers don't fall through to the `unknown` index signature.
    requires_wav?: boolean;
    offer_expires_at?: string;
    created_at: string;
    [key: string]: unknown;
}

export interface RiderInfo {
    id: string;
    first_name?: string;
    last_name?: string;
    phone?: string;
    rating?: number;
    profile_image?: string;
    [key: string]: unknown;
}

export interface VehicleTypeInfo {
    id: string;
    name: string;
    icon?: string;
    capacity?: number;
    [key: string]: unknown;
}

export interface ActiveRide {
    ride: RideInfo;
    rider: RiderInfo;
    vehicle_type: VehicleTypeInfo;
    // incentive_type is always present — both backend endpoints default it to
    // "per_ride" (routes/rides.py, routes/drivers.py), matching IncomingRide
    // and RideOfferPanel's IncentiveItem.
    incentives?: Array<{ name: string; bonus_amount: number; incentive_type: string }>;
    total_bonus?: number;
    // Matches the backend payload (routes/rides.py / routes/drivers.py) and what
    // RideOfferPanel reads. The previous {progress, reward} shape was stale and
    // never matched the data, so any code reading it got undefined fields.
    quest_hint?: {
        title: string;
        current_value: number;
        target_value: number;
        progress_pct: number;
        reward_amount: number;
    } | null;
}

export interface CompletedRideData {
    id?: string;
    base_fare?: number;
    distance_fare?: number;
    time_fare?: number;
    booking_fee?: number;
    tip_amount?: number;
    total_fare?: number;
    driver_earnings?: number;
    distance_km?: number;
    duration_minutes?: number;
    pickup_address?: string;
    dropoff_address?: string;
    ride_completed_at?: string;
}

export interface RideHistoryItem {
    id?: string;
    status?: string;
    created_at?: string;
    ride_completed_at?: string;
    cancelled_at?: string;
    pickup_address?: string;
    dropoff_address?: string;
    total_fare?: string;
    distance_km?: number;
    duration_minutes?: number;
    [key: string]: unknown;
}

export interface EarningsSummary {
    period: string;
    total_earnings: string; // MoneyString
    total_tips: string; // MoneyString
    total_incentives: string; // MoneyString
    total_bonuses: string; // MoneyString — quest + driver-referral bonuses (from driver_bonuses)
    total_cancel_fees: string; // MoneyString
    total_tax: string; // MoneyString
    total_rides: number;
    total_distance_km: number;
    total_duration_minutes: number;
    average_per_ride: string; // MoneyString
}

export interface DailyEarning {
    date: string;
    earnings: string; // MoneyString
    tips: string; // MoneyString
    rides: number;
    distance_km: number;
}

export interface TripEarning {
    ride_id: string;
    pickup_address: string;
    dropoff_address: string;
    distance_km: number;
    duration_minutes: number;
    base_fare: string; // MoneyString
    distance_fare: string; // MoneyString
    time_fare: string; // MoneyString
    driver_earnings: string; // MoneyString
    tip_amount: string; // MoneyString
    rider_rating: number | null;
    completed_at: string;
    ride_code?: string;
}

export interface BankAccount {
    bank_name: string;
    institution_number: string;
    transit_number: string;
    account_number: string;
    account_holder_name: string;
    account_type: string;
    is_verified: boolean;
}

export interface DriverBalance {
    total_earnings: string; // MoneyString
    payable_balance: string; // MoneyString — renamed from available_balance (NOT wallet.balance)
    pending_payouts: string; // MoneyString
    total_paid_out: string; // MoneyString
    has_bank_account: boolean;
}

export interface Payout {
    id: string;
    amount: string; // MoneyString
    status: string;
    bank_name: string;
    account_last4: string;
    created_at: string;
    processed_at: string | null;
    error_message: string | null;
}

export interface T4ASummary {
    year: number;
    total_earnings: string; // MoneyString
    total_trips: number;
    platform_fees: string; // MoneyString
    net_earnings: string; // MoneyString
    generated_at: string;
}

export interface T4ADocument {
    year: number;
    document_type: string;
    issued_date: string;
    status: string;
}

export interface WeeklyEarning {
    week_start: string;
    week_end: string;
    earnings: string; // MoneyString
    tips: string; // MoneyString
    rides: number;
    online_hours: number;
    distance_km: number;
}

export interface MonthlyEarning {
    month: string;
    year: number;
    earnings: string; // MoneyString
    tips: string; // MoneyString
    rides: number;
    online_hours: number;
    distance_km: number;
}

export interface EarningsComparison {
    period: string;
    current: { earnings: string; rides: number; tips: string }; // MoneyString
    previous: { earnings: string; rides: number; tips: string }; // MoneyString
    change_pct: { earnings: number; rides: number; tips: number }; // percentages stay number
}

interface IncomingRide {
    ride_id: string;
    pickup_address: string;
    dropoff_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_lat: number;
    dropoff_lng: number;
    fare: string; // MoneyString
    distance_km?: number;
    duration_minutes?: number;
    rider_name?: string;
    rider_rating?: number;
    rider_profile_image?: string;
    // Wheelchair-accessible vehicle requested by rider — Saskatchewan
    // Transportation Act s.22. Drivers with WAV-equipped vehicles see this
    // flag in the ride offer panel; non-WAV drivers should not receive
    // these offers at all (backend filters dispatch).
    requires_wav?: boolean;
    // Per-offer countdown sourced from the dispatch payload — overrides
    // the cached configuredCountdownSeconds so an admin-changed timeout
    // takes effect on the very next offer, not the next cold start.
    countdown_seconds?: number;
    offer_expires_at?: string;
    surge_multiplier?: number;
    incentives?: Array<{ name: string; bonus_amount: number; incentive_type: string }>;
    total_bonus?: number;
    quest_hint?: {
        title: string;
        current_value: number;
        target_value: number;
        progress_pct: number;
        reward_amount: number;
    } | null;
    payment_method?: string;
    planned_route_polyline?: Array<[number, number]> | null;
    service_area_polygon?: Array<{ lat: number; lng: number }> | null;
}

interface DriverState {
    // Ride state machine
    rideState: RideState;
    incomingRide: IncomingRide | null;
    activeRide: ActiveRide | null;
    completedRide: CompletedRideData | null;
    countdownSeconds: number;

    // Server-driven operational config (populated by applyDriverConfig on mount).
    // These override the module-level fallbacks without requiring a rebuild.
    configuredCountdownSeconds: number;
    configuredPickupRadiusMeters: number;

    // Earnings
    earnings: EarningsSummary | null;
    dailyEarnings: DailyEarning[];
    weeklyEarnings: WeeklyEarning[];
    monthlyEarnings: MonthlyEarning[];
    earningsComparison: EarningsComparison | null;
    tripEarnings: TripEarning[];

    // Payout/Bank Account
    bankAccount: BankAccount | null;
    driverBalance: DriverBalance | null;
    payoutHistory: Payout[];
    hasBankAccount: boolean;

    // Tax Documents (T4A)
    t4aSummaries: T4ASummary[];
    availableYears: number[];
    selectedYear: number | null;

    // Ride history
    rideHistory: RideHistoryItem[];
    historyTotal: number;

    // In-ride chat (real-time via WS, seeded from REST on screen mount)
    chatMessages: ChatMessage[];
    riderTyping: boolean;

    // Loading states
    isLoading: boolean;
    isCancellingRide: boolean;
    error: string | null;

    // Actions - Ride lifecycle
    setIncomingRide: (ride: IncomingRide | null) => void;
    setCountdown: (seconds: number) => void;
    applyDriverConfig: (config: { ride_offer_timeout_seconds?: number; pickup_radius_meters?: number }) => void;
    acceptRide: (rideId: string) => Promise<void>;
    declineRide: (rideId: string) => Promise<void>;
    arriveAtPickup: (rideId: string, driverLat?: number, driverLng?: number) => Promise<{ success: boolean; distance?: number; error?: string }>;
    verifyOTP: (rideId: string, otp: string) => Promise<boolean>;
    startRide: (rideId: string) => Promise<void>;
    completeRide: (rideId: string) => Promise<void>;
    cancelRide: (rideId: string, reason?: string) => Promise<void>;

    // Fetch
    fetchActiveRide: () => Promise<void>;
    fetchRideHistory: (limit?: number, offset?: number, append?: boolean) => Promise<void>;
    fetchEarnings: (period?: string) => Promise<void>;
    fetchDailyEarnings: (days?: number) => Promise<void>;
    fetchWeeklyEarnings: (weeks?: number) => Promise<void>;
    fetchMonthlyEarnings: (months?: number) => Promise<void>;
    fetchEarningsComparison: (period?: string) => Promise<void>;
    fetchTripEarnings: (limit?: number, offset?: number) => Promise<void>;

    // Payout actions
    fetchBankAccount: () => Promise<void>;
    setBankAccount: (account: BankAccount) => Promise<boolean>;
    deleteBankAccount: () => Promise<boolean>;
    fetchDriverBalance: () => Promise<void>;
    requestPayout: (amount: number) => Promise<{ success: boolean; error?: string }>;
    fetchPayoutHistory: (limit?: number, offset?: number) => Promise<void>;
    exportEarnings: (year?: number) => Promise<{ data: string; filename: string } | null>;

    // T4A Tax Documents
    fetchT4ASummaries: () => Promise<void>;
    fetchT4ADetails: (year: number) => Promise<void>;
    setSelectedYear: (year: number | null) => void;

    // Chat actions
    addChatMessage: (msg: ChatMessage) => void;
    setChatMessages: (msgs: ChatMessage[]) => void;
    clearChatMessages: () => void;
    setRiderTyping: (typing: boolean) => void;

    // State management
    hydrateDriverRideState: () => Promise<void>;
    resetRideState: () => void;
    rateRider: (rideId: string, rating: number, comment?: string) => Promise<void>;
    clearError: () => void;
}

export const useDriverStore = create<DriverState>((set, get) => ({
    rideState: 'idle',
    incomingRide: null,
    activeRide: null,
    completedRide: null,
    countdownSeconds: 0,
    // Server-config fallbacks — overwritten by applyDriverConfig() after
    // the first /drivers/config fetch.
    configuredCountdownSeconds: FALLBACK_COUNTDOWN,
    configuredPickupRadiusMeters: FALLBACK_PICKUP_RADIUS_METERS,
    earnings: null,
    dailyEarnings: [],
    weeklyEarnings: [],
    monthlyEarnings: [],
    earningsComparison: null,
    tripEarnings: [],
    // Payout state
    bankAccount: null,
    driverBalance: null,
    payoutHistory: [],
    hasBankAccount: false,
    // Tax Documents (T4A)
    t4aSummaries: [],
    availableYears: [],
    selectedYear: null,
    // History
    rideHistory: [],
    historyTotal: 0,
    // Chat messages for the active ride (seeded via REST, kept live by WS)
    chatMessages: [],
    riderTyping: false,
    isLoading: false,
    isCancellingRide: false,
    error: null,

    setIncomingRide: (ride) => {
        if (ride !== null) {
            const current = get().rideState;
            if (current !== 'idle') {
                // Defense-in-depth: WS and FCM handlers guard first, but if a
                // duplicate slips through, don't overwrite an active ride state.
                console.warn('[driverStore] setIncomingRide blocked in state:', current);
                return;
            }
        }
        const cached = get().configuredCountdownSeconds || FALLBACK_COUNTDOWN;
        // Per-offer countdown from the WS/FCM payload wins over the cached
        // config — the backend may have a newer value than this app instance.
        const countdown = ride?.countdown_seconds && ride.countdown_seconds > 0
            ? ride.countdown_seconds
            : cached;
        set({
            incomingRide: ride,
            rideState: ride ? 'ride_offered' : 'idle',
            countdownSeconds: ride ? countdown : 0,
        });
    },

    applyDriverConfig: (config) => {
        const patch: Partial<DriverState> = {};
        if (typeof config.ride_offer_timeout_seconds === 'number' && config.ride_offer_timeout_seconds > 0) {
            patch.configuredCountdownSeconds = config.ride_offer_timeout_seconds;
        }
        if (typeof config.pickup_radius_meters === 'number' && config.pickup_radius_meters > 0) {
            patch.configuredPickupRadiusMeters = config.pickup_radius_meters;
        }
        if (Object.keys(patch).length > 0) {
            set(patch as DriverState);
        }
    },

    setCountdown: (seconds) => {
        set({ countdownSeconds: seconds });
        if (seconds <= 0 && get().rideState === 'ride_offered') {
            // Auto-decline on timeout + show a clear toast so the driver
            // knows the offer expired (G10). Previously the offer just
            // silently disappeared with no feedback.
            const incoming = get().incomingRide;
            if (incoming) {
                get().declineRide(incoming.ride_id).catch(console.log);
                set({ error: 'Ride offer expired. You\'ll see the next one when it comes in.' });
            }
        }
    },

    acceptRide: async (rideId: string) => {
        set({ isLoading: true, error: null });
        try {
            await api.post(`/drivers/rides/${rideId}/accept`);
            set({ rideState: 'navigating_to_pickup', incomingRide: null, countdownSeconds: 0 });
            // Fetch the full active ride data
            await get().fetchActiveRide();
            _persistDriverState('navigating_to_pickup', get().activeRide);
        } catch (err: unknown) {
            const status = isAxiosError(err) ? err.response?.status : undefined;
            const detail: string = isAxiosError(err) ? (err.response?.data?.detail || '') : '';

            // Race-condition handling: another driver beat us to the ride, or
            // the rider cancelled between dispatch and accept. Backend returns
            // 400 with detail "Ride not assigned to you" (see
            // backend/routes/drivers.py accept_ride) or 404 if the ride row is
            // gone. Either way the right UX is to clear the incoming offer,
            // drop back to `idle`, and surface a short, non-alarming toast —
            // NOT leave the driver stuck staring at the accept/decline panel.
            const alreadyTakenDetail = /not assigned|already|no longer|cancelled|canceled/i.test(detail);
            const alreadyTakenStatus = status === 404 || (status === 400 && alreadyTakenDetail);

            if (!alreadyTakenStatus) {
                recordNonFatal(err, { store: 'driverStore', action: 'acceptRide' });
            }
            if (alreadyTakenStatus) {
                set({
                    rideState: 'idle',
                    incomingRide: null,
                    countdownSeconds: 0,
                    error: 'This ride was already taken by another driver. You\'ll see the next offer when it comes in.',
                });
            } else {
                set({ error: detail || 'Failed to accept ride' });
            }
        } finally {
            set({ isLoading: false });
        }
    },

    declineRide: async (rideId: string) => {
        try {
            await api.post(`/drivers/rides/${rideId}/decline`);
        } catch {
            // Decline failure is non-critical — reset state regardless
        }
        set({ rideState: 'idle', incomingRide: null, countdownSeconds: 0 });
    },

    arriveAtPickup: async (rideId: string, driverLat?: number, driverLng?: number) => {
        // If driver location is provided, verify they're at the pickup
        const activeRide = get().activeRide;
        const radiusMeters = get().configuredPickupRadiusMeters || FALLBACK_PICKUP_RADIUS_METERS;
        if (driverLat !== undefined && driverLng !== undefined && activeRide?.ride) {
            const pickupLat = activeRide.ride.pickup_lat;
            const pickupLng = activeRide.ride.pickup_lng;

            if (pickupLat && pickupLng) {
                const distance = calculateDistance(driverLat, driverLng, pickupLat, pickupLng);

                if (distance > radiusMeters) {
                    set({
                        error: `You must be within ${radiusMeters}m of the pickup location. Current distance: ${Math.round(distance)}m`,
                        isLoading: false
                    });
                    return { success: false, distance, error: 'Not at pickup location' };
                }
            }
        }

        set({ isLoading: true, error: null });
        try {
            await api.post(`/drivers/rides/${rideId}/arrive`);
            set({ rideState: 'arrived_at_pickup' });
            await get().fetchActiveRide();
            _persistDriverState('arrived_at_pickup', get().activeRide);
            return { success: true };
        } catch (err: unknown) {
            const detail = isAxiosError(err) ? (err.response?.data?.detail || 'Failed to mark arrival') : 'Failed to mark arrival';
            set({ error: detail });
            return { success: false, error: detail };
        } finally {
            set({ isLoading: false });
        }
    },

    verifyOTP: async (rideId: string, otp: string): Promise<boolean> => {
        set({ isLoading: true, error: null });
        try {
            await api.post(`/drivers/rides/${rideId}/verify-otp`, { otp });
            set({ rideState: 'trip_in_progress' });
            await get().fetchActiveRide();
            _persistDriverState('trip_in_progress', get().activeRide);
            return true;
        } catch (err: unknown) {
            set({ error: isAxiosError(err) ? (err.response?.data?.detail || 'Invalid OTP') : 'Invalid OTP' });
            return false;
        } finally {
            set({ isLoading: false });
        }
    },

    startRide: async (rideId: string) => {
        set({ isLoading: true, error: null });
        try {
            await api.post(`/drivers/rides/${rideId}/start`);
            set({ rideState: 'trip_in_progress' });
            await get().fetchActiveRide();
            _persistDriverState('trip_in_progress', get().activeRide);
        } catch (err: unknown) {
            set({ error: isAxiosError(err) ? (err.response?.data?.detail || 'Failed to start ride') : 'Failed to start ride' });
        } finally {
            set({ isLoading: false });
        }
    },

    completeRide: async (rideId: string) => {
        set({ isLoading: true, error: null });
        try {
            const res = await api.post<CompletedRideData>(`/drivers/rides/${rideId}/complete`);
            // chatMessages belongs to the just-finished ride — drop it so a
            // long shift doesn't accumulate every prior conversation in
            // memory. Same for incomingRide which can linger from a stale
            // offer panel.
            set({
                rideState: 'trip_completed',
                completedRide: res.data,
                activeRide: null,
                incomingRide: null,
                chatMessages: [],
            });
            AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
        } catch (err: unknown) {
            // 409 means the ride is already in a terminal state (completed or
            // cancelled). This happens when the network drops between the
            // successful backend write and the client receiving the 200 —
            // the driver retaps "Complete" and gets a state-conflict error.
            // Instead of showing a red error, check whether there is still
            // an active ride. If not, the completion went through and we
            // treat it as success by syncing state from the server.
            const status = isAxiosError(err) ? err.response?.status : undefined;
            if (status === 409) {
                await get().fetchActiveRide();
                if (!get().activeRide) {
                    // Ride is gone server-side — completion already happened.
                    set({ rideState: 'trip_completed', activeRide: null, incomingRide: null, chatMessages: [] });
                    AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
                    return;
                }
            }
            recordNonFatal(err, { store: 'driverStore', action: 'completeRide' });
            set({ error: isAxiosError(err) ? (err.response?.data?.detail || 'Failed to complete ride') : 'Failed to complete ride' });
        } finally {
            set({ isLoading: false });
        }
    },

    cancelRide: async (rideId: string, reason?: string) => {
        set({ isLoading: true, isCancellingRide: true, error: null });
        try {
            // Reason goes in the body (not the URL) so it can't leak into logs;
            // omit the body when there's no reason (plain single-arg POST).
            const trimmed = reason?.trim();
            if (trimmed) {
                await api.post(`/drivers/rides/${rideId}/cancel`, { reason: trimmed });
            } else {
                await api.post(`/drivers/rides/${rideId}/cancel`);
            }
            set({
                rideState: 'idle',
                activeRide: null,
                incomingRide: null,
                chatMessages: [],
            });
            AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
        } catch (err: unknown) {
            recordNonFatal(err, { store: 'driverStore', action: 'cancelRide' });
            set({ error: isAxiosError(err) ? (err.response?.data?.detail || 'Failed to cancel ride') : 'Failed to cancel ride' });
        } finally {
            set({ isLoading: false, isCancellingRide: false });
        }
    },

    fetchActiveRide: async () => {
        try {
            // Guard: if the driver already has a live offer counting down
            // (set by the WS/FCM handler), don't let the HTTP poll clobber it.
            // The WS event is authoritative for new offers; the API round-trip
            // can race and return stale state (no active ride yet, or a
            // different offer_expires_at) that would flash-dismiss the panel.
            const _cur = get();
            if (_cur.rideState === 'ride_offered' && _cur.incomingRide && _cur.countdownSeconds > 2) {
                return;
            }

            const res = await api.get<ActiveRide | null>('/drivers/rides/active');
            if (res.data && res.data.ride) {
                const ride = res.data.ride;
                const rider = res.data.rider;

                // `driver_assigned` means the backend dispatched the offer to
                // this driver but they have NOT clicked accept yet — resume
                // the ride_offered countdown screen instead of jumping to the
                // post-accept navigation panel.
                if (ride.status === 'driver_assigned') {
                    const fullTimeout = get().configuredCountdownSeconds || FALLBACK_COUNTDOWN;
                    // Use the server's offer_expires_at to compute remaining
                    // time — otherwise the client always restarts at 15s even
                    // if the backend timeout has nearly elapsed, causing the
                    // offer to vanish when the server-side handler fires.
                    let countdown = fullTimeout;
                    const offerExpiresAt = (ride as any).offer_expires_at as string | undefined;
                    if (offerExpiresAt) {
                        const remaining = Math.floor(
                            (new Date(offerExpiresAt).getTime() - Date.now()) / 1000,
                        );
                        countdown = Math.max(0, remaining);
                    }
                    if (countdown <= 0) {
                        // Offer already expired server-side — don't show it.
                        set({ activeRide: null, rideState: 'idle', incomingRide: null, countdownSeconds: 0 });
                        AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
                        return;
                    }
                    const riderName = rider
                        ? (rider.first_name || rider.name || undefined)
                        : undefined;
                    const riderRating = rider && rider.rating != null
                        ? Number(rider.rating)
                        : undefined;
                    const apiIncentives = Array.isArray(res.data.incentives) ? res.data.incentives : undefined;
                    const apiTotalBonus = typeof res.data.total_bonus === 'number' ? res.data.total_bonus : undefined;
                    const apiQuestHint = res.data.quest_hint ?? undefined;
                    const existing = get().incomingRide;
                    set({
                        activeRide: res.data,
                        rideState: 'ride_offered',
                        incomingRide: {
                            ride_id: ride.id,
                            pickup_address: ride.pickup_address,
                            dropoff_address: ride.dropoff_address,
                            pickup_lat: ride.pickup_lat,
                            pickup_lng: ride.pickup_lng,
                            dropoff_lat: ride.dropoff_lat,
                            dropoff_lng: ride.dropoff_lng,
                            fare: (ride.driver_earnings ?? ride.fare) as string,
                            distance_km: ride.distance_km,
                            duration_minutes: ride.duration_minutes,
                            rider_name: riderName as string | undefined,
                            rider_rating: riderRating,
                            surge_multiplier: (ride.surge_multiplier ?? 0) > 1 ? ride.surge_multiplier : undefined,
                            incentives: apiIncentives ?? existing?.incentives,
                            total_bonus: apiTotalBonus ?? existing?.total_bonus,
                            quest_hint: apiQuestHint ?? existing?.quest_hint,
                            payment_method: ride.payment_method ?? existing?.payment_method,
                            offer_expires_at: ride.offer_expires_at ?? existing?.offer_expires_at,
                            requires_wav: ride.requires_wav ?? existing?.requires_wav,
                            planned_route_polyline: (ride as any).planned_route_polyline ?? undefined,
                            service_area_polygon: (res.data as any).service_area_polygon ?? existing?.service_area_polygon ?? undefined,
                        },
                        countdownSeconds: countdown,
                    });
                    // Intentionally do NOT persist ride_offered — the offer
                    // is ephemeral and we want a fresh fetchActiveRide to
                    // decide the state on next mount.
                    AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
                    return;
                }

                let rideState: RideState = 'idle';
                if (ride.status === 'driver_accepted') rideState = 'navigating_to_pickup';
                else if (ride.status === 'driver_arrived') rideState = 'arrived_at_pickup';
                else if (ride.status === 'in_progress') rideState = 'trip_in_progress';

                set({
                    activeRide: res.data,
                    rideState,
                    incomingRide: null,
                    countdownSeconds: 0,
                });
                _persistDriverState(rideState, res.data);
            } else {
                // No active ride on the server. With batch dispatch the ride
                // stays in 'searching' (no driver_id set), so the API returns
                // null even though a live WS offer is counting down. Don't
                // nuke a ride_offered state — the WS/timeout handler manages it.
                if (get().rideState !== 'ride_offered') {
                    set({ activeRide: null, rideState: 'idle', incomingRide: null, countdownSeconds: 0 });
                    AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
                }
            }
        } catch {
            // On network errors, don't wipe a live offer — the driver is
            // trying to accept and a transient 500/timeout shouldn't kill
            // the panel. Only reset if we were in a non-offer state where
            // stale AsyncStorage data could hide the GO button.
            if (get().rideState !== 'ride_offered') {
                set({ rideState: 'idle', activeRide: null });
                AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
            }
        }
    },

    fetchRideHistory: async (limit = 20, offset = 0, append = false) => {
        try {
            const res = await api.get<{ rides: RideHistoryItem[]; total: number }>(`/drivers/rides/history?limit=${limit}&offset=${offset}`);
            const nextRides = res.data.rides || [];
            set((state) => {
                if (!append || offset === 0) {
                    return {
                        rideHistory: nextRides,
                        historyTotal: res.data.total || 0,
                    };
                }

                const seen = new Set(state.rideHistory.map((ride) => ride.id).filter(Boolean));
                const merged = [...state.rideHistory];
                nextRides.forEach((ride) => {
                    if (!ride.id || !seen.has(ride.id)) {
                        merged.push(ride);
                        if (ride.id) seen.add(ride.id);
                    }
                });

                return {
                    rideHistory: merged,
                    historyTotal: res.data.total || merged.length,
                };
            });
        } catch (err) {
            console.log('Fetch history error:', err);
        }
    },

    fetchEarnings: async (period = 'day') => {
        try {
            const res = await api.get<EarningsSummary>(`/drivers/earnings?period=${period}`);
            set({ earnings: res.data });
        } catch (err) {
            throw err;
        }
    },

    fetchDailyEarnings: async (days = 7) => {
        try {
            const res = await api.get<DailyEarning[]>(`/drivers/earnings/daily?days=${days}`);
            set({ dailyEarnings: res.data || [] });
        } catch (err) {
            throw err;
        }
    },

    fetchWeeklyEarnings: async (weeks = 4) => {
        try {
            const res = await api.get<WeeklyEarning[]>(`/drivers/earnings/weekly?weeks=${weeks}`);
            set({ weeklyEarnings: res.data || [] });
        } catch (err) {
            throw err;
        }
    },

    fetchMonthlyEarnings: async (months = 6) => {
        try {
            const res = await api.get<MonthlyEarning[]>(`/drivers/earnings/monthly?months=${months}`);
            set({ monthlyEarnings: res.data || [] });
        } catch (err) {
            throw err;
        }
    },

    fetchEarningsComparison: async (period = 'week') => {
        try {
            const res = await api.get<EarningsComparison>(`/drivers/earnings/comparison?period=${period}`);
            set({ earningsComparison: res.data || null });
        } catch (err) {
            throw err;
        }
    },

    fetchTripEarnings: async (limit = 20, offset = 0) => {
        try {
            const res = await api.get<{ trips: TripEarning[]; limit: number; offset: number }>(`/drivers/earnings/trips?limit=${limit}&offset=${offset}`);
            // Backend returns { trips, limit, offset } — pull out the array.
            // Previously set res.data directly, which left the store holding
            // an object; earnings.tsx then called .map() on it and threw
            // "undefined is not a function".
            set({ tripEarnings: res.data?.trips || [] });
        } catch (err) {
            throw err;
        }
    },

    // ── Chat actions ────────────────────────────────────────────────
    addChatMessage: (msg: ChatMessage) => {
        const { chatMessages } = get();
        // Deduplicate by id — WS and REST may both deliver the same message
        if (chatMessages.some((m) => m.id === msg.id)) return;
        set({ chatMessages: [...chatMessages, msg] });
    },

    setChatMessages: (msgs: ChatMessage[]) => set({ chatMessages: msgs }),

    clearChatMessages: () => set({ chatMessages: [], riderTyping: false }),

    setRiderTyping: (typing: boolean) => set({ riderTyping: typing }),

    // ── Offline hydration ────────────────────────────────────────────
    // Called once on mount (before fetchActiveRide). Restores the last
    // known driver ride state from AsyncStorage so the UI resumes
    // instantly after an app restart mid-trip.
    hydrateDriverRideState: async () => {
        try {
            const raw = await AsyncStorage.getItem(DRIVER_RIDE_KEY);
            if (!raw) return;
            const { rideState, activeRide } = JSON.parse(raw);
            if (!activeRide || DRIVER_TERMINAL_STATES.has(rideState)) {
                await AsyncStorage.removeItem(DRIVER_RIDE_KEY);
                return;
            }
            // Don't overwrite a live offer — the pending-offer hydration
            // (from FCM AsyncStorage) runs before this and sets
            // rideState='ride_offered'. Overwriting it with stale cached
            // state causes the offer to vanish until fetchActiveRide
            // corrects it.
            if (get().rideState === 'ride_offered') return;
            if (!get().activeRide) {
                set({ activeRide, rideState });
            }
        } catch {
            AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
        }
    },

    resetRideState: () => {
        set({
            rideState: 'idle',
            incomingRide: null,
            activeRide: null,
            completedRide: null,
            countdownSeconds: 0,
            chatMessages: [],
            error: null,
        });
        AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
    },

    clearError: () => set({ error: null }),

    rateRider: async (rideId: string, rating: number, comment?: string) => {
        try {
            await api.post(`/drivers/rides/${rideId}/rate-rider`, { rating, comment: comment || '' });
        } catch (err: unknown) {
            set({ error: isAxiosError(err) ? (err.response?.data?.detail || 'Failed to rate rider') : 'Failed to rate rider' });
        }
    },

    // ============ Payout Actions ============
    fetchBankAccount: async () => {
        try {
            const res = await api.get<{ has_bank_account: boolean; bank_account: BankAccount | null }>('/drivers/bank-account');
            set({
                hasBankAccount: res.data.has_bank_account || false,
                bankAccount: res.data.bank_account || null
            });
        } catch (err) {
            console.log('Fetch bank account error:', err);
            set({ hasBankAccount: false, bankAccount: null });
        }
    },

    setBankAccount: async (account: BankAccount): Promise<boolean> => {
        set({ isLoading: true, error: null });
        try {
            await api.post('/drivers/bank-account', account);
            await get().fetchBankAccount();
            return true;
        } catch (err: unknown) {
            set({ error: isAxiosError(err) ? (err.response?.data?.detail || 'Failed to save bank account') : 'Failed to save bank account' });
            return false;
        } finally {
            set({ isLoading: false });
        }
    },

    deleteBankAccount: async (): Promise<boolean> => {
        set({ isLoading: true, error: null });
        try {
            await api.delete('/drivers/bank-account');
            set({ hasBankAccount: false, bankAccount: null });
            return true;
        } catch (err: unknown) {
            set({ error: isAxiosError(err) ? (err.response?.data?.detail || 'Failed to delete bank account') : 'Failed to delete bank account' });
            return false;
        } finally {
            set({ isLoading: false });
        }
    },

    fetchDriverBalance: async () => {
        try {
            const res = await api.get<DriverBalance>('/drivers/balance');
            set({ driverBalance: res.data });
        } catch (err) {
            throw err;
        }
    },

    requestPayout: async (amount: number): Promise<{ success: boolean; error?: string }> => {
        set({ isLoading: true, error: null });
        try {
            await api.post('/drivers/payouts', { amount });
            await get().fetchDriverBalance();
            await get().fetchPayoutHistory();
            return { success: true };
        } catch (err: unknown) {
            const error = isAxiosError(err) ? (err.response?.data?.detail || 'Failed to request payout') : 'Failed to request payout';
            set({ error });
            return { success: false, error };
        } finally {
            set({ isLoading: false });
        }
    },

    fetchPayoutHistory: async (limit = 20, offset = 0) => {
        try {
            const res = await api.get<{ payouts: Payout[] }>(`/drivers/payouts?limit=${limit}&offset=${offset}`);
            set({ payoutHistory: res.data.payouts || [] });
        } catch (err) {
            console.log('Fetch payout history error:', err);
        }
    },

    exportEarnings: async (year?: number): Promise<{ data: string; filename: string } | null> => {
        try {
            const res = await api.get<{ data: string; filename: string }>(`/drivers/earnings/export?year=${year || new Date().getFullYear()}`);
            return { data: res.data.data, filename: res.data.filename };
        } catch (err) {
            console.log('Export earnings error:', err);
            return null;
        }
    },

    // T4A Tax Documents
    fetchT4ASummaries: async () => {
        try {
            const currentYear = new Date().getFullYear();
            const years = [currentYear - 1, currentYear - 2, currentYear - 3].filter(y => y >= 2024);
            set({ availableYears: years });

            const promises = years.map(year =>
                api.get<T4ASummary>(`/drivers/t4a/${year}`).then(res => res.data).catch(() => null)
            );
            const results = await Promise.all(promises);
            const summaries = results.filter((r): r is T4ASummary => r !== null);
            set({ t4aSummaries: summaries });
        } catch (err) {
            console.log('Fetch T4A summaries error:', err);
        }
    },

    fetchT4ADetails: async (year: number) => {
        try {
            set({ selectedYear: year, isLoading: true });
            const res = await api.get<T4ASummary>(`/drivers/t4a/${year}`);
            set({
                t4aSummaries: get().t4aSummaries.map(s =>
                    s.year === year ? res.data : s
                ).length === get().t4aSummaries.length
                    ? get().t4aSummaries
                    : [...get().t4aSummaries, res.data],
                isLoading: false
            });
        } catch (err) {
            console.log('Fetch T4A details error:', err);
            set({ isLoading: false });
        }
    },

    setSelectedYear: (year: number | null) => {
        set({ selectedYear: year });
    },
}));

export default useDriverStore;
