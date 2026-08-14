"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { adminPlacesAutocomplete, type AdminPlaceBias } from "@/lib/api";

const DEBOUNCE_MS = 300;
const MIN_QUERY_LEN = 3;

interface Prediction {
    place_id: string;
    description: string;
    structured_formatting?: { main_text?: string; secondary_text?: string };
}

function newSessionToken(): string {
    return typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
              const r = (Math.random() * 16) | 0;
              return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
          });
}

export interface UseAdminPlacesAutocompleteResult {
    predictions: Prediction[];
    loading: boolean;
    /** Drop all current results — call when the caller selects a prediction. */
    clear: () => void;
    /** Mint a fresh session token after closing one with a details call. */
    rotateSessionToken: () => void;
    /** Current session token — pass to a place-details request to close the billing session. */
    sessionToken: string;
}

/**
 * Admin-side parallel of @shared/hooks/usePlacesAutocomplete.
 *
 * Same shape and behaviour: 300ms debounce, soft location bias, in-flight
 * cancellation, session-token rotation. Hits POST-equivalent admin proxy
 * via adminPlacesAutocomplete instead of the rider proxy.
 *
 * Cannot share code with the rider/driver hook because admin-dashboard
 * is a Next.js app with its own API client (cookies, admin auth) — and
 * the @shared package depends on React Native primitives.
 */
export function usePlacesAutocomplete(
    input: string,
    bias?: AdminPlaceBias | null,
): UseAdminPlacesAutocompleteResult {
    const [predictions, setPredictions] = useState<Prediction[]>([]);
    const [loading, setLoading] = useState(false);
    const [sessionToken, setSessionToken] = useState<string>(() => newSessionToken());
    const seqRef = useRef(0);
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const searchInput = input.trim();
    const tooShort = searchInput.length < MIN_QUERY_LEN;

    useEffect(() => {
        // Below the minimum length there's nothing to fetch. Rather than
        // setState-ing predictions/loading back to empty here (a direct
        // synchronous state write in the effect body — react-hooks/set-state-in-effect),
        // leave the state alone; the return value below derives the
        // empty/idle view whenever tooShort, so no extra render is needed
        // and any stale predictions/loading are masked without being cleared.
        if (tooShort) {
            return;
        }
        const mySeq = ++seqRef.current;

        debounceRef.current = setTimeout(async () => {
            setLoading(true);
            try {
                const data = await adminPlacesAutocomplete(searchInput, sessionToken, bias ?? null);
                if (mySeq !== seqRef.current) return;
                setPredictions(data?.predictions ?? []);
            } catch {
                if (mySeq !== seqRef.current) return;
                setPredictions([]);
            } finally {
                if (mySeq === seqRef.current) setLoading(false);
            }
        }, DEBOUNCE_MS);

        return () => {
            if (debounceRef.current) clearTimeout(debounceRef.current);
        };
        // We depend on the bias coordinates, NOT the bias object identity —
        // callers pass a freshly-built object every render and we don't want
        // to refetch unless lat/lng/radius actually changed.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [input, sessionToken, bias?.lat, bias?.lng, bias?.radiusMeters]);

    const clear = useCallback(() => {
        setPredictions([]);
        setLoading(false);
    }, []);

    const rotateSessionToken = useCallback(() => {
        setSessionToken(newSessionToken());
    }, []);

    return {
        predictions: tooShort ? [] : predictions,
        loading: tooShort ? false : loading,
        clear,
        rotateSessionToken,
        sessionToken,
    };
}
