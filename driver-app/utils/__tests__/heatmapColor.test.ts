import { hexToRgb, rampColorForRatio, rgbaString } from '../heatmapColor';

const RAMP = ['#ffe3e0', '#ffb3ac', '#ff7a6e', '#ff3b30', '#b71c1c'] as const;

describe('hexToRgb', () => {
  it('parses a 6-digit hex color into its RGB components', () => {
    expect(hexToRgb('#ff3b30')).toEqual([255, 59, 48]);
    expect(hexToRgb('#000000')).toEqual([0, 0, 0]);
    expect(hexToRgb('#ffffff')).toEqual([255, 255, 255]);
  });
});

describe('rampColorForRatio', () => {
  it('lands exactly on the first stop at ratio 0', () => {
    expect(rampColorForRatio(0, RAMP)).toEqual(hexToRgb(RAMP[0]));
  });

  it('lands exactly on the last stop at ratio 1', () => {
    expect(rampColorForRatio(1, RAMP)).toEqual(hexToRgb(RAMP[4]));
  });

  it('interpolates linearly between two adjacent stops mid-segment', () => {
    // 5 stops -> 4 segments; ratio 0.125 = halfway through the first segment.
    const [r, g, b] = rampColorForRatio(0.125, RAMP);
    const [r1, g1, b1] = hexToRgb(RAMP[0]);
    const [r2, g2, b2] = hexToRgb(RAMP[1]);
    expect(r).toBeCloseTo((r1 + r2) / 2, 5);
    expect(g).toBeCloseTo((g1 + g2) / 2, 5);
    expect(b).toBeCloseTo((b1 + b2) / 2, 5);
  });

  it('produces different colors for different ratios (not bucketed)', () => {
    const a = rampColorForRatio(0.1, RAMP);
    const b = rampColorForRatio(0.3, RAMP);
    expect(a).not.toEqual(b);
  });

  it('clamps an out-of-range ratio instead of indexing past the ramp', () => {
    expect(rampColorForRatio(-1, RAMP)).toEqual(hexToRgb(RAMP[0]));
    expect(rampColorForRatio(2, RAMP)).toEqual(hexToRgb(RAMP[4]));
  });
});

describe('rgbaString', () => {
  it('formats an [r,g,b] tuple and alpha into an rgba() string, rounding components', () => {
    expect(rgbaString([255, 59.4, 48.6], 0.5)).toBe('rgba(255,59,49,0.5)');
  });
});
