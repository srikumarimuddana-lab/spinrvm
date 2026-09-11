import {
  HEAT_RING_STOPS,
  HEAT_PEAK_ALPHA,
  HEAT_BLOB_RADIUS_FACTOR,
  HEAT_NATIVE_LAYER_ALPHA,
  SOFT_HEAT_RENDER_ENABLED,
  cellCenter,
  hexToRgba,
  nativeGradient,
  ringAlphas,
  paintedPeakAlpha,
} from '../../lib/heatFalloff';

/** Stack `alphas` largest-first the way the renderer draws them. */
function cumulative(alphas: number[]): number[] {
  let acc = 0;
  return alphas.map((a) => {
    acc = acc + a * (1 - acc);
    return acc;
  });
}

describe('ringAlphas', () => {
  it('returns one alpha per ring stop', () => {
    expect(ringAlphas(1)).toHaveLength(HEAT_RING_STOPS.length);
  });

  it('keeps every alpha a finite number inside [0, 1]', () => {
    for (const intensity of [0, 0.1, 0.5, 0.9, 1]) {
      for (const a of ringAlphas(intensity)) {
        expect(Number.isFinite(a)).toBe(true);
        expect(a).toBeGreaterThanOrEqual(0);
        expect(a).toBeLessThanOrEqual(1);
      }
    }
  });

  it('grows denser toward the centre', () => {
    const alphas = ringAlphas(1);
    for (let i = 1; i < alphas.length; i++) {
      expect(alphas[i]).toBeGreaterThan(alphas[i - 1]);
    }
  });

  it('stacks into a monotonically rising cumulative opacity', () => {
    const stacked = cumulative(ringAlphas(1));
    for (let i = 1; i < stacked.length; i++) {
      expect(stacked[i]).toBeGreaterThan(stacked[i - 1]);
    }
  });

  // The whole point of the change: the old two-circle stand-in jumped from
  // 0.14 to ~0.57 in one step, which is what reads as a bullseye ring on iOS.
  it('never steps as hard as the two-circle renderer it replaces', () => {
    const OLD_WORST_STEP = 1 - (1 - 0.14) * (1 - 0.5) - 0.14; // ≈ 0.43
    const stacked = cumulative(ringAlphas(1));
    let worst = 0;
    let prev = 0;
    for (const c of stacked) {
      worst = Math.max(worst, c - prev);
      prev = c;
    }
    expect(worst).toBeLessThan(OLD_WORST_STEP / 2);
  });

  it('scales the painted peak linearly with intensity', () => {
    const full = paintedPeakAlpha();
    const half = cumulative(ringAlphas(0.5)).slice(-1)[0];
    expect(half / full).toBeCloseTo(0.5, 2);
  });

  it('paints nothing for a zero-weight cell', () => {
    expect(ringAlphas(0).every((a) => a === 0)).toBe(true);
  });

  it('clamps out-of-range and non-finite intensities instead of leaking NaN', () => {
    // A NaN alpha reaches the platform SDK unmodified, so this must clamp
    // rather than propagate — Math.min/max alone would return NaN here.
    for (const bad of [NaN, Infinity, -Infinity, -1, 2]) {
      const alphas = ringAlphas(bad as number);
      expect(alphas.every((a) => Number.isFinite(a))).toBe(true);
    }
    expect(ringAlphas(NaN as number).every((a) => a === 0)).toBe(true);
    expect(ringAlphas(-1)).toEqual(ringAlphas(0));
    expect(ringAlphas(2)).toEqual(ringAlphas(1));
  });
});

describe('paintedPeakAlpha', () => {
  it('matches the cumulative opacity of a full-intensity stack', () => {
    const stacked = cumulative(ringAlphas(1));
    expect(paintedPeakAlpha()).toBeCloseTo(stacked[stacked.length - 1], 6);
  });

  // A LONE cell must stay faint. The reference's hot core comes from many
  // overlapping contributions, not from one saturated cell, and the base map
  // has to stay readable underneath.
  it('leaves a single cell faint enough to read the map through', () => {
    expect(paintedPeakAlpha()).toBeGreaterThan(0.15);
    expect(paintedPeakAlpha()).toBeLessThan(0.35);
    expect(paintedPeakAlpha()).toBeLessThan(HEAT_PEAK_ALPHA);
  });
});

describe('SOFT_HEAT_RENDER_ENABLED', () => {
  // Guards the release gate, not the maths: flipped on 2026-09-11 after a
  // live driver confirmed the flag-off "square boundary" look on both the
  // phone and the Android Auto car display — the native-device evidence this
  // flag was shipped dark waiting for. See its own doc comment and
  // docs/change-log/2026-09-11-heatmap-soft-render-enabled.md.
  it('is on now that native-device evidence exists for the reported bug', () => {
    expect(SOFT_HEAT_RENDER_ENABLED).toBe(true);
  });
});

describe('cellCenter', () => {
  // Shared by the phone and the car renderer: if they snapped differently the
  // same demand would draw in two slightly different places per surface.
  it('snaps a point to the middle of its grid square', () => {
    const c = cellCenter(52.131, -106.673, 0.004, 0.006);
    expect(c.latitude).toBeCloseTo(52.13, 9);
    expect(c.longitude).toBeCloseTo(-106.671, 9);
  });

  it('maps every point inside one square to the same centre', () => {
    // Both points sit inside lat [52.128, 52.132) x lng [-106.674, -106.668).
    const a = cellCenter(52.1285, -106.6735, 0.004, 0.006);
    const b = cellCenter(52.1315, -106.6685, 0.004, 0.006);
    expect(a.latitude).toBeCloseTo(b.latitude, 9);
    expect(a.longitude).toBeCloseTo(b.longitude, 9);
  });

  it('keeps negative longitudes on the floor side, not toward zero', () => {
    // Math.floor, not truncation — Saskatchewan is entirely west of Greenwich,
    // so a truncating implementation would shift every cell here by one square.
    expect(cellCenter(1, -0.001, 0.01, 0.01).longitude).toBeCloseTo(-0.005, 9);
  });
});

/** Alpha this cell paints at `d` cell-spans from its centre. */
function paintedAt(d: number): number {
  const f = d / HEAT_BLOB_RADIUS_FACTOR;
  if (f > 1) return 0;
  const alphas = ringAlphas(1);
  let acc = 0;
  let painted = 0;
  HEAT_RING_STOPS.forEach((stop, i) => {
    acc = acc + alphas[i] * (1 - acc);
    if (f <= stop) painted = acc;
  });
  return painted;
}

const composite = (xs: number[]) => 1 - xs.reduce((p, x) => p * (1 - x), 1);

describe('kernel width and density build-up', () => {
  // This is what separates "a field" from "tiled circles": one cell has to
  // reach its neighbours so their contributions integrate.
  it('reaches the four edge neighbours but not the diagonals', () => {
    expect(paintedAt(1)).toBeGreaterThan(0);
    expect(paintedAt(Math.SQRT2)).toBe(0);
  });

  it('builds a hot core from overlap rather than from one cell', () => {
    const lone = paintedAt(0);
    const busy = composite([lone, ...Array(4).fill(paintedAt(1))]);
    expect(busy).toBeGreaterThan(lone * 1.4);
    expect(busy).toBeLessThan(0.6); // still translucent; roads stay visible
  });

  // Android sums density itself, so it takes the composited value, not the
  // per-cell one. If these drift apart the two phones stop matching.
  it('keeps the Android layer opacity aligned with the composited core', () => {
    const busy = composite([paintedAt(0), ...Array(4).fill(paintedAt(1))]);
    expect(HEAT_NATIVE_LAYER_ALPHA).toBeCloseTo(busy, 2);
  });
});

describe('nativeGradient', () => {
  const RAMP = ['#FFE3E0', '#FFB3AC', '#FF7A6E', '#FF3B30', '#B71C1C'];

  it('anchors the lookup table at fully transparent', () => {
    const { colors } = nativeGradient(RAMP);
    // Without this the lowest density paints solid ramp[0] and the layer ends
    // at a visible disc edge instead of fading out.
    expect(colors[0]).toBe('rgba(255,227,224,0)');
  });

  it('keeps the transparent anchor in the same hue as the first band', () => {
    const { colors } = nativeGradient(RAMP);
    expect(colors[0]).toBe(hexToRgba(RAMP[0], 0));
    expect(colors[1]).toBe(RAMP[0]);
  });

  it('emits one start point per colour, strictly increasing across [0, 1]', () => {
    const { colors, startPoints } = nativeGradient(RAMP);
    expect(startPoints).toHaveLength(colors.length);
    expect(startPoints[0]).toBe(0);
    expect(startPoints[startPoints.length - 1]).toBeCloseTo(1, 9);
    for (let i = 1; i < startPoints.length; i++) {
      expect(startPoints[i]).toBeGreaterThan(startPoints[i - 1]);
    }
  });

  it('preserves the brand ramp verbatim above the anchor', () => {
    expect(nativeGradient(RAMP).colors.slice(1)).toEqual(RAMP);
  });

  it('does not divide by zero on a single-colour ramp', () => {
    const { colors, startPoints } = nativeGradient(['#FF3B30']);
    expect(colors).toHaveLength(2);
    expect(startPoints.every((p) => Number.isFinite(p))).toBe(true);
    expect(startPoints[1]).toBeGreaterThan(startPoints[0]);
  });
});
