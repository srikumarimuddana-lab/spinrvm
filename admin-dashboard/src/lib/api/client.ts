// Shared fetch client for every admin-dashboard API module. Extracted from the
// (formerly monolithic) src/lib/api.ts so per-domain files (src/lib/api/*.ts)
// can share one auth/CSRF/refresh/error-shape implementation instead of each
// reinventing it. src/lib/api.ts re-exports everything here so existing
// imports (`import { ... } from "@/lib/api"`) are unaffected.

// Always use relative URL so /api/* requests route through next.config.ts rewrites.
// Never talk directly to the backend from the browser — that bypasses the proxy and triggers CORS.
export const API_BASE = "";

import { useAuthStore } from "@/store/authStore";

// ─── B-P1-8: typed rate-limit error ──────────────────────────────────
// Mirrors shared/api/client.ts's RateLimitError so admin login,
// staff "Sign out everywhere", and admin password-change screens can
// show "try again in N seconds" UX. The contract is pinned in
// docs/runbooks/rate-limits.md.
export class RateLimitError extends Error {
    readonly status = 429;
    retryAfterSeconds: number;
    limit: number | null;
    remaining: number | null;
    resetSeconds: number | null;
    data: any;

    constructor(opts: {
        message: string;
        retryAfterSeconds: number;
        limit: number | null;
        remaining: number | null;
        resetSeconds: number | null;
        data: any;
    }) {
        super(opts.message);
        this.name = "RateLimitError";
        this.retryAfterSeconds = opts.retryAfterSeconds;
        this.limit = opts.limit;
        this.remaining = opts.remaining;
        this.resetSeconds = opts.resetSeconds;
        this.data = opts.data;
    }
}

// RFC 9110 §10.2.3: integer seconds OR HTTP-date. Negative clamped to 0.
const parseRetryAfter = (header: string | null, fallback: number): number => {
    if (!header) return fallback;
    const trimmed = header.trim();
    if (/^\d+$/.test(trimmed)) {
        const seconds = parseInt(trimmed, 10);
        return Number.isFinite(seconds) ? Math.max(seconds, 0) : fallback;
    }
    const dateMs = Date.parse(trimmed);
    if (Number.isFinite(dateMs)) {
        return Math.max(Math.ceil((dateMs - Date.now()) / 1000), 0);
    }
    return fallback;
};

const parseIntHeader = (header: string | null): number | null => {
    if (!header) return null;
    const n = parseInt(header.trim(), 10);
    return Number.isFinite(n) ? n : null;
};

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    // Get token from Zustand store
    const store = useAuthStore.getState();
    const token = store.token;
    // Don't force JSON Content-Type when the body is FormData — the browser
    // sets multipart/form-data with the right boundary automatically.
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    const headers: Record<string, string> = {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...(options.headers as Record<string, string>),
    };
    // An explicit Authorization header from the caller wins over the store
    // token. The forced-enrollment flow passes the enrollment-scoped token
    // while a previous account's session may still sit in the Zustand store
    // (account switching on /login) — overwriting it would enroll MFA on the
    // wrong account.
    if (token && !headers["Authorization"]) headers["Authorization"] = `Bearer ${token}`;
    const method = (options.method ?? "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && store.csrfToken) {
        headers["X-CSRF-Token"] = store.csrfToken;
    }

    const url = `${API_BASE}${path}`;
    try {
        const res = await fetch(url, { ...options, headers });
        if (process.env.NODE_ENV === "development") {
            console.log(`API Request: ${options.method || 'GET'} ${path} -> ${res.status}`);
        }

        if (res.status === 401) {
            // For the login endpoint, fall through to the !res.ok handler so
            // "Invalid credentials" is shown to the user rather than "Unauthorized".
            // Same for calls that supplied their own Authorization header (the
            // forced-MFA-enrollment flow): silently refreshing would swap in the
            // store session's token — acting as the previously signed-in account
            // in the account-switch case — and the logout()+redirect below would
            // destroy the enrollment flow. Those callers must see the real 401.
            const callerProvidedAuth = Boolean(
                (options.headers as Record<string, string> | undefined)?.["Authorization"],
            );
            if (path !== "/api/admin/auth/login" && !callerProvidedAuth) {
                // Attempt one silent refresh before giving up. The HttpOnly
                // refresh cookie is sent automatically — no need to check for
                // a token in JS state.
                await store.silentRefresh();
                const refreshedStore = useAuthStore.getState();
                const newToken = refreshedStore.token;
                if (newToken) {
                    // Rebuild BOTH auth headers from the refreshed store: the
                    // refresh rotated the csrf_token cookie + value, so reusing
                    // the stale X-CSRF-Token from `headers` sends the OLD token
                    // alongside the NEW cookie and the backend double-submit
                    // check rejects the retry with 403 "CSRF token invalid".
                    // Same fix as companyApi.ts — see its matching comment.
                    const retryHeaders: Record<string, string> = {
                        ...headers,
                        Authorization: `Bearer ${newToken}`,
                    };
                    if (!["GET", "HEAD", "OPTIONS"].includes(method) && refreshedStore.csrfToken) {
                        retryHeaders["X-CSRF-Token"] = refreshedStore.csrfToken;
                    }
                    const retryRes = await fetch(url, { ...options, headers: retryHeaders });
                    if (retryRes.ok) return retryRes.json() as T;
                    if (retryRes.status !== 401) {
                        const retryBody = await retryRes.json().catch(() => ({}));
                        const retryMsg =
                            retryBody.detail ||
                            retryBody.error?.detail ||
                            retryBody.error?.message ||
                            retryBody.message ||
                            retryRes.statusText;
                        throw new Error(retryMsg);
                    }
                }
                useAuthStore.getState().logout();
                if (typeof window !== "undefined") {
                    window.location.href = "/login";
                }
                throw new Error("Unauthorized");
            }
        }

        // B-P1-8: throw typed RateLimitError before the generic !res.ok
        // path so the admin login / staff "Sign out everywhere"
        // screens can render a countdown rather than a generic
        // "Request failed". Always reads body so callers still get
        // the backend's `message`/`detail` for display.
        if (res.status === 429) {
            const body = await res.json().catch(() => ({}));
            const retryAfterSeconds = parseRetryAfter(
                res.headers.get("Retry-After"),
                typeof body?.retry_after === "number" ? body.retry_after : 60,
            );
            const limit =
                parseIntHeader(res.headers.get("RateLimit-Limit")) ??
                (typeof body?.limit === "number" ? body.limit : null);
            const remaining = parseIntHeader(res.headers.get("RateLimit-Remaining"));
            const resetSeconds = parseIntHeader(res.headers.get("RateLimit-Reset"));
            const msg =
                body?.message ||
                body?.detail ||
                body?.error?.message ||
                body?.error?.detail ||
                "Too many requests";
            throw new RateLimitError({
                message: msg,
                retryAfterSeconds,
                limit,
                remaining,
                resetSeconds,
                data: body,
            });
        }

        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            if (process.env.NODE_ENV === "development") {
                console.error(`API Error: ${path}`, body);
            }
            // Backend uses two error shapes:
            //   • FastAPI HTTPException  → { detail: "..." }
            //   • Custom error handler  → { error: { detail: "...", message: "..." } }
            const msg =
                body.detail ||
                body.error?.detail ||
                body.error?.message ||
                body.message ||
                res.statusText;
            throw new Error(msg);
        }

        return res.json();
    } catch (err) {
        if (process.env.NODE_ENV === "development") {
            console.error(`API Request Failed: ${url}`, err);
        }
        throw err;
    }
}
