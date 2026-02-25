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
} from 'react-native';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';

const { width } = Dimensions.get('window');

import SpinrConfig from '@shared/config/spinr.config';

const THEME = SpinrConfig.theme.colors;
const COLORS = {
  primary: THEME.background, // Background (white)
  accent: THEME.primary,     // Action/brand color (red)
  accentDim: THEME.primaryDark,
  surface: THEME.surface,   // Card background (white)
  surfaceLight: THEME.surfaceLight, // Light gray for subtle elements
  text: THEME.text,
  textDim: THEME.textDim,
  success: THEME.success,
  gold: '#FFD700',
  orange: '#FF9500',
  danger: THEME.error,
  border: THEME.border,
};

type Period = 'today' | 'week' | 'month' | 'all';

export default function EarningsScreen() {
  const router = useRouter();
  const {
    earnings,
    dailyEarnings,
    tripEarnings,
    driverBalance,
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

  return (
    <View style={styles.container}>
      {/* Header */}
      <LinearGradient
        colors={[COLORS.surface, COLORS.primary]}
        style={styles.header}
      >
        <View style={styles.headerRow}>
          <Text style={styles.headerTitle}>Earnings</Text>
          <TouchableOpacity
            style={styles.payoutBtn}
            onPress={() => router.push('/(driver)/payout' as any)}
          >
            <Ionicons name="wallet" size={18} color={COLORS.accent} />
            <Text style={styles.payoutBtnText}>Payout</Text>
          </TouchableOpacity>
        </View>

        {/* Period Selector */}
        <View style={styles.periodRow}>
          {(['today', 'week', 'month', 'all'] as Period[]).map((p) => (
            <TouchableOpacity
              key={p}
              style={[styles.periodBtn, period === p && styles.periodBtnActive]}
              onPress={() => setPeriod(p)}
            >
              <Text style={[styles.periodText, period === p && styles.periodTextActive]}>
                {p === 'today' ? 'Today' : p === 'week' ? 'Week' : p === 'month' ? 'Month' : 'All'}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Total Earnings */}
        <View style={styles.totalContainer}>
          <Text style={styles.totalLabel}>TOTAL EARNINGS</Text>
          <Text style={styles.totalAmount}>
            ${loading ? '--' : (earnings?.total_earnings || 0).toFixed(2)}
          </Text>
          {earnings?.total_tips ? (
            <View style={styles.tipsBadge}>
              <Ionicons name="gift" size={12} color={COLORS.gold} />
              <Text style={styles.tipsText}>+${earnings.total_tips.toFixed(2)} tips</Text>
            </View>
          ) : null}
        </View>
      </LinearGradient>

      <ScrollView
        style={styles.content}
        contentContainerStyle={{ paddingBottom: 100 }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.accent} />}
      >
        {/* Stats Cards */}
        <View style={styles.statsGrid}>
          <View style={styles.statCard}>
            <FontAwesome5 name="car" size={18} color={COLORS.accent} />
            <Text style={styles.statValue}>{earnings?.total_rides || 0}</Text>
            <Text style={styles.statLabel}>Trips</Text>
          </View>
          <View style={styles.statCard}>
            <MaterialCommunityIcons name="road-variant" size={18} color={COLORS.orange} />
            <Text style={styles.statValue}>{(earnings?.total_distance_km || 0).toFixed(1)}</Text>
            <Text style={styles.statLabel}>KM Driven</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="time" size={18} color={COLORS.gold} />
            <Text style={styles.statValue}>
              {Math.round((earnings?.total_duration_minutes || 0) / 60)}h
            </Text>
            <Text style={styles.statLabel}>Online</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="trending-up" size={18} color={COLORS.success} />
            <Text style={styles.statValue}>${(earnings?.average_per_ride || 0).toFixed(2)}</Text>
            <Text style={styles.statLabel}>Avg/Trip</Text>
          </View>
        </View>

        {/* Bar Chart */}
        {dailyEarnings.length > 1 && (
          <View style={styles.chartSection}>
            <Text style={styles.sectionTitle}>Daily Breakdown</Text>
            <View style={styles.chartContainer}>
              {dailyEarnings.map((day, i) => {
                const barHeight = (day.earnings / maxDailyEarning) * 120;
                const dayLabel = new Date(day.date + 'T00:00:00').toLocaleDateString('en', { weekday: 'short' });
                return (
                  <View key={i} style={styles.barColumn}>
                    <Text style={styles.barValue}>
                      {day.earnings > 0 ? `$${day.earnings.toFixed(0)}` : ''}
                    </Text>
                    <View style={styles.barTrack}>
                      <LinearGradient
                        colors={[COLORS.accent, COLORS.accentDim]}
                        style={[styles.bar, { height: Math.max(barHeight, 4) }]}
                      />
                    </View>
                    <Text style={styles.barLabel}>{dayLabel}</Text>
                  </View>
                );
              })}
            </View>
          </View>
        )}

        {/* Trip List */}
        <View style={styles.tripsSection}>
          <Text style={styles.sectionTitle}>Recent Trips</Text>
          {loading ? (
            <ActivityIndicator color={COLORS.accent} style={{ marginTop: 20 }} />
          ) : tripEarnings.length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="car-outline" size={48} color={COLORS.surfaceLight} />
              <Text style={styles.emptyText}>No completed trips yet</Text>
              <Text style={styles.emptySubText}>Complete rides to see your earnings here</Text>
            </View>
          ) : (
            tripEarnings.map((trip) => (
              <View key={trip.ride_id} style={styles.tripCard}>
                <View style={styles.tripHeader}>
                  <View style={styles.tripDate}>
                    <Text style={styles.tripDateText}>
                      {trip.completed_at
                        ? new Date(trip.completed_at).toLocaleDateString('en', {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                        : ''}
                    </Text>
                  </View>
                  <Text style={styles.tripAmount}>+${trip.driver_earnings.toFixed(2)}</Text>
                </View>
                <View style={styles.tripRoute}>
                  <View style={styles.routeDots}>
                    <View style={[styles.routeDot, { backgroundColor: COLORS.accent }]} />
                    <View style={styles.routeLine} />
                    <View style={[styles.routeDot, { backgroundColor: COLORS.danger }]} />
                  </View>
                  <View style={styles.routeTexts}>
                    <Text style={styles.routeText} numberOfLines={1}>{trip.pickup_address}</Text>
                    <Text style={styles.routeText} numberOfLines={1}>{trip.dropoff_address}</Text>
                  </View>
                </View>
                <View style={styles.tripMeta}>
                  <Text style={styles.tripMetaText}>{trip.distance_km.toFixed(1)} km</Text>
                  <Text style={styles.tripMetaDivider}>·</Text>
                  <Text style={styles.tripMetaText}>{trip.duration_minutes} min</Text>
                  {trip.tip_amount > 0 && (
                    <>
                      <Text style={styles.tripMetaDivider}>·</Text>
                      <Text style={[styles.tripMetaText, { color: COLORS.gold }]}>
                        Tip: ${trip.tip_amount.toFixed(2)}
                      </Text>
                    </>
                  )}
                  {trip.rider_rating !== null && (
                    <>
                      <Text style={styles.tripMetaDivider}>·</Text>
                      <Ionicons name="star" size={12} color={COLORS.gold} />
                      <Text style={styles.tripMetaText}>{trip.rider_rating}</Text>
                    </>
                  )}
                </View>
              </View>
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
    backgroundColor: COLORS.primary,
  },
  header: {
    paddingTop: Platform.OS === 'ios' ? 60 : 45,
    paddingHorizontal: 20,
    paddingBottom: 20,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  headerTitle: {
    color: COLORS.text,
    fontSize: 26,
    fontWeight: '800',
  },
  payoutBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(0,212,170,0.1)',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    gap: 6,
  },
  payoutBtnText: {
    color: COLORS.accent,
    fontSize: 14,
    fontWeight: '600',
  },
  periodRow: {
    flexDirection: 'row',
    backgroundColor: COLORS.surfaceLight,
    borderRadius: 12,
    padding: 3,
    marginBottom: 20,
  },
  periodBtn: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: 10,
    alignItems: 'center',
  },
  periodBtnActive: {
    backgroundColor: COLORS.accent,
  },
  periodText: {
    color: COLORS.textDim,
    fontSize: 13,
    fontWeight: '600',
  },
  periodTextActive: {
    color: '#fff',
  },
  totalContainer: {
    alignItems: 'center',
  },
  totalLabel: {
    color: COLORS.textDim,
    fontSize: 11,
    letterSpacing: 1.5,
    fontWeight: '600',
  },
  totalAmount: {
    color: COLORS.text,
    fontSize: 44,
    fontWeight: '800',
    marginTop: 4,
  },
  tipsBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: 'rgba(255, 215, 0, 0.1)',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 20,
    marginTop: 8,
  },
  tipsText: {
    color: COLORS.gold,
    fontSize: 12,
    fontWeight: '600',
  },
  content: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 16,
  },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    marginBottom: 20,
  },
  statCard: {
    width: (width - 42) / 2,
    backgroundColor: COLORS.surface,
    borderRadius: 16,
    padding: 16,
    alignItems: 'center',
    gap: 6,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.04)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 8,
    elevation: 3,
  },
  statValue: {
    color: COLORS.text,
    fontSize: 20,
    fontWeight: '800',
  },
  statLabel: {
    color: COLORS.textDim,
    fontSize: 11,
    letterSpacing: 0.3,
  },
  chartSection: {
    marginBottom: 20,
  },
  sectionTitle: {
    color: COLORS.text,
    fontSize: 17,
    fontWeight: '700',
    marginBottom: 14,
  },
  chartContainer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
    backgroundColor: COLORS.surface,
    borderRadius: 16,
    padding: 16,
    height: 200,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.04)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 8,
    elevation: 3,
  },
  barColumn: {
    flex: 1,
    alignItems: 'center',
    gap: 6,
  },
  barValue: {
    color: COLORS.accent,
    fontSize: 10,
    fontWeight: '600',
  },
  barTrack: {
    width: 20,
    height: 130,
    backgroundColor: COLORS.surfaceLight,
    borderRadius: 10,
    justifyContent: 'flex-end',
    overflow: 'hidden',
  },
  bar: {
    width: '100%',
    borderRadius: 10,
  },
  barLabel: {
    color: COLORS.textDim,
    fontSize: 10,
    fontWeight: '600',
  },
  tripsSection: {
    paddingBottom: 20,
  },
  emptyState: {
    alignItems: 'center',
    paddingVertical: 40,
    gap: 8,
  },
  emptyText: {
    color: COLORS.textDim,
    fontSize: 16,
    fontWeight: '600',
  },
  emptySubText: {
    color: COLORS.surfaceLight,
    fontSize: 13,
  },
  tripCard: {
    backgroundColor: COLORS.surface,
    borderRadius: 16,
    padding: 16,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.04)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 8,
    elevation: 3,
  },
  tripHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  tripDate: {},
  tripDateText: {
    color: COLORS.textDim,
    fontSize: 12,
  },
  tripAmount: {
    color: COLORS.accent,
    fontSize: 18,
    fontWeight: '800',
  },
  tripRoute: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 10,
  },
  routeDots: {
    alignItems: 'center',
    width: 12,
    paddingTop: 4,
  },
  routeDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  routeLine: {
    width: 2,
    height: 14,
    backgroundColor: COLORS.surfaceLight,
    marginVertical: 2,
  },
  routeTexts: {
    flex: 1,
    gap: 8,
  },
  routeText: {
    color: COLORS.text,
    fontSize: 13,
    fontWeight: '500',
  },
  tripMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: COLORS.surfaceLight,
  },
  tripMetaText: {
    color: COLORS.textDim,
    fontSize: 12,
  },
  tripMetaDivider: {
    color: COLORS.surfaceLight,
    fontSize: 12,
  },
});
