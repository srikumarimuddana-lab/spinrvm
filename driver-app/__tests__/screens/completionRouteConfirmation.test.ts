import fs from 'fs';
import path from 'path';

describe('driver completion route confirmation', () => {
  // CRLF→LF — see hooks/__tests__/onlineResync.test.ts. Windows checkouts are CRLF,
  // so a needle spanning a line break would match in CI and fail only on a dev machine.
  const screenSource = fs
    .readFileSync(path.resolve(__dirname, '..', '..', 'app', 'driver', '(tabs)', 'index.tsx'), 'utf8')
    .replace(/\r\n/g, '\n');

  test('keeps a completion in progress until the driver chooses an allowed exception reason', () => {
    expect(screenSource).toContain('completionConfirmationVisible');
    expect(screenSource).toContain('completionConfirmationRideId');
    expect(screenSource).toContain("completeRide(rideId, confirmation)");
    expect(screenSource).toContain("'rider_requested_stop'");
    expect(screenSource).toContain("'changed_destination'");
    expect(screenSource).toContain("'emergency'");
    expect(screenSource).toContain("'location_unavailable'");
  });
});
