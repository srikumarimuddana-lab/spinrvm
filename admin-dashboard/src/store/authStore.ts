import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

// ── Cookie helpers ───────────────────────────────────────────────
// The JWT is dual-written to sessionStorage (for Zustand/api.ts) AND to
// an `admin_token` cookie (for the Next.js middleware at src/middleware.ts,
// which runs on the edge and cannot read sessionStorage). Both sides must
// stay in lockstep — see middleware.ts for the full rationale.
const COOKIE_NAME = 'admin_token';
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 8; // 8 hours — standard admin session

function setAuthCookie(token: string) {
    if (typeof document === 'undefined') return;
    const secure = typeof window !== 'undefined' && window.location.protocol === 'https:';
    const parts = [
        `${COOKIE_NAME}=${encodeURIComponent(token)}`,
        'path=/',
        `max-age=${COOKIE_MAX_AGE_SECONDS}`,
        'SameSite=Lax',
    ];
    if (secure) parts.push('Secure');
    document.cookie = parts.join('; ');
}

function clearAuthCookie() {
    if (typeof document === 'undefined') return;
    document.cookie = `${COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax`;
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
    token: string | null;
    isAuthenticated: boolean;
    isLoading: boolean;
    setUser: (user: User | null) => void;
    setToken: (token: string | null) => void;
    setLoading: (loading: boolean) => void;
    logout: () => void;
    checkAuth: () => Promise<void>;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const useAuthStore = create<AuthState>()(
    persist(
        (set, get) => ({
            user: null,
            token: null,
            isAuthenticated: false,
            isLoading: true,

            setUser: (user) => {
                set({
                    user,
                    isAuthenticated: !!user,
                    isLoading: false
                });
            },

            setToken: (token) => {
                set({ token });
                if (token) {
                    setAuthCookie(token);
                } else {
                    clearAuthCookie();
                }
            },

            setLoading: (loading) => {
                set({ isLoading: loading });
            },

            logout: () => {
                clearAuthCookie();
                set({
                    user: null,
                    token: null,
                    isAuthenticated: false,
                    isLoading: false
                });
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
            partialize: (state) => ({
                token: state.token,
                user: state.user,
                isAuthenticated: state.isAuthenticated,
            }),
            onRehydrateStorage: () => (state) => {
                // Check auth when store is rehydrated from sessionStorage.
                // Also re-sync the cookie so middleware sees the session
                // after a page reload (Zustand rehydrates from sessionStorage,
                // but the cookie may have been cleared independently).
                if (state) {
                    if (state.token) {
                        setAuthCookie(state.token);
                    }
                    state.checkAuth();
                }
            },
        }
    )
);