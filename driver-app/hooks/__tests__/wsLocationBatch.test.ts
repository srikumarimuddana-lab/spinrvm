import { readFileSync } from 'fs';
import { resolve } from 'path';

const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');

describe('WebSocket live location markers', () => {
  it('sends an ephemeral live marker only after durable recorder enqueueing', () => {
    const recorderCall = hookSource.indexOf("tripLocationRecorder.recordNativeFix(loc, 'foreground', rideId)");
    const liveMarker = hookSource.indexOf("type: 'driver_location',");

    expect(recorderCall).toBeGreaterThan(-1);
    expect(liveMarker).toBeGreaterThan(recorderCall);
    expect(hookSource).toContain('durable: false');
  });

  it('does not retain an in-memory WebSocket batch while the socket is unavailable', () => {
    expect(hookSource).not.toContain('wsBatchRef');
    expect(hookSource).not.toContain("type: 'driver_location_batch'");
  });
});
