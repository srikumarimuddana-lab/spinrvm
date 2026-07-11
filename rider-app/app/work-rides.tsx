import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useWorkProfileStore } from '../store/workProfileStore';

/**
 * Work ride history (fast-follow FF3): work-profile's "See all" navigated to
 * /work-rides, which never existed — this screen lists the rider's company
 * rides from GET /rider/work-profile/{companyId}/rides.
 */

interface WorkRide {
  id: string;
  status?: string;
  pickup_address?: string;
  dropoff_address?: string;
  grand_total?: number | string;
  created_at?: string;
}

export default function WorkRidesScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const params = useLocalSearchParams<{ companyId?: string }>();
  const activeCompanyId = useWorkProfileStore((s) => s.activeCompanyId);
  const companyId = (params.companyId || activeCompanyId || '').toString();

  const [rides, setRides] = useState<WorkRide[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!companyId) {
      setError('No work profile selected.');
      setLoading(false);
      return;
    }
    try {
      const res = await api.get<WorkRide[]>(`/rider/work-profile/${companyId}/rides`);
      setRides(Array.isArray(res.data) ? res.data : []);
      setError(null);
    } catch {
      setError('Could not load your work rides.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [companyId]);

  useEffect(() => {
    load();
  }, [load]);

  const renderRide = ({ item }: { item: WorkRide }) => (
    <TouchableOpacity
      style={styles.row}
      activeOpacity={0.7}
      onPress={() => router.push({ pathname: '/ride-details', params: { rideId: item.id } } as any)}
    >
      <View style={styles.rowIcon}>
        <Ionicons name="briefcase" size={18} color={colors.primary} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={styles.rowTitle} numberOfLines={1}>
          {item.pickup_address || 'Pickup'} → {item.dropoff_address || 'Dropoff'}
        </Text>
        <Text style={styles.rowSub}>
          {item.created_at ? new Date(item.created_at).toLocaleDateString('en-CA') : ''}
          {item.status ? ` · ${item.status}` : ''}
        </Text>
      </View>
      {item.grand_total != null && (
        <Text style={styles.rowAmount}>${Number(item.grand_total).toFixed(2)}</Text>
      )}
    </TouchableOpacity>
  );

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} accessibilityLabel="Go back">
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Work rides</Text>
        <View style={{ width: 24 }} />
      </View>

      {loading ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
      ) : error ? (
        <Text style={styles.error}>{error}</Text>
      ) : (
        <FlatList
          data={rides}
          keyExtractor={(r) => r.id}
          renderItem={renderRide}
          contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 24 }}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load();
              }}
              tintColor={colors.primary}
            />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>No work rides yet — turn on Work Mode when booking.</Text>
          }
        />
      )}
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
    },
    headerTitle: { fontSize: 17, fontWeight: '700', color: colors.text },
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      backgroundColor: colors.surfaceLight,
      borderRadius: 14,
      padding: 14,
      marginBottom: 10,
    },
    rowIcon: {
      backgroundColor: `${colors.primary}14`,
      borderRadius: 10,
      padding: 8,
    },
    rowTitle: { fontSize: 14, fontWeight: '600', color: colors.text },
    rowSub: { fontSize: 12, color: colors.textDim, marginTop: 2 },
    rowAmount: { fontSize: 14, fontWeight: '700', color: colors.text },
    error: { textAlign: 'center', color: colors.danger, marginTop: 40, paddingHorizontal: 24 },
    empty: { textAlign: 'center', color: colors.textDim, marginTop: 40 },
  });
}
