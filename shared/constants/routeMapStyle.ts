/**
 * Route-map styling shared by every screen that renders a ride route in an
 * in-app MapView (rider ride-details, rider ride-completed, driver
 * ride-detail). Matches the backend Google Static Maps snapshot look — one
 * red actual-route stroke, green pickup / red dropoff pins — so the live
 * MapView fallback and the immutable snapshot image read as the same map.
 *
 * Do not restyle a single screen inline: two rides rendering the same route
 * in different colors is exactly the inconsistency this module removes.
 */

/** Actual (GPS-observed) route stroke — same red as the snapshot path. */
export const ACTUAL_ROUTE_STROKE = {
  strokeColor: '#EE2B2B',
  strokeWidth: 4,
} as const;

/** Planned (pre-trip estimate) route stroke — dashed neutral gray. */
export const PLANNED_ROUTE_STROKE = {
  strokeColor: '#6B7280',
  strokeWidth: 3,
  lineDashPattern: [8, 6] as number[],
} as const;

/** Marker pin fills — match the snapshot's P (green) / D (red) / C (amber). */
export const ROUTE_PIN_COLORS = {
  pickup: '#10B981',
  dropoff: '#EF4444',
  completion: '#F59E0B',
} as const;
