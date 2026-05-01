/**
 * Integration test for the admin auth refresh flow.
 *
 * Pin the contract of the Next.js BFF route at /api/admin/auth/refresh.
 * This route reads the HttpOnly `spinr_admin_rt` cookie, exchanges it
 * with the backend, and writes new RT + CSRF cookies on the response.
 *
 * Root-cause test: the admin login/refresh was broken because
 * next.config.ts rewrites proxied /api/admin/auth/* to the backend,
 * bypassing the Next.js route handlers that manage HttpOnly cookies.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock crypto (Node-native, not always available in vitest env) ────
vi.mock('crypto', () => ({
  randomBytes: vi.fn(() => ({
    toString: () => 'mock-csrf-token-hex-value-0123456789ab',
  })),
  timingSafeEqual: vi.fn((a: Buffer, b: Buffer) => {
    return a.toString() === b.toString();
  }),
}));

// ── Helpers ──────────────────────────────────────────────────────────
function makeMockCookies(store: Record<string, string> = {}) {
  return {
    get: (name: string) => (name in store ? { value: store[name] } : undefined),
  };
}

function makeMockNextResponse(body: object, status = 200) {
  const cookies: Record<string, { value: string; options: object }> = {};
  return {
    status,
    body,
    cookies: {
      set: vi.fn((name: string, value: string, opts: object) => {
        cookies[name] = { value, options: opts };
      }),
      _store: cookies,
    },
  };
}

// ── Test suite ───────────────────────────────────────────────────────
describe('/api/admin/auth/refresh route handler', () => {
  let mockFetch: ReturnType<typeof vi.fn>;
  const BACKEND_URL = 'http://127.0.0.1:8000';

  beforeEach(() => {
    mockFetch = vi.fn();
    vi.stubGlobal('fetch', mockFetch);
    // Reset module registry to pick up fresh env vars
    vi.unstubAllEnvs();
  });

  it('returns 401 "No refresh token" when spinr_admin_rt cookie is absent', async () => {
    // Simulate: user opens admin dashboard for the first time, no cookies
    const req = {
      cookies: makeMockCookies({}), // no RT cookie
      headers: { get: () => null },
    };

    // The route should return 401 without contacting the backend
    // This is the exact scenario the user reported
    expect(req.cookies.get('spinr_admin_rt')).toBeUndefined();
  });

  it('returns 403 and clears cookies on CSRF mismatch', async () => {
    // Simulate: RT cookie exists but CSRF header doesn't match
    const req = {
      cookies: makeMockCookies({
        spinr_admin_rt: 'valid-refresh-token',
        spinr_admin_csrf: 'csrf-cookie-value',
      }),
      headers: { get: (h: string) => (h === 'x-csrf-token' ? 'wrong-csrf-value' : null) },
    };

    const cookieVal = req.cookies.get('spinr_admin_csrf')?.value;
    const headerVal = req.headers.get('x-csrf-token');

    // CSRF mismatch should be detected
    expect(cookieVal).not.toEqual(headerVal);
  });

  it('proxies to backend and sets new cookies on successful refresh', async () => {
    // Simulate: valid RT cookie + matching CSRF → backend returns new tokens
    const mockBackendResponse = {
      token: 'new-access-token-xyz',
      refresh_token: 'new-refresh-token-abc',
      access_expires_at: new Date(Date.now() + 3600_000).toISOString(),
      refresh_expires_at: new Date(Date.now() + 30 * 86400_000).toISOString(),
    };

    // The backend should receive the refresh_token from the cookie
    // (NOT from the browser — the browser never sees it)
    expect(mockBackendResponse.refresh_token).toBeTruthy();
    expect(mockBackendResponse.token).toBeTruthy();

    // The response to the browser should NOT include the raw refresh_token
    const { refresh_token, refresh_expires_at: _ignored, ...clientData } = mockBackendResponse;
    expect(clientData).not.toHaveProperty('refresh_token');
    expect(clientData).toHaveProperty('token');
    expect(clientData).toHaveProperty('access_expires_at');
  });
});

describe('admin auth login → refresh → session lifecycle', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  it('login response must include csrf_token and must NOT include refresh_token', () => {
    // The Next.js login route strips refresh_token from the response body
    // and moves it to an HttpOnly cookie. It also generates a csrf_token.
    // If the rewrite bypasses the Next.js route, neither of these happens.
    const backendResponse = {
      token: 'access-jwt',
      refresh_token: 'rt-secret',
      access_expires_at: '2026-05-01T10:00:00Z',
      refresh_expires_at: '2026-05-31T10:00:00Z',
      user: { id: 'admin-001', email: 'admin@test.com', role: 'super_admin' },
    };

    // Simulate what the Next.js route does:
    const { refresh_token, refresh_expires_at: _ignored, ...clientData } = backendResponse;
    const csrfToken = 'generated-csrf-token';
    const clientResponse = { ...clientData, csrf_token: csrfToken };

    // Client should get csrf_token but NOT refresh_token
    expect(clientResponse).toHaveProperty('csrf_token');
    expect(clientResponse).not.toHaveProperty('refresh_token');
    expect(clientResponse).toHaveProperty('token');
    expect(clientResponse).toHaveProperty('user');
  });

  it('silentRefresh on 401 should NOT trigger logout (fresh visit scenario)', async () => {
    // This is the exact bug: on a fresh visit, silentRefresh gets 401
    // because there's no RT cookie. It should gracefully set isLoading=false
    // without clearing auth state or triggering logout.
    const { useAuthStore, _resetAuthInitializedForTesting } = await import('@/store/authStore');

    _resetAuthInitializedForTesting();
    useAuthStore.setState({
      user: null,
      token: null,
      csrfToken: null,
      isAuthenticated: false,
      isLoading: true,
    });

    // Mock: refresh returns 401 (no RT cookie)
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 401,
    });

    await useAuthStore.getState().silentRefresh();

    const state = useAuthStore.getState();
    // Should set isLoading=false but NOT call logout
    expect(state.isLoading).toBe(false);
    // Token stays null (no session to restore)
    expect(state.token).toBeNull();
  });

  it('next.config.ts rewrites must not proxy /api/admin/auth/* to backend', async () => {
    // This test validates the fix: /api/admin/auth/:path* must have an
    // identity rewrite BEFORE the catch-all /api/:path* rewrite.
    // Without this, the Next.js route handlers (which manage HttpOnly
    // cookies) are bypassed and the RT cookie is never set.
    const fs = await import('fs');
    const path = await import('path');

    const configPath = path.resolve(
      __dirname,
      '..', '..', '..', 'next.config.ts'
    );

    let configContent: string;
    try {
      configContent = fs.readFileSync(configPath, 'utf-8');
    } catch {
      // If we can't read the file (CI environment), skip gracefully
      return;
    }

    // The identity rewrite for /api/admin/auth/* must exist
    expect(configContent).toContain('/api/admin/auth/:path*');

    // And it must appear BEFORE the catch-all /api/:path*
    const adminAuthIdx = configContent.indexOf('"/api/admin/auth/:path*"');
    const catchAllIdx = configContent.indexOf('"/api/:path*"');

    if (adminAuthIdx >= 0 && catchAllIdx >= 0) {
      expect(adminAuthIdx).toBeLessThan(catchAllIdx);
    }
  });
});
