/**
 * Turn-by-turn step-progress tracking — Phase 1 of
 * docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md.
 *
 * Pure math, no React/native imports, same discipline as vehicleTracking.ts
 * (which this file reuses distanceMeters/TrackingLatLng from). Given the
 * step list returned by GET /rides/{id}/navigation-steps and the driver's
 * current position, tracks which step is "current" and how far remains to
 * that step's own maneuver point — nothing here talks to the network, a
 * map, or a camera; callers (the banner UI, the follow-camera's approach-
 * zoom override) read trackStepProgress()'s output.
 */

import { distanceMeters, type TrackingLatLng } from './vehicleTracking';

/** One maneuver, shaped exactly like GET /rides/{id}/navigation-steps's response. */
export interface NavigationStep {
  instruction: string;
  /** Google's maneuver enum (e.g. "turn-right", "roundabout-left"), or null
   * for a plain "continue straight" step — see route_distance.py's own doc
   * comment on why null is the correct absence value, not a parse failure. */
  maneuver: string | null;
  distanceMeters: number;
  startLocation: [number, number] | null;
  endLocation: [number, number] | null;
}

export interface StepProgress {
  /** Index into the steps array of the maneuver the driver is currently
   * approaching. */
  stepIndex: number;
  /** Straight-line distance from the current position to that step's own
   * endLocation (its maneuver point) — not a road-distance figure, same
   * simplification snapToRoute's own bearing/deviation math already makes
   * elsewhere in this codebase. */
  distanceToManeuverMeters: number;
  /** The step at stepIndex, for convenience — same object, not a copy. */
  step: NavigationStep;
}

// How close the driver must get to a step's endLocation before that step is
// considered "done" and progress advances to the next one. Deliberately
// generous relative to snapToRoute's MAX_ROUTE_SNAP_M-style thresholds
// elsewhere: a maneuver point is a single GPS coordinate (not a road
// segment to snap onto), and normal GPS noise/GNSS drift right at an
// intersection (buildings, overpasses) is exactly where accuracy is worst —
// too tight a threshold would strand progress on a step the driver has
// actually already completed.
export const STEP_ADVANCE_THRESHOLD_M = 30;

/**
 * Determine which step the driver is currently on and how far remains to
 * its maneuver point.
 *
 * `prevIndex` is the same continuity-hint pattern snapToRoute uses
 * (`preferredFromIndex`): the caller's own last-known step index, so a
 * normal tick starts its search there instead of from the beginning of the
 * route. Advances past any number of already-completed steps in one call
 * (a fast GPS-jittery position update, or a period where the app wasn't
 * polling, can validly skip several short steps at once) but never moves
 * backward — a driver who is nudged slightly behind a maneuver point by GPS
 * noise must not un-advance progress that already correctly moved forward.
 *
 * Returns null only when there are no steps at all (feature-flagged off,
 * fetch failed, or the leg has none) — never throws.
 */
export function trackStepProgress(
  steps: readonly NavigationStep[],
  position: TrackingLatLng,
  prevIndex: number | null,
): StepProgress | null {
  if (steps.length === 0) return null;

  let index =
    prevIndex != null && prevIndex >= 0 && prevIndex < steps.length ? prevIndex : 0;

  while (index < steps.length - 1) {
    const end = steps[index].endLocation;
    if (!end) break; // no maneuver point to compare against — stay put
    const d = distanceMeters(position.latitude, position.longitude, end[0], end[1]);
    if (d > STEP_ADVANCE_THRESHOLD_M) break;
    index += 1;
  }

  const step = steps[index];
  const end = step.endLocation;
  const distanceToManeuverMeters = end
    ? distanceMeters(position.latitude, position.longitude, end[0], end[1])
    : step.distanceMeters; // no coordinate to measure against — fall back to Google's own leg-relative figure

  return { stepIndex: index, distanceToManeuverMeters, step };
}
