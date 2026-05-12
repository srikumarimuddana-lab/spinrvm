"use client";

import { useEffect, useState } from "react";

export interface AdminLocation {
    lat: number;
    lng: number;
    /** Always "gps" — the hook returns null when a real fix isn't available. */
    source: "gps";
}

/**
 * Resolve a "where is the admin" point used to bias place-search results.
 *
 * Asks the browser for geolocation once on mount. Returns null when the admin
 * denies the prompt, the API is unavailable (insecure context, headless test),
 * or the request times out. Callers must treat null as "no bias" — never
 * substitute a hardcoded city, because that mis-biases place search for any
 * admin outside that city (e.g. an admin in Regina searching "Walmart" would
 * see Saskatoon stores).
 *
 * Only successful fixes are module-cached per page load — so a transient
 * failure does not poison the rest of the session. A hard refresh always
 * re-requests.
 */
let cached: AdminLocation | null = null;
let pendingResolve: Array<(loc: AdminLocation | null) => void> = [];
let inFlight = false;

function resolveAdminLocation(): Promise<AdminLocation | null> {
    if (cached) return Promise.resolve(cached);
    if (typeof window === "undefined" || !navigator?.geolocation) {
        return Promise.resolve(null);
    }
    if (inFlight) {
        return new Promise((res) => pendingResolve.push(res));
    }
    inFlight = true;
    return new Promise((resolve) => {
        const finalize = (loc: AdminLocation | null) => {
            if (loc) cached = loc;
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
                finalize(null);
            },
            { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 },
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
