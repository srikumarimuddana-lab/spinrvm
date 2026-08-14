/**
 * Regression: handleApiError's 503 retry must be bounded to ONE attempt.
 *
 * The retry goes back through client.post(), which passes a fresh retryFn
 * of its own, so before the _inflight503Retries guard a *persistent* 503
 * (e.g. Twilio unconfigured → /auth/send-otp 503s every time) looped
 * fetch → 1.5s → fetch forever. The caller's login promise never settled
 * and users saw a misleading "unable to reach server" once a fetch in the
 * loop eventually failed at the network layer.
 *
 * Code under test: shared/api/client.ts::handleApiError (503 branch)
 */

import api from '@shared/api/client';

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'http://localhost:8000' },
}));

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(() => Promise.resolve(null)),
  setItemAsync: jest.fn(() => Promise.resolve()),
  deleteItemAsync: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/store/authStore', () => ({
  useAuthStore: {
    getState: jest.fn(() => ({ token: null, logout: jest.fn() })),
  },
}));

const make503Response = () => ({
  ok: false,
  status: 503,
  json: async () => ({
    detail: 'Verification is temporarily unavailable, please try again later',
  }),
  headers: { get: () => null },
});

describe('503 retry is bounded', () => {
  beforeEach(() => {
    (global as any).fetch = jest.fn(async () => make503Response());
  });

  it('fetches exactly twice on persistent 503 and surfaces the server detail', async () => {
    const started = Date.now();
    await expect(api.post('/auth/send-otp', { phone: '+13065550100' })).rejects.toMatchObject({
      response: {
        status: 503,
        data: {
          detail: 'Verification is temporarily unavailable, please try again later',
        },
      },
    });
    // Original attempt + exactly one retry — not an unbounded loop.
    expect((global as any).fetch).toHaveBeenCalledTimes(2);
    // Sanity: the promise settled shortly after the single 1.5s backoff,
    // rather than hanging until some fetch failed at the network layer.
    expect(Date.now() - started).toBeLessThan(10_000);
  }, 15_000);
});
