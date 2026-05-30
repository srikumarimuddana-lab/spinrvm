import React, { useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../../store/languageStore';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import ActivityView from '../../../components/activity/ActivityView';

function ActivityScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { t } = useLanguageStore();

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Activity</Text>
        <TouchableOpacity
          style={styles.payoutBtn}
          onPress={() => router.push('/driver/payout' as any)}
          accessibilityRole="button"
          accessibilityLabel={t('earnings.payout')}
        >
          <Ionicons name="wallet-outline" size={16} color={colors.primary} />
          <Text style={styles.payoutBtnText}>{t('earnings.payout')}</Text>
        </TouchableOpacity>
      </View>

      <ActivityView />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surface,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 24,
      paddingVertical: 16,
    },
    headerTitle: {
      fontSize: 28,
      fontWeight: '800',
      color: colors.text,
    },
    payoutBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderRadius: 20,
      gap: 6,
      borderWidth: 1,
      borderColor: colors.border,
    },
    payoutBtnText: {
      color: colors.primary,
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
