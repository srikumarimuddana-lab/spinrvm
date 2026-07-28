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
import type { LocationSuggestionCandidate } from '../types/ai';

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
