/**
 * Tests for shared/utils/navigationSteps — turn-by-turn step-progress
 * tracking (Phase 1 of
 * docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md).
 *
 * Lives in driver-app/__tests__ (not shared/utils/__tests__) — same reason
 * as rider-app/__tests__/vehicleTracking.test.ts: no CI jest run collects
 * tests from the shared/ package directly.
 */
import {
  STEP_ADVANCE_THRESHOLD_M,
  trackStepProgress,
  type NavigationStep,
} from '@shared/utils/navigationSteps';

// ~11 m per 1e-4 degree latitude near Regina, SK.
const LAT0 = 50.4452;
const LNG0 = -104.6189;
const north = (m: number) => LAT0 + (m / 111_320);

function step(
  instruction: string,
  endMetersNorth: number,
  overrides: Partial<NavigationStep> = {},
): NavigationStep {
  return {
    instruction,
    maneuver: null,
    distanceMeters: 100,
    startLocation: null,
    endLocation: [north(endMetersNorth), LNG0],
    ...overrides,
  };
}

describe('trackStepProgress', () => {
  it('returns null for an empty step list', () => {
    expect(trackStepProgress([], { latitude: LAT0, longitude: LNG0 }, null)).toBeNull();
  });

  it('starts at step 0 with no continuity hint', () => {
    const steps = [step('Head north', 200), step('Turn right', 400)];
    const result = trackStepProgress(steps, { latitude: LAT0, longitude: LNG0 }, null);
    expect(result?.stepIndex).toBe(0);
    expect(result?.step).toBe(steps[0]);
    expect(result?.distanceToManeuverMeters).toBeCloseTo(200, 0);
  });

  it('reports decreasing distance as the driver approaches the maneuver point', () => {
    const steps = [step('Head north', 200), step('Turn right', 400)];
    // Both positions stay outside STEP_ADVANCE_THRESHOLD_M(30) of the 200m
    // maneuver point (150m and 50m short, respectively), so both remain on
    // step 0 — this test is purely about the distance figure decreasing.
    const far = trackStepProgress(steps, { latitude: north(50), longitude: LNG0 }, 0);
    const near = trackStepProgress(steps, { latitude: north(150), longitude: LNG0 }, 0);
    expect(far?.stepIndex).toBe(0);
    expect(near?.stepIndex).toBe(0);
    expect(near!.distanceToManeuverMeters).toBeLessThan(far!.distanceToManeuverMeters);
  });

  it('advances to the next step once within STEP_ADVANCE_THRESHOLD_M of the maneuver point', () => {
    const steps = [step('Head north', 200), step('Turn right', 400)];
    // 10m short of the first maneuver point — well inside the threshold.
    const pos = { latitude: north(200 - 10), longitude: LNG0 };
    const result = trackStepProgress(steps, pos, 0);
    expect(result?.stepIndex).toBe(1);
    expect(result?.step).toBe(steps[1]);
  });

  it('does not advance while still outside the threshold', () => {
    const steps = [step('Head north', 200), step('Turn right', 400)];
    const pos = { latitude: north(200 - (STEP_ADVANCE_THRESHOLD_M + 20)), longitude: LNG0 };
    const result = trackStepProgress(steps, pos, 0);
    expect(result?.stepIndex).toBe(0);
  });

  it('can skip multiple completed short steps in one call', () => {
    const steps = [
      step('Head north', 50),
      step('Turn right', 60), // a very short step right after the first
      step('Continue', 400),
    ];
    // Already past both of the first two maneuver points.
    const pos = { latitude: north(60), longitude: LNG0 };
    const result = trackStepProgress(steps, pos, 0);
    expect(result?.stepIndex).toBe(2);
  });

  it('never advances past the final step', () => {
    const steps = [step('Turn right', 100)];
    const pos = { latitude: north(1000), longitude: LNG0 }; // way past the only maneuver point
    const result = trackStepProgress(steps, pos, 0);
    expect(result?.stepIndex).toBe(0);
    expect(result?.step).toBe(steps[0]);
  });

  it('does not move backward when GPS noise reports a position slightly behind an already-advanced step', () => {
    const steps = [step('Head north', 200), step('Turn right', 400), step('Continue', 600)];
    // Continuity hint says we're already on step 2 (index 2) — a noisy fix
    // landing near step 1's maneuver point must not un-advance progress.
    const noisyPos = { latitude: north(395), longitude: LNG0 };
    const result = trackStepProgress(steps, noisyPos, 2);
    expect(result?.stepIndex).toBe(2);
  });

  it('clamps an out-of-range continuity hint back to a valid index instead of throwing', () => {
    const steps = [step('Head north', 200)];
    expect(() =>
      trackStepProgress(steps, { latitude: LAT0, longitude: LNG0 }, 99),
    ).not.toThrow();
    const result = trackStepProgress(steps, { latitude: LAT0, longitude: LNG0 }, 99);
    expect(result?.stepIndex).toBe(0);
  });

  it('falls back to the step\'s own distanceMeters when endLocation is missing', () => {
    const steps: NavigationStep[] = [
      { instruction: 'Continue', maneuver: null, distanceMeters: 75, startLocation: null, endLocation: null },
    ];
    const result = trackStepProgress(steps, { latitude: LAT0, longitude: LNG0 }, null);
    expect(result?.distanceToManeuverMeters).toBe(75);
    expect(result?.stepIndex).toBe(0);
  });
});
