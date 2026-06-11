/**
 * Driver home screen "Getting your location…" stuck-spinner regression.
 *
 * The dashboard renders an infinite spinner whenever location is null and
 * locationStatus is still 'pending'. The retry UI only appears on the
 * terminal 'denied' / 'unavailable' states. Earlier fixes only raced
 * getCurrentPositionAsync against a timeout; the permission, services, and
 * last-known queries were left unguarded. On devices where any of those
 * calls hangs (or throws) with permission already granted, refreshLocation
 * never left 'pending' and the driver was stranded on the spinner.
 *
 * This pins the contract: refreshLocation ALWAYS reaches a terminal status,
 * regardless of which underlying expo-location call hangs or throws. It is a
 * standalone replica of the resolution logic in
 * useDriverDashboard.ts::refreshLocation (mirrors the goOnlinePermission test
 * convention of replicating the code path without mounting the full hook).
 */

type Status = 'pending' | 'denied' | 'unavailable' | 'ok';

interface LocationApi {
  getForegroundPermissionsAsync: () => Promise<{ status: string }>;
  requestForegroundPermissionsAsync: () => Promise<{ status: string }>;
  hasServicesEnabledAsync: () => Promise<boolean>;
  getLastKnownPositionAsync: () => Promise<any>;
  getCurrentPositionAsync: () => Promise<any>;
}

const never = () => new Promise<never>(() => {});

// Faithful replica of refreshLocation's status resolution (useCache=false).
async function refreshLocation(L: LocationApi): Promise<Status> {
  let status: Status = 'pending';
  let locationRef: any = null;

  const withTimeout = <T,>(p: Promise<T>, ms: number): Promise<T> =>
    Promise.race([
      p,
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('location call timeout')), ms),
      ),
    ]);

  try {
    let perm: string;
    try {
      ({ status: perm } = await withTimeout(L.getForegroundPermissionsAsync(), 10000));
      if (perm !== 'granted') {
        const res = await withTimeout(L.requestForegroundPermissionsAsync(), 60000);
        perm = res.status;
      }
    } catch {
      return 'unavailable';
    }
    if (perm !== 'granted') return 'denied';

    const servicesOn = await withTimeout(L.hasServicesEnabledAsync(), 5000).catch(() => true);
    if (!servicesOn) return 'unavailable';

    try {
      const loc = await withTimeout(L.getCurrentPositionAsync(), 15000);
      locationRef = loc;
      return 'ok';
    } catch {
      try {
        const lastKnown = await withTimeout(L.getLastKnownPositionAsync(), 5000);
        if (lastKnown) { locationRef = lastKnown; return 'ok'; }
      } catch {}
      return locationRef ? 'ok' : 'unavailable';
    }
  } catch {
    return status === 'ok' ? 'ok' : 'unavailable';
  }
}

const base = (): LocationApi => ({
  getForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  requestForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  hasServicesEnabledAsync: jest.fn().mockResolvedValue(true),
  getLastKnownPositionAsync: jest.fn().mockResolvedValue(null),
  getCurrentPositionAsync: jest.fn().mockResolvedValue({
    coords: { latitude: 52.1, longitude: -106.6 },
  }),
});

// Run a promise that internally relies on real setTimeout timers, advancing
// fake timers until it settles.
async function settleWithTimers<T>(p: Promise<T>): Promise<T> {
  let done = false;
  const wrapped = p.finally(() => { done = true; });
  while (!done) {
    await Promise.resolve();
    jest.advanceTimersByTime(1000);
  }
  return wrapped;
}

describe('driver location fix — terminal status guarantee', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('resolves to ok on a normal fix', async () => {
    await expect(settleWithTimers(refreshLocation(base()))).resolves.toBe('ok');
  });

  it('does not hang when getForegroundPermissionsAsync never settles', async () => {
    const L = base();
    (L.getForegroundPermissionsAsync as jest.Mock).mockReturnValue(never());
    await expect(settleWithTimers(refreshLocation(L))).resolves.toBe('unavailable');
  });

  it('does not hang when getCurrentPositionAsync never settles', async () => {
    const L = base();
    (L.getCurrentPositionAsync as jest.Mock).mockReturnValue(never());
    // No last-known fix available → terminal 'unavailable', never 'pending'.
    await expect(settleWithTimers(refreshLocation(L))).resolves.toBe('unavailable');
  });

  it('does not hang when getLastKnownPositionAsync never settles in the catch path', async () => {
    const L = base();
    (L.getCurrentPositionAsync as jest.Mock).mockRejectedValue(new Error('no fix'));
    (L.getLastKnownPositionAsync as jest.Mock).mockReturnValue(never());
    await expect(settleWithTimers(refreshLocation(L))).resolves.toBe('unavailable');
  });

  it('falls back to last-known when the live fix times out', async () => {
    const L = base();
    (L.getCurrentPositionAsync as jest.Mock).mockReturnValue(never());
    (L.getLastKnownPositionAsync as jest.Mock).mockResolvedValue({
      coords: { latitude: 52.0, longitude: -106.5 },
    });
    await expect(settleWithTimers(refreshLocation(L))).resolves.toBe('ok');
  });

  it('surfaces denied when permission is not granted', async () => {
    const L = base();
    (L.getForegroundPermissionsAsync as jest.Mock).mockResolvedValue({ status: 'denied' });
    (L.requestForegroundPermissionsAsync as jest.Mock).mockResolvedValue({ status: 'denied' });
    await expect(settleWithTimers(refreshLocation(L))).resolves.toBe('denied');
  });

  it('treats a thrown permission query as unavailable, not a hang', async () => {
    const L = base();
    (L.getForegroundPermissionsAsync as jest.Mock).mockRejectedValue(new Error('boom'));
    await expect(settleWithTimers(refreshLocation(L))).resolves.toBe('unavailable');
  });
});
