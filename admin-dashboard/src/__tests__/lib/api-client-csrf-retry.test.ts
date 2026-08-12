/**
 * The 401 silent-refresh retry must re-read the CSRF token from the store.
 *
 * Regression: `request()` rebuilt only the Authorization header for the retry
 * and spread the original `headers`, so the stale X-CSRF-Token went out
 * alongside the freshly-rotated csrf_token cookie the browser now holds. The
 * backend double-submit check compares the two and rejected the retry:
 *
 *   PUT /api/admin/drivers/<id> -> 403 Forbidden
 *   WARNING core.middleware: CSRF token mismatch
 *
 * Symptom for the admin: an edit silently fails whenever the access token
 * happened to expire on that request. companyApi.ts already carried this fix;
 * the admin client did not.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { request } from "@/lib/api/client";
import { useAuthStore } from "@/store/authStore";

const STALE_CSRF = "csrf-before-refresh";
const FRESH_CSRF = "csrf-after-refresh";

function jsonRes(status: number, body: unknown = {}) {
    return {
        ok: status >= 200 && status < 300,
        status,
        statusText: `status ${status}`,
        headers: { get: () => null },
        json: () => Promise.resolve(body),
    };
}

describe("api client 401 retry", () => {
    beforeEach(() => {
        useAuthStore.setState({
            user: null,
            token: "expired-access-token",
            csrfToken: STALE_CSRF,
            isAuthenticated: true,
            isLoading: false,
            // silentRefresh is what rotates both values server-side; stub it to
            // mirror that rotation without going near the network.
            silentRefresh: async () => {
                useAuthStore.setState({ token: "fresh-access-token", csrfToken: FRESH_CSRF });
            },
        } as Partial<ReturnType<typeof useAuthStore.getState>> as never);
        vi.stubGlobal("fetch", vi.fn());
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it("sends the refreshed CSRF token on a retried write", async () => {
        const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
        fetchMock
            .mockResolvedValueOnce(jsonRes(401))
            .mockResolvedValueOnce(jsonRes(200, { ok: true }));

        await request("/api/admin/drivers/373bc278", {
            method: "PUT",
            body: JSON.stringify({ name: "New Name" }),
        });

        expect(fetchMock).toHaveBeenCalledTimes(2);
        const firstHeaders = fetchMock.mock.calls[0][1].headers as Record<string, string>;
        const retryHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>;

        expect(firstHeaders["X-CSRF-Token"]).toBe(STALE_CSRF);
        // The actual regression: this was STALE_CSRF, so the backend 403'd.
        expect(retryHeaders["X-CSRF-Token"]).toBe(FRESH_CSRF);
        expect(retryHeaders["Authorization"]).toBe("Bearer fresh-access-token");
    });

    it("does not attach a CSRF header to a retried GET", async () => {
        const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
        fetchMock
            .mockResolvedValueOnce(jsonRes(401))
            .mockResolvedValueOnce(jsonRes(200, { ok: true }));

        await request("/api/admin/drivers");

        const retryHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>;
        expect(retryHeaders["X-CSRF-Token"]).toBeUndefined();
        expect(retryHeaders["Authorization"]).toBe("Bearer fresh-access-token");
    });

    it("keeps caller-supplied headers on the retry", async () => {
        const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
        fetchMock
            .mockResolvedValueOnce(jsonRes(401))
            .mockResolvedValueOnce(jsonRes(200, { ok: true }));

        await request("/api/admin/drivers/373bc278", {
            method: "PUT",
            headers: { "X-Trace-Id": "abc123" },
            body: "{}",
        });

        const retryHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>;
        expect(retryHeaders["X-Trace-Id"]).toBe("abc123");
        expect(retryHeaders["X-CSRF-Token"]).toBe(FRESH_CSRF);
    });
});
