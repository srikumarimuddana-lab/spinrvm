import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.join(__dirname, '..', 'ride-details.tsx'), 'utf8');

describe('ride-details v2 route rendering contract', () => {
  it('prefers a revision-matched snapshot and renders actual segments independently', () => {
    expect(source).toContain('toReactNativeSegments');
    expect(source).toContain('actual_route_segments');
    expect(source).toContain('route_snapshot_url && isActualSnapshot');
    expect(source).toContain('actualSegments.map((coordinates, index) => (');
    expect(source).not.toContain('actualSegments.flat()');
  });

  it('labels planned-only geometry and never rebuilds a completed actual route with Directions', () => {
    expect(source).toContain("const routeLabel = hasActualRoute ? 'Actual route' : 'Planned route';");
    expect(source).not.toContain('decodePolyline');
    expect(source).not.toContain('fetchFallbackRoute');
  });

  it('includes revisioned snapshot and incomplete-quality copy in the client PDF HTML', () => {
    expect(source).toContain('Actual route (revision ${routeRevision})');
    expect(source).toContain('Route snapshot unavailable');
    expect(source).toContain('routeQualityLabel');
  });

  it('renders the shared quality label once (no duplicated "Actual route" prefix)', () => {
    // M-H: routeQuality is already a full "Actual route ..." string, so prefixing
    // it with routeLabel produced "Actual route · Actual route".
    expect(source).not.toContain('`${routeLabel} · ${routeQuality}`');
    expect(source).not.toContain('${routeLabel} · Actual GPS route unavailable');
  });
});
