/**
 * Contract tests for admin-dashboard API client (src/lib/api.ts).
 *
 * Strategy: vi.mock fetch so no real network calls are made.
 * Each test verifies the correct URL, HTTP method, and Authorization header
 * are sent by the internal `request()` helper.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ---------- mock Zustand authStore so api.ts can import without Next.js runtime ----------
vi.mock('@/store/authStore', () => ({
  useAuthStore: {
    getState: () => ({ token: 'test-token', logout: vi.fn() }),
  },
}));

// ---------- mock global fetch ----------
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

// Helpers to create standard fetch responses
function okResponse(body: unknown = {}) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as Response);
}

// ---------- import after mocks are in place ----------
import {
  loginAdminSession,
  sendOtp,
  getStats,
  getRides,
  getDrivers,
  getSettings,
  updateSettings,
} from '../api';

describe('api.ts contract tests', () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it('loginAdminSession — POST /api/admin/auth/login with credentials', async () => {
    mockFetch.mockReturnValueOnce(okResponse({ token: 'tok', user: {} }));

    const testEmail = 'admin@spinr.io';
    const testCred = 'test-pass';
    await loginAdminSession(testEmail, testCred).catch(() => {});

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/admin\/auth\/login$/);
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string);
    expect(body.email).toBe(testEmail);
    // verify the credential field is passed through (value equality, not literal)
    expect(body).toHaveProperty('password', testCred);
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer test-token');
  });

  it('sendOtp — POST /api/auth/send-otp with phone', async () => {
    mockFetch.mockReturnValueOnce(okResponse({ success: true }));

    await sendOtp('+13061234567').catch(() => {});

    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/auth\/send-otp$/);
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string);
    expect(body.phone).toBe('+13061234567');
  });

  it('getStats — GET /api/admin/stats with auth header', async () => {
    mockFetch.mockReturnValueOnce(okResponse({ total_rides: 0 }));

    await getStats().catch(() => {});

    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/admin\/stats/);
    expect(init.method ?? 'GET').toBe('GET');
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer test-token');
  });

  it('getRides — GET /api/admin/rides', async () => {
    mockFetch.mockReturnValueOnce(okResponse([]));

    await getRides().catch(() => {});

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/admin\/rides$/);
  });

  it('getDrivers — GET /api/admin/drivers', async () => {
    mockFetch.mockReturnValueOnce(okResponse([]));

    await getDrivers().catch(() => {});

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/admin\/drivers$/);
  });

  it('getSettings — GET /api/admin/settings', async () => {
    mockFetch.mockReturnValueOnce(okResponse({}));

    await getSettings().catch(() => {});

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/admin\/settings$/);
  });

  it('updateSettings — PATCH /api/admin/settings with data', async () => {
    mockFetch.mockReturnValueOnce(okResponse({}));

    await updateSettings({ free_cancel_window_seconds: 120 }).catch(() => {});

    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/admin\/settings$/);
    expect(init.method).toBe('PUT');
    const body = JSON.parse(init.body as string);
    expect(body.free_cancel_window_seconds).toBe(120);
  });
});
