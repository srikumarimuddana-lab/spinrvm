import { readFileSync } from 'fs';
import { resolve } from 'path';

// CRLF→LF. This repo checks out CRLF on Windows (core.autocrlf, no .gitattributes),
// so any needle spanning a line break matches in CI and fails locally. Today's
// needles are single-line and safe; this keeps the next one from being a confusing
// Windows-only failure. Full account: onlineResync.test.ts.
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8').replace(
  /\r\n/g,
  '\n',
);

describe('app-state transition outbox flush', () => {
  it('force-flushes the durable outbox on foreground/background transitions during a trip', () => {
    const effect = hookSource.slice(
      hookSource.indexOf('// Flush the durable outbox on app-state transitions during a trip.'),
      hookSource.indexOf('// Location subscription — frequency adapts'),
    );
    expect(effect).toContain("TRACKED_TRIP_PHASES.includes(rideState)");
    expect(effect).toContain("AppState.addEventListener('change'");
    expect(effect).toContain("next === 'active' || next === 'background'");
    expect(effect).toContain('flushPending(foregroundLocationTransport, { force: true })');
    // Failures must never throw out of the listener — points stay in SQLite.
    expect(effect).toContain('.catch(() => {');
  });

  it('keeps a single trip-phase source of truth shared with the watcher effect', () => {
    expect(hookSource).toContain(
      "const TRACKED_TRIP_PHASES = ['navigating_to_pickup', 'arrived_at_pickup', 'trip_in_progress']",
    );
    expect(hookSource).toContain('const inTripPhase = TRACKED_TRIP_PHASES.includes(rideState)');
  });
});
