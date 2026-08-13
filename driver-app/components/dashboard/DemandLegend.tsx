import React, { useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Modal,
  Pressable,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

type HeatmapStatus = 'loading' | 'empty' | 'stale' | 'ready' | 'error';

interface DemandLegendProps {
  status: HeatmapStatus;
  visible: boolean;
}

export const DemandLegend: React.FC<DemandLegendProps> = ({
  status,
  visible,
}) => {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [infoOpen, setInfoOpen] = useState(false);

  if (!visible) return null;

  if (status === 'loading') {
    return (
      <View style={styles.pill}>
        <View style={styles.shimmerRow}>
          {colors.heatmapRamp.map((_, i) => (
            <View key={i} style={[styles.swatch, { backgroundColor: colors.border }]} />
          ))}
        </View>
      </View>
    );
  }

  if (status === 'empty') {
    return (
      <View style={styles.pill}>
        <Ionicons name="map-outline" size={14} color={colors.textDim} />
        <Text style={styles.statusText}>No busy areas right now</Text>
      </View>
    );
  }

  if (status === 'error') {
    return (
      <View style={styles.pill}>
        <Ionicons name="cloud-offline-outline" size={14} color={colors.textDim} />
        <Text style={styles.statusText}>Demand info unavailable</Text>
      </View>
    );
  }

  return (
    <>
      <View style={styles.pill}>
        <Text style={styles.label}>Quiet</Text>
        <View style={styles.rampRow}>
          {colors.heatmapRamp.map((color, i) => (
            <View key={i} style={[styles.swatch, { backgroundColor: color }]} />
          ))}
        </View>
        <Text style={styles.label}>Busy</Text>
        {status === 'stale' && (
          <Ionicons name="time-outline" size={12} color={colors.warning} style={{ marginLeft: 4 }} />
        )}
        <TouchableOpacity onPress={() => setInfoOpen(true)} hitSlop={8} style={{ marginLeft: 6 }}>
          <Ionicons name="information-circle-outline" size={16} color={colors.textDim} />
        </TouchableOpacity>
      </View>

      <Modal visible={infoOpen} transparent animationType="fade" onRequestClose={() => setInfoOpen(false)}>
        <Pressable style={styles.overlay} onPress={() => setInfoOpen(false)}>
          <View style={styles.sheet}>
            <Text style={styles.sheetTitle}>Demand in your area</Text>
            <Text style={styles.sheetBody}>
              Based on recent rider activity in your area. Where you drive is always your choice.
            </Text>
            <TouchableOpacity style={styles.sheetClose} onPress={() => setInfoOpen(false)}>
              <Text style={styles.sheetCloseText}>Got it</Text>
            </TouchableOpacity>
          </View>
        </Pressable>
      </Modal>
    </>
  );
};

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    pill: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      borderRadius: 20,
      paddingHorizontal: 12,
      paddingVertical: 6,
      gap: 6,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.1,
      shadowRadius: 3,
      elevation: 2,
    },
    rampRow: {
      flexDirection: 'row',
      gap: 2,
    },
    shimmerRow: {
      flexDirection: 'row',
      gap: 2,
      opacity: 0.4,
    },
    swatch: {
      width: 16,
      height: 10,
      borderRadius: 2,
    },
    label: {
      fontSize: 11,
      color: colors.textDim,
      fontWeight: '500',
    },
    statusText: {
      fontSize: 12,
      color: colors.textDim,
      fontWeight: '500',
    },
    overlay: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: 'rgba(0,0,0,0.4)',
    },
    sheet: {
      backgroundColor: colors.surface,
      borderRadius: 16,
      padding: 24,
      margin: 32,
      maxWidth: 340,
    },
    sheetTitle: {
      fontSize: 17,
      fontWeight: '600',
      color: colors.text,
      marginBottom: 12,
    },
    sheetBody: {
      fontSize: 15,
      color: colors.textSecondary,
      lineHeight: 22,
      marginBottom: 20,
    },
    sheetClose: {
      alignSelf: 'flex-end',
      paddingHorizontal: 16,
      paddingVertical: 8,
      backgroundColor: colors.primary,
      borderRadius: 8,
    },
    sheetCloseText: {
      color: '#FFFFFF',
      fontWeight: '600',
      fontSize: 15,
    },
  });
