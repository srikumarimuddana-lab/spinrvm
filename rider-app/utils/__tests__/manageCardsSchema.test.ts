import {
  isCardDetailsComplete,
  isCardholderNameValid,
  isStripeReady,
  getManageCardsFormError,
} from '../manageCardsSchema';

describe('isCardDetailsComplete', () => {
  it('mirrors the boolean passed in', () => {
    expect(isCardDetailsComplete(false)).toBe(false);
    expect(isCardDetailsComplete(true)).toBe(true);
  });
});

describe('isCardholderNameValid', () => {
  it('rejects an empty/whitespace-only name', () => {
    expect(isCardholderNameValid('')).toBe(false);
    expect(isCardholderNameValid('   ')).toBe(false);
  });
  it('accepts a non-empty name', () => {
    expect(isCardholderNameValid('Jane Doe')).toBe(true);
  });
});

describe('isStripeReady', () => {
  it('rejects an undefined createPaymentMethod', () => {
    expect(isStripeReady(undefined)).toBe(false);
  });
  it('accepts a defined createPaymentMethod', () => {
    expect(isStripeReady(() => {})).toBe(true);
  });
});

describe('getManageCardsFormError', () => {
  const readyFn = () => {};

  it('returns the missing-details error first', () => {
    expect(getManageCardsFormError(false, 'Jane Doe', readyFn)).toEqual({
      title: 'Missing Details',
      message: 'Please enter complete card details',
    });
  });

  it('returns the missing-name error when details are complete but name is empty', () => {
    expect(getManageCardsFormError(true, '', readyFn)).toEqual({
      title: 'Missing Name',
      message: 'Please enter the cardholder name',
    });
  });

  it('returns the payments-unavailable error when Stripe is not ready', () => {
    expect(getManageCardsFormError(true, 'Jane Doe', undefined)).toEqual({
      title: 'Payments unavailable',
      message: 'Payment processing is still starting up. Try again in a moment.',
    });
  });

  it('returns null when every check passes', () => {
    expect(getManageCardsFormError(true, 'Jane Doe', readyFn)).toBeNull();
  });
});
