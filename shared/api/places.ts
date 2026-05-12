/**
 * Cross-surface types for Google Places Autocomplete.
 *
 * The backend already exposes two compatible proxies:
 *   GET /maps/places/autocomplete         (rider/driver auth)
 *   GET /api/admin/places/autocomplete    (admin auth)
 *
 * Both return the same shape (Google's prediction object verbatim) and now
 * both accept the same location-bias params (location="lat,lng" + radius
 * in metres, default 50000). The shared hook keys off these types so the
 * caller does not care which proxy it talks to.
 */

export interface StructuredFormatting {
  main_text: string;
  secondary_text?: string;
}

export interface PlacePrediction {
  place_id: string;
  description: string;
  structured_formatting?: StructuredFormatting;
  types?: string[];
}

export interface PlaceDetails {
  lat: number;
  lng: number;
  formatted_address: string;
}

export interface PlaceBias {
  lat: number;
  lng: number;
  /** Soft-bias radius in metres. Defaults to 20km if unset by the caller. */
  radiusMeters?: number;
}

export interface PlacesAutocompleteQuery {
  input: string;
  session_token: string;
  /** "lat,lng" pre-formatted for the proxy. */
  location?: string;
  /** Metres. Backend clamps to [1000, 100000]. */
  radius?: number;
}

/**
 * Build the query-string params from a high-level bias object. Centralised
 * here so every surface formats lat/lng identically (truncated to keep URLs
 * short and to avoid leaking high-precision GPS through query-string logs).
 */
export function buildPlacesQuery(
  input: string,
  sessionToken: string,
  bias?: PlaceBias | null,
): PlacesAutocompleteQuery {
  const q: PlacesAutocompleteQuery = { input, session_token: sessionToken };
  if (bias && Number.isFinite(bias.lat) && Number.isFinite(bias.lng)) {
    // 4 decimals (~11m) is plenty for soft-bias and avoids over-precise GPS in URLs.
    q.location = `${bias.lat.toFixed(4)},${bias.lng.toFixed(4)}`;
    // Default 50km radius covers a typical metro area (Regina, Saskatoon, etc.)
    // Backend uses strictbounds=true, so anything outside this radius won't appear.
    q.radius = Math.min(Math.max(bias.radiusMeters ?? 50000, 1000), 100000);
  }
  return q;
}
