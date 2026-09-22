import React, { useMemo, useState, useCallback } from 'react';
import {
  View, StyleSheet, TouchableOpacity, FlatList,
  ActivityIndicator, RefreshControl, Modal, Pressable,
} from 'react-native';
import { Text } from '@shared/components/Text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SPACING, FONT } from '@shared/utils/responsive';
import {
  useNotifications,
  useMarkNotificationRead,
  useMarkAllNotificationsRead,
  useDeleteNotification,
  useClearNotifications,
} from '@shared/hooks/queries';
import ConfirmSheet, { type ConfirmSheetButton, type ConfirmVariant } from '../components/ConfirmSheet';

interface AppNotification {
  id: string;
  title: string;
  body: string;
  type: string;
  data?: Record<string, string>;
  is_read: boolean;
  created_at: string;
}

// Category tabs reuse the SAME `type` taxonomy getTypeIcon already maps —
// no new category names invented beyond what the codebase's `type` values
// (and backend/routes/notifications.py's NOTIFICATION_DEEPLINKS) already use.
type CategoryFilter = 'all' | 'rides' | 'promotions' | 'safety' | 'general';

const CATEGORY_TABS: { key: CategoryFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'rides', label: 'Rides' },
  { key: 'promotions', label: 'Promotions' },
  { key: 'safety', label: 'Safety' },
  { key: 'general', label: 'General' },
];

function matchesCategory(type: string, category: CategoryFilter): boolean {
  if (category === 'all') return true;
  switch (category) {
    case 'rides':
      return type === 'ride_update' || type === 'ride' || type === 'ride_completed' ||
        type === 'driver_accepted' || type === 'driver_arrived' || type === 'chat_message';
    case 'promotions':
      return type === 'promotion';
    case 'safety':
      return type === 'safety';
    case 'general':
      return !['ride_update', 'ride', 'ride_completed', 'driver_accepted', 'driver_arrived',
        'chat_message', 'promotion', 'safety', 'lost_and_found', 'lost_and_found_message'].includes(type);
    default:
      return true;
  }
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

  const { data: rawData, isLoading, isError, isFetching, refetch } = useNotifications(50);
  const data = rawData as { notifications?: AppNotification[]; unread_count?: number } | undefined;
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  const deleteNotification = useDeleteNotification();
  const clearNotifications = useClearNotifications();

  const notifications: AppNotification[] = data?.notifications ?? [];
  const unreadCount: number = data?.unread_count ?? 0;

  const [category, setCategory] = useState<CategoryFilter>('all');
  const filtered = useMemo(
    () => notifications.filter((n) => matchesCategory(n.type, category)),
    [notifications, category],
  );

  const [selectedNotification, setSelectedNotification] = useState<AppNotification | null>(null);
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean; title: string; message?: string; variant?: ConfirmVariant; buttons?: ConfirmSheetButton[];
  }>({ visible: false, title: '' });

  const handleNotificationPress = (item: AppNotification) => {
    if (!item.is_read) {
      markRead.mutate(item.id);
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
    setSelectedNotification(item);
  };

  const handleMarkAllRead = () => {
    if (unreadCount === 0) return;
    markAllRead.mutate();
  };

  const handleDelete = useCallback((item: AppNotification) => {
    setConfirmSheet({
      visible: true,
      title: 'Delete Notification',
      message: 'Remove this notification? This can’t be undone.',
      variant: 'warning',
      buttons: [
        { text: 'Delete', style: 'destructive', onPress: () => deleteNotification.mutate(item.id) },
        { text: 'Cancel', style: 'cancel' },
      ],
    });
  }, [deleteNotification]);

  const handleClearAll = useCallback(() => {
    if (notifications.length === 0) return;
    setConfirmSheet({
      visible: true,
      title: 'Clear All Notifications',
      message: 'This removes every notification in your inbox. This can’t be undone.',
      variant: 'danger',
      buttons: [
        { text: 'Clear All', style: 'destructive', onPress: () => clearNotifications.mutate(false) },
        { text: 'Cancel', style: 'cancel' },
      ],
    });
  }, [notifications.length, clearNotifications]);

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
      <View style={[styles.card, !item.is_read && styles.cardUnread]}>
        <TouchableOpacity
          style={styles.cardTouchable}
          onPress={() => handleNotificationPress(item)}
          activeOpacity={0.7}
          accessibilityLabel={`${item.title}${item.is_read ? '' : ', unread'}`}
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
        <TouchableOpacity
          onPress={() => handleDelete(item)}
          style={styles.deleteBtn}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          accessibilityRole="button"
          accessibilityLabel="Delete notification"
        >
          <Ionicons name="trash-outline" size={18} color={colors.textDim} />
        </TouchableOpacity>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Notifications</Text>
        <View style={styles.headerActions}>
          {unreadCount > 0 && (
            <TouchableOpacity style={styles.headerActionBtn} onPress={handleMarkAllRead}>
              <Text style={styles.headerActionText}>Mark all read</Text>
            </TouchableOpacity>
          )}
          {notifications.length > 0 && (
            <TouchableOpacity
              style={styles.headerActionBtn}
              onPress={handleClearAll}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
              accessibilityRole="button"
              accessibilityLabel="Clear all notifications"
            >
              <Ionicons name="trash-outline" size={18} color={colors.danger} />
            </TouchableOpacity>
          )}
        </View>
      </View>

      <View style={styles.tabsRow} accessibilityRole="tablist">
        <View style={styles.tabsContent}>
          {CATEGORY_TABS.map((item) => {
            const active = category === item.key;
            return (
              <TouchableOpacity
                key={item.key}
                style={[styles.tab, active && { backgroundColor: colors.primaryDark }]}
                onPress={() => setCategory(item.key)}
                accessibilityRole="tab"
                accessibilityState={{ selected: active }}
              >
                <Text style={[styles.tabText, active && styles.tabTextActive]}>{item.label}</Text>
              </TouchableOpacity>
            );
          })}
        </View>
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <FlatList
          data={filtered}
          renderItem={renderNotification}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={isFetching && !isLoading}
              onRefresh={refetch}
              tintColor={colors.primary}
              colors={[colors.primary]}
            />
          }
          ListEmptyComponent={
            isError ? (
              <View style={styles.empty}>
                <Ionicons name="cloud-offline-outline" size={52} color={colors.danger} />
                <Text style={styles.emptyTitle}>Couldn&apos;t load notifications</Text>
                <Text style={styles.emptySub}>Check your connection and try again.</Text>
                <TouchableOpacity style={styles.retryBtn} onPress={() => refetch()}>
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

      <ConfirmSheet
        visible={confirmSheet.visible}
        title={confirmSheet.title}
        message={confirmSheet.message}
        variant={confirmSheet.variant}
        buttons={confirmSheet.buttons}
        onClose={() => setConfirmSheet((prev) => ({ ...prev, visible: false }))}
      />
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
    headerActions: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    headerActionBtn: { paddingHorizontal: SPACING.xs, paddingVertical: 6, minWidth: 36, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
    headerActionText: { fontSize: FONT.bodySm, fontWeight: '600', color: colors.primary },

    tabsRow: {
      borderBottomWidth: 1, borderBottomColor: colors.border,
      paddingVertical: SPACING.sm,
    },
    tabsContent: { flexDirection: 'row', flexWrap: 'wrap', paddingHorizontal: SPACING.md, gap: 8 },
    tab: {
      paddingHorizontal: 14, paddingVertical: 7, borderRadius: 20,
      backgroundColor: colors.surfaceLight,
      minHeight: 44, justifyContent: 'center',
    },
    tabText: { fontSize: FONT.bodySm, fontWeight: '600', color: colors.textDim },
    tabTextActive: { color: '#fff' },

    center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    list: { padding: SPACING.md },

    card: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surfaceLight,
      borderRadius: 16, marginBottom: 10,
      overflow: 'hidden',
    },
    cardTouchable: {
      flex: 1, flexDirection: 'row', alignItems: 'center', padding: 14,
    },
    deleteBtn: { paddingHorizontal: 14, alignSelf: 'stretch', justifyContent: 'center' },
    cardUnread: {
      backgroundColor: `${colors.primary}0A`,
    },
    unreadBar: {
      position: 'absolute', left: 0, top: 0, bottom: 0, width: 4,
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
      paddingVertical: 14,
      borderRadius: 20,
      backgroundColor: colors.primary,
    },
    modalCloseText: { color: '#fff', fontSize: 14, fontWeight: '600' },
  });
}
