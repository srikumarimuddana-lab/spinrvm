import React, { useMemo } from 'react';
import { useTheme } from '@shared/theme/ThemeContext';
import { rampColorForRatio, rgbaString } from '../../utils/heatmapColor';
import { cellCenter, useVisibleHeatmapCells, type HeatmapRegion, type LatLng } from '../../hooks/useVisibleHeatmapCells';
import { projectToScreen, type Viewport } from '../../utils/heatmapProjection';
import type { HeatmapCell } from '../../hooks/useDemandHeatmap';

// HM-32 (ACTION_ITEMS.md): a true raster-gradient heatmap for iOS, where
// react-native-maps' native <Heatmap> layer doesn't work (Apple Maps has no
// equivalent — see HeatmapCells.tsx's own USE_NATIVE_GRADIENT comment).
// Renders each visible cell as a solid, fully-saturated Circle in
// SCREEN-space (not geo-space — Skia draws to an offscreen canvas overlaid
// on the map, not into the map itself), all inside one Group whose `layer`
// applies a real image-space Gaussian blur (Blur, an image filter) to the
// Group's RASTERIZED composite. That's the key difference from
// HeatmapCells.tsx's iOS fallback (several translucent Circles per cell,
// each blurred only by eye via opacity layering): here, overlapping circles
// from DIFFERENT cells actually merge into one continuous blurred field,
// which is what a real heatmap blur looks like.
//
// Not exported from useVisibleHeatmapCells' driver-exclusion feature: this
// component reuses that hook unchanged, so a cell near the driver's own
// position is dropped here too — the same "concentric circles around the
// car icon" fix applies regardless of which iOS renderer is active.
//
// Cell radius (BLOB_RADIUS_PX) is a fixed screen-pixel value, matching how
// react-native-maps' own native Android <Heatmap radius={45}> works (a
// constant per-point radius; density comes from overlapping points, not
// point size) — intensity is conveyed by color/opacity (rampColorForRatio),
// not by varying circle size.
const BLOB_RADIUS_PX = 45;
// Image-space blur radius, in pixels — the Skia equivalent of the native
// <Heatmap>'s own internal blur kernel. Tuned to merge adjacent cells'
// circles into a continuous field without washing out the color entirely;
// not verified against a real device (see the HM-32 change-log entry).
const BLUR_RADIUS_PX = 30;

interface HeatmapGradientOverlayProps {
  cells: HeatmapCell[];
  region?: HeatmapRegion | null;
  /** Grid size from the server; null falls back to useVisibleHeatmapCells' defaults. */
  cellLatDeg?: number | null;
  cellLngDeg?: number | null;
  driverLocation?: LatLng | null;
  /**
   * Pixel size of the map container this canvas is overlaid on — required to
   * project cells' lat/lng into this canvas's screen space (see
   * utils/heatmapProjection.ts). The caller measures this once via onLayout
   * on the MapView's wrapping View.
   */
  viewport: Viewport;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type SkiaModule = { Canvas: any; Group: any; Circle: any; Paint: any; Blur: any };

// @shopify/react-native-skia is a NATIVE dependency added 2026-09-09 (HM-32).
// A plain top-level `import` would throw at module-evaluation time — before
// React ever gets a chance to render anything — on a JS bundle that reaches
// a native binary built before this dependency existed (exactly the
// OTA/native-build mismatch class this session already found real instances
// of: the ring-freeze fix, the OTA-channel bug). No React error boundary can
// catch an import-time crash, so this lazily requires the module instead —
// the same guard carSurface.tsx already uses for its own react-native-maps-
// dependent imports — and degrades to "no gradient overlay" rather than
// crashing the driver dashboard screen.
function loadSkia(): SkiaModule | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require('@shopify/react-native-skia');
  } catch {
    return null;
  }
}

export const HeatmapGradientOverlay: React.FC<HeatmapGradientOverlayProps> = React.memo(
  ({ cells, region, cellLatDeg, cellLngDeg, driverLocation, viewport }) => {
    const { colors } = useTheme();
    const Skia = loadSkia();

    const { visibleCells, maxWeight, cellLat, cellLng } = useVisibleHeatmapCells(
      cells, region, cellLatDeg, cellLngDeg, driverLocation,
    );

    // Memoized: recreated only if Skia's own module identity or the blur
    // radius changes (neither does, today) — avoids reallocating a fresh
    // <Paint> element on every region-change-driven re-render during a drag.
    const blurLayer = useMemo(() => {
      if (!Skia) return null;
      const { Paint, Blur } = Skia;
      return (
        <Paint>
          <Blur blur={BLUR_RADIUS_PX} mode="decal" />
        </Paint>
      );
    }, [Skia]);

    if (!Skia || !region || !visibleCells.length || viewport.width <= 0 || viewport.height <= 0) return null;
    const { Canvas, Group, Circle } = Skia;

    return (
      <Canvas
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: viewport.width,
          height: viewport.height,
        }}
        pointerEvents="none"
      >
        <Group layer={blurLayer}>
          {visibleCells.map((cell) => {
            const ratio = maxWeight > 0 ? cell.weight / maxWeight : 0;
            const rgb = rampColorForRatio(ratio, colors.heatmapRamp);
            const center = cellCenter(cell.lat, cell.lng, cellLat, cellLng);
            const point = projectToScreen(center.latitude, center.longitude, region, viewport);
            // Fully opaque per-circle fill — the SOFTNESS comes entirely
            // from the Group's blur filter compositing overlapping circles
            // together, not from per-circle transparency (that's the
            // opacity-layering technique HeatmapCells.tsx's fallback uses
            // instead, for platforms without a real blur filter).
            return (
              <Circle
                key={`hm-${cell.lat}-${cell.lng}`}
                cx={point.x}
                cy={point.y}
                r={BLOB_RADIUS_PX}
                color={rgbaString(rgb, 1)}
              />
            );
          })}
        </Group>
      </Canvas>
    );
  },
);
HeatmapGradientOverlay.displayName = 'HeatmapGradientOverlay';
