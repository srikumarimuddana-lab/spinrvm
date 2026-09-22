/**
 * Tests for the shared route-quality label — in particular the
 * "estimated from booking" copy shown when GPS was too incomplete to trust,
 * so the rider is never shown a precise-looking distance that isn't measured.
 */
import { normalizeDecodedPolyline, routeQualityLabel } from '../routeSegments';

describe('routeQualityLabel', () => {
  it('shows the estimated-from-booking copy for planned_estimated basis', () => {
    expect(routeQualityLabel({ distance_basis: 'planned_estimated' })).toBe(
      'Distance estimated from booking · GPS incomplete',
    );
  });

  it('estimated basis wins even when ratios are present', () => {
    // A broken trace can still carry observed/inferred ratios; the estimated
    // basis must take precedence so we do not imply a measured figure.
    expect(
      routeQualityLabel({
        distance_basis: 'planned_estimated',
        observed_distance_ratio: 0.4,
        inferred_distance_ratio: 0.6,
      }),
    ).toBe('Distance estimated from booking · GPS incomplete');
  });

  it('shows the from-booking copy for planned_capped, over the discarded reconstruction ratios', () => {
    // SPR-EG7X86: the finalizer capped a 15.1 km reconstruction to the 9.21 km
    // booking; route_quality still carries the reconstruction's 74/26 split.
    expect(
      routeQualityLabel({
        distance_basis: 'planned_capped',
        observed_distance_ratio: 0.742,
        inferred_distance_ratio: 0.258,
      }),
    ).toBe('Distance from booking · GPS route implausible');
  });

  it('shows the from-booking copy for planned_guess_deviation, not the rejected ratios', () => {
    // Ride 0c24901f: a 389 m GPS hole was bridged with 2.33 km of routed
    // gap fill, putting the reconstruction at 8.96 km against a 6.99 km
    // booking. The finalizer published the booking. Without a branch here
    // this fell through to the ratio copy and captioned that booked figure
    // 'Route reconstructed · 74% GPS observed · 26% inferred' — percentages
    // belonging to the 8.96 km reconstruction that was thrown away.
    expect(
      routeQualityLabel({
        distance_basis: 'planned_guess_deviation',
        observed_distance_ratio: 0.74,
        inferred_distance_ratio: 0.26,
      }),
    ).toBe('Distance from booking · GPS route partly inferred');
  });

  it('does not alter observed/reconstructed labels', () => {
    expect(
      routeQualityLabel({ observed_distance_ratio: 0.87, inferred_distance_ratio: 0.13 }),
    ).toBe('Route reconstructed · 87% GPS observed · 13% inferred');
    expect(routeQualityLabel({ observed_distance_ratio: 1, inferred_distance_ratio: 0 })).toBe(
      'Route verified · 100% GPS observed',
    );
  });

  it('other bases do not trigger the estimated label', () => {
    expect(routeQualityLabel({ distance_basis: 'observed', coverage_ratio: 1 })).toBe(
      'Route verified · 100% GPS coverage',
    );
  });

  it('reconstruction status still takes top precedence', () => {
    expect(
      routeQualityLabel({ reconstruction_status: 'retrying', distance_basis: 'planned_estimated' }),
    ).toBe('Route reconstruction in progress');
  });
});

describe('normalizeDecodedPolyline', () => {
  it('keeps contract [[lat, lng], …] arrays', () => {
    expect(normalizeDecodedPolyline([[50.45, -104.62], [50.46, -104.61]])).toEqual([
      { lat: 50.45, lng: -104.62 },
      { lat: 50.46, lng: -104.61 },
    ]);
  });

  it('accepts legacy {lat, lng} objects so a route still draws', () => {
    expect(normalizeDecodedPolyline([
      { lat: 50.45, lng: -104.62 },
      { lat: 50.46, lng: -104.61 },
    ])).toEqual([
      { lat: 50.45, lng: -104.62 },
      { lat: 50.46, lng: -104.61 },
    ]);
  });

  it('swaps GeoJSON [lng, lat] when the first number cannot be a latitude', () => {
    expect(normalizeDecodedPolyline([[-104.62, 50.45], [-104.61, 50.46]])).toEqual([
      { lat: 50.45, lng: -104.62 },
      { lat: 50.46, lng: -104.61 },
    ]);
  });

  it('coerces numeric strings', () => {
    expect(normalizeDecodedPolyline([['50.45', '-104.62'], ['50.46', '-104.61']])).toEqual([
      { lat: 50.45, lng: -104.62 },
      { lat: 50.46, lng: -104.61 },
    ]);
  });
});
