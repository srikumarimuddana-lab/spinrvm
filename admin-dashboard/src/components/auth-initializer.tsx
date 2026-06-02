"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/store/authStore";

// Paths where admin auth should never be bootstrapped.
// The tracking page is public — calling initAuth() there only generates
// a 401 on /api/admin/auth/refresh and floods the Vercel function log.
const PUBLIC_PREFIXES = ["/track/"];

/**
 * Mounts once at the root layout level and triggers initAuth() to bootstrap
 * the admin session from the HttpOnly RT cookie and non-HttpOnly CSRF cookie.
 * No auth state is read from or written to sessionStorage.
 * Skipped entirely on public pages (e.g. /track/*).
 */
export function AuthInitializer() {
    const initAuth = useAuthStore((s) => s.initAuth);
    const pathname = usePathname();

    useEffect(() => {
        const isPublicPage = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
        if (!isPublicPage) {
            initAuth();
        }
    }, []); // eslint-disable-line react-hooks/exhaustive-deps
    return null;
}
