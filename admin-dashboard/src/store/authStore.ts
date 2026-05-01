import { create } from 'zustand';

// ── Cookie helpers ───────────────────────────────────────────────
// The JWT is dual-written to memory (Zustand) AND to an `admin_token`
// HttpOnly cookie (for the Next.js middleware at src/middleware.ts, which
// runs on the edge and cannot read in-memory state). No auth state is
// written to sessionStorage — on every page load, initAuth() reads the
// non-HttpOnly spinr_admin_csrf cookie to bootstrap the in-memory CSRF
// token, then calls silentRefresh() to exchange the HttpOnly RT cookie
// for a new short-lived access token. JS never sees the RT value.
const API_BASE = "";
const REFRESH_BEFORE_EXPIRY_MS = 5 * 60 * 1000; // refresh 5 min before access token expires
const IDLE_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes (F-19)

let _refreshTimer: ReturnType<typeof setTimeout> | null = null;
// Prevent initAuth from running more than once per page load.
let _authInitialized = false;
// Exposed only for unit tests — resets the once-per-load guard.
export function _resetAuthInitializedForTesting() { _authInitialized = false; }

// Cookie helpers — delegate to the Next.js API route so the cookie is set
// HttpOnly by the server (F-02). document.cookie cannot set HttpOnly.
// Fire-and-forget: the in-memory token drives API calls; the HttpOnly cookie
// drives middleware route-protection checks on hard refreshes/SSR.
function setAuthCookie(token: string): void {
    if (typeof window === 'undefined') return;
    fetch('/api/auth/set-cookie', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
    }).catch((error) => {
        console.error('[AuthStore] Failed to set auth cookie:', error);
    });
}

function clearAuthCookie(): void {
    if (typeof window === 'undefined') return;
    fetch('/api/auth/set-cookie', { method: 'DELETE' }).catch(() => { /* non-critical */ });
}

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
    // Access token lives in memory only — never written to storage.
    token: string | null;
    // CSRF double-submit token — in-memory only, bootstrapped from the
    // non-HttpOnly spinr_admin_csrf cookie on page load by initAuth().
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
    initAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()((set, get) => ({
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
        if (token) {
            setAuthCookie(token);
        } else {
            clearAuthCookie();
        }
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
        clearAuthCookie();
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
            ...(csrfToken ? { headers: { "X-CSRF-Token": csrfToken } } : {}),
        }).catch(() => {});
    },

    // Exchange the HttpOnly refresh cookie for a new short-lived access
    // token via the /api/admin/auth/refresh Next.js route (which reads the
    // cookie server-side so JS never sees the token value). Called
    // proactively by the scheduled timer and by initAuth() on every page load.
    // On failure, calls logout() to clear state and set isLoading=false.
    silentRefresh: async () => {
        try {
            const csrfToken = get().csrfToken;
            const res = await fetch(`${API_BASE}/api/admin/auth/refresh`, {
                method: "POST",
                ...(csrfToken ? { headers: { "X-CSRF-Token": csrfToken } } : {}),
            });
            if (!res.ok) {
                // 401 = no RT cookie (fresh visit, no session to restore) — just
                // stop the loading spinner without destroying any existing state.
                if (res.status === 401) {
                    set({ isLoading: false });
                    return;
                }
                get().logout();
                return;
            }
            const data: { token: string; access_expires_at: string; csrf_token?: string } = await res.json();
            // Always overwrite csrfToken — if absent the session is broken; next
            // refresh will fail CSRF and trigger a clean logout.
            set({ token: data.token, csrfToken: data.csrf_token ?? null });
            setAuthCookie(data.token);
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

    // Bootstrap auth on every page load. Runs exactly once per page lifetime.
    // 1. Reads spinr_admin_csrf from document.cookie (non-HttpOnly, safe for JS)
    //    to restore the in-memory CSRF token before calling silentRefresh.
    // 2. Calls silentRefresh() to exchange the HttpOnly RT cookie for a new
    //    access token. On failure, logout() is called (sets isLoading=false).
    // 3. On success, calls checkAuth() to populate the user profile from the
    //    /api/admin/auth/session endpoint.
    initAuth: async () => {
        if (_authInitialized) return;
        _authInitialized = true;

        if (typeof window !== 'undefined') {
            const csrfCookie = document.cookie
                .split('; ')
                .find(c => c.startsWith('spinr_admin_csrf='))
                ?.split('=')[1];
            if (csrfCookie) {
                set({ csrfToken: csrfCookie });
            }
        }

        await get().silentRefresh();
        if (get().token) {
            await get().checkAuth();
        }
    },
}));
