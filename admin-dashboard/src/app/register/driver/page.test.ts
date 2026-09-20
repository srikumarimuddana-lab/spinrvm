import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Regression guard for the header-merge logic behind the public driver
 * registration page's document uploads: buildUploadHeaders() is the exact
 * spread that shipped in uploadFile()'s fetch() call. This pins that the
 * Authorization header (when a token exists) and the App Check header
 * (src/lib/firebase-app-check.ts) are both attached and neither clobbers
 * the other, without needing to render the full multi-step wizard.
 */

const appCheckHeaderMock = vi.fn();

vi.mock("@/lib/firebase-app-check", () => ({
  appCheckHeader: (...a: unknown[]) => appCheckHeaderMock(...a),
}));

import { buildUploadHeaders } from "./page";

describe("buildUploadHeaders", () => {
  beforeEach(() => {
    appCheckHeaderMock.mockReset();
  });

  it("attaches both Authorization and X-Firebase-AppCheck when both are available", async () => {
    appCheckHeaderMock.mockResolvedValue({ "X-Firebase-AppCheck": "appcheck-token-abc" });

    const headers = await buildUploadHeaders("bearer-token-123");

    expect(headers).toEqual({
      Authorization: "Bearer bearer-token-123",
      "X-Firebase-AppCheck": "appcheck-token-abc",
    });
  });

  it("omits Authorization when no bearer token is present, but keeps App Check", async () => {
    appCheckHeaderMock.mockResolvedValue({ "X-Firebase-AppCheck": "appcheck-token-abc" });

    const headers = await buildUploadHeaders("");

    expect(headers).toEqual({ "X-Firebase-AppCheck": "appcheck-token-abc" });
  });

  it("keeps Authorization when App Check fails open with {}", async () => {
    appCheckHeaderMock.mockResolvedValue({});

    const headers = await buildUploadHeaders("bearer-token-123");

    expect(headers).toEqual({ Authorization: "Bearer bearer-token-123" });
  });

  it("neither header clobbers the other when both are present", async () => {
    appCheckHeaderMock.mockResolvedValue({ "X-Firebase-AppCheck": "appcheck-token-xyz" });

    const headers = await buildUploadHeaders("another-token");

    expect(headers.Authorization).toBe("Bearer another-token");
    expect(headers["X-Firebase-AppCheck"]).toBe("appcheck-token-xyz");
    expect(Object.keys(headers)).toHaveLength(2);
  });
});
