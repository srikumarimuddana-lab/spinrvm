import fs from 'fs';
import path from 'path';

// CRLF→LF — see hooks/__tests__/onlineResync.test.ts. Windows checkouts are CRLF.
// This file matters most: its toMatch regexes deliberately span lines. They happen
// to be CRLF-safe today because [\s\S] and \s both match \r, but that is a property
// of those particular patterns, not of the file — normalising removes the trap.
const source = fs
  .readFileSync(path.resolve(__dirname, '..', '..', 'app', 'driver', '(tabs)', 'index.tsx'), 'utf8')
  .replace(/\r\n/g, '\n');

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
