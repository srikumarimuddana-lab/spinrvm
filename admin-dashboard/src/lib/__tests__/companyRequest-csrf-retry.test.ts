/**
 * Regression tests for companyApi.ts::companyRequest's CSRF-invalid retry.
 *
 * Bug: the double-submit csrf_token_company cookie is shared across every
 * browser tab/window on the origin, but the value echoed as X-CSRF-Token
 * lives in this tab's in-memory Zustand state. If another tab's background
 * silentRefresh rotates the cookie, this tab's next write (e.g. inviting a
 * company member) 403s with "CSRF token invalid" even though the session is
 * perfectly valid — and companyRequest previously only self-healed on 401,
 * leaving that 403 an unrecoverable dead end short of a manual reload.
 *
 * Strategy: mock the Zustand store's getState() to return a "stale" pair on
 * the first call and a "refreshed" pair after silentRefresh() resolves —
 * mirrors what the real store does across the refresh call.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const silentRefreshMock = vi.fn();
const logoutMock = vi.fn();

// Mutable state the mock store reads from — tests flip this to simulate the
// store updating in place, exactly like the real Zustand `set()` would.
let mockState = {
  token: "stale-token",
  csrfToken: "stale-csrf",
};

vi.mock("@/store/companyAuthStore", () => ({
  useCompanyAuthStore: {
    getState: () => ({
      ...mockState,
      silentRefresh: silentRefreshMock,
      logout: logoutMock,
    }),
  },
}));

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    clone() {
      return jsonResponse(status, body);
    },
  } as unknown as Response;
}

describe("companyRequest CSRF-invalid retry", () => {
  beforeEach(() => {
    mockState = { token: "stale-token", csrfToken: "stale-csrf" };
    vi.stubGlobal("fetch", vi.fn());
    silentRefreshMock.mockReset();
    logoutMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("silently refreshes and retries once on a CSRF-invalid 403, succeeding with the new token", async () => {
    const { companyRequest } = await import("@/lib/companyApi");
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;

    fetchMock
      .mockResolvedValueOnce(jsonResponse(403, { detail: "CSRF token invalid" }))
      .mockResolvedValueOnce(jsonResponse(200, { success: true }));

    silentRefreshMock.mockImplementationOnce(async () => {
      mockState = { token: "fresh-token", csrfToken: "fresh-csrf" };
      return true;
    });

    const result = await companyRequest("/api/company/co-1/members/invite", {
      method: "POST",
      body: JSON.stringify({ email: "new@acme.test", role: "member" }),
    });

    expect(result).toEqual({ success: true });
    expect(silentRefreshMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // First attempt carried the stale token.
    const firstCallHeaders = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(firstCallHeaders["X-CSRF-Token"]).toBe("stale-csrf");
    expect(firstCallHeaders["Authorization"]).toBe("Bearer stale-token");

    // Retry must use the REFRESHED token, not the stale one that just failed.
    const retryHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>;
    expect(retryHeaders["X-CSRF-Token"]).toBe("fresh-csrf");
    expect(retryHeaders["Authorization"]).toBe("Bearer fresh-token");

    expect(logoutMock).not.toHaveBeenCalled();
  });

  it("logs out and surfaces Unauthorized when the refresh itself fails after a CSRF-invalid 403", async () => {
    const { companyRequest } = await import("@/lib/companyApi");
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;

    fetchMock.mockResolvedValueOnce(jsonResponse(403, { detail: "CSRF token invalid" }));
    silentRefreshMock.mockResolvedValueOnce(false);

    await expect(
      companyRequest("/api/company/co-1/members/invite", { method: "POST" })
    ).rejects.toThrow("Unauthorized");

    expect(logoutMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1); // no retry attempted without a refreshed session
  });

  it("does NOT retry a plain 403 that isn't a CSRF error (e.g. role-based denial)", async () => {
    const { companyRequest } = await import("@/lib/companyApi");
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;

    fetchMock.mockResolvedValueOnce(
      jsonResponse(403, { detail: "Only company owners can invite members" })
    );

    await expect(
      companyRequest("/api/company/co-1/members/invite", { method: "POST" })
    ).rejects.toThrow("Only company owners can invite members");

    expect(silentRefreshMock).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not loop if the retry itself is still CSRF-invalid — surfaces the retry's error and stops", async () => {
    const { companyRequest } = await import("@/lib/companyApi");
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>;

    fetchMock
      .mockResolvedValueOnce(jsonResponse(403, { detail: "CSRF token invalid" }))
      .mockResolvedValueOnce(jsonResponse(403, { detail: "CSRF token invalid" }));

    silentRefreshMock.mockImplementationOnce(async () => {
      mockState = { token: "fresh-token", csrfToken: "fresh-csrf" };
      return true;
    });

    await expect(
      companyRequest("/api/company/co-1/members/invite", { method: "POST" })
    ).rejects.toThrow("CSRF token invalid");

    // Exactly one retry — never re-enters the refresh-and-retry branch a
    // second time, even though the retry's own response is CSRF-invalid too.
    expect(silentRefreshMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(logoutMock).not.toHaveBeenCalled();
  });
});
