import React, { useCallback, useMemo } from 'react';
import {
    View,
    StyleSheet,
    TouchableOpacity,
    FlatList,
    Alert,
    ActivityIndicator,
} from 'react-native';
import { Text } from '@shared/components/Text';
import SafeRefreshControl from '../../components/SafeRefreshControl';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useFocusEffect } from 'expo-router/react-navigation';
import {
    useNotifications,
    useMarkNotificationRead,
    useMarkAllNotificationsRead,
} from '@shared/hooks/queries';
import { useLanguageStore } from '../../store/languageStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SPACING, FONT } from '@shared/utils/responsive';

interface Notification {
    id: string;
    title: string;
    body: string;
    type: string;
    data?: Record<string, string>;
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
    const { data: rawNotifData, isFetching, isPending, isError, refetch } = useNotifications(50);

    // Belt-and-suspenders alongside the AppState/focusManager wiring in
    // shared/api/queryClient.ts: that covers app-level foreground, this
    // covers in-app screen focus (e.g. tapping the bell without ever
    // backgrounding the app) — same pattern as lost-and-found.tsx.
    useFocusEffect(
        useCallback(() => {
            refetch();
        }, [refetch]),
    );

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
        lost_and_found: { name: 'bag-handle', color: colors.orange },
        lost_and_found_message: { name: 'bag-handle', color: colors.orange },
        chat_message: { name: 'chatbubble', color: colors.primary },
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
        const caseId = item.data?.case_id;
        if (item.type === 'document_expiry') router.push('/driver/documents' as any);
        else if (item.type === 'payout_processed') router.push('/driver/activity' as any);
        else if (item.type === 'ride_offer') router.push('/driver/' as any);
        else if (item.type === 'quest_earned') router.push('/driver/quests' as any);
        else if (item.type === 'lost_and_found' || item.type === 'lost_and_found_message') {
            if (caseId) router.push({ pathname: '/driver/lost-and-found-chat', params: { caseId } } as any);
            else router.push('/driver/lost-and-found' as any);
        }
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
        // FlatList IS the screen's root, with the header as its
        // ListHeaderComponent (stickied via stickyHeaderIndices), rather than
        // a separate View+FlatList sibling pair. The previous sibling layout
        // (a fixed-height View above a `flex: 1` FlatList) reproduced, live,
        // on at least one real Android device: the header rendered but the
        // FlatList area stayed at zero height — pull-to-refresh didn't even
        // register a touch — despite `style={{ flex: 1 }}` and
        // `contentContainerStyle={{ flexGrow: 1 }}` already being set (see
        // this file's own prior "FlatList zero-height on Android" fix, which
        // was never confirmed against a real device and evidently didn't
        // hold universally). Every other FlatList screen in this app that
        // renders correctly on that same device (Activity, and others) uses
        // this ListHeaderComponent shape instead of a sibling header — this
        // change adopts that proven-working pattern rather than patching the
        // sibling layout further.
        <FlatList
            style={styles.container}
            data={notifications}
            renderItem={renderNotification}
            keyExtractor={(item) => item.id}
            ListHeaderComponent={
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
            }
            // Keeps the back button / "Mark All Read" reachable while
            // scrolling through a long inbox, instead of it scrolling away
            // with the rest of the ListHeaderComponent content.
            stickyHeaderIndices={[0]}
            contentContainerStyle={{ flexGrow: 1, paddingBottom: insets.bottom + 40 }}
            showsVerticalScrollIndicator={false}
            initialNumToRender={10}
            maxToRenderPerBatch={10}
            windowSize={5}
            refreshControl={
                <SafeRefreshControl refreshing={isFetching} onRefresh={onRefresh} tintColor={colors.primary} />
            }
            ListEmptyComponent={
                // An empty list is NOT automatically "all caught up" — a failed
                // fetch also yields zero rows. Rendering the same cheerful empty
                // state for both is what made a 401'd inbox look like an inbox
                // with nothing in it, while the bell badge still read "6 unread".
                isPending ? (
                    <View style={styles.emptyState}>
                        <ActivityIndicator size="large" color={colors.primary} />
                    </View>
                ) : isError ? (
                    <View style={styles.emptyState}>
                        <Ionicons name="cloud-offline-outline" size={56} color={colors.danger} />
                        <Text style={styles.emptyTitle}>{t('notifications.loadFailed')}</Text>
                        <Text style={styles.emptySub}>{t('notifications.loadFailedBody')}</Text>
                        <TouchableOpacity style={styles.retryBtn} onPress={onRefresh}>
                            <Text style={styles.retryText}>{t('notifications.retry')}</Text>
                        </TouchableOpacity>
                    </View>
                ) : (
                    <View style={styles.emptyState}>
                        <Ionicons name="notifications-off-outline" size={56} color={colors.surfaceLight} />
                        <Text style={styles.emptyTitle}>{t('notifications.noNotifications')}</Text>
                        <Text style={styles.emptySub}>{t('notifications.allCaughtUp')}</Text>
                    </View>
                )
            }
        />
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        header: {
            paddingBottom: 14,
            paddingHorizontal: SPACING.md,
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
        markAllBtn: { padding: SPACING.sm },
        markAllText: { color: colors.primary, fontSize: FONT.bodySm, fontWeight: '600' },
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
            // Was horizontal inset from the FlatList's own contentContainerStyle
            // before the header moved into ListHeaderComponent; kept here now
            // that contentContainerStyle no longer applies it (it would also
            // wrap the header, double-padding it against styles.header's own
            // paddingHorizontal).
            marginHorizontal: 16,
            marginBottom: SPACING.sm,
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
            marginBottom: SPACING.xs,
        },
        notifTitle: { color: colors.text, fontSize: 14, fontWeight: '600', flex: 1 },
        notifTime: { color: colors.textDim, fontSize: FONT.label, marginLeft: SPACING.sm },
        notifBody: { color: colors.textDim, fontSize: FONT.bodySm, lineHeight: 18 },
        unreadDot: {
            width: 8,
            height: 8,
            borderRadius: 4,
            backgroundColor: colors.primary,
            marginTop: SPACING.sm,
        },
        // paddingHorizontal was previously inherited from the FlatList's
        // contentContainerStyle; that no longer wraps this (see notifCard's
        // own comment), so it's applied directly here to keep the same
        // side-inset on the error/empty-state text and retry button.
        emptyState: { alignItems: 'center', paddingVertical: 60, paddingHorizontal: 16, gap: 8 },
        emptyTitle: { color: colors.textDim, fontSize: 18, fontWeight: '600' },
        emptySub: { color: colors.textSecondary, fontSize: FONT.bodySm, textAlign: 'center' },
        retryBtn: {
            marginTop: SPACING.sm,
            paddingHorizontal: 20,
            paddingVertical: 10,
            borderRadius: 20,
            backgroundColor: colors.primary,
        },
        retryText: { color: '#fff', fontSize: 14, fontWeight: '600' },
    });
}
