/**
 * PIPEDA: nothing sensitive may reach LogRocket's session replay.
 *
 * LogRocket captures request and response bodies by default and our client uses
 * fetch, so before these sanitizers existed every authenticated call was shipped
 * whole to a third party — phone numbers, OTP codes, access AND refresh tokens,
 * names, emails, addresses, raw GPS traces.
 *
 * These tests use realistic Spinr payloads rather than toy strings, so a
 * regression shows up as "the phone number is visible" rather than as an abstract
 * shape mismatch.
 *
 * Code under test: shared/services/logrocketSanitizer.ts
 */

import {
  REDACTED_BODY,
  logRocketNetworkConfig,
  sanitizeHeaders,
  sanitizeRequest,
  sanitizeResponse,
  sanitizeUrl,
} from '../logrocketSanitizer';

describe('sanitizeHeaders', () => {
  it('redacts every credential-bearing header, whatever its casing', () => {
    const out = sanitizeHeaders({
      Authorization: 'Bearer eyJhbGciOiJIUzI1NiJ9.super-secret',
      'X-CSRF-Token': 'csrf-abc',
      'x-firebase-appcheck': 'appcheck-xyz',
      COOKIE: 'refresh_token=rt-secret',
      'Set-Cookie': 'auth_token=at-secret; HttpOnly',
    });

    for (const value of Object.values(out)) {
      expect(value).toBe('[redacted]');
    }
    expect(JSON.stringify(out)).not.toContain('super-secret');
    expect(JSON.stringify(out)).not.toContain('rt-secret');
  });

  it('keeps non-credential headers so requests stay debuggable', () => {
    const out = sanitizeHeaders({
      'Content-Type': 'application/json',
      'X-Request-ID': 'req-123',
      'X-Deadline-Ms': '1750000000000',
    });

    expect(out).toEqual({
      'Content-Type': 'application/json',
      'X-Request-ID': 'req-123',
      'X-Deadline-Ms': '1750000000000',
    });
  });

  it('tolerates missing headers', () => {
    expect(sanitizeHeaders(undefined)).toEqual({});
  });
});

describe('sanitizeUrl', () => {
  it('leaves a URL with no query string untouched', () => {
    expect(sanitizeUrl('https://api-spinr.spinr.ca/api/v1/auth/me')).toBe(
      'https://api-spinr.spinr.ca/api/v1/auth/me',
    );
  });

  it('masks coordinates while keeping the path and parameter names', () => {
    const out = sanitizeUrl('/drivers/nearby?lat=52.1332&lng=-106.6700&radius=5');
    expect(out).toBe('/drivers/nearby?lat=[redacted]&lng=[redacted]&radius=5');
    expect(out).not.toContain('52.1332');
    expect(out).not.toContain('106.6700');
  });

  it('masks a user-typed address in a places-autocomplete query', () => {
    // The riskiest query string in the app: whatever the user typed into search.
    const out = sanitizeUrl('/places/autocomplete?input=221B%20Baker%20Street&sessiontoken=abc');
    expect(out).not.toContain('Baker');
    expect(out).toContain('input=[redacted]');
    expect(out).toContain('sessiontoken=abc');
  });

  it('preserves a fragment instead of folding it into the last value', () => {
    expect(sanitizeUrl('/x?q=secret&keep=1#section')).toBe('/x?q=[redacted]&keep=1#section');
  });

  it('does not throw on malformed query strings', () => {
    expect(() => sanitizeUrl('/x?')).not.toThrow();
    expect(() => sanitizeUrl('/x?&&')).not.toThrow();
    expect(() => sanitizeUrl('/x?flag')).not.toThrow();
    expect(sanitizeUrl('/x?flag')).toBe('/x?flag');
  });

  it('passes through undefined', () => {
    expect(sanitizeUrl(undefined)).toBeUndefined();
  });
});

describe('sanitizeRequest', () => {
  it('redacts the OTP-verify body — phone number and code', () => {
    const out = sanitizeRequest({
      method: 'POST',
      url: '/api/v1/auth/verify-otp',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: '+13065551234', otp: '481920' }),
    });

    expect(out.body).toBe(REDACTED_BODY);
    expect(out.body).not.toContain('3065551234');
    expect(out.body).not.toContain('481920');
    // The envelope survives, which is the point of sanitising rather than dropping.
    expect(out.method).toBe('POST');
    expect(out.url).toBe('/api/v1/auth/verify-otp');
  });

  it('redacts a raw GPS location batch', () => {
    const out = sanitizeRequest({
      method: 'POST',
      url: '/api/v1/drivers/location-batch',
      headers: { Authorization: 'Bearer tok' },
      body: JSON.stringify({
        ride_id: 'ride-1',
        points: [{ lat: 52.1332, lng: -106.67, captured_at: '2026-07-29T12:00:00Z' }],
      }),
    });

    expect(out.body).toBe(REDACTED_BODY);
    expect(out.headers.Authorization).toBe('[redacted]');
    expect(JSON.stringify(out)).not.toContain('52.1332');
  });

  it('redacts bodies that look innocuous — default-deny, not a denylist', () => {
    // A denylist of "sensitive endpoints" fails open the moment an endpoint is
    // added. This asserts the policy is default-deny so that cannot happen.
    const out = sanitizeRequest({
      method: 'POST',
      url: '/api/v1/some/brand/new/endpoint',
      headers: {},
      body: JSON.stringify({ anything: 'at all' }),
    });

    expect(out.body).toBe(REDACTED_BODY);
  });

  it('leaves an absent body absent rather than inventing one', () => {
    const out = sanitizeRequest({ method: 'GET', url: '/api/v1/auth/me', headers: {} });
    expect(out.body).toBeUndefined();
  });

  it('never returns null — that would drop the whole record', () => {
    expect(sanitizeRequest({ method: 'GET', url: '/x', headers: {} })).not.toBeNull();
  });
});

describe('sanitizeResponse', () => {
  it('redacts the token pair and profile returned by verify-otp', () => {
    const out = sanitizeResponse({
      status: 200,
      url: '/api/v1/auth/verify-otp',
      headers: { 'Set-Cookie': 'refresh_token=rt-secret; HttpOnly' },
      body: JSON.stringify({
        token: 'eyJhbGciOiJIUzI1NiJ9.access',
        refresh_token: 'rt-super-secret',
        expires_in: 900,
        user: {
          id: 'u-1',
          phone: '+13065551234',
          email: 'driver@example.com',
          first_name: 'Jane',
          last_name: 'Doe',
        },
      }),
    });

    const serialised = JSON.stringify(out);
    expect(out.body).toBe(REDACTED_BODY);
    expect(serialised).not.toContain('rt-super-secret');
    expect(serialised).not.toContain('3065551234');
    expect(serialised).not.toContain('driver@example.com');
    expect(serialised).not.toContain('Jane');
    // Status is kept — a 200 vs 401 is the useful part.
    expect(out.status).toBe(200);
  });

  it('redacts the /auth/me profile', () => {
    const out = sanitizeResponse({
      status: 200,
      url: '/api/v1/auth/me',
      headers: {},
      body: JSON.stringify({ id: 'u-1', first_name: 'Jane', email: 'j@example.com' }),
    });

    expect(out.body).toBe(REDACTED_BODY);
    expect(JSON.stringify(out)).not.toContain('j@example.com');
  });
});

describe('logRocketNetworkConfig', () => {
  it('exposes both sanitizers so the two apps cannot drift apart', () => {
    expect(logRocketNetworkConfig.requestSanitizer).toBe(sanitizeRequest);
    expect(logRocketNetworkConfig.responseSanitizer).toBe(sanitizeResponse);
  });
});
