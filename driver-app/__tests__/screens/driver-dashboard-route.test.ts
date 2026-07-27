import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'app', 'driver', '(tabs)', 'index.tsx'),
  'utf8',
);

describe('driver dashboard route presentation contract', () => {
  it('does not reuse the pickup-to-dropoff route while navigating to pickup', () => {
    expect(source).toContain(
      "const useSavedRoute = hasSavedRoute &&\n            (rideState === 'ride_offered' || rideState === 'trip_in_progress');",
    );
    expect(source).toContain(
      'const needsDirections = GOOGLE_MAPS_API_KEY && !useSavedRoute && !osrmRouteActive;',
    );
  });
});
