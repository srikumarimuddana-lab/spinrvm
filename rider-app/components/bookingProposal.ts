/**
 * Pure logic for the in-chat booking proposal card (kept component-free so
 * jest exercises it without a renderer — same pattern as attemptRidePayment).
 *
 * The card's contract: it fetches the AUTHORITATIVE estimate through the
 * normal /rides/estimate path (surge-locked estimate_token included in the
 * store's estimates) and books via rideStore.createRide() — the exact code
 * path of the manual booking flow. The AI never books; the rider's tap does.
 */
import type { BookingProposal } from '@shared/types/ai';

/** Quotes auto-refresh on this cadence so a stale estimate_token (surge
 * lock) is never submitted. Matches the booking screens' refresh habit. */
export const QUOTE_REFRESH_SECONDS = 60;

export interface EstimateLike {
  vehicle_type: { id: string; name: string };
  grand_total?: string;
  total_fare: string;
  surge_multiplier?: number;
  eta_minutes?: number;
  available?: boolean;
}

/** Choose which estimate the card preselects: the proposal's vehicle type
 * when it exists and is available, otherwise the first available, otherwise
 * null (card shows a no-availability state). */
export function pickEstimate<T extends EstimateLike>(
  estimates: T[],
  proposal: BookingProposal,
): T | null {
  if (!estimates.length) return null;
  const available = estimates.filter((e) => e.available !== false);
  if (!available.length) return null;
  if (proposal.vehicle_type_id) {
    const match = available.find((e) => e.vehicle_type.id === proposal.vehicle_type_id);
    if (match) return match;
  }
  return available[0];
}

/** Rider-facing display amount: grand_total (fees+taxes) when present. */
export function displayFare(estimate: EstimateLike): string {
  return estimate.grand_total ?? estimate.total_fare;
}

export interface BookingErrorDescriptor {
  message: string;
  /** Deep link the card offers when the standard flow must take over. */
  link?: { label: string; href: string };
}

/** Map createRide failures to card copy. createRide already guards the
 * active-ride (409-style) and unpaid-ride (402) cases with thrown errors. */
export function mapBookingError(error: unknown): BookingErrorDescriptor {
  const message = error instanceof Error ? error.message : String(error ?? '');
  if (/already.*active/i.test(message)) {
    return {
      message: 'You already have a ride in progress.',
      link: { label: 'View my ride', href: '/(tabs)' },
    };
  }
  if (/402|unpaid|outstanding/i.test(message)) {
    return {
      message: 'You have an unpaid ride to settle first.',
      link: { label: 'Open payments', href: '/(tabs)' },
    };
  }
  if (/missing ride details/i.test(message)) {
    return {
      message: "Something's missing from this trip — use the booking screen instead.",
      link: { label: 'Open booking', href: '/(tabs)' },
    };
  }
  return {
    message: "The booking didn't go through — nothing was charged. Try again or use the booking screen.",
    link: { label: 'Open booking', href: '/(tabs)' },
  };
}
