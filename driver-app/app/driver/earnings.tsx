import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  Dimensions,
  Platform,
  ActivityIndicator,
  RefreshControl,
  FlatList,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';

const { width } = Dimensions.get('window');

import SpinrConfig from '@shared/config/spinr.config';
import { useLanguageStore } from '../../store/languageStore';

const THEME = SpinrConfig.theme.colors;
const COLORS = {
  primary: THEME.background,
  accent: THEME.primary,
  accentDark: THEME.primaryDark,
  surface: THEME.surface,
  surfaceLight: THEME.surfaceLight,
  text: THEME.text,
  textDim: THEME.textDim,
  success: '#10B981',
  gold: '#FFD700',
  orange: '#FF9500',
  danger: '#EF4444',
  warning: '#F59E0B',
  border: THEME.border,
};

type Period = 'today' | 'week' | 'month' | 'all';

export default function EarningsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { t } = useLanguageStore();
  const {
    earnings,
    dailyEarnings,
    tripEarnings,
    fetchEarnings,
    fetchDailyEarnings,
    fetchTripEarnings,
    fetchDriverBalance,
  } = useDriverStore();

  const [period, setPeriod] = useState<Period>('today');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    await Promise.all([
      fetchEarnings(period),
      fetchDailyEarnings(period === 'today' ? 1 : period === 'week' ? 7 : period === 'month' ? 30 : 30),
      fetchTripEarnings(),
      fetchDriverBalance(),
    ]);
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

  const maxDailyEarning = Math.max(...(dailyEarnings.map((d) => d.earnings) || [1]), 1);

  const renderFilterTabs = () => (
    <View style={styles.filterWrapper}>
      <FlatList
        horizontal
        showsHorizontalScrollIndicator={false}
        data={['today', 'week', 'month', 'all'] as Period[]}
        contentContainerStyle={styles.filterListContent}
        keyExtractor={(item) => item}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={[styles.filterPill, period === item && styles.filterPillActive]}
            onPress={() => setPeriod(item)}
            activeOpacity={0.7}
          >
            <Text style={[styles.filterPillText, period === item && styles.filterPillTextActive]}>
              {item === 'all' ? t('earnings.allTime') : item === 'today' ? t('earnings.today') : item === 'week' ? t('earnings.thisWeek') : t('earnings.thisMonth')}
            </Text>
          </TouchableOpacity>
        )}
      />
    </View>
  );

  return (
    <View style={styles.container}>
      {/* Rich Header Hero Background */}
      <LinearGradient
        colors={[COLORS.accent, COLORS.accentDark]}
        style={[styles.headerHero, { paddingTop: insets.top + 20 }]}
      >
        <View style={styles.headerTop}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#fff" />
          </TouchableOpacity>
          <Text style={styles.headerHeroTitle}>{t('earnings.title')}</Text>
          <TouchableOpacity
            style={styles.payoutBtn}
            onPress={() => router.push('/driver/payout' as any)}
          >
            <Ionicons name="wallet-outline" size={16} color={COLORS.accentDark} />
            <Text style={styles.payoutBtnText}>{t('earnings.payout')}</Text>
          </TouchableOpacity>
        </View>

        <View style={styles.totalBox}>
          <Text style={styles.totalLabel}>{t('earnings.totalEarnings')}</Text>
          <Text style={styles.totalAmount}>
            ${loading ? '--' : (earnings?.total_earnings || 0).toFixed(2)}
          </Text>
          
          {(earnings?.total_tips ? earnings.total_tips > 0 : false) && (
            <View style={styles.tipsBadge}>
              <Ionicons name="gift" size={14} color={COLORS.gold} style={{ marginRight: 2 }} />
              <Text style={styles.tipsText}>+${earnings?.total_tips?.toFixed(2)} tips included</Text>
            </View>
          )}
        </View>
      </LinearGradient>

      {/* Pill Filters outside ScrollView to stay fixed if needed, but keeping it smooth in ScrollView is also fine. Let's put it at top level. */}
      {renderFilterTabs()}

      <ScrollView
        style={styles.content}
        contentContainerStyle={{ paddingBottom: insets.bottom + 90 }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.accent} />}
      >
        {/* Modern Stats Grid */}
        <View style={styles.statsGrid}>
          <View style={styles.statCard}>
            <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(239, 68, 68, 0.1)' }]}>
                <FontAwesome5 name="car" size={16} color={COLORS.accent} />
            </View>
            <View>
                <Text style={styles.statValue}>{loading ? '--' : (earnings?.total_rides || 0)}</Text>
                <Text style={styles.statLabel}>Total Trips</Text>
            </View>
          </View>
          
          <View style={styles.statCard}>
            <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(245, 158, 11, 0.1)' }]}>
                <MaterialCommunityIcons name="road-variant" size={18} color={COLORS.warning} />
            </View>
            <View>
                <Text style={styles.statValue}>{loading ? '--' : (earnings?.total_distance_km || 0).toFixed(1)}</Text>
                <Text style={styles.statLabel}>KM Driven</Text>
            </View>
          </View>
          
          <View style={styles.statCard}>
            <View style={[styles.statIconWrapper, { backgroundColor: 'rgba(16, 185, 129, 0.1)' }]}>
                <Ionicons name="time" size={18} color={COLORS.success} />
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
                <Text style={styles.statValue}>${loading ? '--' : (earnings?.average_per_ride || 0).toFixed(2)}</Text>
                <Text style={styles.statLabel}>Avg per Trip</Text>
            </View>
          </View>
        </View>

        {/* Bar Chart Section */}
        {(!loading && dailyEarnings.length > 1) && (
          <View style={styles.chartSection}>
            <Text style={styles.sectionTitle}>Daily Breakdown</Text>
            <View style={styles.chartCard}>
              <View style={styles.chartInnerContainer}>
                {dailyEarnings.map((day, i) => {
                  const safeEarnings = Math.max(day.earnings, 1); // Avoid 0 division issues completely
                  const barHeightRatio = (safeEarnings / maxDailyEarning);
                  const barHeight = Math.max(barHeightRatio * 140, 6); // Minimum height logic
                  const dayLabel = new Date(day.date + 'T00:00:00').toLocaleDateString('en', { weekday: 'narrow' });
                  
                  return (
                    <View key={i} style={styles.barColumn}>
                      <Text style={styles.barValue}>
                        {day.earnings > 0 ? `$${day.earnings.toFixed(0)}` : ''}
                      </Text>
                      <View style={styles.barTrack}>
                        <LinearGradient
                          colors={[COLORS.accent, COLORS.accentDark]}
                          style={[styles.bar, { height: barHeight }]}
                        />
                      </View>
                      <Text style={styles.barLabel}>{dayLabel}</Text>
                    </View>
                  );
                })}
              </View>
            </View>
          </View>
        )}

        {/* Premium Trip List */}
        <View style={styles.tripsSection}>
          <Text style={styles.sectionTitle}>Recent Trips</Text>
          {loading ? (
            <ActivityIndicator color={COLORS.accent} style={{ marginTop: 40 }} />
          ) : tripEarnings.length === 0 ? (
            <View style={styles.emptyStateContainer}>
              <View style={styles.emptyIconCircle}>
                <Ionicons name="wallet-outline" size={48} color={COLORS.accent} />
              </View>
              <Text style={styles.emptyStateTitle}>No trips to show</Text>
              <Text style={styles.emptyStateDesc}>Complete rides to start seeing your earnings breakdown here.</Text>
            </View>
          ) : (
            tripEarnings.map((trip) => (
              <TouchableOpacity
                  key={trip.ride_id}
                  style={styles.rideCard} 
                  activeOpacity={0.8}
                  onPress={() => router.push(`/driver/ride-detail?id=${trip.ride_id}` as any)}
                >
                  {/* Top Header */}
                  <View style={styles.cardHeader}>
                    <View style={[styles.statusBadge, { backgroundColor: 'rgba(16, 185, 129, 0.1)' }]}>
                      <Ionicons name="checkmark-circle" size={14} color={COLORS.success} />
                      <Text style={[styles.statusText, { color: COLORS.success }]}>Completed</Text>
                    </View>
                    <View style={{ alignItems: 'flex-end' }}>
                      <Text style={styles.dateText}>
                        {trip.completed_at
                          ? new Date(trip.completed_at).toLocaleDateString('en', {
                              month: 'short',
                              day: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          : ''}
                      </Text>
                      <Text style={styles.bookingIdText}>ID: #{String(trip.ride_id).substring(0, 8).toUpperCase()}</Text>
                    </View>
                  </View>

                  {/* Route Timeline */}
                  <View style={styles.routeContainer}>
                    <View style={styles.timelineIndicators}>
                      <View style={[styles.dot, { backgroundColor: COLORS.accent }]} />
                      <View style={styles.timelineLine} />
                      <View style={[styles.dot, { backgroundColor: COLORS.danger }]} />
                    </View>
                    
                    <View style={styles.routeDetails}>
                      <View style={styles.routePoint}>
                        <Text style={styles.routeLabel}>PICKUP</Text>
                        <Text style={styles.routeAddress} numberOfLines={1}>
                          {trip.pickup_address || 'Unknown Pickup Location'}
                        </Text>
                      </View>
                      <View style={styles.routePointSpacer} />
                      <View style={styles.routePoint}>
                        <Text style={styles.routeLabel}>DROP-OFF</Text>
                        <Text style={styles.routeAddress} numberOfLines={1}>
                          {trip.dropoff_address || 'Unknown Dropoff Location'}
                        </Text>
                      </View>
                    </View>
                  </View>

                  {/* Footer info breakdown */}
                  <View style={styles.cardFooter}>
                    <View style={styles.tripMetaRow}>
                      <View style={styles.metaBadge}>
                        <Ionicons name="map-outline" size={14} color={COLORS.textDim} />
                        <Text style={styles.metaText}>{trip.distance_km.toFixed(1)} km</Text>
                      </View>
                      <View style={styles.metaBadge}>
                        <Ionicons name="time-outline" size={14} color={COLORS.textDim} />
                        <Text style={styles.metaText}>{trip.duration_minutes} min</Text>
                      </View>
                      {trip.rider_rating !== null && (
                         <View style={styles.metaBadge}>
                           <Ionicons name="star" size={14} color={COLORS.gold} />
                           <Text style={styles.metaText}>{trip.rider_rating}</Text>
                         </View>
                      )}
                    </View>
                    
                    <View style={styles.fareContainer}>
                      <Text style={styles.fareLabel}>Earned</Text>
                      <Text style={styles.fareText}>${trip.driver_earnings.toFixed(2)}</Text>
                      {trip.tip_amount > 0 && (
                          <Text style={styles.tipAmountText}>+ ${trip.tip_amount.toFixed(2)} tip</Text>
                      )}
                    </View>
                  </View>
              </TouchableOpacity>
            ))
          )}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F3F4F6',
  },
  // Header Hero
  headerHero: {
    paddingHorizontal: 20,
    paddingBottom: 30,
    borderBottomLeftRadius: 32,
    borderBottomRightRadius: 32,
    shadowColor: COLORS.accentDark,
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
  payoutBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 20,
    gap: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  payoutBtnText: {
    color: COLORS.accentDark,
    fontSize: 14,
    fontWeight: '700',
  },
  totalBox: {
    alignItems: 'center',
    paddingTop: 10,
    paddingBottom: 10,
  },
  totalLabel: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1.5,
    marginBottom: 8,
  },
  totalAmount: {
    color: '#fff',
    fontSize: 54,
    fontWeight: '900',
    letterSpacing: -1,
  },
  tipsBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(0,0,0,0.15)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
    marginTop: 10,
  },
  tipsText: {
    color: COLORS.gold,
    fontSize: 13,
    fontWeight: '700',
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
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#E5E7EB',
  },
  filterPillActive: {
    backgroundColor: COLORS.accent,
    borderColor: COLORS.accent,
  },
  filterPillText: {
    color: COLORS.textDim,
    fontSize: 13,
    fontWeight: '600',
  },
  filterPillTextActive: {
    color: '#fff',
    fontWeight: '700',
  },
  // Content Array
  content: {
    flex: 1,
  },
  // Stats Grid
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    paddingHorizontal: 16,
    paddingTop: 8,
    marginBottom: 24,
  },
  statCard: {
    width: (width - 44) / 2, // 44 = 16 left + 16 right + 12 gap
    backgroundColor: '#fff',
    borderRadius: 20,
    padding: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.02)',
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
    color: COLORS.text,
    fontSize: 18,
    fontWeight: '800',
  },
  statLabel: {
    color: COLORS.textDim,
    fontSize: 11,
    fontWeight: '600',
    letterSpacing: 0.2,
    marginTop: 1,
  },
  // Chart Section
  chartSection: {
    paddingHorizontal: 16,
    marginBottom: 24,
  },
  sectionTitle: {
    color: COLORS.text,
    fontSize: 18,
    fontWeight: '800',
    marginBottom: 16,
    letterSpacing: 0.2,
  },
  chartCard: {
    backgroundColor: '#fff',
    borderRadius: 24,
    paddingHorizontal: 16,
    paddingVertical: 20,
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.02)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.04,
    shadowRadius: 16,
    elevation: 3,
  },
  chartInnerContainer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-around',
    height: 180,
  },
  barColumn: {
    alignItems: 'center',
    gap: 8,
  },
  barValue: {
    color: COLORS.textDim,
    fontSize: 10,
    fontWeight: '700',
  },
  barTrack: {
    width: 14,
    height: 140,
    backgroundColor: '#F3F4F6',
    borderRadius: 8,
    justifyContent: 'flex-end',
    overflow: 'hidden',
  },
  bar: {
    width: '100%',
    borderRadius: 8,
  },
  barLabel: {
    color: COLORS.textDim,
    fontSize: 11,
    fontWeight: '600',
    marginTop: 4,
  },
  // Empty state
  emptyStateContainer: {
    alignItems: 'center',
    paddingVertical: 50,
    paddingHorizontal: 30,
  },
  emptyIconCircle: {
    width: 90,
    height: 90,
    borderRadius: 45,
    backgroundColor: 'rgba(255,255,255,1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20,
    shadowColor: COLORS.accent,
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.1,
    shadowRadius: 16,
    elevation: 4,
  },
  emptyStateTitle: {
    fontSize: 20,
    fontWeight: '800',
    color: COLORS.text,
    marginBottom: 10,
  },
  emptyStateDesc: {
    fontSize: 14,
    color: COLORS.textDim,
    textAlign: 'center',
    lineHeight: 22,
  },
  // Trips Section
  tripsSection: {
    paddingHorizontal: 16,
    paddingBottom: 20,
  },
  // Ride Card Modern Match
  rideCard: {
    backgroundColor: '#fff',
    borderRadius: 24,
    padding: 20,
    marginBottom: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.04,
    shadowRadius: 16,
    elevation: 3,
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.02)',
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
    color: COLORS.textDim,
    fontSize: 13,
    fontWeight: '500',
  },
  bookingIdText: {
    color: '#9CA3AF',
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
    backgroundColor: '#E5E7EB',
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
    color: COLORS.textDim,
    letterSpacing: 1,
    marginBottom: 2,
  },
  routeAddress: {
    fontSize: 15,
    fontWeight: '600',
    color: COLORS.text,
  },
  // Footer
  cardFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: '#F3F4F6',
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
    backgroundColor: '#F9FAFB',
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderRadius: 8,
  },
  metaText: {
    color: COLORS.textDim,
    fontSize: 13,
    fontWeight: '600',
  },
  fareContainer: {
    alignItems: 'flex-end',
  },
  fareLabel: {
    fontSize: 11,
    color: COLORS.textDim,
    fontWeight: '600',
    marginBottom: 2,
  },
  fareText: {
    fontSize: 22,
    fontWeight: '900',
    color: COLORS.accent,
    letterSpacing: -0.5,
  },
  tipAmountText: {
    color: COLORS.gold,
    fontSize: 11,
    fontWeight: '700',
    marginTop: 2,
  },
});
