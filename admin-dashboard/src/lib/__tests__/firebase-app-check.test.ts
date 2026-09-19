import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Regression guard for the public driver-registration page's upload 401s:
 * shared/api/upload.ts (mobile) already had this exact bug (missing
 * X-Firebase-AppCheck header on a hand-rolled request) — this pins the web
 * equivalent (src/lib/firebase-app-check.ts) the same way.
 *
 * firebaseConfig is read from process.env at module load time, so each test
 * that needs a different env state resets modules and re-imports.
 */

const initializeAppMock = vi.fn(() => ({ name: "[DEFAULT]" }));
const getAppsMock = vi.fn(() => []);
const initializeAppCheckMock = vi.fn(() => ({ app: {}, provider: {} }));
const getTokenMock = vi.fn();

vi.mock("firebase/app", () => ({
  initializeApp: initializeAppMock,
  getApps: getAppsMock,
}));

vi.mock("firebase/app-check", () => ({
  initializeAppCheck: initializeAppCheckMock,
  getToken: getTokenMock,
  ReCaptchaV3Provider: vi.fn(function (this: unknown, siteKey: string) {
    return { siteKey };
  }),
}));

const ENV_KEYS = [
  "NEXT_PUBLIC_FIREBASE_API_KEY",
  "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN",
  "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
  "NEXT_PUBLIC_FIREBASE_APP_ID",
  "NEXT_PUBLIC_RECAPTCHA_SITE_KEY",
] as const;

function clearFirebaseEnv() {
  for (const key of ENV_KEYS) delete process.env[key];
}

function setFirebaseEnv() {
  process.env.NEXT_PUBLIC_FIREBASE_API_KEY = "test-api-key";
  process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN = "test.firebaseapp.com";
  process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID = "test-project";
  process.env.NEXT_PUBLIC_FIREBASE_APP_ID = "test-app-id";
  process.env.NEXT_PUBLIC_RECAPTCHA_SITE_KEY = "test-site-key";
}

describe("appCheckHeader (web)", () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    vi.resetModules();
    initializeAppMock.mockClear();
    getAppsMock.mockClear().mockReturnValue([]);
    initializeAppCheckMock.mockClear();
    getTokenMock.mockReset();
  });

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  it("fails open — returns {} when Firebase config env vars are unset", async () => {
    clearFirebaseEnv();
    const { appCheckHeader } = await import("../firebase-app-check");

    const header = await appCheckHeader();

    expect(header).toEqual({});
    expect(initializeAppCheckMock).not.toHaveBeenCalled();
  });

  it("fails open — returns {} when Firebase config is set but the reCAPTCHA site key is missing", async () => {
    setFirebaseEnv();
    delete process.env.NEXT_PUBLIC_RECAPTCHA_SITE_KEY;
    const { appCheckHeader } = await import("../firebase-app-check");

    const header = await appCheckHeader();

    expect(header).toEqual({});
    expect(initializeAppCheckMock).not.toHaveBeenCalled();
  });

  it("attaches X-Firebase-AppCheck when fully configured and a token is available", async () => {
    setFirebaseEnv();
    getTokenMock.mockResolvedValue({ token: "web-appcheck-token-xyz" });
    const { appCheckHeader } = await import("../firebase-app-check");

    const header = await appCheckHeader();

    expect(header).toEqual({ "X-Firebase-AppCheck": "web-appcheck-token-xyz" });
    expect(initializeAppCheckMock).toHaveBeenCalledTimes(1);
  });

  it("fails open — returns {} rather than throwing when getToken rejects", async () => {
    setFirebaseEnv();
    getTokenMock.mockRejectedValue(new Error("reCAPTCHA challenge failed"));
    const { appCheckHeader } = await import("../firebase-app-check");

    const header = await appCheckHeader();

    expect(header).toEqual({});
  });

  it("reuses an existing Firebase app instance instead of re-initializing", async () => {
    setFirebaseEnv();
    getAppsMock.mockReturnValue([{ name: "[DEFAULT]" }] as never);
    getTokenMock.mockResolvedValue({ token: "abc" });
    const { appCheckHeader } = await import("../firebase-app-check");

    await appCheckHeader();

    expect(initializeAppMock).not.toHaveBeenCalled();
  });
});
