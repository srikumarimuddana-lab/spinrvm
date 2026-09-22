import * as SecureStore from 'expo-secure-store';
import { installSessionLock, setSessionKeychainOptions } from '../../../shared/auth/sessionLock';
import { createBackgroundTokenProvider } from '../../utils/backgroundAuth';
import { getAppCheckToken } from '@shared/services/firebase';
let renewBackgroundAuthToken: () => Promise<string | null>;

const mockStorage: Record<string, string> = {};
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async (key: string) => mockStorage[key] ?? null),
  setItemAsync: jest.fn(async (key: string, value: string) => { mockStorage[key] = value; }),
  deleteItemAsync: jest.fn(async (key: string) => { delete mockStorage[key]; }),
}));
jest.mock('@shared/config/spinr.config', () => ({ __esModule: true, default: { backendUrl: 'https://example.test' } }));
jest.mock('@shared/services/firebase', () => ({
  initFirebaseServices: jest.fn(async () => {}), getAppCheckToken: jest.fn(async () => 'app-check'),
}));
jest.mock('../../utils/crashlytics', () => ({ recordNonFatal: jest.fn() }));
jest.mock('expo-crypto', () => ({
  CryptoDigestAlgorithm: { SHA256: 'SHA-256' },
  digestStringAsync: async (_algorithm: string, value: string) => require('node:crypto').createHash('sha256').update(value).digest('hex'),
}));

// The real backend's RefreshResponse (backend/routes/auth.py) sends
// access_expires_at as an absolute ISO timestamp, never expires_in -- fixtures
// must match that real contract, not the shape the code used to (wrongly)
// expect. See Sentry CRIMSON-SMOKE-7445-10F/10Y/SE.
const futureIso = (seconds: number) => new Date(Date.now() + seconds * 1000).toISOString();

beforeEach(() => {
  renewBackgroundAuthToken = createBackgroundTokenProvider();
  jest.clearAllMocks();
  for (const key of Object.keys(mockStorage)) delete mockStorage[key];
  let tail: Promise<unknown> = Promise.resolve();
  installSessionLock(work => { const result = tail.then(work, work); tail = result.catch(() => {}); return result; });
  setSessionKeychainOptions({ keychainAccessible: 42 });
  Object.assign(mockStorage, { refresh_token: 'refresh-old', fg_access_token: 'expired-access', token_expires_at: '1' });
  global.fetch = jest.fn(async () => ({ ok: true, status: 200, json: async () => ({ token: 'access-new', refresh_token: 'refresh-new', access_expires_at: futureIso(900) }) })) as any;
});

it('renews expired credentials, persists the rotated pair, and supplies App Check', async () => {
  expect(await renewBackgroundAuthToken()).toBe('access-new');
  expect(mockStorage.refresh_token).toBe('refresh-new');
  expect(mockStorage.fg_access_token).toBe('access-new');
  expect(Number(mockStorage.token_expires_at)).toBeGreaterThan(Date.now() + 800_000);
  expect(fetch).toHaveBeenCalledWith('https://example.test/api/v1/auth/refresh', expect.objectContaining({
    body: JSON.stringify({ refresh_token: 'refresh-old' }),
    headers: expect.objectContaining({ 'X-Firebase-AppCheck': 'app-check' }),
  }));
  expect(SecureStore.setItemAsync).toHaveBeenCalledWith('refresh_token', 'refresh-new', { keychainAccessible: 42 });
});

it.each([1, -1])('uses expires_in relative to device time when its clock is skewed %s days', async skewDays => {
  const realNow = Date.now();
  jest.useFakeTimers().setSystemTime(realNow + skewDays * 24 * 60 * 60 * 1000);
  try {
    (fetch as jest.Mock).mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({
      token: 'access-new', refresh_token: 'successor-refresh', expires_in: 900,
      access_expires_at: new Date(realNow + 900_000).toISOString(),
    }) });
    expect(await renewBackgroundAuthToken()).toBe('access-new');
    expect(mockStorage.refresh_token).toBe('successor-refresh');
    expect(Number(mockStorage.token_expires_at)).toBe(Date.now() + 900_000);

    mockStorage.token_expires_at = '1';
    const restarted = createBackgroundTokenProvider();
    (fetch as jest.Mock).mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({
      token: 'access-next', refresh_token: 'next-successor', expires_in: 900,
    }) });
    expect(await restarted()).toBe('access-next');
    expect((fetch as jest.Mock).mock.calls[1][1].body).toBe(JSON.stringify({ refresh_token: 'successor-refresh' }));
  } finally {
    jest.useRealTimers();
  }
});

it('uses the server Date header for legacy absolute expiry when the device clock is ahead', async () => {
  const realNow = Date.now();
  jest.useFakeTimers().setSystemTime(realNow + 24 * 60 * 60 * 1000);
  try {
    (fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: { get: (name: string) => name.toLowerCase() === 'date' ? new Date(realNow).toUTCString() : null },
      json: async () => ({ token: 'access-new', refresh_token: 'successor-refresh', access_expires_at: new Date(realNow + 900_000).toISOString() }),
    });
    expect(await renewBackgroundAuthToken()).toBe('access-new');
    expect(mockStorage.refresh_token).toBe('successor-refresh');
    expect(Number(mockStorage.token_expires_at)).toBeGreaterThan(Date.now() + 899_000);
    expect(Number(mockStorage.token_expires_at)).toBeLessThanOrEqual(Date.now() + 901_000);
  } finally {
    jest.useRealTimers();
  }
});

it('ignores an overflowing relative lifetime and falls back to a valid legacy timestamp', async () => {
  (fetch as jest.Mock).mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({
    token: 'access-new', refresh_token: 'successor-refresh', expires_in: Number.MAX_VALUE,
    access_expires_at: futureIso(900),
  }) });
  expect(await renewBackgroundAuthToken()).toBe('access-new');
  expect(mockStorage.refresh_token).toBe('successor-refresh');
  expect(Number.isFinite(Number(mockStorage.token_expires_at))).toBe(true);
});

it('concurrent callers reuse the winning rotation', async () => {
  expect(await Promise.all([renewBackgroundAuthToken(), renewBackgroundAuthToken()])).toEqual(['access-new', 'access-new']);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each(['logout', 'replacement'])('discards a delayed response after session %s', async action => {
  (fetch as jest.Mock).mockImplementationOnce(async () => {
    if (action === 'logout') mockStorage.spinr_session_ended = '1';
    else mockStorage.refresh_token = 'different-login';
    return { ok: true, json: async () => ({ token: 'obsolete', refresh_token: 'obsolete-refresh', access_expires_at: futureIso(900) }) };
  });
  expect(await renewBackgroundAuthToken()).toBeNull();
  expect(mockStorage.refresh_token).toBe(action === 'logout' ? 'refresh-old' : 'different-login');
  expect(mockStorage.fg_access_token).toBe('expired-access');
});

it('defers all network work when native exclusion is unavailable', async () => {
  installSessionLock(async () => { throw new Error('database is locked'); });
  expect(await renewBackgroundAuthToken()).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it('does not renew or upload after logout', async () => {
  mockStorage.spinr_session_ended = '1';
  expect(await renewBackgroundAuthToken()).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it('fails closed when the session marker cannot be read', async () => {
  (SecureStore.getItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain locked'));
  expect(await renewBackgroundAuthToken()).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it.each([401, 503])('keeps credentials on refresh rejection %s', async status => {
  (fetch as jest.Mock).mockResolvedValueOnce({ ok: false, status });
  expect(await renewBackgroundAuthToken()).toBeNull();
  expect(mockStorage.refresh_token).toBe('refresh-old');
  expect(mockStorage.spinr_session_ended).toBeUndefined();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(await renewBackgroundAuthToken()).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('rejects malformed success responses without corrupting credentials', async () => {
  (fetch as jest.Mock).mockResolvedValueOnce({ ok: true, json: async () => ({ token: 'access-new' }) });
  expect(await renewBackgroundAuthToken()).toBeNull();
  expect(mockStorage.refresh_token).toBe('refresh-old');
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('never returns a token whose rotated credential failed to persist', async () => {
  (SecureStore.setItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain write failed'));
  expect(await renewBackgroundAuthToken()).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('aborts a stalled request and retains the recording session credentials', async () => {
  jest.useFakeTimers();
  (fetch as jest.Mock).mockImplementationOnce((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')));
  }));
  try {
    const result = renewBackgroundAuthToken();
    await jest.advanceTimersByTimeAsync(10_100);
    expect(await result).toBeNull();
    expect(mockStorage.refresh_token).toBe('refresh-old');
  } finally { jest.useRealTimers(); }
});

it('releases the lock if App Check hangs and never sends a late refresh', async () => {
  jest.useFakeTimers();
  let finish!: (token: string) => void;
  (getAppCheckToken as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  try {
    const result = renewBackgroundAuthToken();
    await jest.advanceTimersByTimeAsync(10_100);
    expect(await result).toBeNull();
    finish('late-app-check');
    await jest.advanceTimersByTimeAsync(100);
    expect(fetch).not.toHaveBeenCalled();
  } finally { jest.useRealTimers(); }
});

it('does not replay a rejected credential after the headless runtime restarts', async () => {
  (fetch as jest.Mock).mockResolvedValueOnce({ ok: false, status: 401 });
  expect(await renewBackgroundAuthToken()).toBeNull();
  const restarted = createBackgroundTokenProvider();
  expect(await restarted()).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
  mockStorage.refresh_token = 'foreground-recovered';
  expect(await restarted()).toBe('access-new');
});
