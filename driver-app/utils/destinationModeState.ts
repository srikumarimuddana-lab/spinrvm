/**
 * Shared read-side logic for driver destination mode ("heading home").
 *
 * C136 T2: a driver left destination mode on all day and got zero offers
 * because nothing on the main screen said the filter was on. Both the
 * main-screen DestinationModeBanner and the Settings row derive "is it on?"
 * from `isDestinationActive` below so they can never disagree.
 */

/** GET /drivers/destination response (C136 contract). */
export interface DestinationModeResponse {
  destination_mode: boolean;
  destination_address: string | null;
  destination_lat?: number | null;
  destination_lng?: number | null;
  destination_set_at?: string | null;
  /** ISO-8601; server sets a 2h TTL on POST. Missing on pre-C136 backends. */
  destination_expires_at?: string | null;
  /** Server-computed (mode on + coords + expiry in future). Missing on pre-C136 backends. */
  active?: boolean;
  /**
   * settings.destination_mode_enabled (migration 482). false → the feature is
   * hidden: no entry points, no banner, and POST is refused. Missing on older
   * backends (which have no switch) → treated as available.
   */
  enabled?: boolean;
}

/** Whether destination mode may be offered to this driver at all. */
export function isDestinationModeAvailable(d: DestinationModeResponse | null | undefined): boolean {
  return !!d && d.enabled !== false;
}

/** Epoch ms of the expiry, or null when absent/unparseable. */
export function destinationExpiryMs(d: DestinationModeResponse | null | undefined): number | null {
  if (!d?.destination_expires_at) return null;
  const ms = Date.parse(d.destination_expires_at);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * Whether the destination filter is currently limiting this driver's offers.
 *
 * Decision (C136 T2), in order:
 *  0. `enabled === false` → off. The feature is switched off server-side
 *     (migration 482); dispatch ignores any stored destination.
 *  1. `active === false` → off. The server is authoritative.
 *  2. `active === true` → on, UNLESS the expiry we were given has since passed
 *     on the device clock (the response was fetched earlier and the 2h TTL ran
 *     out while the screen sat open) → off.
 *  3. `active` missing (pre-C136 backend): requires `destination_mode`. If an
 *     expiry is present it must be in the future. If there is no expiry at
 *     all we still report ON (the countdown is simply hidden): an old backend
 *     never expires the filter, and the whole point of the banner is that the
 *     driver must know the filter is on — hiding it is exactly the bug.
 */
export function isDestinationActive(
  d: DestinationModeResponse | null | undefined,
  nowMs: number = Date.now(),
): boolean {
  if (!d) return false;
  if (d.enabled === false) return false;
  const exp = destinationExpiryMs(d);
  if (d.active === false) return false;
  if (d.active === true) return exp === null || exp > nowMs;
  if (!d.destination_mode) return false;
  return exp === null || exp > nowMs;
}

/**
 * Remaining time split for display. Rounds UP to whole minutes so the banner
 * never reads "0m" while the filter is still on.
 */
export function remainingParts(expiryMs: number, nowMs: number): { h: number; m: number } {
  const totalMin = Math.max(1, Math.ceil((expiryMs - nowMs) / 60000));
  return { h: Math.floor(totalMin / 60), m: totalMin % 60 };
}
