/**
 * Unit tests for lib/androidAuto/carCard.ts — the pure trip-card model that
 * drives the Lyft-style head-unit surface and the ride-offer alert. No native
 * module, no head unit: this asserts the labels a driver reads at a glance.
 */
import {
  bonusLabel,
  buildOfferCard,
  buildTripCard,
  perkLabel,
  surgeBadge,
  type OfferLike,
} from '../carCard';
import type { ActiveRide } from '../../../store/driverStore';

const offer: OfferLike = {
  ride_id: 'r1',
  pickup_address: '101 Pickup St',
  dropoff_address: '202 Dropoff Ave',
  fare: '14.50',
  distance_km: 3.24,
  duration_minutes: 7.6,
  rider_name: 'Alex Morgan',
  rider_rating: 4.92,
  rider_profile_image: 'https://cdn.spinr.ca/r/alex.jpg',
  requires_wav: true,
  surge_multiplier: 1.5,
  total_bonus: 3,
  incentives: [{ name: 'Weekend boost', bonus_amount: 3, incentive_type: 'per_ride' }],
  quest_hint: { title: '3 of 5 rides — $20 bonus' },
};

const activeRide = (overrides: Partial<ActiveRide['ride']> = {}): ActiveRide =>
  ({
    ride: {
      id: 'r1',
      status: 'driver_accepted',
      pickup_address: '101 Pickup St',
      dropoff_address: '202 Dropoff Ave',
      pickup_lat: 52.13,
      pickup_lng: -106.67,
      dropoff_lat: 52.2,
      dropoff_lng: -106.6,
      total_fare: '14.50',
      distance_km: 3.24,
      duration_minutes: 7.6,
      rider_id: 'rider',
      created_at: '2026-06-16T00:00:00Z',
      ...overrides,
    },
    rider: { id: 'rider', first_name: 'Alex', rating: 4.92, profile_image: 'https://cdn.spinr.ca/r/alex.jpg' },
    vehicle_type: { id: 'vt', name: 'Standard' },
    total_bonus: 3,
    quest_hint: {
      title: '3 of 5 rides — $20 bonus',
      current_value: 3,
      target_value: 5,
      progress_pct: 60,
      reward_amount: 20,
    },
  }) as unknown as ActiveRide;

describe('surgeBadge', () => {
  it('hides the badge at or below the 1.0 baseline', () => {
    expect(surgeBadge(1.0)).toBeNull();
    expect(surgeBadge(0.8)).toBeNull();
    expect(surgeBadge(undefined)).toBeNull();
  });
  it('trims trailing zeros', () => {
    expect(surgeBadge(1.5)).toBe('1.5×');
    expect(surgeBadge(1.75)).toBe('1.75×');
    expect(surgeBadge(2.0)).toBe('2×');
  });
});

describe('bonusLabel', () => {
  it('shows only a positive bonus', () => {
    expect(bonusLabel(3)).toBe('+$3.00 bonus');
    expect(bonusLabel(0)).toBeNull();
    expect(bonusLabel(undefined)).toBeNull();
  });
});

describe('perkLabel', () => {
  it('prefers a quest title, then the top incentive', () => {
    expect(perkLabel([{ name: 'Boost' }], { title: 'Quest!' })).toBe('Quest!');
    expect(perkLabel([{ name: 'Boost' }], null)).toBe('Boost');
    expect(perkLabel([], null)).toBeNull();
  });
});

describe('buildOfferCard', () => {
  it('formats the fresh-offer card from the dispatch payload', () => {
    const c = buildOfferCard(offer);
    expect(c.leg).toBe('offer');
    expect(c.statusLabel).toBe('New ride request');
    expect(c.riderName).toBe('Alex'); // first token only
    expect(c.riderRating).toBe('4.9');
    expect(c.riderPhoto).toBe('https://cdn.spinr.ca/r/alex.jpg');
    expect(c.destinationLabel).toBe('101 Pickup St');
    expect(c.destinationCaption).toBe('Pick-up');
    expect(c.etaLabel).toBe('8 min'); // 7.6 → rounded
    expect(c.distanceLabel).toBe('3.2 km');
    expect(c.fareLabel).toBe('$14.50');
    expect(c.surgeLabel).toBe('1.5×');
    expect(c.wav).toBe(true);
    expect(c.bonusLabel).toBe('+$3.00 bonus');
    expect(c.perkLabel).toBe('3 of 5 rides — $20 bonus');
  });

  it('degrades gracefully with a null offer', () => {
    const c = buildOfferCard(null);
    expect(c.leg).toBe('offer');
    expect(c.riderName).toBeNull();
    expect(c.fareLabel).toBeNull();
    expect(c.destinationLabel).toBeNull();
    expect(c.wav).toBe(false);
  });
});

describe('buildTripCard', () => {
  it('prefers the live offer payload for ride_offered', () => {
    const c = buildTripCard('ride_offered', null, offer);
    expect(c.leg).toBe('offer');
    expect(c.fareLabel).toBe('$14.50');
    expect(c.riderName).toBe('Alex');
  });

  it('falls back to ActiveRide when no push offer is present', () => {
    const c = buildTripCard('ride_offered', activeRide(), null);
    expect(c.leg).toBe('offer');
    expect(c.destinationLabel).toBe('101 Pickup St');
    expect(c.fareLabel).toBe('$14.50');
  });

  it('points at the pickup while navigating to pickup', () => {
    const c = buildTripCard('navigating_to_pickup', activeRide());
    expect(c.leg).toBe('pickup');
    expect(c.statusLabel).toBe('Heading to pickup');
    expect(c.destinationLabel).toBe('101 Pickup St');
    expect(c.etaLabel).toBe('8 min');
    expect(c.hint).toBeNull();
    // rich fields threaded from ActiveRide (photo + bonus + quest)
    expect(c.riderPhoto).toBe('https://cdn.spinr.ca/r/alex.jpg');
    expect(c.bonusLabel).toBe('+$3.00 bonus');
    expect(c.perkLabel).toBe('3 of 5 rides — $20 bonus');
  });

  it('shows the phone-only PIN hint and no ETA at pickup', () => {
    const c = buildTripCard('arrived_at_pickup', activeRide());
    expect(c.statusLabel).toBe('Arrived at pickup');
    expect(c.etaLabel).toBeNull();
    expect(c.hint).toContain('phone');
  });

  it('points at the dropoff during the trip', () => {
    const c = buildTripCard('trip_in_progress', activeRide());
    expect(c.leg).toBe('dropoff');
    expect(c.destinationLabel).toBe('202 Dropoff Ave');
    expect(c.statusLabel).toBe('Trip in progress');
  });

  it('renders a completion card with a phone hand-off hint', () => {
    const c = buildTripCard('trip_completed', activeRide());
    expect(c.leg).toBe('complete');
    expect(c.destinationLabel).toBeNull();
    expect(c.hint).toContain('phone');
  });

  it('renders a neutral idle card with no ride data', () => {
    const c = buildTripCard('idle', null);
    expect(c.leg).toBe('idle');
    expect(c.riderName).toBeNull();
    expect(c.destinationLabel).toBeNull();
    expect(c.fareLabel).toBeNull();
  });

  it('omits surge below baseline and keeps it above', () => {
    expect(buildTripCard('trip_in_progress', activeRide({ surge_multiplier: 1.0 })).surgeLabel).toBeNull();
    expect(buildTripCard('trip_in_progress', activeRide({ surge_multiplier: 1.75 })).surgeLabel).toBe('1.75×');
  });
});
