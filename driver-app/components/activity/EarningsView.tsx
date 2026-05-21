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
import { useRouter } from 'expo-router';
import api from '@shared/api/client';
import { useDriverStore } from '../../store/driverStore';
import EarningsBarChart from '../charts/EarningsBarChart';
import EarningsLineChart from '../charts/EarningsLineChart';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useLanguageStore } from '../../store/languageStore';

const toMoney = (s: string | number | null | undefined): string => {
  const n = Math.round((parseFloat(String(s ?? '0')) || 0) * 100) / 100;
  return n.toFixed(2);
};
const parseMoney = (s: string | number | null | undefined): number =>
  Math.round((parseFloat(String(s ?? '0')) || 0) * 100) / 100;

const safeLocaleDateString = (
  dateStr: string | undefined | null,
  options: Intl.DateTimeFormatOptions,
  fallback: string = ''
): string => {
  if (!dateStr) return fallback;
  try {
    const cleanStr = dateStr.includes('T') ? dateStr : dateStr + 'T00:00:00';
    const d = new Date(cleanStr);
    if (isNaN(d.getTime())) return fallback;
    return d.toLocaleDateString('en', options);
  } catch {
    return fallback;
  }
};

type Period = 'today' | 'week' | 'month' | 'all';
type ChartMode = 'daily' | 'weekly' | 'monthly';

export default function EarningsView() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { t } = useLanguageStore();
  const {
    earnings,
    dailyEarnings,
    weeklyEarnings,
    monthlyEarnings,
    earningsComparison,
    rideHistory,
    fetchEarnings,
    fetchDailyEarnings,
    fetchWeeklyEarnings,
    fetchMonthlyEarnings,
    fetchEarningsComparison,
    fetchRideHistory,
    fetchDriverBalance,
  } = useDriverStore();

  const [period, setPeriod] = useState<Period>('today');
  const [chartMode, setChartMode] = useState<ChartMode>('daily');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [forecast, setForecast] = useState<{
    this_week_earnings: string | number;
    projected_weekly_total: string | number;
    days_remaining_this_week: number;
    this_week_trips: number;
  } | null>(null);

  useEffect(() => {
    api
      .get<{ this_week_earnings: number; projected_weekly_total: number; days_remaining_this_week: number; this_week_trips: number }>('/drivers/earnings/forecast')
      .then((r) => setForecast(r.data))
      .catch(() => {});
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const results = await Promise.allSettled([
        fetchEarnings(period),
        fetchDailyEarnings(period === 'today' ? 1 : period === 'week' ? 7 : period === 'month' ? 30 : 30),
        fetchWeeklyEarnings(4),
        fetchMonthlyEarnings(6),
        fetchEarningsComparison(period === 'month' ? 'month' : 'week'),
        fetchRideHistory(50, 0),
        fetchDriverBalance(),
      ]);
      const anyFailed = results.some((r) => r.status === 'rejected');
      if (anyFailed) setFetchError('Some data could not load. Pull down to retry.');
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

  const recentRides = rideHistory.slice(0, 20);

  const barChartData = (dailyEarnings ?? []).map((d) => ({
    label: safeLocaleDateString(d.date, { weekday: 'narrow' }),
    value: parseMoney(d.earnings),
    secondary: parseMoney(d.tips),
  }));

  const weeklyChartData = (weeklyEarnings ?? []).map((w) => ({
    label: safeLocaleDateString(w.week_start, { month: 'short', day: 'numeric' }),
    value: parseMoney(w.earnings),
  }));

  const monthlyChartData = (monthlyEarnings ?? []).map((m) => {
    const monthStr = m.month ? (m.month.includes('-01') ? m.month : m.month + '-01') : '';
    return {
      label: safeLocaleDateString(monthStr, { month: 'short' }),
      value: parseMoney(m.earnings),
    };
  });

  const compPct = Number(earningsComparison?.change_pct?.earnings ?? 0);
  const compLabel = earningsComparison?.period === 'month' ? 'last month' : 'last week';

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

      {/* Earnings + Tips Breakdown */}
      <View style={styles.breakdownCard}>
          <View style={styles.breakdownRow}>
            <View style={styles.breakdownItem}>
              <Ionicons name="cash-outline" size={20} color={colors.primary} />
              <Text style={styles.breakdownLabel}>Fare Earnings</Text>
              <Text style={styles.breakdownValue}>${loading ? '--' : toMoney(fareEarnings)}</Text>
            </View>
            <View style={styles.breakdownDivider} />
            <View style={styles.breakdownItem}>
              <Ionicons name="gift-outline" size={20} color={colors.gold} />
              <Text style={styles.breakdownLabel}>Tips</Text>
              <Text style={[styles.breakdownValue, { color: colors.gold }]}>${loading ? '--' : toMoney(totalTips)}</Text>
            </View>
            <View style={styles.breakdownDivider} />
            <View style={styles.breakdownItem}>
              <Ionicons name="wallet" size={20} color={colors.success} />
              <Text style={styles.breakdownLabel}>Total</Text>
              <Text style={[styles.breakdownValue, { color: colors.success }]}>${loading ? '--' : toMoney(totalEarnings)}</Text>
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
              <Text style={styles.statValue}>{loading ? '--' : (earnings?.total_rides || 0)}</Text>
              <Text style={styles.statLabel}>Total Trips</Text>
            </View>
          </View>
          <View style={styles.statCard}>
            <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(245, 158, 11, 0.1)' }]}>
              <MaterialCommunityIcons name="road-variant" size={18} color="#F59E0B" />
            </View>
            <View>
              <Text style={styles.statValue}>{loading ? '--' : parseMoney(earnings?.total_distance_km).toFixed(1)}</Text>
              <Text style={styles.statLabel}>KM Driven</Text>
            </View>
          </View>
          <View style={styles.statCard}>
            <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(16, 185, 129, 0.1)' }]}>
              <Ionicons name="time" size={18} color={colors.success} />
            </View>
            <View>
              <Text style={styles.statValue}>
                {loading ? '--' : Math.round((earnings?.total_duration_minutes || 0) / 60)}h
              </Text>
              <Text style={styles.statLabel}>Online Time</Text>
            </View>
          </View>
          <View style={styles.statCard}>
            <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(56, 189, 248, 0.1)' }]}>
              <Ionicons name="trending-up" size={18} color="#38BDF8" />
            </View>
            <View>
              <Text style={styles.statValue}>${loading ? '--' : toMoney(earnings?.average_per_ride)}</Text>
              <Text style={styles.statLabel}>Avg per Trip</Text>
            </View>
          </View>
        </View>

        {/* Recent Trips List — uses rideHistory (same data source as Rides tab) */}
        <View style={styles.tripsSection}>
          <Text style={styles.sectionTitle}>Recent Trips</Text>
          {loading ? (
            <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
          ) : recentRides.length === 0 ? (
            <View style={styles.emptyStateContainer}>
              <View style={styles.emptyIconCircle}>
                <Ionicons name="wallet-outline" size={48} color={colors.primary} />
              </View>
              <Text style={styles.emptyStateTitle}>No trips to show</Text>
              <Text style={styles.emptyStateDesc}>Complete rides to start seeing your earnings breakdown here.</Text>
            </View>
          ) : (
            recentRides.map((ride) => {
              const isCompleted = ride.status === 'completed';
              const isCancelled = ride.status === 'cancelled';
              let statusColor = '#F59E0B';
              let statusBg = 'rgba(245, 158, 11, 0.1)';
              let statusLabel = 'Scheduled';
              let statusIcon: string = 'time';
              if (isCompleted) {
                statusColor = '#10B981';
                statusBg = 'rgba(16, 185, 129, 0.1)';
                statusLabel = 'Completed';
                statusIcon = 'checkmark-circle';
              } else if (isCancelled) {
                statusColor = '#EF4444';
                statusBg = 'rgba(239, 68, 68, 0.1)';
                statusLabel = 'Cancelled';
                statusIcon = 'close-circle';
              }
              const date = ride.ride_completed_at || (ride as any).cancelled_at || ride.created_at;
              const fareAmount = (ride as any).driver_earnings || ride.total_fare || 0;
              const tipAmount = parseMoney((ride as any).tip_amount);
              return (
                <TouchableOpacity
                  key={ride.id}
                  style={styles.rideCard}
                  activeOpacity={0.8}
                  onPress={() => router.push(`/driver/ride-detail?id=${ride.id}` as any)}
                >
                  <View style={styles.cardHeader}>
                    <View style={[styles.statusBadge, { backgroundColor: statusBg }]}>
                      <Ionicons name={statusIcon as any} size={14} color={statusColor} />
                      <Text style={[styles.statusText, { color: statusColor }]}>{statusLabel}</Text>
                    </View>
                    <View style={{ alignItems: 'flex-end' }}>
                      <Text style={styles.dateText}>
                        {date ? new Date(date).toLocaleDateString('en', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
                      </Text>
                      <Text style={styles.bookingIdText}>
                        {ride.ride_code ? String(ride.ride_code) : `ID: #${String(ride.id).substring(0, 8).toUpperCase()}`}
                      </Text>
                    </View>
                  </View>

                  <View style={styles.routeContainer}>
                    <View style={styles.timelineIndicators}>
                      <View style={[styles.dot, { backgroundColor: colors.primary }]} />
                      <View style={styles.timelineLine} />
                      <View style={[styles.dot, { backgroundColor: '#EF4444' }]} />
                    </View>
                    <View style={styles.routeDetails}>
                      <View style={styles.routePoint}>
                        <Text style={styles.routeLabel}>PICKUP</Text>
                        <Text style={styles.routeAddress} numberOfLines={1}>
                          {ride.pickup_address || 'Unknown Pickup Location'}
                        </Text>
                      </View>
                      <View style={styles.routePointSpacer} />
                      <View style={styles.routePoint}>
                        <Text style={styles.routeLabel}>DROP-OFF</Text>
                        <Text style={styles.routeAddress} numberOfLines={1}>
                          {ride.dropoff_address || 'Unknown Dropoff Location'}
                        </Text>
                      </View>
                    </View>
                  </View>

                  <View style={styles.cardFooter}>
                    <View style={styles.tripMetaRow}>
                      {ride.distance_km && (
                        <View style={styles.metaBadge}>
                          <Ionicons name="map-outline" size={14} color={colors.textDim} />
                          <Text style={styles.metaText}>{ride.distance_km.toFixed(1)} km</Text>
                        </View>
                      )}
                      {ride.duration_minutes && (
                        <View style={styles.metaBadge}>
                          <Ionicons name="time-outline" size={14} color={colors.textDim} />
                          <Text style={styles.metaText}>{ride.duration_minutes} min</Text>
                        </View>
                      )}
                    </View>
                    <View style={styles.fareContainer}>
                      {isCompleted ? (
                        <>
                          <Text style={styles.fareLabel}>Earned</Text>
                          <Text style={styles.fareText}>${toMoney(fareAmount)}</Text>
                          {tipAmount > 0 && (
                            <Text style={styles.tipAmountText}>+ ${toMoney(tipAmount)} tip</Text>
                          )}
                        </>
                      ) : isCancelled ? (
                        <Text style={[styles.fareText, { color: colors.textDim, fontSize: 16 }]}>$0.00</Text>
                      ) : (
                        <Text style={styles.fareText}>Est. ${toMoney(fareAmount)}</Text>
                      )}
                    </View>
                  </View>
                </TouchableOpacity>
              );
            })
          )}
        </View>

        {!loading && earningsComparison && compPct !== 0 && (
          <View style={styles.comparisonBanner}>
            <Ionicons
              name={compPct > 0 ? 'trending-up' : 'trending-down'}
              size={18}
              color={compPct > 0 ? colors.success : colors.danger}
            />
            <Text style={[styles.comparisonText, { color: compPct > 0 ? colors.success : colors.danger }]}>
              {compPct > 0 ? '+' : ''}{compPct.toFixed(1)}% from {compLabel}
            </Text>
          </View>
        )}

        {forecast && (
          <View style={styles.forecastCard}>
            <View style={styles.forecastHeader}>
              <Ionicons name="trending-up" size={16} color="#10B981" />
              <Text style={styles.forecastTitle}>This Week's Projection</Text>
            </View>
            <View style={styles.forecastBody}>
              <View style={styles.forecastStat}>
                <Text style={styles.forecastAmount}>${toMoney(forecast.this_week_earnings)}</Text>
                <Text style={styles.forecastLabel}>Earned so far</Text>
              </View>
              <View style={styles.forecastDivider} />
              <View style={styles.forecastStat}>
                <Text style={[styles.forecastAmount, { color: '#10B981' }]}>${toMoney(forecast.projected_weekly_total)}</Text>
                <Text style={styles.forecastLabel}>Projected total</Text>
              </View>
              <View style={styles.forecastDivider} />
              <View style={styles.forecastStat}>
                <Text style={styles.forecastAmount}>{forecast.days_remaining_this_week}</Text>
                <Text style={styles.forecastLabel}>Days left</Text>
              </View>
            </View>
          </View>
        )}

        {!loading && (
          <View style={styles.chartSection}>
            <View style={styles.chartHeader}>
              <Text style={styles.sectionTitle}>Earnings Breakdown</Text>
              <View style={styles.chartModeToggle}>
                {(['daily', 'weekly', 'monthly'] as ChartMode[]).map((mode) => (
                  <TouchableOpacity
                    key={mode}
                    style={[styles.chartModeBtn, chartMode === mode && styles.chartModeBtnActive]}
                    onPress={() => setChartMode(mode)}
                  >
                    <Text style={[styles.chartModeBtnText, chartMode === mode && styles.chartModeBtnTextActive]}>
                      {mode.charAt(0).toUpperCase() + mode.slice(1)}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
            <View style={styles.chartCard}>
              {chartMode === 'daily' && (
                <EarningsBarChart data={barChartData} height={200} primaryColor={colors.primary} secondaryColor={colors.gold} />
              )}
              {chartMode === 'weekly' && (
                <EarningsLineChart data={weeklyChartData} height={200} color={colors.primary} showArea />
              )}
              {chartMode === 'monthly' && (
                <EarningsLineChart data={monthlyChartData} height={200} color={colors.success} showArea />
              )}
            </View>
          </View>
        )}
    </ScrollView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
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
    comparisonBanner: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      marginHorizontal: 16,
      marginBottom: 16,
      backgroundColor: colors.surface,
      paddingHorizontal: 16,
      paddingVertical: 12,
      borderRadius: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    comparisonText: {
      fontSize: 14,
      fontWeight: '700',
    },
    chartSection: {
      paddingHorizontal: 16,
      marginBottom: 24,
    },
    chartHeader: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 16,
    },
    sectionTitle: {
      color: colors.text,
      fontSize: 18,
      fontWeight: '800',
      letterSpacing: 0.2,
    },
    chartModeToggle: {
      flexDirection: 'row',
      backgroundColor: colors.surfaceLight,
      borderRadius: 10,
      padding: 2,
    },
    chartModeBtn: {
      paddingHorizontal: 10,
      paddingVertical: 5,
      borderRadius: 8,
    },
    chartModeBtnActive: {
      backgroundColor: colors.surface,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.1,
      shadowRadius: 2,
      elevation: 2,
    },
    chartModeBtnText: {
      fontSize: 11,
      fontWeight: '600',
      color: colors.textDim,
    },
    chartModeBtnTextActive: {
      color: colors.text,
      fontWeight: '700',
    },
    chartCard: {
      backgroundColor: colors.surface,
      borderRadius: 24,
      paddingHorizontal: 8,
      paddingVertical: 16,
      borderWidth: 1,
      borderColor: colors.border,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 6 },
      shadowOpacity: 0.04,
      shadowRadius: 16,
      elevation: 3,
    },
    tripsSection: {
      paddingHorizontal: 16,
      paddingBottom: 20,
    },
    rideCard: {
      backgroundColor: colors.surface,
      borderRadius: 24,
      padding: 20,
      marginBottom: 16,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 6 },
      shadowOpacity: 0.04,
      shadowRadius: 16,
      elevation: 3,
      borderWidth: 1,
      borderColor: colors.border,
    },
    cardHeader: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 20,
    },
    statusBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      paddingHorizontal: 12,
      paddingVertical: 6,
      borderRadius: 16,
    },
    statusText: {
      fontSize: 12,
      fontWeight: '700',
      letterSpacing: 0.3,
    },
    dateText: {
      color: colors.textDim,
      fontSize: 13,
      fontWeight: '500',
    },
    bookingIdText: {
      color: colors.textDim,
      fontSize: 11,
      fontWeight: '600',
      marginTop: 2,
      letterSpacing: 0.5,
    },
    routeContainer: {
      flexDirection: 'row',
      marginBottom: 20,
    },
    timelineIndicators: {
      alignItems: 'center',
      width: 24,
      paddingTop: 4,
    },
    dot: {
      width: 10,
      height: 10,
      borderRadius: 5,
    },
    timelineLine: {
      width: 2,
      flex: 1,
      backgroundColor: colors.border,
      marginVertical: 4,
    },
    routeDetails: {
      flex: 1,
    },
    routePoint: {
      justifyContent: 'center',
    },
    routePointSpacer: {
      height: 24,
    },
    routeLabel: {
      fontSize: 10,
      fontWeight: '700',
      color: colors.textDim,
      letterSpacing: 1,
      marginBottom: 2,
    },
    routeAddress: {
      fontSize: 15,
      fontWeight: '600',
      color: colors.text,
    },
    cardFooter: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'flex-end',
      paddingTop: 16,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    tripMetaRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
    },
    metaBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 8,
      paddingVertical: 6,
      borderRadius: 8,
    },
    metaText: {
      color: colors.textDim,
      fontSize: 13,
      fontWeight: '600',
    },
    fareContainer: {
      alignItems: 'flex-end',
    },
    fareLabel: {
      fontSize: 11,
      color: colors.textDim,
      fontWeight: '600',
      marginBottom: 2,
    },
    fareText: {
      fontSize: 22,
      fontWeight: '900',
      color: colors.primary,
      letterSpacing: -0.5,
    },
    tipAmountText: {
      color: colors.gold,
      fontSize: 11,
      fontWeight: '700',
      marginTop: 2,
    },
    forecastCard: {
      marginHorizontal: 16,
      marginBottom: 16,
      borderRadius: 16,
      backgroundColor: colors.surface,
      borderWidth: 1,
      borderColor: 'rgba(16, 185, 129, 0.2)',
      padding: 14,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.06,
      shadowRadius: 6,
      elevation: 2,
    },
    forecastHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
      marginBottom: 10,
    },
    forecastTitle: {
      fontSize: 13,
      fontWeight: '700',
      color: colors.text,
    },
    forecastBody: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-around',
    },
    forecastStat: {
      alignItems: 'center',
      flex: 1,
    },
    forecastAmount: {
      fontSize: 18,
      fontWeight: '800',
      color: colors.text,
    },
    forecastLabel: {
      fontSize: 11,
      color: colors.textDim,
      marginTop: 2,
    },
    forecastDivider: {
      width: 1,
      height: 36,
      backgroundColor: colors.border,
    },
    emptyStateContainer: {
      alignItems: 'center',
      paddingVertical: 40,
      paddingHorizontal: 20,
    },
    emptyIconCircle: {
      width: 80,
      height: 80,
      borderRadius: 40,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 20,
    },
    emptyStateTitle: {
      fontSize: 20,
      fontWeight: '800',
      color: colors.text,
      marginBottom: 10,
    },
    emptyStateDesc: {
      fontSize: 14,
      color: colors.textDim,
      textAlign: 'center',
      lineHeight: 22,
    },
  });
}
