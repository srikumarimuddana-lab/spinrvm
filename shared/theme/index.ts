/**
 * Spinr Design System — Color Palettes
 *
 * The single source of truth for color tokens. All consumers call
 * useTheme() to get the active palette.
 *
 * (Until 2026-09-04, `shared/config/spinr.config.ts` also carried a
 * `theme.colors` block this file's structure mirrored — it had drifted
 * out of sync with the values here and was removed once confirmed unused
 * by any live UI. This is now the only palette definition.)
 */

export type ThemeColors = {
  // Brand
  primary: string;
  primaryDark: string;
  // Surfaces
  background: string;
  surface: string;
  surfaceLight: string;
  // Typography
  text: string;
  textDim: string;
  textSecondary: string;
  // UI chrome
  border: string;
  overlay: string;
  // Semantic — foreground tones
  error: string;
  success: string;
  warning: string;
  info: string;
  // Semantic — surface tints (background behind status icons / pill badges)
  successBg: string;
  warningBg: string;
  dangerBg: string;
  infoBg: string;
  // Heatmap — 5-step sequential ramp (quiet → busy)
  heatmapRamp: [string, string, string, string, string];
  // Aliases / Legacy
  accent: string;
  accentDim: string;
  danger: string;
  orange: string;
  gold: string;
};

export const lightColors: ThemeColors = {
  primary: '#FF3B30',
  primaryDark: '#D32F2F',
  background: '#FFFFFF',
  surface: '#FFFFFF',
  surfaceLight: '#F5F5F5',
  text: '#1A1A1A',
  textDim: '#666666',
  textSecondary: '#6B7280',
  border: '#E5E7EB',
  overlay: 'rgba(255, 255, 255, 0.95)',
  error: '#EF4444',          // Tailwind red-500 — updated 2026-09-07, was #DC2626 (iOS red-600);
                              // this shade was already the app's de facto majority convention
                              // (rider-app used it 150x vs. 58x for the old token) before being
                              // formally adopted here — see docs/design/rider-driver-app-design-system.md
  success: '#10B981',        // Tailwind emerald-500 — updated 2026-09-07, was #34C759 (iOS green)
  warning: '#F59E0B',        // Tailwind amber-500 — updated 2026-09-07, was #d97706 (amber-600)
  info: '#3B82F6',           // iOS-system-blue (unchanged — already the dominant convention)
  successBg: '#ECFDF5',
  warningBg: '#FFF7ED',
  dangerBg:  '#FEF2F2',
  infoBg:    '#EFF6FF',
  heatmapRamp: ['#FFE3E0', '#FFB3AC', '#FF7A6E', '#FF3B30', '#B71C1C'],
  accent: '#FF3B30',
  accentDim: '#D32F2F',
  danger: '#EF4444',         // legacy alias, kept mirroring `error` exactly
  orange: '#FF9500',
  gold: '#FFD700',
};

export const darkColors: ThemeColors = {
  primary: '#FF453A',       // iOS system red — brighter for dark bg
  primaryDark: '#D32F2F',
  background: '#000000',    // True black — OLED power savings
  surface: '#1C1C1E',       // iOS dark surface
  surfaceLight: '#2C2C2E',  // iOS dark elevated surface
  text: '#F2F2F7',          // iOS primary label (dark)
  textDim: '#AEAEB2',       // iOS secondary label
  textSecondary: '#8E8E93', // iOS tertiary label
  border: '#38383A',        // iOS separator dark
  overlay: 'rgba(0, 0, 0, 0.92)',
  error: '#F87171',         // Tailwind red-400 — updated 2026-09-07 (was iOS red #FF453A), to
                             // stay in the same Tailwind family as the new light-mode `error`
  success: '#34D399',       // Tailwind emerald-400 — updated 2026-09-07 (was iOS green #30D158)
  warning: '#FBBF24',       // Tailwind amber-400 — updated 2026-09-07 (was #F59E0B, which is now
                             // the *light*-mode value; needed a distinct, brighter dark shade)
  info: '#0A84FF',          // iOS system blue (dark) — unchanged
  // Surface tints — NOT just inverted; very-low-luminance versions of the
  // foreground hue so they read as a tinted dark surface, not a near-white
  // tint inverted to off-tone aubergine.
  successBg: '#0B3D2E',
  warningBg: '#431A03',
  dangerBg:  '#3D0B0B',
  infoBg:    '#0B243D',
  heatmapRamp: ['#4E211E', '#7F2D26', '#B2382E', '#FF453A', '#FF8A80'],
  accent: '#FF453A',
  accentDim: '#D32F2F',
  danger: '#F87171',        // legacy alias, kept mirroring `error` exactly
  orange: '#FF9F0A',        // iOS system orange (dark)
  gold: '#FFD700',
};

export type ThemeColorKey = { [K in keyof ThemeColors]: ThemeColors[K] extends string ? K : never }[keyof ThemeColors];

export type ColorScheme = 'light' | 'dark' | 'system';
