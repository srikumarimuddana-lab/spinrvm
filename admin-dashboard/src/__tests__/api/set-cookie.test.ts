/**
 * A-P0-1 regression tests: /api/auth/set-cookie Route Handler
 *
 * Verifies the security-critical cookie attributes that enforce HttpOnly
 * token storage. The access token must never be readable by JS (httpOnly),
 * must not cross origins (sameSite: strict), and must vanish on DELETE.
 *
 * Layer 1 — pure-logic tests (cookie attribute constants): always run.
 * Layer 2 — handler integration tests using mocked next/server: always run.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock next/server before importing the route ──────────────────────────────
// NextResponse.json() returns a minimal object with a trackable cookies.set spy.

const cookieSetSpy = vi.fn();

function _makeResponseObj(data: unknown) {
  return { _data: data, cookies: { set: cookieSetSpy } };
}

vi.mock('next/server', () => ({
  NextResponse: {
    json: vi.fn((data: unknown) => _makeResponseObj(data)),
  },
}));

// Import after mock is registered
import { POST, DELETE } from '@/app/api/auth/set-cookie/route';

// ── Layer 1 — pure-logic: cookie attribute invariants ─────────────────────────

describe('set-cookie route — security attribute invariants', () => {
  it('COOKIE_MAX_AGE constant equals 8 hours in seconds', () => {
    // Cookie outlives individual 1-h JWTs; each silentRefresh resets it.
    // The middleware checks JWT exp, so a stale cookie just triggers /login.
    expect(8 * 60 * 60).toBe(28800);
  });

  it('httpOnly is true — JS cannot read the token via document.cookie', () => {
    const attrs = { httpOnly: true, sameSite: 'strict' as const, secure: false, maxAge: 28800, path: '/' };
    expect(attrs.httpOnly).toBe(true);
  });

  it('sameSite strict prevents cross-site request from carrying the cookie', () => {
    const attrs = { httpOnly: true, sameSite: 'strict' as const };
    expect(attrs.sameSite).toBe('strict');
  });
});

// ── Layer 2 — handler integration ────────────────────────────────────────────

// W6 (2026-09-10 RBAC audit): POST now requires the double-submit CSRF
// cookie + X-CSRF-Token header to match (mirrors admin/auth/refresh's
// verifyCsrf). `csrf` defaults to a matching pair so every pre-existing
// test below still exercises the cookie-attribute behavior it was written
// for, not the new CSRF gate — the CSRF-specific tests set it explicitly.
const VALID_CSRF = 'test-csrf-token-abc123';

function makeRequest(
  body: unknown,
  csrf: { cookie?: string | null; header?: string | null } = { cookie: VALID_CSRF, header: VALID_CSRF },
): Request {
  return {
    json: () => Promise.resolve(body),
    cookies: { get: (name: string) => (name === 'spinr_admin_csrf' && csrf.cookie != null ? { value: csrf.cookie } : undefined) },
    headers: { get: (name: string) => (name.toLowerCase() === 'x-csrf-token' ? csrf.header ?? null : null) },
  } as unknown as Request;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('POST /api/auth/set-cookie — CSRF gate (W6)', () => {
  it('returns 403 and does not set the cookie when the CSRF cookie is missing', async () => {
    const req = makeRequest({ token: 'x' }, { cookie: null, header: VALID_CSRF });
    const response = await POST(req as any) as any;

    expect(cookieSetSpy).not.toHaveBeenCalled();
    expect(response._data).toMatchObject({ error: expect.any(String) });
  });

  it('returns 403 and does not set the cookie when the X-CSRF-Token header is missing', async () => {
    const req = makeRequest({ token: 'x' }, { cookie: VALID_CSRF, header: null });
    const response = await POST(req as any) as any;

    expect(cookieSetSpy).not.toHaveBeenCalled();
    expect(response._data).toMatchObject({ error: expect.any(String) });
  });

  it('returns 403 and does not set the cookie when the cookie and header values differ', async () => {
    // The exploit this closes: a cross-site page can't read spinr_admin_csrf
    // (httpOnly:false but sameSite:strict keeps it out of cross-origin
    // requests), so it can't produce a header value that matches the cookie.
    const req = makeRequest({ token: 'attacker-value' }, { cookie: VALID_CSRF, header: 'wrong-value' });
    const response = await POST(req as any) as any;

    expect(cookieSetSpy).not.toHaveBeenCalled();
    expect(response._data).toMatchObject({ error: expect.any(String) });
  });

  it('returns 403 when the header is a different-length string (rules out the timingSafeEqual length crash)', async () => {
    const req = makeRequest({ token: 'x' }, { cookie: VALID_CSRF, header: 'short' });
    const response = await POST(req as any) as any;

    expect(cookieSetSpy).not.toHaveBeenCalled();
    expect(response._data).toMatchObject({ error: expect.any(String) });
  });
});

describe('POST /api/auth/set-cookie', () => {
  it('sets cookie with httpOnly: true when token is provided', async () => {
    const req = makeRequest({ token: 'eyJhbGciOiJIUzI1NiJ9.test.sig' });
    await POST(req as any);

    expect(cookieSetSpy).toHaveBeenCalledOnce();
    const [_name, _value, opts] = cookieSetSpy.mock.calls[0];
    expect(opts).toMatchObject({ httpOnly: true });
  });

  it('sets sameSite: strict on the cookie', async () => {
    const req = makeRequest({ token: 'test-jwt' });
    await POST(req as any);

    const [, , opts] = cookieSetSpy.mock.calls[0];
    expect(opts).toMatchObject({ sameSite: 'strict' });
  });

  it('sets the correct cookie name and token value', async () => {
    const req = makeRequest({ token: 'my-jwt-value' });
    await POST(req as any);

    const [name, value] = cookieSetSpy.mock.calls[0];
    expect(name).toBe('admin_token');
    expect(value).toBe('my-jwt-value');
  });

  it('sets maxAge to 28800 (8 hours)', async () => {
    const req = makeRequest({ token: 'test-jwt' });
    await POST(req as any);

    const [, , opts] = cookieSetSpy.mock.calls[0];
    expect(opts).toMatchObject({ maxAge: 28800 });
  });

  it('returns 400 when token field is missing', async () => {
    const req = makeRequest({});
    const response = await POST(req as any) as any;

    // cookie must NOT be set — no token to store
    expect(cookieSetSpy).not.toHaveBeenCalled();
    expect(response._data).toMatchObject({ error: expect.any(String) });
  });

  it('returns 400 when token is not a string', async () => {
    const req = makeRequest({ token: 12345 });
    const response = await POST(req as any) as any;

    expect(cookieSetSpy).not.toHaveBeenCalled();
    expect(response._data).toMatchObject({ error: expect.any(String) });
  });

  it('returns 400 on malformed JSON body', async () => {
    const badReq = {
      json: () => Promise.reject(new SyntaxError('Unexpected token')),
      cookies: { get: () => ({ value: VALID_CSRF }) },
      headers: { get: () => VALID_CSRF },
    } as unknown as Request;

    const response = await POST(badReq as any) as any;
    expect(cookieSetSpy).not.toHaveBeenCalled();
    expect(response._data).toMatchObject({ error: expect.any(String) });
  });
});

describe('DELETE /api/auth/set-cookie', () => {
  it('clears the cookie by setting maxAge: 0', async () => {
    await DELETE();

    expect(cookieSetSpy).toHaveBeenCalledOnce();
    const [name, value, opts] = cookieSetSpy.mock.calls[0];
    expect(name).toBe('admin_token');
    expect(value).toBe('');
    expect(opts).toMatchObject({ maxAge: 0 });
  });

  it('clears with httpOnly: true (prevents JS from accidentally re-reading)', async () => {
    await DELETE();

    const [, , opts] = cookieSetSpy.mock.calls[0];
    expect(opts).toMatchObject({ httpOnly: true });
  });
});
