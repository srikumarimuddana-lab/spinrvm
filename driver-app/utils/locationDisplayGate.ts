/**
 * Pure helpers for the driver map's display-quality gate and the
 * speed-adaptive follow zoom. Kept out of useDriverDashboard so the
 * decisions are unit-testable without mocking the location stack.
 */

// A fix with reported horizontal accuracy worse than this is a cell/Wi-Fi
// guess, not GPS — rendering it walks the car off the road sideways (the
// 2026-08-28 Albert St "sliding" report). Typical GNSS fixes are 3–15 m.
export const MAX_DISPLAY_ACCURACY_M = 50;

// Never freeze the marker forever on poor signal (parkade, urban canyon):
// after this long without an accepted fix, show the rough one anyway —
// an approximate car beats a car stuck blocks behind.
export const ACCURACY_OVERRIDE_MS = 30_000;

// Floor for the on-map speed readout (index.tsx's speedChip). coords.speed is
// Doppler-derived and noisy near zero — a genuinely stationary vehicle
// (dashboard mount, weaker sky view) has been live-reported showing ~2.2 m/s
// (8 km/h) while parked. 3 m/s clears that with margin while staying well
// below any real driving speed, so a driver who actually starts moving still
// sees the chip appear promptly.
export const MIN_DISPLAYED_SPEED_MPS = 3; // ~10.8 km/h

// Age past which a fix's coords.speed is presumed stale and the chip clamps
// to 0 instead of showing it. `watchPositionAsync`'s distanceInterval gate
// (LOCATION_CONFIGS in useDriverDashboard.ts) goes fully quiet at a genuine
// standstill — no new fix means `location.coords.speed` just holds whatever
// the last real reading was, which was live-reported as a value (e.g.
// 57 km/h) sitting on screen for minutes after the vehicle actually stopped.
// 6s comfortably clears every LOCATION_CONFIGS timeInterval (worst case 10s
// for trip_completed, but the chip is speed==0 relevant states only; the
// next-worst live states are 8s/4s) while being short enough that a stop
// reads as a stop within a couple of ticks, not minutes. Paired with
// displaySpeedKmh's caller re-evaluating on a 1s UI tick (index.tsx), not
// only when a new fix arrives — a frozen `location` object can't otherwise
// tell the chip time has passed.
export const MAX_SPEED_FIX_AGE_MS = 6_000;

/**
 * Speed-chip display value, km/h, clamped for both known failure modes:
 * GPS noise near zero (MIN_DISPLAYED_SPEED_MPS) and a stale fix held past
 * MAX_SPEED_FIX_AGE_MS with no fresh reading to overwrite it. Pure so the
 * staleness behavior is unit-testable without a device or a location mock.
 */
export function displaySpeedKmh(
  speedMps: number | null | undefined,
  fixTimestampMs: number | null | undefined,
  nowMs: number,
): number {
  const age = typeof fixTimestampMs === 'number' && Number.isFinite(fixTimestampMs)
    ? nowMs - fixTimestampMs
    : null;
  if (age != null && age > MAX_SPEED_FIX_AGE_MS) return 0;
  const sp = speedMps ?? 0;
  return sp >= MIN_DISPLAYED_SPEED_MPS ? Math.round(sp * 3.6) : 0;
}

/**
 * Should this fix move the marker / feed the live WS position?
 * (Durable trip capture is NOT gated by this — capture-before-filter.)
 */
export function shouldDisplayFix(
  accuracy: number | null | undefined,
  msSinceLastDisplayed: number,
): boolean {
  if (accuracy == null || !Number.isFinite(accuracy) || accuracy <= 0) {
    // No accuracy reported — trust the platform rather than hide the driver.
    return true;
  }
  if (accuracy <= MAX_DISPLAY_ACCURACY_M) return true;
  return msSinceLastDisplayed >= ACCURACY_OVERRIDE_MS;
}

// Follow-mode zoom tiers: stopped/creeping → street-level detail (turn lanes,
// one-ways render on the vector map at ≥17); city driving → block context;
// fast roads → corridor overview. Zero API cost — Google's vector tiles
// already carry this detail, zoom just reveals it.
export interface ZoomTier {
  /** Inclusive upper speed bound for the tier, m/s. */
  maxSpeedMps: number;
  zoom: number;
}

export const FOLLOW_ZOOM_TIERS: readonly ZoomTier[] = [
  { maxSpeedMps: 2, zoom: 17.5 },   // stopped / rolling up to an intersection
  { maxSpeedMps: 9, zoom: 16.75 },  // residential / city streets (~32 km/h)
  { maxSpeedMps: Infinity, zoom: 16 }, // arterials & highway
];

// Hysteresis: to LEAVE the current tier the speed must clear the boundary by
// this margin, so a fix oscillating around a boundary doesn't pump the zoom.
const TIER_HYSTERESIS_MPS = 0.75;

/**
 * Pick the follow zoom tier for a speed reading, sticky around boundaries.
 * `speedMps` may be null/-1 (unknown — treat as stopped only if we have no
 * previous tier, otherwise keep the previous tier).
 */
export function zoomTierForSpeed(
  speedMps: number | null | undefined,
  prevTier: number | null,
): number {
  const sp =
    speedMps != null && Number.isFinite(speedMps) && speedMps >= 0 ? speedMps : null;
  if (sp == null) return prevTier ?? 0;

  const naive = FOLLOW_ZOOM_TIERS.findIndex((t) => sp <= t.maxSpeedMps);
  if (prevTier == null || naive === prevTier) return naive;

  if (naive > prevTier) {
    // Speeding up into a farther-out tier: must clear the previous tier's
    // ceiling by the margin.
    return sp > FOLLOW_ZOOM_TIERS[prevTier].maxSpeedMps + TIER_HYSTERESIS_MPS
      ? naive
      : prevTier;
  }
  // Slowing down into a closer tier: must drop below that tier's ceiling by
  // the margin.
  return sp <= FOLLOW_ZOOM_TIERS[naive].maxSpeedMps - TIER_HYSTERESIS_MPS
    ? naive
    : prevTier;
}
