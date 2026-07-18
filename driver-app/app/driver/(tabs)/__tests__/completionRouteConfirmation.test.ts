import fs from 'fs';
import path from 'path';

describe('driver completion route confirmation', () => {
  const screenSource = fs.readFileSync(path.resolve(__dirname, '..', 'index.tsx'), 'utf8');

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
