import { readFileSync } from 'fs';
import { resolve } from 'path';

const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');

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
