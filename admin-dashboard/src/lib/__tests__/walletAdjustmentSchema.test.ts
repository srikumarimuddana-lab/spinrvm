import { describe, it, expect } from 'vitest';
import {
  MAX_SINGLE_ADJUSTMENT,
  isAdjustmentAmountValid,
  isAdjustmentAmountWithinLimit,
  isAdjustmentAmountFullyValid,
  isAdjustmentNoteValid,
} from '../walletAdjustmentSchema';

describe('isAdjustmentAmountValid', () => {
  it('accepts a positive amount', () => {
    expect(isAdjustmentAmountValid(100)).toBe(true);
  });

  it('accepts a negative amount', () => {
    expect(isAdjustmentAmountValid(-50)).toBe(true);
  });

  it('accepts a fractional amount', () => {
    expect(isAdjustmentAmountValid(12.5)).toBe(true);
  });

  it('rejects zero', () => {
    expect(isAdjustmentAmountValid(0)).toBe(false);
  });

  it('rejects NaN (mirrors parseFloat("") / parseFloat("abc"))', () => {
    expect(isAdjustmentAmountValid(NaN)).toBe(false);
    expect(isAdjustmentAmountValid(parseFloat('abc'))).toBe(false);
    expect(isAdjustmentAmountValid(parseFloat(''))).toBe(false);
  });

  it('accepts an amount parsed from a valid signed string, matching the original parseFloat flow', () => {
    expect(isAdjustmentAmountValid(parseFloat('-250.75'))).toBe(true);
    expect(isAdjustmentAmountValid(parseFloat('250.75'))).toBe(true);
  });
});

describe('isAdjustmentAmountWithinLimit', () => {
  it('accepts an amount exactly at the cap', () => {
    expect(isAdjustmentAmountWithinLimit(MAX_SINGLE_ADJUSTMENT)).toBe(true);
    expect(isAdjustmentAmountWithinLimit(-MAX_SINGLE_ADJUSTMENT)).toBe(true);
  });

  it('accepts an amount under the cap', () => {
    expect(isAdjustmentAmountWithinLimit(9999.99)).toBe(true);
    expect(isAdjustmentAmountWithinLimit(-9999.99)).toBe(true);
  });

  it('rejects an amount over the cap, either sign', () => {
    expect(isAdjustmentAmountWithinLimit(MAX_SINGLE_ADJUSTMENT + 0.01)).toBe(false);
    expect(isAdjustmentAmountWithinLimit(-(MAX_SINGLE_ADJUSTMENT + 0.01))).toBe(false);
  });

  it('MAX_SINGLE_ADJUSTMENT is the documented $10,000 CAD safety cap', () => {
    expect(MAX_SINGLE_ADJUSTMENT).toBe(10000);
  });
});

describe('isAdjustmentAmountFullyValid (combined predicate)', () => {
  it('accepts a valid in-limit amount', () => {
    expect(isAdjustmentAmountFullyValid(500)).toBe(true);
  });

  it('rejects zero even though it is within the limit', () => {
    expect(isAdjustmentAmountFullyValid(0)).toBe(false);
  });

  it('rejects an amount over the limit even though it is nonzero', () => {
    expect(isAdjustmentAmountFullyValid(50000)).toBe(false);
  });

  it('rejects NaN', () => {
    expect(isAdjustmentAmountFullyValid(NaN)).toBe(false);
  });
});

describe('isAdjustmentNoteValid', () => {
  it('accepts a non-empty reason', () => {
    expect(isAdjustmentNoteValid('Manual correction for double-charged fare')).toBe(true);
  });

  it('accepts a reason that only becomes empty-looking after trimming leading/trailing whitespace, if content remains', () => {
    expect(isAdjustmentNoteValid('  goodwill credit  ')).toBe(true);
  });

  it('rejects an empty string', () => {
    expect(isAdjustmentNoteValid('')).toBe(false);
  });

  it('rejects a whitespace-only string (mirrors the original `!notes.trim()` check)', () => {
    expect(isAdjustmentNoteValid('   ')).toBe(false);
    expect(isAdjustmentNoteValid('\n\t')).toBe(false);
  });
});
