import React, { useMemo } from 'react';
import { StyleProp, StyleSheet, View, ViewProps, ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';
import { SPACING } from '../utils/responsive';

/**
 * Card — the shared surface-container primitive.
 *
 * Extracted from the one card shape repeated identically, byte-for-byte on
 * the container rule, in both rider-app/components/FareQuoteCard.tsx and
 * BookingProposalCard.tsx: `padding:14, borderRadius:14, backgroundColor:
 * colors.surface, borderWidth:1, borderColor:colors.border`. `padding="md"`
 * below is that value rounded to the nearest SPACING token (16 vs 14 — close
 * enough that no screen using it needs a pixel-exact match).
 *
 * Not migrated anywhere in this PR — landing the primitive alongside
 * Button/Input is this PR's scope; wiring existing cards to it is future
 * opportunistic work (see docs/change-log/2026-09-04-shared-button-card-input-primitives.md).
 */
export type CardPadding = 'sm' | 'md' | 'lg';

export interface CardProps extends ViewProps {
  children: React.ReactNode;
  padding?: CardPadding;
  /** Renders the 1px themed border seen in the extraction source. Default
   *  true. Set false for the borderless surface variant used e.g. by
   *  notifications.tsx's card (surfaceLight fill, no border). */
  bordered?: boolean;
  style?: StyleProp<ViewStyle>;
}

const PADDING_VALUES: Record<CardPadding, number> = {
  sm: SPACING.sm,
  md: SPACING.md,
  lg: SPACING.lg,
};

export function Card({ children, padding = 'md', bordered = true, style, ...rest }: CardProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  return (
    <View
      style={[
        styles.base,
        { padding: PADDING_VALUES[padding] },
        bordered && styles.bordered,
        style,
      ]}
      {...rest}
    >
      {children}
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    base: {
      backgroundColor: colors.surface,
      borderRadius: 14,
    },
    bordered: {
      borderWidth: 1,
      borderColor: colors.border,
    },
  });
}

export default Card;
