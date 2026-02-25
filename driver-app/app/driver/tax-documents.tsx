import React, { useState, useEffect } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    Platform,
    ActivityIndicator,
    Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import api from '@shared/api/client';
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
    gold: '#FFD700',
};

interface T4AYear {
    year: number;
    grossEarnings: number;
    tips: number;
    fees: number;
    netIncome: number;
    totalRides: number;
    distance: number;
}

export default function TaxDocumentsScreen() {
    const router = useRouter();
    const [years, setYears] = useState<number[]>([]);
    const [selectedYear, setSelectedYear] = useState<number | null>(null);
    const [t4aData, setT4aData] = useState<T4AYear | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetchAvailableYears();
    }, []);

    const fetchAvailableYears = async () => {
        try {
            const res = await api.get('/drivers/t4a');
            setYears(res.data.available_years || []);
            if (res.data.available_years?.length > 0) {
                setSelectedYear(res.data.available_years[0]);
                fetchT4AData(res.data.available_years[0]);
            }
        } catch (err) {
            console.log('Error fetching years:', err);
            // Try to get current year at least
            setYears([new Date().getFullYear()]);
            setSelectedYear(new Date().getFullYear());
        } finally {
            setLoading(false);
        }
    };

    const fetchT4AData = async (year: number) => {
        setLoading(true);
        try {
            const res = await api.get(`/drivers/t4a/${year}`);
            const data = res.data;
            setT4aData({
                year: data.tax_year,
                grossEarnings: data.summary.gross_earnings,
                tips: data.summary.total_tips,
                fees: data.summary.platform_fees,
                netIncome: data.summary.net_income,
                totalRides: data.summary.total_rides,
                distance: data.summary.total_distance_km,
            });
        } catch (err) {
            console.log('Error fetching T4A:', err);
            Alert.alert('Error', 'Failed to load tax data');
        } finally {
            setLoading(false);
        }
    };

    const handleYearSelect = (year: number) => {
        setSelectedYear(year);
        fetchT4AData(year);
    };

    const formatCurrency = (amount: number) => `$${amount.toFixed(2)}`;

    return (
        <View style={styles.container}>
            {/* Header */}
            <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.header}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={COLORS.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Tax Documents</Text>
                    <View style={{ width: 40 }} />
                </View>
            </LinearGradient>

            <ScrollView contentContainerStyle={{ paddingBottom: 100 }} showsVerticalScrollIndicator={false}>
                {/* T4A Info Card */}
                <View style={styles.infoCard}>
                    <View style={styles.infoIcon}>
                        <Ionicons name="document-text" size={24} color={COLORS.accent} />
                    </View>
                    <View style={styles.infoContent}>
                        <Text style={styles.infoTitle}>T4A Summary</Text>
                        <Text style={styles.infoText}>
                            Annual summary of your earnings for Canadian tax purposes.
                            Drivers are considered self-employed.
                        </Text>
                    </View>
                </View>

                {/* Year Selector */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Tax Year</Text>
                    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                        <View style={styles.yearRow}>
                            {years.map((year) => (
                                <TouchableOpacity
                                    key={year}
                                    style={[
                                        styles.yearChip,
                                        selectedYear === year && styles.yearChipActive,
                                    ]}
                                    onPress={() => handleYearSelect(year)}
                                >
                                    <Text
                                        style={[
                                            styles.yearChipText,
                                            selectedYear === year && styles.yearChipTextActive,
                                        ]}
                                    >
                                        {year}
                                    </Text>
                                </TouchableOpacity>
                            ))}
                        </View>
                    </ScrollView>
                </View>

                {/* T4A Summary */}
                {loading ? (
                    <ActivityIndicator size="large" color={COLORS.accent} style={{ marginTop: 40 }} />
                ) : t4aData ? (
                    <View style={styles.section}>
                        <View style={styles.t4aCard}>
                            <View style={styles.t4aHeader}>
                                <Text style={styles.t4aTitle}>T4A - {t4aData.year}</Text>
                                <View style={styles.t4aBadge}>
                                    <Ionicons name="checkmark-circle" size={14} color={COLORS.success} />
                                    <Text style={styles.t4aBadgeText}>Available</Text>
                                </View>
                            </View>

                            {/* Income Summary */}
                            <View style={styles.incomeSection}>
                                <Text style={styles.incomeLabel}>Box 024 - Commissions</Text>
                                <Text style={styles.incomeAmount}>
                                    {formatCurrency(t4aData.grossEarnings)}
                                </Text>
                            </View>
                            <View style={styles.incomeSection}>
                                <Text style={styles.incomeLabel}>Box 194 - Fee for Service</Text>
                                <Text style={styles.incomeAmount}>
                                    {formatCurrency(t4aData.grossEarnings)}
                                </Text>
                            </View>

                            <View style={styles.divider} />

                            {/* Breakdown */}
                            <Text style={styles.breakdownTitle}>Earnings Breakdown</Text>

                            <View style={styles.breakdownRow}>
                                <Text style={styles.breakdownLabel}>Total Rides</Text>
                                <Text style={styles.breakdownValue}>{t4aData.totalRides}</Text>
                            </View>
                            <View style={styles.breakdownRow}>
                                <Text style={styles.breakdownLabel}>Total Distance</Text>
                                <Text style={styles.breakdownValue}>{t4aData.distance.toFixed(1)} km</Text>
                            </View>
                            <View style={styles.breakdownRow}>
                                <Text style={styles.breakdownLabel}>Gross Earnings</Text>
                                <Text style={styles.breakdownValue}>{formatCurrency(t4aData.grossEarnings)}</Text>
                            </View>
                            <View style={styles.breakdownRow}>
                                <Text style={styles.breakdownLabel}>Total Tips</Text>
                                <Text style={[styles.breakdownValue, { color: COLORS.gold }]}>
                                    {formatCurrency(t4aData.tips)}
                                </Text>
                            </View>
                            <View style={styles.breakdownRow}>
                                <Text style={styles.breakdownLabel}>Platform Fees</Text>
                                <Text style={[styles.breakdownValue, { color: COLORS.danger }]}>
                                    -{formatCurrency(t4aData.fees)}
                                </Text>
                            </View>

                            <View style={styles.divider} />

                            <View style={styles.breakdownRow}>
                                <Text style={[styles.breakdownLabel, { fontWeight: '700' }]}>Net Income</Text>
                                <Text style={[styles.breakdownValue, { fontWeight: '700', fontSize: 18 }]}>
                                    {formatCurrency(t4aData.netIncome)}
                                </Text>
                            </View>
                        </View>

                        {/* Important Notice */}
                        <View style={styles.noticeCard}>
                            <Ionicons name="information-circle" size={20} color={COLORS.warning} />
                            <Text style={styles.noticeText}>
                                This is for informational purposes only. Please consult a Canadian tax
                                professional for accurate tax filing. As a self-employed driver, you are
                                responsible for reporting income and may be able to deduct business expenses.
                            </Text>
                        </View>

                        {/* Export Button */}
                        <TouchableOpacity
                            style={styles.exportButton}
                            onPress={async () => {
                                try {
                                    const res = await api.get(`/drivers/earnings/export?year=${selectedYear}`);
                                    Alert.alert(
                                        'Export Ready',
                                        `Your ${selectedYear} earnings data is ready. Data includes ride details, tips, and payouts.`
                                    );
                                } catch (err) {
                                    Alert.alert('Error', 'Failed to export data');
                                }
                            }}
                        >
                            <Ionicons name="download-outline" size={20} color={COLORS.accent} />
                            <Text style={styles.exportButtonText}>Export for Tax Filing</Text>
                        </TouchableOpacity>
                    </View>
                ) : (
                    <View style={styles.emptyState}>
                        <Ionicons name="document-text-outline" size={64} color={COLORS.surfaceLight} />
                        <Text style={styles.emptyTitle}>No Data Available</Text>
                        <Text style={styles.emptySub}>
                            Select a different year or start driving to generate tax documents
                        </Text>
                    </View>
                )}
            </ScrollView>
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
    backBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    headerTitle: { color: COLORS.text, fontSize: 20, fontWeight: '700' },

    infoCard: {
        flexDirection: 'row',
        backgroundColor: COLORS.surface,
        marginHorizontal: 16,
        marginTop: 16,
        borderRadius: 16,
        padding: 16,
    },
    infoIcon: {
        width: 48,
        height: 48,
        borderRadius: 12,
        backgroundColor: 'rgba(0,212,170,0.1)',
        justifyContent: 'center',
        alignItems: 'center',
        marginRight: 14,
    },
    infoContent: { flex: 1 },
    infoTitle: {
        color: COLORS.text,
        fontSize: 16,
        fontWeight: '700',
        marginBottom: 4,
    },
    infoText: {
        color: COLORS.textDim,
        fontSize: 13,
        lineHeight: 18,
    },

    section: {
        paddingHorizontal: 16,
        marginTop: 24,
    },
    sectionTitle: {
        color: COLORS.text,
        fontSize: 17,
        fontWeight: '700',
        marginBottom: 12,
    },
    yearRow: {
        flexDirection: 'row',
        gap: 10,
    },
    yearChip: {
        paddingHorizontal: 20,
        paddingVertical: 10,
        borderRadius: 20,
        backgroundColor: COLORS.surface,
        borderWidth: 1,
        borderColor: COLORS.surfaceLight,
    },
    yearChipActive: {
        backgroundColor: COLORS.accent,
        borderColor: COLORS.accent,
    },
    yearChipText: {
        color: COLORS.text,
        fontSize: 14,
        fontWeight: '600',
    },
    yearChipTextActive: {
        color: '#fff',
    },

    t4aCard: {
        backgroundColor: COLORS.surface,
        borderRadius: 16,
        padding: 20,
    },
    t4aHeader: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: 20,
    },
    t4aTitle: {
        color: COLORS.text,
        fontSize: 20,
        fontWeight: '800',
    },
    t4aBadge: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: 'rgba(16,185,129,0.1)',
        paddingHorizontal: 10,
        paddingVertical: 4,
        borderRadius: 12,
        gap: 4,
    },
    t4aBadgeText: {
        color: COLORS.success,
        fontSize: 12,
        fontWeight: '600',
    },

    incomeSection: {
        marginBottom: 12,
    },
    incomeLabel: {
        color: COLORS.textDim,
        fontSize: 12,
        marginBottom: 4,
    },
    incomeAmount: {
        color: COLORS.text,
        fontSize: 24,
        fontWeight: '800',
    },

    divider: {
        height: 1,
        backgroundColor: COLORS.surfaceLight,
        marginVertical: 16,
    },

    breakdownTitle: {
        color: COLORS.text,
        fontSize: 14,
        fontWeight: '700',
        marginBottom: 12,
    },
    breakdownRow: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        marginBottom: 10,
    },
    breakdownLabel: {
        color: COLORS.textDim,
        fontSize: 14,
    },
    breakdownValue: {
        color: COLORS.text,
        fontSize: 14,
        fontWeight: '600',
    },

    noticeCard: {
        flexDirection: 'row',
        backgroundColor: 'rgba(255,149,0,0.1)',
        borderRadius: 12,
        padding: 14,
        marginTop: 16,
        gap: 10,
    },
    noticeText: {
        flex: 1,
        color: COLORS.warning,
        fontSize: 12,
        lineHeight: 18,
    },

    exportButton: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: COLORS.surface,
        borderRadius: 12,
        padding: 16,
        marginTop: 16,
        gap: 8,
        borderWidth: 1,
        borderColor: COLORS.accent,
    },
    exportButtonText: {
        color: COLORS.accent,
        fontSize: 16,
        fontWeight: '600',
    },

    emptyState: {
        alignItems: 'center',
        paddingVertical: 60,
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
        textAlign: 'center',
        paddingHorizontal: 40,
    },
});
