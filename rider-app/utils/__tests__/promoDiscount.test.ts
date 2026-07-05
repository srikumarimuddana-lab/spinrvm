/**
 * Mirror-contract tests for utils/promoDiscount.ts against the server's
 * promo math (backend/routes/promotions.py):
 *   free_ride → grand total; percentage → % of ride portion capped at
 *   max_discount; flat → min(value, ride portion); min_ride_fare gates on
 *   the ride portion. If the server formula changes, change both.
 */

import { promoDiscountForEstimate, ridePortionOf, grandTotalOf } from '../promoDiscount';

// base 10 + distance 5 + time 5 = ride portion 20; fees/taxes bring it to 24.50
const estimate = {
  base_fare: '10.00',
  distance_fare: '5.00',
  time_fare: '5.00',
  grand_total: '24.50',
};

describe('promoDiscountForEstimate', () => {
  it('percentage promo discounts the ride portion, not the grand total', () => {
    const promo = { discount_type: 'percentage', discount_value: 20 };
    expect(promoDiscountForEstimate(promo, estimate)).toBe(4.0); // 20% of 20, not of 24.50
  });

  it('percentage promo is capped at max_discount', () => {
    const promo = { discount_type: 'percentage', discount_value: 50, max_discount: 5 };
    expect(promoDiscountForEstimate(promo, estimate)).toBe(5.0); // 10 → capped to 5
  });

  it('flat promo is capped at the ride portion (never discounts fees/taxes)', () => {
    const promo = { discount_type: 'flat', discount_value: 30 };
    expect(promoDiscountForEstimate(promo, estimate)).toBe(20.0);
  });

  it('flat promo below the ride portion applies in full', () => {
    const promo = { discount_type: 'flat', discount_value: 5 };
    expect(promoDiscountForEstimate(promo, estimate)).toBe(5.0);
  });

  it('free_ride covers the entire grand total', () => {
    const promo = { free_ride: true, discount_type: 'flat', discount_value: 0 };
    expect(promoDiscountForEstimate(promo, estimate)).toBe(24.5);
  });

  it('returns 0 when the ride portion is below min_ride_fare', () => {
    const promo = { discount_type: 'percentage', discount_value: 20, min_ride_fare: 25 };
    expect(promoDiscountForEstimate(promo, estimate)).toBe(0);
  });

  it('applies when the ride portion meets min_ride_fare exactly', () => {
    const promo = { discount_type: 'percentage', discount_value: 10, min_ride_fare: 20 };
    expect(promoDiscountForEstimate(promo, estimate)).toBe(2.0);
  });

  it('rounds to cents', () => {
    const est = { base_fare: '10.33', distance_fare: '0', time_fare: '0', grand_total: '12.00' };
    const promo = { discount_type: 'percentage', discount_value: 15 };
    expect(promoDiscountForEstimate(promo, est)).toBe(1.55); // 1.5495 → 1.55
  });

  it('returns 0 for missing promo or estimate', () => {
    expect(promoDiscountForEstimate(null, estimate)).toBe(0);
    expect(promoDiscountForEstimate({ discount_type: 'flat', discount_value: 5 }, null)).toBe(0);
  });

  it('tolerates string-typed fields from the API', () => {
    const promo = { discount_type: 'percentage', discount_value: '20', max_discount: '3.50' } as any;
    expect(promoDiscountForEstimate(promo, estimate)).toBe(3.5);
  });
});

describe('fare field helpers', () => {
  it('ridePortionOf sums base + distance + time', () => {
    expect(ridePortionOf(estimate)).toBe(20);
  });

  it('grandTotalOf falls back to total_fare', () => {
    expect(grandTotalOf({ total_fare: '18.25' })).toBe(18.25);
  });
});
