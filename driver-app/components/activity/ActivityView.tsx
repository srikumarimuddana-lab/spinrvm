import React, { useState, useCallback, useMemo, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ActivityIndicator,
  TouchableOpacity,
  useWindowDimensions,
} from 'react-native';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useFocusEffect } from '@react-navigation/native';
import { useDriverStore } from '../../store/driverStore';

const toMoney = (s: string | number | null | undefined): string => {
  const n = Math.round((parseFloat(String(s ?? '0')) || 0) * 100) / 100;
  return n.toFixed(2);
};
const parseMoney = (s: string | number | null | undefined): number =>
  Math.round((parseFloat(String(s ?? '0')) || 0) * 100) / 100;

type Period = 'today' | 'week' | 'month' | 'all';
type StatusFilter = 'all' | 'completed' | 'cancelled' | 'scheduled';
const PAGE_SIZE = 50;

export default function ActivityView() {
  const router = useRouter();
  // Filter pills wrap onto a second row rather than scrolling horizontally, so
  // every option stays visible and tappable without a discovery gesture (same
  // reasoning as payout-history.tsx). On narrow phones the pills also tighten
  // their padding/type so the wrap happens later. 380 matches the sibling
  // rider-app Activity screen; BREAKPOINTS.sm (375) would miss a 375dp iPhone SE.
  const { width } = useWindowDimensions();
  const isCompactFilterLayout = width < 380;
  const {
    earnings,
    earningsByPeriod,
    rideHistory,
    historyTotal,
    fetchEarnings,
    fetchRideHistory,
    fetchDriverBalance,
  } = useDriverStore();

  const [period, setPeriod] = useState<Period>('today');
  // Prefer this period's cached earnings so a revisited pill renders instantly;
  // fall back to the most-recent `earnings` (first render / cache miss).
  const shownEarnings = earningsByPeriod?.[period] ?? earnings;
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  // True once the first data load has completed.
  const hasLoadedRef = useRef(false);
  // The period whose data is currently on screen. The store holds only ONE
  // `earnings` object (overwritten by each fetchEarnings), so a pill change must
  // always refetch — showing the last-fetched period's numbers under a
  // different pill would be wrong.
  const shownPeriodRef = useRef<string | null>(null);
  // Last same-period refresh, so rapid tab re-focus (same pill) can skip a
  // redundant background refetch. Never applied across a pill change.
  const lastFocusFetchRef = useRef(0);
  const FRESH_MS = 30_000;

  const loadData = useCallback(async () => {
    const isPeriodChange = shownPeriodRef.current !== period;
    // Read the per-period cache via getState() so loadData stays stable and
    // doesn't retrigger useFocusEffect in a loop.
    const cached = !!useDriverStore.getState().earningsByPeriod?.[period];
    // Same-period re-focus, still cached and refreshed within FRESH_MS: skip the
    // network round-trip — this is what makes re-opening the Activity tab
    // instant. If the cache was invalidated (e.g. a ride just completed) `cached`
    // is false, so we refetch fresh instead of showing stale numbers. A pill
    // change is never skipped (gated on !isPeriodChange).
    if (
      !isPeriodChange &&
      hasLoadedRef.current &&
      cached &&
      Date.now() - lastFocusFetchRef.current < FRESH_MS
    ) {
      return;
    }
    // Show the spinner whenever this period isn't cached — first load, a
    // never-opened pill, or after a ride invalidated the cache. A cached pill
    // renders its numbers instantly and refreshes silently in the background.
    if (!cached) setLoading(true);
    try {
      if (isPeriodChange && hasLoadedRef.current) {
        // Pill change (not the first load): only earnings is period-specific.
        // The ride list re-filters client-side and the balance is
        // period-independent, so DON'T refetch them — replacing rideHistory
        // forces the whole FlatList to re-render for no reason, which is the
        // client-side lag on pill taps.
        await fetchEarnings(period);
      } else {
        await Promise.allSettled([
          fetchEarnings(period),
          fetchRideHistory(PAGE_SIZE, 0, false),
          fetchDriverBalance(),
        ]);
      }
    } catch {}
    hasLoadedRef.current = true;
    shownPeriodRef.current = period;
    lastFocusFetchRef.current = Date.now();
    setLoading(false);
  }, [period, fetchEarnings, fetchRideHistory, fetchDriverBalance]);

  useFocusEffect(
    useCallback(() => {
      loadData();
    }, [loadData])
  );

  const totalEarnings = parseMoney(shownEarnings?.total_earnings);
  const totalTips = parseMoney(shownEarnings?.total_tips);
  const totalIncentives = parseMoney(shownEarnings?.total_incentives);
  // Quest + referral rewards (driver_bonuses) — distinct from per-ride incentives.
  const totalBonuses = parseMoney(shownEarnings?.total_bonuses);
  // Referral-only slice, shown as its own line; the remainder is quest bonuses.
  const totalReferralBonuses = parseMoney(shownEarnings?.total_referral_bonuses);
  const totalTax = parseMoney(shownEarnings?.total_tax);
  const fareEarnings = Math.max(totalEarnings - totalTips - totalIncentives - totalBonuses - totalTax, 0);
  const periodRideTotal = period === 'all' ? historyTotal : Number(shownEarnings?.total_rides ?? 0);
  const hasMoreHistory = rideHistory.length < historyTotal;

  const loadMoreHistory = useCallback(async () => {
    if (loading || loadingMore || !hasMoreHistory) return;
    setLoadingMore(true);
    try {
      await fetchRideHistory(PAGE_SIZE, rideHistory.length, true);
    } finally {
      setLoadingMore(false);
    }
  }, [loading, loadingMore, hasMoreHistory, fetchRideHistory, rideHistory.length]);

  const filteredRides = useMemo(() => {
    return rideHistory.filter((r) => {
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
  }, [rideHistory, statusFilter, period]);
  const canLoadMoreHistory =
    hasMoreHistory &&
    filteredRides.length > 0 &&
    (period === 'all' && statusFilter === 'all'
      ? true
      : (statusFilter === 'all' && periodRideTotal > filteredRides.length) || filteredRides.length >= PAGE_SIZE);

  // #6: render one ride card. Extracted from the inline map so the list can be
  // virtualized by FlatList. Wrapped in the 16px horizontal inset that the old
  // `ridesSection` View used to provide (rideCard has no inset of its own).
  const renderRideCard = useCallback(({ item: ride }: { item: any }) => {
    const isCompleted = ride.status === 'completed';
    const isCancelled = ride.status === 'cancelled';
    const statusColor = isCompleted ? '#10b981' : isCancelled ? '#ef4444' : '#f59e0b';
    const statusBg = isCompleted ? 'rgba(16,185,129,0.1)' : isCancelled ? 'rgba(239,68,68,0.1)' : 'rgba(245,158,11,0.1)';
    const statusLabel = isCompleted ? 'Completed' : isCancelled ? 'Cancelled' : 'Scheduled';
    const statusIcon = isCompleted ? 'checkmark-circle' : isCancelled ? 'close-circle' : 'time';

    const date = ride.ride_completed_at || (ride as any).cancelled_at || ride.created_at;
    const tipAmount = parseMoney((ride as any).tip_amount);
    const incentiveAmount = parseMoney((ride as any).incentive_amount);
    const totalEarned = parseMoney((ride as any).total_earned);

    return (
      <View style={styles.ridesSection}>
        <TouchableOpacity
          style={styles.rideCard}
          activeOpacity={0.7}
          onPress={() => router.push(`/driver/ride-detail?id=${ride.id}` as any)}
        >
          {/* Top row: status + date */}
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

          {/* Route: pickup → dropoff */}
          <View style={styles.routeContainer}>
            <View style={styles.routeDots}>
              <View style={[styles.dot, { backgroundColor: '#ef4444' }]} />
              <View style={styles.routeLine} />
              <View style={[styles.dot, { backgroundColor: '#10b981' }]} />
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

          {/* Bottom row: trip meta + fare */}
          <View style={styles.rideBottomRow}>
            <View style={styles.tripMeta}>
              {ride.distance_km != null && (
                <View style={styles.metaBadge}>
                  <Ionicons name="map-outline" size={13} color="#9ca3af" />
                  <Text style={styles.metaText}>{ride.distance_km.toFixed(1)} km</Text>
                </View>
              )}
              {ride.duration_minutes != null && (
                <View style={styles.metaBadge}>
                  <Ionicons name="time-outline" size={13} color="#9ca3af" />
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
                  <Text style={[styles.fareAmount, { color: '#9ca3af', fontSize: 16 }]}>$0.00</Text>
                )
              ) : (
                <Text style={styles.fareAmount}>Est. ${toMoney(parseMoney((ride as any).driver_earnings))}</Text>
              )}
            </View>
          </View>
        </TouchableOpacity>
      </View>
    );
  }, [router, styles]);

  // #6: FlatList virtualizes the (potentially long, 'all'-period) ride history
  // that was previously a filteredRides.map() inside a ScrollView — rendering
  // every row at once. The chrome (pills/earnings/stats/section header/status
  // pills) is the ListHeaderComponent; empty + load-more are the empty/footer
  // slots. Each preserves the 16px inset the old `ridesSection` View provided.
  const listHeader = (
    <>
      {/* Period pills */}
      <View style={styles.pillRow}>
        {(['today', 'week', 'month', 'all'] as Period[]).map((item) => (
          <TouchableOpacity
            key={item}
            style={[
              styles.pill,
              isCompactFilterLayout && styles.pillCompact,
              period === item && styles.pillActive,
            ]}
            onPress={() => setPeriod(item)}
          >
            <Text
              style={[
                styles.pillText,
                isCompactFilterLayout && styles.pillTextCompact,
                period === item && styles.pillTextActive,
              ]}
            >
              {item === 'all' ? 'All Time' : item === 'today' ? 'Today' : item === 'week' ? 'This Week' : 'This Month'}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {loading ? (
        <ActivityIndicator color="#ef4444" style={{ marginTop: 60 }} />
      ) : (
        <>
          {/* Earnings breakdown */}
          <View style={styles.card}>
            <View style={styles.totalRow}>
              <Text style={styles.totalLabel}>Total Earned</Text>
              <Text style={styles.totalValue}>${toMoney(totalEarnings)}</Text>
            </View>
            {/* Vertical list (icon + label on the left, amount right-aligned).
                A single row of five columns cramped the dollar values into
                ~65px each, so amounts like $254.62 wrapped mid-number and the
                columns fell out of alignment. A full-width row per category
                gives every amount room to render on one line and stay uniform
                regardless of how large it is or how narrow the screen. */}
            <View style={styles.breakdownList}>
              <View style={styles.breakdownRow}>
                <Ionicons name="cash-outline" size={18} color="#ef4444" style={styles.breakdownIcon} />
                <Text style={styles.label}>Fare</Text>
                <Text style={styles.value} numberOfLines={1}>${toMoney(fareEarnings)}</Text>
              </View>
              <View style={[styles.breakdownRow, styles.breakdownRowBorder]}>
                <Ionicons name="gift-outline" size={18} color="#f59e0b" style={styles.breakdownIcon} />
                <Text style={styles.label}>Tips</Text>
                <Text style={[styles.value, { color: '#f59e0b' }]} numberOfLines={1}>${toMoney(totalTips)}</Text>
              </View>
              <View style={[styles.breakdownRow, styles.breakdownRowBorder]}>
                <Ionicons name="flash" size={18} color="#8b5cf6" style={styles.breakdownIcon} />
                {/* Per-ride incentives + quest rewards (referral shown separately). */}
                <Text style={styles.label}>Bonus</Text>
                <Text style={[styles.value, { color: '#8b5cf6' }]} numberOfLines={1}>${toMoney(totalIncentives + (totalBonuses - totalReferralBonuses))}</Text>
              </View>
              <View style={[styles.breakdownRow, styles.breakdownRowBorder]}>
                <Ionicons name="people-outline" size={18} color="#10b981" style={styles.breakdownIcon} />
                {/* Referral rewards paid into payable_balance (kind='referral'). */}
                <Text style={styles.label}>Referral</Text>
                <Text style={[styles.value, { color: '#10b981' }]} numberOfLines={1}>${toMoney(totalReferralBonuses)}</Text>
              </View>
              <View style={[styles.breakdownRow, styles.breakdownRowBorder]}>
                <Ionicons name="receipt-outline" size={18} color="#6b7280" style={styles.breakdownIcon} />
                <Text style={styles.label}>Tax</Text>
                <Text style={[styles.value, { color: '#6b7280' }]} numberOfLines={1}>${toMoney(totalTax)}</Text>
              </View>
            </View>
          </View>

          {/* Stats grid */}
          <View style={styles.statsGrid}>
            <View style={styles.statCard}>
              <View style={[styles.iconWrap, { backgroundColor: 'rgba(239,68,68,0.1)' }]}>
                <FontAwesome5 name="car" size={16} color="#ef4444" />
              </View>
              <View>
                <Text style={styles.statValue}>{shownEarnings?.total_rides || 0}</Text>
                <Text style={styles.statLabel}>Total Trips</Text>
              </View>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.iconWrap, { backgroundColor: 'rgba(245,158,11,0.1)' }]}>
                <MaterialCommunityIcons name="road-variant" size={18} color="#f59e0b" />
              </View>
              <View>
                <Text style={styles.statValue}>{parseMoney(shownEarnings?.total_distance_km).toFixed(1)}</Text>
                <Text style={styles.statLabel}>KM Driven</Text>
              </View>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.iconWrap, { backgroundColor: 'rgba(16,185,129,0.1)' }]}>
                <Ionicons name="time" size={18} color="#10b981" />
              </View>
              <View>
                <Text style={styles.statValue}>
                  {Math.round((shownEarnings?.total_duration_minutes || 0) / 60)}h
                </Text>
                <Text style={styles.statLabel}>Online Time</Text>
              </View>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.iconWrap, { backgroundColor: 'rgba(56,189,248,0.1)' }]}>
                <Ionicons name="trending-up" size={18} color="#38bdf8" />
              </View>
              <View>
                <Text style={styles.statValue}>${toMoney(shownEarnings?.average_per_ride)}</Text>
                <Text style={styles.statLabel}>Avg per Trip</Text>
              </View>
            </View>
          </View>

          {/* Rides section header + status filter. The ride cards themselves are
              virtualized below as FlatList items (see renderRideCard). */}
          <View style={styles.ridesSection}>
            <View style={styles.ridesSectionHeader}>
              <Text style={styles.sectionTitle}>Your Rides</Text>
              <Text style={styles.rideCount}>{filteredRides.length} rides</Text>
            </View>

            {/* Status filter pills — wrap to match the period row above. */}
            <View style={styles.statusPillRow}>
              {(['all', 'completed', 'scheduled', 'cancelled'] as StatusFilter[]).map((item) => (
                <TouchableOpacity
                  key={item}
                  style={[
                    styles.statusPill,
                    isCompactFilterLayout && styles.statusPillCompact,
                    statusFilter === item && styles.statusPillActive,
                  ]}
                  onPress={() => setStatusFilter(item)}
                >
                  <Text
                    style={[
                      styles.statusPillText,
                      isCompactFilterLayout && styles.statusPillTextCompact,
                      statusFilter === item && styles.statusPillTextActive,
                    ]}
                  >
                    {item === 'all' ? 'All Status' : item.charAt(0).toUpperCase() + item.slice(1)}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        </>
      )}
    </>
  );

  return (
    <FlatList
      style={styles.container}
      contentContainerStyle={styles.content}
      data={loading ? [] : filteredRides}
      keyExtractor={(item: any) => String(item.id)}
      renderItem={renderRideCard}
      ListHeaderComponent={listHeader}
      ListEmptyComponent={
        loading ? null : (
          <View style={styles.ridesSection}>
            <View style={styles.emptyState}>
              <Ionicons name="car-sport-outline" size={48} color="#d1d5db" />
              <Text style={styles.emptyTitle}>No Rides Found</Text>
              <Text style={styles.emptyDesc}>No rides match this filter for the selected period.</Text>
            </View>
          </View>
        )
      }
      ListFooterComponent={
        !loading && canLoadMoreHistory ? (
          <View style={styles.ridesSection}>
            <TouchableOpacity
              style={[styles.loadMoreButton, loadingMore && styles.loadMoreButtonDisabled]}
              activeOpacity={0.8}
              disabled={loadingMore}
              onPress={loadMoreHistory}
            >
              {loadingMore ? (
                <ActivityIndicator color="#ef4444" />
              ) : (
                <Text style={styles.loadMoreText}>Load more rides</Text>
              )}
            </TouchableOpacity>
          </View>
        ) : null
      }
      initialNumToRender={8}
      maxToRenderPerBatch={8}
      windowSize={11}
      removeClippedSubviews
    />
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    paddingBottom: 40,
  },
  // Period pills
  pillRow: {
    flexDirection: 'row',
    // Wrap so the last pill ("All Time") is never pushed off-screen and
    // untappable on a 360dp/375dp phone.
    flexWrap: 'wrap',
    paddingHorizontal: 16,
    gap: 10,
    marginTop: 12,
    marginBottom: 4,
  },
  pill: {
    paddingHorizontal: 18,
    paddingVertical: 8,
    borderRadius: 24,
    backgroundColor: '#f3f4f6',
    borderWidth: 1,
    borderColor: '#e5e7eb',
    // Never let a pill squeeze its own label; wrap the row instead.
    flexShrink: 0,
    // MIN_TOUCH from shared/utils/responsive.ts — the old 8px padding + 13px
    // text was ~34pt tall, under the WCAG 2.1 AA target size.
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  // Narrow phones: tighten padding/type so the row wraps later.
  pillCompact: {
    paddingHorizontal: 12,
  },
  pillActive: {
    backgroundColor: '#ef4444',
    borderColor: '#ef4444',
  },
  pillText: {
    color: '#6b7280',
    fontSize: 13,
    fontWeight: '600',
  },
  pillTextCompact: {
    fontSize: 12,
  },
  pillTextActive: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '700',
  },
  // Earnings card
  card: {
    marginHorizontal: 16,
    marginTop: 12,
    marginBottom: 16,
    backgroundColor: '#fff',
    borderRadius: 20,
    padding: 16,
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  totalRow: {
    alignItems: 'center',
    marginBottom: 14,
    paddingBottom: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  totalLabel: {
    color: '#6b7280',
    fontSize: 12,
    fontWeight: '600',
    marginBottom: 4,
  },
  totalValue: {
    color: '#10b981',
    fontSize: 28,
    fontWeight: '900',
  },
  breakdownList: {
    // Vertical stack of full-width category rows.
  },
  breakdownRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
  },
  breakdownRowBorder: {
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
  },
  breakdownIcon: {
    width: 22,
    textAlign: 'center',
  },
  label: {
    flex: 1,
    marginLeft: 10,
    color: '#374151',
    fontSize: 14,
    fontWeight: '600',
  },
  value: {
    color: '#111827',
    fontSize: 16,
    fontWeight: '800',
    marginLeft: 12,
  },
  // Stats grid
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
    backgroundColor: '#fff',
    borderRadius: 20,
    padding: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  iconWrap: {
    width: 36,
    height: 36,
    borderRadius: 18,
    justifyContent: 'center',
    alignItems: 'center',
  },
  statValue: {
    color: '#111827',
    fontSize: 18,
    fontWeight: '800',
  },
  statLabel: {
    color: '#6b7280',
    fontSize: 11,
    fontWeight: '600',
    marginTop: 1,
  },
  // Rides section
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
    color: '#111827',
    fontSize: 18,
    fontWeight: '800',
  },
  rideCount: {
    color: '#9ca3af',
    fontSize: 13,
    fontWeight: '600',
  },
  // Status filter pills
  statusPillRow: {
    // Was a horizontal ScrollView's contentContainerStyle; now a plain wrapping
    // row so every status stays visible, matching the period row above. The
    // 16px inset comes from the parent `ridesSection`.
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 16,
  },
  statusPill: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 20,
    backgroundColor: '#f3f4f6',
    borderWidth: 1,
    borderColor: '#e5e7eb',
    flexShrink: 0,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
  },
  statusPillCompact: {
    paddingHorizontal: 10,
  },
  statusPillActive: {
    backgroundColor: '#1f2937',
    borderColor: '#1f2937',
  },
  statusPillText: {
    color: '#6b7280',
    fontSize: 12,
    fontWeight: '600',
  },
  statusPillTextCompact: {
    fontSize: 11,
  },
  statusPillTextActive: {
    color: '#fff',
    fontWeight: '700',
  },
  // Ride cards
  rideCard: {
    backgroundColor: '#fff',
    borderRadius: 20,
    padding: 18,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: '#e5e7eb',
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
    color: '#6b7280',
    fontSize: 12,
    fontWeight: '500',
  },
  rideCodeText: {
    color: '#9ca3af',
    fontSize: 10,
    fontWeight: '600',
    marginTop: 2,
    letterSpacing: 0.5,
  },
  // Route
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
    backgroundColor: '#e5e7eb',
    marginVertical: 4,
  },
  routeAddresses: {
    flex: 1,
    marginLeft: 8,
  },
  routeLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: '#9ca3af',
    letterSpacing: 1,
    marginBottom: 2,
  },
  routeAddress: {
    fontSize: 14,
    fontWeight: '600',
    color: '#111827',
  },
  // Bottom row
  rideBottomRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    paddingTop: 14,
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
  },
  tripMeta: {
    flexDirection: 'row',
    gap: 10,
  },
  metaBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: '#f9fafb',
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: 8,
  },
  metaText: {
    color: '#6b7280',
    fontSize: 12,
    fontWeight: '600',
  },
  fareAmount: {
    fontSize: 20,
    fontWeight: '900',
    color: '#10b981',
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
  // Empty state
  emptyState: {
    alignItems: 'center',
    paddingVertical: 40,
  },
  emptyTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: '#374151',
    marginTop: 16,
    marginBottom: 8,
  },
  emptyDesc: {
    fontSize: 13,
    color: '#9ca3af',
    textAlign: 'center',
  },
  loadMoreButton: {
    marginTop: 8,
    borderWidth: 1,
    borderColor: '#ef4444',
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: 'center',
    backgroundColor: '#fff',
  },
  loadMoreButtonDisabled: {
    opacity: 0.7,
  },
  loadMoreText: {
    color: '#ef4444',
    fontSize: 14,
    fontWeight: '700',
  },
});
