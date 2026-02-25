import SpinrConfig from '@shared/config/spinr.config';

// ─── Colors (Mapped to Light Theme) ────────────────────────────────
const THEME = SpinrConfig.theme.colors;

export const COLORS = {
    // Map old "primary" (background) to new light background
    primary: THEME.background,
    // Map old "accent" (action) to new primary (Red)
    accent: THEME.primary,
    accentDim: THEME.primaryDark,
    danger: THEME.error,
    orange: '#FF9500', // Keep orange for specific UI elements or map to warning
    surface: THEME.surface,
    surfaceLight: THEME.surfaceLight,
    text: THEME.text,
    textDim: THEME.textDim,
    success: THEME.success,
    gold: '#FFD700',
    overlay: 'rgba(255, 255, 255, 0.95)', // Light overlay
    border: THEME.border,
    // Helper for old "primary" usage that might need actual brand color
    brand: THEME.primary,
};

export default COLORS;
