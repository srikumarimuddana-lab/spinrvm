import React, { useEffect, useState, useMemo, useCallback } from 'react';
import {
  View, StyleSheet, TouchableOpacity, FlatList,
  ActivityIndicator, RefreshControl, Modal, Pressable,
} from 'react-native';
import { Text } from '@shared/components/Text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SPACING, FONT } from '@shared/utils/responsive';

interface AppNotification {
  id: string;
  title: string;
  body: string;
  type: string;
  data?: Record<string, string>;
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
      return { name: 'car', color: '' };
    case 'promotion':
      return { name: 'gift', color: '' };
    case 'safety':
      return { name: 'shield-checkmark', color: '' };
    case 'lost_and_found':
    case 'lost_and_found_message':
      return { name: 'bag-handle', color: '' };
    case 'chat_message':
      return { name: 'chatbubble', color: '' };
    default:
      return { name: 'notifications', color: '' };
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
  const [loadFailed, setLoadFailed] = useState(false);
  // Notifications whose `type` (or a mapped type missing its ride/case id)
  // has no destination screen previously did nothing at all when tapped
  // beyond marking as read, with no way to read past the row's own
  // 2-line-truncated body. This shows the full title/body in place instead.
  const [selectedNotification, setSelectedNotification] = useState<AppNotification | null>(null);
  const loadNotifications = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.get<{ notifications?: AppNotification[]; unread_count?: number }>('/notifications?limit=50&offset=0');
      setNotifications(res.data.notifications || []);
      setUnreadCount(res.data.unread_count ?? 0);
      setLoadFailed(false);
    } catch (err) {
      // A failed fetch leaves `notifications` empty, which the list would
      // otherwise render as the cheerful "You're all caught up!" state —
      // indistinguishable from a genuinely empty inbox. Track the failure so
      // the empty state can say what actually happened and offer a retry.
      console.error('[notifications]', err);
      setLoadFailed(true);
    }
    finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // loadNotifications is a useCallback with a stable ([]) dep array, so
  // this fires once on mount; the state it sets isn't in this effect's deps.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadNotifications(); }, [loadNotifications]);

  const handleRefresh = () => {
    setRefreshing(true);
    loadNotifications(true);
  };

  const handleNotificationPress = (item: AppNotification) => {
    if (!item.is_read) {
      setNotifications(prev =>
        prev.map(n => n.id === item.id ? { ...n, is_read: true } : n)
      );
      setUnreadCount(prev => Math.max(0, prev - 1));
      api.put(`/notifications/${item.id}/read`).catch(() => {
        setNotifications(prev =>
          prev.map(n => n.id === item.id ? { ...n, is_read: false } : n)
        );
        setUnreadCount(prev => prev + 1);
      });
    }

    const caseId = item.data?.case_id;
    const rideId = item.data?.ride_id;
    switch (item.type) {
      case 'lost_and_found':
      case 'lost_and_found_message':
        if (caseId) router.push({ pathname: '/lost-and-found-chat', params: { caseId } } as any);
        else router.push('/lost-and-found' as any);
        return;
      case 'chat_message':
        if (rideId) { router.push({ pathname: '/chat-driver', params: { rideId } } as any); return; }
        break;
      case 'ride_completed':
        if (rideId) { router.push({ pathname: '/ride-completed', params: { rideId } } as any); return; }
        break;
      case 'driver_accepted':
      case 'driver_arrived':
        if (rideId) { router.push({ pathname: '/driver-arriving', params: { rideId } } as any); return; }
        break;
      default:
        break;
    }
    // No destination reached (unmapped type, or a mapped type missing its
    // required id) — show the full text in place instead of doing nothing.
    setSelectedNotification(item);
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
    const iconColor = typeInfo.color || (() => {
      switch (typeInfo.name) {
        case 'car': return colors.primary;
        case 'gift': return colors.orange;
        case 'shield-checkmark': return colors.danger;
        case 'bag-handle': return colors.orange;
        case 'chatbubble': return colors.primary;
        default: return colors.textDim;
      }
    })();

    return (
      <TouchableOpacity
        style={[styles.card, !item.is_read && styles.cardUnread]}
        onPress={() => handleNotificationPress(item)}
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
            loadFailed ? (
              <View style={styles.empty}>
                <Ionicons name="cloud-offline-outline" size={52} color={colors.danger} />
                <Text style={styles.emptyTitle}>Couldn&apos;t load notifications</Text>
                <Text style={styles.emptySub}>Check your connection and try again.</Text>
                <TouchableOpacity style={styles.retryBtn} onPress={() => loadNotifications()}>
                  <Text style={styles.retryText}>Retry</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <View style={styles.empty}>
                <Ionicons name="notifications-off-outline" size={52} color="#DDD" />
                <Text style={styles.emptyTitle}>No notifications</Text>
                <Text style={styles.emptySub}>You&apos;re all caught up! Check back later.</Text>
              </View>
            )
          }
        />
      )}

      <Modal
        visible={!!selectedNotification}
        transparent
        animationType="fade"
        onRequestClose={() => setSelectedNotification(null)}
        testID="notification-detail-modal"
      >
        <View style={styles.modalBackdrop}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setSelectedNotification(null)} />
          <View style={styles.modalCard}>
            {selectedNotification && (() => {
              const typeInfo = getTypeIcon(selectedNotification.type);
              const iconColor = typeInfo.color || (() => {
                switch (typeInfo.name) {
                  case 'car': return colors.primary;
                  case 'gift': return colors.orange;
                  case 'shield-checkmark': return colors.danger;
                  case 'bag-handle': return colors.orange;
                  case 'chatbubble': return colors.primary;
                  default: return colors.textDim;
                }
              })();
              return (
                <>
                  <View
                    accessible={false}
                    importantForAccessibility="no-hide-descendants"
                    style={[styles.iconWrap, { backgroundColor: `${iconColor}18`, marginBottom: SPACING.sm, marginLeft: 0 }]}
                  >
                    <Ionicons name={typeInfo.name as any} size={22} color={iconColor} />
                  </View>
                  <Text style={styles.modalTitle}>{selectedNotification.title}</Text>
                  <Text style={styles.modalTime}>{getRelativeTime(selectedNotification.created_at)}</Text>
                  <Text style={styles.modalBody} testID="notification-detail-body">{selectedNotification.body}</Text>
                </>
              );
            })()}
            <TouchableOpacity
              style={styles.modalCloseBtn}
              onPress={() => setSelectedNotification(null)}
              accessibilityRole="button"
              accessibilityLabel="Close"
            >
              <Text style={styles.modalCloseText}>Close</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: SPACING.md, paddingVertical: 12,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    markAllBtn: { paddingHorizontal: SPACING.xs, paddingVertical: 6 },
    markAllText: { fontSize: FONT.bodySm, fontWeight: '600', color: colors.primary },

    center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    list: { padding: SPACING.md },

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
    cardTitle: { fontSize: 14, fontWeight: '600', color: colors.text, flex: 1, marginRight: SPACING.sm },
    cardTitleUnread: { fontWeight: '700' },
    cardTime: { fontSize: FONT.label, color: colors.textDim },
    cardBody: { fontSize: FONT.bodySm, color: colors.textDim, lineHeight: 18 },

    empty: { alignItems: 'center', paddingVertical: 60 },
    emptyTitle: { fontSize: 17, fontWeight: '700', color: colors.text, marginTop: 14 },
    emptySub: { fontSize: FONT.bodySm, color: colors.textDim, marginTop: SPACING.xs, textAlign: 'center' },
    retryBtn: {
      marginTop: SPACING.md,
      paddingHorizontal: 20,
      paddingVertical: 10,
      borderRadius: 20,
      backgroundColor: colors.primary,
    },
    retryText: { color: '#fff', fontSize: 14, fontWeight: '600' },
    modalBackdrop: {
      flex: 1,
      backgroundColor: 'rgba(0,0,0,0.5)',
      justifyContent: 'center',
      alignItems: 'center',
      padding: SPACING.lg,
    },
    modalCard: {
      width: '100%',
      maxWidth: 420,
      backgroundColor: colors.surface,
      borderRadius: 20,
      padding: SPACING.lg,
    },
    modalTitle: { color: colors.text, fontSize: 17, fontWeight: '700', marginBottom: 4 },
    modalTime: { color: colors.textDim, fontSize: FONT.label, marginBottom: SPACING.sm },
    modalBody: { color: colors.text, fontSize: FONT.bodySm, lineHeight: 21, marginBottom: SPACING.lg },
    modalCloseBtn: {
      alignSelf: 'flex-end',
      paddingHorizontal: 20,
      // 14 (not the row-level retryBtn's 10) since the backdrop tap is the
      // only other dismiss path and this needs to clear a ~44pt touch target
      // on its own.
      paddingVertical: 14,
      borderRadius: 20,
      backgroundColor: colors.primary,
    },
    modalCloseText: { color: '#fff', fontSize: 14, fontWeight: '600' },
  });
}
