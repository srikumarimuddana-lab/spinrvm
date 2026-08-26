/**
 * Pins the request-timeout header contract on the client side.
 *
 * Background — the 2026-08-24 "Spinr DB calls rejected" alert storm. The client
 * sent only `X-Deadline-Ms`, an ABSOLUTE epoch stamped from this device's
 * `Date.now()`. The backend derived each request's time budget by subtracting
 * its OWN clock from that value, so device clock skew landed directly in the
 * budget: a handset running ~15s behind sent a deadline that was already
 * expired on arrival, and every DB call in that request was rejected pre-flight
 * with a 503 for as long as the clock stayed wrong.
 *
 * The fix is `X-Timeout-Ms`, a RELATIVE duration the backend adds to its own
 * monotonic clock — this device's wall clock never enters the calculation, so
 * skew cannot affect the budget by construction. Both headers are sent during
 * the migration window so a backend that predates X-Timeout-Ms keeps working.
 *
 * Code under test: shared/api/client.ts::deadlineHeader (via client.get)
 */

jest.mock('react-native', () => ({
  Platform: { OS: 'web' },
}));

// client.ts imports SpinrConfig from '../config/spinr.config', which pulls in
// expo-constants. Mock the module itself rather than its dependency tree.
jest.mock('../../config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'http://localhost:8000' },
}));

jest.mock('../../services/firebase', () => ({
  auth: { currentUser: null, onAuthStateChanged: null },
  isFirebaseConfigured: false,
}));

const _secureStoreBacking: Record<string, string | null> = {};
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn((k: string) => Promise.resolve(_secureStoreBacking[k] ?? null)),
  setItemAsync: jest.fn((k: string, v: string) => { _secureStoreBacking[k] = v; return Promise.resolve(); }),
  deleteItemAsync: jest.fn((k: string) => { delete _secureStoreBacking[k]; return Promise.resolve(); }),
}));

const _webStorage: Record<string, string> = {};
Object.defineProperty(global, 'sessionStorage', {
  value: {
    getItem: (k: string) => _webStorage[k] ?? null,
    setItem: (k: string, v: string) => { _webStorage[k] = v; },
    removeItem: (k: string) => { delete _webStorage[k]; },
  },
  writable: true,
});
Object.defineProperty(global, 'localStorage', {
  value: {
    getItem: (k: string) => _webStorage[k] ?? null,
    setItem: (k: string, v: string) => { _webStorage[k] = v; },
    removeItem: (k: string) => { delete _webStorage[k]; },
  },
  writable: true,
});

import client from '../../api/client';

/** Capture the headers of the next fetch() the client issues. */
function captureHeaders(): { get: (name: string) => string | undefined } {
  const captured: Record<string, string> = {};
  global.fetch = jest.fn((_url: string, options: RequestInit = {}) => {
    Object.entries((options.headers ?? {}) as Record<string, string>).forEach(([k, v]) => {
      captured[k.toLowerCase()] = v;
    });
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve('{}'),
      headers: { get: () => null },
    });
  }) as unknown as typeof fetch;
  return { get: (name: string) => captured[name.toLowerCase()] };
}

describe('shared/api/client — request timeout headers (clock-skew fix)', () => {
  it('sends X-Timeout-Ms as a relative duration, not an epoch', async () => {
    const headers = captureHeaders();
    await client.get('/api/v1/anything');

    const timeout = headers.get('X-Timeout-Ms');
    expect(timeout).toBeDefined();

    // The whole point: a duration, not a timestamp. An epoch-ms value in 2026
    // is ~1.7e12; a plausible timeout is a handful of seconds.
    const value = Number(timeout);
    expect(Number.isFinite(value)).toBe(true);
    expect(value).toBeGreaterThan(0);
    expect(value).toBeLessThan(120_000);
  });

  it('still sends the legacy X-Deadline-Ms so an older backend keeps working', async () => {
    const headers = captureHeaders();
    await client.get('/api/v1/anything');

    const deadline = Number(headers.get('X-Deadline-Ms'));
    expect(Number.isFinite(deadline)).toBe(true);
    // Legacy spelling is an absolute epoch in the near future.
    expect(deadline).toBeGreaterThan(Date.now() - 1000);
  });

  it('keeps the two headers consistent: deadline == now + timeout', async () => {
    const headers = captureHeaders();
    const before = Date.now();
    await client.get('/api/v1/anything');
    const after = Date.now();

    const timeout = Number(headers.get('X-Timeout-Ms'));
    const deadline = Number(headers.get('X-Deadline-Ms'));

    expect(deadline).toBeGreaterThanOrEqual(before + timeout);
    expect(deadline).toBeLessThanOrEqual(after + timeout);
  });

  it('X-Timeout-Ms is unaffected by this device\'s wall clock', async () => {
    // Simulate the broken handset: a clock 30s behind real time. The legacy
    // absolute header moves with it (that was the bug); the relative one must
    // not budge, because that is what makes it skew-immune.
    const realNow = Date.now;
    try {
      const headersSkewed = captureHeaders();
      Date.now = () => realNow() - 30_000;
      await client.get('/api/v1/anything');
      const skewedTimeout = headersSkewed.get('X-Timeout-Ms');
      const skewedDeadline = Number(headersSkewed.get('X-Deadline-Ms'));

      Date.now = realNow;
      const headersNormal = captureHeaders();
      await client.get('/api/v1/anything');
      const normalTimeout = headersNormal.get('X-Timeout-Ms');
      const normalDeadline = Number(headersNormal.get('X-Deadline-Ms'));

      expect(skewedTimeout).toBe(normalTimeout);
      // And confirm the legacy header really does carry the skew, which is
      // why the backend has to clamp it rather than trust it.
      expect(normalDeadline - skewedDeadline).toBeGreaterThan(25_000);
    } finally {
      Date.now = realNow;
    }
  });

  it('sends both headers on POST as well as GET', async () => {
    const headers = captureHeaders();
    await client.post('/api/v1/anything', { some: 'body' });

    expect(headers.get('X-Timeout-Ms')).toBeDefined();
    expect(headers.get('X-Deadline-Ms')).toBeDefined();
  });
});
