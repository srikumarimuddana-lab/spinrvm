import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
} from 'react-native';
import SafeRefreshControl from '../SafeRefreshControl';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { useDriverStore } from '../../store/driverStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../store/languageStore';

const toMoney = (s: string | number | null | undefined): string => {
  const n = Math.round((parseFloat(String(s ?? '0')) || 0) * 100) / 100;
  return n.toFixed(2);
};
const parseMoney = (s: string | number | null | undefined): number =>
  Math.round((parseFloat(String(s ?? '0')) || 0) * 100) / 100;

type Period = 'today' | 'week' | 'month' | 'all';

export default function ActivityView() {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { t } = useLanguageStore();
  const {
    earnings,
    fetchEarnings,
    fetchDriverBalance,
  } = useDriverStore();

  const [period, setPeriod] = useState<Period>('today');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      await Promise.allSettled([
        fetchEarnings(period),
        fetchDriverBalance(),
      ]);
    } catch {
      setFetchError('Could not load data. Pull down to retry.');
    }
    setLoading(false);
  }, [period]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const onRefresh = async () => {
    setRefreshing(true);
    await loadData();
    setRefreshing(false);
  };

  const totalEarnings = parseMoney(earnings?.total_earnings);
  const totalTips = parseMoney(earnings?.total_tips);
  const fareEarnings = Math.max(totalEarnings - totalTips, 0);

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{ paddingBottom: Math.max(insets.bottom, 16) + 24 }}
      showsVerticalScrollIndicator={false}
      refreshControl={<SafeRefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
    >
      {/* Period Filter Pills */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.filterWrapper} contentContainerStyle={styles.filterListContent}>
        {(['today', 'week', 'month', 'all'] as Period[]).map((item) => (
          <TouchableOpacity
            key={item}
            style={[styles.filterPill, period === item && styles.filterPillActive]}
            onPress={() => setPeriod(item)}
            activeOpacity={0.7}
          >
            <Text style={[styles.filterPillText, period === item && styles.filterPillTextActive]}>
              {item === 'all' ? t('earnings.allTime') : item === 'today' ? t('earnings.today') : item === 'week' ? t('earnings.thisWeek') : t('earnings.thisMonth')}
            </Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {fetchError && (
        <View style={styles.errorBanner} accessibilityRole="alert" accessibilityLiveRegion="polite" accessibilityLabel={fetchError}>
          <Ionicons name="alert-circle-outline" size={16} color="#fff" />
          <Text style={styles.errorBannerText}>{fetchError}</Text>
        </View>
      )}

      {loading ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 60 }} />
      ) : (
        <>
          {/* Earnings + Tips Breakdown */}
          <View style={styles.breakdownCard}>
            <View style={styles.breakdownRow}>
              <View style={styles.breakdownItem}>
                <Ionicons name="cash-outline" size={20} color={colors.primary} />
                <Text style={styles.breakdownLabel}>Fare Earnings</Text>
                <Text style={styles.breakdownValue}>${toMoney(fareEarnings)}</Text>
              </View>
              <View style={styles.breakdownDivider} />
              <View style={styles.breakdownItem}>
                <Ionicons name="gift-outline" size={20} color={colors.gold} />
                <Text style={styles.breakdownLabel}>Tips</Text>
                <Text style={[styles.breakdownValue, { color: colors.gold }]}>${toMoney(totalTips)}</Text>
              </View>
              <View style={styles.breakdownDivider} />
              <View style={styles.breakdownItem}>
                <Ionicons name="wallet" size={20} color={colors.success} />
                <Text style={styles.breakdownLabel}>Total</Text>
                <Text style={[styles.breakdownValue, { color: colors.success }]}>${toMoney(totalEarnings)}</Text>
              </View>
            </View>
          </View>

          {/* Stats Grid */}
          <View style={styles.statsGrid}>
            <View style={styles.statCard}>
              <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(239, 68, 68, 0.1)' }]}>
                <FontAwesome5 name="car" size={16} color={colors.primary} />
              </View>
              <View>
                <Text style={styles.statValue}>{earnings?.total_rides || 0}</Text>
                <Text style={styles.statLabel}>Total Trips</Text>
              </View>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(245, 158, 11, 0.1)' }]}>
                <MaterialCommunityIcons name="road-variant" size={18} color="#F59E0B" />
              </View>
              <View>
                <Text style={styles.statValue}>{parseMoney(earnings?.total_distance_km).toFixed(1)}</Text>
                <Text style={styles.statLabel}>KM Driven</Text>
              </View>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(16, 185, 129, 0.1)' }]}>
                <Ionicons name="time" size={18} color={colors.success} />
              </View>
              <View>
                <Text style={styles.statValue}>
                  {Math.round((earnings?.total_duration_minutes || 0) / 60)}h
                </Text>
                <Text style={styles.statLabel}>Online Time</Text>
              </View>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(56, 189, 248, 0.1)' }]}>
                <Ionicons name="trending-up" size={18} color="#38BDF8" />
              </View>
              <View>
                <Text style={styles.statValue}>${toMoney(earnings?.average_per_ride)}</Text>
                <Text style={styles.statLabel}>Avg per Trip</Text>
              </View>
            </View>
          </View>
        </>
      )}
    </ScrollView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
    },
    filterWrapper: {
      marginTop: 12,
      marginBottom: 4,
    },
    filterListContent: {
      paddingHorizontal: 16,
      gap: 10,
    },
    filterPill: {
      paddingHorizontal: 18,
      paddingVertical: 8,
      borderRadius: 24,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: colors.border,
    },
    filterPillActive: {
      backgroundColor: colors.primary,
      borderColor: colors.primary,
    },
    filterPillText: {
      color: colors.textDim,
      fontSize: 13,
      fontWeight: '600',
    },
    filterPillTextActive: {
      color: '#fff',
      fontWeight: '700',
    },
    errorBanner: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.error,
      paddingHorizontal: 16,
      paddingVertical: 10,
      gap: 8,
    },
    errorBannerText: {
      color: '#fff',
      fontSize: 13,
      flex: 1,
    },
    breakdownCard: {
      marginHorizontal: 16,
      marginTop: 12,
      marginBottom: 16,
      backgroundColor: colors.surface,
      borderRadius: 20,
      padding: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    breakdownRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-around',
    },
    breakdownItem: {
      flex: 1,
      alignItems: 'center',
      gap: 4,
    },
    breakdownLabel: {
      color: colors.textDim,
      fontSize: 11,
      fontWeight: '600',
      letterSpacing: 0.3,
    },
    breakdownValue: {
      color: colors.text,
      fontSize: 20,
      fontWeight: '900',
    },
    breakdownDivider: {
      width: 1,
      height: 44,
      backgroundColor: colors.border,
    },
    statsGrid: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 12,
      paddingHorizontal: 16,
      paddingTop: 8,
      marginBottom: 24,
    },
    statCard: {
      flexBasis: '47%',
      flexGrow: 1,
      backgroundColor: colors.surface,
      borderRadius: 20,
      padding: 16,
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      borderWidth: 1,
      borderColor: colors.border,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.03,
      shadowRadius: 10,
      elevation: 2,
    },
    statIconWrapper: {
      width: 36,
      height: 36,
      borderRadius: 18,
      justifyContent: 'center',
      alignItems: 'center',
    },
    statValue: {
      color: colors.text,
      fontSize: 18,
      fontWeight: '800',
    },
    statLabel: {
      color: colors.textDim,
      fontSize: 11,
      fontWeight: '600',
      letterSpacing: 0.2,
      marginTop: 1,
    },
  });
}
