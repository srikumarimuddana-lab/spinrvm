/**
 * Booking-card logic (pure helpers — no renderer needed).
 * Pins: vehicle preselection honours the proposal but never picks an
 * unavailable type, the displayed fare is the all-in grand_total, and
 * createRide failures map to actionable copy with deep links (active ride,
 * unpaid ride, missing details, generic).
 */
import { displayFare, mapBookingError, pickEstimate } from '../bookingProposal';
import type { BookingProposal } from '@shared/types/ai';

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
