import React, { useMemo } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import type { DemandHeatmapTheme } from '@shared/hooks/queries';
import { rampColors } from './demandHeatmap';

/**
 * Compact legend chip for the demand-heatmap overlay.
 *
 * The overlay's bright end sits below 3:1 contrast against map tiles, so a
 * labeled legend is required relief — demand identity must never be
 * color-alone. Mirrors MapControls' floating-chip styling (overlay surface,
 * hairline border) and sits bottom-left, opposite the zoom controls.
 */
export const DemandHeatmapLegend: React.FC<{ theme?: DemandHeatmapTheme }> = ({ theme }) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  return (
    <View style={styles.container} pointerEvents="none" testID="demand-heatmap-legend">
      <Text style={styles.title}>Rider demand</Text>
      <View style={styles.scaleRow}>
        <Text style={styles.endLabel}>Low</Text>
        <LinearGradient
          colors={rampColors(theme) as [string, string, ...string[]]}
          start={{ x: 0, y: 0.5 }}
          end={{ x: 1, y: 0.5 }}
          style={styles.gradientBar}
        />
        <Text style={styles.endLabel}>High</Text>
      </View>
    </View>
  );
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      position: 'absolute',
      left: 16,
      bottom: 120,
      paddingHorizontal: 12,
      paddingVertical: 8,
      borderRadius: 16,
      backgroundColor: colors.overlay,
      borderWidth: 1,
      borderColor: colors.border,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.15,
      shadowRadius: 10,
      elevation: 6,
    },
    title: {
      fontSize: 11,
      fontWeight: '600',
      color: colors.textSecondary,
      marginBottom: 4,
    },
    scaleRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
    },
    gradientBar: {
      width: 72,
      height: 8,
      borderRadius: 4,
    },
    endLabel: {
      fontSize: 10,
      color: colors.textDim,
    },
  });
}
