import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  FlatList,
  Platform,
  ActivityIndicator,
  RefreshControl,
  Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

type Filter = 'all' | 'completed' | 'cancelled' | 'scheduled';
type PeriodFilter = 'today' | 'week' | 'month' | 'all';

export default function RidesScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { rideHistory, fetchRideHistory } = useDriverStore();
  const [filter, setFilter] = useState<Filter>('all');
  const [period, setPeriod] = useState<PeriodFilter>('all');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    await fetchRideHistory(50, 0);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const onRefresh = async () => {
    setRefreshing(true);
    await fetchRideHistory(50, 0);
    setRefreshing(false);
  };

  const filteredRides = rideHistory.filter((r) => {
    // Status Filter
    if (filter !== 'all') {
      if (filter === 'scheduled' && r.status !== 'scheduled') return false;
      if (filter !== 'scheduled' && r.status !== filter) return false;
    }

    // Period Filter
    if (period !== 'all') {
      const dateStr = r.ride_completed_at || r.cancelled_at || r.created_at;
      if (!dateStr) return false;

      const date = new Date(dateStr);
      const today = new Date();

      if (period === 'today') {
        if (
          date.getDate() !== today.getDate() ||
          date.getMonth() !== today.getMonth() ||
          date.getFullYear() !== today.getFullYear()
        ) {
          return false;
        }
      } else if (period === 'week') {
        const diffDays = (today.getTime() - date.getTime()) / (1000 * 3600 * 24);
        if (diffDays > 7 || diffDays < 0) return false;
      } else if (period === 'month') {
        if (
          date.getMonth() !== today.getMonth() ||
          date.getFullYear() !== today.getFullYear()
        ) {
          return false;
        }
      }
    }

    return true;
  });

  const periodCompletedRides = filteredRides.filter((r) => r.status === 'completed').length;

  const renderRideCard = ({ item, index }: { item: any; index: number }) => {
    const isCompleted = item.status === 'completed';
    const isScheduled = item.status === 'scheduled';
    const isCancelled = item.status === 'cancelled';

    let statusColor = '#F59E0B';
    let statusBg = 'rgba(245, 158, 11, 0.1)';
    let statusLabel = 'Scheduled';
    let statusIcon = 'time';

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

    const date = item.ride_completed_at || item.cancelled_at || item.created_at;
    const formattedDate = date
      ? new Date(date).toLocaleDateString('en', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
      : '';

    return (
      <TouchableOpacity
        style={styles.rideCard}
        activeOpacity={0.8}
        onPress={() => router.push(`/driver/ride-detail?id=${item.id}` as any)}
      >
        {/* Top Header */}
        <View style={styles.cardHeader}>
          <View style={[styles.statusBadge, { backgroundColor: statusBg }]}>
            <Ionicons name={statusIcon as any} size={14} color={statusColor} />
            <Text style={[styles.statusText, { color: statusColor }]}>{statusLabel}</Text>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text style={styles.dateText}>{formattedDate}</Text>
            <Text style={styles.bookingIdText}>ID: #{String(item.id).substring(0, 8).toUpperCase()}</Text>
          </View>
        </View>

        {/* Route Timeline */}
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
                {item.pickup_address || 'Unknown Pickup Location'}
              </Text>
            </View>
            <View style={styles.routePointSpacer} />
            <View style={styles.routePoint}>
              <Text style={styles.routeLabel}>DROP-OFF</Text>
              <Text style={styles.routeAddress} numberOfLines={1}>
                {item.dropoff_address || 'Unknown Dropoff Location'}
              </Text>
            </View>
          </View>
        </View>

        {/* Footer info breakdown */}
        <View style={styles.cardFooter}>
          <View style={styles.tripMetaRow}>
            {item.distance_km && (
              <View style={styles.metaBadge}>
                <Ionicons name="map-outline" size={14} color={colors.textDim} />
                <Text style={styles.metaText}>{item.distance_km.toFixed(1)} km</Text>
              </View>
            )}
            {item.duration_minutes && (
              <View style={styles.metaBadge}>
                <Ionicons name="time-outline" size={14} color={colors.textDim} />
                <Text style={styles.metaText}>{item.duration_minutes} min</Text>
              </View>
            )}
          </View>

          <View style={styles.fareContainer}>
            {isCompleted ? (
              <>
                <Text style={styles.fareLabel}>Earned</Text>
                <Text style={styles.fareText}>
                  ${(item.driver_earnings || item.total_fare || 0).toFixed(2)}
                </Text>
              </>
            ) : isCancelled ? (
                <Text style={[styles.fareText, { color: colors.textDim, fontSize: 16 }]}>$0.00</Text>
            ) : (
                <Text style={styles.fareText}>
                  Est. ${(item.total_fare || 0).toFixed(2)}
                </Text>
            )}
          </View>
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <View style={styles.container}>
      {/* Rich Header Background */}
      <LinearGradient
        colors={[colors.primary, colors.primaryDark]}
        style={[styles.headerHero, { paddingTop: insets.top + 20 }]}
      >
        <View style={styles.headerTop}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#fff" />
          </TouchableOpacity>
          <Text style={styles.headerHeroTitle}>My Rides</Text>
          <View style={{ width: 40 }} />
        </View>

        <View style={styles.summaryBox}>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryLabel}>TOTAL COMPLETED</Text>
            <Text style={styles.summaryValue}>{loading ? '--' : periodCompletedRides}</Text>
          </View>
          <View style={styles.summaryDivider} />
          <View style={styles.summaryItem}>
            <Text style={styles.summaryLabel}>IN PERIOD</Text>
            <Text style={styles.summaryValue}>{loading ? '--' : filteredRides.length}</Text>
          </View>
        </View>
      </LinearGradient>

      {/* Modern Pill Filters */}
      <View style={styles.filterWrapper}>
        <FlatList
          horizontal
          showsHorizontalScrollIndicator={false}
          data={['today', 'week', 'month', 'all'] as PeriodFilter[]}
          contentContainerStyle={[styles.filterListContent, { marginBottom: 12 }]}
          keyExtractor={(item) => item}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.filterPill, period === item && styles.filterPillActive]}
              onPress={() => setPeriod(item)}
              activeOpacity={0.7}
            >
              <Text style={[styles.filterPillText, period === item && styles.filterPillTextActive]}>
                {item === 'all' ? 'All Time' : item === 'today' ? 'Today' : item === 'week' ? 'This Week' : 'This Month'}
              </Text>
            </TouchableOpacity>
          )}
        />
        <FlatList
          horizontal
          showsHorizontalScrollIndicator={false}
          data={['all', 'completed', 'scheduled', 'cancelled'] as Filter[]}
          contentContainerStyle={styles.filterListContent}
          keyExtractor={(item) => item}
          renderItem={({ item }) => (
            <TouchableOpacity
              style={[styles.filterPill, filter === item && styles.filterPillActive]}
              onPress={() => setFilter(item)}
              activeOpacity={0.7}
            >
              <Text style={[styles.filterPillText, filter === item && styles.filterPillTextActive]}>
                {item === 'all' ? 'All Status' : item.charAt(0).toUpperCase() + item.slice(1)}
              </Text>
            </TouchableOpacity>
          )}
        />
      </View>

      {/* Content List */}
      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>Fetching rides...</Text>
        </View>
      ) : (
        <FlatList
          data={filteredRides}
          renderItem={renderRideCard}
          keyExtractor={(item) => item.id || Math.random().toString()}
          contentContainerStyle={styles.listContent}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
          }
          ListEmptyComponent={
            <View style={styles.emptyStateContainer}>
              <View style={styles.emptyIconCircle}>
                <Ionicons name="car-sport-outline" size={48} color={colors.primary} />
              </View>
              <Text style={styles.emptyStateTitle}>No Rides Found</Text>
              <Text style={styles.emptyStateDesc}>
                There are no rides matching this filter criteria at the moment.
              </Text>
            </View>
          }
        />
      )}
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.background,
    },
    // Header Hero
    headerHero: {
      paddingHorizontal: 20,
      paddingBottom: 30,
      borderBottomLeftRadius: 32,
      borderBottomRightRadius: 32,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 10 },
      shadowOpacity: 0.2,
      shadowRadius: 20,
      elevation: 10,
      zIndex: 10,
    },
    headerTop: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 24,
    },
    backButton: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: 'rgba(255,255,255,0.2)',
      justifyContent: 'center',
      alignItems: 'center',
    },
    headerHeroTitle: {
      color: '#fff',
      fontSize: 22,
      fontWeight: '800',
      letterSpacing: 0.5,
    },
    summaryBox: {
      flexDirection: 'row',
      backgroundColor: 'rgba(255,255,255,0.15)',
      borderRadius: 20,
      paddingVertical: 20,
      paddingHorizontal: 16,
      borderWidth: 1,
      borderColor: 'rgba(255,255,255,0.3)',
    },
    summaryItem: {
      flex: 1,
      alignItems: 'center',
    },
    summaryDivider: {
      width: 1,
      backgroundColor: 'rgba(255,255,255,0.2)',
      marginHorizontal: 10,
    },
    summaryLabel: {
      color: 'rgba(255,255,255,0.8)',
      fontSize: 11,
      fontWeight: '700',
      letterSpacing: 1,
      marginBottom: 6,
    },
    summaryValue: {
      color: '#fff',
      fontSize: 28,
      fontWeight: '900',
    },
    // Filters
    filterWrapper: {
      marginTop: 16,
      marginBottom: 8,
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
    // List Area
    loadingContainer: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
      marginTop: 40,
    },
    loadingText: {
      marginTop: 12,
      color: colors.textDim,
      fontSize: 15,
      fontWeight: '500',
    },
    listContent: {
      paddingHorizontal: 16,
      paddingBottom: 40,
      paddingTop: 8,
    },
    // Ride Card Modern
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
    // Timeline
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
    // Footer
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
    // Empty state
    emptyStateContainer: {
      alignItems: 'center',
      paddingVertical: 60,
      paddingHorizontal: 30,
    },
    emptyIconCircle: {
      width: 90,
      height: 90,
      borderRadius: 45,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 20,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 8 },
      shadowOpacity: 0.1,
      shadowRadius: 16,
      elevation: 4,
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
