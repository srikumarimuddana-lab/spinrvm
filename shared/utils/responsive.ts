/**
 * Spinr Responsive Utilities (mobile only — not for admin-dashboard)
 *
 * Replaces module-level Dimensions.get('window') calls, which freeze at app
 * startup and never update on device rotation, foldable unfold, or split-screen.
 *
 * Usage:
 *   import { useResponsive, SPACING, FONT } from '@shared/utils/responsive';
 *
 *   function MyScreen() {
 *     const { isTablet, mapHeight, sheetSnaps } = useResponsive();
 *     ...
 *   }
 */

import { useWindowDimensions } from 'react-native';

// ── Device breakpoints (dp / logical pixels) ─────────────────────────────────
export const BREAKPOINTS = {
  sm: 375,   // small phones (iPhone SE, Galaxy A series)
  md: 428,   // large phones (iPhone Pro Max, Pixel 7 Pro)
  lg: 768,   // tablets (iPad portrait, Galaxy Tab)
  xl: 1024,  // large tablets landscape (iPad Pro, Surface Go)
} as const;

// ── Spacing scale ─────────────────────────────────────────────────────────────
export const SPACING = {
  xs:  4,
  sm:  8,
  md:  16,
  lg:  24,
  xl:  32,
  xxl: 48,
} as const;

// ── Typography scale ──────────────────────────────────────────────────────────
export const FONT = {
  h1:     32,
  h2:     26,
  h3:     22,
  bodyLg: 16,
  bodyMd: 15,
  bodySm: 13,
  label:  11,
} as const;

// ── Minimum touch target (Apple HIG = 44pt, Material = 48dp) ─────────────────
export const MIN_TOUCH = 44;

// ── Dynamic font scaling ─────────────────────────────────────────────────────
// Design baseline: 375dp (iPhone SE / standard Figma frame).
// Scales linearly with screen width but dampened by a factor so fonts don't
// become enormous on tablets. Clamped to ±30 % of the design size.
const DESIGN_WIDTH = 375;
const SCALE_FACTOR = 0.4; // 0 = no scaling, 1 = fully proportional

/**
 * Scale a font size to the current screen width.
 * Call inside a component or pass `width` from useWindowDimensions.
 *
 *   const styles = createStyles(sf);
 *   // in StyleSheet: fontSize: sf(15)
 */
export function createScaledFont(screenWidth: number) {
  return (size: number): number => {
    const scale = screenWidth / DESIGN_WIDTH;
    const scaled = size + (size * (scale - 1)) * SCALE_FACTOR;
    const min = size * 0.8;
    const max = size * 1.3;
    return Math.round(Math.min(Math.max(scaled, min), max) * 10) / 10;
  };
}

// ── Hook ──────────────────────────────────────────────────────────────────────
export function useResponsive() {
  // useWindowDimensions re-renders when the window size changes (rotation,
  // foldable unfold, iPad split-screen). Dimensions.get() does not.
  const { width, height } = useWindowDimensions();

  const isSmall     = width < BREAKPOINTS.sm;
  const isPhone     = width < BREAKPOINTS.lg;
  const isTablet    = width >= BREAKPOINTS.lg;
  const isLandscape = height < width;

  // Responsive map height: 38 % of current screen height, clamped so it is
  // never too small on landscape phones or absurdly tall on iPad Pro.
  const mapHeight = Math.min(Math.max(Math.round(height * 0.38), 180), 460);

  // Bottom-sheet snap points sized for the active device configuration.
  // The gorhom/bottom-sheet library accepts percentage strings.
  const sheetSnaps: string[] = isTablet
    ? ['40%', '70%']
    : isLandscape
    ? ['50%', '90%']
    : ['30%', '50%', '85%'];

  // Two-column card width: (screen − 2×horizontal padding − 1 gap) / 2
  const statCardWidth = Math.floor((width - SPACING.md * 2 - SPACING.sm) / 2);

  // Dynamic font scaler for the current screen width
  const sf = createScaledFont(width);

  return {
    width,
    height,
    isSmall,
    isPhone,
    isTablet,
    isLandscape,
    mapHeight,
    sheetSnaps,
    statCardWidth,
    sf,
  };
}
