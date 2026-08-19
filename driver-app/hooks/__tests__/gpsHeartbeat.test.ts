import { readFileSync } from 'fs';
import { resolve } from 'path';

const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');

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

  it('captures heartbeat fixes WITHOUT an integrity gate (capture-before-filter)', () => {
    // The heartbeat's only output is the durable recorder — no display surface
    // to protect. A false teleport/speed verdict here deleted the one fix that
    // plugs a 30s trail gap (SPR-PE7TTB class loss); server settlement filters
    // own quality. Pin the gate's absence and the rationale comment.
    expect(effect).not.toContain('integrity.trusted');
    expect(effect).toContain('Capture-before-filter: no integrity gate here.');
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
