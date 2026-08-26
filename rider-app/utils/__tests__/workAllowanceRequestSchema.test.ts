/**
 * ACTION_ITEMS.md B39 pilot: closes the "validation-rule coverage is
 * invisible" gap the item names — this schema previously lived as inline
 * `isValid` logic in app/work-allowance-request.tsx with no dedicated
 * test. Every case here pins the exact accept/reject behavior the old
 * inline check had (`!isNaN(parseFloat(amount)) && parseFloat(amount) > 0
 * && reason.trim().length >= 5`), not a new/tightened/loosened rule.
 */
import { isWorkAllowanceRequestValid, workAllowanceRequestSchema } from '../workAllowanceRequestSchema';

describe('workAllowanceRequestSchema', () => {
  describe('accepts', () => {
    it.each([
      ['a plain positive integer amount', '25', 'Need funds for gas'],
      ['a decimal amount', '99.99', 'Need funds for gas'],
      ['a reason exactly 5 characters long', '10', '12345'],
      ['a reason with leading/trailing whitespace that trims to >= 5', '10', '  hello  '],
      ['a large amount', '5000', 'Emergency vehicle repair needed'],
    ])('%s', (_label, amount, reason) => {
      expect(isWorkAllowanceRequestValid(amount, reason)).toBe(true);
      expect(workAllowanceRequestSchema.safeParse({ amount, reason }).success).toBe(true);
    });
  });

  describe('rejects', () => {
    it.each([
      ['an empty amount string', '', 'Need funds for gas'],
      ['a non-numeric amount', 'abc', 'Need funds for gas'],
      ['a zero amount', '0', 'Need funds for gas'],
      ['a negative amount', '-10', 'Need funds for gas'],
      ['a bare decimal point', '.', 'Need funds for gas'],
      ['a reason under 5 characters', '25', 'gas'],
      ['an empty reason', '25', ''],
      ['a reason that trims to under 5 characters', '25', '   hi   '],
      ['a whitespace-only reason', '25', '     '],
    ])('%s', (_label, amount, reason) => {
      expect(isWorkAllowanceRequestValid(amount, reason)).toBe(false);
      expect(workAllowanceRequestSchema.safeParse({ amount, reason }).success).toBe(false);
    });
  });

  it('trims the reason before validating length (matches the old reason.trim().length >= 5 check)', () => {
    const result = workAllowanceRequestSchema.safeParse({ amount: '10', reason: '  hello  ' });
    expect(result.success).toBe(true);
  });
});
