import React, { useEffect, useState, useMemo, useCallback } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList,
  ActivityIndicator, RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '@shared/api/client';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface AppNotification {
  id: string;
  title: string;
  body: string;
  type: string;
  is_read: boolean;
  created_at: string;
}

function getRelativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${diffDay}d ago`;
}

function getTypeIcon(type: string): { name: string; color: string } {
  switch (type) {
    case 'ride_update':
    case 'ride':
      return { name: 'car', color: '' }; // primary color — filled at render time
    case 'promotion':
      return { name: 'gift', color: '#F59E0B' };
    case 'safety':
      return { name: 'shield-checkmark', color: '#DC2626' };
    default:
      return { name: 'notifications', color: '' }; // textDim — filled at render time
  }
}

export default function NotificationsScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const loadNotifications = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.get('/notifications?limit=50&offset=0');
      setNotifications(res.data.notifications || []);
      setUnreadCount(res.data.unread_count ?? 0);
    } catch {}
    finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { loadNotifications(); }, [loadNotifications]);

  const handleRefresh = () => {
    setRefreshing(true);
    loadNotifications(true);
  };

  const handleMarkRead = async (item: AppNotification) => {
    if (item.is_read) return;
    // Optimistic update
    setNotifications(prev =>
      prev.map(n => n.id === item.id ? { ...n, is_read: true } : n)
    );
    setUnreadCount(prev => Math.max(0, prev - 1));
    try {
      await api.put(`/notifications/${item.id}/read`);
    } catch {
      // Roll back
      setNotifications(prev =>
        prev.map(n => n.id === item.id ? { ...n, is_read: false } : n)
      );
      setUnreadCount(prev => prev + 1);
    }
  };

  const handleMarkAllRead = async () => {
    if (unreadCount === 0) return;
    // Optimistic update
    setNotifications(prev => prev.map(n => ({ ...n, is_read: true })));
    setUnreadCount(0);
    try {
      await api.put('/notifications/read-all');
    } catch {
      // Re-fetch to restore accurate state
      loadNotifications(true);
    }
  };

  const renderNotification = ({ item }: { item: AppNotification }) => {
    const typeInfo = getTypeIcon(item.type);
    const iconColor = typeInfo.color || (
      typeInfo.name === 'car' ? colors.primary : colors.textDim
    );

    return (
      <TouchableOpacity
        style={[styles.card, !item.is_read && styles.cardUnread]}
        onPress={() => handleMarkRead(item)}
        activeOpacity={0.7}
      >
        {!item.is_read && <View style={[styles.unreadBar, { backgroundColor: colors.primary }]} />}
        <View style={[styles.iconWrap, { backgroundColor: !item.is_read ? `${iconColor}18` : colors.surfaceLight }]}>
          <Ionicons name={typeInfo.name as any} size={22} color={iconColor} />
        </View>
        <View style={styles.cardContent}>
          <View style={styles.cardTopRow}>
            <Text style={[styles.cardTitle, !item.is_read && styles.cardTitleUnread]} numberOfLines={1}>
              {item.title}
            </Text>
            <Text style={styles.cardTime}>{getRelativeTime(item.created_at)}</Text>
          </View>
          <Text style={styles.cardBody} numberOfLines={2}>{item.body}</Text>
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Notifications</Text>
        {unreadCount > 0 ? (
          <TouchableOpacity style={styles.markAllBtn} onPress={handleMarkAllRead}>
            <Text style={styles.markAllText}>Mark all read</Text>
          </TouchableOpacity>
        ) : (
          <View style={{ width: 90 }} />
        )}
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <FlatList
          data={notifications}
          renderItem={renderNotification}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={handleRefresh}
              tintColor={colors.primary}
              colors={[colors.primary]}
            />
          }
          ListEmptyComponent={
            <View style={styles.empty}>
              <Ionicons name="notifications-off-outline" size={52} color="#DDD" />
              <Text style={styles.emptyTitle}>No notifications</Text>
              <Text style={styles.emptySub}>You're all caught up! Check back later.</Text>
            </View>
          }
        />
      )}

      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    markAllBtn: { paddingHorizontal: 4, paddingVertical: 6 },
    markAllText: { fontSize: 13, fontWeight: '600', color: colors.primary },

    center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    list: { padding: 16 },

    card: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surfaceLight,
      borderRadius: 16, padding: 14, marginBottom: 10,
      overflow: 'hidden',
    },
    cardUnread: {
      backgroundColor: `${colors.primary}0A`,
    },
    unreadBar: {
      position: 'absolute', left: 0, top: 0, bottom: 0, width: 4,
      borderTopLeftRadius: 16, borderBottomLeftRadius: 16,
    },
    iconWrap: {
      width: 46, height: 46, borderRadius: 14,
      justifyContent: 'center', alignItems: 'center', marginRight: 12, marginLeft: 6,
    },
    cardContent: { flex: 1 },
    cardTopRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 3 },
    cardTitle: { fontSize: 14, fontWeight: '600', color: colors.text, flex: 1, marginRight: 8 },
    cardTitleUnread: { fontWeight: '700' },
    cardTime: { fontSize: 11, color: colors.textDim },
    cardBody: { fontSize: 13, color: colors.textDim, lineHeight: 18 },

    empty: { alignItems: 'center', paddingVertical: 60 },
    emptyTitle: { fontSize: 17, fontWeight: '700', color: colors.text, marginTop: 14 },
    emptySub: { fontSize: 13, color: colors.textDim, marginTop: 4, textAlign: 'center' },
  });
}
