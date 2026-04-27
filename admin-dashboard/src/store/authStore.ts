import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

// The access token lives in Zustand memory only (for Authorization headers).
// The HttpOnly `admin_token` cookie used by Edge middleware (src/proxy.ts) is
// set and rotated exclusively by the Next.js BFF routes:
//   POST /api/admin/auth/login   → sets admin_token + spinr_admin_rt
//   POST /api/admin/auth/refresh → rotates both cookies
//   POST /api/admin/auth/logout  → clears both cookies
// JS never writes to document.cookie for auth purposes (A-PE-P2-1).
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const REFRESH_BEFORE_EXPIRY_MS = 5 * 60 * 1000; // refresh 5 min before access token expires
const IDLE_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes (F-19)

let _refreshTimer: ReturnType<typeof setTimeout> | null = null;

function cancelRefreshTimer() {
    if (_refreshTimer) {
        clearTimeout(_refreshTimer);
        _refreshTimer = null;
    }
}

function scheduleTokenRefresh(expiryIso: string, silentRefresh: () => Promise<void>) {
    cancelRefreshTimer();
    const msUntilExpiry = new Date(expiryIso).getTime() - Date.now();
    const delay = Math.max(0, msUntilExpiry - REFRESH_BEFORE_EXPIRY_MS);
    _refreshTimer = setTimeout(silentRefresh, delay);
}

// ── Idle session timeout (F-19) ─────────────────────────────────
// Track user activity and auto-logout after IDLE_TIMEOUT_MS of inactivity.
// Event listeners are registered in the browser only (SSR-safe guard).
let _idleTimer: ReturnType<typeof setTimeout> | null = null;
let _idleLogoutFn: (() => void) | null = null;

const _ACTIVITY_EVENTS = ['mousemove', 'keydown', 'mousedown', 'touchstart', 'scroll'] as const;

function _resetIdleTimer() {
    if (!_idleLogoutFn) return;
    if (_idleTimer) clearTimeout(_idleTimer);
    _idleTimer = setTimeout(_idleLogoutFn, IDLE_TIMEOUT_MS);
}

function startIdleWatch(logoutFn: () => void) {
    if (typeof window === 'undefined') return;
    stopIdleWatch();
    _idleLogoutFn = logoutFn;
    for (const evt of _ACTIVITY_EVENTS) {
        window.addEventListener(evt, _resetIdleTimer, { passive: true });
    }
    _idleTimer = setTimeout(_idleLogoutFn, IDLE_TIMEOUT_MS);
}

function stopIdleWatch() {
    if (_idleTimer) {
        clearTimeout(_idleTimer);
        _idleTimer = null;
    }
    if (typeof window !== 'undefined') {
        for (const evt of _ACTIVITY_EVENTS) {
            window.removeEventListener(evt, _resetIdleTimer);
        }
    }
    _idleLogoutFn = null;
}

interface User {
    id: string;
    phone?: string;
    email?: string;
    first_name?: string;
    last_name?: string;
    role: string;
    modules?: string[];
    profile_complete?: boolean;
}

interface AuthState {
    user: User | null;
    // Access token lives in memory only — never written to sessionStorage.
    token: string | null;
    // CSRF double-submit token — read from the csrf_token cookie on refresh,
    // sent as X-CSRF-Token on every state-changing request.
    csrfToken: string | null;
    isAuthenticated: boolean;
    isLoading: boolean;
    setUser: (user: User | null) => void;
    setToken: (token: string | null) => void;
    setCsrfToken: (token: string | null) => void;
    scheduleRefresh: (accessExpiresAt: string) => void;
    setLoading: (loading: boolean) => void;
    logout: () => void;
    checkAuth: () => Promise<void>;
    silentRefresh: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()(
    persist(
        (set, get) => ({
            user: null,
            token: null,
            csrfToken: null,
            isAuthenticated: false,
            isLoading: true,

            setUser: (user) => {
                set({
                    user,
                    isAuthenticated: !!user,
                    isLoading: false
                });
                if (user) {
                    startIdleWatch(get().logout);
                } else {
                    stopIdleWatch();
                }
            },

            setToken: (token) => {
                set({ token });
                // Cookie is set/cleared server-side by the BFF routes — no
                // document.cookie writes here (A-PE-P2-1).
            },

            setCsrfToken: (token) => {
                set({ csrfToken: token });
            },

            scheduleRefresh: (accessExpiresAt) => {
                scheduleTokenRefresh(accessExpiresAt, get().silentRefresh);
            },

            setLoading: (loading) => {
                set({ isLoading: loading });
            },

            logout: () => {
                cancelRefreshTimer();
                stopIdleWatch();
                const csrfToken = get().csrfToken;
                set({
                    user: null,
                    token: null,
                    csrfToken: null,
                    isAuthenticated: false,
                    isLoading: false
                });
                // Clear the HttpOnly RT cookie server-side (fire-and-forget).
                fetch("/api/admin/auth/logout", {
                    method: "POST",
                    headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
                }).catch(() => {});
            },

            // Exchange the HttpOnly refresh cookie for a new short-lived access
            // token via the /api/admin/auth/refresh Next.js route (which reads the
            // cookie server-side so JS never sees the token value). Called
            // proactively by the scheduled timer and on every page reload.
            // On failure, calls logout() to clear state.
            silentRefresh: async () => {
                try {
                    // Read the csrf_token cookie (set on last login/refresh) to
                    // bootstrap the CSRF header before the in-memory token is available.
                    const csrfCookie = typeof document !== "undefined"
                        ? (document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)?.[1] ?? null)
                        : null;
                    const csrfHeader = csrfCookie ?? get().csrfToken;
                    const res = await fetch(`${API_BASE}/api/admin/auth/refresh`, {
                        method: "POST",
                        headers: csrfHeader ? { "X-CSRF-Token": csrfHeader } : {},
                    });
                    if (!res.ok) {
                        get().logout();
                        return;
                    }
                    const data: { token: string; access_expires_at: string; csrf_token?: string } = await res.json();
                    set({ token: data.token, csrfToken: data.csrf_token ?? null });
                    scheduleTokenRefresh(data.access_expires_at, get().silentRefresh);
                    startIdleWatch(get().logout);
                } catch {
                    get().logout();
                }
            },

            checkAuth: async () => {
                const token = get().token;
                if (!token) {
                    set({ isLoading: false });
                    return;
                }

                try {
                    const res = await fetch(`${API_BASE}/api/admin/auth/session`, {
                        headers: {
                            'Authorization': `Bearer ${token}`,
                            'Content-Type': 'application/json',
                        },
                    });

                    if (res.ok) {
                        const data = await res.json();
                        if (data.authenticated && data.user) {
                            set({
                                user: data.user,
                                isAuthenticated: true,
                                isLoading: false
                            });
                            startIdleWatch(get().logout);
                        } else {
                            get().logout();
                        }
                    } else {
                        get().logout();
                    }
                } catch (error) {
                    console.error('Auth check failed:', error);
                    get().logout();
                }
            },
        }),
        {
            name: 'auth-storage',
            storage: createJSONStorage(() => sessionStorage),
            // Only persist the user profile / auth flag for optimistic rendering.
            // The access token and refresh token are intentionally NOT stored in
            // sessionStorage. On page reload, silentRefresh() re-acquires a fresh
            // access token by presenting the HttpOnly RT cookie to the Next.js
            // /api/admin/auth/refresh route — JS never sees the RT value.
            partialize: (state) => ({
                user: state.user,
                isAuthenticated: state.isAuthenticated,
            }),
            onRehydrateStorage: () => (state) => {
                if (!state) return;
                if (state.isAuthenticated) {
                    // Verify the session is still valid and get a fresh access token.
                    // silentRefresh calls logout() (sets isLoading=false) on failure.
                    state.silentRefresh().then(() => {
                        useAuthStore.getState().setLoading(false);
                    });
                } else {
                    state.setLoading(false);
                }
            },
        }
    )
);
