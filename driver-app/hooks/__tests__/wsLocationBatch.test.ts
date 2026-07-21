import { readFileSync } from 'fs';
import { resolve } from 'path';

const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8');

describe('WebSocket live location markers', () => {
  it('sends an ephemeral live marker only after durable recorder enqueueing', () => {
    const recorderCall = hookSource.indexOf("tripLocationRecorder.recordNativeFix(loc, 'foreground', rideId)");
    const liveMarker = hookSource.indexOf("type: 'driver_location',");
    const livePayload = hookSource.slice(liveMarker, hookSource.indexOf('}));', liveMarker));

    expect(recorderCall).toBeGreaterThan(-1);
    expect(liveMarker).toBeGreaterThan(recorderCall);
    expect(livePayload).toContain('durable: !batchUploadHealthyRef.current');
    expect(livePayload).toContain('captured_at: point.captured_at');
    expect(livePayload).not.toContain('recording_session_id:');
    expect(livePayload).not.toContain('sequence_number:');
  });

  it('does not retain an in-memory WebSocket batch while the socket is unavailable', () => {
    expect(hookSource).not.toContain('wsBatchRef');
    expect(hookSource).not.toContain("type: 'driver_location_batch'");
  });
});
