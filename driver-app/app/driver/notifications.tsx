import React, { useEffect, useState, useCallback } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    FlatList,
    Platform,
    RefreshControl,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';

import SpinrConfig from '@shared/config/spinr.config';

const THEME = SpinrConfig.theme.colors;
const COLORS = {
    primary: THEME.background, // Background (white)
    accent: THEME.primary, // Action/brand color (red)
    accentDim: THEME.primaryDark,
    surface: THEME.surface,
    surfaceLight: THEME.surfaceLight,
    text: THEME.text,
    textDim: THEME.textDim,
    success: THEME.success,
    gold: '#FFD700',
    orange: '#FF9500',
    danger: THEME.error,
    border: THEME.border,
};

interface Notification {
    id: string;
    title: string;
    body: string;
    type: 'ride' | 'earnings' | 'promo' | 'system' | 'safety';
    read: boolean;
    created_at: string;
}

// Sample notifications for display (in production, fetch from API)
const sampleNotifications: Notification[] = [
    {
        id: '1',
        title: 'New Ride Request',
        body: 'You have a new ride request from John near 8th Ave.',
        type: 'ride',
        read: false,
        created_at: new Date().toISOString(),
    },
    {
        id: '2',
        title: 'Daily Earnings Summary',
        body: 'You earned $142.50 today across 8 trips. Great work!',
        type: 'earnings',
        read: false,
        created_at: new Date(Date.now() - 3600000).toISOString(),
    },
    {
        id: '3',
        title: 'Weekend Bonus',
        body: 'Complete 15 rides this weekend to earn a $50 bonus!',
        type: 'promo',
        read: true,
        created_at: new Date(Date.now() - 86400000).toISOString(),
    },
    {
        id: '4',
        title: 'Safety Reminder',
        body: "Please ensure your vehicle inspection is up to date. It expires in 7 days.",
        type: 'safety',
        read: true,
        created_at: new Date(Date.now() - 172800000).toISOString(),
    },
    {
        id: '5',
        title: 'System Update',
        body: 'A new version of the Spinr Driver app is available. Update now for the latest features.',
        type: 'system',
        read: true,
        created_at: new Date(Date.now() - 259200000).toISOString(),
    },
];

const iconMap: Record<string, { name: string; color: string }> = {
    ride: { name: 'car', color: COLORS.accent },
    earnings: { name: 'wallet', color: COLORS.gold },
    promo: { name: 'gift', color: COLORS.orange },
    system: { name: 'settings', color: COLORS.textDim },
    safety: { name: 'shield-checkmark', color: COLORS.danger },
};

export default function NotificationsScreen() {
    const router = useRouter();
    const [notifications, setNotifications] = useState<Notification[]>(sampleNotifications);
    const [refreshing, setRefreshing] = useState(false);

    const markAsRead = (id: string) => {
        setNotifications((prev) =>
            prev.map((n) => (n.id === id ? { ...n, read: true } : n))
        );
    };

    const markAllRead = () => {
        setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    };

    const onRefresh = () => {
        setRefreshing(true);
        // In production, fetch from API
        setTimeout(() => setRefreshing(false), 800);
    };

    const unreadCount = notifications.filter((n) => !n.read).length;

    const formatTime = (dateStr: string) => {
        const diff = Date.now() - new Date(dateStr).getTime();
        const minutes = Math.floor(diff / 60000);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        return `${days}d ago`;
    };

    const renderNotification = ({ item }: { item: Notification }) => {
        const icon = iconMap[item.type] || iconMap.system;
        return (
            <TouchableOpacity
                style={[styles.notifCard, !item.read && styles.notifUnread]}
                onPress={() => markAsRead(item.id)}
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
                {!item.read && <View style={styles.unreadDot} />}
            </TouchableOpacity>
        );
    };

    return (
        <View style={styles.container}>
            {/* Header */}
            <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.header}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={COLORS.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Notifications</Text>
                    {unreadCount > 0 ? (
                        <TouchableOpacity onPress={markAllRead} style={styles.markAllBtn}>
                            <Text style={styles.markAllText}>Mark All Read</Text>
                        </TouchableOpacity>
                    ) : (
                        <View style={{ width: 80 }} />
                    )}
                </View>
                {unreadCount > 0 && (
                    <Text style={styles.unreadCountText}>{unreadCount} unread notification{unreadCount !== 1 ? 's' : ''}</Text>
                )}
            </LinearGradient>

            <FlatList
                data={notifications}
                renderItem={renderNotification}
                keyExtractor={(item) => item.id}
                contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 100 }}
                showsVerticalScrollIndicator={false}
                refreshControl={
                    <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.accent} />
                }
                ListEmptyComponent={
                    <View style={styles.emptyState}>
                        <Ionicons name="notifications-off-outline" size={56} color={COLORS.surfaceLight} />
                        <Text style={styles.emptyTitle}>No notifications</Text>
                        <Text style={styles.emptySub}>You're all caught up!</Text>
                    </View>
                }
            />
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: COLORS.primary },
    header: {
        paddingTop: Platform.OS === 'ios' ? 55 : 35,
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
        backgroundColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    headerTitle: { color: COLORS.text, fontSize: 20, fontWeight: '700' },
    markAllBtn: { padding: 8 },
    markAllText: { color: COLORS.accent, fontSize: 13, fontWeight: '600' },
    unreadCountText: {
        color: COLORS.textDim,
        fontSize: 12,
        marginTop: 6,
        textAlign: 'center',
    },
    notifCard: {
        flexDirection: 'row',
        alignItems: 'flex-start',
        gap: 12,
        backgroundColor: COLORS.surface,
        borderRadius: 16,
        padding: 14,
        marginBottom: 8,
        borderWidth: 1,
        borderColor: 'rgba(255,255,255,0.04)',
    },
    notifUnread: {
        borderColor: 'rgba(0,212,170,0.15)',
        backgroundColor: 'rgba(0,212,170,0.04)',
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
    notifTitle: { color: COLORS.text, fontSize: 14, fontWeight: '600', flex: 1 },
    notifTime: { color: COLORS.textDim, fontSize: 11, marginLeft: 8 },
    notifBody: { color: COLORS.textDim, fontSize: 13, lineHeight: 18 },
    unreadDot: {
        width: 8,
        height: 8,
        borderRadius: 4,
        backgroundColor: COLORS.accent,
        marginTop: 8,
    },
    emptyState: { alignItems: 'center', paddingVertical: 60, gap: 8 },
    emptyTitle: { color: COLORS.textDim, fontSize: 18, fontWeight: '600' },
    emptySub: { color: COLORS.surfaceLight, fontSize: 13 },
});
