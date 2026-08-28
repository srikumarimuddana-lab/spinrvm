/**
 * Shared mock fixtures for driver-app E2E tests.
 *
 * The driver-app is a React Native Web app exported via `expo export`. It
 * makes HTTP calls to `${EXPO_PUBLIC_BACKEND_URL}/api/v1/*` and opens a
 * WebSocket to `/ws/driver/{driverId}` for ride offers and rider chat.
 *
 * These fixtures intercept every backend call with `page.route()` and stub
 * the WebSocket + Google Maps + Firebase so specs run fully offline.
 */
import type { Page, Route } from '@playwright/test';

export const MOCK_DRIVER_USER = {
  id: 'driver_e2e',
  phone: '+13065550101',
  first_name: 'Test',
  last_name: 'Driver',
  email: 'driver@spinr.ca',
  role: 'driver',
  is_driver: true,
  profile_complete: true,
};

export const MOCK_DRIVER = {
  id: 'driver_e2e',
  user_id: 'driver_e2e',
  status: 'verified',
  onboarding_status: 'verified',
  is_online: false,
  vehicle: { make: 'Honda', model: 'Civic', year: 2022, plate: 'ABC123' },
  rating: 4.9,
  total_trips: 127,
};

export const MOCK_TOKEN = 'e2e-driver-jwt-token';

export const MOCK_RIDE_OFFER = {
  id: 'ride_offer_1',
  status: 'driver_assigned',
  rider_id: 'rider_e2e',
  pickup_address: '123 Main St, Saskatoon, SK',
  pickup_lat: 52.13,
  pickup_lng: -106.67,
  dropoff_address: '456 Broadway Ave, Saskatoon, SK',
  dropoff_lat: 52.12,
  dropoff_lng: -106.65,
  total_fare: '18.50',
  distance_km: 3.2,
  duration_minutes: 8,
  surge_multiplier: 1.0,
  payment_method: 'card',
  pickup_otp: '4242',
  created_at: '2026-04-20T10:00:00Z',
};

export const MOCK_EARNINGS = {
  today: { amount: 125.5, trips: 7, hours: 4.2 },
  week: { amount: 842.25, trips: 48, hours: 28.5 },
  month: { amount: 3521.75, trips: 192, hours: 118.3 },
};

/**
 * Inject auth + driver state and mock third-party SDKs before any app script runs.
 */
export async function seedAuthedDriverSession(
  page: Page,
  overrides: { driver?: Partial<typeof MOCK_DRIVER>; user?: Partial<typeof MOCK_DRIVER_USER> } = {}
) {
  const driver = { ...MOCK_DRIVER, ...overrides.driver };
  const user = { ...MOCK_DRIVER_USER, ...overrides.user };

  await page.addInitScript(
    ({ token, user, driver }) => {
      localStorage.setItem('auth_token', token);
      localStorage.setItem('auth_user', JSON.stringify(user));
      localStorage.setItem('auth_driver', JSON.stringify(driver));

      // Stub Google Maps
      (window as any).google = {
        maps: {
          places: {
            AutocompleteService: class {
              getPlacePredictions(_req: unknown, cb: Function) {
                cb([], 'OK');
              }
            },
          },
          Geocoder: class {
            geocode(_req: unknown, cb: Function) {
              cb([], 'OK');
            }
          },
        },
      };

      // Stub WebSocket so we can push mock events from tests via window.__pushWsEvent
      const listeners: { [key: string]: Function[] } = {};
      const sentMessages: any[] = [];
      const OriginalWS = (window as any).WebSocket;
      class MockWebSocket {
        url: string;
        readyState = 0;
        onopen: ((ev: any) => void) | null = null;
        onmessage: ((ev: any) => void) | null = null;
        onclose: ((ev: any) => void) | null = null;
        onerror: ((ev: any) => void) | null = null;
        constructor(url: string) {
          this.url = url;
          (window as any).__lastWsUrl = url;
          setTimeout(() => {
            this.readyState = 1;
            this.onopen && this.onopen({ type: 'open' });
          }, 10);
        }
        send(data: string) {
          sentMessages.push(data);
          (window as any).__wsSent = sentMessages;
        }
        close() {
          this.readyState = 3;
          this.onclose && this.onclose({ type: 'close', code: 1000 });
        }
        addEventListener(ev: string, cb: Function) {
          listeners[ev] = listeners[ev] || [];
          listeners[ev].push(cb);
        }
      }
      (window as any).__MockWebSocket = MockWebSocket;
      (window as any).__OriginalWS = OriginalWS;
      (window as any).WebSocket = MockWebSocket;

      // Helper used by tests to push a ride_offer / location / etc.
      (window as any).__pushWsEvent = (event: any) => {
        const sockets = (window as any).__mockWsInstances || [];
        sockets.forEach((s: any) => s.onmessage && s.onmessage({ data: JSON.stringify(event) }));
      };
    },
    { token: MOCK_TOKEN, user, driver }
  );
}

/**
 * Drives the real login flow (phone entry → send-otp → OTP verify) instead of
 * seeding auth into storage.
 *
 * shared/store/authStore.ts keeps web sessions memory-only by design (no
 * client-readable token is ever persisted — an XSS-prevention decision) and
 * has no zustand `persist` middleware, so `storage.getItem('refresh_token')`
 * is hardcoded to return null on web. There is no cold-start hydration path
 * to short-circuit: `seedAuthedDriverSession`'s localStorage writes are never
 * read by the app on web. Any spec that needs to reach an authenticated
 * driver screen has to actually go through this flow, same as production.
 *
 * Call mockDriverBackend() BEFORE this so its broad `/api/v1/**` route is
 * registered first — Playwright runs routes in reverse registration order,
 * so the specific send-otp/verify-otp handlers registered here take
 * priority over (and short-circuit) mockDriverBackend's generic `/auth/*`
 * fallback.
 */
export async function loginAsDriver(
  page: Page,
  overrides: { driver?: Partial<typeof MOCK_DRIVER>; user?: Partial<typeof MOCK_DRIVER_USER> } = {}
) {
  const user = { ...MOCK_DRIVER_USER, ...overrides.user };

  await page.addInitScript(() => {
    // Stub Google Maps
    (window as any).google = {
      maps: {
        places: {
          AutocompleteService: class {
            getPlacePredictions(_req: unknown, cb: Function) {
              cb([], 'OK');
            }
          },
        },
        Geocoder: class {
          geocode(_req: unknown, cb: Function) {
            cb([], 'OK');
          }
        },
      },
    };

    // Stub WebSocket so tests can push mock events via window.__pushWsEvent
    const listeners: { [key: string]: Function[] } = {};
    const sentMessages: any[] = [];
    const OriginalWS = (window as any).WebSocket;
    class MockWebSocket {
      url: string;
      readyState = 0;
      onopen: ((ev: any) => void) | null = null;
      onmessage: ((ev: any) => void) | null = null;
      onclose: ((ev: any) => void) | null = null;
      onerror: ((ev: any) => void) | null = null;
      constructor(url: string) {
        this.url = url;
        (window as any).__lastWsUrl = url;
        setTimeout(() => {
          this.readyState = 1;
          this.onopen && this.onopen({ type: 'open' });
        }, 10);
      }
      send(data: string) {
        sentMessages.push(data);
        (window as any).__wsSent = sentMessages;
      }
      close() {
        this.readyState = 3;
        this.onclose && this.onclose({ type: 'close', code: 1000 });
      }
      addEventListener(ev: string, cb: Function) {
        listeners[ev] = listeners[ev] || [];
        listeners[ev].push(cb);
      }
    }
    (window as any).__MockWebSocket = MockWebSocket;
    (window as any).__OriginalWS = OriginalWS;
    (window as any).WebSocket = MockWebSocket;

    (window as any).__pushWsEvent = (event: any) => {
      const sockets = (window as any).__mockWsInstances || [];
      sockets.forEach((s: any) => s.onmessage && s.onmessage({ data: JSON.stringify(event) }));
    };
  });

  // Specific overrides for the two auth calls the login screens actually
  // make. Registered after mockDriverBackend() so they win (Playwright
  // matches routes in reverse registration order) over its generic
  // `/auth/*` → { token, user } fallback, which doesn't match the
  // `{ success: true }` shape /auth/send-otp's caller checks for.
  await page.route('**/api/v1/auth/send-otp', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true }),
    });
  });

  await page.route('**/api/v1/auth/verify-otp', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        token: MOCK_TOKEN,
        refresh_token: 'e2e-refresh-token',
        expires_in: 900,
        user,
      }),
    });
  });

  await page.goto('/');
  await page.getByTestId('phone-input').fill('3065550100');
  await page.getByTestId('send-otp-btn').click();

  // OTP screen — the code input is a hidden, autoFocus TextInput with no
  // testID (it's covered by decorative visible "boxes"), so type via the
  // keyboard once the screen is confirmed mounted rather than locating it.
  await page.getByText('Verify Your Number').waitFor({ timeout: 10_000 });
  await page.keyboard.type('1234');
  await page.getByText('Verify & Continue').click();

  // otp.tsx router.replace()s to /driver (profile_complete) once the store's
  // `user` is set from the mocked verify-otp response.
  await page.waitForURL(/\/driver/, { timeout: 10_000 });
}

export async function mockDriverBackend(
  page: Page,
  opts: {
    activeRide?: typeof MOCK_RIDE_OFFER | null;
    earnings?: typeof MOCK_EARNINGS;
    onlineStatus?: boolean;
  } = {}
) {
  const { activeRide = null, earnings = MOCK_EARNINGS, onlineStatus = false } = opts;
  let isOnline = onlineStatus;

  await page.route('**/api/v1/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    const method = route.request().method();

    const json = (status: number, body: unknown) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });

    // Auth / profile
    if (path === '/auth/me' || path === '/users/me') return json(200, MOCK_DRIVER_USER);
    if (path.startsWith('/auth/')) return json(200, { token: MOCK_TOKEN, user: MOCK_DRIVER_USER });

    // Driver profile + config
    if (path === '/drivers/me' && method === 'GET') return json(200, { ...MOCK_DRIVER, is_online: isOnline });
    if (path === '/drivers/me' && method === 'PUT') return json(200, MOCK_DRIVER);
    if (path === '/drivers/config') {
      return json(200, {
        ride_offer: { countdown_seconds: 15 },
        pickup_radius_meters: 100,
        earnings_cycle: 'weekly',
      });
    }
    if (path === '/drivers/status' && method === 'POST') {
      isOnline = !isOnline;
      return json(200, { is_online: isOnline });
    }

    // Active / history / earnings
    if (path === '/drivers/rides/active') {
      return json(200, activeRide ? { active: true, ride: activeRide } : { active: false, ride: null });
    }
    if (path === '/drivers/rides/history') return json(200, { rides: [] });
    if (path === '/drivers/earnings') return json(200, earnings);
    if (path.startsWith('/drivers/earnings/')) return json(200, earnings);
    if (path === '/drivers/balance') return json(200, { available: 125.5, pending: 42.0, currency: 'CAD' });
    if (path === '/drivers/payouts') return json(200, { payouts: [] });

    // Ride lifecycle (driver-side actions)
    if (method === 'POST' && /^\/drivers\/rides\/[\w-]+\/(accept|decline|arrive|verify-otp|start|complete|cancel|rate-rider)$/.test(path)) {
      return json(200, { ok: true, ride: activeRide });
    }

    // Location batch
    if (path === '/drivers/location-batch' && method === 'POST') return json(200, { accepted: 1 });

    // Quests / referral / subscription / tax
    if (path.startsWith('/quests')) return json(200, { quests: [] });
    if (path.startsWith('/referral')) return json(200, { code: 'TESTDRV', referrals: [] });
    if (path.startsWith('/drivers/subscription')) return json(200, { tier: 'standard', active: true });
    if (path.startsWith('/drivers/t4a')) return json(200, { year: 2025, gross: 0 });

    // Default
    return json(200, {});
  });
}
