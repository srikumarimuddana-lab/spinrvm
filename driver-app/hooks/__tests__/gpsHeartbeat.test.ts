import { readFileSync } from 'fs';
import { resolve } from 'path';

// CRLF→LF — see onlineResync.test.ts. Windows checkouts are CRLF, so a needle
// spanning a line break would match in CI and fail only on a dev machine.
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8').replace(
  /\r\n/g,
  '\n',
);

describe('trip-phase GPS heartbeat', () => {
  const effect = hookSource.slice(
    hookSource.indexOf('// GPS heartbeat: during a trip'),
    hookSource.indexOf('// Flush the durable outbox on app-state transitions during a trip.'),
  );

  it('actively requests a fix only when the recorder reports no recent fix', () => {
    expect(effect).toContain("health.degradationReason !== 'no_recent_fix'");
    expect(effect).toContain('Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High })');
  });

  it('runs only during tracked trip phases while online, on a 30s cadence', () => {
    expect(effect).toContain('TRACKED_TRIP_PHASES.includes(rideState)');
    expect(effect).toContain('30_000');
  });

  it('gates heartbeat fixes through the same integrity filter as the watcher', () => {
    expect(effect).toContain('checkLocationIntegrity(loc)');
    expect(effect).toContain('if (!integrity.trusted) return;');
  });

  it('surfaces device-wide location services going off mid-trip', () => {
    expect(effect).toContain('Location.hasServicesEnabledAsync()');
    expect(effect).toContain("setLocationStatus('unavailable')");
  });

  it('records through the durable recorder and flushes best-effort', () => {
    expect(effect).toContain("tripLocationRecorder.recordNativeFix(loc, 'foreground', rideId)");
    expect(effect).toContain('flushPending(foregroundLocationTransport)');
  });
});
