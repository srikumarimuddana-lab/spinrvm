import React, { useMemo } from 'react';
import { Platform } from 'react-native';
import { Circle, Heatmap } from 'react-native-maps';
import { useTheme } from '@shared/theme/ThemeContext';
import { distanceMeters } from '@shared/utils/vehicleTracking';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// Fallbacks only. The server sends the grid size it actually bucketed with
// (cell_lat_deg / cell_lng_deg) because that size is tunable per service area;
// these values are what an older backend that omits them used, so an app on a
// new build talking to an old backend keeps its previous behaviour exactly.
const DEFAULT_CELL_LAT = 0.004;
const DEFAULT_CELL_LNG = 0.006;
const MAX_POLYGONS = 200;
// The soft-blob (Circle-ring) renderer draws BLOB_LAYERS.length shapes per
// cell — cap tighter than MAX_POLYGONS so iOS/non-Google-Maps builds don't
// push hundreds of overlapping translucent circles through the native
// bridge every poll.
const MAX_BLOBS = 45;
// Metres per degree of latitude — used to size blob radii off the server's
// own grid cell size rather than a hardcoded metre value, so denser grids
// (smaller service areas) automatically get smaller, tighter blobs.
const METERS_PER_LAT_DEG = 111_320;
// Layered circles standing in for a real Gaussian blur (Apple MapKit has no
// native heat-density layer — see USE_NATIVE_GRADIENT below). Widest/palest
// layer first so each cell reads as a soft falloff from center rather than a
// small number of visible concentric rings.
const BLOB_LAYERS = [
  { radiusFactor: 1.0, opacity: 0.08 },
  { radiusFactor: 0.62, opacity: 0.16 },
  { radiusFactor: 0.32, opacity: 0.3 },
] as const;

interface HeatmapCellsProps {
  cells: HeatmapCell[];
  region?: { latitude: number; longitude: number; latitudeDelta: number; longitudeDelta: number } | null;
  /** Grid size from the server; null falls back to the constants above. */
  cellLatDeg?: number | null;
  cellLngDeg?: number | null;
  /**
   * The driver's own live position. A cell whose center falls within
   * DRIVER_EXCLUDE_RADIUS_M of it is dropped so a demand blob never renders
   * directly on top of / around the driver's own CarMarker — live-testing
   * report 2026-09-09: "concentric circles around the car icon" (iOS). The
   * iOS soft-blob renderer below draws several translucent Circle overlays
   * per cell (see BLOB_LAYERS); a driver idling inside (or bordering) a busy
   * cell was seeing those circles stack visually with the car's own colored
   * presence ring
   * (CarMarker's `ring` prop) with no way to tell them apart. Optional so a
   * caller without a live fix yet (cold start) just renders every cell, same
   * as before this prop existed.
   */
  driverLocation?: { latitude: number; longitude: number } | null;
}

function cellCenter(lat: number, lng: number, cellLat: number, cellLng: number) {
  const baseLat = Math.floor(lat / cellLat) * cellLat;
  const baseLng = Math.floor(lng / cellLng) * cellLng;
  return { latitude: baseLat + cellLat / 2, longitude: baseLng + cellLng / 2 };
}

function hexToRgb(hex: string): [number, number, number] {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

// Continuous interpolation across the 5-step brand ramp instead of snapping
// to one of 5 discrete buckets (the old weightToRampIndex) — design feedback
// 2026-09-09 (referencing Uber's driver-app demand heatmap): 5 discrete
// bucketed colors read as visibly banded/blocky, not the soft continuous
// gradient a blurred heatmap gives. Ramp entries sit at even positions
// along [0,1], matching the native <Heatmap> gradient's own `startPoints`
// spacing above, so the two renderers never disagree on what a given shade
// means. `ratio` is clamped so an out-of-range weight can't index past the
// ramp array.
function rampColorForRatio(ratio: number, ramp: readonly string[]): [number, number, number] {
  const clamped = Math.max(0, Math.min(1, ratio));
  const steps = ramp.length - 1;
  const pos = clamped * steps;
  const i = Math.min(steps - 1, Math.floor(pos));
  const t = pos - i;
  const [r1, g1, b1] = hexToRgb(ramp[i]);
  const [r2, g2, b2] = hexToRgb(ramp[i + 1]);
  return [r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t];
}

function rgbaString([r, g, b]: readonly [number, number, number], alpha: number): string {
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${alpha})`;
}

// react-native-maps' native <Heatmap> (true gradient density layer) is only
// backed on Android and on iOS-with-Google-Maps — this app deliberately runs
// Apple Maps on iOS (see app.config.ts's comment on why), so the gradient
// component silently no-ops there. HM-GRAD-1: split the renderer instead of
// shipping the same hard-edged grid everywhere — Android gets the real thing,
// iOS gets a softened stand-in (layered translucent circles per cell, see
// BLOB_LAYERS, plus a continuous color ramp — rampColorForRatio) rather than
// the flat square-box look this was reported as looking like.
const USE_NATIVE_GRADIENT = Platform.OS === 'android';

export const HeatmapCells: React.FC<HeatmapCellsProps> = React.memo(
  ({ cells, region, cellLatDeg, cellLngDeg, driverLocation }) => {
  const { colors } = useTheme();

  const cellLat = typeof cellLatDeg === 'number' && cellLatDeg > 0 ? cellLatDeg : DEFAULT_CELL_LAT;
  const cellLng = typeof cellLngDeg === 'number' && cellLngDeg > 0 ? cellLngDeg : DEFAULT_CELL_LNG;

  // Radius a cell's blob visually occupies (see the layered-Circle iOS path
  // below) — computed here, ahead of the early return, because it also sizes
  // the driver-position exclusion zone regardless of which render path runs.
  // 0.9x (not the original 0.62x): a larger radius means ADJACENT cells'
  // blobs overlap instead of sitting as isolated dots with visible gaps
  // between them — part of the same "looks blocky, not blurred" fix as the
  // continuous color ramp above.
  const outerRadiusM = cellLat * METERS_PER_LAT_DEG * 0.9;
  // 1.3x the blob's own radius: covers the driver's own grid cell plus a
  // small margin so the blob's edge doesn't visibly clip right at the car
  // icon's boundary.
  const excludeRadiusM = outerRadiusM * 1.3;

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

  if (!visibleCells.length) return null;

  if (USE_NATIVE_GRADIENT) {
    const points = visibleCells.map((cell) => {
      const { latitude, longitude } = cellCenter(cell.lat, cell.lng, cellLat, cellLng);
      return { latitude, longitude, weight: cell.weight };
    });
    // Even spacing across the 5-step brand ramp (quiet -> busy), same colors
    // the collapsed legend swatch uses, so the gradient and the legend never
    // disagree about what a given shade means.
    const gradientColors = colors.heatmapRamp;
    const startPoints = gradientColors.map((_, i) => i / (gradientColors.length - 1));
    return (
      <Heatmap
        points={points}
        radius={45}
        opacity={0.75}
        gradient={{
          colors: gradientColors,
          startPoints,
          colorMapSize: 256,
        }}
      />
    );
  }

  // iOS (Apple Maps) polish path — no native gradient layer available, so
  // approximate the same soft, edgeless "heat" read with layered, low-opacity
  // circles per cell (a hand-rolled falloff standing in for a real Gaussian
  // blur) instead of one hard-edged Polygon square or the earlier flat
  // 2-circle version — design feedback 2026-09-09 (referencing Uber's
  // driver-app demand heatmap): more layers with a gentler opacity curve
  // reads as soft/blurred rather than as a small number of visible rings.
  // Radii are derived from the server's own grid size so denser grids (small
  // service areas) get proportionally smaller blobs rather than overlapping
  // into one blob. outerRadiusM itself is computed above (also sizes the
  // driver-position exclusion zone).
  const blobCells = visibleCells.slice(0, MAX_BLOBS);

  return (
    <>
      {blobCells.map((cell) => {
        const ratio = maxWeight > 0 ? cell.weight / maxWeight : 0;
        const rgb = rampColorForRatio(ratio, colors.heatmapRamp);
        const center = cellCenter(cell.lat, cell.lng, cellLat, cellLng);
        // The busiest cells (top ramp tier) get a modest opacity boost so
        // the hottest spots still read as visually hottest, matching the
        // native gradient's own top-of-ramp emphasis.
        const boost = ratio > 0.8 ? 1.4 : 1;
        return (
          // Keyed on the cell's own coordinates, not array index — the list
          // is re-sorted by weight on every poll, so an index-prefixed key
          // would churn native views for cells that had only moved position.
          <React.Fragment key={`hm-${cell.lat}-${cell.lng}`}>
            {BLOB_LAYERS.map((layer, i) => (
              <Circle
                key={i}
                center={center}
                radius={outerRadiusM * layer.radiusFactor}
                fillColor={rgbaString(rgb, Math.min(0.6, layer.opacity * boost))}
                strokeWidth={0}
              />
            ))}
          </React.Fragment>
        );
      })}
    </>
  );
  },
);
HeatmapCells.displayName = 'HeatmapCells';
