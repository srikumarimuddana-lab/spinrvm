import { describe, it, expect } from 'vitest';
import { isSpinrPassPlanNameValid, isSpinrPassPlanPriceValid } from '../spinrPassAreaPlanSchema';

describe('isSpinrPassPlanNameValid', () => {
  it('accepts a non-empty name', () => {
    expect(isSpinrPassPlanNameValid('Monthly Pass')).toBe(true);
  });

  it('rejects an empty string (mirrors the original `!form.name` check)', () => {
    expect(isSpinrPassPlanNameValid('')).toBe(false);
  });
});

describe('isSpinrPassPlanPriceValid', () => {
  it('accepts a non-empty numeric-looking string', () => {
    expect(isSpinrPassPlanPriceValid('19.99')).toBe(true);
  });

  it('accepts the string "0" (a non-empty string passes the original truthy check)', () => {
    expect(isSpinrPassPlanPriceValid('0')).toBe(true);
  });

  it('rejects an empty string (mirrors the original `!form.price` check)', () => {
    expect(isSpinrPassPlanPriceValid('')).toBe(false);
  });

  it('accepts a non-numeric string (this field is only checked for non-emptiness, not numeric validity, matching the original behavior)', () => {
    expect(isSpinrPassPlanPriceValid('abc')).toBe(true);
  });
});
