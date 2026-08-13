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
import type { HeatmapLayer, HeatmapStatus } from '../../hooks/useDemandHeatmap';
import { useLanguageStore } from '../../store/languageStore';

// Status type is imported from the hook rather than redeclared. The local
// copy had drifted (no 'disabled'/'idle'), which forced the call site to
// launder the value through a ternary and would have silently mistyped any
// future state.

interface DemandLegendProps {
  status: HeatmapStatus;
  visible: boolean;
  isV2?: boolean;
  layer?: HeatmapLayer;
  onLayerChange?: (layer: HeatmapLayer) => void;
}

const LAYER_OPTIONS: { key: HeatmapLayer; labelKey: string }[] = [
  // i18n keys, not literals — resolved via t() at render so the segmented
  // control translates with the rest of the app.
  { key: 'blend', labelKey: 'heatmap.layer.all' },
  { key: 'live', labelKey: 'heatmap.layer.now' },
  { key: 'baseline', labelKey: 'heatmap.layer.usual' },
  { key: 'scheduled', labelKey: 'heatmap.layer.soon' },
];

export const DemandLegend: React.FC<DemandLegendProps> = ({
  status,
  visible,
  isV2 = false,
  layer = 'blend',
  onLayerChange,
}) => {
  const { colors } = useTheme();
  const { t } = useLanguageStore();
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
        <Text style={styles.statusText}>{t('heatmap.empty')}</Text>
      </View>
    );
  }

  if (status === 'error') {
    return (
      <View style={styles.pill}>
        <Ionicons name="cloud-offline-outline" size={14} color={colors.textDim} />
        <Text style={styles.statusText}>{t('heatmap.unavailable')}</Text>
      </View>
    );
  }

  return (
    <>
      <View style={styles.container}>
        <View style={styles.pill}>
          <Text style={styles.label}>{t('heatmap.legend.quiet')}</Text>
          <View style={styles.rampRow}>
            {colors.heatmapRamp.map((color, i) => (
              <View key={i} style={[styles.swatch, { backgroundColor: color }]} />
            ))}
          </View>
          <Text style={styles.label}>{t('heatmap.legend.busy')}</Text>
          {status === 'stale' && (
            <Ionicons name="time-outline" size={12} color={colors.warning} style={{ marginLeft: 4 }} />
          )}
          <TouchableOpacity onPress={() => setInfoOpen(true)} hitSlop={8} style={{ marginLeft: 6 }}>
            <Ionicons name="information-circle-outline" size={16} color={colors.textDim} />
          </TouchableOpacity>
        </View>

        {isV2 && onLayerChange && (
          <View style={styles.segmentRow}>
            {LAYER_OPTIONS.map((opt) => {
              const active = layer === opt.key;
              return (
                <TouchableOpacity
                  key={opt.key}
                  style={[styles.segment, active && styles.segmentActive]}
                  onPress={() => onLayerChange(opt.key)}
                  activeOpacity={0.7}
                >
                  <Text style={[styles.segmentText, active && styles.segmentTextActive]}>
                    {t(opt.labelKey)}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        )}
      </View>

      <Modal visible={infoOpen} transparent animationType="fade" onRequestClose={() => setInfoOpen(false)}>
        <Pressable style={styles.overlay} onPress={() => setInfoOpen(false)}>
          <View style={styles.sheet}>
            <Text style={styles.sheetTitle}>{t('heatmap.info.title')}</Text>
            <Text style={styles.sheetBody}>
              {t('heatmap.info.body')}
            </Text>
            {isV2 && (
              <Text style={styles.sheetBody}>
                {t('heatmap.info.layers')}
              </Text>
            )}
            <TouchableOpacity style={styles.sheetClose} onPress={() => setInfoOpen(false)}>
              <Text style={styles.sheetCloseText}>{t('heatmap.gotIt')}</Text>
            </TouchableOpacity>
          </View>
        </Pressable>
      </Modal>
    </>
  );
};

const createStyles = (colors: ThemeColors) =>
  StyleSheet.create({
    container: {
      alignItems: 'center',
      gap: 4,
    },
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
    segmentRow: {
      flexDirection: 'row',
      backgroundColor: colors.surface,
      borderRadius: 14,
      padding: 2,
      gap: 2,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.08,
      shadowRadius: 2,
      elevation: 1,
    },
    segment: {
      paddingHorizontal: 10,
      paddingVertical: 4,
      borderRadius: 12,
    },
    segmentActive: {
      backgroundColor: colors.primary,
    },
    segmentText: {
      fontSize: 11,
      fontWeight: '500',
      color: colors.textDim,
    },
    segmentTextActive: {
      color: '#FFFFFF',
      fontWeight: '600',
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
