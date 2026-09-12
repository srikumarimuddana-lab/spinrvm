import React, { useMemo } from 'react';
import { View, StyleSheet } from 'react-native';
import { Text } from '@shared/components/Text';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SPACING } from '@shared/utils/responsive';
import type { NavigationStep } from '@shared/utils/navigationSteps';

/**
 * Google Directions `maneuver` strings (see
 * https://developers.google.com/maps/documentation/directions/get-directions#Steps)
 * mapped to a generic directional Ionicon. Phase 1 is deliberately not lane
 * guidance (docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md
 * §3) — a plain left/right/straight/u-turn/roundabout/merge glyph set is
 * enough, no bespoke turn-arrow icon set.
 */
const MANEUVER_ICONS: Record<string, keyof typeof Ionicons.glyphMap> = {
  'turn-slight-left': 'arrow-back',
  'turn-left': 'arrow-back',
  'turn-sharp-left': 'arrow-back',
  'uturn-left': 'return-down-back',
  'turn-slight-right': 'arrow-forward',
  'turn-right': 'arrow-forward',
  'turn-sharp-right': 'arrow-forward',
  'uturn-right': 'return-down-forward',
  straight: 'arrow-up',
  merge: 'git-merge',
  'fork-left': 'git-branch',
  'fork-right': 'git-branch',
  'ramp-left': 'arrow-back',
  'ramp-right': 'arrow-forward',
  'roundabout-left': 'sync',
  'roundabout-right': 'sync',
  ferry: 'boat',
  'ferry-train': 'train',
};

export function iconForManeuver(maneuver: string | null): keyof typeof Ionicons.glyphMap {
  if (!maneuver) return 'navigate';
  return MANEUVER_ICONS[maneuver] ?? 'navigate';
}

/** "80 m" under 1km (rounded to nearest 10m to avoid a jittery readout as
 * GPS fixes arrive), "1.2 km" at or above it. */
export function formatManeuverDistance(distanceMeters: number): string {
  const m = Math.max(0, distanceMeters);
  if (m >= 1000) return `${(m / 1000).toFixed(1)} km`;
  return `${Math.round(m / 10) * 10} m`;
}

interface NavigationStepBannerProps {
  step: NavigationStep;
  distanceToManeuverMeters: number;
  topOffset: number;
}

export const NavigationStepBanner: React.FC<NavigationStepBannerProps> = ({
  step,
  distanceToManeuverMeters,
  topOffset,
}) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const distanceLabel = formatManeuverDistance(distanceToManeuverMeters);

  return (
    <View
      style={[styles.banner, { top: topOffset }]}
      pointerEvents="none"
      accessibilityRole="text"
      accessibilityLabel={`${step.instruction}, in ${distanceLabel}`}
    >
      <View style={styles.iconWrap}>
        <Ionicons name={iconForManeuver(step.maneuver)} size={26} color="#fff" />
      </View>
      <View style={styles.textWrap}>
        <Text style={styles.instruction} numberOfLines={1} allowFontScaling={false}>
          {step.instruction}
        </Text>
        <Text style={styles.distance} allowFontScaling={false}>
          {distanceLabel}
        </Text>
      </View>
    </View>
  );
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    banner: {
      position: 'absolute',
      left: 16,
      right: 16,
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.overlay,
      borderRadius: 16,
      borderWidth: 1,
      borderColor: colors.border,
      paddingHorizontal: SPACING.md,
      paddingVertical: SPACING.sm,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.15,
      shadowRadius: 10,
      elevation: 6,
      zIndex: 150,
    },
    iconWrap: {
      width: 36,
      height: 36,
      borderRadius: 18,
      backgroundColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: SPACING.sm,
    },
    textWrap: {
      flex: 1,
    },
    instruction: {
      fontSize: 15,
      fontWeight: '700',
      color: colors.text,
    },
    distance: {
      fontSize: 12,
      color: colors.textDim,
      marginTop: 2,
    },
  });
}

export default NavigationStepBanner;
