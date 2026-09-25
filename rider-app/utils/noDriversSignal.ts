/**
 * Was this cancel the backend's "no driver accepted in time" auto-cancel?
 *
 * The backend tags that cancel with `cancellation_type: 'no_drivers_found'`
 * on the ride row, the `ride_cancelled` WS message, the `ride_status_changed`
 * broadcast and the push data. The stuck-ride sweeper's older WS payload only
 * had `reason: 'no_drivers_found'`, so that is accepted too. Rider, driver and
 * admin cancels never carry either value.
 */
export const NO_DRIVERS_FOUND = 'no_drivers_found';

export function isNoDriversCancellation(
  signal: { cancellation_type?: unknown; reason?: unknown } | null | undefined,
): boolean {
  if (!signal) return false;
  return signal.cancellation_type === NO_DRIVERS_FOUND || signal.reason === NO_DRIVERS_FOUND;
}
