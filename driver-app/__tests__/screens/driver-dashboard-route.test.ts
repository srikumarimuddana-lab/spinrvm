import fs from 'fs';
import path from 'path';

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'app', 'driver', '(tabs)', 'index.tsx'),
  'utf8',
);

describe('driver dashboard route presentation contract', () => {
  it('does not reuse the pickup-to-dropoff route while navigating to pickup', () => {
    const savedRouteStateGuard = /(?:canUseSaved|useSavedRoute)\s*=\s*[\s\S]*?rideState === 'ride_offered'[\s\S]*?rideState === 'trip_in_progress'/g;

    // The state-seeding effect, the render path, and (R7,
    // docs/audit/ride-experience/ROADMAP.md) the Directions-proxy attempt's
    // own hoisted duplicate of the render path's derivation must all exclude
    // pre-pickup.
    expect(source.match(savedRouteStateGuard)).toHaveLength(3);
    expect(source).toContain(
      'const needsDirections = GOOGLE_MAPS_API_KEY && !useSavedRoute && !osrmRouteActive &&\n' +
      '            directionsProxyFlagLoaded && (!directionsProxyEnabled || proxyFailedKey === directionsKey);',
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

  // R7 (docs/audit/ride-experience/ROADMAP.md): the backend Directions proxy
  // is tried before the on-device MapViewDirections call when dark-launched
  // on, gated per directionsKey generation so a stale proxy failure doesn't
  // permanently block a later retry as the driver moves.
  describe('Directions proxy (R7)', () => {
    it('imports and calls fetchDirectionsRoute, gated by the dark-launch flag', () => {
      expect(source).toContain("import { fetchDirectionsRoute } from '@shared/api/directions'");
      expect(source).toContain("import { useDirectionsProxyFlag } from '../../../hooks/useDirectionsProxyFlag'");
      expect(source).toContain(
        'const { enabled: directionsProxyEnabled, loaded: directionsProxyFlagLoaded } = useDirectionsProxyFlag();',
      );
      expect(source).toMatch(/if \(!directionsProxyEnabled \|\| !proxyRouteParams\) return;/);
      expect(source).toContain('await fetchDirectionsRoute(proxyRouteParams.origin, proxyRouteParams.destination);');
    });

    it('records a per-directionsKey failure so a later retry (new key) gets a fresh attempt', () => {
      expect(source).toContain('const [proxyFailedKey, setProxyFailedKey] = useState<number | null>(null);');
      expect(source).toMatch(/if \(proxyFailedKey === directionsKey\) return;/);
      // Both the empty-result and the caught-exception branches record the
      // failure against the CURRENT key, not permanently.
      expect(source.match(/setProxyFailedKey\(directionsKey\);/g)).toHaveLength(2);
    });

    it('gates needsDirections so the on-device call never runs while the proxy attempt is live', () => {
      expect(source).toContain(
        'const needsDirections = GOOGLE_MAPS_API_KEY && !useSavedRoute && !osrmRouteActive &&\n' +
        '            directionsProxyFlagLoaded && (!directionsProxyEnabled || proxyFailedKey === directionsKey);',
      );
    });

    it('holds the on-device fallback off until the flag has loaded, closing the relaunch-mid-flow race', () => {
      // Without this, a caller mounted mid-navigating_to_pickup (e.g. app
      // relaunch) would render MapViewDirections on the enabled=false
      // default the instant before the real (possibly true) value arrives,
      // firing both this on-device call and the proxy for the same
      // directionsKey with no way to cancel the on-device one.
      expect(source).toContain('directionsProxyFlagLoaded &&');
    });

    it('applies the same ETA-preference and fit-to-coordinates handling as the on-device onReady', () => {
      // Both paths write the exact same set of state on success -- pinned so
      // a future edit to one can't silently drift from the other (they're
      // deliberately NOT refactored into a shared function, see the source
      // comment, so nothing else enforces this).
      for (const call of [
        'setRouteCoords(result.coordinates)',
        'setDirectionsFailed(false)',
        'if (result.duration != null) setRouteEtaMinutes(Math.round(result.duration));',
        'if (result.distance != null) setRouteDistanceKm(Math.round(result.distance * 10) / 10);',
      ]) {
        expect(source).toContain(call);
      }
    });
  });
});
