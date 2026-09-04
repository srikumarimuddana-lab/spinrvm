import React, { useMemo } from 'react';
import { Platform } from 'react-native';
import { Circle, Heatmap } from 'react-native-maps';
import { useTheme } from '@shared/theme/ThemeContext';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// Fallbacks only. The server sends the grid size it actually bucketed with
// (cell_lat_deg / cell_lng_deg) because that size is tunable per service area;
// these values are what an older backend that omits them used, so an app on a
// new build talking to an old backend keeps its previous behaviour exactly.
const DEFAULT_CELL_LAT = 0.004;
const DEFAULT_CELL_LNG = 0.006;
const MAX_POLYGONS = 200;
// The soft-blob (Circle-ring) renderer draws 2 shapes per cell — cap tighter
// than MAX_POLYGONS so iOS/non-Google-Maps builds don't push 400+ overlapping
// translucent circles through the native bridge every poll.
const MAX_BLOBS = 60;
// Metres per degree of latitude — used to size blob radii off the server's
// own grid cell size rather than a hardcoded metre value, so denser grids
// (smaller service areas) automatically get smaller, tighter blobs.
const METERS_PER_LAT_DEG = 111_320;

interface HeatmapCellsProps {
  cells: HeatmapCell[];
  region?: { latitude: number; longitude: number; latitudeDelta: number; longitudeDelta: number } | null;
  /** Grid size from the server; null falls back to the constants above. */
  cellLatDeg?: number | null;
  cellLngDeg?: number | null;
}

function cellCenter(lat: number, lng: number, cellLat: number, cellLng: number) {
  const baseLat = Math.floor(lat / cellLat) * cellLat;
  const baseLng = Math.floor(lng / cellLng) * cellLng;
  return { latitude: baseLat + cellLat / 2, longitude: baseLng + cellLng / 2 };
}

function weightToRampIndex(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return 0;
  const ratio = weight / maxWeight;
  if (ratio < 0.2) return 0;
  if (ratio < 0.4) return 1;
  if (ratio < 0.6) return 2;
  if (ratio < 0.8) return 3;
  return 4;
}

function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// react-native-maps' native <Heatmap> (true gradient density layer) is only
// backed on Android and on iOS-with-Google-Maps — this app deliberately runs
// Apple Maps on iOS (see app.config.ts's comment on why), so the gradient
// component silently no-ops there. HM-GRAD-1: split the renderer instead of
// shipping the same hard-edged grid everywhere — Android gets the real thing,
// iOS gets a softened stand-in (concentric translucent circles per cell
// instead of one flat-edged rectangle) rather than the flat square-box look
// this was reported as looking like.
const USE_NATIVE_GRADIENT = Platform.OS === 'android';

export const HeatmapCells: React.FC<HeatmapCellsProps> = React.memo(
  ({ cells, region, cellLatDeg, cellLngDeg }) => {
  const { colors } = useTheme();

  const cellLat = typeof cellLatDeg === 'number' && cellLatDeg > 0 ? cellLatDeg : DEFAULT_CELL_LAT;
  const cellLng = typeof cellLngDeg === 'number' && cellLngDeg > 0 ? cellLngDeg : DEFAULT_CELL_LNG;

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

    // Sort a copy. When `region` is null the filter above is the only thing
    // standing between this and `cells` itself — and before the isFinite
    // filter existed, `filtered` WAS `cells`, so this sorted the hook's own
    // state array in place, reordering what every other consumer sees.
    return [...filtered].sort((a, b) => b.weight - a.weight).slice(0, MAX_POLYGONS);
  }, [cells, region]);

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
  // fake the same soft, edgeless "heat" read with two concentric,
  // low-opacity circles per cell instead of one hard-edged Polygon square.
  // Radii are derived from the server's own grid size so denser grids (small
  // service areas) get proportionally smaller blobs rather than overlapping
  // into one blob.
  const outerRadiusM = cellLat * METERS_PER_LAT_DEG * 0.62;
  const innerRadiusM = outerRadiusM * 0.5;
  const blobCells = visibleCells.slice(0, MAX_BLOBS);

  return (
    <>
      {blobCells.map((cell) => {
        const idx = weightToRampIndex(cell.weight, maxWeight);
        const color = colors.heatmapRamp[idx];
        const center = cellCenter(cell.lat, cell.lng, cellLat, cellLng);
        return (
          // Keyed on the cell's own coordinates, not array index — the list
          // is re-sorted by weight on every poll, so an index-prefixed key
          // would churn native views for cells that had only moved position.
          <React.Fragment key={`hm-${cell.lat}-${cell.lng}`}>
            <Circle
              center={center}
              radius={outerRadiusM}
              fillColor={hexToRgba(color, 0.14)}
              strokeWidth={0}
            />
            <Circle
              center={center}
              radius={innerRadiusM}
              fillColor={hexToRgba(color, idx === 4 ? 0.5 : 0.32)}
              strokeWidth={0}
            />
          </React.Fragment>
        );
      })}
    </>
  );
  },
);
HeatmapCells.displayName = 'HeatmapCells';
