import React from 'react';
import { Platform } from 'react-native';
import { Circle, Heatmap } from 'react-native-maps';
import { useTheme } from '@shared/theme/ThemeContext';
import {
  HEAT_NATIVE_LAYER_ALPHA,
  HEAT_RING_STOPS,
  SOFT_HEAT_RENDER_ENABLED,
  cellCenter,
  hexToRgba,
  nativeGradient,
  ringAlphas,
  weightToRampIndex,
} from '../../lib/heatFalloff';
import { useVisibleHeatmapCells } from '../../hooks/useVisibleHeatmapCells';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// The soft-blob (Circle-ring) renderer draws HEAT_RING_STOPS.length shapes
// per cell instead of 2, so the cell cap comes down to keep the native view
// count in the same order: 60x2 = 120 shapes today, 40x5 = 200 with the soft
// path. The wider kernel also means more overdraw per shape. Nothing in this
// repo can profile Apple Maps or an Auto head unit, so this is a
// conservative budget, not a measured one.
const MAX_BLOBS = 60;
const MAX_SOFT_BLOBS = 40;

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
// iOS gets a softened stand-in (concentric translucent circles per cell
// instead of one flat-edged rectangle) rather than the flat square-box look
// this was reported as looking like. The shape/falloff math for both the
// Android gradient and the iOS stand-in lives in lib/heatFalloff.ts, gated by
// SOFT_HEAT_RENDER_ENABLED (ships dark pending native-device verification —
// see that flag's own doc comment). HM-32 (ACTION_ITEMS.md) tracks a further,
// separate iOS improvement (a true Skia raster-gradient overlay,
// HeatmapGradientOverlay.tsx) that replaces this component's iOS render path
// entirely rather than building on this flag.
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
    // disagree about what a given shade means. The soft path additionally
    // anchors the table at alpha 0 so low density fades out instead of ending
    // at a disc edge — see nativeGradient().
    const { colors: gradientColors, startPoints } = SOFT_HEAT_RENDER_ENABLED
      ? nativeGradient(colors.heatmapRamp)
      : {
          colors: colors.heatmapRamp as unknown as string[],
          startPoints: colors.heatmapRamp.map((_, i) => i / (colors.heatmapRamp.length - 1)),
        };
    return (
      <Heatmap
        points={points}
        radius={45}
        // Android sums density internally, so this takes the COMPOSITED alpha
        // a busy area reaches on the iOS stack, not the per-cell peak — the
        // latter would render Android markedly fainter than iOS. Still only a
        // first-order match: the native layer maps colour its own way.
        opacity={SOFT_HEAT_RENDER_ENABLED ? HEAT_NATIVE_LAYER_ALPHA : 0.75}
        gradient={{
          colors: gradientColors,
          startPoints,
          colorMapSize: 256,
        }}
      />
    );
  }

  // iOS (Apple Maps) polish path — no native gradient layer available, so
  // fake the same soft, edgeless "heat" read with either the legacy 2-circle
  // stand-in or (once SOFT_HEAT_RENDER_ENABLED) nested Gaussian-falloff rings
  // (see lib/heatFalloff.ts). Radii are derived from the server's own grid
  // size so denser grids (small service areas) get proportionally smaller
  // blobs rather than overlapping into one blob. outerRadiusM comes from the
  // shared hook (also sizes the driver-position exclusion zone there).
  const innerRadiusM = outerRadiusM * 0.5;
  const blobCells = visibleCells.slice(0, SOFT_HEAT_RENDER_ENABLED ? MAX_SOFT_BLOBS : MAX_BLOBS);

  return (
    <>
      {blobCells.map((cell) => {
        const idx = weightToRampIndex(cell.weight, maxWeight);
        const color = colors.heatmapRamp[idx];
        const center = cellCenter(cell.lat, cell.lng, cellLat, cellLng);

        if (SOFT_HEAT_RENDER_ENABLED) {
          // Nested rings whose STACKED opacity follows a Gaussian, so the blob
          // fades instead of stepping. strokeColor is set explicitly here: the
          // two-circle path below sets only strokeWidth={0} and leaves the
          // colour undefined, which is the leading candidate for the black
          // outlines reported on iOS (HM26-05 — candidate containment, not a
          // confirmed cause; it still needs reproducing on a native build).
          const alphas = ringAlphas(maxWeight > 0 ? cell.weight / maxWeight : 0);
          return (
            <React.Fragment key={`hm-${cell.lat}-${cell.lng}`}>
              {HEAT_RING_STOPS.map((stop, i) => (
                <Circle
                  key={stop}
                  center={center}
                  radius={outerRadiusM * stop}
                  fillColor={hexToRgba(color, alphas[i])}
                  strokeColor="transparent"
                  strokeWidth={0}
                />
              ))}
            </React.Fragment>
          );
        }

        return (
          // Keyed on the cell's own coordinates, not array index — the list
          // is re-sorted by weight on every poll, so an index-prefixed key
          // would churn native views for cells that had only moved position.
          <React.Fragment key={`hm-${cell.lat}-${cell.lng}`}>
            <Circle
              center={center}
              radius={outerRadiusM}
              fillColor={hexToRgba(color, 0.14)}
              strokeColor="transparent"
              strokeWidth={0}
            />
            <Circle
              center={center}
              radius={innerRadiusM}
              fillColor={hexToRgba(color, idx === 4 ? 0.5 : 0.32)}
              strokeColor="transparent"
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
