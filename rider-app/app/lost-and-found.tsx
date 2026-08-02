import React, { useState, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useFocusEffect } from 'expo-router/react-navigation';
import { Ionicons } from '@expo/vector-icons';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface LostFoundCase {
  id: string;
  ride_id?: string;
  item_description: string;
  item_category?: string;
  status: string;
  reporter_type: string;
  created_at: string;
  updated_at: string;
}

const STATUS_LABELS: Record<string, string> = {
  reported: 'Awaiting Driver',
  driver_notified: 'Driver Notified',
  driver_found: 'Item Found',
  found: 'Item Found',
  not_found: 'Not Found',
  returned: 'Returned',
  unclaimed: 'Unclaimed',
  resolved: 'Resolved',
  closed: 'Closed',
  admin_created: 'Under Review',
};

const STATUS_COLORS: Record<string, string> = {
  reported: '#F59E0B',
  driver_notified: '#3B82F6',
  driver_found: '#10B981',
  found: '#10B981',
  not_found: '#EF4444',
  returned: '#6B7280',
  unclaimed: '#6B7280',
  resolved: '#6B7280',
  closed: '#6B7280',
  admin_created: '#8B5CF6',
};

const CATEGORY_ICONS: Record<string, string> = {
  electronics: 'phone-portrait',
  clothing: 'shirt',
  bag: 'bag',
  document: 'document-text',
  keys: 'key',
  other: 'help-circle',
};

export default function LostAndFoundScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [cases, setCases] = useState<LostFoundCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.get<{ cases: LostFoundCase[] }>('/lost-and-found');
      setCases(res.data?.cases ?? []);
    } catch {
      // silently keep stale data on error
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    load();
  }, [load]);

  const renderCase = ({ item }: { item: LostFoundCase }) => {
    const statusColor = STATUS_COLORS[item.status] ?? '#6B7280';
    const statusLabel = STATUS_LABELS[item.status] ?? item.status;
    const icon = (CATEGORY_ICONS[item.item_category ?? ''] ?? 'help-circle') as any;
    const date = new Date(item.created_at).toLocaleDateString('en-CA', {
      month: 'short', day: 'numeric', year: 'numeric',
    });
    const isDriverReported = item.reporter_type === 'driver';

    return (
      <TouchableOpacity
        style={styles.card}
        onPress={() => router.push({ pathname: '/lost-and-found-chat', params: { caseId: item.id } } as any)}
        activeOpacity={0.7}
      >
        <View style={[styles.iconBadge, { backgroundColor: `${statusColor}18` }]}>
          <Ionicons name={icon} size={22} color={statusColor} />
        </View>

        <View style={styles.cardBody}>
          <View style={styles.cardRow}>
            <Text style={styles.description} numberOfLines={1}>{item.item_description}</Text>
            <View style={[styles.statusPill, { backgroundColor: `${statusColor}18` }]}>
              <Text style={[styles.statusText, { color: statusColor }]}>{statusLabel}</Text>
            </View>
          </View>
          <Text style={styles.meta}>
            {isDriverReported ? 'Driver reported • ' : ''}{date}
          </Text>
        </View>

        <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
      </TouchableOpacity>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.back}>
          <Ionicons name="chevron-back" size={28} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title}>Lost & Found</Text>
        <View style={{ width: 44 }} />
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : cases.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="bag-handle-outline" size={56} color={colors.textDim} />
          <Text style={styles.emptyTitle}>No cases yet</Text>
          <Text style={styles.emptyHint}>
            If you left something in a ride, report it from the ride summary in the Activity tab.
          </Text>
        </View>
      ) : (
        <FlatList
          data={cases}
          keyExtractor={c => c.id}
          renderItem={renderCase}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        />
      )}
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.background },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    back: { padding: 4 },
    title: { fontSize: 18, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text },
    center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32, gap: 12 },
    emptyTitle: { fontSize: 16, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },
    emptyHint: { fontSize: 14, color: colors.textDim, textAlign: 'center', lineHeight: 20 },
    list: { padding: 16, gap: 12 },
    card: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      borderRadius: 14,
      padding: 14,
      gap: 12,
      borderWidth: 1,
      borderColor: colors.border,
    },
    iconBadge: {
      width: 44,
      height: 44,
      borderRadius: 22,
      alignItems: 'center',
      justifyContent: 'center',
    },
    cardBody: { flex: 1, gap: 4 },
    cardRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    description: {
      flex: 1,
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    statusPill: { borderRadius: 8, paddingHorizontal: 8, paddingVertical: 2 },
    statusText: { fontSize: 11, fontFamily: 'PlusJakartaSans_600SemiBold' },
    meta: { fontSize: 13, color: colors.textDim },
  });
}
