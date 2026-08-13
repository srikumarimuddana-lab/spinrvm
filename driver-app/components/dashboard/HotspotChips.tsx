import React, { useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import type { Hotspot } from '../../hooks/useDemandHeatmap';

interface HotspotChipsProps {
  hotspots: Hotspot[];
  visible: boolean;
  onPress?: (lat: number, lng: number) => void;
}

const HOTSPOT_LABELS = ['High demand', 'Busy area', 'Busy area'];

export const HotspotChips: React.FC<HotspotChipsProps> = ({ hotspots, visible, onPress }) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  if (!visible || hotspots.length === 0) return null;

  return (
    <View style={styles.row}>
      {hotspots.map((spot, i) => (
        <TouchableOpacity
          key={i}
          style={[styles.chip, spot.intensity === 'high' && styles.chipHigh]}
          activeOpacity={0.7}
          onPress={() => onPress?.(spot.lat, spot.lng)}
        >
          <Ionicons
            name="flame"
            size={12}
            color={spot.intensity === 'high' ? colors.heatmapRamp[4] : colors.heatmapRamp[2]}
          />
          <Text style={[styles.chipText, spot.intensity === 'high' && styles.chipTextHigh]}>
            {HOTSPOT_LABELS[i] ?? 'Busy area'}
          </Text>
        </TouchableOpacity>
      ))}
    </View>
  );
};

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    row: {
      flexDirection: 'row',
      gap: 6,
      flexWrap: 'wrap',
    },
    chip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      backgroundColor: colors.surface,
      borderRadius: 16,
      paddingHorizontal: 10,
      paddingVertical: 5,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.08,
      shadowRadius: 2,
      elevation: 1,
    },
    chipHigh: {
      borderWidth: 1,
      borderColor: colors.heatmapRamp[3],
    },
    chipText: {
      fontSize: 11,
      fontWeight: '500',
      color: colors.textDim,
    },
    chipTextHigh: {
      fontWeight: '600',
      color: colors.text,
    },
  });
