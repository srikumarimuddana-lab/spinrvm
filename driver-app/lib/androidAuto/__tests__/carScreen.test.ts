/**
 * Unit tests for the Android Auto car-screen model builder.
 *
 * buildCarScreen is pure (no native modules), so no mocks are required.
 * Covers every RideState branch plus the missing-data fallbacks the head
 * unit relies on (it rejects empty / >4-row panes and blank strings).
 */
import { buildCarScreen } from '../carScreen';
import type { ActiveRide } from '../../../store/driverStore';

const ride = (over: Partial<ActiveRide['ride']> = {}): ActiveRide =>
  ({
    ride: {
      id: 'r1',
      status: 'driver_accepted',
      pickup_address: '123 Pickup St, Regina',
      dropoff_address: '456 Dropoff Ave, Regina',
      pickup_lat: 0,
      pickup_lng: 0,
      dropoff_lat: 0,
      dropoff_lng: 0,
      total_fare: '24.50',
      distance_km: 8,
      duration_minutes: 15,
      rider_id: 'rider1',
      created_at: '',
      ...over,
    },
    rider: { id: 'rider1', first_name: 'Alex' },
    vehicle_type: { id: 'v1', name: 'Standard' },
  }) as ActiveRide;

describe('buildCarScreen', () => {
  it('always returns between 1 and 4 rows for every ride state', () => {
    const states = [
      'idle',
      'ride_offered',
      'navigating_to_pickup',
      'arrived_at_pickup',
      'trip_in_progress',
      'trip_completed',
    ] as const;
    for (const s of states) {
      const model = buildCarScreen(s, ride());
      expect(model.items.length).toBeGreaterThanOrEqual(1);
      expect(model.items.length).toBeLessThanOrEqual(4);
      expect(model.title.length).toBeGreaterThan(0);
      // No blank primary text — the host rejects empty rows.
      model.items.forEach((row) => expect(row.text.trim().length).toBeGreaterThan(0));
    }
  });

  it('idle shows a no-active-ride prompt even with a stale ride object', () => {
    const model = buildCarScreen('idle', ride());
    expect(model.title).toBe('Spinr Driver');
    expect(model.items[0].text).toMatch(/no active ride/i);
  });

  it('surfaces rider name and pickup address while heading to pickup', () => {
    const model = buildCarScreen('navigating_to_pickup', ride());
    expect(model.items[0].text).toContain('Alex');
    expect(model.items[0].detailText).toBe('123 Pickup St, Regina');
    expect(model.items[1].detailText).toBe('456 Dropoff Ave, Regina');
  });

  it('focuses on the drop-off once the trip is in progress', () => {
    const model = buildCarScreen('trip_in_progress', ride());
    expect(model.title).toBe('Trip in progress');
    expect(model.items).toHaveLength(1);
    expect(model.items[0].detailText).toBe('456 Dropoff Ave, Regina');
  });

  it('falls back gracefully when rider name / addresses are missing', () => {
    const blank = ride({ pickup_address: '', dropoff_address: '   ' });
    blank.rider = { id: 'rider1' };
    const model = buildCarScreen('navigating_to_pickup', blank);
    expect(model.items[0].text).toContain('your rider');
    expect(model.items[0].detailText).toBe('Pickup location');
    expect(model.items[1].detailText).toBe('Drop-off location');
  });

  it('handles a null active ride without throwing', () => {
    expect(() => buildCarScreen('navigating_to_pickup', null)).not.toThrow();
    const model = buildCarScreen('navigating_to_pickup', null);
    expect(model.items[0].detailText).toBe('Pickup location');
  });
});
