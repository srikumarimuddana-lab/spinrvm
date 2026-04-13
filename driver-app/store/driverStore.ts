import { create } from 'zustand';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';

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
    estimated_fare: number;
    distance_km: number;
    duration_minutes: number;
    rider_id: string;
    driver_id?: string;
    otp?: string;
    surge_multiplier?: number;
    payment_method?: string;
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
}

export interface EarningsSummary {
    period: string;
    total_earnings: number;
    total_tips: number;
    total_rides: number;
    total_distance_km: number;
    total_duration_minutes: number;
    average_per_ride: number;
}

export interface DailyEarning {
    date: string;
    earnings: number;
    tips: number;
    rides: number;
    distance_km: number;
}

export interface TripEarning {
    ride_id: string;
    pickup_address: string;
    dropoff_address: string;
    distance_km: number;
    duration_minutes: number;
    base_fare: number;
    distance_fare: number;
    time_fare: number;
    driver_earnings: number;
    tip_amount: number;
    rider_rating: number | null;
    completed_at: string;
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
    total_earnings: number;
    available_balance: number;
    pending_payouts: number;
    total_paid_out: number;
    has_bank_account: boolean;
}

export interface Payout {
    id: string;
    amount: number;
    status: string;
    bank_name: string;
    account_last4: string;
    created_at: string;
    processed_at: string | null;
    error_message: string | null;
}

export interface T4ASummary {
    year: number;
    total_earnings: number;
    total_trips: number;
    platform_fees: number;
    net_earnings: number;
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
    earnings: number;
    tips: number;
    rides: number;
    online_hours: number;
    distance_km: number;
}

export interface MonthlyEarning {
    month: string;
    year: number;
    earnings: number;
    tips: number;
    rides: number;
    online_hours: number;
    distance_km: number;
}

export interface EarningsComparison {
    period: string;
    current: { earnings: number; rides: number; tips: number };
    previous: { earnings: number; rides: number; tips: number };
    change_pct: { earnings: number; rides: number; tips: number };
}

interface IncomingRide {
    ride_id: string;
    pickup_address: string;
    dropoff_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_lat: number;
    dropoff_lng: number;
    fare: number;
    distance_km?: number;
    duration_minutes?: number;
    rider_name?: string;
    rider_rating?: number;
}

interface DriverState {
    // Ride state machine
    rideState: RideState;
    incomingRide: IncomingRide | null;
    activeRide: ActiveRide | null;
    completedRide: any | null;
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
    rideHistory: any[];
    historyTotal: number;

    // Loading states
    isLoading: boolean;
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
    fetchRideHistory: (limit?: number, offset?: number) => Promise<void>;
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

    // State management
    resetRideState: () => void;
    rateRider: (rideId: string, rating: number, comment?: string) => Promise<void>;
    submitTip: (rideId: string, amount: number) => Promise<boolean>;
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
    isLoading: false,
    error: null,

    setIncomingRide: (ride) => {
        const countdown = get().configuredCountdownSeconds || FALLBACK_COUNTDOWN;
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
            set({
                rideState: 'navigating_to_pickup',
                incomingRide: null,
                countdownSeconds: 0,
            });
            // Fetch the full active ride data
            await get().fetchActiveRide();
        } catch (err: any) {
            const status = err?.response?.status;
            const detail: string = err?.response?.data?.detail || '';

            // Race-condition handling: another driver beat us to the ride, or
            // the rider cancelled between dispatch and accept. Backend returns
            // 400 with detail "Ride not assigned to you" (see
            // backend/routes/drivers.py accept_ride) or 404 if the ride row is
            // gone. Either way the right UX is to clear the incoming offer,
            // drop back to `idle`, and surface a short, non-alarming toast —
            // NOT leave the driver stuck staring at the accept/decline panel.
            const alreadyTakenDetail = /not assigned|already|no longer|cancelled|canceled/i.test(detail);
            const alreadyTakenStatus = status === 404 || (status === 400 && alreadyTakenDetail);

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
            return { success: true };
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Failed to mark arrival' });
            return { success: false, error: err.response?.data?.detail || 'Failed to mark arrival' };
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
            return true;
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Invalid OTP' });
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
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Failed to start ride' });
        } finally {
            set({ isLoading: false });
        }
    },

    completeRide: async (rideId: string) => {
        set({ isLoading: true, error: null });
        try {
            const res = await api.post(`/drivers/rides/${rideId}/complete`);
            set({
                rideState: 'trip_completed',
                completedRide: res.data,
                activeRide: null,
            });
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Failed to complete ride' });
        } finally {
            set({ isLoading: false });
        }
    },

    cancelRide: async (rideId: string, reason?: string) => {
        set({ isLoading: true, error: null });
        try {
            await api.post(`/drivers/rides/${rideId}/cancel?reason=${encodeURIComponent(reason || '')}`);
            set({ rideState: 'idle', activeRide: null, incomingRide: null });
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Failed to cancel ride' });
        } finally {
            set({ isLoading: false });
        }
    },

    fetchActiveRide: async () => {
        try {
            const res = await api.get('/drivers/rides/active');
            if (res.data && res.data.ride) {
                const ride = res.data.ride;
                let rideState: RideState = 'idle';
                if (ride.status === 'driver_assigned') rideState = 'navigating_to_pickup';
                else if (ride.status === 'driver_accepted') rideState = 'navigating_to_pickup';
                else if (ride.status === 'driver_arrived') rideState = 'arrived_at_pickup';
                else if (ride.status === 'in_progress') rideState = 'trip_in_progress';

                set({ activeRide: res.data, rideState });
            } else {
                set({ activeRide: null });
            }
        } catch {
            // Non-critical — caller state remains unchanged
        }
    },

    fetchRideHistory: async (limit = 20, offset = 0) => {
        try {
            const res = await api.get(`/drivers/rides/history?limit=${limit}&offset=${offset}`);
            set({ rideHistory: res.data.rides || [], historyTotal: res.data.total || 0 });
        } catch (err) {
            console.log('Fetch history error:', err);
        }
    },

    fetchEarnings: async (period = 'day') => {
        try {
            const res = await api.get(`/drivers/earnings?period=${period}`);
            set({ earnings: res.data });
        } catch (err) {
            console.log('Fetch earnings error:', err);
        }
    },

    fetchDailyEarnings: async (days = 7) => {
        try {
            const res = await api.get(`/drivers/earnings/daily?days=${days}`);
            set({ dailyEarnings: res.data || [] });
        } catch (err) {
            console.log('Fetch daily earnings error:', err);
        }
    },

    fetchWeeklyEarnings: async (weeks = 4) => {
        try {
            const res = await api.get(`/drivers/earnings/weekly?weeks=${weeks}`);
            set({ weeklyEarnings: res.data || [] });
        } catch (err) {
            console.log('Fetch weekly earnings error:', err);
        }
    },

    fetchMonthlyEarnings: async (months = 6) => {
        try {
            const res = await api.get(`/drivers/earnings/monthly?months=${months}`);
            set({ monthlyEarnings: res.data || [] });
        } catch (err) {
            console.log('Fetch monthly earnings error:', err);
        }
    },

    fetchEarningsComparison: async (period = 'week') => {
        try {
            const res = await api.get(`/drivers/earnings/comparison?period=${period}`);
            set({ earningsComparison: res.data || null });
        } catch (err) {
            console.log('Fetch earnings comparison error:', err);
        }
    },

    fetchTripEarnings: async (limit = 20, offset = 0) => {
        try {
            const res = await api.get(`/drivers/earnings/trips?limit=${limit}&offset=${offset}`);
            set({ tripEarnings: res.data || [] });
        } catch (err) {
            console.log('Fetch trip earnings error:', err);
        }
    },

    resetRideState: () => {
        set({
            rideState: 'idle',
            incomingRide: null,
            activeRide: null,
            completedRide: null,
            countdownSeconds: 0,
            error: null,
        });
    },

    clearError: () => set({ error: null }),

    rateRider: async (rideId: string, rating: number, comment?: string) => {
        try {
            await api.post(`/drivers/rides/${rideId}/rate-rider`, { rating, comment: comment || '' });
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Failed to rate rider' });
        }
    },

    submitTip: async (rideId: string, amount: number): Promise<boolean> => {
        try {
            await api.post(`/rides/${rideId}/tip`, { amount });
            return true;
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Failed to submit tip' });
            return false;
        }
    },

    // ============ Payout Actions ============
    fetchBankAccount: async () => {
        try {
            const res = await api.get('/drivers/bank-account');
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
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Failed to save bank account' });
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
        } catch (err: any) {
            set({ error: err.response?.data?.detail || 'Failed to delete bank account' });
            return false;
        } finally {
            set({ isLoading: false });
        }
    },

    fetchDriverBalance: async () => {
        try {
            const res = await api.get('/drivers/balance');
            set({ driverBalance: res.data });
        } catch (err) {
            console.log('Fetch balance error:', err);
        }
    },

    requestPayout: async (amount: number): Promise<{ success: boolean; error?: string }> => {
        set({ isLoading: true, error: null });
        try {
            await api.post('/drivers/payouts', { amount });
            await get().fetchDriverBalance();
            await get().fetchPayoutHistory();
            return { success: true };
        } catch (err: any) {
            const error = err.response?.data?.detail || 'Failed to request payout';
            set({ error });
            return { success: false, error };
        } finally {
            set({ isLoading: false });
        }
    },

    fetchPayoutHistory: async (limit = 20, offset = 0) => {
        try {
            const res = await api.get(`/drivers/payouts?limit=${limit}&offset=${offset}`);
            set({ payoutHistory: res.data.payouts || [] });
        } catch (err) {
            console.log('Fetch payout history error:', err);
        }
    },

    exportEarnings: async (year?: number): Promise<{ data: string; filename: string } | null> => {
        try {
            const res = await api.get(`/drivers/earnings/export?year=${year || new Date().getFullYear()}`);
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
                api.get(`/drivers/t4a/${year}`).then(res => res.data).catch(() => null)
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
            const res = await api.get(`/drivers/t4a/${year}`);
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
