import { readFileSync } from 'fs';
import { resolve } from 'path';

// CRLF→LF — see onlineResync.test.ts. Windows checkouts are CRLF, so a needle
// spanning a line break would match in CI and fail only on a dev machine.
const hookSource = readFileSync(resolve(__dirname, '..', 'useDriverDashboard.ts'), 'utf8').replace(
  /\r\n/g,
  '\n',
);

describe('foreground durable trip recording', () => {
  it('passes each native foreground fix directly to the durable recorder', () => {
    expect(hookSource).toContain("tripLocationRecorder.recordNativeFix(loc, 'foreground', rideId)");
    expect(hookSource).toContain('tripLocationRecorder.startRide(activeRide.ride.id)');
  });

  it('does not own a capped REST queue or clear samples after repeated upload failures', () => {
    expect(hookSource).not.toContain('LOCATION_BUFFER_KEY');
    expect(hookSource).not.toContain('MAX_LOCATION_RETRIES');
    expect(hookSource).not.toContain('locationBufferRef');
  });

  it('does not replace a native sensor timestamp with a foreground wall-clock timestamp', () => {
    const foregroundSection = hookSource.slice(
      hookSource.indexOf('Location subscription — frequency adapts'),
      hookSource.indexOf('// ─── WebSocket Message Handler'),
    );

    expect(foregroundSection).not.toContain('new Date().toISOString()');
  });
});
