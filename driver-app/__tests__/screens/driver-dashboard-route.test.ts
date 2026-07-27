import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'app', 'driver', '(tabs)', 'index.tsx'),
  'utf8',
);

describe('driver dashboard route presentation contract', () => {
  it('does not reuse the pickup-to-dropoff route while navigating to pickup', () => {
    const savedRouteStateGuard = /(?:canUseSaved|useSavedRoute)\s*=\s*[\s\S]*?rideState === 'ride_offered'[\s\S]*?rideState === 'trip_in_progress'/g;

    // Both the state-seeding effect and render path must exclude pre-pickup.
    expect(source.match(savedRouteStateGuard)).toHaveLength(2);
    expect(source).toContain(
      'const needsDirections = GOOGLE_MAPS_API_KEY && !useSavedRoute && !osrmRouteActive;',
    );
  });

  it('clears stale geometry when live pre-pickup routing cannot supply a path', () => {
    expect(source).toMatch(
      /else \{\s*setOsrmRouteActive\(false\);[\s\S]*?setRouteCoords\(\[\]\);/,
    );
    expect(source).toMatch(
      /onError=\{\(err\) => \{[\s\S]*?setRouteCoords\(\[\]\);[\s\S]*?setDirectionsFailed\(true\);/,
    );
  });
});
