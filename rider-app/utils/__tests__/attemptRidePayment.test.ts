/**
 * Unit tests for attemptRidePayment (P0-5 Phase C).
 *
 * Covers the three backend response variants + two edge cases:
 *   - Success (2xx, success=true)
 *   - Already paid (2xx, already_paid=true)
 *   - 3DS requires_action → confirmPayment → re-POST → success
 *   - 3DS confirmPayment returns error → retry alert
 *   - 3DS confirmPayment returns non-succeeded status → retry alert
 *   - 3DS but stripe unavailable → stripe_unavailable alert
 *   - 402 card_declined → decline alert with change-card CTA
 *   - 502 processor error → transient warning
 *   - Unknown 4xx/5xx → generic payment error
 *   - Unexpected 2xx shape → not-ok, generic alert
 *
 * The helper is pure-data — it returns alert descriptors rather than
 * triggering UI. Callers wire the alerts to their own UI primitives.
 */
import { attemptRidePayment } from '../attemptRidePayment';

const makeApi = (postImpl: (url: string, body?: any) => any) => ({
  post: jest.fn(postImpl),
});

const makeStripe = (
  confirmImpl: (secret: string) => any = async () => ({
    paymentIntent: { status: 'Succeeded' },
  }),
) => ({
  confirmPayment: jest.fn(confirmImpl),
});

describe('attemptRidePayment', () => {
  describe('success branch', () => {
    it('returns ok when backend says success=true', async () => {
      const api = makeApi(async () => ({
        data: { success: true, charged_amount: 20.5 },
      }));
      const result = await attemptRidePayment({
        api,
        stripe: makeStripe(),
        rideId: 'r1',
        tipAmount: 2,
      });
      expect(result.ok).toBe(true);
      expect(result.charged).toBe(20.5);
      expect(result.alert).toBeUndefined();
      expect(api.post).toHaveBeenCalledWith('/rides/r1/process-payment', {
        tip_amount: 2,
      });
    });

    it('returns ok when ride is already paid (idempotent replay)', async () => {
      const api = makeApi(async () => ({
        data: { already_paid: true, charged_amount: 18.5 },
      }));
      const result = await attemptRidePayment({
        api,
        stripe: makeStripe(),
        rideId: 'r1',
        tipAmount: 0,
      });
      expect(result.ok).toBe(true);
      expect(result.charged).toBe(18.5);
    });
  });

  describe('3DS / requires_action branch', () => {
    it('runs confirmPayment and re-POSTs on Succeeded', async () => {
      const apiImpl = jest
        .fn()
        .mockResolvedValueOnce({
          data: {
            success: false,
            status: 'requires_action',
            client_secret: 'pi_x_secret_y',
            payment_intent_id: 'pi_x',
          },
        })
        .mockResolvedValueOnce({
          data: { success: true, charged_amount: 20.5 },
        });
      const api = { post: apiImpl };
      const stripe = makeStripe();

      const result = await attemptRidePayment({
        api,
        stripe,
        rideId: 'r1',
        tipAmount: 2,
      });

      expect(stripe.confirmPayment).toHaveBeenCalledWith('pi_x_secret_y');
      expect(apiImpl).toHaveBeenCalledTimes(2);
      expect(result.ok).toBe(true);
      expect(result.charged).toBe(20.5);
    });

    it('returns retry alert when stripe.confirmPayment returns an error', async () => {
      const api = makeApi(async () => ({
        data: {
          success: false,
          status: 'requires_action',
          client_secret: 'pi_x_secret_y',
        },
      }));
      const stripe = makeStripe(async () => ({
        error: { message: 'Authentication timeout' },
      }));

      const result = await attemptRidePayment({
        api,
        stripe,
        rideId: 'r1',
        tipAmount: 0,
      });

      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Authentication failed');
      expect(result.alert?.message).toContain('Authentication timeout');
      expect(result.alert?.buttons?.some((b) => b.kind === 'change_card')).toBe(true);
      // Must not have re-POSTed
      expect(api.post).toHaveBeenCalledTimes(1);
    });

    it('returns retry alert if paymentIntent status is not succeeded', async () => {
      const api = makeApi(async () => ({
        data: {
          success: false,
          status: 'requires_action',
          client_secret: 'pi_x_secret_y',
        },
      }));
      const stripe = makeStripe(async () => ({
        paymentIntent: { status: 'requires_payment_method' },
      }));

      const result = await attemptRidePayment({
        api,
        stripe,
        rideId: 'r1',
        tipAmount: 0,
      });

      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Authentication failed');
    });

    it('returns unavailable alert when stripe is not provided', async () => {
      const api = makeApi(async () => ({
        data: {
          success: false,
          status: 'requires_action',
          client_secret: 'pi_x_secret_y',
        },
      }));

      const result = await attemptRidePayment({
        api,
        stripe: null,
        rideId: 'r1',
        tipAmount: 0,
      });

      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Payment unavailable');
    });
  });

  describe('402 card declined', () => {
    it('returns decline alert with change-card CTA (detail-wrapped body)', async () => {
      const err: any = new Error('declined');
      err.response = {
        status: 402,
        data: {
          detail: {
            code: 'card_declined',
            decline_code: 'insufficient_funds',
            message: 'Your card has insufficient funds.',
            suggested_action: 'change_card',
          },
        },
      };
      const api = { post: jest.fn().mockRejectedValue(err) };

      const result = await attemptRidePayment({
        api,
        stripe: makeStripe(),
        rideId: 'r1',
        tipAmount: 0,
      });

      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Card declined');
      expect(result.alert?.message).toContain('insufficient funds');
      const buttonKinds = (result.alert?.buttons || []).map((b) => b.kind);
      expect(buttonKinds).toContain('change_card');
      expect(buttonKinds).toContain('cancel');
    });

    it('also handles flat (non-detail-wrapped) 402 bodies', async () => {
      const err: any = new Error('declined');
      err.response = {
        status: 402,
        data: {
          code: 'card_declined',
          message: 'Card declined.',
        },
      };
      const api = { post: jest.fn().mockRejectedValue(err) };

      const result = await attemptRidePayment({
        api,
        stripe: makeStripe(),
        rideId: 'r1',
        tipAmount: 0,
      });

      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Card declined');
    });
  });

  describe('502 processor error', () => {
    it('returns transient-warning alert without change-card CTA', async () => {
      const err: any = new Error('upstream error');
      err.response = {
        status: 502,
        data: {
          detail: { code: 'payment_processor_error', message: 'Stripe rate limit' },
        },
      };
      const api = { post: jest.fn().mockRejectedValue(err) };

      const result = await attemptRidePayment({
        api,
        stripe: makeStripe(),
        rideId: 'r1',
        tipAmount: 0,
      });

      expect(result.ok).toBe(false);
      expect(result.alert?.variant).toBe('warning');
      expect(result.alert?.title).toMatch(/can't process payment/i);
      // No change-card CTA — this is an ops issue, not a card issue
      expect(result.alert?.buttons).toBeFalsy();
    });
  });

  describe('unknown errors', () => {
    it('returns generic alert on 500', async () => {
      const err: any = new Error('boom');
      err.response = { status: 500, data: {} };
      const api = { post: jest.fn().mockRejectedValue(err) };

      const result = await attemptRidePayment({
        api,
        stripe: makeStripe(),
        rideId: 'r1',
        tipAmount: 0,
      });

      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Payment error');
    });

    it('returns generic alert on network error (no response)', async () => {
      const api = { post: jest.fn().mockRejectedValue(new Error('Network down')) };

      const result = await attemptRidePayment({
        api,
        stripe: makeStripe(),
        rideId: 'r1',
        tipAmount: 0,
      });

      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Payment error');
    });

    it('returns not-ok + generic alert on unexpected 2xx shape', async () => {
      const api = makeApi(async () => ({
        data: { foo: 'bar' }, // no success, no already_paid, no status
      }));
      const result = await attemptRidePayment({
        api,
        stripe: makeStripe(),
        rideId: 'r1',
        tipAmount: 0,
      });
      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Payment error');
    });

    it('retries once on 409 requires-completed-state and succeeds', async () => {
      jest.useFakeTimers();
      let calls = 0;
      const api = {
        post: jest.fn().mockImplementation(async () => {
          calls += 1;
          if (calls === 1) {
            const err: any = new Error('409');
            err.response = {
              status: 409,
              data: { detail: "Ride is in status 'in_progress'; payment requires completed state." },
            };
            throw err;
          }
          return { data: { success: true, charged_amount: 12.5 } };
        }),
      };

      const promise = attemptRidePayment({ api, stripe: makeStripe(), rideId: 'r1', tipAmount: 0 });
      await jest.runAllTimersAsync();
      const result = await promise;

      expect(result.ok).toBe(true);
      expect(result.charged).toBe(12.5);
      expect(api.post).toHaveBeenCalledTimes(2);
      jest.useRealTimers();
    });

    it('does not retry on 409 with a different message', async () => {
      const err: any = new Error('409');
      err.response = { status: 409, data: { detail: 'ride_taken' } };
      const api = { post: jest.fn().mockRejectedValue(err) };

      const result = await attemptRidePayment({ api, stripe: makeStripe(), rideId: 'r1', tipAmount: 0 });

      expect(result.ok).toBe(false);
      expect(result.alert?.title).toBe('Payment error');
      expect(api.post).toHaveBeenCalledTimes(1);
    });
  });
});
