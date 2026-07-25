/**
 * Booking-card logic (pure helpers — no renderer needed).
 * Pins: vehicle preselection honours the proposal but never picks an
 * unavailable type, the displayed fare is the all-in grand_total, and
 * createRide failures map to actionable copy with deep links (active ride,
 * unpaid ride, missing details, generic).
 */
import {
  buildQuoteBookingMessage,
  displayFare,
  mapBookingError,
  paymentMethodForProposal,
  pickEstimate,
  postBookingRoute,
  promoForProposal,
  scheduledDateForProposal,
} from '../bookingProposal';
import type { AiAction, BookingProposal, FareQuoteOption } from '@shared/types/ai';

const PROPOSAL: BookingProposal = {
  pickup_lat: 52.13,
  pickup_lng: -106.66,
  pickup_address: '12 Elm St',
  dropoff_lat: 52.17,
  dropoff_lng: -106.7,
  dropoff_address: 'Airport',
  vehicle_type_id: 'vt-xl',
};

const est = (id: string, overrides: Record<string, unknown> = {}) => ({
  vehicle_type: { id, name: id },
  total_fare: '18.50',
  grand_total: '21.45',
  available: true,
  ...overrides,
});

describe('pickEstimate', () => {
  it('prefers the proposed vehicle type when available', () => {
    const picked = pickEstimate([est('vt-x'), est('vt-xl')], PROPOSAL);
    expect(picked?.vehicle_type.id).toBe('vt-xl');
  });

  it('falls back to the first available type', () => {
    const picked = pickEstimate([est('vt-x'), est('vt-xl', { available: false })], PROPOSAL);
    expect(picked?.vehicle_type.id).toBe('vt-x');
  });

  it('returns null when nothing is available', () => {
    expect(pickEstimate([est('vt-x', { available: false })], PROPOSAL)).toBeNull();
    expect(pickEstimate([], PROPOSAL)).toBeNull();
  });

  it('ignores a proposed type that does not exist', () => {
    const picked = pickEstimate([est('vt-x')], { ...PROPOSAL, vehicle_type_id: 'vt-ghost' });
    expect(picked?.vehicle_type.id).toBe('vt-x');
  });
});

describe('displayFare', () => {
  it('shows the all-in grand_total when present', () => {
    expect(displayFare(est('vt-x'))).toBe('21.45');
  });
  it('falls back to total_fare for legacy estimates', () => {
    expect(displayFare(est('vt-x', { grand_total: undefined }))).toBe('18.50');
  });
});

describe('proposal preferences', () => {
  it('defaults to card unless the AI proposal explicitly says wallet', () => {
    expect(paymentMethodForProposal(PROPOSAL)).toBe('card');
    expect(paymentMethodForProposal({ ...PROPOSAL, payment_method: 'wallet' })).toBe('wallet');
  });

  it('parses valid scheduled times and ignores invalid ones', () => {
    const date = scheduledDateForProposal({ ...PROPOSAL, scheduled_time: '2026-06-12T20:00:00-06:00' });
    expect(date?.toISOString()).toBe('2026-06-13T02:00:00.000Z');
    expect(scheduledDateForProposal({ ...PROPOSAL, scheduled_time: 'not a date' })).toBeNull();
  });

  it('creates a lightweight promo object for rideStore.createRide', () => {
    expect(promoForProposal(PROPOSAL)).toBeNull();
    expect(promoForProposal({ ...PROPOSAL, promo_code: ' SAVE75 ' })).toMatchObject({
      code: 'SAVE75',
      discount_type: 'unknown',
      discount_value: 0,
    });
  });
});

describe('postBookingRoute', () => {
  it('immediate ride hands off to the live tracking screen', () => {
    expect(postBookingRoute('ride-1', null)).toEqual({
      pathname: '/driver-arriving',
      params: { rideId: 'ride-1' },
    });
  });

  it('scheduled ride stays on the chat (no active ride to track)', () => {
    expect(postBookingRoute('ride-1', new Date('2026-06-13T02:00:00Z'))).toBeNull();
  });

  it('never navigates without a ride id', () => {
    expect(postBookingRoute(undefined, null)).toBeNull();
  });
});

describe('mapBookingError', () => {
  it('active ride → view-my-ride link', () => {
    const d = mapBookingError(new Error('A ride is already active'));
    expect(d.message).toContain('already have a ride');
    expect(d.link?.href).toBe('/(tabs)');
  });

  it('unpaid ride → payments link', () => {
    const d = mapBookingError(new Error('Request failed with status code 402'));
    expect(d.message).toContain('unpaid');
  });

  it('missing details → booking-screen handoff', () => {
    const d = mapBookingError(new Error('Missing ride details'));
    expect(d.link?.label).toBe('Open booking');
  });

  it('generic failure reassures nothing was charged', () => {
    const d = mapBookingError(new Error('network down'));
    expect(d.message).toContain('nothing was charged');
    expect(d.link).toBeDefined();
  });
});

describe('buildQuoteBookingMessage', () => {
  const quote: Extract<AiAction, { type: 'fare_quote' }> = {
    type: 'fare_quote',
    quotes: [],
    pickup_address: '4500 Gordon Rd, Regina',
    dropoff_address: '4325 Wakeling St, Regina',
    pickup_lat: 50.4079,
    pickup_lng: -104.6501,
    dropoff_lat: 50.4497,
    dropoff_lng: -104.5345,
  };
  const option: FareQuoteOption = {
    vehicle_type_id: 'vt-1',
    vehicle_type: 'Economy',
    total: '30.92',
    final_total: '20.92',
    promo_code: 'SAVE75',
  };

  it('carries verbatim coordinates, vehicle id, promo and total', () => {
    expect(buildQuoteBookingMessage(quote, option)).toBe(
      'Book the Economy (vehicle id vt-1) from 4500 Gordon Rd, Regina [50.40790,-104.65010] ' +
        'to 4325 Wakeling St, Regina [50.44970,-104.53450] with promo SAVE75, total $20.92.',
    );
  });

  it('falls back to prose when the action has no coordinates (older backend)', () => {
    const bare = { ...quote, pickup_lat: undefined, pickup_lng: undefined, dropoff_lat: undefined, dropoff_lng: undefined };
    expect(buildQuoteBookingMessage(bare, { ...option, promo_code: undefined, final_total: '' })).toBe(
      'Book the Economy (vehicle id vt-1) from 4500 Gordon Rd, Regina to 4325 Wakeling St, Regina.',
    );
  });

  it('sends coordinates even without address strings', () => {
    const coordsOnly = { ...quote, pickup_address: undefined, dropoff_address: undefined };
    expect(buildQuoteBookingMessage(coordsOnly, { ...option, promo_code: undefined })).toBe(
      'Book the Economy (vehicle id vt-1) from [50.40790,-104.65010] to [50.44970,-104.53450], total $20.92.',
    );
  });

  it('degrades to the recommended option with no metadata at all', () => {
    const bare = { type: 'fare_quote', quotes: [] } as Extract<AiAction, { type: 'fare_quote' }>;
    expect(buildQuoteBookingMessage(bare, { total: '', final_total: '' })).toBe(
      'Book the recommended option.',
    );
  });
});
