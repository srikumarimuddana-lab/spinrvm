import React, { useEffect, useMemo, useState } from 'react';
import {
    View,
    Text,
    StyleSheet,
    FlatList,
    TouchableOpacity,
} from 'react-native';
import SafeRefreshControl from '../../components/SafeRefreshControl';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function PayoutHistoryScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const { payoutHistory, fetchPayoutHistory, isLoading } = useDriverStore();
    const [statusFilter, setStatusFilter] = useState<string>('all');

    const STATUS_FILTERS = ['all', 'completed', 'pending', 'processing', 'failed'] as const;

    useEffect(() => {
        // fetchPayoutHistory is a stable driverStore action; adding it
        // doesn't change this mount-only effect's firing.
        fetchPayoutHistory();
    }, [fetchPayoutHistory]);

    const filteredHistory = useMemo(() => {
        const sorted = [...(payoutHistory || [])].sort((a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        );
        if (statusFilter === 'all') return sorted;
        return sorted.filter(p => p.status === statusFilter);
    }, [payoutHistory, statusFilter]);

    // Previous-app transfers get their own section below the Spinr list so
    // the two eras never interleave. The server stops sending these rows
    // after the transition cutoff (Aug 31, 2026 — see backend
    // utils/legacy_rides), so this section retires itself with no app
    // release needed.
    const spinrHistory = useMemo(
        () => filteredHistory.filter((p: any) => p.payout_type !== 'stripe_sync'),
        [filteredHistory],
    );
    const previousAppHistory = useMemo(
        () => filteredHistory.filter((p: any) => p.payout_type === 'stripe_sync'),
        [filteredHistory],
    );

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'completed':
                return colors.success;
            case 'pending':
            case 'processing':
                return colors.warning;
            case 'failed':
                return colors.danger;
            default:
                return colors.textDim;
        }
    };

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'completed':
                return 'checkmark-circle';
            case 'pending':
            case 'processing':
                return 'time';
            case 'failed':
                return 'close-circle';
            default:
                return 'help-circle';
        }
    };

    const formatDate = (dateStr: string | null) => {
        if (!dateStr) return '--';
        const date = new Date(dateStr);
        return date.toLocaleDateString('en', {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
        });
    };

    const renderPayoutItem = ({ item }: { item: any }) => (
        <View style={styles.payoutCard}>
            <View style={styles.payoutHeader}>
                <View style={[styles.statusIcon, { backgroundColor: `${getStatusColor(item.status)}20` }]}>
                    <Ionicons
                        name={getStatusIcon(item.status) as any}
                        size={20}
                        color={getStatusColor(item.status)}
                    />
                </View>
                <View style={styles.payoutInfo}>
                    <Text style={styles.payoutAmount}>${item.amount.toFixed(2)}</Text>
                    <Text style={[styles.payoutStatus, { color: getStatusColor(item.status) }]}>
                        {item.status.charAt(0).toUpperCase() + item.status.slice(1)}
                    </Text>
                </View>
            </View>

            <View style={styles.payoutDetails}>
                <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>Date</Text>
                    <Text style={styles.detailValue}>{formatDate(item.created_at)}</Text>
                </View>
                {item.bank_name && (
                    <View style={styles.detailRow}>
                        <Text style={styles.detailLabel}>Bank</Text>
                        <Text style={styles.detailValue}>{item.bank_name}</Text>
                    </View>
                )}
                {item.account_last4 && (
                    <View style={styles.detailRow}>
                        <Text style={styles.detailLabel}>Account</Text>
                        <Text style={styles.detailValue}>•••• {item.account_last4}</Text>
                    </View>
                )}
                {item.processed_at && (
                    <View style={styles.detailRow}>
                        <Text style={styles.detailLabel}>Processed</Text>
                        <Text style={styles.detailValue}>{formatDate(item.processed_at)}</Text>
                    </View>
                )}
                {item.error_message && (
                    <View style={styles.errorRow}>
                        <Text style={styles.errorText}>{item.error_message}</Text>
                    </View>
                )}
            </View>
        </View>
    );

    const renderEmpty = () => (
        <View style={styles.emptyState}>
            <Ionicons name="wallet-outline" size={64} color={colors.surfaceLight} />
            <Text style={styles.emptyTitle}>No payouts yet</Text>
            <Text style={styles.emptySub}>
                Your payout history will appear here
            </Text>
        </View>
    );

    return (
        <View style={styles.container}>
            <LinearGradient colors={[colors.surface, colors.background]} style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Payout History</Text>
                    <TouchableOpacity onPress={() => router.push('/driver/tax-documents' as any)} style={styles.taxDocsBtn}>
                        <Ionicons name="document-text-outline" size={22} color={colors.primary} />
                    </TouchableOpacity>
                </View>
            </LinearGradient>

            {/* Status Filter — pills wrap onto multiple rows so every option
                (incl. "Failed") is always fully visible and tappable, with no
                reliance on a horizontal scroll gesture. */}
            <View style={styles.filterRow}>
                {STATUS_FILTERS.map(s => (
                    <TouchableOpacity
                        key={s}
                        style={[styles.filterPill, statusFilter === s && styles.filterPillActive]}
                        onPress={() => setStatusFilter(s)}
                    >
                        <Text style={[styles.filterPillText, statusFilter === s && styles.filterPillTextActive]}>
                            {s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
                        </Text>
                    </TouchableOpacity>
                ))}
            </View>

            <FlatList
                data={spinrHistory}
                renderItem={renderPayoutItem}
                keyExtractor={(item) => item.id}
                contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 40 }}
                showsVerticalScrollIndicator={false}
                initialNumToRender={10}
                maxToRenderPerBatch={10}
                windowSize={5}
                removeClippedSubviews={true}
                ListEmptyComponent={previousAppHistory.length === 0 ? renderEmpty : null}
                ListFooterComponent={
                    previousAppHistory.length > 0 ? (
                        <View>
                            <View style={styles.previousAppHeader}>
                                <Ionicons name="time-outline" size={16} color={colors.textDim} />
                                <View style={{ marginLeft: 8, flex: 1 }}>
                                    <Text style={styles.previousAppTitle}>Previous app</Text>
                                    <Text style={styles.previousAppSub}>
                                        Payments made by the previous Spinr app — shown for your records
                                        until Aug 31, 2026. Not part of your Spinr earnings.
                                    </Text>
                                </View>
                            </View>
                            {previousAppHistory.map((item: any) => (
                                <React.Fragment key={item.id}>{renderPayoutItem({ item })}</React.Fragment>
                            ))}
                        </View>
                    ) : null
                }
                refreshControl={
                    <SafeRefreshControl
                        refreshing={isLoading}
                        onRefresh={() => fetchPayoutHistory()}
                        tintColor={colors.primary}
                    />
                }
            />
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        previousAppHeader: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            marginTop: 20,
            marginBottom: 10,
            paddingTop: 14,
            borderTopWidth: 1,
            borderTopColor: colors.border,
        },
        previousAppTitle: {
            fontSize: 13,
            fontWeight: '700',
            color: colors.text,
        },
        previousAppSub: {
            marginTop: 2,
            fontSize: 11,
            lineHeight: 15,
            color: colors.textDim,
        },
        header: {
            paddingBottom: 12,
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
        taxDocsBtn: {
            width: 40,
            height: 40,
            justifyContent: 'center',
            alignItems: 'center',
        },
        headerTitle: { color: colors.text, fontSize: 20, fontWeight: '700' },

        filterRow: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            paddingHorizontal: 16,
            gap: 8,
            marginBottom: 8,
        },
        filterPill: {
            paddingHorizontal: 16,
            paddingVertical: 8,
            borderRadius: 20,
            backgroundColor: colors.surfaceLight,
            borderWidth: 1,
            borderColor: colors.border,
        },
        filterPillActive: {
            backgroundColor: colors.primary,
            borderColor: colors.primary,
        },
        filterPillText: {
            fontSize: 13,
            fontWeight: '600',
            color: colors.textDim,
        },
        filterPillTextActive: {
            color: '#fff',
        },

        payoutCard: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 16,
            marginBottom: 12,
        },
        payoutHeader: {
            flexDirection: 'row',
            alignItems: 'center',
            marginBottom: 12,
        },
        statusIcon: {
            width: 40,
            height: 40,
            borderRadius: 20,
            justifyContent: 'center',
            alignItems: 'center',
            marginRight: 12,
        },
        payoutInfo: { flex: 1 },
        payoutAmount: {
            color: colors.text,
            fontSize: 20,
            fontWeight: '700',
        },
        payoutStatus: {
            fontSize: 13,
            fontWeight: '600',
            marginTop: 2,
        },
        payoutDetails: {
            paddingTop: 12,
            borderTopWidth: 1,
            borderTopColor: colors.surfaceLight,
        },
        detailRow: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            marginBottom: 8,
        },
        detailLabel: {
            color: colors.textDim,
            fontSize: 13,
        },
        detailValue: {
            color: colors.text,
            fontSize: 13,
            fontWeight: '500',
        },
        errorRow: {
            marginTop: 8,
            padding: 10,
            backgroundColor: 'rgba(255,71,87,0.1)',
            borderRadius: 8,
        },
        errorText: {
            color: colors.danger,
            fontSize: 12,
        },

        emptyState: {
            alignItems: 'center',
            paddingVertical: 80,
        },
        emptyTitle: {
            color: colors.text,
            fontSize: 18,
            fontWeight: '600',
            marginTop: 16,
        },
        emptySub: {
            color: colors.textDim,
            fontSize: 14,
            marginTop: 4,
        },
    });
}
