import React, { useEffect } from 'react';
import {
    View,
    Text,
    StyleSheet,
    FlatList,
    Platform,
    RefreshControl,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';
import SpinrConfig from '@shared/config/spinr.config';

const THEME = SpinrConfig.theme.colors;
const COLORS = {
    primary: THEME.background,
    accent: THEME.primary,
    surface: THEME.surface,
    surfaceLight: THEME.surfaceLight,
    text: THEME.text,
    textDim: THEME.textDim,
    success: THEME.success,
    warning: THEME.warning,
    danger: THEME.error,
};

export default function PayoutHistoryScreen() {
    const router = useRouter();
    const { payoutHistory, fetchPayoutHistory, isLoading } = useDriverStore();

    useEffect(() => {
        fetchPayoutHistory();
    }, []);

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'completed':
                return COLORS.success;
            case 'pending':
            case 'processing':
                return COLORS.warning;
            case 'failed':
                return COLORS.danger;
            default:
                return COLORS.textDim;
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
            <Ionicons name="wallet-outline" size={64} color={COLORS.surfaceLight} />
            <Text style={styles.emptyTitle}>No payouts yet</Text>
            <Text style={styles.emptySub}>
                Your payout history will appear here
            </Text>
        </View>
    );

    return (
        <View style={styles.container}>
            <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.header}>
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
                contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 100 }}
                showsVerticalScrollIndicator={false}
                ListEmptyComponent={renderEmpty}
                refreshControl={
                    <RefreshControl
                        refreshing={isLoading}
                        onRefresh={() => fetchPayoutHistory()}
                        tintColor={COLORS.accent}
                    />
                }
            />
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: COLORS.primary },
    header: {
        paddingTop: Platform.OS === 'ios' ? 55 : 35,
        paddingBottom: 12,
        paddingHorizontal: 16,
    },
    headerRow: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
    },
    backBtn: { width: 40 },
    headerTitle: { color: COLORS.text, fontSize: 20, fontWeight: '700' },

    payoutCard: {
        backgroundColor: COLORS.surface,
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
        color: COLORS.text,
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
        borderTopColor: COLORS.surfaceLight,
    },
    detailRow: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        marginBottom: 8,
    },
    detailLabel: {
        color: COLORS.textDim,
        fontSize: 13,
    },
    detailValue: {
        color: COLORS.text,
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
        color: COLORS.danger,
        fontSize: 12,
    },

    emptyState: {
        alignItems: 'center',
        paddingVertical: 80,
    },
    emptyTitle: {
        color: COLORS.text,
        fontSize: 18,
        fontWeight: '600',
        marginTop: 16,
    },
    emptySub: {
        color: COLORS.textDim,
        fontSize: 14,
        marginTop: 4,
    },
});
