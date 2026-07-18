import {
  normalizeActualRouteSegments,
  routeQualityLabel,
  toGeoJsonMultiLineString,
  toReactNativeSegments,
} from '@spinr/shared/utils/routeSegments';

const actualSegments = [
  { source_segment_index: 0, coordinates: [[50.45, -104.61], [50.451, -104.611]] },
  { source_segment_index: 1, coordinates: [[50.49, -104.67], [50.491, -104.671]] },
];

describe('segmented route geometry', () => {
  it('rejects invalid coordinates without joining separate segments', () => {
    const normalized = normalizeActualRouteSegments([
      ...actualSegments,
      { coordinates: [[95, -104.6], [50.5, -104.7]] },
    ]);

    expect(normalized).toHaveLength(2);
    expect(normalized[0].coordinates).toEqual(actualSegments[0].coordinates);
    expect(normalized[1].coordinates).toEqual(actualSegments[1].coordinates);
  });

  it('converts each segment to independent React Native coordinates', () => {
    expect(toReactNativeSegments(actualSegments)).toEqual([
      [{ latitude: 50.45, longitude: -104.61 }, { latitude: 50.451, longitude: -104.611 }],
      [{ latitude: 50.49, longitude: -104.67 }, { latitude: 50.491, longitude: -104.671 }],
    ]);
  });

  it('produces a GeoJSON MultiLineString in longitude-latitude order', () => {
    expect(toGeoJsonMultiLineString(actualSegments)).toEqual({
      type: 'MultiLineString',
      coordinates: [
        [[-104.61, 50.45], [-104.611, 50.451]],
        [[-104.67, 50.49], [-104.671, 50.491]],
      ],
    });
  });

  it('uses clear incomplete coverage copy', () => {
    expect(routeQualityLabel({ coverage_ratio: 0.54, missing_tail: true })).toBe(
      'Route incomplete · 54% GPS coverage',
    );
  });
});
