/**
 * The trip-share safety message must never contain fabricated data.
 *
 * Regression: the message used to hardcode "4th Avenue North" as the
 * rider's current location and fall back to "1055 Canada Place" (a
 * Vancouver address) as the destination — fake data in a message riders
 * send to emergency contacts.
 */

import { buildShareTripMessage } from '../lib/shareTripMessage';

const baseArgs = {
  driverName: 'Sam Driver',
  driverRating: 4.9,
  vehicleColor: 'Blue',
  vehicleMake: 'Toyota',
  vehicleModel: 'Corolla',
  licensePlate: 'ABC 123',
  estimatedTime: '3:42 PM',
  etaMinutes: 12,
  rideCode: 'SPNR-42',
  liveTrackingUrl: 'https://track.spinr.ca/tok123',
};

describe('buildShareTripMessage', () => {
  it('includes the real dropoff address when known', () => {
    const msg = buildShareTripMessage({ ...baseArgs, dropoffAddress: '123 Main St, Saskatoon' });
    expect(msg).toContain('📍 HEADING TO: 123 Main St, Saskatoon');
    expect(msg).toContain('https://track.spinr.ca/tok123');
    expect(msg).toContain('🆔 RIDE CODE: SPNR-42');
  });

  it('omits the destination line entirely when the dropoff is unknown — no fake fallback', () => {
    const msg = buildShareTripMessage({ ...baseArgs, dropoffAddress: null });
    expect(msg).not.toContain('HEADING TO');
    expect(msg).not.toContain('1055 Canada Place');
  });

  it('never contains a fabricated current-location line', () => {
    const msg = buildShareTripMessage({ ...baseArgs, dropoffAddress: '123 Main St' });
    expect(msg).not.toContain('CURRENT LOCATION');
    expect(msg).not.toContain('4th Avenue North');
  });

  it('omits the ride-code line when absent', () => {
    const msg = buildShareTripMessage({ ...baseArgs, rideCode: null });
    expect(msg).not.toContain('RIDE CODE');
  });
});
