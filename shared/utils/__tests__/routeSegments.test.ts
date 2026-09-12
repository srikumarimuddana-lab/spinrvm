/**
 * Tests for the shared route-quality label — in particular the
 * "estimated from booking" copy shown when GPS was too incomplete to trust,
 * so the rider is never shown a precise-looking distance that isn't measured.
 */
import { routeQualityLabel } from '../routeSegments';

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
