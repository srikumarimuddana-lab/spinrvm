/**
 * Analytics — typed event catalog over Firebase Analytics (SPR-03/3c).
 *
 * Web:    Firebase Web SDK analytics (firebase/analytics — already installed).
 * Native: @react-native-firebase/analytics when available (EAS builds);
 *         silently no-ops in Expo Go and test environments.
 *
 * All events follow Firebase's recommended naming convention (snake_case).
 * Add new events here; call sites import `Analytics` and call typed methods.
 *
 * Funnel instrumented:
 *   signup_started → otp_verified → profile_completed → login →
 *   ride_requested → driver_accepted → ride_started → ride_completed →
 *   payment_completed
 */
import { Platform } from 'react-native';

// ── Analytics instance resolution ────────────────────────────────────────────

type AnalyticsInstance =
  | { kind: 'web'; instance: import('firebase/analytics').Analytics }
  | { kind: 'native'; instance: any };

let _instance: AnalyticsInstance | null | 'pending' = 'pending';

async function getInstance(): Promise<AnalyticsInstance | null> {
  if (_instance !== 'pending') return _instance;

  try {
    if (Platform.OS === 'web') {
      const [{ getAnalytics, isSupported }, { app: firebaseApp }] = await Promise.all([
        import('firebase/analytics'),
        import('../config/firebaseConfig'),
      ]);
      if (!firebaseApp || !(await isSupported())) {
        _instance = null;
        return null;
      }
      _instance = { kind: 'web', instance: getAnalytics(firebaseApp) };
    } else {
      // Native: @react-native-firebase/analytics (only in EAS builds)
      const rnAnalytics = require('@react-native-firebase/analytics').default;
      _instance = { kind: 'native', instance: rnAnalytics() };
    }
  } catch {
    // Firebase not configured, blocked by test env, or native module absent.
    _instance = null;
  }
  return _instance;
}

async function track(eventName: string, params?: Record<string, unknown>): Promise<void> {
  try {
    const inst = await getInstance();
    if (!inst) return;

    if (inst.kind === 'web') {
      const { logEvent } = await import('firebase/analytics');
      logEvent(inst.instance, eventName, params as any);
    } else {
      await inst.instance.logEvent(eventName, params);
    }
  } catch {
    // Analytics must never crash the app.
  }
}

// ── Typed event catalog ───────────────────────────────────────────────────────

export const Analytics = {
  // ── Auth funnel ──────────────────────────────────────────────────────────
  /** User tapped "Send Verification Code" on the login screen. */
  signupStarted: () => track('signup_started'),

  /** OTP was verified successfully; user now has a session token. */
  otpVerified: () => track('otp_verified'),

  /** User completed profile-setup and has a full profile. */
  profileCompleted: () => track('profile_completed'),

  /** User successfully authenticated (existing or new account). */
  login: (method: 'otp' = 'otp') => track('login', { method }),

  // ── Ride funnel ──────────────────────────────────────────────────────────
  /** Rider submitted a ride request (after payment confirm). */
  rideRequested: (params: { vehicle_type: string; estimated_fare: number }) =>
    track('ride_requested', params),

  /** A driver accepted the ride offer. */
  driverAccepted: (params: { wait_seconds: number }) =>
    track('driver_accepted', params),

  /** Ride status became in_progress (driver picked up rider). */
  rideStarted: () => track('ride_started'),

  /** Ride status became completed. */
  rideCompleted: (params: { fare: number; distance_km?: number; duration_min?: number }) =>
    track('ride_completed', params),

  /** Ride was cancelled by rider or driver. */
  rideCancelled: (params: { reason?: string; stage: string }) =>
    track('ride_cancelled', params),

  // ── Payments ─────────────────────────────────────────────────────────────
  /** User tapped pay on the payment confirm screen. */
  paymentInitiated: (params: { method: string; amount: number }) =>
    track('payment_initiated', params),

  /** Payment settled (wallet deducted / charge succeeded). */
  paymentCompleted: (params: { method: string; amount: number }) =>
    track('payment_completed', params),

  // ── Fare split ───────────────────────────────────────────────────────────
  fareSplitCreated: (params: { split_count: number; total_fare: number }) =>
    track('fare_split_created', params),

  fareSplitAccepted: () => track('fare_split_accepted'),

  fareSplitPaid: (params: { amount: number }) => track('fare_split_paid', params),

  // ── Quests / loyalty ─────────────────────────────────────────────────────
  questJoined: (params: { quest_id: string; quest_type: string }) =>
    track('quest_joined', params),

  questRewardClaimed: (params: { reward_amount: number }) =>
    track('quest_reward_claimed', params),

  loyaltyPointsRedeemed: (params: { points: number; credit_amount: number }) =>
    track('loyalty_points_redeemed', params),

  // ── Driver events (driver-app) ────────────────────────────────────────────
  driverWentOnline: () => track('driver_went_online'),

  driverWentOffline: () => track('driver_went_offline'),

  driverAcceptedOffer: (params: { wait_seconds?: number } = {}) =>
    track('driver_accepted_offer', params),

  driverRejectedOffer: () => track('driver_rejected_offer'),

  driverArrivedAtPickup: () => track('driver_arrived_at_pickup'),
} as const;

export default Analytics;
