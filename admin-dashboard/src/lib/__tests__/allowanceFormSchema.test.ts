import { describe, it, expect } from 'vitest';
import { isAllowanceFormValid, allowanceFormSchema } from '../allowanceFormSchema';

const base = { type: 'unlimited' as const, amount: '', periodStart: '', periodEnd: '' };

describe('isAllowanceFormValid', () => {
  it('accepts "unlimited" with no amount/dates', () => {
    expect(isAllowanceFormValid({ ...base, type: 'unlimited' })).toBe(true);
  });

  it('rejects "unlimited" — irrelevant, amount/dates are ignored either way', () => {
    // Sanity: unlimited never requires amount even if one happens to be set.
    expect(isAllowanceFormValid({ ...base, type: 'unlimited', amount: '999' })).toBe(true);
  });

  it('accepts "one_time" with an amount', () => {
    expect(isAllowanceFormValid({ ...base, type: 'one_time', amount: '500' })).toBe(true);
  });

  it('rejects "one_time" with an empty amount', () => {
    expect(isAllowanceFormValid({ ...base, type: 'one_time', amount: '' })).toBe(false);
  });

  it('accepts "fixed_recurring" with amount and both period dates', () => {
    expect(
      isAllowanceFormValid({
        type: 'fixed_recurring',
        amount: '500',
        periodStart: '2026-01-01',
        periodEnd: '2026-01-31',
      }),
    ).toBe(true);
  });

  it('rejects "fixed_recurring" with no amount', () => {
    expect(
      isAllowanceFormValid({
        type: 'fixed_recurring',
        amount: '',
        periodStart: '2026-01-01',
        periodEnd: '2026-01-31',
      }),
    ).toBe(false);
  });

  it('rejects "fixed_recurring" missing periodStart', () => {
    expect(
      isAllowanceFormValid({
        type: 'fixed_recurring',
        amount: '500',
        periodStart: '',
        periodEnd: '2026-01-31',
      }),
    ).toBe(false);
  });

  it('rejects "fixed_recurring" missing periodEnd', () => {
    expect(
      isAllowanceFormValid({
        type: 'fixed_recurring',
        amount: '500',
        periodStart: '2026-01-01',
        periodEnd: '',
      }),
    ).toBe(false);
  });

  it('rejects "fixed_recurring" missing both amount and period dates', () => {
    expect(
      isAllowanceFormValid({ type: 'fixed_recurring', amount: '', periodStart: '', periodEnd: '' }),
    ).toBe(false);
  });

  it('rejects an unknown type value', () => {
    expect(isAllowanceFormValid({ type: 'bogus', amount: '500', periodStart: '', periodEnd: '' })).toBe(false);
  });
});

describe('allowanceFormSchema issue priority (mirrors save()\'s throw order)', () => {
  it('reports "Amount is required." first when both amount and period dates are missing', () => {
    const result = allowanceFormSchema.safeParse({
      type: 'fixed_recurring',
      amount: '',
      periodStart: '',
      periodEnd: '',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].message).toBe('Amount is required.');
    }
  });

  it('reports the period-dates message when only dates are missing', () => {
    const result = allowanceFormSchema.safeParse({
      type: 'fixed_recurring',
      amount: '500',
      periodStart: '',
      periodEnd: '',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].message).toBe('fixed_recurring requires both period dates.');
    }
  });
});
