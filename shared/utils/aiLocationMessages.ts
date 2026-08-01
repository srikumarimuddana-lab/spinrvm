/**
 * Builders for the self-contained chat messages a tapped AI card sends back
 * to the assistant.
 *
 * The assistant's next turn sees only message text — conversation history
 * never carries tool results — so a tap must embed everything the model
 * needs. In particular it must carry the tapped candidate's exact [lat,lng]
 * verbatim: a prose-only "Use 655 Albert St as my dropoff." forces the model
 * to re-geocode the address, and a street address Google can't pin re-trips
 * the imprecise-address gate on every retry ("please check the exact street
 * address…" forever). The bracketed format is the established transport —
 * the quote-card tap and the map-pin picker use it, the backend PII scrubber
 * preserves it (keep_trip_pins), and prompt rule 6b tells the model to use
 * it verbatim without re-geocoding.
 */
import type { AiAction, FareQuoteOption, LocationSuggestionCandidate } from '../types/ai';

export type LocationRole = 'pickup' | 'dropoff' | null | undefined;

/** The message a tapped location-suggestion candidate sends back to the
 * assistant, e.g. `Use 655 Albert St, Regina [50.44079,-104.61802] as my
 * dropoff.` — or null when the candidate has no usable label. toFixed(5)
 * matches the quote-card/map-pin precedent and the backend's bracketed
 * coordinate pattern. */
export function buildLocationChoiceMessage(
  candidate: Pick<LocationSuggestionCandidate, 'name' | 'address' | 'lat' | 'lng'>,
  role: LocationRole,
): string | null {
  const label = candidate.address || candidate.name;
  if (!label) return null;
  const coords = `[${candidate.lat.toFixed(5)},${candidate.lng.toFixed(5)}]`;
  const suffix = role === 'pickup' ? ' as my pickup' : role === 'dropoff' ? ' as my dropoff' : '';
  return `Use ${label} ${coords}${suffix}.`;
}

type FareQuoteAction = Extract<AiAction, { type: 'fare_quote' }>;

/** The message a tapped quote option sends back to the assistant. The next
 * turn sees only message text, so this must be self-contained — and it must
 * carry the quote's exact [lat,lng] coordinates and vehicle id verbatim so
 * the model books THE priced trip instead of re-geocoding the addresses (the
 * old prose-only message caused a third independent geocode, moving pins and
 * prices between the quote and the confirm card). Shared by the rider app's
 * chat (`bookingProposal.ts` re-exports this) and the admin AI console, which
 * mirrors the rider card so the console shows exactly what the rider would
 * see. */
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
