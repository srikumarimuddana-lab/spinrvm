import React from 'react';
import { Platform } from 'react-native';
import { Circle, Heatmap } from 'react-native-maps';
import { useTheme } from '@shared/theme/ThemeContext';
import { rampColorForRatio, rgbaString } from '../../utils/heatmapColor';
import { cellCenter, useVisibleHeatmapCells } from '../../hooks/useVisibleHeatmapCells';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// The soft-blob (Circle-ring) renderer draws BLOB_LAYERS.length shapes per
// cell — cap tighter than the shared hook's MAX_POLYGONS cap so iOS/
// non-Google-Maps builds don't push hundreds of overlapping translucent
// circles through the native bridge every poll.
const MAX_BLOBS = 45;
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
  /** Grid size from the server; null falls back to the shared hook's defaults. */
  cellLatDeg?: number | null;
  cellLngDeg?: number | null;
  /**
   * The driver's own live position — passed straight through to
   * useVisibleHeatmapCells (see its own doc comment for why a cell near the
   * driver is dropped: live-testing report 2026-09-09, "concentric circles
   * around the car icon"). Optional so a caller without a live fix yet (cold
   * start) just renders every cell.
   */
  driverLocation?: { latitude: number; longitude: number } | null;
}

// react-native-maps' native <Heatmap> (true gradient density layer) is only
// backed on Android and on iOS-with-Google-Maps — this app deliberately runs
// Apple Maps on iOS (see app.config.ts's comment on why), so the gradient
// component silently no-ops there. HM-GRAD-1: split the renderer instead of
// shipping the same hard-edged grid everywhere — Android gets the real thing,
// iOS gets a softened stand-in (layered translucent circles per cell, see
// BLOB_LAYERS, plus a continuous color ramp — rampColorForRatio) rather than
// the flat square-box look this was reported as looking like. HM-32 tracks a
// true Skia raster-gradient renderer as a further iOS improvement over this
// fallback (ACTION_ITEMS.md).
const USE_NATIVE_GRADIENT = Platform.OS === 'android';

export const HeatmapCells: React.FC<HeatmapCellsProps> = React.memo(
  ({ cells, region, cellLatDeg, cellLngDeg, driverLocation }) => {
  const { colors } = useTheme();

  const { visibleCells, maxWeight, cellLat, cellLng, outerRadiusM } = useVisibleHeatmapCells(
    cells, region, cellLatDeg, cellLngDeg, driverLocation,
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
  // into one blob. outerRadiusM comes from the shared hook (also sizes the
  // driver-position exclusion zone there).
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
