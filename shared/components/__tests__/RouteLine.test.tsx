import React from 'react';
import { render } from '@testing-library/react-native';

import { RouteLine, trimTraveled, type RoutePoint } from '../RouteLine';

// react-native-maps requires native modules Jest can't load. The stub renders a
// findable host element rather than null (the pattern driver-app's
// HeatmapCells.test.tsx uses) so the RENDERING block below can count children —
// the trimTraveled tests never render, so they are unaffected either way.
// The stub records MOUNTS, not just presence. A count-only assertion cannot
// tell "React updated 24 polylines in place" from "React destroyed 24 and built
// 24 new ones" — and that distinction is the entire point of the churn tests
// below, so counting host nodes alone would let a key change regress silently.
const mountCount = { n: 0 };
jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const { View } = require('react-native');
  return {
    __esModule: true,
    Polyline: () => {
      ReactActual.useEffect(() => {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        jest.requireMock('react-native-maps').__mountCount.n += 1;
      }, []);
      return ReactActual.createElement(View, { testID: 'route-polyline' });
    },
    __mountCount: mountCount,
  };
});

// A short, roughly-straight road heading east, one point every ~11m
// (0.0001° longitude at this latitude) — realistic GPS-fix spacing.
const ROAD: RoutePoint[] = [
  { latitude: 50.445, longitude: -104.62 },
  { latitude: 50.445, longitude: -104.6199 },
  { latitude: 50.445, longitude: -104.6198 },
  { latitude: 50.445, longitude: -104.6197 },
  { latitude: 50.445, longitude: -104.6196 },
];

describe('trimTraveled', () => {
  it('returns the path unchanged when no vehicle position is given', () => {
    expect(trimTraveled(ROAD, null)).toEqual(ROAD);
    expect(trimTraveled(ROAD, undefined)).toEqual(ROAD);
  });

  it('returns the path unchanged when it has fewer than 2 points', () => {
    const single = [ROAD[0]];
    expect(trimTraveled(single, { latitude: 50.445, longitude: -104.6199 })).toEqual(single);
  });

  it('drops the portion behind the vehicle and keeps the snapped point + remainder', () => {
    // Vehicle sitting almost exactly on the 3rd point (index 2) — should
    // trim everything before it and start the line from there.
    const vehicle = { latitude: 50.445, longitude: -104.61979 };
    const trimmed = trimTraveled(ROAD, vehicle);
    // First point of the trimmed line is the snapped point (near ROAD[2]),
    // not the original ROAD[0] — the traveled prefix is gone.
    expect(trimmed[0].longitude).toBeCloseTo(ROAD[2].longitude, 3);
    expect(trimmed.length).toBeLessThan(ROAD.length);
    // Everything still ahead of the vehicle (indices 3, 4) survives intact.
    expect(trimmed[trimmed.length - 1]).toEqual(ROAD[ROAD.length - 1]);
  });

  it('renders the whole path unchanged when the vehicle is far off-route (detour/stale fix)', () => {
    // ~1km away — well outside snapToRoute's default 35m tolerance.
    const farAway = { latitude: 50.455, longitude: -104.62 };
    expect(trimTraveled(ROAD, farAway)).toEqual(ROAD);
  });

  it('keeps the whole path when the vehicle sits at the very start (nothing traveled yet)', () => {
    const atStart = { latitude: 50.445, longitude: -104.62 };
    const trimmed = trimTraveled(ROAD, atStart);
    expect(trimmed.length).toBe(ROAD.length);
  });
});

/**
 * RENDERING — the coverage this component did not have.
 *
 * Every consumer's test stubs RouteLine to `() => null` (6 rider-app screens,
 * 3 driver-app ones), and the block above only exercises the exported
 * trimTraveled helper, so nothing anywhere rendered this component even once.
 * That matters more than a normal coverage gap: RouteLine's child COUNT is what
 * drives MapView child add/remove churn, and react-native-maps@1.27.2 mishandles
 * that churn natively (its `features` list is cleared on detach and restored
 * asynchronously, `removeFeatureAt` has no bounds check, and add uses
 * `List.set` where remove uses `List.remove`). These tests characterise the
 * count so a change to it is a visible, reviewable diff rather than a silent one.
 */
const longRoad = (n: number): RoutePoint[] =>
  Array.from({ length: n }, (_, i) => ({ latitude: 50.445, longitude: -104.62 + i * 0.0001 }));

const countPolylines = (el: React.ReactElement) =>
  render(el).queryAllByTestId('route-polyline').length;

describe('RouteLine rendering', () => {
  it('renders nothing when there is no usable geometry', () => {
    expect(countPolylines(<RouteLine />)).toBe(0);
    expect(countPolylines(<RouteLine path={[ROAD[0]]} />)).toBe(0);
  });

  it('caps the child count at `segments` for a single `path`, however long', () => {
    // A real trip's trail is hundreds of points; the gradient must not emit one
    // Polyline per point. NOTE this cap is a property of the single-`path`
    // branch only — see the `paths` test below, where it does NOT hold.
    expect(countPolylines(<RouteLine path={longRoad(600)} segments={24} />)).toBe(24);
    expect(countPolylines(<RouteLine path={longRoad(600)} segments={8} />)).toBe(8);
  });

  it('emits FEWER children than `segments` once the path is shorter than the cap', () => {
    // The characterisation that matters: chunks = min(segments, pts - 1), so as
    // trimTraveled eats the path during a trip the child count SHRINKS. Siblings
    // render after RouteLine inside the same MapView, so each shrink is a
    // mid-list removal for the native side, not a tail one.
    expect(countPolylines(<RouteLine path={longRoad(5)} segments={24} />)).toBe(4);
    expect(countPolylines(<RouteLine path={longRoad(3)} segments={24} />)).toBe(2);
    expect(countPolylines(<RouteLine path={longRoad(2)} segments={24} />)).toBe(1);
  });

  it('does not REMOUNT children when the path updates above the cap', () => {
    // Both paths exceed `segments`, so React updates coordinates in place and
    // the native MapView sees zero add/remove churn. Re-keying an ANCESTOR
    // instead destroys and recreates all 24 at once, which is the mass churn
    // react-native-maps mishandles natively.
    //
    // This does NOT by itself license removing the driver dashboard's rideState
    // key — carSurface.tsx keys RouteLine on route.leg deliberately, to drop a
    // leftover route overlay per leg. See the comment at that key in
    // driver-app/app/driver/(tabs)/index.tsx for which trade-off applies and
    // what has to be verified before either key moves.
    mountCount.n = 0;
    const view = render(<RouteLine path={longRoad(600)} segments={24} />);
    expect(view.queryAllByTestId('route-polyline')).toHaveLength(24);
    expect(mountCount.n).toBe(24); // the initial mount

    view.rerender(<RouteLine path={longRoad(300)} segments={24} />);
    expect(view.queryAllByTestId('route-polyline')).toHaveLength(24);
    // The assertion that matters: still 24 TOTAL mounts, so nothing was
    // destroyed and rebuilt. A content-derived key (e.g. keying on colour or a
    // coordinate hash) would remount all 24 here and push this to 48.
    expect(mountCount.n).toBe(24);

    // Dropping below the cap is the only case that changes the count at all.
    view.rerender(<RouteLine path={longRoad(10)} segments={24} />);
    expect(view.queryAllByTestId('route-polyline')).toHaveLength(9);
  });

  it('does NOT cap the child count on the `paths` (multi-section) branch', () => {
    // Characterising a real difference rather than asserting a cap that does not
    // exist. buildMultiPathGradient floors each section at Math.max(1, ...), so
    // N sections emit at least N polylines no matter what `segments` says — a
    // completed ride captured as many short sections (backgrounding, GPS gaps)
    // renders far more than 24. Live on rider-app's ride-details and
    // ride-completed maps and driver-app's ride-detail.
    const sections = Array.from({ length: 50 }, (_, k) => [
      { latitude: 50.445 + k * 0.01, longitude: -104.62 },
      { latitude: 50.445 + k * 0.01, longitude: -104.6199 },
    ]);
    expect(countPolylines(<RouteLine paths={sections} segments={24} />)).toBe(50);
  });

  it('is deterministic — identical props give an identical child count', () => {
    // Deliberately NOT claiming to prove purity: two renders of the same props
    // would also match if the component held state or ran effects, so this is a
    // determinism check, not a no-state proof. RouteLine's statelessness is
    // evident from its source (no hooks), and the remount assertion above is
    // what actually guards the behaviour that depends on it.
    expect(countPolylines(<RouteLine path={longRoad(50)} segments={24} />)).toBe(
      countPolylines(<RouteLine path={longRoad(50)} segments={24} />),
    );
  });
});
