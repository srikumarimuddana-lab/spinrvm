/**
 * The 401 give-up path must let the server clear the refresh cookie BEFORE
 * forcing the full page load to /login.
 *
 * 2026-09-12, admin-001: logout() was fire-and-forget and window.location
 * was set in the same tick. The /login page's bootstrap (initAuth →
 * silentRefresh) then ran with the HttpOnly refresh cookie still present and
 * replayed the token the logout had revoked 1.3 s earlier. The backend read
 * "revoked without rotation + replayed" as theft and cascade-revoked every
 * admin session — the founder was logged out of the live map mid-review, and
 * each session that cascade killed replayed its own dead token an hour later
 * and killed the next one (Sentry CRIMSON-SMOKE-7445-82 / -81 / -9).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { request } from "@/lib/api/client";
import { useAuthStore } from "@/store/authStore";

function jsonRes(status: number, body: unknown = {}) {
    return {
        ok: status >= 200 && status < 300,
        status,
        statusText: `status ${status}`,
        headers: { get: () => null },
        json: () => Promise.resolve(body),
    };
}

describe("api client 401 give-up path", () => {
    const originalLocation = window.location;
    // The store is a module singleton: a stubbed logout from one test would
    // otherwise leak into the next.
    const realLogout = useAuthStore.getState().logout;
    let location: { href: string };

    beforeEach(() => {
        location = { href: "/dashboard/monitoring" };
        Object.defineProperty(window, "location", { value: location, writable: true, configurable: true });
        vi.stubGlobal("fetch", vi.fn());
    });

    afterEach(() => {
        useAuthStore.setState({ logout: realLogout } as Partial<ReturnType<typeof useAuthStore.getState>> as never);
        Object.defineProperty(window, "location", { value: originalLocation, writable: true, configurable: true });
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it("awaits the server-side logout before navigating to /login", async () => {
        let releaseLogout!: () => void;
        const logoutSettled = new Promise<void>((resolve) => {
            releaseLogout = resolve;
        });
        const logout = vi.fn(() => logoutSettled);
        useAuthStore.setState({
            token: "expired-access-token",
            csrfToken: null,
            isAuthenticated: true,
            isLoading: false,
            // Refresh cannot mint a token (the cookie is dead), so the client gives up.
            silentRefresh: async () => {
                useAuthStore.setState({ token: null });
            },
            logout,
        } as Partial<ReturnType<typeof useAuthStore.getState>> as never);
        (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(jsonRes(401));

        const pending = request("/api/admin/monitoring/snapshot");
        // Let the 401 + failed refresh play out; logout is now in flight.
        await vi.waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
        await Promise.resolve();

        expect(location.href).toBe("/dashboard/monitoring");

        releaseLogout();
        await expect(pending).rejects.toThrow("Unauthorized");
        expect(location.href).toBe("/login");
    });

    it("still navigates when the logout call itself fails", async () => {
        useAuthStore.setState({
            token: "expired-access-token",
            csrfToken: "csrf-1",
            isAuthenticated: true,
            isLoading: false,
            silentRefresh: async () => {
                useAuthStore.setState({ token: null });
            },
        } as Partial<ReturnType<typeof useAuthStore.getState>> as never);
        const fetchMock = global.fetch as ReturnType<typeof vi.fn>;
        // logout() also fires a DELETE /api/auth/set-cookie; key the mock on
        // the URL rather than on call order.
        fetchMock.mockImplementation((input: string) => {
            if (input === "/api/admin/auth/logout") return Promise.reject(new Error("network down"));
            if (input === "/api/auth/set-cookie") return Promise.resolve(jsonRes(204));
            return Promise.resolve(jsonRes(401)); // the API call
        });

        await expect(request("/api/admin/monitoring/snapshot")).rejects.toThrow("Unauthorized");

        expect(fetchMock.mock.calls.map((c) => c[0])).toContain("/api/admin/auth/logout");
        expect(useAuthStore.getState().isAuthenticated).toBe(false);
        expect(location.href).toBe("/login");
    });
});
