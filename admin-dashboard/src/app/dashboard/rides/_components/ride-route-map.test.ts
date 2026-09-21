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

  it('refits the camera after the dialog panel has a real size', () => {
    // Default zoom 13 in a 280px-tall panel cannot show an 8 km N-S ride.
    // fitBounds must run after map.resize() once the container is measurable.
    expect(mapSource).toContain('map.resize()');
    expect(mapSource).toContain('ResizeObserver');
    expect(mapSource).toContain('box.clientHeight < 2');
  });

  it('normalizes stored polylines in the detail modal', () => {
    expect(detailSource).toContain('normalizeDecodedPolyline');
    expect(mapSource).toContain('routeLineData');
    expect(mapSource).toContain('finiteTrail');
  });

  it('draws the route as an SVG overlay so it cannot sit under basemap fills', () => {
    // Pins are DOM markers; a GL line can be buried by late style fills or
    // wiped by a styledata clear+add loop. The overlay uses the same stacking
    // context as the pins.
    expect(mapSource).toContain('ride-route-svg-overlay');
    expect(mapSource).toContain('map.project');
    expect(mapSource).toContain('syncRouteSvgOverlay');
  });

  it('does not rebuild route layers on every styledata once they exist', () => {
    expect(mapSource).toContain('hasRouteLayer');
    expect(mapSource).toContain('restackRouteLayers');
  });

  it('opens the ride modal on Planned Trip so the camera frames that polyline', () => {
    // Actual GPS is often incomplete; defaulting to that tab framed two pins
    // (or a fragment) instead of the booked road route.
    expect(detailSource).toContain('useState<"pickup" | "actual" | "planned">("planned")');
    expect(detailSource).toContain('setSelectedPhase("planned")');
  });

  it('draws the booked polyline on Actual Trip when GPS is too incomplete to measure', () => {
    // planned_estimated means the km card already shows booked distance; the
    // map must show that same path, not a leftover GPS fragment.
    expect(detailSource).toContain('planned_estimated');
    expect(detailSource).toContain('gpsTooIncomplete');
    expect(detailSource).toContain('normalizeActualRouteSegments');
  });

  it('passes the v2 actual segments and their quality label from the admin detail', () => {
    expect(detailSource).toContain('actual_route_segments');
    expect(detailSource).toContain('routeQualityLabel');
    expect(detailSource).toContain('actualSegments={actualSegmentsProp}');
  });

  it('never borrows the pickup-to-dropoff chord for a GPS phase with no geometry', () => {
    // Ride 0c24901f: the Pickup tab said "No Phase 2 GPS trail for this ride"
    // and still drew a long diagonal, because suppressStraightFallback was only
    // set for imported rides. That diagonal is the *trip's* crow-flies line, so
    // it read as a driver approach on the screen used for SGI and dispute
    // review and contradicted the 0.60 km pickup card above it.
    expect(detailSource).toContain('const noGeometryForPhase');
    expect(detailSource).toContain('suppressStraightFallback={importedNoGps || noGeometryForPhase}');
    // The planned view is exempt: there the straight line IS the content, and
    // it is labelled "straight-line reference".
    expect(detailSource).toContain('selectedPhase !== "planned" && !hasGeometryForPhase');
    expect(detailSource).toContain('Planned Trip (straight-line reference)');
  });

  it('counts a phase as having geometry only when a line can actually be drawn', () => {
    // A one-point trail renders nothing, so it must not keep the suppression
    // off and let the chord through; the map's own guards use the same bar.
    expect(detailSource).toContain('(pickupProp?.length ?? 0) > 1');
    expect(detailSource).toContain('(tripProp?.length ?? 0) > 1');
    expect(detailSource).toContain('(plannedProp?.length ?? 0) > 1');
    expect(detailSource).toContain('(trailForMap?.length ?? 0) > 1');
    expect(detailSource).toContain('actualSegmentsProp != null');
    expect(mapSource).toContain('!suppressStraightFallback && !hasPlannedTrail && !hasRouteGeometry');
  });
});
