/**
 * Pins the demand-heatmap presentation spec (components/dashboard/demandHeatmap.ts)
 * and the legend chip.
 *
 * The damping function feeds react-native-maps' <Heatmap> directly: it must
 * stay monotonic (busier cells always render hotter) and bounded below by 1,
 * and the gradient must remain the validated lightness-monotonic ramp — a
 * regression to a green→red gradient would break CVD legibility.
 */
import React from 'react';
import { render } from '@testing-library/react-native';

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    isDark: false,
    colors: {
      overlay: 'rgba(255,255,255,0.95)',
      border: '#E5E7EB',
      textSecondary: '#6B7280',
      textDim: '#666666',
    },
  }),
}));
jest.mock('expo-linear-gradient', () => {
  const { View } = require('react-native');
  return { LinearGradient: View };
});

import {
  dampenHeatmapPoints,
  heatmapAppearance,
  rampColors,
  DEMAND_RAMPS,
} from '../../components/dashboard/demandHeatmap';
import { DemandHeatmapLegend } from '../../components/dashboard/DemandHeatmapLegend';

const pt = (weight: number) => ({ latitude: 52.13, longitude: -106.67, weight });

describe('dampenHeatmapPoints', () => {
  it('log-damps weights: compressive but monotonic', () => {
    const [a, b, c] = dampenHeatmapPoints([pt(3), pt(30), pt(300)]);
    // Order preserved…
    expect(a.weight).toBeLessThan(b.weight);
    expect(b.weight).toBeLessThan(c.weight);
    // …but the 100× raw spread compresses to single digits so one downtown
    // hotspot can't flatten every other neighbourhood.
    expect(c.weight / a.weight).toBeLessThan(4);
    expect(c.weight).toBeLessThan(10);
  });

  it('never emits a weight below 1', () => {
    const damped = dampenHeatmapPoints([pt(1), pt(0), pt(-5 as any)]);
    for (const p of damped) expect(p.weight).toBeGreaterThanOrEqual(1);
  });

  it('preserves coordinates untouched', () => {
    const [p] = dampenHeatmapPoints([pt(7)]);
    expect(p.latitude).toBe(52.13);
    expect(p.longitude).toBe(-106.67);
  });

  it('does not mutate its input', () => {
    const input = [pt(8)];
    dampenHeatmapPoints(input);
    expect(input[0].weight).toBe(8);
  });
});

describe('heatmapAppearance', () => {
  it('defaults to the inferno ramp in both modes', () => {
    expect(heatmapAppearance(true).gradient.colors).toEqual(DEMAND_RAMPS.inferno);
    expect(heatmapAppearance(false).gradient.colors).toEqual(DEMAND_RAMPS.inferno);
  });

  it('selects the admin-configured ramp per theme', () => {
    expect(heatmapAppearance(true, 'ocean').gradient.colors).toEqual(DEMAND_RAMPS.ocean);
    expect(heatmapAppearance(false, 'viridis').gradient.colors).toEqual(DEMAND_RAMPS.viridis);
  });

  it('falls back to the default ramp for unknown theme names', () => {
    expect(rampColors('lava' as any)).toEqual(DEMAND_RAMPS.inferno);
    expect(rampColors(undefined)).toEqual(DEMAND_RAMPS.inferno);
  });

  it('keeps startPoints sorted and within (0, 1] with one stop per color, every theme', () => {
    for (const isDark of [true, false]) {
      for (const theme of Object.keys(DEMAND_RAMPS) as (keyof typeof DEMAND_RAMPS)[]) {
        const { gradient } = heatmapAppearance(isDark, theme);
        expect(gradient.startPoints).toHaveLength(gradient.colors.length);
        const sorted = [...gradient.startPoints].sort((x, y) => x - y);
        expect(gradient.startPoints).toEqual(sorted);
        expect(sorted[0]).toBeGreaterThan(0);
        expect(sorted[sorted.length - 1]).toBeLessThanOrEqual(1);
      }
    }
  });

  it('is more opaque on dark maps, subtler on light maps', () => {
    expect(heatmapAppearance(true).opacity).toBeGreaterThan(heatmapAppearance(false).opacity);
  });
});

describe('DemandHeatmapLegend', () => {
  it('labels the scale so demand identity is never color-alone', () => {
    const { getByText, getByTestId } = render(<DemandHeatmapLegend />);
    expect(getByTestId('demand-heatmap-legend')).toBeTruthy();
    expect(getByText('Rider demand')).toBeTruthy();
    expect(getByText('Low')).toBeTruthy();
    expect(getByText('High')).toBeTruthy();
  });
});
