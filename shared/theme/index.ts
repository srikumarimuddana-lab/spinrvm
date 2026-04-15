/**
 * Spinr Design System — Color Palettes
 *
 * Two palettes derived from the canonical SpinrConfig.theme.colors keys.
 * All consumers call useTheme() to get the active palette — never import
 * SpinrConfig.theme.colors directly in UI components.
 *
 * Structure mirrors SpinrConfig.theme.colors exactly so existing consumers
 * can swap `SpinrConfig.theme.colors` for `colors` from useTheme() with a
 * one-line import change.
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
  // Semantic
  error: string;
  success: string;
  warning: string;
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
  error: '#DC2626',
  success: '#34C759',
  warning: '#FFCC00',
  accent: '#FF3B30',
  accentDim: '#D32F2F',
  danger: '#DC2626',
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
  error: '#FF453A',
  success: '#30D158',       // iOS system green (dark)
  warning: '#FFD60A',       // iOS system yellow (dark)
  accent: '#FF453A',
  accentDim: '#D32F2F',
  danger: '#FF453A',
  orange: '#FF9F0A',        // iOS system orange (dark)
  gold: '#FFD700',
};

export type ColorScheme = 'light' | 'dark' | 'system';
