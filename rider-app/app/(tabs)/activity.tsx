import React, { useState, useMemo, useCallback, useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  useWindowDimensions,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useFocusEffect } from 'expo-router/react-navigation';
import SkeletonBox from '../../components/SkeletonBox';
import { useRideStore } from '../../store/rideStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import api from '@shared/api/client';
import { useTranslation } from '../../i18n';
import type { FareBreakdownLine } from '../../store/walletStore';

interface RideHistory {
  id: string;
  ride_code?: string;
  pickup_address: string;
  dropoff_address: string;
  total_fare: number | string;
  grand_total?: number | string;
  tip_amount?: number | string;
  discount_amount?: number | string;
  promo_code?: string;
  distance_km: number;
  duration_minutes: number;
  status: string;
  vehicle_type_id: string;
  created_at: string;
  corporate_account_id?: string | null;
  scheduled_time?: string;
  fare_breakdown?: FareBreakdownLine[];
  // Non-empty only for rides carried over from the previous app during the
  // one-time legacy migration (backend/services/booking_import_service.py).
  // Presence, not contents, is all this screen needs — see A30 Finding 4,
  // docs/audit/2026-08-13-migrated-data-visibility-audit.md.
  legacy_import_metadata?: Record<string, unknown> | null;
}

type FilterType = 'all' | 'personal' | 'business';
type TabType = 'history' | 'upcoming';
type Period = 'week' | 'month' | 'all';

interface RiderStats {
  period: string;
  total_rides: number;
  total_distance_km: number;
  total_saved: string;
  co2_saved_kg: number;
}

const PAGE_LIMIT = 20;
// Module-level so it's a stable reference for the useCallback/useEffect deps
// below (was a per-render `const` inside the component, which is why the
// exhaustive-deps rule flagged it as "missing" even though its value never
// changes — moving it out fixes the lint without altering behavior).
const ACTIVITY_TTL_MS = 30 * 1000;

export default function ActivityScreen() {
  const router = useRouter();
  const { scheduledRides, fetchScheduledRides } = useRideStore();
  const [rides, setRides] = useState<RideHistory[]>([]);
  const [vehicleTypes, setVehicleTypes] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<FilterType>('all');
  const [activeTab, setActiveTab] = useState<TabType>('history');
  const [period, setPeriod] = useState<Period>('all');
  const [stats, setStats] = useState<RiderStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const { colors } = useTheme();
  const { width } = useWindowDimensions();
  const isCompactFilterLayout = width < 380;
  const styles = useMemo(
    () => createStyles(colors, isCompactFilterLayout),
    [colors, isCompactFilterLayout]
  );
  const { t } = useTranslation();

  const fetchPage = useCallback(async (cursor?: string) => {
    const url = cursor
      ? `/rides/history?limit=${PAGE_LIMIT}&before=${cursor}`
      : `/rides/history?limit=${PAGE_LIMIT}`;
    const res = await api.get<{ rides: RideHistory[]; next_cursor: string | null }>(url);
    return { rides: res.data?.rides ?? [], next_cursor: res.data?.next_cursor ?? null };
  }, []);

  const statsGenRef = useRef(0);

  const fetchStats = useCallback(async (p: Period) => {
    const gen = ++statsGenRef.current;
    setStatsLoading(true);
    try {
      const res = await api.get<RiderStats>(`/rides/stats?period=${p}`);
      if (gen !== statsGenRef.current) return; // superseded by a newer request
      setStats(res.data);
    } catch {
      // non-fatal — stats card simply stays empty
    } finally {
      if (gen === statsGenRef.current) setStatsLoading(false);
    }
  }, []);

  const fetchData = useCallback(async () => {
    setFetchError(null);
    setLoadMoreError(null);
    try {
      const [pageResult, typesRes] = await Promise.all([
        fetchPage(),
        api.get('/vehicle-types').catch(() => ({ data: [] })),
      ]);
      setRides(pageResult.rides);
      setNextCursor(pageResult.next_cursor);

      const typesMap: Record<string, string> = {};
      ((typesRes.data as unknown[]) || []).forEach((t: any) => {
        typesMap[t.id] = t.name;
      });
      setVehicleTypes(typesMap);
    } catch {
      setFetchError('Could not load rides. Pull to refresh.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [fetchPage]);

  const loadMore = useCallback(async (forceRetry = false) => {
    if (loadingMore || !nextCursor || (loadMoreError && !forceRetry)) return;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const pageResult = await fetchPage(nextCursor);
      setRides(prev => [...prev, ...pageResult.rides]);
      setNextCursor(pageResult.next_cursor);
    } catch {
      setLoadMoreError('Could not load more rides.');
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, nextCursor, loadMoreError, fetchPage]);

  const lastFetchedAt = useRef<number>(0);
  const lastStatsFetchedAt = useRef<number>(0);

  const refreshStats = useCallback((p: Period, force = false) => {
    const now = Date.now();
    if (!force && now - lastStatsFetchedAt.current <= ACTIVITY_TTL_MS) return;
    lastStatsFetchedAt.current = now;
    fetchStats(p);
  }, [fetchStats]);

  // Re-fetch stats whenever period pill changes without duplicating the initial
  // focus fetch. Duplicate stats calls make the tab feel like it stops/starts on
  // slower phones or cellular networks.
  useEffect(() => { refreshStats(period, true); }, [period, refreshStats]);

  useFocusEffect(
    useCallback(() => {
      const now = Date.now();
      if (now - lastFetchedAt.current > ACTIVITY_TTL_MS) {
        lastFetchedAt.current = now;
        fetchData();
        refreshStats(period);
        fetchScheduledRides();
      }
    }, [fetchData, refreshStats, fetchScheduledRides, period])
  );

  const onRefresh = () => {
    setRefreshing(true);
    fetchData();
    refreshStats(period, true);
    fetchScheduledRides();
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const isYesterday = new Date(now.getTime() - 86400000).toDateString() === date.toDateString();

    if (isToday) {
      return `Today, ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    } else if (isYesterday) {
      return `Yesterday, ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    } else {
      return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
        `, ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    }
  };

  const getStatusColor = (status: string): string => {
    switch (status) {
      case 'completed': return '#10B981';
      case 'cancelled': return '#999';
      case 'in_progress': return '#3B82F6';
      default: return '#FFB800';
    }
  };

  // Wrapped in useCallback (instead of a plain function re-created every
  // render) so renderItem below can safely list these as dependencies:
  // useTranslation()'s `t` is a fresh closure each render, so without this,
  // adding `getStatusText` to renderItem's deps as-is would have made
  // renderItem re-create on every render too.
  const getStatusText = useCallback((status: string) => {
    switch (status) {
      case 'completed': return t('activity.completed');
      case 'cancelled': return t('activity.cancelled');
      default: return t('activity.completed');
    }
  }, [t]);

  const getRideIcon = (status: string) => {
    if (status === 'cancelled') return 'close-circle-outline';
    return 'checkmark-circle';
  };

  const getVehicleType = useCallback((id: string) => {
    return vehicleTypes[id] || 'Standard';
  }, [vehicleTypes]);

  const handleRidePress = useCallback((ride: RideHistory) => {
    router.push({ pathname: '/ride-details', params: { rideId: ride.id } } as any);
  }, [router]);

  const filteredRides = useMemo(() => rides.filter(ride => {
    if (filter === 'all') return true;
    if (filter === 'business') return !!ride.corporate_account_id;
    if (filter === 'personal') return !ride.corporate_account_id;
    return true;
  }), [rides, filter]);

  const sections = useMemo(() => {
    const groups: { title: string; data: RideHistory[] }[] = [];
    const keyMap: Record<string, RideHistory[]> = {};
    const keyOrder: string[] = [];
    const now = new Date();

    filteredRides.forEach(ride => {
      const date = new Date(ride.created_at);
      const isRecent = (now.getTime() - date.getTime()) < 7 * 24 * 60 * 60 * 1000;
      const key = isRecent ? 'RECENT' : date.toLocaleDateString([], { month: 'long' }).toUpperCase();

      if (!keyMap[key]) {
        keyMap[key] = [];
        keyOrder.push(key);
      }
      keyMap[key].push(ride);
    });

    keyOrder.forEach(key => groups.push({ title: key, data: keyMap[key] }));
    return groups;
  }, [filteredRides]);

  type ListItem =
    | { type: 'header'; key: string; title: string }
    | { type: 'ride'; key: string; ride: RideHistory };

  const listItems = useMemo((): ListItem[] => {
    const items: ListItem[] = [];
    sections.forEach(section => {
      items.push({ type: 'header', key: `h-${section.title}`, title: section.title });
      section.data.forEach(ride => items.push({ type: 'ride', key: ride.id, ride }));
    });
    return items;
  }, [sections]);

  const renderItem = useCallback(({ item }: { item: ListItem }) => {
    if (item.type === 'header') {
      return <Text style={styles.monthHeader}>{item.title}</Text>;
    }
    const { ride } = item;
    const fb = ride.fare_breakdown || [];
    const tipLine = fb.find(l => l.type === 'tip');
    const discountLine = fb.find(l => l.type === 'discount');
    const grandTotal = parseFloat(String(ride.grand_total ?? ride.total_fare ?? 0)) || 0;
    const tip = tipLine ? Math.abs(parseFloat(String(tipLine.amount ?? 0))) : 0;
    const discount = discountLine ? Math.abs(parseFloat(String(discountLine.amount ?? 0))) : 0;
    // grand_total from the backend already includes tip (via _sum_fare_breakdown),
    // so don't add tip again. Only add it if fare_breakdown has no tip line
    // (legacy rides where grand_total came from the DB column without tip).
    const total = (fb.length > 0 ? grandTotal : grandTotal + tip).toFixed(2);
    return (
      <TouchableOpacity
        style={styles.rideCard}
        onPress={() => handleRidePress(ride)}
        accessibilityRole="button"
        accessibilityLabel={`${getStatusText(ride.status)} ride to ${ride.dropoff_address || 'unknown destination'}, $${total}`}
      >
        <View style={[styles.rideIcon, { backgroundColor: '#FFF0F0' }]}>
          <Ionicons
            name={getRideIcon(ride.status) as any}
            size={20}
            color={colors.primary}
          />
        </View>

        <View style={styles.rideDetails}>
          <Text style={styles.rideDestination} numberOfLines={1}>
            {ride.dropoff_address || 'Unknown destination'}
          </Text>
          <Text style={styles.rideInfo}>
            {formatDate(ride.created_at)} · {getVehicleType(ride.vehicle_type_id)}
          </Text>
          {(ride.ride_code || ride.id) && (
            <Text style={styles.rideBookingId} numberOfLines={1}>
              {ride.ride_code || ride.id.slice(0, 8).toUpperCase()}
            </Text>
          )}
          {!!ride.legacy_import_metadata && Object.keys(ride.legacy_import_metadata).length > 0 && (
            <View style={styles.legacyBadge}>
              <Text style={styles.legacyBadgeText}>Imported from your previous account</Text>
            </View>
          )}
        </View>

        <View style={styles.rideFareContainer}>
          <Text style={[styles.rideFare, ride.status === 'cancelled' && styles.rideFareCancelled]} allowFontScaling={false}>
            ${total}
          </Text>
          {ride.status !== 'cancelled' && (tip > 0 || discount > 0) && (
            <Text style={styles.rideFareBreakdown} numberOfLines={1}>
              {[
                tip > 0 ? `Tip $${tip.toFixed(2)}` : '',
                discount > 0 ? `Saved $${discount.toFixed(2)}` : '',
              ].filter(Boolean).join(' · ')}
            </Text>
          )}
          <View style={styles.rideStatusContainer}>
            <View style={[styles.statusDot, { backgroundColor: getStatusColor(ride.status) }]} />
            <Text style={[styles.rideStatus, { color: getStatusColor(ride.status) }]} allowFontScaling={false}>
              {getStatusText(ride.status)}
            </Text>
          </View>
        </View>
      </TouchableOpacity>
    );
  }, [styles, colors, getStatusText, getVehicleType, handleRidePress]);

  const ListFooter = loadingMore ? (
    <View style={styles.listFooter}>
      <ActivityIndicator size="small" color={colors.primary} />
      <Text style={styles.listFooterText}>Loading more rides...</Text>
    </View>
  ) : loadMoreError ? (
    <TouchableOpacity
      style={styles.listFooter}
      onPress={() => loadMore(true)}
      accessibilityRole="button"
      accessibilityLabel="Retry loading more rides"
    >
      <Text style={[styles.listFooterText, { color: '#EF4444' }]}>{loadMoreError}</Text>
      <Text style={styles.listFooterAction}>Tap to retry</Text>
    </TouchableOpacity>
  ) : !nextCursor && rides.length > 0 ? (
    <View style={styles.listFooter}>
      <Text style={styles.listFooterText}>No more rides</Text>
    </View>
  ) : null;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>{t('activity.title')}</Text>
      </View>

      <View style={styles.tabRow} accessibilityRole="tablist">
        <TouchableOpacity
          style={[styles.tab, activeTab === 'history' && styles.tabActive]}
          onPress={() => setActiveTab('history')}
          accessibilityRole="tab"
          accessibilityState={{ selected: activeTab === 'history' }}
        >
          <Text style={[styles.tabText, activeTab === 'history' && styles.tabTextActive]}>{t('activity.history_tab')}</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.tab, activeTab === 'upcoming' && styles.tabActive]}
          onPress={() => setActiveTab('upcoming')}
          accessibilityRole="tab"
          accessibilityState={{ selected: activeTab === 'upcoming' }}
        >
          <Text style={[styles.tabText, activeTab === 'upcoming' && styles.tabTextActive]}>
            {t('activity.upcoming_tab')}{scheduledRides.length > 0 ? ` (${scheduledRides.length})` : ''}
          </Text>
        </TouchableOpacity>
      </View>

      {activeTab === 'history' && (
        <>
          {/* Period pills — narrowest-first (matches the driver app's activity
              filters): recent activity is the common case; lifetime is last. */}
          <View style={styles.periodPillRow}>
            {([
              { key: 'week',  label: 'This Week' },
              { key: 'month', label: 'This Month' },
              { key: 'all',   label: 'All Time' },
            ] as { key: Period; label: string }[]).map(({ key, label }) => (
              <TouchableOpacity
                key={key}
                style={[styles.periodPill, period === key && styles.periodPillActive]}
                onPress={() => setPeriod(key)}
                accessibilityRole="radio"
                accessibilityState={{ checked: period === key }}
              >
                <Text
                  style={[styles.periodPillText, period === key && styles.periodPillTextActive]}
                  numberOfLines={1}
                >
                  {label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Stats summary card */}
          <View style={styles.statsCard}>
            {statsLoading ? (
              <View style={styles.statsRow}>
                {[0, 1, 2, 3].map(i => (
                  <View key={i} style={styles.statItem}>
                    <SkeletonBox width={44} height={18} style={{ marginBottom: 4 }} />
                    <SkeletonBox width={32} height={12} />
                  </View>
                ))}
              </View>
            ) : (
              <View style={styles.statsRow}>
                <View style={styles.statItem}>
                  <Text style={styles.statValue}>{stats?.total_rides ?? 0}</Text>
                  <Text style={styles.statLabel}>Trips</Text>
                </View>
                <View style={styles.statDivider} />
                <View style={styles.statItem}>
                  <Text style={styles.statValue}>{stats?.total_distance_km?.toFixed(1) ?? '0.0'}</Text>
                  <Text style={styles.statLabel}>km</Text>
                </View>
                <View style={styles.statDivider} />
                <View style={styles.statItem}>
                  <Text style={[styles.statValue, { color: '#10B981' }]}>${stats?.total_saved ?? '0.00'}</Text>
                  <Text style={styles.statLabel}>Saved</Text>
                </View>
                <View style={styles.statDivider} />
                <View style={styles.statItem}>
                  <Text style={[styles.statValue, { color: '#22C55E' }]}>{stats?.co2_saved_kg?.toFixed(1) ?? '0.0'}</Text>
                  <Text style={styles.statLabel}>kg CO₂</Text>
                </View>
              </View>
            )}
          </View>

          <View style={styles.filterTabs}>
            {(['all', 'personal', 'business'] as FilterType[]).map(f => (
              <TouchableOpacity
                key={f}
                style={[styles.filterTab, filter === f && styles.filterTabActive]}
                onPress={() => setFilter(f as FilterType)}
                accessibilityRole="radio"
                accessibilityState={{ checked: filter === f }}
              >
                <Text
                  style={[styles.filterTabText, filter === f && styles.filterTabTextActive]}
                  numberOfLines={1}
                >
                  {f.charAt(0).toUpperCase() + f.slice(1)}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {fetchError ? (
            <ScrollView
              style={styles.content}
              contentContainerStyle={styles.emptyState}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
            >
              <Ionicons name="alert-circle-outline" size={48} color="#EF4444" />
              <Text style={[styles.emptyTitle, { color: '#EF4444' }]}>Could not load rides</Text>
              <Text style={styles.emptyText}>Pull down to refresh.</Text>
            </ScrollView>
          ) : loading && rides.length === 0 ? (
            <View style={{ paddingHorizontal: 16, paddingTop: 8 }}>
              {[0, 1, 2, 3].map((i) => (
                <View key={i} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: '#F0F0F0' }}>
                  <SkeletonBox width={40} height={40} borderRadius={20} style={{ marginRight: 12 }} />
                  <View style={{ flex: 1, gap: 8 }}>
                    <SkeletonBox width="60%" height={14} />
                    <SkeletonBox width="40%" height={12} />
                  </View>
                  <View style={{ alignItems: 'flex-end', gap: 8 }}>
                    <SkeletonBox width={50} height={14} />
                    <SkeletonBox width={40} height={12} />
                  </View>
                </View>
              ))}
            </View>
          ) : rides.length === 0 && !loading ? (
            <ScrollView
              style={styles.content}
              contentContainerStyle={styles.emptyState}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
            >
              <View style={styles.emptyIconContainer}>
                <Ionicons name="car-outline" size={48} color="#CCC" />
              </View>
              <Text style={styles.emptyTitle}>{t('activity.no_rides')}</Text>
              <Text style={styles.emptyText}>{t('activity.no_rides_subtitle')}</Text>
            </ScrollView>
          ) : (
            <FlatList
              data={listItems}
              keyExtractor={item => item.key}
              renderItem={renderItem}
              style={styles.content}
              contentContainerStyle={styles.contentContainer}
              showsVerticalScrollIndicator={false}
              onEndReached={() => loadMore()}
              onEndReachedThreshold={0.3}
              ListFooterComponent={ListFooter}
              refreshControl={
                <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
              }
            />
          )}
        </>
      )}

      {activeTab === 'upcoming' && (
        <ScrollView
          style={styles.content}
          contentContainerStyle={styles.contentContainer}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        >
          {scheduledRides.length === 0 ? (
            <View style={styles.emptyState}>
              <View style={styles.emptyIconContainer}>
                <Ionicons name="calendar-outline" size={48} color="#CCC" />
              </View>
              <Text style={styles.emptyTitle}>{t('activity.no_upcoming')}</Text>
              <Text style={styles.emptyText}>{t('activity.no_upcoming_subtitle')}</Text>
            </View>
          ) : (
            scheduledRides.map((ride: any) => (
              <TouchableOpacity
                key={ride.id}
                style={styles.rideCard}
                onPress={() => handleRidePress(ride)}
                accessibilityRole="button"
                accessibilityLabel={`Scheduled ride to ${ride.dropoff_address || 'unknown destination'}`}
              >
                <View style={[styles.rideIcon, { backgroundColor: '#EFF6FF' }]}>
                  <Ionicons name="calendar" size={20} color="#3B82F6" />
                </View>
                <View style={styles.rideDetails}>
                  <Text style={styles.rideDestination} numberOfLines={1}>
                    {ride.dropoff_address || 'Unknown destination'}
                  </Text>
                  <Text style={styles.rideInfo}>
                    {ride.scheduled_time ? formatDate(ride.scheduled_time) : 'Scheduled'} · {getVehicleType(ride.vehicle_type_id)}
                  </Text>
                </View>
                <View style={styles.rideFareContainer}>
                  <Text style={styles.rideFare} allowFontScaling={false}>
                    ${parseFloat(String(ride.grand_total ?? ride.total_fare ?? 0)).toFixed(2)}
                  </Text>
                  <View style={styles.rideStatusContainer}>
                    <View style={[styles.statusDot, { backgroundColor: '#3B82F6' }]} />
                    <Text style={[styles.rideStatus, { color: '#3B82F6' }]} allowFontScaling={false}>{t('activity.scheduled')}</Text>
                  </View>
                </View>
              </TouchableOpacity>
            ))
          )}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors, isCompactFilterLayout: boolean) { return StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 24, paddingVertical: 16,
  },
  title: { fontSize: 28, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text },
  tabRow: {
    flexDirection: 'row',
    paddingHorizontal: 20,
    marginBottom: 4,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  tab: {
    paddingHorizontal: 16, paddingVertical: 12,
    marginRight: 8,
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  tabActive: { borderBottomColor: colors.primary },
  tabText: { fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.textDim },
  tabTextActive: { color: colors.primary },
  periodPillRow: {
    flexDirection: 'row', paddingHorizontal: 20, paddingTop: 12, gap: 8, flexWrap: 'wrap',
  },
  periodPill: {
    paddingHorizontal: isCompactFilterLayout ? 10 : 14, paddingVertical: 7,
    borderRadius: 20, borderWidth: 1.5, borderColor: colors.border,
    backgroundColor: colors.surface, flexShrink: 0,
    minHeight: 36, justifyContent: 'center', alignItems: 'center',
  },
  periodPillActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  periodPillText: {
    fontSize: isCompactFilterLayout ? 12 : 13,
    fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text, textAlign: 'center',
  },
  periodPillTextActive: { color: '#fff' },
  statsCard: {
    marginHorizontal: 20, marginTop: 12, marginBottom: 4,
    backgroundColor: colors.surfaceLight ?? colors.surface,
    borderRadius: 16, padding: 16,
    borderWidth: 1, borderColor: colors.border,
  },
  statsRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-around' },
  statItem: { flex: 1, alignItems: 'center' },
  statValue: { fontSize: 17, fontFamily: 'PlusJakartaSans_700Bold', color: colors.primary, marginBottom: 2 },
  statLabel: { fontSize: 11, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim },
  statDivider: { width: 1, height: 32, backgroundColor: colors.border },
  filterTabs: {
    flexDirection: 'row', paddingHorizontal: 20, marginBottom: 8, gap: 8, flexWrap: 'wrap',
  },
  filterTab: {
    paddingHorizontal: isCompactFilterLayout ? 14 : 20, paddingVertical: 10,
    borderRadius: 24, borderWidth: 1.5, borderColor: colors.border,
    backgroundColor: colors.surface, flexShrink: 0,
    minWidth: isCompactFilterLayout ? 92 : undefined,
    flexGrow: isCompactFilterLayout ? 1 : 0,
    minHeight: 42, justifyContent: 'center', alignItems: 'center',
  },
  filterTabActive: { backgroundColor: colors.text, borderColor: colors.text },
  filterTabText: {
    fontSize: 14, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text,
  },
  filterTabTextActive: { color: colors.surface },
  content: { flex: 1 },
  contentContainer: { padding: 20, paddingTop: 12 },
  listFooter: {
    alignItems: 'center', justifyContent: 'center',
    paddingTop: 16, paddingBottom: 28, gap: 6,
  },
  listFooterText: {
    fontSize: 13, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim,
  },
  listFooterAction: {
    fontSize: 13, fontFamily: 'PlusJakartaSans_700Bold', color: colors.primary,
  },
  monthHeader: {
    fontSize: 12, fontFamily: 'PlusJakartaSans_700Bold', color: colors.textDim,
    letterSpacing: 0.5, marginTop: 16, marginBottom: 12,
  },
  rideCard: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  rideIcon: { width: 48, height: 48, borderRadius: 24, justifyContent: 'center', alignItems: 'center', marginRight: 14 },
  rideDetails: { flex: 1 },
  rideDestination: {
    fontSize: 16, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text, marginBottom: 4,
  },
  rideInfo: { fontSize: 13, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim },
  rideBookingId: {
    fontSize: 11, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.textDim,
    letterSpacing: 0.5, marginTop: 4,
  },
  legacyBadge: {
    alignSelf: 'flex-start', backgroundColor: colors.border,
    borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2, marginTop: 4,
  },
  legacyBadgeText: {
    fontSize: 10, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim,
  },
  rideFareContainer: { alignItems: 'flex-end' },
  rideFare: { fontSize: 17, fontFamily: 'PlusJakartaSans_700Bold', color: colors.primary, marginBottom: 4 },
  rideFareCancelled: { color: colors.textDim },
  rideFareBreakdown: { fontSize: 10, color: '#10B981', marginBottom: 3 },
  rideStatusContainer: { flexDirection: 'row', alignItems: 'center' },
  statusDot: { width: 6, height: 6, borderRadius: 3, marginRight: 4 },
  rideStatus: { fontSize: 12, fontFamily: 'PlusJakartaSans_500Medium' },
  emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingTop: 100 },
  emptyIconContainer: {
    width: 100, height: 100, borderRadius: 50, backgroundColor: colors.surfaceLight,
    justifyContent: 'center', alignItems: 'center', marginBottom: 24,
  },
  emptyTitle: {
    fontSize: 20, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text, marginBottom: 8,
  },
  emptyText: {
    fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim,
    textAlign: 'center', lineHeight: 22,
  },
}); }
