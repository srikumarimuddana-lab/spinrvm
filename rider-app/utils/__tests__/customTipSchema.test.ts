import { getCustomTipAmount } from '../customTipSchema';

describe('getCustomTipAmount', () => {
  it('returns the parsed amount for a positive numeric string', () => {
    expect(getCustomTipAmount('5')).toBe(5);
    expect(getCustomTipAmount('12.50')).toBe(12.5);
  });

  it('returns 0 for an empty string (mirrors the original `customTip ? ... : 0` check)', () => {
    expect(getCustomTipAmount('')).toBe(0);
  });

  it('returns 0 for a non-numeric string (mirrors the original `parseFloat(...) || 0` fallback)', () => {
    expect(getCustomTipAmount('abc')).toBe(0);
  });

  it('returns 0 for zero (mirrors the original `|| 0` fallback, since parseFloat("0") is falsy)', () => {
    expect(getCustomTipAmount('0')).toBe(0);
  });

  it('BUG FIX: rejects a negative value instead of passing it through (the original `|| 0` fallback did not catch this, since a negative number is truthy)', () => {
    expect(getCustomTipAmount('-5')).toBe(0);
    expect(getCustomTipAmount('-0.01')).toBe(0);
  });

  it('rejects a non-finite parsed value', () => {
    expect(getCustomTipAmount('Infinity')).toBe(0);
  });
});
