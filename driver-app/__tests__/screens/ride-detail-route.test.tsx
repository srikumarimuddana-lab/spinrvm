import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'app', 'driver', 'ride-detail.tsx'),
  'utf8',
);

describe('driver ride detail route presentation contract', () => {
  it('renders durable actual route segments without a directions fallback', () => {
    expect(source).toContain('toReactNativeSegments');
    expect(source).toContain('actualSegments.map((coordinates, index) => (');
    expect(source).not.toContain('MapViewDirections');
    expect(source).not.toContain('fallbackCoords');
  });

  it('does not present an outdated snapshot as the current actual route', () => {
    expect(source).toContain('snapshot_revision');
    expect(source).toContain('isActualSnapshot');
    expect(source).toContain('Route snapshot unavailable');
  });

  it('keeps a no-GPS route visibly planned or incomplete', () => {
    expect(source).toContain('Planned route');
    expect(source).toContain('routeQualityLabel');
  });
});
