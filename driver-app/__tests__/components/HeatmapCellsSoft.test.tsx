import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { Circle } from 'react-native-maps';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// Force the soft renderer on. The real flag ships false (it changes how a
// live driver-facing map looks and has never been seen on a native build), so
// without this override the path below is unreachable in tests. The maths
// itself is the real module — only the gate is overridden.
jest.mock('../../lib/heatFalloff', () => ({
  ...jest.requireActual('../../lib/heatFalloff'),
  SOFT_HEAT_RENDER_ENABLED: true,
}));

jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  return {
    __esModule: true,
    Heatmap: (props: any) => ReactActual.createElement('Heatmap', props),
    Circle: (props: any) => ReactActual.createElement('Circle', props),
  };
});

// Real six-digit hex, unlike the '#a' placeholders the sibling test uses —
// hexToRgba slices channels out of the string, so a short value yields
// rgba(10,NaN,NaN,a) and makes every colour assertion vacuous.
const RAMP = ['#FFE3E0', '#FFB3AC', '#FF7A6E', '#FF3B30', '#B71C1C'];
jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({ colors: { heatmapRamp: RAMP } }),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { HeatmapCells } = require('../../components/dashboard/HeatmapCells');
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { HEAT_RING_STOPS, HEAT_BLOB_RADIUS_FACTOR } = jest.requireActual('../../lib/heatFalloff');

const RINGS = HEAT_RING_STOPS.length;
const METERS_PER_LAT_DEG = 111_320;

function render(cells: HeatmapCell[], cellLatDeg = 0.01) {
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(
      <HeatmapCells cells={cells} region={null} cellLatDeg={cellLatDeg} cellLngDeg={cellLatDeg} />,
    );
  });
  return renderer.root.findAllByType(Circle as any).map((n) => n.props);
}

function alphaOf(fillColor: string): number {
  const m = /rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([\d.]+)\s*\)/.exec(fillColor);
  if (!m) throw new Error(`unparseable fillColor: ${fillColor}`);
  return Number(m[1]);
}

describe('HeatmapCells soft renderer', () => {
  it('draws one circle per ring stop for each cell', () => {
    const circles = render([{ lat: 1, lng: 1, weight: 5 }]);
    expect(circles).toHaveLength(RINGS);
  });

  it('nests the rings from the outer radius inward', () => {
    const cellLatDeg = 0.01;
    const circles = render([{ lat: 1, lng: 1, weight: 5 }], cellLatDeg);
    const outer = cellLatDeg * METERS_PER_LAT_DEG * HEAT_BLOB_RADIUS_FACTOR;
    expect(circles.map((c) => c.radius)).toEqual(
      HEAT_RING_STOPS.map((s: number) => outer * s),
    );
    for (let i = 1; i < circles.length; i++) {
      expect(circles[i].radius).toBeLessThan(circles[i - 1].radius);
    }
  });

  it('deepens opacity toward the centre without emitting NaN', () => {
    const circles = render([{ lat: 1, lng: 1, weight: 5 }]);
    const alphas = circles.map((c) => alphaOf(c.fillColor));
    for (const a of alphas) {
      expect(Number.isFinite(a)).toBe(true);
      expect(a).toBeGreaterThan(0);
    }
    for (let i = 1; i < alphas.length; i++) {
      expect(alphas[i]).toBeGreaterThan(alphas[i - 1]);
    }
  });

  it('sets an explicit transparent stroke on every ring', () => {
    // The reported iOS symptom is a heavy black outline; leaving strokeColor
    // undefined is the leading candidate cause.
    for (const c of render([{ lat: 1, lng: 1, weight: 5 }])) {
      expect(c.strokeColor).toBe('transparent');
      expect(c.strokeWidth).toBe(0);
    }
  });

  it('draws a weaker cell fainter than the strongest one', () => {
    const circles = render([
      { lat: 1, lng: 1, weight: 10 },
      { lat: 2, lng: 2, weight: 1 },
    ]);
    const strongest = alphaOf(circles[RINGS - 1].fillColor);
    const weakest = alphaOf(circles[2 * RINGS - 1].fillColor);
    expect(weakest).toBeLessThan(strongest);
  });

  it('still drops non-finite cells before they reach a native shape', () => {
    const circles = render([
      { lat: 1, lng: 1, weight: 5 },
      { lat: NaN, lng: 2, weight: 3 },
      { lat: 3, lng: Infinity, weight: 3 },
    ]);
    expect(circles).toHaveLength(RINGS);
  });
});
