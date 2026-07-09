import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { sendCompanyEmailOtp, verifyCompanyEmailOtp } from "@/lib/companyApi";

const cookieSetSpy = vi.fn();

vi.mock("next/server", () => ({
  NextResponse: {
    json: vi.fn((data: unknown, init?: { status?: number }) => ({
      _data: data,
      status: init?.status ?? 200,
      cookies: { set: cookieSetSpy },
    })),
  },
}));

function okJson(body: unknown = {}) {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  };
}

function makeRequest(body: unknown) {
  return {
    json: () => Promise.resolve(body),
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "user-agent" ? "vitest-browser" : null,
    },
  };
}

describe("company email OTP client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    cookieSetSpy.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends email OTP requests to the portal auth endpoint", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(okJson());

    await sendCompanyEmailOtp("owner@acme.test");

    expect(global.fetch).toHaveBeenCalledWith(
      "/api/portal/auth/send-email-otp",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "owner@acme.test" }),
      })
    );
  });

  it("verifies email OTP through the local cookie-setting proxy", async () => {
    const session = {
      token: "access-token",
      csrf_token: "csrf-token",
      user: { id: "user-1", email: "owner@acme.test" },
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(okJson(session));

    const result = await verifyCompanyEmailOtp("owner@acme.test", "123456");

    expect(global.fetch).toHaveBeenCalledWith(
      "/api/company-auth/verify-email-otp",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "owner@acme.test", code: "123456" }),
      })
    );
    expect(result).toEqual(session);
  });
});

describe("/api/company-auth/verify-email-otp route", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("BACKEND_URL", "https://backend.spinr.test");
    vi.stubGlobal("fetch", vi.fn());
    cookieSetSpy.mockClear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("proxies verification, strips refresh_token, and sets HttpOnly cookies", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      okJson({
        token: "access-token",
        refresh_token: "refresh-token",
        refresh_expires_at: "2026-08-01T00:00:00Z",
        user: { id: "user-1", email: "owner@acme.test" },
      })
    );

    const { POST } = await import("@/app/api/company-auth/verify-email-otp/route");
    const response = (await POST(
      makeRequest({ email: "owner@acme.test", code: "123456" }) as never
    )) as { _data: Record<string, unknown> };

    expect(global.fetch).toHaveBeenCalledWith(
      "https://backend.spinr.test/api/portal/auth/verify-email-otp",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ email: "owner@acme.test", code: "123456" }),
      })
    );
    expect(response._data).toMatchObject({
      token: "access-token",
      csrf_token: expect.any(String),
    });
    expect(response._data).not.toHaveProperty("refresh_token");
    expect(cookieSetSpy).toHaveBeenCalledWith(
      "spinr_company_rt",
      "refresh-token",
      expect.objectContaining({ httpOnly: true, path: "/api/company-auth" })
    );
  });
});
