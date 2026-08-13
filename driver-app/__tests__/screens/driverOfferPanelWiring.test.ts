import fs from 'fs';
import path from 'path';

// The dashboard route (`app/driver/(tabs)/index.tsx`) pulls in
// react-native-maps, react-native-maps-directions and the real
// useDriverDashboard GPS/WS hook, none of which are mockable cheaply enough
// to justify a full `render()` here — this file follows the existing
// source-contract convention used by
// `__tests__/screens/driver-dashboard-route.test.ts` and
// `hooks/__tests__/appStateFlush.test.ts` for the same reason: assert on the
// exact wiring rather than mounting the whole screen. Behavioral coverage of
// what the isLoading wire *produces* (buttons actually disabling) lives one
// layer down, in `__tests__/components/RideOfferPanel.test.tsx`.
const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'app', 'driver', '(tabs)', 'index.tsx'),
  'utf8',
);

describe('ride offer panel wiring (double-tap + stale-countdown fixes)', () => {
  it('wires the real store isLoading into RideOfferPanel instead of a hardcoded false', () => {
    // Bug A regression guard: `isLoading={false}` hard-coded here disabled
    // the panel's own `disabled={isLoading}` double-tap guard entirely.
    expect(source).not.toMatch(/<RideOfferPanel[\s\S]*?isLoading=\{false\}/);
    expect(source).toMatch(/<RideOfferPanel[\s\S]*?isLoading=\{isLoading\}/);
    // Pulled off the real ride store, not a local screen-only flag.
    expect(source).toMatch(/const\s*\{[\s\S]*?\bisLoading\b[\s\S]*?\}\s*=\s*useDriverStore\(\);/);
  });

  it('recomputes the offer countdown from offer_expires_at on AppState → active', () => {
    const effectStart = source.indexOf('// Resync the offer countdown against wall-clock time');
    expect(effectStart).toBeGreaterThan(-1);
    const effect = source.slice(effectStart, source.indexOf('// Clear route + ETA when ride state changes'));

    // Listens for the foreground transition specifically (not background/inactive).
    expect(effect).toContain("AppState.addEventListener('change'");
    expect(effect).toContain("if (next !== 'active') return;");

    // Reads live store state at fire time (not a stale render-time closure),
    // matching the convention used elsewhere in this file (see the error
    // effect a few lines up, which also reads useDriverStore.getState()).
    expect(effect).toContain('useDriverStore.getState()');

    // Only recomputes while an offer is actually showing, and only when the
    // server gave us an expiry to diff against.
    expect(effect).toContain("curRideState !== 'ride_offered'");
    expect(effect).toContain('!curIncomingRide?.offer_expires_at');

    // Wall-clock diff — not a re-seed to the configured max, and not a
    // continuation of the possibly-stalled interval's local counter.
    expect(effect).toMatch(
      /Math\.floor\(\(new Date\(curIncomingRide\.offer_expires_at\)\.getTime\(\)\s*-\s*Date\.now\(\)\)\s*\/\s*1000\)/,
    );
    // Never displays a negative countdown.
    expect(effect).toContain('Math.max(');

    // Drives both the local tick display and (only once truly expired) the
    // store, so the store's own auto-decline-on-expiry watcher still fires.
    expect(effect).toContain('setCountdownState(remaining)');
    expect(effect).toMatch(/if \(remaining <= 0\) \{\s*setCountdown\(0\);/);
  });

  it('keeps the normal per-second interval as the steady-state ticker (this is a resync, not a replacement)', () => {
    // The existing interval-based countdown effect must still be present and
    // still be the thing driving the once-a-second UI tick.
    expect(source).toContain("setInterval(() => {");
    expect(source).toMatch(/setCountdownState\(\(prev\) => \{/);
  });
});
