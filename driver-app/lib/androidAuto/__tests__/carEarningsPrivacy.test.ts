/**
 * Unit tests for lib/androidAuto/carEarningsPrivacy.ts — the earnings-privacy
 * rule for the Android Auto surface.
 *
 * The rule these lock down is a privacy one, not a styling one: while hidden,
 * the amount must not be produced at all, and every new session must start
 * hidden. Both are checked on the pure helper + store, so neither depends on a
 * head unit or on how the pill happens to be styled today.
 */
import {
  MASKED_AMOUNT,
  displayEarnings,
  useCarEarningsPrivacy,
} from '../carEarningsPrivacy';

describe('displayEarnings', () => {
  it('masks the amount while hidden — the number is never returned', () => {
    expect(displayEarnings('$16.74', true)).toBe(MASKED_AMOUNT);
    expect(displayEarnings('$16.74', true)).not.toContain('16');
  });

  it('returns the real amount once revealed', () => {
    expect(displayEarnings('$16.74', false)).toBe('$16.74');
  });

  it('stays null when there is no summary at all (pill hidden entirely)', () => {
    expect(displayEarnings(null, true)).toBeNull();
    expect(displayEarnings(null, false)).toBeNull();
  });

  it('masks $0.00 too — a zero is still the driver’s financial information', () => {
    expect(displayEarnings('$0.00', true)).toBe(MASKED_AMOUNT);
  });
});

describe('useCarEarningsPrivacy', () => {
  beforeEach(() => useCarEarningsPrivacy.getState().reset());

  it('defaults to hidden', () => {
    expect(useCarEarningsPrivacy.getState().hidden).toBe(true);
  });

  it('toggles both ways', () => {
    useCarEarningsPrivacy.getState().toggle();
    expect(useCarEarningsPrivacy.getState().hidden).toBe(false);
    useCarEarningsPrivacy.getState().toggle();
    expect(useCarEarningsPrivacy.getState().hidden).toBe(true);
  });

  it('reset re-masks, so a reveal never carries into the next session', () => {
    useCarEarningsPrivacy.getState().toggle();
    expect(useCarEarningsPrivacy.getState().hidden).toBe(false);
    useCarEarningsPrivacy.getState().reset();
    expect(useCarEarningsPrivacy.getState().hidden).toBe(true);
  });
});
