/**
 * Pure logic for the in-chat booking proposal card (kept component-free so
 * jest exercises it without a renderer — same pattern as attemptRidePayment).
 *
 * The card's contract: it fetches the AUTHORITATIVE estimate through the
 * normal /rides/estimate path (surge-locked estimate_token included in the
 * store's estimates) and books via rideStore.createRide() — the exact code
 * path of the manual booking flow. The AI never books; the rider's tap does.
 */
import type { AiAction, BookingProposal, FareQuoteOption } from '@shared/types/ai';
import {
  grandTotalOf,
  promoDiscountForEstimate,
  type EstimateFareLike,
  type PromoLike,
} from '../utils/promoDiscount';

type FareQuoteAction = Extract<AiAction, { type: 'fare_quote' }>;

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

/** Post-promo display amount — the same math the manual booking screen uses
 * (ride-options.tsx: grand_total − promoDiscountForEstimate). The AI quote
 * card shows post-promo totals, so the confirm card must too: showing the
 * pre-promo grand_total beside a promo chip overstated the fare ($20.92
 * quoted, $39.44 on the card in the incident). Display-only — the server
 * recomputes and enforces the discount at booking. */
export function displayFareWithPromo(
  estimate: EstimateFareLike,
  promo: PromoLike | null | undefined,
): string {
  const discounted = Math.max(0, grandTotalOf(estimate) - promoDiscountForEstimate(promo, estimate));
  return discounted.toFixed(2);
}

/** The message a tapped quote option sends back to the assistant. The next
 * turn sees only message text, so this must be self-contained — and it must
 * carry the quote's exact [lat,lng] coordinates and vehicle id verbatim so
 * the model books THE priced trip instead of re-geocoding the addresses (the
 * old prose-only message caused a third independent geocode, moving pins and
 * prices between the quote and the confirm card). */
export function buildQuoteBookingMessage(quote: FareQuoteAction, option: FareQuoteOption): string {
  const endpoint = (label: 'from' | 'to', address?: string, lat?: number, lng?: number): string => {
    const coords =
      typeof lat === 'number' && typeof lng === 'number'
        ? `[${lat.toFixed(5)},${lng.toFixed(5)}]`
        : '';
    const place = [address, coords].filter(Boolean).join(' ');
    return place ? ` ${label} ${place}` : '';
  };
  const vehicle = option.vehicle_type ?? 'recommended option';
  const vehicleId = option.vehicle_type_id ? ` (vehicle id ${option.vehicle_type_id})` : '';
  const promo = option.promo_code ? ` with promo ${option.promo_code}` : '';
  const total = option.final_total ? `, total $${option.final_total}` : '';
  return (
    `Book the ${vehicle}${vehicleId}` +
    `${endpoint('from', quote.pickup_address, quote.pickup_lat, quote.pickup_lng)}` +
    `${endpoint('to', quote.dropoff_address, quote.dropoff_lat, quote.dropoff_lng)}` +
    `${promo}${total}.`
  );
}

export type ProposalPaymentMethod = 'card' | 'wallet';

export function paymentMethodForProposal(proposal: BookingProposal): ProposalPaymentMethod {
  return proposal.payment_method === 'wallet' ? 'wallet' : 'card';
}

export function scheduledDateForProposal(proposal: BookingProposal): Date | null {
  if (!proposal.scheduled_time) return null;
  const date = new Date(proposal.scheduled_time);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function promoForProposal(proposal: BookingProposal) {
  const code = proposal.promo_code?.trim();
  if (!code) return null;
  return {
    id: `ai-${code}`,
    code,
    discount_type: 'unknown',
    discount_value: 0,
  };
}

/** Where the card lands after a successful booking. Immediate rides hand
 * off to the live tracking screen (callers must use router.replace so back
 * can't return to the chat mid-ride); scheduled rides return null and stay
 * on the chat — there is no active ride to track yet. */
export function postBookingRoute(
  rideId: string | undefined,
  scheduledDate: Date | null,
): { pathname: string; params: { rideId: string } } | null {
  if (scheduledDate || !rideId) return null;
  return { pathname: '/driver-arriving', params: { rideId } };
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
