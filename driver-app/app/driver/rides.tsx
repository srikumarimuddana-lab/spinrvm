import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  FlatList,
  Platform,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';

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

type Filter = 'all' | 'completed' | 'cancelled';

export default function RidesScreen() {
  const router = useRouter();
  const { rideHistory, historyTotal, fetchRideHistory } = useDriverStore();
  const [filter, setFilter] = useState<Filter>('all');
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
    if (filter === 'all') return true;
    return r.status === filter;
  });

  const renderRideCard = ({ item }: { item: any }) => {
    const isCompleted = item.status === 'completed';
    const statusColor = isCompleted ? COLORS.accent : COLORS.danger;
    const statusLabel = isCompleted ? 'Completed' : 'Cancelled';

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
      <TouchableOpacity style={styles.rideCard} onPress={() => router.push(`/(driver)/ride-detail?id=${item.id}` as any)}>
        {/* Status and Date */}
        <View style={styles.cardHeader}>
          <View style={[styles.statusBadge, { backgroundColor: `${statusColor}15` }]}>
            <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
            <Text style={[styles.statusText, { color: statusColor }]}>{statusLabel}</Text>
          </View>
          <Text style={styles.dateText}>{formattedDate}</Text>
        </View>

        {/* Route */}
        <View style={styles.routeRow}>
          <View style={styles.routeDots}>
            <View style={[styles.dot, { backgroundColor: COLORS.accent }]} />
            <View style={styles.dotLine} />
            <View style={[styles.dot, { backgroundColor: COLORS.danger }]} />
          </View>
          <View style={styles.routeTexts}>
            <Text style={styles.routeAddress} numberOfLines={1}>
              {item.pickup_address || 'Pickup location'}
            </Text>
            <Text style={styles.routeAddress} numberOfLines={1}>
              {item.dropoff_address || 'Dropoff location'}
            </Text>
          </View>
        </View>

        {/* Fare and Trip Info */}
        <View style={styles.cardFooter}>
          <View style={styles.tripMeta}>
            {item.distance_km && (
              <Text style={styles.metaText}>{item.distance_km.toFixed(1)} km</Text>
            )}
            {item.duration_minutes && (
              <>
                <Text style={styles.metaDivider}>·</Text>
                <Text style={styles.metaText}>{item.duration_minutes} min</Text>
              </>
            )}
            {item.rider_rating !== null && item.rider_rating !== undefined && (
              <>
                <Text style={styles.metaDivider}>·</Text>
                <Ionicons name="star" size={12} color={COLORS.gold} />
                <Text style={styles.metaText}>{item.rider_rating}</Text>
              </>
            )}
          </View>
          {isCompleted && (
            <Text style={styles.fareText}>
              ${(item.driver_earnings || item.total_fare || 0).toFixed(2)}
            </Text>
          )}
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <View style={styles.container}>
      {/* Header */}
      <LinearGradient
        colors={[COLORS.surface, COLORS.primary]}
        style={styles.header}
      >
        <Text style={styles.headerTitle}>Ride History</Text>

        {/* Total Rides */}
        <View style={styles.totalContainer}>
          <Text style={styles.totalLabel}>TOTAL RIDES</Text>
          <Text style={styles.totalAmount}>
            {loading ? '--' : historyTotal}
          </Text>
        </View>
      </LinearGradient>

      {/* Filter Tabs */}
      <View style={styles.filterRow}>
        {(['all', 'completed', 'cancelled'] as Filter[]).map((f) => (
          <TouchableOpacity
            key={f}
            style={[styles.filterBtn, filter === f && styles.filterBtnActive]}
            onPress={() => setFilter(f)}
          >
            <Text style={[styles.filterText, filter === f && styles.filterTextActive]}>
              {f === 'all' ? 'All' : f === 'completed' ? 'Completed' : 'Cancelled'}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Rides List */}
      {loading ? (
        <ActivityIndicator color={COLORS.accent} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={filteredRides}
          renderItem={renderRideCard}
          keyExtractor={(item) => item.id || Math.random().toString()}
          contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 100 }}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.accent} />
          }
          ListEmptyComponent={
            <View style={styles.emptyState}>
              <Ionicons name="car-outline" size={56} color={COLORS.surfaceLight} />
              <Text style={styles.emptyTitle}>No rides yet</Text>
              <Text style={styles.emptySub}>Your completed rides will appear here</Text>
            </View>
          }
        />
      )}
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
  headerTitle: {
    color: COLORS.text,
    fontSize: 26,
    fontWeight: '800',
    marginBottom: 16,
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
  filterRow: {
    flexDirection: 'row',
    backgroundColor: COLORS.surfaceLight,
    borderRadius: 12,
    padding: 3,
    marginHorizontal: 16,
    marginBottom: 16,
  },
  filterBtn: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: 10,
    alignItems: 'center',
  },
  filterBtnActive: {
    backgroundColor: COLORS.accent,
  },
  filterText: {
    color: COLORS.textDim,
    fontSize: 13,
    fontWeight: '600',
  },
  filterTextActive: {
    color: '#fff',
  },
  rideCard: {
    backgroundColor: COLORS.surface,
    borderRadius: 18,
    padding: 16,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.04)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 8,
    elevation: 3,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 14,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  statusText: {
    fontSize: 12,
    fontWeight: '600',
  },
  dateText: {
    color: COLORS.textDim,
    fontSize: 11,
  },
  routeRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 12,
  },
  routeDots: {
    alignItems: 'center',
    width: 12,
    paddingTop: 3,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  dotLine: {
    width: 2,
    height: 16,
    backgroundColor: COLORS.surfaceLight,
    marginVertical: 2,
  },
  routeTexts: {
    flex: 1,
    gap: 10,
  },
  routeAddress: {
    color: COLORS.text,
    fontSize: 13,
    fontWeight: '500',
  },
  cardFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: COLORS.surfaceLight,
  },
  tripMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  metaText: {
    color: COLORS.textDim,
    fontSize: 12,
  },
  metaDivider: {
    color: COLORS.surfaceLight,
    fontSize: 12,
  },
  fareText: {
    color: COLORS.accent,
    fontSize: 18,
    fontWeight: '800',
  },
  emptyState: {
    alignItems: 'center',
    paddingVertical: 60,
    gap: 8,
  },
  emptyTitle: {
    color: COLORS.textDim,
    fontSize: 18,
    fontWeight: '600',
  },
  emptySub: {
    color: COLORS.surfaceLight,
    fontSize: 13,
  },
});
