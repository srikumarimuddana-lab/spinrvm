"use client";

import { useEffect, useState } from "react";

/** Saskatoon City Hall — the project's default-region fallback. */
const FALLBACK_LAT = 52.1332;
const FALLBACK_LNG = -106.6700;

export interface AdminLocation {
    lat: number;
    lng: number;
    /** "gps" if the browser granted geolocation, "fallback" if we used the default city. */
    source: "gps" | "fallback";
}

/**
 * Resolve a "where is the admin" point used to bias place-search results.
 *
 * Asks the browser for geolocation once on mount. If the admin denies the
 * prompt or the API is unavailable (insecure context, headless test) we
 * return the configured fallback so e.g. searching "Walmart" still biases
 * to Saskatoon instead of returning generic Canada-wide results.
 *
 * The result is intentionally module-cached per page load (no Context) —
 * we don't want a re-prompt on every modal open, but a hard refresh
 * always re-requests.
 */
let cached: AdminLocation | null = null;
let pendingResolve: Array<(loc: AdminLocation) => void> = [];
let inFlight = false;

function resolveAdminLocation(): Promise<AdminLocation> {
    if (cached) return Promise.resolve(cached);
    if (typeof window === "undefined" || !navigator?.geolocation) {
        cached = { lat: FALLBACK_LAT, lng: FALLBACK_LNG, source: "fallback" };
        return Promise.resolve(cached);
    }
    if (inFlight) {
        return new Promise((res) => pendingResolve.push(res));
    }
    inFlight = true;
    return new Promise((resolve) => {
        const finalize = (loc: AdminLocation) => {
            cached = loc;
            inFlight = false;
            pendingResolve.forEach((r) => r(loc));
            pendingResolve = [];
            resolve(loc);
        };
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                finalize({
                    lat: pos.coords.latitude,
                    lng: pos.coords.longitude,
                    source: "gps",
                });
            },
            () => {
                finalize({ lat: FALLBACK_LAT, lng: FALLBACK_LNG, source: "fallback" });
            },
            { enableHighAccuracy: false, timeout: 5000, maximumAge: 600000 },
        );
    });
}

export function useAdminLocation(): AdminLocation | null {
    const [loc, setLoc] = useState<AdminLocation | null>(cached);
    useEffect(() => {
        let alive = true;
        resolveAdminLocation().then((r) => {
            if (alive) setLoc(r);
        });
        return () => {
            alive = false;
        };
    }, []);
    return loc;
}
