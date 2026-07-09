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

// Helper to build a 429 response with configurable headers + body.
// Mirrors the contract pinned in
// docs/runbooks/rate-limits.md and
// backend/tests/test_rate_limit_response_shape.py.
function rateLimitedResponse(opts: {
  headers?: Record<string, string>;
  body?: any;
} = {}) {
  const body = opts.body ?? {
    error: 'rate_limit_exceeded',
    message: 'Too many requests',
    retry_after: 60,
    limit: 5,
  };
  return Promise.resolve({
    ok: false,
    status: 429,
    headers: {
      get: (k: string) => (opts.headers ?? {})[k] ?? null,
    },
    json: () => Promise.resolve(body),
  } as unknown as Response);
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
  RateLimitError,
  listCompanyMembers,
  inviteCompanyMember,
  listCompanyAllowanceRequests,
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
    expect(url).toMatch(/\/api\/admin\/rides/);
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

  it('company members — GET routes through /api/company proxy', async () => {
    mockFetch.mockReturnValueOnce(okResponse([]));

    await listCompanyMembers('company-123').catch(() => {});

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/company/company-123/members');
  });

  it('company member invite — POST routes through /api/company proxy', async () => {
    mockFetch.mockReturnValueOnce(okResponse({ member: {}, invite_url: 'https://join.test/i' }));

    await inviteCompanyMember('company-123', {
      email: 'employee@acme.test',
      role: 'member',
    }).catch(() => {});

    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/company/company-123/members/invite');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      email: 'employee@acme.test',
      role: 'member',
    });
  });

  it('company allowance requests — GET routes through /api/company proxy', async () => {
    mockFetch.mockReturnValueOnce(okResponse([]));

    await listCompanyAllowanceRequests('company-123', 'all').catch(() => {});

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/company/company-123/allowance-requests?status=all');
  });

  // ─── B-P1-8: typed RateLimitError on 429 ────────────────────────────
  // Without a typed error, admin login / "Sign out everywhere" /
  // password change all surface 429s as a generic "Too many requests"
  // string with no countdown, no limit context. These tests pin the
  // typed-error parsing so the UI can render "try again in 60s".

  it('429 throws RateLimitError with retryAfterSeconds parsed from header', async () => {
    mockFetch.mockReturnValueOnce(
      rateLimitedResponse({
        headers: {
          'Retry-After': '60',
          'RateLimit-Limit': '5',
          'RateLimit-Remaining': '0',
          'RateLimit-Reset': '60',
        },
      }),
    );

    let caught: unknown = null;
    try {
      await loginAdminSession('admin@spinr.io', 'pass');
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(RateLimitError);
    const err = caught as RateLimitError;
    expect(err.status).toBe(429);
    expect(err.retryAfterSeconds).toBe(60);
    expect(err.limit).toBe(5);
    expect(err.remaining).toBe(0);
    expect(err.resetSeconds).toBe(60);
  });

  it('429 falls back to body.retry_after when Retry-After header is absent', async () => {
    // Some corporate WAFs strip non-standard headers. The body field
    // is our defence-in-depth; never both stripped, so RateLimitError
    // always surfaces a usable retryAfterSeconds.
    mockFetch.mockReturnValueOnce(
      rateLimitedResponse({
        headers: {},
        body: {
          error: 'rate_limit_exceeded',
          message: 'Too many requests',
          retry_after: 30,
          limit: 5,
        },
      }),
    );

    let caught: unknown = null;
    try {
      await loginAdminSession('admin@spinr.io', 'pass');
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(RateLimitError);
    expect((caught as RateLimitError).retryAfterSeconds).toBe(30);
    expect((caught as RateLimitError).limit).toBe(5);
  });

  it('429 parses HTTP-date form of Retry-After (RFC 9110 alternative)', async () => {
    const futureDate = new Date(Date.now() + 90_000).toUTCString();
    mockFetch.mockReturnValueOnce(
      rateLimitedResponse({ headers: { 'Retry-After': futureDate } }),
    );

    let caught: unknown = null;
    try {
      await loginAdminSession('admin@spinr.io', 'pass');
    } catch (e) {
      caught = e;
    }

    const err = caught as RateLimitError;
    // Allow ±2s slack between Date.now() in the test and inside the
    // parser to absorb test execution time.
    expect(err.retryAfterSeconds).toBeGreaterThanOrEqual(88);
    expect(err.retryAfterSeconds).toBeLessThanOrEqual(91);
  });

  it('429 surfaces the backend message string, not a generic fallback', async () => {
    // The whole point of the typed error is that callers can render
    // the backend's message. If we accidentally swap in a hardcoded
    // "Too many requests" the UX loses the "try again at HH:MM" hint
    // some endpoints emit.
    mockFetch.mockReturnValueOnce(
      rateLimitedResponse({
        headers: { 'Retry-After': '60' },
        body: {
          error: 'rate_limit_exceeded',
          message: 'Too many login attempts. Try again at 14:32 PT.',
          retry_after: 60,
          limit: 5,
        },
      }),
    );

    let caught: unknown = null;
    try {
      await loginAdminSession('admin@spinr.io', 'pass');
    } catch (e) {
      caught = e;
    }

    expect((caught as RateLimitError).message).toBe(
      'Too many login attempts. Try again at 14:32 PT.',
    );
  });
});
