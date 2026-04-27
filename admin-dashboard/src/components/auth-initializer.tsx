"use client";

import { useEffect } from "react";
import { useAuthStore } from "@/store/authStore";

/**
 * Mounts once at the root layout level and triggers initAuth() to bootstrap
 * the admin session from the HttpOnly RT cookie and non-HttpOnly CSRF cookie.
 * No auth state is read from or written to sessionStorage.
 */
export function AuthInitializer() {
    const initAuth = useAuthStore((s) => s.initAuth);
    useEffect(() => {
        initAuth();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps
    return null;
}
