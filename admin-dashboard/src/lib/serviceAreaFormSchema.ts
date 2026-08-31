import { z } from "zod";

/**
 * dashboard/service-areas/page.tsx's `handleCreate` and
 * `handleCreateAirportSubRegion` -- validation extracted from two inline
 * checks per ACTION_ITEMS.md B39. Both gate a `createServiceArea` call
 * (a new geofenced dispatch area, or an airport sub-region within one),
 * so a validation gap here could create a malformed or unnamed area.
 */

export const serviceAreaNameSchema = z.string().min(1);

/**
 * Mirrors `handleCreate`'s `!createForm.name` guard -- the silent
 * early-return before creating a new top-level service area.
 */
export function isServiceAreaNameValid(name: string): boolean {
  return serviceAreaNameSchema.safeParse(name).success;
}

export const airportZoneNameSchema = z.string().min(1);

/** Minimum polygon points to define a boundary (a valid polygon needs at least 3). */
export const MIN_AIRPORT_ZONE_POLYGON_POINTS = 3;

/**
 * Mirrors `handleCreateAirportSubRegion`'s
 * `!airportForm.name || airportForm.polygon.length < 3` guard -- the
 * "Missing airport boundary" toast's condition.
 */
export function isAirportZoneValid(name: string, polygonPointCount: number): boolean {
  return (
    airportZoneNameSchema.safeParse(name).success &&
    polygonPointCount >= MIN_AIRPORT_ZONE_POLYGON_POINTS
  );
}
