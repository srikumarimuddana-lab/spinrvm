import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { toGeoJsonMultiLineString } from '@spinr/shared/utils/routeSegments';

const componentDirectory = path.resolve(__dirname);
const mapSource = fs.readFileSync(path.join(componentDirectory, 'ride-route-map.tsx'), 'utf8');
const detailSource = fs.readFileSync(path.join(componentDirectory, 'ride-detail-modal.tsx'), 'utf8');

describe('admin route replay contract', () => {
  it('derives the v2 actual geometry from the boundary-preserving MultiLineString', () => {
    expect(mapSource).toContain('toGeoJsonMultiLineString');
    expect(mapSource).toContain('actualSegments?: unknown');
    // The gradient is built per-segment from the MultiLineString coordinates, so
    // each captured segment stays independent (no false chord across gaps).
    expect(mapSource).toContain('buildGradientFeatureCollection(actualGeometry.coordinates)');
    expect(toGeoJsonMultiLineString([
      { coordinates: [[50.45, -104.62], [50.46, -104.63]] },
      { coordinates: [[50.47, -104.64], [50.48, -104.65]] },
    ])).toMatchObject({ type: 'MultiLineString' });
  });

  it('does not draw an unrecorded pickup-to-dropoff chord', () => {
    expect(mapSource).not.toContain('coordinates: [[pickupLng, pickupLat], [dropoffLng, dropoffLat]]');
  });

  it('paints the actual route as the shared orange→red gradient, not blue', () => {
    // Colours come from the shared spec via buildPathGradient; the per-feature
    // colour drives the line paint.
    expect(mapSource).toContain('@spinr/shared/constants/routeMapStyle');
    expect(mapSource).toContain('buildPathGradient');
    expect(mapSource).toContain('"line-color": ["get", "color"]');
    // The old per-phase blue actual-route fills are gone.
    expect(mapSource).not.toContain('#2563eb');
    expect(mapSource).not.toContain('"line-color": "#3b82f6"');
  });

  it('draws the shared route pin (disc + glyph), not a bare admin circle', () => {
    // makeRoutePinEl renders routePinSvg from the shared spec — the same pin
    // the rider/driver apps and the Android Auto surface draw. A bare
    // makeCircleMarkerEl here is what made the same ride look like a different
    // product on every screen.
    expect(mapSource).toContain('makeRoutePinEl');
    expect(mapSource).toContain('kind: "pickup"');
    expect(mapSource).toContain('kind: "dropoff"');
    expect(mapSource).not.toContain('makeCircleMarkerEl');
  });

  it('passes the v2 actual segments and their quality label from the admin detail', () => {
    expect(detailSource).toContain('actual_route_segments');
    expect(detailSource).toContain('routeQualityLabel');
    expect(detailSource).toContain('actualSegments={actualSegmentsProp}');
  });
});
