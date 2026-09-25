import React, { useCallback, useState } from 'react';
import { View, StyleSheet, FlatList, ActivityIndicator, TouchableOpacity } from 'react-native';
import { Text } from '@shared/components/Text';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import { SPACING, FONT } from '@shared/utils/responsive';
import { ScreenHeader } from '../../components/ScreenHeader';
import SafeRefreshControl from '../../components/SafeRefreshControl';

type UpcomingRide = {
  id: string;
  status?: string;
  scheduled_time?: string;
  pickup_address?: string;
  dropoff_address?: string;
};

// Date and time on separate lines, no seconds. The previous single
// toLocaleString('en-CA') rendered e.g. "2026-09-25, 3:30:00 p.m." in one
// cramped line.
function formatWhen(raw?: string): { date: string; time: string } | null {
  if (!raw) return null;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return null;
  return {
    date: d.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' }),
    time: d.toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' }),
  };
}

export default function UpcomingRidesScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [rides, setRides] = useState<UpcomingRide[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    api.get<{ rides: UpcomingRide[] }>('/drivers/rides/upcoming').then(
      (res) => {
        setRides(res.data?.rides ?? []);
        setError(null);
        setLoading(false);
        setRefreshing(false);
      },
      () => {
        setError('Could not load upcoming trips.');
        setLoading(false);
        setRefreshing(false);
      },
    );
  }, []);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const renderEmpty = () => {
    // Error and empty are mutually exclusive: before, a failed load showed
    // the error AND "No upcoming scheduled trips." underneath it.
    if (error) {
      return (
        <View style={styles.emptyWrap}>
          <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
          <Text style={[styles.emptyText, { color: colors.error }]}>{error}</Text>
          <TouchableOpacity
            onPress={() => load()}
            style={[styles.retryBtn, { backgroundColor: colors.primary }]}
            activeOpacity={0.8}
            accessibilityRole="button"
            accessibilityLabel="Retry loading upcoming trips"
          >
            <Ionicons name="refresh" size={18} color="#fff" />
            <Text style={styles.retryBtnText}>Try Again</Text>
          </TouchableOpacity>
        </View>
      );
    }
    return (
      <View style={styles.emptyWrap}>
        <Ionicons name="calendar-outline" size={40} color={colors.textSecondary} />
        <Text style={[styles.emptyText, { color: colors.textSecondary }]}>No upcoming scheduled trips.</Text>
      </View>
    );
  };

  return (
    <View style={[styles.wrap, { backgroundColor: colors.background }]}>
      <ScreenHeader title="Upcoming trips" />
      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : (
        <FlatList
          data={error ? [] : rides}
          keyExtractor={(item) => item.id}
          contentContainerStyle={[styles.list, { paddingBottom: insets.bottom + 16 }]}
          refreshControl={<SafeRefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor={colors.primary} />}
          ListEmptyComponent={renderEmpty}
          renderItem={({ item }) => {
            const when = formatWhen(item.scheduled_time);
            return (
              <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                <View style={styles.whenRow}>
                  <Ionicons name="time-outline" size={18} color={colors.primary} />
                  <Text style={[styles.whenDate, { color: colors.text }]}>{when ? when.date : 'Scheduled'}</Text>
                  {when ? <Text style={[styles.whenTime, { color: colors.primary }]}>{when.time}</Text> : null}
                </View>
                <View style={styles.stopRow}>
                  <View style={[styles.dot, { backgroundColor: colors.success }]} />
                  <Text style={[styles.stopText, { color: colors.text }]} numberOfLines={2}>
                    {item.pickup_address || 'Pickup'}
                  </Text>
                </View>
                <View style={styles.stopRow}>
                  <View style={[styles.dot, { backgroundColor: colors.primary }]} />
                  <Text style={[styles.stopText, { color: colors.text }]} numberOfLines={2}>
                    {item.dropoff_address || 'Drop-off'}
                  </Text>
                </View>
              </View>
            );
          }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  list: { padding: SPACING.md },
  card: { borderWidth: 1, borderRadius: 14, padding: SPACING.md, marginBottom: SPACING.sm },
  whenRow: { flexDirection: 'row', alignItems: 'center', marginBottom: SPACING.sm },
  whenDate: { flex: 1, marginLeft: 8, fontSize: FONT.bodyMd, fontWeight: '700' },
  whenTime: { fontSize: FONT.bodyMd, fontWeight: '700' },
  stopRow: { flexDirection: 'row', alignItems: 'flex-start', marginTop: 6 },
  dot: { width: 8, height: 8, borderRadius: 4, marginTop: 6, marginRight: 10 },
  stopText: { flex: 1, fontSize: FONT.bodySm, lineHeight: 20 },
  emptyWrap: { alignItems: 'center', paddingTop: 48, paddingHorizontal: SPACING.lg },
  emptyText: { marginTop: SPACING.sm, textAlign: 'center', fontSize: FONT.bodyMd },
  // Same shape as lost-and-found.tsx's retry button.
  retryBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: SPACING.md,
    paddingHorizontal: SPACING.lg, paddingVertical: 12, borderRadius: 25, minHeight: 44,
  },
  retryBtnText: { color: '#fff', fontSize: FONT.bodyLg, fontWeight: '600' },
});
