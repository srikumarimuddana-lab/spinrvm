/**
 * Shared mock fixtures for rider-app E2E tests (SPR-02/2b).
 *
 * The rider-app is a React Native Web app exported via `expo export`. It makes
 * HTTP calls to the backend at `${EXPO_PUBLIC_BACKEND_URL}/api/v1/*`. To run
 * the app entirely offline in CI we intercept every `/api/v1/*` request with
 * `page.route()` and respond with canned JSON.
 *
 * Google Maps and Firebase are mocked by stubbing `window.google` and
 * `window.firebase` in `page.addInitScript`.
 */
import type { Page, Route } from '@playwright/test';

export const MOCK_USER = {
  id: 'user_e2e',
  phone: '+13065550100',
  first_name: 'Test',
  last_name: 'Rider',
  email: 'rider@spinr.ca',
  role: 'rider',
  is_driver: false,
  profile_complete: true,
};

export const MOCK_TOKEN = 'e2e-rider-jwt-token';

export const MOCK_RIDE = {
  id: 'ride_e2e_1',
  rider_id: MOCK_USER.id,
  driver_id: null as string | null,
  status: 'searching',
  total_fare: 18.5,
  grand_total: 18.5,
  pickup: { address: '123 Main St, Saskatoon, SK', lat: 52.13, lng: -106.67 },
  dropoff: { address: '456 Broadway Ave, Saskatoon, SK', lat: 52.12, lng: -106.65 },
  created_at: '2026-04-15T10:00:00Z',
};

export const MOCK_ESTIMATES = [
  { id: 'economy', type: 'economy', name: 'Economy', price: 18.5, eta_minutes: 4, seats: 4 },
  { id: 'comfort', type: 'comfort', name: 'Comfort', price: 24.0, eta_minutes: 5, seats: 4 },
  { id: 'xl', type: 'xl', name: 'XL', price: 32.0, eta_minutes: 7, seats: 6 },
];

/**
 * Possible responses for POST /rides/{id}/process-payment.
 * Mirrors the structured outcomes from backend/utils/stripe_charge.py
 * (P0-5 Phase A). Specs pick one of these and pass to `mockBackend`.
 */
export const PAYMENT_RESPONSES = {
  success: {
    status: 200,
    body: { success: true, charged_amount: 18.5, email_sent: false },
  },
  requiresAction: {
    status: 200,
    body: {
      success: false,
      status: 'requires_action',
      client_secret: 'pi_3ds_test_secret_xyz',
      payment_intent_id: 'pi_3ds_test',
    },
  },
  cardDeclined: {
    status: 402,
    body: {
      detail: {
        code: 'card_declined',
        decline_code: 'insufficient_funds',
        message: 'Your card has insufficient funds.',
        suggested_action: 'change_card',
      },
    },
  },
  processorError: {
    status: 502,
    body: {
      detail: {
        code: 'payment_processor_error',
        message: 'Stripe rate limit',
      },
    },
  },
} as const;

export type PaymentResponseKey = keyof typeof PAYMENT_RESPONSES;

/**
 * Inject auth token and mock third-party SDKs before any app script runs.
 * Must be called via `page.addInitScript(...)` before `page.goto(...)`.
 */
export async function seedAuthedSession(page: Page) {
  await page.addInitScript(
    ({ token, user }) => {
      // expo-secure-store falls back to localStorage on web
      localStorage.setItem('auth_token', token);
      localStorage.setItem('auth_user', JSON.stringify(user));
      // Stub Google Maps — avoids loading maps.googleapis.com in tests
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
    },
    { token: MOCK_TOKEN, user: MOCK_USER }
  );
}

/**
 * Mock every backend API endpoint the rider-app calls during a ride flow.
 * Individual tests can override specific routes by calling `page.route()`
 * again with the same pattern before navigation.
 */
export async function mockBackend(
  page: Page,
  opts: {
    activeRide?: typeof MOCK_RIDE | null;
    rideStatusSequence?: Array<typeof MOCK_RIDE>;
    /**
     * P0-5: per-spec override for POST /rides/{id}/process-payment.
     * The default is `success`. Specs that exercise the decline or 3DS
     * branches pass e.g. `paymentResponse: 'cardDeclined'`.
     *
     * When set to a function, the callback receives the call-index
     * (0-based) and must return a PAYMENT_RESPONSES shape. This lets
     * a 3DS spec serve requires_action on the 1st call and success on
     * the 2nd (post-confirmPayment finalize).
     */
    paymentResponse?:
      | PaymentResponseKey
      | ((callIndex: number) => { status: number; body: unknown });
  } = {}
) {
  const { activeRide = null, rideStatusSequence = [], paymentResponse = 'success' } = opts;
  let statusIndex = 0;
  let paymentCallIndex = 0;

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
    if (path === '/auth/me' || path === '/users/me') return json(200, MOCK_USER);
    if (path.startsWith('/auth/')) return json(200, { token: MOCK_TOKEN, user: MOCK_USER });

    // Active ride polling
    if (path === '/rides/active' || path === '/ride/active') {
      if (rideStatusSequence.length > 0) {
        const ride = rideStatusSequence[Math.min(statusIndex, rideStatusSequence.length - 1)];
        statusIndex += 1;
        return json(200, { active: true, ride });
      }
      return json(200, activeRide ? { active: true, ride: activeRide } : { active: false, ride: null });
    }

    // Ride estimates / nearby drivers
    if (path.startsWith('/rides/estimates') || path.startsWith('/ride/estimates')) {
      return json(200, { estimates: MOCK_ESTIMATES });
    }
    if (path.startsWith('/rides/nearby-drivers') || path.startsWith('/drivers/nearby')) {
      return json(200, { drivers: [] });
    }

    // Create ride
    if (method === 'POST' && (path === '/rides' || path === '/ride')) {
      return json(200, { ...MOCK_RIDE, status: 'searching' });
    }

    // P0-5: Ride payment endpoint. Route first (matches more specifically)
    // before the generic ride-detail regex below would swallow it.
    if (method === 'POST' && /^\/rides?\/[^/]+\/process-payment$/.test(path)) {
      const picked =
        typeof paymentResponse === 'function'
          ? paymentResponse(paymentCallIndex)
          : PAYMENT_RESPONSES[paymentResponse];
      paymentCallIndex += 1;
      return json(picked.status, picked.body);
    }

    // Rating endpoint
    if (method === 'POST' && /^\/rides?\/[^/]+\/rate$/.test(path)) {
      return json(200, { success: true });
    }

    // Ride detail — prefer the activeRide override (e.g. a completed/unpaid
    // ride set up via mockBackend's `activeRide` option) when its id matches
    // the requested ride, so screens that fetch by id (not just /rides/active)
    // see the same data. Falls back to the generic MOCK_RIDE otherwise.
    if (path.match(/^\/rides?\/ride_/)) {
      if (activeRide && path === `/rides/${activeRide.id}`) return json(200, activeRide);
      return json(200, MOCK_RIDE);
    }

    // Public settings — the rider-app fetches the Stripe publishable
    // key here at boot (app/_layout.tsx:147). An empty string is fine
    // for E2E: useStripe() still mounts but confirmPayment is stubbed.
    if (path === '/settings' || path === '/settings/') {
      return json(200, { stripe_publishable_key: '' });
    }

    // Wallet / loyalty / promos / saved addresses
    if (path === '/wallet') return json(200, { id: 'w1', balance: 50, currency: 'CAD', is_active: true });
    if (path === '/wallet/transactions') return json(200, { transactions: [], total: 0 });
    if (path === '/loyalty') return json(200, { points: 0, lifetime_points: 0, tier: 'bronze', multiplier: 1.0, redemption_rate: 100 });
    if (path.startsWith('/promos')) return json(200, { promos: [] });
    if (path.startsWith('/saved-places') || path.startsWith('/users/saved-addresses')) return json(200, { saved_addresses: [] });

    // Default: empty 200 so UI doesn't error
    return json(200, {});
  });
}
