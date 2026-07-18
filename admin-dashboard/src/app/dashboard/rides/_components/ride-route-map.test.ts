import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { toGeoJsonMultiLineString } from '@spinr/shared/utils/routeSegments';

const componentDirectory = path.resolve(__dirname);
const mapSource = fs.readFileSync(path.join(componentDirectory, 'ride-route-map.tsx'), 'utf8');
const detailSource = fs.readFileSync(path.join(componentDirectory, 'ride-detail-modal.tsx'), 'utf8');

describe('admin route replay contract', () => {
  it('uses a boundary-preserving MultiLineString for v2 actual geometry', () => {
    expect(mapSource).toContain('toGeoJsonMultiLineString');
    expect(mapSource).toContain('actualSegments?: unknown');
    expect(mapSource).toContain('geometry: actualGeometry');
    expect(toGeoJsonMultiLineString([
      { coordinates: [[50.45, -104.62], [50.46, -104.63]] },
      { coordinates: [[50.47, -104.64], [50.48, -104.65]] },
    ])).toMatchObject({ type: 'MultiLineString' });
  });

  it('does not draw an unrecorded pickup-to-dropoff chord', () => {
    expect(mapSource).not.toContain('coordinates: [[pickupLng, pickupLat], [dropoffLng, dropoffLat]]');
  });

  it('memoizes the actual geometry so the map is not rebuilt every render', () => {
    // MINOR 3b: actualGeometry is a dependency of the map-init effect whose
    // cleanup does `map.remove()`. A fresh identity each render tears down and
    // rebuilds the MapLibre map on every parent re-render — memoize it.
    expect(mapSource).toContain('useMemo(() => toGeoJsonMultiLineString(actualSegments), [actualSegments])');
  });

  it('passes the v2 actual segments and their quality label from the admin detail', () => {
    expect(detailSource).toContain('actual_route_segments');
    expect(detailSource).toContain('routeQualityLabel');
    expect(detailSource).toContain('actualSegments={actualSegmentsProp}');
  });
});
