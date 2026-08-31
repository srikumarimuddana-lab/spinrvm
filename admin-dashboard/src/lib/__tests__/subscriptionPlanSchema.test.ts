import { describe, it, expect } from 'vitest';
import { isPlanNameValid, isPlanPriceValid } from '../subscriptionPlanSchema';

describe('isPlanNameValid', () => {
  it('accepts a non-empty name', () => {
    expect(isPlanNameValid('Pro 30-Day Pass')).toBe(true);
  });

  it('accepts a name that only becomes empty-looking after trimming leading/trailing whitespace, if content remains', () => {
    expect(isPlanNameValid('  Weekly Pass  ')).toBe(true);
  });

  it('rejects an empty string', () => {
    expect(isPlanNameValid('')).toBe(false);
  });

  it('rejects a whitespace-only string (mirrors the original `!form.name.trim()` check)', () => {
    expect(isPlanNameValid('   ')).toBe(false);
    expect(isPlanNameValid('\n\t')).toBe(false);
  });
});

describe('isPlanPriceValid', () => {
  it('accepts a positive price', () => {
    expect(isPlanPriceValid(19.99)).toBe(true);
  });

  it('accepts zero (free plan)', () => {
    expect(isPlanPriceValid(0)).toBe(true);
  });

  it('rejects a negative price (mirrors the original `form.price < 0` check)', () => {
    expect(isPlanPriceValid(-0.01)).toBe(false);
    expect(isPlanPriceValid(-100)).toBe(false);
  });
});
