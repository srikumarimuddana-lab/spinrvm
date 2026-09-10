import React, { useMemo } from 'react';
import {
  ActivityIndicator,
  StyleProp,
  StyleSheet,
  Text,
  TextStyle,
  TouchableOpacity,
  TouchableOpacityProps,
  ViewStyle,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';
import { FONT, SPACING } from '../utils/responsive';

/**
 * Button — the shared primary-CTA primitive.
 *
 * Extracted from driver-app's RideOfferPanel accept/decline buttons (design
 * audit called these "already exemplary": 54px tall, 14px radius, spinner
 * swapped in for the label while busy, disabled without extra dimming) —
 * that shape is `size="lg"`. `variant="secondary"` extracts RideOfferPanel's
 * decline button (neutral surface + border, dimmed text) and `variant="danger"`
 * extracts the destructive-confirm pattern seen in rider-app's ride-completed
 * cancel-reason modal (solid `colors.danger` fill). `size="md"` extracts the
 * paddingVertical:16 block-CTA pattern repeated (with three different
 * border-radii — 30px, 12px, 28px) across become-driver.tsx, report-safety.tsx
 * and ride-options.tsx; those three are this PR's only migrated consumers.
 *
 * `icon` (added for driver-app's UX3 migration, 2026-09-10) is an optional
 * leading glyph rendered before the label — extracted from the first two
 * real call sites that actually needed one: ActivityView's "Try Again"
 * retry pill and documents.tsx's "Re-upload Document" button. Every
 * pre-existing consumer omits it and renders byte-identical to before.
 *
 * What this intentionally does NOT do: no outline/ghost variant — nothing
 * in the codebase's real CTA buttons needed one yet, and this component
 * only extracts patterns that already exist (see CLAUDE.md "Simplicity
 * first"). Add one when a real screen needs it.
 */
export type ButtonVariant = 'primary' | 'secondary' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps
  extends Omit<TouchableOpacityProps, 'style' | 'disabled' | 'children'> {
  /** Button label. A plain string (or template literal) is the common case;
   *  a custom node (e.g. multi-part fare text) is also accepted. */
  children: React.ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner in place of the label and blocks onPress. Does not by
   *  itself dim the button — matches RideOfferPanel's accept/decline, which
   *  swap content only. */
  loading?: boolean;
  disabled?: boolean;
  /** Stretches to the width of its parent. Default true — every real CTA
   *  this was extracted from is a full-width block button. Pass false for
   *  a button meant to sit inline (e.g. side-by-side with flex:1 wrappers,
   *  as RideOfferPanel's accept/decline pair already does at the call site). */
  fullWidth?: boolean;
  /** Optional leading icon (an Ionicons glyph name) rendered before the
   *  label. Hidden while `loading` — the spinner replaces the whole label
   *  row, same as a button with no icon. Omit for a plain text button;
   *  every pre-UX3 consumer does. */
  icon?: keyof typeof Ionicons.glyphMap;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
}

// Leading-icon size/gap per size — proportioned to each size's text/padding,
// matching the two real call sites' own prior icon sizes (16 for the sm
// re-upload button, 18 for the md-ish retry pill) rather than one fixed value.
const ICON_SIZES: Record<ButtonSize, number> = { sm: 16, md: 18, lg: 20 };
const ICON_GAPS: Record<ButtonSize, number> = { sm: 6, md: 8, lg: 8 };

export function Button({
  children,
  variant = 'primary',
  size = 'lg',
  loading = false,
  disabled = false,
  fullWidth = true,
  icon,
  style,
  textStyle,
  onPress,
  accessibilityRole = 'button',
  activeOpacity = 0.8,
  ...rest
}: ButtonProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const isDisabled = disabled || loading;

  // Spinner color: white reads on the two filled variants (primary/danger,
  // same '#fff' the extraction sources already hardcode for on-color text —
  // universal regardless of theme, not a brand color); secondary is an
  // outlined/neutral surface so it needs a theme-derived foreground instead.
  const spinnerColor = variant === 'secondary' ? colors.textDim : '#FFFFFF';

  return (
    <TouchableOpacity
      accessibilityRole={accessibilityRole}
      accessibilityState={{ disabled: isDisabled, busy: loading }}
      onPress={onPress}
      disabled={isDisabled}
      activeOpacity={activeOpacity}
      style={[
        styles.base,
        SIZE_STYLES[size].container,
        styles[`${variant}Container`],
        fullWidth && styles.fullWidth,
        disabled && !loading && styles.disabledDim,
        style,
      ]}
      {...rest}
    >
      {loading ? (
        <ActivityIndicator color={spinnerColor} size="small" />
      ) : (
        <>
          {icon && (
            <Ionicons
              name={icon}
              size={ICON_SIZES[size]}
              color={spinnerColor}
              style={{ marginRight: ICON_GAPS[size] }}
            />
          )}
          <Text
            style={[SIZE_STYLES[size].text, styles[`${variant}Text`], textStyle]}
            numberOfLines={1}
          >
            {children}
          </Text>
        </>
      )}
    </TouchableOpacity>
  );
}

const SIZE_STYLES: Record<ButtonSize, { container: ViewStyle; text: TextStyle }> = {
  sm: {
    container: { minHeight: 40, borderRadius: 10, paddingHorizontal: SPACING.md },
    text: { fontSize: FONT.bodySm, fontWeight: '600' },
  },
  md: {
    container: { minHeight: 48, borderRadius: 12, paddingHorizontal: SPACING.lg },
    text: { fontSize: FONT.bodyMd, fontWeight: '600' },
  },
  // Matches RideOfferPanel's accept/decline exactly (54px / 14px radius).
  lg: {
    container: { height: 54, borderRadius: 14, paddingHorizontal: SPACING.lg },
    text: { fontSize: FONT.bodyLg, fontWeight: '700' },
  },
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    base: {
      alignItems: 'center',
      justifyContent: 'center',
      flexDirection: 'row',
    },
    fullWidth: {
      alignSelf: 'stretch',
    },
    disabledDim: {
      opacity: 0.5,
    },
    primaryContainer: {
      backgroundColor: colors.primary,
    },
    primaryText: {
      color: '#FFFFFF',
    },
    secondaryContainer: {
      backgroundColor: colors.surfaceLight,
      borderWidth: 1,
      borderColor: colors.border,
    },
    secondaryText: {
      color: colors.textDim,
    },
    dangerContainer: {
      backgroundColor: colors.danger,
    },
    dangerText: {
      color: '#FFFFFF',
    },
  });
}

export default Button;
