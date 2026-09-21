/**
 * Minimum custom tip (settings.min_tip_amount, default $1.00).
 *
 * A $0.05 custom tip became a separate Stripe charge under Stripe's $0.50
 * minimum and silently failed — never charged, never paid to the driver. The
 * rider now sees why on the tip screen and can't submit until it's fixed; the
 * server enforces the same rule (backend/utils/tip_policy.py).
 */

import { customTipMinimumError, getCustomTipAmount } from '../utils/customTipSchema';

describe('customTipMinimumError', () => {
  it('allows no tip', () => {
    expect(customTipMinimumError(0, 1)).toBeNull();
    expect(customTipMinimumError(getCustomTipAmount(''), 1)).toBeNull();
  });

  it('blocks anything between $0 and the minimum, with the message', () => {
    for (const amount of [0.01, 0.05, 0.5, 0.99]) {
      expect(customTipMinimumError(amount, 1)).toBe('Minimum tip is $1.00');
    }
  });

  it('allows the minimum and above', () => {
    expect(customTipMinimumError(1, 1)).toBeNull();
    expect(customTipMinimumError(12.5, 1)).toBeNull();
  });

  it('follows the configured minimum', () => {
    expect(customTipMinimumError(1.5, 2)).toBe('Minimum tip is $2.00');
    expect(customTipMinimumError(2, 2)).toBeNull();
  });

  it('is switched off by a zero minimum', () => {
    expect(customTipMinimumError(0.05, 0)).toBeNull();
  });

  it('checks what would actually be charged, parsed from the text box', () => {
    expect(customTipMinimumError(getCustomTipAmount('0.5'), 1)).toBe('Minimum tip is $1.00');
    expect(customTipMinimumError(getCustomTipAmount('1'), 1)).toBeNull();
  });
});

describe('attemptRidePayment with a server-rejected tip', () => {
  // Only reachable when the minimum changed after the screen loaded it (the
  // screen blocks sub-minimum tips itself). The rider must see the real reason
  // and be able to fix the tip — not the generic Change Card alert.
  const rejectWith = (status: number, detail: unknown) => ({
    post: jest.fn().mockRejectedValue({ response: { status, data: { detail } } }),
  });

  it('shows the server message with Edit tip + Contact Support', async () => {
    const { attemptRidePayment } = require('../utils/attemptRidePayment');
    const result = await attemptRidePayment({
      api: rejectWith(400, 'Minimum tip is $2.00'),
      stripe: null,
      rideId: 'ride_1',
      tipAmount: 1,
    });

    expect(result.ok).toBe(false);
    expect(result.alert.message).toBe('Minimum tip is $2.00');
    expect(result.alert.buttons.map((b: { kind?: string }) => b.kind)).toEqual(['cancel', 'support']);
  });

  it('leaves other 400s on the generic alert', async () => {
    const { attemptRidePayment } = require('../utils/attemptRidePayment');
    const result = await attemptRidePayment({
      api: rejectWith(400, 'Tip amount cannot be negative'),
      stripe: null,
      rideId: 'ride_1',
      tipAmount: 1,
    });

    expect(result.alert.message).not.toBe('Tip amount cannot be negative');
  });
});
