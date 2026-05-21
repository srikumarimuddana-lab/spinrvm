import React, { useState, useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../../store/languageStore';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import EarningsView from '../../../components/activity/EarningsView';
import RidesView from '../../../components/activity/RidesView';

type Tab = 'earnings' | 'rides';

function ActivityScreen() {
  const [activeTab, setActiveTab] = useState<Tab>('earnings');
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

        <View style={styles.segmentContainer}>
          <TouchableOpacity
            style={[styles.segmentBtn, activeTab === 'earnings' && styles.segmentBtnActive]}
            onPress={() => setActiveTab('earnings')}
            activeOpacity={0.7}
            accessibilityRole="tab"
            accessibilityState={{ selected: activeTab === 'earnings' }}
          >
            <Ionicons
              name="wallet"
              size={16}
              color={activeTab === 'earnings' ? colors.primaryDark : 'rgba(255,255,255,0.7)'}
            />
            <Text style={[styles.segmentText, activeTab === 'earnings' && styles.segmentTextActive]}>
              {t('earnings.title')}
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.segmentBtn, activeTab === 'rides' && styles.segmentBtnActive]}
            onPress={() => setActiveTab('rides')}
            activeOpacity={0.7}
            accessibilityRole="tab"
            accessibilityState={{ selected: activeTab === 'rides' }}
          >
            <Ionicons
              name="list"
              size={16}
              color={activeTab === 'rides' ? colors.primaryDark : 'rgba(255,255,255,0.7)'}
            />
            <Text style={[styles.segmentText, activeTab === 'rides' && styles.segmentTextActive]}>
              {t('rides.myRides')}
            </Text>
          </TouchableOpacity>
        </View>
      </LinearGradient>

      {activeTab === 'earnings' ? <EarningsView /> : <RidesView />}
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
      marginBottom: 16,
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
    segmentContainer: {
      flexDirection: 'row',
      backgroundColor: 'rgba(255,255,255,0.15)',
      borderRadius: 14,
      padding: 3,
    },
    segmentBtn: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      paddingVertical: 10,
      borderRadius: 11,
    },
    segmentBtnActive: {
      backgroundColor: '#fff',
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.1,
      shadowRadius: 4,
      elevation: 3,
    },
    segmentText: {
      color: 'rgba(255,255,255,0.7)',
      fontSize: 14,
      fontWeight: '600',
    },
    segmentTextActive: {
      color: colors.primaryDark,
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
