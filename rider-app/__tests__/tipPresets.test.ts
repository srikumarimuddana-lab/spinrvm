/**
 * Fare-scaled tip presets.
 *
 * The reconciliation rule here guards a money bug that is easy to reintroduce:
 * the ride-completed screen is reachable BEFORE the fare loads (a push
 * notification cold-starts straight into it), so a rider can tap a preset built
 * from a placeholder fare. When the real fare arrives the amounts change, and if
 * the stale selection survives, every chip renders unselected while submit still
 * charges the old number — a tip billed with nothing on screen showing it.
 */

import {
  computeTipOptions,
  isTipLadderReady,
  reconcileSelectedTip,
} from '../components/tipPresets';

describe('computeTipOptions', () => {
  it('scales with the fare', () => {
    // 15/18/20% of $100.
    expect(computeTipOptions(100)).toEqual([15, 18, 20]);
  });

  it('never offers a $10 preset on a $5 ride', () => {
    const options = computeTipOptions(5);
    expect(Math.max(...options)).toBeLessThan(10);
  });

  it('is always strictly increasing, so no two buttons collide', () => {
    // Small fares round every percentage to the same dollar; without the
    // forced ladder this renders three identical buttons AND duplicate React
    // keys, since the key IS the amount.
    for (const fare of [0, 1, 3, 4, 5, 7, 12, 25, 50, 200]) {
      const options = computeTipOptions(fare);
      expect(options).toHaveLength(3);
      expect(options[1]).toBeGreaterThan(options[0]);
      expect(options[2]).toBeGreaterThan(options[1]);
      expect(new Set(options).size).toBe(3);
    }
  });

  it('floors at $1 rather than offering a $0 tip', () => {
    expect(Math.min(...computeTipOptions(1))).toBeGreaterThanOrEqual(1);
  });

  it('returns whole dollars only — the charged amount must match the label', () => {
    for (const fare of [7.77, 23.45, 61.19]) {
      for (const value of computeTipOptions(fare)) {
        expect(Number.isInteger(value)).toBe(true);
      }
    }
  });

  it('degrades to a placeholder ladder on an unknown fare', () => {
    expect(computeTipOptions(0)).toEqual([1, 2, 3]);
    expect(computeTipOptions(NaN)).toEqual([1, 2, 3]);
    expect(computeTipOptions(-5)).toEqual([1, 2, 3]);
  });
});

describe('isTipLadderReady', () => {
  it('is false until a real fare is known, so the placeholder is not tappable', () => {
    expect(isTipLadderReady(0)).toBe(false);
    expect(isTipLadderReady(NaN)).toBe(false);
    expect(isTipLadderReady(-1)).toBe(false);
  });

  it('is true for a real fare', () => {
    expect(isTipLadderReady(0.5)).toBe(true);
    expect(isTipLadderReady(25)).toBe(true);
  });
});

describe('reconcileSelectedTip', () => {
  it('keeps a selection that is still on the ladder', () => {
    expect(reconcileSelectedTip(18, [15, 18, 20])).toBe(18);
  });

  it('drops a selection the ladder no longer offers', () => {
    // The regression this exists for: rider taps $2 on the [1,2,3] placeholder,
    // the real $100 fare loads, ladder becomes [15,18,20]. Keeping 2 would
    // charge a tip matching no visible button.
    expect(reconcileSelectedTip(2, [15, 18, 20])).toBeNull();
  });

  it('leaves "no tip" alone', () => {
    expect(reconcileSelectedTip(null, [15, 18, 20])).toBeNull();
  });

  it('end to end: a placeholder tap does not survive the real fare arriving', () => {
    const placeholder = computeTipOptions(0);
    expect(isTipLadderReady(0)).toBe(false);

    const tapped = placeholder[1];
    const real = computeTipOptions(100);

    expect(real).not.toContain(tapped);
    expect(reconcileSelectedTip(tapped, real)).toBeNull();
  });

  it('a genuine selection survives an unrelated re-render at the same fare', () => {
    const first = computeTipOptions(40);
    const second = computeTipOptions(40);
    expect(reconcileSelectedTip(first[2], second)).toBe(first[2]);
  });
});
