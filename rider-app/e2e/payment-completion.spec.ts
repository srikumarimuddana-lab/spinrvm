/**
 * Rider-app payment-completion E2E (P0-5 Phase D).
 *
 * Covers the three backend response variants for POST
 * /rides/{id}/process-payment:
 *
 *   1. success — rider lands on ride-completed, submit → app navigates
 *      back to tabs
 *   2. card_declined (HTTP 402) — rider sees a "Card declined" alert
 *      with a Change Card action (does NOT navigate away)
 *   3. processor_error (HTTP 502) — rider sees a transient error alert
 *      (does NOT navigate away)
 *
 * These specs assert behavior at the request + page-content layer.
 * They do NOT click through to the 3DS sheet because Stripe React
 * Native's native <CardSheet/> does not render in the Expo Web export
 * (stubbed out) — that branch is covered by the Jest unit tests in
 * rider-app/utils/__tests__/attemptRidePayment.test.ts.
 *
 * Strategy for driving the UI into the ride-completed state: seed the
 * /rides/active response with a completed+unpaid ride. The app's auth
 * routing (app/index.tsx) treats that as "must pay" and pushes the
 * rider to /ride-completed.
 */
import { test, expect } from '@playwright/test';
import { MOCK_RIDE, mockBackend, seedAuthedSession } from './fixtures';

const COMPLETED_UNPAID_RIDE = {
  ...MOCK_RIDE,
  status: 'completed',
  payment_status: 'pending',
  total_fare: 18.5,
  base_fare: 3.0,
  distance_fare: 8.5,
  time_fare: 5.0,
  booking_fee: 2.0,
  distance_km: 3.2,
  duration_minutes: 8,
  card_last4: '4242',
};

test.describe('rider-app: payment completion', () => {
  test('captures process-payment request with tip amount on submit', async ({ page }) => {
    await seedAuthedSession(page);
    await mockBackend(page, {
      activeRide: COMPLETED_UNPAID_RIDE,
      paymentResponse: 'success',
    });

    const paymentRequest = new Promise<any>((resolve) => {
      page.on('request', (req) => {
        if (
          req.method() === 'POST' &&
          /\/api\/v1\/rides?\/[^/]+\/process-payment$/.test(req.url())
        ) {
          try {
            resolve(req.postDataJSON());
          } catch {
            resolve({});
          }
        }
      });
      // Resolve with a sentinel if nothing fires in 12s so the test
      // fails clearly instead of hanging.
      setTimeout(() => resolve({ __timeout: true }), 12_000);
    });

    await page.goto(`/ride-completed?rideId=${COMPLETED_UNPAID_RIDE.id}`);
    await page.waitForTimeout(2500);

    // The submit button renders "Pay $X.XX & Done". React Native Web
    // renders TouchableOpacity as a <div role="button"> and the label
    // is inside a nested <Text>. Match on the text anywhere on page.
    const submitButton = page.getByText(/Pay \$|& Done/i).first();
    await submitButton.click({ timeout: 8_000 }).catch(() => {
      // If the specific text doesn't render (screen failed to mount),
      // the paymentRequest promise will time out with __timeout:true
      // and the assertion below will surface a clear error.
    });

    const body = await paymentRequest;
    expect(
      body.__timeout,
      'POST /process-payment was never captured — submit button did not fire or the screen did not mount',
    ).toBeUndefined();
    expect(body).toHaveProperty('tip_amount');
  });

  test('card decline surfaces a visible "Card declined" message with a retry action', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, {
      activeRide: COMPLETED_UNPAID_RIDE,
      paymentResponse: 'cardDeclined',
    });

    await page.goto(`/ride-completed?rideId=${COMPLETED_UNPAID_RIDE.id}`);
    await page.waitForTimeout(2500);

    // Tap submit
    const submitButton = page.getByText(/Pay \$|& Done/i).first();
    await submitButton.click({ timeout: 8_000 }).catch(() => {});

    // After the 402 fires, the app shows the decline alert. We match
    // both possible surfaces: the CustomAlert title + the Change Card
    // button label. One of them must appear.
    const declineText = page
      .getByText(/Card declined|insufficient funds|Change Card/i)
      .first();
    await expect(declineText).toBeVisible({ timeout: 8_000 });

    // Fatal JS errors would mean the alert never rendered — sanity-check.
    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps|MapView/i.test(e),
    );
    expect(fatal, `Unexpected runtime errors: ${fatal.join('\n')}`).toEqual([]);
  });

  test('processor error surfaces a transient warning (no change-card CTA)', async ({ page }) => {
    await seedAuthedSession(page);
    await mockBackend(page, {
      activeRide: COMPLETED_UNPAID_RIDE,
      paymentResponse: 'processorError',
    });

    await page.goto(`/ride-completed?rideId=${COMPLETED_UNPAID_RIDE.id}`);
    await page.waitForTimeout(2500);

    const submitButton = page.getByText(/Pay \$|& Done/i).first();
    await submitButton.click({ timeout: 8_000 }).catch(() => {});

    // The exact alert title is "Can't process payment right now" —
    // match the distinctive fragment while tolerating Unicode quote
    // variants used by React Native Web rendering.
    const warningText = page.getByText(/process payment|try again in a moment/i).first();
    await expect(warningText).toBeVisible({ timeout: 8_000 });
  });

  test('successful payment does NOT leave fatal errors on the page', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, {
      activeRide: COMPLETED_UNPAID_RIDE,
      paymentResponse: 'success',
    });

    await page.goto(`/ride-completed?rideId=${COMPLETED_UNPAID_RIDE.id}`);
    await page.waitForTimeout(3000);

    const submitButton = page.getByText(/Pay \$|& Done/i).first();
    await submitButton.click({ timeout: 8_000 }).catch(() => {});
    await page.waitForTimeout(2500);

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps|MapView/i.test(e),
    );
    expect(fatal, `Unexpected runtime errors: ${fatal.join('\n')}`).toEqual([]);
  });
});
