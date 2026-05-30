import React, { useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../../store/languageStore';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import ActivityView from '../../../components/activity/ActivityView';

function ActivityScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { t } = useLanguageStore();

  return (
    <View style={styles.container}>
      <LinearGradient
        colors={[colors.primary, colors.primaryDark]}
        style={[styles.header, { paddingTop: insets.top + 12 }]}
      >
        <View style={styles.headerRow}>
          <Text style={styles.headerTitle}>Activity</Text>
          <TouchableOpacity
            style={styles.payoutBtn}
            onPress={() => router.push('/driver/payout' as any)}
            accessibilityRole="button"
            accessibilityLabel={t('earnings.payout')}
          >
            <Ionicons name="wallet-outline" size={16} color={colors.primaryDark} />
            <Text style={styles.payoutBtnText}>{t('earnings.payout')}</Text>
          </TouchableOpacity>
        </View>
      </LinearGradient>

      <ActivityView />
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.background,
    },
    header: {
      paddingHorizontal: 20,
      paddingBottom: 16,
      borderBottomLeftRadius: 28,
      borderBottomRightRadius: 28,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 8 },
      shadowOpacity: 0.15,
      shadowRadius: 16,
      elevation: 8,
      zIndex: 10,
    },
    headerRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
    },
    headerTitle: {
      color: '#fff',
      fontSize: 24,
      fontWeight: '800',
      letterSpacing: 0.3,
    },
    payoutBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: '#fff',
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderRadius: 20,
      gap: 6,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
      elevation: 3,
    },
    payoutBtnText: {
      color: colors.primaryDark,
      fontSize: 13,
      fontWeight: '700',
    },
  });
}

export default function ActivityScreenWithBoundary() {
  return (
    <ErrorBoundary>
      <ActivityScreen />
    </ErrorBoundary>
  );
}
