import React, { useMemo } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import type { ForecastEntry } from '../../hooks/useDemandHeatmap';

interface ForecastStripProps {
  forecast: ForecastEntry[];
  visible: boolean;
}

function formatHour(hour: number): string {
  if (hour === 0 || hour === 24) return '12a';
  if (hour === 12) return '12p';
  return hour < 12 ? `${hour}a` : `${hour - 12}p`;
}

export const ForecastStrip: React.FC<ForecastStripProps> = ({ forecast, visible }) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  if (!visible || forecast.length === 0) return null;

  const nextPeak = forecast.find((f) => f.is_peak);

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>Next few hours</Text>
        {nextPeak && (
          <Text style={styles.peakHint}>
            Peak at {formatHour(nextPeak.hour)}
          </Text>
        )}
      </View>
      <View style={styles.barRow}>
        {forecast.map((entry, i) => (
          <View key={i} style={styles.barCol}>
            <View style={styles.barTrack}>
              <View
                style={[
                  styles.barFill,
                  {
                    height: `${Math.max(entry.demand * 100, 4)}%`,
                    backgroundColor: entry.is_peak
                      ? colors.heatmapRamp[4]
                      : colors.heatmapRamp[Math.min(Math.floor(entry.demand * 4), 3)],
                  },
                ]}
              />
            </View>
            <Text style={[styles.hourLabel, entry.is_peak && styles.hourLabelPeak]}>
              {formatHour(entry.hour)}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
};

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    container: {
      backgroundColor: colors.surface,
      borderRadius: 14,
      padding: 10,
      paddingBottom: 6,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.08,
      shadowRadius: 2,
      elevation: 1,
    },
    headerRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 6,
    },
    title: {
      fontSize: 11,
      fontWeight: '600',
      color: colors.textDim,
    },
    peakHint: {
      fontSize: 10,
      fontWeight: '500',
      color: colors.heatmapRamp[4],
    },
    barRow: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      gap: 3,
      height: 36,
    },
    barCol: {
      flex: 1,
      alignItems: 'center',
    },
    barTrack: {
      width: '100%',
      height: 28,
      justifyContent: 'flex-end',
      borderRadius: 3,
      overflow: 'hidden',
    },
    barFill: {
      width: '100%',
      borderRadius: 3,
      minHeight: 2,
    },
    hourLabel: {
      fontSize: 9,
      color: colors.textDim,
      marginTop: 2,
    },
    hourLabelPeak: {
      fontWeight: '600',
      color: colors.heatmapRamp[4],
    },
  });
