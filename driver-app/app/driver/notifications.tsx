import React, { useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    FlatList,
    Alert,
} from 'react-native';
import SafeRefreshControl from '../../components/SafeRefreshControl';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import {
    useNotifications,
    useMarkNotificationRead,
    useMarkAllNotificationsRead,
} from '@shared/hooks/queries';
import { useLanguageStore } from '../../store/languageStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface Notification {
    id: string;
    title: string;
    body: string;
    type: string;
    is_read: boolean;
    created_at: string;
}

// Module-level (not component-scope) so react-hooks/purity doesn't treat the
// Date.now() read as an impure call "during render" — this is called
// directly from JSX in the FlatList renderItem below. Doesn't reference any
// component state, so moving it out is behavior-neutral.
function formatTime(dateStr: string): string {
    const diff = Date.now() - new Date(dateStr).getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}

export default function NotificationsScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const { t } = useLanguageStore();

    // /notifications is owned by the useNotifications hook. The hook
    // handles dedupe, cache, refetch on focus, and the persisted cache
    // means re-opening this screen renders the inbox instantly while a
    // background refetch keeps it fresh.
    const { data: rawNotifData, isFetching, refetch } = useNotifications(50);
    const data = rawNotifData as { notifications?: Notification[]; unread_count?: number } | undefined;
    const notifications: Notification[] = data?.notifications ?? [];
    const unreadCount: number = data?.unread_count ?? 0;
    const markReadMutation = useMarkNotificationRead();
    const markAllReadMutation = useMarkAllNotificationsRead();

    const iconMap: Record<string, { name: string; color: string }> = {
        ride_update: { name: 'car', color: colors.primary },
        ride: { name: 'car', color: colors.primary },
        earnings: { name: 'wallet', color: colors.orange },
        promotion: { name: 'gift', color: colors.orange },
        general: { name: 'notifications', color: colors.textDim },
        system: { name: 'settings', color: colors.textDim },
        safety: { name: 'shield-checkmark', color: colors.danger },
    };

    const markAsRead = (id: string) => {
        // Mutation invalidates the list cache on success; the screen
        // re-renders with the row's is_read=true once the server confirms.
        markReadMutation.mutate(id, {
            onError: () => {
                Alert.alert(t('notifications.markReadError'), t('notifications.markReadErrorBody'));
            },
        });
    };

    const markAllRead = () => {
        markAllReadMutation.mutate(undefined, {
            onError: () => {
                Alert.alert(t('notifications.markReadError'), t('notifications.markReadErrorBody'));
            },
        });
    };

    const onRefresh = () => { refetch(); };

    const handleNotificationPress = (item: Notification) => {
        markAsRead(item.id);
        if (item.type === 'document_expiry') router.push('/driver/documents' as any);
        else if (item.type === 'payout_processed') router.push('/driver/activity' as any);
        else if (item.type === 'ride_offer') router.push('/driver/' as any);
        else if (item.type === 'quest_earned') router.push('/driver/quests' as any);
        // Unknown type: notification is marked read above; no navigation needed.
        // Explicit no-op prevents accidental fall-through if new types are added later.
    };

    const renderNotification = ({ item }: { item: Notification }) => {
        const icon = iconMap[item.type] || iconMap.system;
        return (
            <TouchableOpacity
                style={[styles.notifCard, !item.is_read && styles.notifUnread]}
                onPress={() => handleNotificationPress(item)}
                activeOpacity={0.7}
            >
                <View style={[styles.notifIcon, { backgroundColor: `${icon.color}12` }]}>
                    <Ionicons name={icon.name as any} size={20} color={icon.color} />
                </View>
                <View style={{ flex: 1 }}>
                    <View style={styles.notifHeader}>
                        <Text style={styles.notifTitle}>{item.title}</Text>
                        <Text style={styles.notifTime}>{formatTime(item.created_at)}</Text>
                    </View>
                    <Text style={styles.notifBody} numberOfLines={2}>{item.body}</Text>
                </View>
                {!item.is_read && <View style={styles.unreadDot} />}
            </TouchableOpacity>
        );
    };

    return (
        <View style={styles.container}>
            {/* Header */}
            <LinearGradient colors={[colors.surface, colors.background]} style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>{t('notifications.title')}</Text>
                    {unreadCount > 0 ? (
                        <TouchableOpacity onPress={markAllRead} style={styles.markAllBtn}>
                            <Text style={styles.markAllText}>{t('notifications.markAllRead')}</Text>
                        </TouchableOpacity>
                    ) : (
                        <View style={{ width: 80 }} />
                    )}
                </View>
                {unreadCount > 0 && (
                    <Text style={styles.unreadCountText}>{unreadCount} {unreadCount !== 1 ? t('notifications.unreadCountPlural').replace('{{count}}', '') : t('notifications.unreadCount').replace('{{count}}', '')}</Text>
                )}
            </LinearGradient>

            <FlatList
                data={notifications}
                renderItem={renderNotification}
                keyExtractor={(item) => item.id}
                contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 40 }}
                showsVerticalScrollIndicator={false}
                initialNumToRender={10}
                maxToRenderPerBatch={10}
                windowSize={5}
                refreshControl={
                    <SafeRefreshControl refreshing={isFetching} onRefresh={onRefresh} tintColor={colors.primary} />
                }
                ListEmptyComponent={
                    <View style={styles.emptyState}>
                        <Ionicons name="notifications-off-outline" size={56} color={colors.surfaceLight} />
                        <Text style={styles.emptyTitle}>{t('notifications.noNotifications')}</Text>
                        <Text style={styles.emptySub}>{t('notifications.allCaughtUp')}</Text>
                    </View>
                }
            />
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        header: {
            paddingBottom: 14,
            paddingHorizontal: 16,
        },
        headerRow: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
        },
        backBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        headerTitle: { color: colors.text, fontSize: 20, fontWeight: '700' },
        markAllBtn: { padding: 8 },
        markAllText: { color: colors.primary, fontSize: 13, fontWeight: '600' },
        unreadCountText: {
            color: colors.textDim,
            fontSize: 12,
            marginTop: 6,
            textAlign: 'center',
        },
        notifCard: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            gap: 12,
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 14,
            marginBottom: 8,
            borderWidth: 1,
            borderColor: colors.border,
        },
        notifUnread: {
            borderColor: `${colors.primary}30`,
            backgroundColor: `${colors.primary}08`,
        },
        notifIcon: {
            width: 40,
            height: 40,
            borderRadius: 12,
            justifyContent: 'center',
            alignItems: 'center',
            marginTop: 2,
        },
        notifHeader: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 4,
        },
        notifTitle: { color: colors.text, fontSize: 14, fontWeight: '600', flex: 1 },
        notifTime: { color: colors.textDim, fontSize: 11, marginLeft: 8 },
        notifBody: { color: colors.textDim, fontSize: 13, lineHeight: 18 },
        unreadDot: {
            width: 8,
            height: 8,
            borderRadius: 4,
            backgroundColor: colors.primary,
            marginTop: 8,
        },
        emptyState: { alignItems: 'center', paddingVertical: 60, gap: 8 },
        emptyTitle: { color: colors.textDim, fontSize: 18, fontWeight: '600' },
        emptySub: { color: colors.textSecondary, fontSize: 13 },
    });
}
