/**
 * Analytics — no-op stub.
 *
 * Firebase Analytics has been removed from the rider and driver apps
 * (user decision: reduce Firebase surface area, stop blank-screen / native-
 * module issues in Expo Go / dev builds). All calls here are no-ops so
 * existing call sites continue to compile without any runtime cost.
 *
 * The exported `Analytics` object preserves the original method signatures
 * so re-enabling (e.g. swapping to Segment, PostHog, or Sentry
 * performance) is a drop-in change inside this module — no changes needed
 * at the hundreds of call sites scattered across the apps.
 */

const noop = async (..._args: unknown[]): Promise<void> => { /* removed */ };

export const Analytics = {
  // ── Auth funnel ──────────────────────────────────────────────────────────
  signupStarted: () => noop(),
  otpVerified: () => noop(),
  profileCompleted: () => noop(),
  login: (_method: 'otp' = 'otp') => noop(),

  // ── Ride funnel ──────────────────────────────────────────────────────────
  rideRequested: (_params: { vehicle_type: string; estimated_fare: number }) => noop(),
  driverAccepted: (_params: { wait_seconds: number }) => noop(),
  rideStarted: () => noop(),
  rideCompleted: (_params: { fare: number; distance_km?: number; duration_min?: number }) => noop(),
  rideCancelled: (_params: { reason?: string; stage: string }) => noop(),

  // ── Payments ─────────────────────────────────────────────────────────────
  paymentInitiated: (_params: { method: string; amount: number }) => noop(),
  paymentCompleted: (_params: { method: string; amount: number }) => noop(),

  // ── Fare split ───────────────────────────────────────────────────────────
  fareSplitCreated: (_params: { split_count: number; total_fare: number }) => noop(),
  fareSplitAccepted: () => noop(),
  fareSplitPaid: (_params: { amount: number }) => noop(),

  // ── Quests / loyalty ─────────────────────────────────────────────────────
  questJoined: (_params: { quest_id: string; quest_type: string }) => noop(),
  questRewardClaimed: (_params: { reward_amount: number }) => noop(),
  loyaltyPointsRedeemed: (_params: { points: number; credit_amount: number }) => noop(),

  // ── Driver events (driver-app) ────────────────────────────────────────────
  driverWentOnline: () => noop(),
  driverWentOffline: () => noop(),
  driverAcceptedOffer: (_params: { wait_seconds?: number } = {}) => noop(),
  driverRejectedOffer: () => noop(),
  driverArrivedAtPickup: () => noop(),
} as const;

export default Analytics;
