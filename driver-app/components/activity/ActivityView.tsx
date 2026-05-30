import React, { useState, useCallback, useMemo, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ScrollView,
  ActivityIndicator,
  TouchableOpacity,
} from 'react-native';
import SafeRefreshControl from '../SafeRefreshControl';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useDriverStore, type RideHistoryItem } from '../../store/driverStore';
import api from '@shared/api/client';

const PAGE_SIZE = 20;

const toMoney = (s: string | number | null | undefined): string => {
  const n = Math.round((parseFloat(String(s ?? '0')) || 0) * 100) / 100;
  return n.toFixed(2);
};
const parseMoney = (s: string | number | null | undefined): number =>
  Math.round((parseFloat(String(s ?? '0')) || 0) * 100) / 100;

type Period = 'today' | 'week' | 'month' | 'all';
type StatusFilter = 'all' | 'completed' | 'cancelled' | 'scheduled';

export default function ActivityView() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { earnings, fetchEarnings, fetchDriverBalance } = useDriverStore();

  const [period, setPeriod] = useState<Period>('today');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [rides, setRides] = useState<RideHistoryItem[]>([]);
  const [totalRides, setTotalRides] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const fetchPage = useCallback(async (offset: number) => {
    const res = await api.get<{ rides: RideHistoryItem[]; total: number }>(
      `/drivers/rides/history?limit=${PAGE_SIZE}&offset=${offset}`
    );
    return { rides: res.data?.rides ?? [], total: res.data?.total ?? 0 };
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadMoreError(null);
    try {
      const [pageResult] = await Promise.all([
        fetchPage(0),
        fetchEarnings(period),
        fetchDriverBalance(),
      ]);
      setRides(pageResult.rides);
      setTotalRides(pageResult.total);
    } catch {}
    setLoading(false);
  }, [period, fetchPage, fetchEarnings, fetchDriverBalance]);

  const lastFetchedAt = useRef(0);

  useFocusEffect(
    useCallback(() => {
      const now = Date.now();
      if (now - lastFetchedAt.current > 30_000) {
        lastFetchedAt.current = now;
        loadData();
      }
    }, [loadData])
  );

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    setLoadMoreError(null);
    try {
      const [pageResult] = await Promise.all([
        fetchPage(0),
        fetchEarnings(period),
        fetchDriverBalance(),
      ]);
      setRides(pageResult.rides);
      setTotalRides(pageResult.total);
    } catch {}
    setRefreshing(false);
  }, [period, fetchPage, fetchEarnings, fetchDriverBalance]);

  const loadMore = useCallback(async () => {
    if (loadingMore || rides.length >= totalRides || loadMoreError) return;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const result = await fetchPage(rides.length);
      setRides(prev => [...prev, ...result.rides]);
      setTotalRides(result.total);
    } catch {
      setLoadMoreError('Could not load more rides.');
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, rides.length, totalRides, loadMoreError, fetchPage]);

  const totalEarnings = parseMoney(earnings?.total_earnings);
  const totalTips = parseMoney(earnings?.total_tips);
  const totalIncentives = parseMoney(earnings?.total_incentives);
  const totalTax = parseMoney(earnings?.total_tax);
  const fareEarnings = Math.max(totalEarnings - totalTips - totalIncentives - totalTax, 0);

  const filteredRides = useMemo(() => {
    return rides.filter((r) => {
      if (statusFilter !== 'all') {
        if (statusFilter === 'scheduled' && r.status !== 'scheduled') return false;
        if (statusFilter !== 'scheduled' && r.status !== statusFilter) return false;
      }
      if (period !== 'all') {
        const dateStr = r.ride_completed_at || (r as any).cancelled_at || r.created_at;
        if (!dateStr) return false;
        const date = new Date(dateStr);
        const today = new Date();
        if (period === 'today') {
          if (date.getDate() !== today.getDate() || date.getMonth() !== today.getMonth() || date.getFullYear() !== today.getFullYear()) return false;
        } else if (period === 'week') {
          const diffDays = (today.getTime() - date.getTime()) / (1000 * 3600 * 24);
          if (diffDays > 7 || diffDays < 0) return false;
        } else if (period === 'month') {
          if (date.getMonth() !== today.getMonth() || date.getFullYear() !== today.getFullYear()) return false;
        }
      }
      return true;
    });
  }, [rides, statusFilter, period]);

  const renderRideCard = useCallback(({ item: ride }: { item: RideHistoryItem }) => {
    const isCompleted = ride.status === 'completed';
    const isCancelled = ride.status === 'cancelled';
    const statusColor = isCompleted ? colors.success : isCancelled ? colors.danger : '#f59e0b';
    const statusBg = isCompleted ? `${colors.success}1A` : isCancelled ? `${colors.danger}1A` : 'rgba(245,158,11,0.1)';
    const statusLabel = isCompleted ? 'Completed' : isCancelled ? 'Cancelled' : 'Scheduled';
    const statusIcon = isCompleted ? 'checkmark-circle' : isCancelled ? 'close-circle' : 'time';

    const date = ride.ride_completed_at || (ride as any).cancelled_at || ride.created_at;
    const tipAmount = parseMoney((ride as any).tip_amount);
    const incentiveAmount = parseMoney((ride as any).incentive_amount);
    const totalEarned = parseMoney((ride as any).total_earned);

    return (
      <TouchableOpacity
        style={styles.rideCard}
        activeOpacity={0.7}
        onPress={() => router.push(`/driver/ride-detail?id=${ride.id}` as any)}
      >
        <View style={styles.rideTopRow}>
          <View style={[styles.statusBadge, { backgroundColor: statusBg }]}>
            <Ionicons name={statusIcon as any} size={14} color={statusColor} />
            <Text style={[styles.statusText, { color: statusColor }]}>{statusLabel}</Text>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text style={styles.dateText}>
              {date ? new Date(date).toLocaleDateString('en', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
            </Text>
            <Text style={styles.rideCodeText}>
              {ride.ride_code ? String(ride.ride_code) : `#${String(ride.id).substring(0, 8).toUpperCase()}`}
            </Text>
          </View>
        </View>

        <View style={styles.routeContainer}>
          <View style={styles.routeDots}>
            <View style={[styles.dot, { backgroundColor: colors.primary }]} />
            <View style={styles.routeLine} />
            <View style={[styles.dot, { backgroundColor: colors.success }]} />
          </View>
          <View style={styles.routeAddresses}>
            <View>
              <Text style={styles.routeLabel}>PICKUP</Text>
              <Text style={styles.routeAddress} numberOfLines={1}>
                {ride.pickup_address || 'Unknown'}
              </Text>
            </View>
            <View style={{ height: 16 }} />
            <View>
              <Text style={styles.routeLabel}>DROP-OFF</Text>
              <Text style={styles.routeAddress} numberOfLines={1}>
                {ride.dropoff_address || 'Unknown'}
              </Text>
            </View>
          </View>
        </View>

        <View style={styles.rideBottomRow}>
          <View style={styles.tripMeta}>
            {ride.distance_km != null && (
              <View style={styles.metaBadge}>
                <Ionicons name="map-outline" size={13} color={colors.textDim} />
                <Text style={styles.metaText}>{ride.distance_km.toFixed(1)} km</Text>
              </View>
            )}
            {ride.duration_minutes != null && (
              <View style={styles.metaBadge}>
                <Ionicons name="time-outline" size={13} color={colors.textDim} />
                <Text style={styles.metaText}>{ride.duration_minutes} min</Text>
              </View>
            )}
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            {isCompleted ? (
              <>
                <Text style={styles.fareAmount}>${toMoney(totalEarned)}</Text>
                {(tipAmount > 0 || incentiveAmount > 0) && (
                  <Text style={styles.tipText}>
                    {[
                      tipAmount > 0 ? `$${toMoney(tipAmount)} tip` : '',
                      incentiveAmount > 0 ? `$${toMoney(incentiveAmount)} bonus` : '',
                    ].filter(Boolean).join(' + ')}
                  </Text>
                )}
              </>
            ) : isCancelled ? (
              parseMoney((ride as any).cancel_fee_earned) > 0 ? (
                <>
                  <Text style={[styles.fareAmount, { color: '#f59e0b' }]}>
                    +${toMoney((ride as any).cancel_fee_earned)}
                  </Text>
                  <Text style={styles.cancelFeeText}>
                    {(ride as any).cancellation_type === 'noshow' ? 'No-show fee' : 'Cancel fee'}
                  </Text>
                </>
              ) : (
                <Text style={[styles.fareAmount, { color: colors.textDim, fontSize: 16 }]}>$0.00</Text>
              )
            ) : (
              <Text style={styles.fareAmount}>Est. ${toMoney(parseMoney((ride as any).driver_earnings))}</Text>
            )}
          </View>
        </View>
      </TouchableOpacity>
    );
  }, [colors, styles, router]);

  const ListHeader = useMemo(() => (
    <>
      {/* Period pills */}
      <View style={styles.pillRow}>
        {(['today', 'week', 'month', 'all'] as Period[]).map((item) => (
          <TouchableOpacity
            key={item}
            style={[styles.pill, period === item && styles.pillActive]}
            onPress={() => setPeriod(item)}
          >
            <Text style={[styles.pillText, period === item && styles.pillTextActive]}>
              {item === 'all' ? 'All Time' : item === 'today' ? 'Today' : item === 'week' ? 'This Week' : 'This Month'}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {loading ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 60, marginBottom: 60 }} />
      ) : (
        <>
          {/* Earnings breakdown */}
          <View style={styles.card}>
            <View style={styles.totalRow}>
              <Text style={styles.totalLabel}>Total Earned</Text>
              <Text style={styles.totalValue}>${toMoney(totalEarnings)}</Text>
            </View>
            <View style={styles.breakdownRow}>
              <View style={styles.breakdownItem}>
                <Ionicons name="cash-outline" size={18} color={colors.primary} />
                <Text style={styles.label}>Fare</Text>
                <Text style={styles.value}>${toMoney(fareEarnings)}</Text>
              </View>
              <View style={styles.divider} />
              <View style={styles.breakdownItem}>
                <Ionicons name="gift-outline" size={18} color="#f59e0b" />
                <Text style={styles.label}>Tips</Text>
                <Text style={[styles.value, { color: '#f59e0b' }]}>${toMoney(totalTips)}</Text>
              </View>
              <View style={styles.divider} />
              <View style={styles.breakdownItem}>
                <Ionicons name="flash" size={18} color="#8b5cf6" />
                <Text style={styles.label}>Bonus</Text>
                <Text style={[styles.value, { color: '#8b5cf6' }]}>${toMoney(totalIncentives)}</Text>
              </View>
              <View style={styles.divider} />
              <View style={styles.breakdownItem}>
                <Ionicons name="receipt-outline" size={18} color={colors.textDim} />
                <Text style={styles.label}>Tax</Text>
                <Text style={[styles.value, { color: colors.textDim }]}>${toMoney(totalTax)}</Text>
              </View>
            </View>
          </View>

          {/* Stats grid */}
          <View style={styles.statsGrid}>
            <View style={styles.statCard}>
              <View style={[styles.iconWrap, { backgroundColor: `${colors.primary}1A` }]}>
                <FontAwesome5 name="car" size={16} color={colors.primary} />
              </View>
              <View>
                <Text style={styles.statValue}>{earnings?.total_rides || 0}</Text>
                <Text style={styles.statLabel}>Total Trips</Text>
              </View>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.iconWrap, { backgroundColor: 'rgba(245,158,11,0.1)' }]}>
                <MaterialCommunityIcons name="road-variant" size={18} color="#f59e0b" />
              </View>
              <View>
                <Text style={styles.statValue}>{parseMoney(earnings?.total_distance_km).toFixed(1)}</Text>
                <Text style={styles.statLabel}>KM Driven</Text>
              </View>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.iconWrap, { backgroundColor: `${colors.success}1A` }]}>
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
              <View style={[styles.iconWrap, { backgroundColor: 'rgba(56,189,248,0.1)' }]}>
                <Ionicons name="trending-up" size={18} color="#38bdf8" />
              </View>
              <View>
                <Text style={styles.statValue}>${toMoney(earnings?.average_per_ride)}</Text>
                <Text style={styles.statLabel}>Avg per Trip</Text>
              </View>
            </View>
          </View>

          {/* Section header + status pills */}
          <View style={styles.ridesSection}>
            <View style={styles.ridesSectionHeader}>
              <Text style={styles.sectionTitle}>Your Rides</Text>
              <Text style={styles.rideCount}>{filteredRides.length} rides</Text>
            </View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.statusPillRow}>
              {(['all', 'completed', 'scheduled', 'cancelled'] as StatusFilter[]).map((item) => (
                <TouchableOpacity
                  key={item}
                  style={[styles.statusPill, statusFilter === item && styles.statusPillActive]}
                  onPress={() => setStatusFilter(item)}
                >
                  <Text style={[styles.statusPillText, statusFilter === item && styles.statusPillTextActive]}>
                    {item === 'all' ? 'All Status' : item.charAt(0).toUpperCase() + item.slice(1)}
                  </Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </>
      )}
    </>
  ), [period, statusFilter, loading, totalEarnings, fareEarnings, totalTips, totalIncentives, totalTax, earnings, filteredRides.length, colors, styles]);

  const ListFooter = useMemo(() => {
    if (loadingMore) {
      return (
        <View style={styles.footerLoader}>
          <ActivityIndicator color={colors.primary} />
          <Text style={styles.footerText}>Loading more rides...</Text>
        </View>
      );
    }
    if (loadMoreError) {
      return (
        <TouchableOpacity style={styles.footerError} onPress={() => { setLoadMoreError(null); loadMore(); }}>
          <Text style={styles.footerErrorText}>{loadMoreError}</Text>
          <Text style={styles.footerRetry}>Tap to retry</Text>
        </TouchableOpacity>
      );
    }
    if (!loading && filteredRides.length > 0 && rides.length >= totalRides) {
      return (
        <Text style={styles.footerEnd}>You've reached the end</Text>
      );
    }
    return null;
  }, [loadingMore, loadMoreError, loading, filteredRides.length, rides.length, totalRides, colors, styles, loadMore]);

  const ListEmpty = useMemo(() => {
    if (loading) return null;
    return (
      <View style={styles.emptyState}>
        <Ionicons name="car-sport-outline" size={48} color={colors.surfaceLight} />
        <Text style={styles.emptyTitle}>No Rides Found</Text>
        <Text style={styles.emptyDesc}>No rides match this filter for the selected period.</Text>
      </View>
    );
  }, [loading, colors, styles]);

  return (
    <FlatList
      style={styles.container}
      contentContainerStyle={styles.content}
      data={loading ? [] : filteredRides}
      renderItem={renderRideCard}
      keyExtractor={(item) => item.id ?? ''}
      ListHeaderComponent={ListHeader}
      ListFooterComponent={ListFooter}
      ListEmptyComponent={ListEmpty}
      onEndReached={loadMore}
      onEndReachedThreshold={0.3}
      showsVerticalScrollIndicator={false}
      initialNumToRender={10}
      maxToRenderPerBatch={10}
      windowSize={5}
      removeClippedSubviews={true}
      refreshControl={
        <SafeRefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          tintColor={colors.primary}
        />
      }
    />
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
    },
    content: {
      paddingBottom: 40,
    },
    pillRow: {
      flexDirection: 'row',
      paddingHorizontal: 16,
      gap: 10,
      marginTop: 12,
      marginBottom: 4,
    },
    pill: {
      paddingHorizontal: 18,
      paddingVertical: 8,
      borderRadius: 24,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1,
      borderColor: colors.border,
    },
    pillActive: {
      backgroundColor: colors.primary,
      borderColor: colors.primary,
    },
    pillText: {
      color: colors.textDim,
      fontSize: 13,
      fontWeight: '600',
    },
    pillTextActive: {
      color: '#fff',
      fontSize: 13,
      fontWeight: '700',
    },
    card: {
      marginHorizontal: 16,
      marginTop: 12,
      marginBottom: 16,
      backgroundColor: colors.surface,
      borderRadius: 20,
      padding: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    totalRow: {
      alignItems: 'center',
      marginBottom: 14,
      paddingBottom: 14,
      borderBottomWidth: 1,
      borderBottomColor: colors.surfaceLight,
    },
    totalLabel: {
      color: colors.textDim,
      fontSize: 12,
      fontWeight: '600',
      marginBottom: 4,
    },
    totalValue: {
      color: colors.success,
      fontSize: 28,
      fontWeight: '900',
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
    label: {
      color: colors.textDim,
      fontSize: 11,
      fontWeight: '600',
    },
    value: {
      color: colors.text,
      fontSize: 20,
      fontWeight: '900',
    },
    divider: {
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
    },
    iconWrap: {
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
      marginTop: 1,
    },
    ridesSection: {
      paddingHorizontal: 16,
    },
    ridesSectionHeader: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 12,
    },
    sectionTitle: {
      color: colors.text,
      fontSize: 18,
      fontWeight: '800',
    },
    rideCount: {
      color: colors.textDim,
      fontSize: 13,
      fontWeight: '600',
    },
    statusPillRow: {
      gap: 8,
      marginBottom: 16,
    },
    statusPill: {
      paddingHorizontal: 14,
      paddingVertical: 6,
      borderRadius: 20,
      backgroundColor: colors.surfaceLight,
      borderWidth: 1,
      borderColor: colors.border,
    },
    statusPillActive: {
      backgroundColor: colors.text,
      borderColor: colors.text,
    },
    statusPillText: {
      color: colors.textDim,
      fontSize: 12,
      fontWeight: '600',
    },
    statusPillTextActive: {
      color: colors.surface,
      fontWeight: '700',
    },
    rideCard: {
      backgroundColor: colors.surface,
      borderRadius: 20,
      padding: 18,
      marginHorizontal: 16,
      marginBottom: 14,
      borderWidth: 1,
      borderColor: colors.border,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.04,
      shadowRadius: 12,
      elevation: 2,
    },
    rideTopRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 16,
    },
    statusBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      paddingHorizontal: 10,
      paddingVertical: 5,
      borderRadius: 12,
    },
    statusText: {
      fontSize: 12,
      fontWeight: '700',
    },
    dateText: {
      color: colors.textDim,
      fontSize: 12,
      fontWeight: '500',
    },
    rideCodeText: {
      color: colors.textDim,
      fontSize: 10,
      fontWeight: '600',
      marginTop: 2,
      letterSpacing: 0.5,
    },
    routeContainer: {
      flexDirection: 'row',
      marginBottom: 16,
    },
    routeDots: {
      alignItems: 'center',
      width: 20,
      paddingTop: 4,
    },
    dot: {
      width: 10,
      height: 10,
      borderRadius: 5,
    },
    routeLine: {
      width: 2,
      flex: 1,
      backgroundColor: colors.border,
      marginVertical: 4,
    },
    routeAddresses: {
      flex: 1,
      marginLeft: 8,
    },
    routeLabel: {
      fontSize: 10,
      fontWeight: '700',
      color: colors.textDim,
      letterSpacing: 1,
      marginBottom: 2,
    },
    routeAddress: {
      fontSize: 14,
      fontWeight: '600',
      color: colors.text,
    },
    rideBottomRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'flex-end',
      paddingTop: 14,
      borderTopWidth: 1,
      borderTopColor: colors.surfaceLight,
    },
    tripMeta: {
      flexDirection: 'row',
      gap: 10,
    },
    metaBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      backgroundColor: colors.surfaceLight,
      paddingHorizontal: 8,
      paddingVertical: 5,
      borderRadius: 8,
    },
    metaText: {
      color: colors.textDim,
      fontSize: 12,
      fontWeight: '600',
    },
    fareAmount: {
      fontSize: 20,
      fontWeight: '900',
      color: colors.success,
    },
    tipText: {
      color: '#f59e0b',
      fontSize: 11,
      fontWeight: '700',
      marginTop: 2,
    },
    cancelFeeText: {
      color: '#f59e0b',
      fontSize: 11,
      fontWeight: '600',
      marginTop: 2,
    },
    emptyState: {
      alignItems: 'center',
      paddingVertical: 40,
    },
    emptyTitle: {
      fontSize: 18,
      fontWeight: '800',
      color: colors.text,
      marginTop: 16,
      marginBottom: 8,
    },
    emptyDesc: {
      fontSize: 13,
      color: colors.textDim,
      textAlign: 'center',
    },
    footerLoader: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      paddingVertical: 20,
    },
    footerText: {
      color: colors.textDim,
      fontSize: 13,
    },
    footerError: {
      alignItems: 'center',
      paddingVertical: 16,
    },
    footerErrorText: {
      color: colors.danger,
      fontSize: 13,
    },
    footerRetry: {
      color: colors.primary,
      fontSize: 13,
      fontWeight: '600',
      marginTop: 4,
    },
    footerEnd: {
      textAlign: 'center',
      color: colors.textDim,
      fontSize: 12,
      paddingVertical: 16,
    },
  });
}
