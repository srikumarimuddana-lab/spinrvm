/**
 * Single reusable Google Places Autocomplete hook for rider/driver apps.
 *
 * Centralises:
 *   - 300ms input debounce
 *   - session-token lifecycle (one token per typing session, rotated when
 *     the caller resolves a prediction via `rotateSessionToken`)
 *   - in-flight request cancellation on input/bias change
 *   - location bias formatting (defers to @shared/api/places.buildPlacesQuery)
 *
 * Returns the raw Google predictions so callers stay UI-agnostic.
 *
 * The admin dashboard has a parallel hook (admin-dashboard/src/hooks/
 * usePlacesAutocomplete.ts) with the same shape — it cannot import this
 * one because @shared/api/client is React-Native specific.
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import api from '@shared/api/client';
import { buildPlacesQuery, type PlaceBias, type PlacePrediction } from '@shared/api/places';
import { newPlacesSessionToken } from '@shared/utils/placesSession';

const DEBOUNCE_MS = 300;
const MIN_QUERY_LEN = 2;

export interface UsePlacesAutocompleteResult {
  predictions: PlacePrediction[];
  loading: boolean;
  /** Drop all current results — call when the caller selects a prediction. */
  clear: () => void;
  /** Mint a fresh session token after closing one with a details call. */
  rotateSessionToken: () => void;
  /** Current session token — pass to a place-details request to close the billing session. */
  sessionToken: string;
}

export function usePlacesAutocomplete(
  input: string,
  bias?: PlaceBias | null,
): UsePlacesAutocompleteResult {
  const [predictions, setPredictions] = useState<PlacePrediction[]>([]);
  const [loading, setLoading] = useState(false);
  const sessionTokenRef = useRef<string>(newPlacesSessionToken());
  // Bump on bias/input change so a late-arriving response can't overwrite a newer one.
  const requestSeqRef = useRef(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!input || input.length < MIN_QUERY_LEN) {
      setPredictions([]);
      setLoading(false);
      return;
    }

    if (debounceRef.current) clearTimeout(debounceRef.current);
    const mySeq = ++requestSeqRef.current;

    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const query = buildPlacesQuery(input, sessionTokenRef.current, bias ?? null);
        const params = new URLSearchParams();
        params.set('input', query.input);
        params.set('session_token', query.session_token);
        if (query.location) params.set('location', query.location);
        if (query.radius != null) params.set('radius', String(query.radius));

        const { data } = await api.get<{ predictions: PlacePrediction[] }>(
          `/maps/places/autocomplete?${params.toString()}`,
        );
        // Discard if a newer request has started since this one fired.
        if (mySeq !== requestSeqRef.current) return;
        setPredictions(data?.predictions ?? []);
      } catch {
        if (mySeq !== requestSeqRef.current) return;
        setPredictions([]);
      } finally {
        if (mySeq === requestSeqRef.current) setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [input, bias?.lat, bias?.lng, bias?.radiusMeters]);

  const clear = useCallback(() => {
    setPredictions([]);
    setLoading(false);
  }, []);

  const rotateSessionToken = useCallback(() => {
    sessionTokenRef.current = newPlacesSessionToken();
  }, []);

  return {
    predictions,
    loading,
    clear,
    rotateSessionToken,
    sessionToken: sessionTokenRef.current,
  };
}
