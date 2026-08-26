/**
 * buildShareTripMessage — pins the file's own stated regression: every line
 * must be REAL data or omitted, never a placeholder (the message used to
 * hardcode "4th Avenue North" / "1055 Canada Place"). This text goes to a
 * rider's emergency contacts, so a wrong or placeholder value is a real
 * safety-messaging bug, not cosmetic.
 */
import { buildShareTripMessage } from '../shareTripMessage';

const BASE = {
  estimatedTime: '5:42 PM',
  etaMinutes: 6,
  liveTrackingUrl: 'https://spinr.ca/track/abc123',
};

describe('buildShareTripMessage', () => {
  it('includes every provided real field', () => {
    const msg = buildShareTripMessage({
      ...BASE,
      driverName: 'Alex',
      driverRating: 4.9,
      vehicleColor: 'Blue',
      vehicleMake: 'Toyota',
      vehicleModel: 'Camry',
      licensePlate: '123 ABC',
      dropoffAddress: '123 Main St',
      rideCode: 'R-9F2K',
    });
    expect(msg).toContain('DRIVER: Alex');
    expect(msg).toContain('RATING: 4.9');
    expect(msg).toContain('VEHICLE: Blue Toyota Camry');
    expect(msg).toContain('LICENSE PLATE: 123 ABC');
    expect(msg).toContain('HEADING TO: 123 Main St');
    expect(msg).toContain('ESTIMATED ARRIVAL: 5:42 PM (6 min left)');
    expect(msg).toContain('RIDE CODE: R-9F2K');
    expect(msg).toContain('https://spinr.ca/track/abc123');
  });

  it('never fabricates a destination when dropoffAddress is unknown', () => {
    const msg = buildShareTripMessage({ ...BASE, dropoffAddress: null });
    expect(msg).not.toContain('HEADING TO');
    // Regression guard: the specific hardcoded placeholder this file's
    // header describes must never appear again.
    expect(msg).not.toContain('1055 Canada Place');
    expect(msg).not.toContain('4th Avenue North');
  });

  it('omits the ride-code line when no code is known', () => {
    const msg = buildShareTripMessage({ ...BASE, rideCode: null });
    expect(msg).not.toContain('RIDE CODE');
  });

  it('falls back to honest placeholders (not fabricated data) for missing driver/vehicle fields', () => {
    const msg = buildShareTripMessage({
      ...BASE,
      driverName: null,
      driverRating: null,
      vehicleColor: null,
      vehicleMake: null,
      vehicleModel: null,
      licensePlate: null,
    });
    expect(msg).toContain('DRIVER: Unknown');
    expect(msg).toContain('RATING: New');
    expect(msg).toContain('VEHICLE:  Unknown Vehicle');
    expect(msg).toContain('LICENSE PLATE: Pending');
  });

  it('accepts a string driverRating unchanged', () => {
    const msg = buildShareTripMessage({ ...BASE, driverRating: '5.0' });
    expect(msg).toContain('RATING: 5.0');
  });

  it('always includes the live tracking link, described as authoritative real-time location', () => {
    const msg = buildShareTripMessage({ ...BASE, liveTrackingUrl: 'https://spinr.ca/track/xyz' });
    expect(msg).toContain('LIVE TRACKING LINK:\nhttps://spinr.ca/track/xyz');
  });
});
