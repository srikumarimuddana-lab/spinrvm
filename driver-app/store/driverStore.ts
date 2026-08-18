import { create } from 'zustand';
import api, { getApiErrorMessage } from '@shared/api/client';
// Keep the default import: many test files jest.mock(
// '@shared/config/spinr.config', () => ({ default: {...} })) without a
// matching named 'SpinrConfig' export, so switching to a named import
// breaks those mocks (confirmed in rider-app's utils/aiChat.ts).
// eslint-disable-next-line import/no-named-as-default
import SpinrConfig from '@shared/config/spinr.config';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { recordNonFatal } from '../utils/crashlytics';
import { RideStatus } from '../constants/rideStatus';
import { tripLocationRecorder, type TripLocationBatchAck } from '../utils/tripLocationRecorder';
import { apiLocationBatchTransport } from '../utils/tripLocationTransport';

function isAxiosError(e: unknown): e is { response?: { status?: number; data?: { detail?: unknown } }; message?: string } {
  return typeof e === 'object' && e !== null;
}

function completionConfirmationFromError(error: unknown): { distanceBand?: string } | null {
    if (!isAxiosError(error) || error.response?.status !== 409) return null;
    const detail = error.response.data?.detail;
    if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
    const code = (detail as { code?: unknown }).code;
    if (code !== 'completion_confirmation_required') return null;
    const distanceBand = (detail as { distance_band?: unknown }).distance_band;
    return { distanceBand: typeof distanceBand === 'string' ? distanceBand : undefined };
}

const DRIVER_RIDE_KEY = '@spinr:driver_active_ride';
// Upper bound on the pre-completion outbox drain. Long enough for a full
// 500-point batch on a slow cell link, short enough that a dead network
// never traps the driver on the completion screen.
const COMPLETION_FLUSH_TIMEOUT_MS = 8_000;
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

const _startTripLocationRecording = async (rideId: string) => {
  try {
    await tripLocationRecorder.startRide(rideId);
  } catch (recordingError: unknown) {
    // The backend transition is already authoritative. Keep the trip active and
    // let the dashboard's idempotent recorder effect retry without hiding the
    // local storage failure from diagnostics.
    recordNonFatal(recordingError, {
      store: 'driverStore',
      action: 'startTripLocationRecording',
      rideId,
    });
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
    // Rider quiet-ride preference (migration 62). Rides along with the ride
    // row so it stays visible on the active ride, not just the offer.
    quiet_mode?: boolean;
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
    incentives?: { name: string; bonus_amount: number; incentive_type: string }[];
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
    location_ack?: TripLocationBatchAck | null;
    legacy_client_missing_tail?: boolean;
    completion_distance_band?: string | null;
    completion_fix_rejection?: string | null;
}

export type OffRouteConfirmation =
    | 'rider_requested_stop'
    | 'changed_destination'
    | 'emergency'
    | 'location_unavailable';

export interface CompleteRideResult {
    confirmationRequired: boolean;
    distanceBand?: string;
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
    total_referral_bonuses: string; // MoneyString — the referral-only slice of total_bonuses
    total_cancel_fees: string; // MoneyString
    total_tax: string; // MoneyString
    total_rides: number;
    total_distance_km: number;
    total_duration_minutes: number;
    average_per_ride: string; // MoneyString
    elapsed_days: number; // Days in this window — fixed for today/week/month, measured from the earliest ride for 'all'. Drives the driver-app's "per day" averages.
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
    /** MoneyString — payments made by the previous Spinr app (synced from
     *  Stripe). History only: not part of total_earnings or the balance. */
    previous_app_paid_total?: string;
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
    // Rider requested a quiet ride (minimal conversation) — migration 62.
    // Surfaced as a badge in the offer panel so the driver knows before
    // accepting; purely informational, no dispatch filtering.
    quiet_mode?: boolean;
    // C35: true when this ride was originally booked in advance (still true
    // after it has since transitioned to searching/dispatched — same
    // "originally scheduled" semantics used elsewhere in the codebase).
    // Surfaced as a "Pre-booked" badge in the offer panel; purely
    // informational, no dispatch/matching behavior change.
    is_scheduled?: boolean;
    // Per-offer countdown sourced from the dispatch payload — overrides
    // the cached configuredCountdownSeconds so an admin-changed timeout
    // takes effect on the very next offer, not the next cold start.
    countdown_seconds?: number;
    offer_expires_at?: string;
    surge_multiplier?: number;
    incentives?: { name: string; bonus_amount: number; incentive_type: string }[];
    total_bonus?: number;
    quest_hint?: {
        title: string;
        current_value: number;
        target_value: number;
        progress_pct: number;
        reward_amount: number;
    } | null;
    payment_method?: string;
    planned_route_polyline?: [number, number][] | null;
    service_area_polygon?: { lat: number; lng: number }[] | null;
}

interface DriverState {
    // Ride state machine
    rideState: RideState;
    incomingRide: IncomingRide | null;
    activeRide: ActiveRide | null;
    completedRide: CompletedRideData | null;
    countdownSeconds: number;

    // ── Monotonic ride-event ordering (V4, issue #11) ────────────────
    // Highest ride_status_changed version applied, plus the ride it belongs
    // to. rides.version is per-ride (each ride has its own sequence, bumped by
    // the migration-225 trigger), so a new ride resets the baseline. Internal
    // (underscore-prefixed) — only shouldApplyRideEvent reads/writes these.
    _lastEventRideId: string | null;
    _lastEventVersion: number;

    // Server-driven operational config (populated by applyDriverConfig on mount).
    // These override the module-level fallbacks without requiring a rebuild.
    configuredCountdownSeconds: number;
    configuredPickupRadiusMeters: number;

    // Earnings
    earnings: EarningsSummary | null;
    // Earnings cached per period key ('today' | 'week' | 'month' | 'all' | …) so
    // switching Activity pills can show a previously-loaded period instantly
    // instead of refetching. `earnings` mirrors the most recent fetch.
    earningsByPeriod: Record<string, EarningsSummary>;
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
    declineRide: (rideId: string, reason?: string) => Promise<void>;
    arriveAtPickup: (rideId: string, driverLat?: number, driverLng?: number) => Promise<{ success: boolean; distance?: number; error?: string }>;
    verifyOTP: (rideId: string, otp: string) => Promise<boolean>;
    startRide: (rideId: string) => Promise<void>;
    completeRide: (rideId: string, offRouteConfirmation?: OffRouteConfirmation) => Promise<CompleteRideResult>;
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
    fetchPayoutHistory: (limit?: number, offset?: number) => Promise<void>;

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
    shouldApplyRideEvent: (rideId: string | undefined, version: number | undefined) => boolean;
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
    // Monotonic ride-event ordering baseline (V4). -1 so version 0 (a ride's
    // first bump) is strictly greater and applies.
    _lastEventRideId: null,
    _lastEventVersion: -1,
    // Server-config fallbacks — overwritten by applyDriverConfig() after
    // the first /drivers/config fetch.
    configuredCountdownSeconds: FALLBACK_COUNTDOWN,
    configuredPickupRadiusMeters: FALLBACK_PICKUP_RADIUS_METERS,
    earnings: null,
    earningsByPeriod: {},
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
            // getApiErrorMessage covers plain `detail`, structured
            // `error.message`, and RateLimitError shapes — a bare
            // `data.detail` read drops SpinrException-style rejections.
            const detail: string = getApiErrorMessage(err, '');

            // Race-condition handling: another driver beat us to the ride, or
            // the rider cancelled between dispatch and accept. Backend returns
            // 400 with detail "Ride not assigned to you" (see
            // backend/routes/drivers.py accept_ride) or 404 if the ride row is
            // gone. Either way the right UX is to clear the incoming offer,
            // drop back to `idle`, and surface a short, non-alarming toast —
            // NOT leave the driver stuck staring at the accept/decline panel.
            // 409 is the atomic-claim conflict (SpinrException RESOURCE_CONFLICT,
            // "Ride already accepted by another driver") — previously unhandled
            // here, which left a genuine race-loser stuck on the offer panel
            // with a raw error string.
            const alreadyTakenDetail = /not assigned|already|no longer|cancelled|canceled/i.test(detail);
            const alreadyTakenStatus =
                status === 404 || status === 409 || (status === 400 && alreadyTakenDetail);

            if (!alreadyTakenStatus) {
                recordNonFatal(err, { store: 'driverStore', action: 'acceptRide' });
            }
            if (alreadyTakenStatus) {
                // Defense-in-depth: a duplicate accept request (double-tap,
                // notification action + in-app tap, network retry) can 409 even
                // though THIS driver won the ride. Verify against the server
                // before telling the driver someone else took it — the false
                // toast was misleading winning drivers.
                try {
                    await get().fetchActiveRide();
                    const st = get();
                    const ownsThisRide =
                        st.activeRide?.ride?.id === rideId &&
                        ['navigating_to_pickup', 'arrived_at_pickup', 'trip_in_progress'].includes(st.rideState);
                    // fetchActiveRide already set + persisted the post-accept
                    // state — nothing to clean up, just skip the false toast.
                    if (ownsThisRide) {
                        return;
                    }
                } catch {
                    // Verification fetch failed — fall through to the taken toast.
                }
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

    declineRide: async (rideId: string, reason?: string) => {
        try {
            // Optional reason (e.g. 'service_animal', reported via the
            // offer card's long-press flag). Omit the body entirely when
            // there's no reason — mirrors cancelRide below — so the default
            // fast decline (single tap, auto-decline-on-timeout) keeps
            // posting exactly the same request it always has.
            const trimmed = reason?.trim();
            if (trimmed) {
                await api.post(`/drivers/rides/${rideId}/decline`, { reason: trimmed });
            } else {
                await api.post(`/drivers/rides/${rideId}/decline`);
            }
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
            const detail = getApiErrorMessage(err, 'Failed to mark arrival');
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
            await _startTripLocationRecording(rideId);
            await get().fetchActiveRide();
            _persistDriverState('trip_in_progress', get().activeRide);
            return true;
        } catch (err: unknown) {
            set({ error: getApiErrorMessage(err, 'Invalid OTP') });
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
            await _startTripLocationRecording(rideId);
            await get().fetchActiveRide();
            _persistDriverState('trip_in_progress', get().activeRide);
        } catch (err: unknown) {
            set({ error: getApiErrorMessage(err, 'Failed to start ride') });
        } finally {
            set({ isLoading: false });
        }
    },

    completeRide: async (rideId: string, offRouteConfirmation?: OffRouteConfirmation) => {
        set({ isLoading: true, error: null });
        try {
            try {
                // Drain the durable outbox before the server stamps
                // ride_completed_at, so route quality/distance is computed
                // from the full tail instead of whatever the last periodic
                // flush happened to cover. Bounded and non-fatal: on
                // timeout or upload failure the points stay in SQLite for
                // the retention-bounded retry path.
                await tripLocationRecorder.flushPendingWithTimeout(
                    apiLocationBatchTransport,
                    COMPLETION_FLUSH_TIMEOUT_MS,
                );
            } catch (flushError: unknown) {
                recordNonFatal(flushError, { store: 'driverStore', action: 'completeRidePreFlush' });
            }
            const completion = await tripLocationRecorder.captureCompletionFix(rideId);
            const res = await api.post<CompletedRideData>(`/drivers/rides/${rideId}/complete`, {
                completion_fix: completion.point,
                final_session_id: completion.point?.recording_session_id ?? null,
                final_sequence_number: completion.point?.sequence_number ?? null,
                pending_outbox_count: completion.pendingCount,
                off_route_confirmation: offRouteConfirmation ?? null,
            });
            try {
                if (res.data.location_ack) {
                    await tripLocationRecorder.applyAcknowledgement(res.data.location_ack);
                }
            } catch (outboxError: unknown) {
                // The server completion is authoritative. Keep the durable
                // points for the normal retry path and record the local sync
                // failure without GPS values.
                recordNonFatal(outboxError, { store: 'driverStore', action: 'completeRideLocationAck' });
            } finally {
                try {
                    // Closing only ends future capture; queued history remains
                    // available for delayed, retention-bounded upload.
                    await tripLocationRecorder.closeRide(rideId);
                } catch (outboxError: unknown) {
                    recordNonFatal(outboxError, { store: 'driverStore', action: 'closeCompletedRideLocationSession' });
                }
            }
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
                // A completed ride changes every period's earnings/trip counts —
                // drop the Activity per-period cache so the next view refetches
                // fresh instead of showing pre-ride numbers under any pill.
                earningsByPeriod: {},
            });
            AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
            return { confirmationRequired: false };
        } catch (err: unknown) {
            const confirmation = completionConfirmationFromError(err);
            if (confirmation) {
                return { confirmationRequired: true, ...confirmation };
            }
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
                    set({ rideState: 'trip_completed', activeRide: null, incomingRide: null, chatMessages: [], earningsByPeriod: {} });
                    AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
                    return { confirmationRequired: false };
                }
            }
            recordNonFatal(err, { store: 'driverStore', action: 'completeRide' });
            set({ error: getApiErrorMessage(err, 'Failed to complete ride') });
            return { confirmationRequired: false };
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
                // A cancellation changes trip counts (and any cancellation fee),
                // so invalidate the Activity per-period earnings cache too.
                earningsByPeriod: {},
            });
            AsyncStorage.removeItem(DRIVER_RIDE_KEY).catch(() => {});
        } catch (err: unknown) {
            recordNonFatal(err, { store: 'driverStore', action: 'cancelRide' });
            set({ error: getApiErrorMessage(err, 'Failed to cancel ride') });
        } finally {
            set({ isLoading: false, isCancellingRide: false });
        }
    },

    fetchActiveRide: async () => {
        try {
            // A live local offer (set by the WS/FCM handler) is authoritative
            // for the PRE-accept phase: an HTTP poll that still reports
            // 'driver_assigned' must not reset its countdown, and a poll that
            // returns no active ride must not flash-dismiss the panel. But an
            // authoritative FORWARD transition (driver_accepted / driver_arrived
            // / in_progress) reported by the server MUST win over a stale local
            // ride_offered — otherwise the driver is stranded on the offer panel
            // after the ride already advanced (issue #5). So we snapshot the
            // live-offer flag, fetch, then preserve the offer only while the
            // server agrees it's still pending; the no-active-ride case is
            // handled by the ride_offered guard in the else-branch below.
            const _cur = get();
            const _hasLiveOffer =
                _cur.rideState === 'ride_offered' && !!_cur.incomingRide && _cur.countdownSeconds > 2;

            const res = await api.get<ActiveRide | null>('/drivers/rides/active');
            if (res.data && res.data.ride) {
                const ride = res.data.ride;
                const rider = res.data.rider;

                if (_hasLiveOffer && ride.status === RideStatus.DRIVER_ASSIGNED) {
                    // Still pending for this driver — keep the live countdown
                    // rather than letting the poll reset it.
                    return;
                }

                // `driver_assigned` means the backend dispatched the offer to
                // this driver but they have NOT clicked accept yet — resume
                // the ride_offered countdown screen instead of jumping to the
                // post-accept navigation panel.
                if (ride.status === RideStatus.DRIVER_ASSIGNED) {
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
                            quiet_mode: ride.quiet_mode ?? existing?.quiet_mode,
                            is_scheduled: (ride as any).is_scheduled ?? existing?.is_scheduled,
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
                if (ride.status === RideStatus.DRIVER_ACCEPTED) rideState = 'navigating_to_pickup';
                else if (ride.status === RideStatus.DRIVER_ARRIVED) rideState = 'arrived_at_pickup';
                else if (ride.status === RideStatus.IN_PROGRESS) rideState = 'trip_in_progress';

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
            // Keep both the current `earnings` and the per-period cache so the
            // Activity tab can render a revisited pill from cache instantly.
            set((s) => ({
                earnings: res.data,
                earningsByPeriod: { ...s.earningsByPeriod, [period]: res.data },
            }));
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

    // Decide whether an incoming ride event should be applied, dropping stale /
    // out-of-order duplicates by the server-authoritative monotonic version
    // (V4, issue #11). Called from the WS ride_status_changed handler.
    //
    // Single-slot tracker: it remembers the baseline for one ride at a time, so
    // it only orders events within a CONTIGUOUS run of the same ride. This is
    // sound because a driver is assigned one ride at a time (a ride reaches a
    // terminal state before the next is offered) and events for one ride arrive
    // in order on the single WS connection — so two rides never interleave here.
    // Even if a stale event for a previous ride slipped through after the tracker
    // moved to a new ride, the only side-effects (resetRideState / fetchEarnings)
    // are idempotent. If driver-facing events for MULTIPLE concurrent rides ever
    // become possible, switch to a per-ride map.
    shouldApplyRideEvent: (rideId, version) => {
        // No version → no ordering info (un-migrated backend, or an event type
        // that doesn't stamp one). Apply unconditionally so behaviour matches
        // pre-V4, and leave the baseline untouched.
        if (typeof version !== 'number') return true;
        const { _lastEventRideId, _lastEventVersion } = get();
        // A different ride starts its own version sequence — versions are not
        // comparable across rides, so reset the baseline and apply.
        if (_lastEventRideId !== rideId) {
            set({ _lastEventRideId: rideId ?? null, _lastEventVersion: version });
            return true;
        }
        // Same ride: apply only if strictly newer than the highest applied.
        if (version > _lastEventVersion) {
            set({ _lastEventVersion: version });
            return true;
        }
        // Stale or duplicate — drop.
        return false;
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
            set({ error: getApiErrorMessage(err, 'Failed to rate rider') });
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
            set({ error: getApiErrorMessage(err, 'Failed to save bank account') });
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
            set({ error: getApiErrorMessage(err, 'Failed to delete bank account') });
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

    fetchPayoutHistory: async (limit = 20, offset = 0) => {
        try {
            const res = await api.get<{ payouts: Payout[] }>(`/drivers/payouts?limit=${limit}&offset=${offset}`);
            set({ payoutHistory: res.data.payouts || [] });
        } catch (err) {
            console.log('Fetch payout history error:', err);
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
