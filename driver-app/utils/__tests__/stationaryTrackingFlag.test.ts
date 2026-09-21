jest.mock('@shared/config/spinr.config', () => ({ __esModule: true, default: { backendUrl: 'https://example.test' } }));
jest.mock('@shared/services/firebase', () => ({
  initFirebaseServices: jest.fn(async () => {}), getAppCheckToken: jest.fn(async () => 'app-check'),
}));
jest.mock('../crashlytics', () => ({ recordNonFatal: jest.fn() }));
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async () => null), setItemAsync: jest.fn(async () => {}),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: 'after-first-unlock',
}));

describe('stationary tracking remote rollout', () => {
  beforeEach(() => { jest.resetModules(); jest.useFakeTimers(); });
  afterEach(() => { jest.useRealTimers(); });

  it.each([undefined, false, 'true', true])('only enables on boolean true: %s', async value => {
    global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ driver_stationary_tracking_enabled: value }) })) as any;
    const { stationaryTrackingEnabled } = require('../stationaryTrackingFlag');
    expect(await stationaryTrackingEnabled()).toBe(value === true);
  });

  it('coalesces reads, caches for a minute, then observes remote disable', async () => {
    global.fetch = jest.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ driver_stationary_tracking_enabled: true }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ driver_stationary_tracking_enabled: false }) });
    const { stationaryTrackingEnabled } = require('../stationaryTrackingFlag');
    expect(await Promise.all([stationaryTrackingEnabled(), stationaryTrackingEnabled()])).toEqual([true, true]);
    expect(await stationaryTrackingEnabled()).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(60_001);
    expect(await stationaryTrackingEnabled()).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('keeps enabled stationary tracking after a failed minute refresh', async () => {
    global.fetch = jest.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ driver_stationary_tracking_enabled: true }) })
      .mockRejectedValueOnce(new Error('network unavailable'));
    const { stationaryTrackingEnabled } = require('../stationaryTrackingFlag');
    expect(await stationaryTrackingEnabled()).toBe(true);
    jest.advanceTimersByTime(60_001);
    expect(await stationaryTrackingEnabled()).toBe(true);
    expect(require('../crashlytics').recordNonFatal).toHaveBeenCalled();
  });

  it('keeps enabled tracking on timeout, then accepts an explicit disable', async () => {
    global.fetch = jest.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ driver_stationary_tracking_enabled: true }) })
      .mockImplementationOnce(() => new Promise(() => {}))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ driver_stationary_tracking_enabled: false }) });
    const { stationaryTrackingEnabled } = require('../stationaryTrackingFlag');
    expect(await stationaryTrackingEnabled()).toBe(true);
    jest.advanceTimersByTime(60_001);
    const refresh = stationaryTrackingEnabled();
    await jest.advanceTimersByTimeAsync(3_000);
    expect(await refresh).toBe(true);
    jest.advanceTimersByTime(60_001);
    expect(await stationaryTrackingEnabled()).toBe(false);
  });

  it('bounds a stalled fetch and never accepts its late enabled response', async () => {
    let complete!: (response: unknown) => void;
    global.fetch = jest.fn(() => new Promise(resolve => { complete = resolve; })) as any;
    const { stationaryTrackingEnabled } = require('../stationaryTrackingFlag');
    const result = stationaryTrackingEnabled();
    await jest.advanceTimersByTimeAsync(3_000);
    expect(await result).toBe(false);
    complete({ ok: true, json: async () => ({ driver_stationary_tracking_enabled: true }) });
    await Promise.resolve();
    expect(await stationaryTrackingEnabled()).toBe(false);
  });

  it('does not enable from an HTTP error payload', async () => {
    global.fetch = jest.fn(async () => ({ ok: false, status: 503, json: async () => ({ driver_stationary_tracking_enabled: true }) })) as any;
    const { stationaryTrackingEnabled } = require('../stationaryTrackingFlag');
    expect(await stationaryTrackingEnabled()).toBe(false);
  });

  it('restores confirmed enablement in a cold headless runtime when offline', async () => {
    require('expo-secure-store').getItemAsync.mockResolvedValue('true');
    global.fetch = jest.fn().mockRejectedValue(new Error('offline'));
    const { stationaryTrackingEnabled } = require('../stationaryTrackingFlag');
    expect(await stationaryTrackingEnabled()).toBe(true);
  });

  it('persists an explicit disable using lock-screen-readable storage', async () => {
    const storage = require('expo-secure-store');
    storage.getItemAsync.mockResolvedValue('true');
    global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ driver_stationary_tracking_enabled: false }) })) as any;
    const { stationaryTrackingEnabled } = require('../stationaryTrackingFlag');
    expect(await stationaryTrackingEnabled()).toBe(false);
    expect(storage.setItemAsync).toHaveBeenCalledWith('spinr_stationary_tracking_enabled', 'false', {
      keychainAccessible: storage.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
    });
  });
});
