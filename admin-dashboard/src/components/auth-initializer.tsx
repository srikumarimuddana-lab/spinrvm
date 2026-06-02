"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/store/authStore";
import { isTrackingHost } from "@/lib/track-host";

// Paths where admin auth should never be bootstrapped.
// The tracking page is public — calling initAuth() there only generates
// a 401 on /api/admin/auth/refresh and floods the Vercel function log.
const PUBLIC_PREFIXES = ["/track/"];

/**
 * Mounts once at the root layout level and triggers initAuth() to bootstrap
 * the admin session from the HttpOnly RT cookie and non-HttpOnly CSRF cookie.
 * No auth state is read from or written to sessionStorage.
 *
 * Skipped entirely on the public tracking surface:
 *   - the tracking domain (track.spinr.ca), where the browser URL is the
 *     clean /{token} form (rewritten server-side to /track/{token}, so the
 *     pathname check below would not catch it), and
 *   - any direct /track/* path on the admin domain.
 */
export function AuthInitializer() {
    const initAuth = useAuthStore((s) => s.initAuth);
    const pathname = usePathname();

    useEffect(() => {
        const onTrackingDomain =
            typeof window !== "undefined" && isTrackingHost(window.location.host);
        const onTrackPath = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
        if (!onTrackingDomain && !onTrackPath) {
            initAuth();
        }
    }, []); // eslint-disable-line react-hooks/exhaustive-deps
    return null;
}
