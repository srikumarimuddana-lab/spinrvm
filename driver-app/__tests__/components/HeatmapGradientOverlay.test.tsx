import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { HeatmapGradientOverlay } from '../../components/dashboard/HeatmapGradientOverlay';
import { cellCenter } from '../../hooks/useVisibleHeatmapCells';
import { projectToScreen } from '../../utils/heatmapProjection';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// @shopify/react-native-skia's real components need a native GPU surface
// jest can't provide — stub each one with a plain element carrying the same
// props, so prop wiring (which cells became which Circles, at what
// projected coordinates, with what color) is exactly as assertable as it
// would be for a real render. Mirrors how react-native-maps is mocked
// elsewhere in this suite.
jest.mock('@shopify/react-native-skia', () => {
  const ReactActual = require('react');
  return {
    __esModule: true,
    Canvas: (props: any) => ReactActual.createElement('SkCanvas', props, props.children),
    // The real Group mounts its `layer` prop (a <Paint><Blur/></Paint>
    // element, in this component's usage) as part of its own subtree —
    // that's how a real image-filter layer actually gets built from JSX.
    // `layer` is rendered here as an extra child alongside `props.children`
    // so findAllByType can discover SkPaint/SkBlur in tests; a mock that
    // only forwarded props.children (ignoring layer entirely) would make
    // the blur invisible to any test, real render or not.
    Group: (props: any) => ReactActual.createElement('SkGroup', props, [props.layer, props.children]),
    Circle: (props: any) => ReactActual.createElement('SkCircle', props),
    Paint: (props: any) => ReactActual.createElement('SkPaint', props, props.children),
    Blur: (props: any) => ReactActual.createElement('SkBlur', props),
  };
});

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({ colors: { heatmapRamp: ['#ffe3e0', '#ffb3ac', '#ff7a6e', '#ff3b30', '#b71c1c'] } }),
}));

const region = { latitude: 52.1, longitude: -106.6, latitudeDelta: 0.02, longitudeDelta: 0.02 };
const viewport = { width: 400, height: 800 };

function render(props: Partial<React.ComponentProps<typeof HeatmapGradientOverlay>> = {}) {
  // 'key' in props (not `??`) so an explicit `null` override (e.g. region:
  // null, to test "not yet known") is honored instead of falling through to
  // the default — `null ?? region` would silently resolve to the default,
  // defeating the point of passing null at all.
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(
      <HeatmapGradientOverlay
        cells={'cells' in props ? props.cells! : []}
        region={'region' in props ? props.region : region}
        cellLatDeg={'cellLatDeg' in props ? props.cellLatDeg : 0.01}
        cellLngDeg={'cellLngDeg' in props ? props.cellLngDeg : 0.01}
        driverLocation={props.driverLocation}
        viewport={'viewport' in props ? props.viewport! : viewport}
      />,
    );
  });
  return renderer;
}

describe('HeatmapGradientOverlay', () => {
  it('renders nothing when there are no cells', () => {
    const renderer = render({ cells: [] });
    expect(renderer.toJSON()).toBeNull();
  });

  it('renders nothing when region is not yet known', () => {
    const renderer = render({
      cells: [{ lat: 52.1, lng: -106.6, weight: 5 }],
      region: null,
    });
    expect(renderer.toJSON()).toBeNull();
  });

  it('renders nothing when the viewport has not been measured yet', () => {
    const renderer = render({
      cells: [{ lat: 52.1, lng: -106.6, weight: 5 }],
      viewport: { width: 0, height: 0 },
    });
    expect(renderer.toJSON()).toBeNull();
  });

  it('renders one Circle per visible cell, projected into canvas screen space', () => {
    // 52.104/-106.596 with a 0.01 grid buckets to (52.105, -106.595) — see
    // useVisibleHeatmapCells.test.ts's identical fixture for the bucketing
    // math. Expected screen position is derived from the same cellCenter +
    // projectToScreen functions the component itself uses, rather than a
    // hand-computed magic number, so this test doesn't silently assume
    // raw-coordinate passthrough (cellCenter buckets onto a grid; it does
    // not return the raw lat/lng unchanged).
    const cells: HeatmapCell[] = [{ lat: 52.104, lng: -106.596, weight: 5 }];
    const renderer = render({ cells });
    const circles = renderer.root.findAllByType('SkCircle' as any);
    expect(circles).toHaveLength(1);
    const center = cellCenter(52.104, -106.596, 0.01, 0.01);
    const expected = projectToScreen(center.latitude, center.longitude, region, viewport);
    expect(circles[0].props.cx).toBeCloseTo(expected.x, 6);
    expect(circles[0].props.cy).toBeCloseTo(expected.y, 6);
    expect(circles[0].props.r).toBeGreaterThan(0);
    expect(circles[0].props.color).toMatch(/^rgba\(/);
  });

  it('every Circle is fully opaque — softness comes from the Group blur, not per-circle transparency', () => {
    const cells: HeatmapCell[] = [
      { lat: 52.1, lng: -106.6, weight: 1 },
      { lat: 52.105, lng: -106.6, weight: 10 },
    ];
    const renderer = render({ cells });
    const circles = renderer.root.findAllByType('SkCircle' as any);
    for (const c of circles) {
      expect(c.props.color).toMatch(/,1\)$/);
    }
  });

  it('wraps all circles in exactly one blurred Group (a real merged blur, not per-shape)', () => {
    const cells: HeatmapCell[] = [
      { lat: 52.1, lng: -106.6, weight: 3 },
      { lat: 52.105, lng: -106.6, weight: 7 },
    ];
    const renderer = render({ cells });
    const groups = renderer.root.findAllByType('SkGroup' as any);
    expect(groups).toHaveLength(1);
    const blurs = renderer.root.findAllByType('SkBlur' as any);
    expect(blurs).toHaveLength(1);
    expect(blurs[0].props.blur).toBeGreaterThan(0);
  });

  it('excludes a cell centered on the driver, same as the iOS fallback renderer', () => {
    const cells: HeatmapCell[] = [{ lat: 52.1, lng: -106.6, weight: 5 }];
    const renderer = render({ cells, driverLocation: { latitude: 52.1, longitude: -106.6 } });
    expect(renderer.toJSON()).toBeNull();
  });

  it('sizes the Canvas to the given viewport', () => {
    const renderer = render({ cells: [{ lat: 52.1, lng: -106.6, weight: 5 }], viewport: { width: 320, height: 640 } });
    const canvas = renderer.root.findByType('SkCanvas' as any);
    expect(canvas.props.style).toMatchObject({ width: 320, height: 640 });
  });
});
