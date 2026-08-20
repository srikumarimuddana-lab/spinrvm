/**
 * Unit tests for lib/androidAuto/carCard.ts — the pure trip-card model that
 * drives the Lyft-style head-unit surface and the ride-offer alert. No native
 * module, no head unit: this asserts the labels a driver reads at a glance.
 */
import {
  bonusLabel,
  buildOfferAlertText,
  buildOfferCard,
  buildTripCard,
  fareBreakdownLabel,
  perKmLabel,
  perkLabel,
  surgeBadge,
  totalEarnings,
  totalEarningsLabel,
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

describe('totalEarnings', () => {
  it('adds a positive bonus to the base fare', () => {
    expect(totalEarnings('14.50', 3)).toBe(17.5);
    expect(totalEarningsLabel('14.50', 3)).toBe('$17.50');
  });

  it('accepts a numeric fare, matching the phone payload shape', () => {
    expect(totalEarningsLabel(14.5, 3)).toBe('$17.50');
  });

  it('is the bare fare when there is no bonus', () => {
    expect(totalEarningsLabel('14.50', 0)).toBe('$14.50');
    expect(totalEarningsLabel('14.50', undefined)).toBe('$14.50');
  });

  it('never lets a negative bonus shrink the advertised total', () => {
    expect(totalEarningsLabel('14.50', -5)).toBe('$14.50');
  });

  it('is null when the fare is missing or unparseable', () => {
    expect(totalEarningsLabel(undefined, 3)).toBeNull();
    expect(totalEarningsLabel('', 3)).toBeNull();
    expect(totalEarningsLabel('n/a', 3)).toBeNull();
  });
});

describe('fareBreakdownLabel', () => {
  it('explains the split only when a bonus makes the hero differ from the fare', () => {
    expect(fareBreakdownLabel('14.50', 3)).toBe('$14.50 fare + $3.00 bonus');
    expect(fareBreakdownLabel('14.50', 0)).toBeNull();
    expect(fareBreakdownLabel('14.50', undefined)).toBeNull();
  });
});

describe('perKmLabel', () => {
  it('rates the ride on total earnings, not the bare fare', () => {
    // 17.50 / 3.5 km — a bonus raises the rate the driver is judging.
    expect(perKmLabel('14.00', 3.5, 3.5)).toBe('$5.00/km');
  });

  it('is null without a usable distance', () => {
    expect(perKmLabel('14.50', 3, 0)).toBeNull();
    expect(perKmLabel('14.50', 3, undefined)).toBeNull();
    expect(perKmLabel('14.50', 3, -2)).toBeNull();
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

  it('leads with fare + bonus, not the bare fare', () => {
    // The regression this guards: the car advertised $14.50 for a ride the
    // phone advertised at $17.50, on the screen where the driver decides.
    const c = buildOfferCard(offer);
    expect(c.totalEarningsLabel).toBe('$17.50');
    expect(c.fareLabel).toBe('$14.50');
    expect(c.fareBreakdownLabel).toBe('$14.50 fare + $3.00 bonus');
    expect(c.perKmLabel).toBe('$5.40/km'); // 17.50 / 3.24
  });

  it('carries both ends of the trip so the offer can show pickup AND dropoff', () => {
    const c = buildOfferCard(offer);
    expect(c.pickupLabel).toBe('101 Pickup St');
    expect(c.dropoffLabel).toBe('202 Dropoff Ave');
  });

  it('reads the badge flags the phone panel shows', () => {
    const c = buildOfferCard({
      ...offer,
      quiet_mode: true,
      is_scheduled: true,
      payment_method: 'cash',
    });
    expect(c.quietMode).toBe(true);
    expect(c.isScheduled).toBe(true);
    expect(c.cashPayment).toBe(true);
  });

  it('leaves every badge off for a payload from a backend that omits them', () => {
    const c = buildOfferCard(offer);
    expect(c.quietMode).toBe(false);
    expect(c.isScheduled).toBe(false);
    expect(c.cashPayment).toBe(false);
  });

  it('degrades gracefully with a null offer', () => {
    const c = buildOfferCard(null);
    expect(c.leg).toBe('offer');
    expect(c.riderName).toBeNull();
    expect(c.fareLabel).toBeNull();
    expect(c.totalEarningsLabel).toBeNull();
    expect(c.fareBreakdownLabel).toBeNull();
    expect(c.perKmLabel).toBeNull();
    expect(c.destinationLabel).toBeNull();
    expect(c.pickupLabel).toBeNull();
    expect(c.dropoffLabel).toBeNull();
    expect(c.wav).toBe(false);
  });
});

describe('buildOfferAlertText', () => {
  const text = (o: OfferLike | null) => buildOfferAlertText(buildOfferCard(o));

  it('leads the title with total earnings, then the rider', () => {
    // The regression this guards: the title used to read "New ride · Alex ★4.9"
    // — the largest text Android Auto gives us, spent on a label — while the
    // amount sat below it in body text, and was the BASE fare at that.
    expect(text(offer).title).toBe('$17.50 · Alex ★4.9');
  });

  it('explains the split and the rate on the first subtitle line', () => {
    const lines = text(offer).subtitle!.split('\n');
    expect(lines[0]).toBe('$14.50 fare + $3.00 bonus · $5.40/km');
  });

  it('puts trip shape and flags on the second line, destination on the third', () => {
    const lines = text(offer).subtitle!.split('\n');
    expect(lines[1]).toBe('8 min away · 3.2 km · 1.5× surge · WAV');
    expect(lines[2]).toBe('→ 202 Dropoff Ave');
  });

  it('carries every badge the phone panel shows', () => {
    const lines = text({
      ...offer,
      quiet_mode: true,
      is_scheduled: true,
      payment_method: 'cash',
    }).subtitle!.split('\n');
    expect(lines[1]).toBe(
      '8 min away · 3.2 km · 1.5× surge · WAV · Quiet ride · Pre-booked · Cash',
    );
  });

  it('omits the rate line entirely when there is no bonus and no distance', () => {
    const t = text({ ride_id: 'r', fare: '11.00', dropoff_address: 'Somewhere' });
    expect(t.title).toBe('$11.00');
    expect(t.subtitle).toBe('→ Somewhere');
  });

  it('shows the bare rate when a ride has distance but no bonus', () => {
    const lines = text({
      ride_id: 'r',
      fare: '9.75',
      distance_km: 2.1,
      rider_name: 'Kiran',
    }).subtitle!.split('\n');
    expect(lines[0]).toBe('$4.64/km');
  });

  it('falls back to a label when the payload carries no money at all', () => {
    expect(text({ ride_id: 'r', rider_name: 'Kiran' }).title).toBe('New ride · Kiran');
    expect(text({ ride_id: 'r' }).title).toBe('New ride request');
    expect(text(null).title).toBe('New ride request');
    expect(text(null).subtitle).toBeNull();
  });

  it('drops the rating from the title when the rider has none', () => {
    expect(text({ ...offer, rider_rating: undefined }).title).toBe('$17.50 · Alex');
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
