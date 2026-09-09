import { useMemo } from 'react';
import { distanceMeters } from '@shared/utils/vehicleTracking';
import {
  HEAT_BLOB_RADIUS_FACTOR,
  METERS_PER_LAT_DEG,
  SOFT_HEAT_RENDER_ENABLED,
  cellCenter,
} from '../lib/heatFalloff';
import type { HeatmapCell } from './useDemandHeatmap';

// Fallbacks only. The server sends the grid size it actually bucketed with
// (cell_lat_deg / cell_lng_deg) because that size is tunable per service area;
// these values are what an older backend that omits them used, so an app on a
// new build talking to an old backend keeps its previous behaviour exactly.
const DEFAULT_CELL_LAT = 0.004;
const DEFAULT_CELL_LNG = 0.006;
const MAX_POLYGONS = 200;
// The pre-HEAT_BLOB_RADIUS_FACTOR radius factor, used only while
// SOFT_HEAT_RENDER_ENABLED is false — matches HeatmapCells.tsx's own
// flag-off geometry exactly (see its outerRadiusM computation) so the
// driver-exclusion zone is always sized to whichever blob is actually drawn.
const LEGACY_BLOB_RADIUS_FACTOR = 0.62;
// 1.3x the blob's own radius: covers the driver's own grid cell plus a small
// margin so a blob's edge doesn't visibly clip right at the car icon's
// boundary.
const DRIVER_EXCLUDE_MARGIN = 1.3;

export interface HeatmapRegion {
  latitude: number;
  longitude: number;
  latitudeDelta: number;
  longitudeDelta: number;
}

export interface LatLng {
  latitude: number;
  longitude: number;
}

/**
 * Cell-filtering + sizing logic shared by every demand-heatmap renderer:
 * drops non-finite coordinates (a corrupted cache row or partial payload
 * would otherwise crash the native map), restricts to the current viewport
 * when a region is known, excludes any cell whose blob would render on top
 * of the driver's own CarMarker (live-testing report 2026-09-09: "concentric
 * circles around the car icon"), then sorts by weight descending and caps at
 * MAX_POLYGONS.
 *
 * `driverLocation` is optional so a caller without a live fix yet (cold
 * start) just gets every cell, same as before this exclusion existed.
 */
export function useVisibleHeatmapCells(
  cells: HeatmapCell[],
  region: HeatmapRegion | null | undefined,
  cellLatDeg: number | null | undefined,
  cellLngDeg: number | null | undefined,
  driverLocation: LatLng | null | undefined,
) {
  const cellLat = typeof cellLatDeg === 'number' && cellLatDeg > 0 ? cellLatDeg : DEFAULT_CELL_LAT;
  const cellLng = typeof cellLngDeg === 'number' && cellLngDeg > 0 ? cellLngDeg : DEFAULT_CELL_LNG;

  // Nominal radius (metres) one cell's blob visually occupies — sizes the
  // driver-exclusion zone below. Mirrors HeatmapCells.tsx's own outerRadiusM
  // exactly (same flag, same factor pair) so exclusion always matches
  // whichever geometry is actually live.
  const outerRadiusM = cellLat * METERS_PER_LAT_DEG * (SOFT_HEAT_RENDER_ENABLED ? HEAT_BLOB_RADIUS_FACTOR : LEGACY_BLOB_RADIUS_FACTOR);
  const excludeRadiusM = outerRadiusM * DRIVER_EXCLUDE_MARGIN;

  const visibleCells = useMemo(() => {
    if (!cells.length) return [];

    // Drop anything that would produce NaN/Infinity coordinates. react-native-maps
    // hands coordinates straight to the native map, and a non-finite one is an
    // app CRASH on Android, not a missing shape — so a single corrupted
    // cache row or a partial payload must never reach the renderers below.
    let filtered = cells.filter((c) => Number.isFinite(c.lat) && Number.isFinite(c.lng) && Number.isFinite(c.weight));

    if (region) {
      const latMin = region.latitude - region.latitudeDelta / 2;
      const latMax = region.latitude + region.latitudeDelta / 2;
      const lngMin = region.longitude - region.longitudeDelta / 2;
      const lngMax = region.longitude + region.longitudeDelta / 2;
      filtered = filtered.filter(
        (c) => c.lat >= latMin && c.lat <= latMax && c.lng >= lngMin && c.lng <= lngMax,
      );
    }

    if (driverLocation && Number.isFinite(driverLocation.latitude) && Number.isFinite(driverLocation.longitude)) {
      filtered = filtered.filter((c) => {
        const center = cellCenter(c.lat, c.lng, cellLat, cellLng);
        return distanceMeters(
          driverLocation.latitude, driverLocation.longitude, center.latitude, center.longitude,
        ) > excludeRadiusM;
      });
    }

    // Sort a copy. When `region` is null the filter above is the only thing
    // standing between this and `cells` itself — and before the isFinite
    // filter existed, `filtered` WAS `cells`, so this sorted the hook's own
    // state array in place, reordering what every other consumer sees.
    return [...filtered].sort((a, b) => b.weight - a.weight).slice(0, MAX_POLYGONS);
  }, [cells, region, driverLocation, cellLat, cellLng, excludeRadiusM]);

  const maxWeight = useMemo(
    () => visibleCells.reduce((m, c) => Math.max(m, c.weight), 0),
    [visibleCells],
  );

  return { visibleCells, maxWeight, cellLat, cellLng, outerRadiusM };
}
