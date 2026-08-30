import { describe, it, expect } from 'vitest';
import { isTaxJustificationValid } from '../taxJustificationSchema';

describe('isTaxJustificationValid', () => {
  it('accepts a non-empty justification', () => {
    expect(isTaxJustificationValid('SK PST rate change per provincial budget update')).toBe(true);
  });

  it('accepts a justification that only becomes empty-looking after trimming leading/trailing whitespace, if content remains', () => {
    expect(isTaxJustificationValid('  budget update  ')).toBe(true);
  });

  it('rejects undefined (mirrors Cancel on window.prompt())', () => {
    expect(isTaxJustificationValid(undefined)).toBe(false);
  });

  it('rejects null', () => {
    expect(isTaxJustificationValid(null)).toBe(false);
  });

  it('rejects an empty string', () => {
    expect(isTaxJustificationValid('')).toBe(false);
  });

  it('rejects a whitespace-only string (mirrors the original `!justification` check on an already-trimmed value)', () => {
    expect(isTaxJustificationValid('   ')).toBe(false);
    expect(isTaxJustificationValid('\n\t')).toBe(false);
  });
});
