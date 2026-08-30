import { describe, it, expect } from 'vitest';
import {
  SURGE_JUSTIFICATION_THRESHOLD,
  needsSurgeJustification,
  isSurgeJustificationValid,
} from '../surgeJustificationSchema';

describe('needsSurgeJustification', () => {
  it('requires justification when surge is enabled and multiplier exceeds the threshold', () => {
    expect(needsSurgeJustification(true, 3.0)).toBe(true);
    expect(needsSurgeJustification(true, 10.0)).toBe(true);
  });

  it('does not require justification at exactly the threshold', () => {
    expect(needsSurgeJustification(true, SURGE_JUSTIFICATION_THRESHOLD)).toBe(false);
  });

  it('does not require justification under the threshold', () => {
    expect(needsSurgeJustification(true, 1.5)).toBe(false);
  });

  it('does not require justification when surge is disabled, regardless of multiplier', () => {
    expect(needsSurgeJustification(false, 5.0)).toBe(false);
  });

  it('SURGE_JUSTIFICATION_THRESHOLD mirrors the documented 2.5x surge cap', () => {
    expect(SURGE_JUSTIFICATION_THRESHOLD).toBe(2.5);
  });
});

describe('isSurgeJustificationValid', () => {
  it('accepts a non-empty justification', () => {
    expect(isSurgeJustificationValid('Major event surge — approved by ops lead @name on 2026-05-21')).toBe(true);
  });

  it('accepts a justification that only becomes empty-looking after trimming leading/trailing whitespace, if content remains', () => {
    expect(isSurgeJustificationValid('  concert night  ')).toBe(true);
  });

  it('rejects an empty string', () => {
    expect(isSurgeJustificationValid('')).toBe(false);
  });

  it('rejects a whitespace-only string (mirrors the original `!form.surge_justification.trim()` check)', () => {
    expect(isSurgeJustificationValid('   ')).toBe(false);
    expect(isSurgeJustificationValid('\n\t')).toBe(false);
  });
});
