import React, { useMemo } from 'react';
import { Polygon } from 'react-native-maps';
import { useTheme } from '@shared/theme/ThemeContext';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

const CELL_LAT = 0.004;
const CELL_LNG = 0.006;
const MAX_POLYGONS = 200;

interface HeatmapCellsProps {
  cells: HeatmapCell[];
  region?: { latitude: number; longitude: number; latitudeDelta: number; longitudeDelta: number } | null;
}

function cellToCorners(lat: number, lng: number) {
  const baseLat = Math.floor(lat / CELL_LAT) * CELL_LAT;
  const baseLng = Math.floor(lng / CELL_LNG) * CELL_LNG;
  return [
    { latitude: baseLat, longitude: baseLng },
    { latitude: baseLat + CELL_LAT, longitude: baseLng },
    { latitude: baseLat + CELL_LAT, longitude: baseLng + CELL_LNG },
    { latitude: baseLat, longitude: baseLng + CELL_LNG },
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

export const HeatmapCells: React.FC<HeatmapCellsProps> = React.memo(({ cells, region }) => {
  const { colors } = useTheme();

  const visibleCells = useMemo(() => {
    if (!cells.length) return [];

    let filtered = cells;
    if (region) {
      const latMin = region.latitude - region.latitudeDelta / 2;
      const latMax = region.latitude + region.latitudeDelta / 2;
      const lngMin = region.longitude - region.longitudeDelta / 2;
      const lngMax = region.longitude + region.longitudeDelta / 2;
      filtered = cells.filter(
        (c) => c.lat >= latMin && c.lat <= latMax && c.lng >= lngMin && c.lng <= lngMax,
      );
    }

    filtered.sort((a, b) => b.weight - a.weight);
    return filtered.slice(0, MAX_POLYGONS);
  }, [cells, region]);

  const maxWeight = useMemo(
    () => visibleCells.reduce((m, c) => Math.max(m, c.weight), 0),
    [visibleCells],
  );

  if (!visibleCells.length) return null;

  return (
    <>
      {visibleCells.map((cell, i) => {
        const idx = weightToRampIndex(cell.weight, maxWeight);
        const color = colors.heatmapRamp[idx];
        const isTop = idx === 4;
        return (
          <Polygon
            key={`hm-${i}-${cell.lat}-${cell.lng}`}
            coordinates={cellToCorners(cell.lat, cell.lng)}
            fillColor={hexToRgba(color, 0.4)}
            strokeColor={isTop ? hexToRgba(color, 0.7) : 'transparent'}
            strokeWidth={isTop ? 1 : 0}
          />
        );
      })}
    </>
  );
});
