import React, { useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    FlatList,
    Platform,
    RefreshControl,
} from 'react-native';
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

    useEffect(() => {
        fetchPayoutHistory();
    }, []);

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
                    <View style={styles.backBtn} />
                    <Text style={styles.headerTitle}>Payout History</Text>
                    <View style={styles.backBtn} />
                </View>
            </LinearGradient>

            <FlatList
                data={payoutHistory}
                renderItem={renderPayoutItem}
                keyExtractor={(item) => item.id}
                contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: insets.bottom + 40 }}
                showsVerticalScrollIndicator={false}
                ListEmptyComponent={renderEmpty}
                refreshControl={
                    <RefreshControl
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
        header: {
            paddingBottom: 12,
            paddingHorizontal: 16,
        },
        headerRow: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
        },
        backBtn: { width: 40 },
        headerTitle: { color: colors.text, fontSize: 20, fontWeight: '700' },

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
