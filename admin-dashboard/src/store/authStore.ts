import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

// ── Cookie helpers ───────────────────────────────────────────────
// The JWT is dual-written to memory (Zustand) AND to an `admin_token`
// cookie (for the Next.js middleware at src/middleware.ts, which runs on
// the edge and cannot read sessionStorage). The access token is NOT
// persisted to sessionStorage — only the opaque refresh token is stored
// there. On page reload, silentRefresh() exchanges the refresh token for
// a new short-lived access token before any API calls are made.
const COOKIE_NAME = 'admin_token';
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 8; // 8 hours — standard admin session
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const REFRESH_BEFORE_EXPIRY_MS = 5 * 60 * 1000; // refresh 5 min before access token expires
const IDLE_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes (F-19)

let _refreshTimer: ReturnType<typeof setTimeout> | null = null;

// NOTE (F-02): document.cookie cannot set HttpOnly — the flag is only settable
// by the server. A full fix requires a Next.js API route (/api/auth/set-cookie)
// that accepts the token and responds with Set-Cookie: HttpOnly; Secure.
// Until that API route is implemented, SameSite=Strict reduces XSS exposure
// by blocking cross-site requests from carrying this cookie.
function setAuthCookie(token: string) {
    if (typeof document === 'undefined') return;
    const secure = typeof window !== 'undefined' && window.location.protocol === 'https:';
    const parts = [
        `${COOKIE_NAME}=${encodeURIComponent(token)}`,
        'path=/',
        `max-age=${COOKIE_MAX_AGE_SECONDS}`,
        'SameSite=Strict',
    ];
    if (secure) parts.push('Secure');
    document.cookie = parts.join('; ');
}

function clearAuthCookie() {
    if (typeof document === 'undefined') return;
    document.cookie = `${COOKIE_NAME}=; path=/; max-age=0; SameSite=Strict`;
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
    // Access token lives in memory only — never written to sessionStorage.
    token: string | null;
    // Refresh token is persisted to sessionStorage (opaque, not a signed JWT).
    refresh_token: string | null;
    isAuthenticated: boolean;
    isLoading: boolean;
    setUser: (user: User | null) => void;
    setToken: (token: string | null) => void;
    setRefreshToken: (refreshToken: string | null) => void;
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
            refresh_token: null,
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

            setRefreshToken: (refreshToken) => {
                set({ refresh_token: refreshToken });
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
                set({
                    user: null,
                    token: null,
                    refresh_token: null,
                    isAuthenticated: false,
                    isLoading: false
                });
            },

            // Exchange the persisted refresh token for a new short-lived access
            // token. Called proactively by the scheduled timer and on page reload
            // from onRehydrateStorage. On failure, calls logout() to clear state.
            silentRefresh: async () => {
                const refreshToken = get().refresh_token;
                if (!refreshToken) {
                    get().logout();
                    return;
                }
                try {
                    const res = await fetch(`${API_BASE}/api/admin/auth/refresh`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ refresh_token: refreshToken }),
                    });
                    if (!res.ok) {
                        get().logout();
                        return;
                    }
                    const data: { token: string; refresh_token: string; access_expires_at: string } = await res.json();
                    set({ token: data.token, refresh_token: data.refresh_token });
                    setAuthCookie(data.token);
                    scheduleTokenRefresh(data.access_expires_at, get().silentRefresh);
                    // Keep the idle watch alive after a silent token refresh.
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
            // Only persist the refresh token + user profile. The access token
            // (a signed JWT) is intentionally excluded so XSS cannot steal it
            // from sessionStorage. On page reload, silentRefresh() re-acquires
            // a fresh access token from the server before rendering.
            partialize: (state) => ({
                refresh_token: state.refresh_token,
                user: state.user,
                isAuthenticated: state.isAuthenticated,
            }),
            onRehydrateStorage: () => (state) => {
                if (!state) return;
                if (state.refresh_token) {
                    // Silently re-acquire an access token, then set isLoading=false.
                    // silentRefresh calls logout() (which sets isLoading=false) on
                    // failure, so .then() covers only the success path.
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
