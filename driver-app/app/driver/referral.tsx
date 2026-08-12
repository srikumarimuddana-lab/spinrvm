import React, { useState, useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    ActivityIndicator,
    Share,
} from 'react-native';
import { showToast } from '../../hooks/useToast';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface ReferralInfo {
    referral_code: string;
    total_referrals: number;
    qualified_referrals?: number;
    pending_referrals?: number;
    referral_earnings: string | number;
    referee_earnings?: string | number;
    reward_amount?: string | number;
    referrer_reward?: string | number;
    referee_reward?: string | number;
    referred_by?: { name: string; code: string } | null;
    rides_required?: number;
    referral_link: string;
    terms: string;
}

interface ReferredDriver {
    name: string;
    email: string;
    referred_at: string;
    total_trips: number;
    rides_required?: number;
    rides_remaining?: number;
    reward_amount?: string | number;
    qualified?: boolean;
    status: string; // 'earned' | 'in_progress'
}

export default function ReferralScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [referralInfo, setReferralInfo] = useState<ReferralInfo | null>(null);
    const [referredDrivers, setReferredDrivers] = useState<ReferredDriver[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(false);

    // Declared before the `useEffect` below (react-hooks/immutability /
    // React Compiler flags referencing a function before its source-order
    // declaration) — same expression, same effect timing, no behavior change.
    const fetchReferralInfo = async () => {
        setIsLoading(true);
        setError(false);
        // The summary (/drivers/referral) is the critical data — it carries the
        // code, share link and stats. Only its failure shows the full error
        // state. The referrals list is secondary: if it fails on its own we keep
        // the summary visible (code stays shareable) and just show the list's
        // empty state, instead of blanking a screen whose data actually loaded.
        try {
            const res = await api.get<ReferralInfo>('/drivers/referral');
            setReferralInfo(res.data);
        } catch (err) {
            console.error('[DriverReferral] summary load failed:', err);
            setReferralInfo(null);
            setError(true);
            setIsLoading(false);
            return;
        }

        try {
            const driversRes = await api.get<{ referred_drivers: any[] }>('/drivers/referrals?limit=50');
            setReferredDrivers(driversRes.data.referred_drivers || []);
        } catch (err) {
            console.error('[DriverReferral] referrals list load failed:', err);
            setReferredDrivers([]);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        // Mount-only fetch; fetchReferralInfo sets state after its own await,
        // not synchronously at the top of the effect. Empty deps, runs once.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        fetchReferralInfo();
    }, []);

    const copyToClipboard = async () => {
        if (referralInfo?.referral_code) {
            await Clipboard.setStringAsync(referralInfo.referral_code);
            showToast('success', 'Copied!', 'Referral code copied to clipboard');
        }
    };

    const shareReferral = async () => {
        if (!referralInfo?.referral_code) return;
        // No deep link — invitees paste the code on the signup screen, so the
        // message shares the CODE and tells them exactly where to enter it.
        const message = `Join me on Spinr! Download the driver app, then paste my referral code ${referralInfo.referral_code} during signup to start driving.`;
        try {
            await Share.share({ message });
        } catch {
            await Clipboard.setStringAsync(referralInfo.referral_code);
            showToast('success', 'Copied!', 'Referral code copied to clipboard');
        }
    };

    return (
        <View style={styles.container}>
            {/* Header */}
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Refer & Earn</Text>
                <View style={{ width: 40 }} />
            </View>

            {isLoading ? (
                <View style={styles.loading}><ActivityIndicator size="large" color={colors.primary} /></View>
            ) : error ? (
                <View style={styles.errorState}>
                    <Ionicons name="cloud-offline-outline" size={48} color={colors.textDim} />
                    <Text style={styles.errorTitle}>{"Couldn't load your referrals"}</Text>
                    <Text style={styles.errorSub}>Something went wrong reaching our servers. Please try again.</Text>
                    <TouchableOpacity style={styles.retryBtn} onPress={fetchReferralInfo} accessibilityLabel="Retry loading referrals">
                        <Ionicons name="refresh" size={18} color="#fff" />
                        <Text style={styles.retryBtnText}>Try Again</Text>
                    </TouchableOpacity>
                </View>
            ) : (
            <ScrollView style={styles.content} contentContainerStyle={{ paddingBottom: Math.max(insets.bottom, 16) + 24 }} showsVerticalScrollIndicator={false}>
                {/* Hero Section */}
                <LinearGradient
                    colors={[colors.primary, colors.primaryDark]}
                    start={{ x: 0, y: 0 }}
                    end={{ x: 1, y: 1 }}
                    style={styles.heroCard}
                >
                    <Text style={styles.heroTitle}>Invite Drivers & Earn</Text>
                    <Text style={styles.heroSubtitle}>
                        {/* Generic tagline only — the exact reward ($ and ride
                            threshold) is stated in the Terms section below, driven
                            by the backend so the two can never contradict. */}
                        Invite drivers to Spinr and earn rewards when they sign up and start driving.
                    </Text>

                    {referralInfo && (
                        <View style={styles.referralCodeBox}>
                            <Text style={styles.referralCodeLabel}>Your Referral Code</Text>
                            <Text style={styles.referralCode}>{referralInfo.referral_code}</Text>
                            <TouchableOpacity style={styles.copyBtn} onPress={copyToClipboard}>
                                <Ionicons name="copy-outline" size={18} color="#fff" />
                                <Text style={styles.copyBtnText}>Copy</Text>
                            </TouchableOpacity>
                        </View>
                    )}

                    <TouchableOpacity style={styles.shareBtn} onPress={shareReferral}>
                        <Ionicons name="share-social-outline" size={20} color={colors.primary} />
                        <Text style={styles.shareBtnText}>Share Your Code</Text>
                    </TouchableOpacity>
                </LinearGradient>

                {referralInfo?.referred_by ? (
                    <Text style={styles.referredByText}>
                        Referred by {referralInfo.referred_by.name} ({referralInfo.referred_by.code})
                    </Text>
                ) : null}

                {/* Stats Section */}
                <View style={styles.statsRow}>
                    <View style={styles.statCard}>
                        <Text style={styles.statValue}>{referralInfo?.total_referrals || 0}</Text>
                        <Text style={styles.statLabel}>Invited</Text>
                    </View>
                    <View style={styles.statCard}>
                        <Text style={styles.statValue}>{referralInfo?.qualified_referrals || 0}</Text>
                        <Text style={styles.statLabel}>Rewarded</Text>
                    </View>
                    <View style={styles.statCard}>
                        <Text style={styles.statValue}>
                            ${(
                                parseFloat(String(referralInfo?.referral_earnings ?? 0)) +
                                parseFloat(String(referralInfo?.referee_earnings ?? 0))
                            ).toFixed(2)}
                        </Text>
                        <Text style={styles.statLabel}>Earned</Text>
                    </View>
                </View>
                {parseFloat(String(referralInfo?.referee_earnings ?? 0)) > 0 ? (
                    <Text style={styles.referredByText}>
                        Includes your ${parseFloat(String(referralInfo?.referee_earnings ?? 0)).toFixed(2)} signup bonus
                    </Text>
                ) : null}

                {/* Reward amounts — who earns what ($0 = that party earns nothing) */}
                <View style={styles.statsRow}>
                    <View style={styles.statCard}>
                        <Text style={styles.statValue}>
                            ${parseFloat(String(referralInfo?.referrer_reward ?? referralInfo?.reward_amount ?? 0)).toFixed(2)}
                        </Text>
                        <Text style={styles.statLabel}>You earn / referral</Text>
                    </View>
                    <View style={styles.statCard}>
                        <Text style={styles.statValue}>
                            ${parseFloat(String(referralInfo?.referee_reward ?? 0)).toFixed(2)}
                        </Text>
                        <Text style={styles.statLabel}>
                            {parseFloat(String(referralInfo?.referee_reward ?? 0)) > 0 ? 'New driver earns' : 'No signup bonus'}
                        </Text>
                    </View>
                </View>

                {/* Referred Drivers List */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Your Invites</Text>
                    {referredDrivers.length > 0 ? (
                        <View style={styles.referralsList}>
                            {referredDrivers.map((driver, index) => (
                                <View key={index} style={styles.referralItem}>
                                    <View style={styles.referralAvatar}>
                                        <Text style={styles.referralInitial}>
                                            {driver.name.charAt(0).toUpperCase()}
                                        </Text>
                                    </View>
                                    <View style={styles.referralInfo}>
                                        <Text style={styles.referralName}>{driver.name}</Text>
                                        {driver.qualified ? (
                                            <Text style={styles.referralEarned}>
                                                Reward earned · ${parseFloat(String(driver.reward_amount ?? 0)).toFixed(2)}
                                            </Text>
                                        ) : (
                                            <>
                                                <Text style={styles.referralTrips}>
                                                    {driver.total_trips}/{driver.rides_required ?? 10} rides
                                                    {typeof driver.rides_remaining === 'number' && driver.rides_remaining > 0
                                                        ? ` · ${driver.rides_remaining} more to unlock`
                                                        : ''}
                                                </Text>
                                                <View style={styles.refProgressBar}>
                                                    <View style={[styles.refProgressFill, {
                                                        width: `${Math.min(100, ((driver.total_trips || 0) / (driver.rides_required || 10)) * 100)}%`,
                                                    }]} />
                                                </View>
                                            </>
                                        )}
                                    </View>
                                    <View style={[
                                        styles.referralBadge,
                                        driver.qualified ? styles.badgeActive : styles.badgePending
                                    ]}>
                                        <Text style={[
                                            styles.badgeText,
                                            driver.qualified ? styles.badgeTextActive : styles.badgeTextPending
                                        ]}>
                                            {driver.qualified ? 'Earned' : 'Pending'}
                                        </Text>
                                    </View>
                                </View>
                            ))}
                        </View>
                    ) : (
                        <View style={styles.emptyState}>
                            <Ionicons name="people-outline" size={44} color={colors.surfaceLight} />
                            <Text style={styles.emptyText}>No invites yet</Text>
                            <Text style={styles.emptySubtext}>
                                Share your code to start earning!
                            </Text>
                        </View>
                    )}
                </View>

                {/* Terms */}
                {referralInfo && (
                    <View style={styles.termsSection}>
                        <Text style={styles.termsTitle}>Terms & Conditions</Text>
                        <Text style={styles.termsText}>{referralInfo.terms}</Text>
                    </View>
                )}
            </ScrollView>
            )}
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
    container: {
        flex: 1,
        backgroundColor: colors.background,
    },
    header: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingBottom: 14,
        paddingHorizontal: 16,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
    },
    loading: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    errorState: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 32 },
    errorTitle: { fontSize: 18, fontWeight: '700', color: colors.text, marginTop: 16, textAlign: 'center' },
    errorSub: { fontSize: 14, color: colors.textDim, marginTop: 8, textAlign: 'center', lineHeight: 20 },
    retryBtn: {
        flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 24,
        backgroundColor: colors.primary, paddingHorizontal: 24, paddingVertical: 12, borderRadius: 25,
    },
    retryBtnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
    backBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: colors.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    headerTitle: {
        fontSize: 18,
        fontWeight: '600',
        color: colors.text,
    },
    content: {
        flex: 1,
        paddingHorizontal: 16,
    },
    heroCard: {
        borderRadius: 16,
        padding: 24,
        marginTop: 16,
        alignItems: 'center',
    },
    heroTitle: {
        fontSize: 22,
        fontWeight: '700',
        color: '#fff',
        marginBottom: 8,
    },
    heroSubtitle: {
        fontSize: 14,
        color: 'rgba(255,255,255,0.9)',
        textAlign: 'center',
        marginBottom: 20,
    },
    referralCodeBox: {
        backgroundColor: 'rgba(255,255,255,0.2)',
        borderRadius: 12,
        padding: 16,
        alignItems: 'center',
        width: '100%',
        marginBottom: 16,
    },
    referralCodeLabel: {
        fontSize: 12,
        color: 'rgba(255,255,255,0.8)',
        marginBottom: 4,
    },
    referralCode: {
        fontSize: 26,
        fontWeight: '700',
        color: '#fff',
        letterSpacing: 2,
        marginBottom: 8,
    },
    copyBtn: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: 'rgba(255,255,255,0.3)',
        paddingHorizontal: 16,
        paddingVertical: 8,
        borderRadius: 20,
    },
    copyBtnText: {
        color: '#fff',
        fontSize: 14,
        fontWeight: '600',
        marginLeft: 6,
    },
    shareBtn: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: '#fff',
        paddingHorizontal: 24,
        paddingVertical: 12,
        borderRadius: 25,
    },
    shareBtnText: {
        color: colors.primary,
        fontSize: 16,
        fontWeight: '600',
        marginLeft: 8,
    },
    statsRow: {
        flexDirection: 'row',
        marginTop: 16,
        gap: 12,
    },
    statCard: {
        flex: 1,
        backgroundColor: colors.surface,
        borderRadius: 12,
        padding: 16,
        alignItems: 'center',
    },
    statValue: {
        fontSize: 22,
        fontWeight: '700',
        color: colors.primary,
    },
    statLabel: {
        fontSize: 12,
        color: colors.textDim,
        marginTop: 4,
    },
    referredByText: {
        fontSize: 13,
        color: colors.textDim,
        textAlign: 'center',
        marginTop: 12,
    },
    section: {
        marginTop: 24,
    },
    sectionTitle: {
        fontSize: 16,
        fontWeight: '600',
        color: colors.text,
        marginBottom: 12,
    },
    referralsList: {
        backgroundColor: colors.surface,
        borderRadius: 12,
        overflow: 'hidden',
    },
    referralItem: {
        flexDirection: 'row',
        alignItems: 'center',
        padding: 12,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
    },
    referralAvatar: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: colors.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    referralInitial: {
        fontSize: 18,
        fontWeight: '600',
        color: colors.text,
    },
    referralInfo: {
        flex: 1,
        marginLeft: 12,
    },
    referralName: {
        fontSize: 15,
        fontWeight: '500',
        color: colors.text,
    },
    referralTrips: {
        fontSize: 12,
        color: colors.textDim,
        marginTop: 2,
    },
    referralEarned: {
        fontSize: 12,
        color: colors.success,
        fontWeight: '700',
        marginTop: 2,
    },
    refProgressBar: {
        height: 5,
        borderRadius: 3,
        backgroundColor: colors.border,
        overflow: 'hidden',
        marginTop: 6,
        maxWidth: 180,
    },
    refProgressFill: {
        height: '100%',
        borderRadius: 3,
        backgroundColor: colors.primary,
    },
    referralBadge: {
        paddingHorizontal: 10,
        paddingVertical: 4,
        borderRadius: 12,
    },
    badgeActive: {
        backgroundColor: 'rgba(16,185,129,0.12)',
    },
    badgePending: {
        backgroundColor: 'rgba(245,158,11,0.12)',
    },
    badgeText: {
        fontSize: 12,
        fontWeight: '600',
    },
    badgeTextActive: {
        color: colors.success,
    },
    badgeTextPending: {
        color: '#F59E0B',
    },
    emptyState: {
        backgroundColor: colors.surface,
        borderRadius: 12,
        padding: 32,
        alignItems: 'center',
    },
    emptyText: {
        fontSize: 16,
        fontWeight: '600',
        color: colors.text,
        marginTop: 12,
    },
    emptySubtext: {
        fontSize: 14,
        color: colors.textDim,
        marginTop: 4,
    },
    termsSection: {
        marginTop: 24,
        marginBottom: 32,
        padding: 16,
        backgroundColor: colors.surface,
        borderRadius: 12,
    },
    termsTitle: {
        fontSize: 14,
        fontWeight: '600',
        color: colors.text,
        marginBottom: 8,
    },
    termsText: {
        fontSize: 13,
        color: colors.textDim,
        lineHeight: 18,
    },
    });
}
