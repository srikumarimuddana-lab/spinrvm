import React, { useMemo } from 'react';
import { Polygon } from 'react-native-maps';
import { useTheme } from '@shared/theme/ThemeContext';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// Fallbacks only. The server sends the grid size it actually bucketed with
// (cell_lat_deg / cell_lng_deg) because that size is tunable per service area;
// these values are what an older backend that omits them used, so an app on a
// new build talking to an old backend keeps its previous behaviour exactly.
const DEFAULT_CELL_LAT = 0.004;
const DEFAULT_CELL_LNG = 0.006;
const MAX_POLYGONS = 200;

interface HeatmapCellsProps {
  cells: HeatmapCell[];
  region?: { latitude: number; longitude: number; latitudeDelta: number; longitudeDelta: number } | null;
  /** Grid size from the server; null falls back to the constants above. */
  cellLatDeg?: number | null;
  cellLngDeg?: number | null;
}

function cellToCorners(lat: number, lng: number, cellLat: number, cellLng: number) {
  const baseLat = Math.floor(lat / cellLat) * cellLat;
  const baseLng = Math.floor(lng / cellLng) * cellLng;
  return [
    { latitude: baseLat, longitude: baseLng },
    { latitude: baseLat + cellLat, longitude: baseLng },
    { latitude: baseLat + cellLat, longitude: baseLng + cellLng },
    { latitude: baseLat, longitude: baseLng + cellLng },
  ];
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

export const HeatmapCells: React.FC<HeatmapCellsProps> = React.memo(
  ({ cells, region, cellLatDeg, cellLngDeg }) => {
  const { colors } = useTheme();

  const cellLat = typeof cellLatDeg === 'number' && cellLatDeg > 0 ? cellLatDeg : DEFAULT_CELL_LAT;
  const cellLng = typeof cellLngDeg === 'number' && cellLngDeg > 0 ? cellLngDeg : DEFAULT_CELL_LNG;

  const visibleCells = useMemo(() => {
    if (!cells.length) return [];

    // Drop anything that would produce NaN/Infinity corners. react-native-maps
    // hands coordinates straight to the native map, and a non-finite one is an
    // app CRASH on Android, not a missing rectangle — so a single corrupted
    // cache row or a partial payload must never reach the Polygon below.
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

  return (
    <>
      {visibleCells.map((cell) => {
        const idx = weightToRampIndex(cell.weight, maxWeight);
        const color = colors.heatmapRamp[idx];
        const isTop = idx === 4;
        return (
          <Polygon
            // Keyed on the cell's own coordinates, not its array index. The
            // list is re-sorted by weight on every poll, so an index-prefixed
            // key changed for most cells whenever demand shifted — React then
            // tore down and recreated native views that had only moved.
            key={`hm-${cell.lat}-${cell.lng}`}
            coordinates={cellToCorners(cell.lat, cell.lng, cellLat, cellLng)}
            fillColor={hexToRgba(color, 0.4)}
            strokeColor={isTop ? hexToRgba(color, 0.7) : 'transparent'}
            strokeWidth={isTop ? 1 : 0}
          />
        );
      })}
    </>
  );
  },
);
HeatmapCells.displayName = 'HeatmapCells';
