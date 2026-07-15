"use client";

/**
 * Company-portal session (Spinr for Business).
 *
 * Company users are RIDER accounts (phone OTP) whose corporate_members rows
 * grant org access — the backend /company/{id}/** guards authenticate the
 * rider JWT. This store is fully separate from the staff authStore: mixing
 * them would send company users to the staff /login on 401 and vice versa.
 *
 * Token custody mirrors the admin pattern:
 *   - access token lives in Zustand memory only (never storage)
 *   - a company_token HttpOnly cookie (set via /api/company-auth/set-cookie)
 *     exists solely for the edge middleware's exp check
 *   - the HttpOnly refresh_token cookie set by the backend at verify-otp
 *     drives silentRefresh() through POST /api/v1/auth/refresh
 *   - only the non-sensitive profile/memberships persist to sessionStorage
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export interface CompanyMembershipProfile {
    membership: {
        id: string;
        role: "owner" | "admin" | "member";
        status: string;
        section_id?: string | null;
        policy_override?: boolean;
    };
    // company.status (M2.4): pending_verification | active | suspended |
    // closed — non-active companies gate onto the portal verification page.
    company: { id: string; name: string; status?: string | null };
}

export interface CompanyPortalUser {
    id: string;
    phone?: string;
    first_name?: string;
    last_name?: string;
}

interface CompanyAuthState {
    token: string | null;
    csrfToken: string | null;
    user: CompanyPortalUser | null;
    memberships: CompanyMembershipProfile[];
    isAuthenticated: boolean;
    isLoading: boolean;

    completeLogin: (opts: {
        token: string;
        csrfToken?: string | null;
        user: CompanyPortalUser;
    }) => Promise<void>;
    setMemberships: (memberships: CompanyMembershipProfile[]) => void;
    silentRefresh: () => Promise<boolean>;
    logout: () => Promise<void>;
    markLoaded: () => void;
}

async function writeCompanyCookie(token: string): Promise<void> {
    try {
        await fetch("/api/company-auth/set-cookie", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token }),
        });
    } catch (e) {
        console.error("[companyAuth] set-cookie failed:", e);
    }
}

// MUTEX: concurrent silentRefresh callers (root layout on rehydrate + several
// companyRequest 401s) must share ONE in-flight refresh. Without this the
// second call presents an already-rotated refresh token; the backend's reuse
// detection revokes the whole token family → instant logout.
let _inflightRefresh: Promise<boolean> | null = null;

export const useCompanyAuthStore = create<CompanyAuthState>()(
    persist(
        (set, get) => ({
            token: null,
            csrfToken: null,
            user: null,
            memberships: [],
            isAuthenticated: false,
            isLoading: true,

            completeLogin: async ({ token, csrfToken, user }) => {
                await writeCompanyCookie(token);
                set({
                    token,
                    csrfToken: csrfToken ?? null,
                    user,
                    isAuthenticated: true,
                    isLoading: false,
                });
            },

            setMemberships: (memberships) => set({ memberships }),

            silentRefresh: async () => {
                // Share one in-flight refresh across concurrent callers (mutex).
                if (_inflightRefresh) return _inflightRefresh;

                const doRefresh = async (): Promise<boolean> => {
                    try {
                        // LOCAL Next route (see /api/company-auth/refresh): reads
                        // the HttpOnly spinr_company_rt cookie, proxies to the
                        // backend server-side (so the CSRF middleware — which only
                        // fires on browser-Origin requests — is skipped, letting
                        // refresh work on a rehydrated load with no in-memory CSRF)
                        // and strips the rotated refresh_token from the body.
                        const res = await fetch("/api/company-auth/refresh", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                        });
                        if (!res.ok) return false;
                        const data = await res.json().catch(() => ({}));
                        const newToken: string | undefined = data.token ?? data.access_token;
                        if (!newToken) return false;
                        await writeCompanyCookie(newToken);
                        set({
                            token: newToken,
                            csrfToken: data.csrf_token ?? get().csrfToken,
                            isAuthenticated: true,
                            isLoading: false,
                        });
                        return true;
                    } catch {
                        return false;
                    } finally {
                        _inflightRefresh = null;
                    }
                };

                _inflightRefresh = doRefresh();
                return _inflightRefresh;
            },

            logout: async () => {
                // Revoke the rider refresh token + clear the HttpOnly
                // spinr_company_rt / csrf_token cookies server-side FIRST, so a
                // shared desk machine can't silent-refresh a new session after
                // sign-out. Then clear the middleware's company_token and local
                // state. All best-effort — never block the local sign-out.
                const { token } = get();
                try {
                    await fetch("/api/company-auth/logout", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            ...(token ? { Authorization: `Bearer ${token}` } : {}),
                        },
                    });
                } catch {
                    /* best-effort revoke */
                }
                try {
                    await fetch("/api/company-auth/set-cookie", { method: "DELETE" });
                } catch {
                    /* cookie clear is best-effort */
                }
                set({
                    token: null,
                    csrfToken: null,
                    user: null,
                    memberships: [],
                    isAuthenticated: false,
                    isLoading: false,
                });
            },

            markLoaded: () => set({ isLoading: false }),
        }),
        {
            name: "spinr-company-portal",
            storage: createJSONStorage(() => sessionStorage),
            // Never persist tokens — memory + HttpOnly cookies only.
            partialize: (s) => ({
                user: s.user,
                memberships: s.memberships,
                isAuthenticated: s.isAuthenticated,
            }),
            onRehydrateStorage: () => (state) => {
                // A rehydrated "authenticated" session has no in-memory token;
                // the first companyRequest 401s and silentRefresh restores it
                // from the refresh cookie. Mark loading done either way.
                state?.markLoaded();
            },
        }
    )
);
