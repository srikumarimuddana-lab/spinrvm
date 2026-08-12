import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    ActivityIndicator,
    Share,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import api from '@shared/api/client';
import { showToast } from '../store/toastStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface ReferralInfo {
    referral_code: string;
    referral_link: string;
    total_referrals: number;
    qualified_referrals: number;
    pending_referrals: number;
    referral_earnings: string | number;
    referee_earnings?: string | number;
    referrer_reward: string | number;
    referee_reward: string | number;
    rides_required: number;
    terms: string;
}

interface ReferredRider {
    name: string;
    referred_at: string;
    completed_rides: number;
    rides_required: number;
    qualified: boolean;
    status: string; // 'earned' | 'in_progress'
}

export default function RiderReferralScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);

    const [info, setInfo] = useState<ReferralInfo | null>(null);
    const [referees, setReferees] = useState<ReferredRider[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError(false);
        try {
            const [infoRes, refsRes] = await Promise.all([
                api.get<ReferralInfo>('/users/referral'),
                api.get<{ referees: ReferredRider[] }>('/users/referrals'),
            ]);
            setInfo(infoRes.data);
            setReferees(refsRes.data?.referees || []);
        } catch (err) {
            // Surface the failure instead of silently rendering an empty
            // "success" state — a null `info` otherwise hides the code box and
            // shows zeroed stats, which reads as a broken/blank screen.
            console.error('[RiderReferral] load failed:', err);
            setInfo(null);
            setError(true);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const copyCode = async () => {
        if (!info?.referral_code) return;
        await Clipboard.setStringAsync(info.referral_code);
        showToast('Copied!', 'Referral code copied to clipboard', 'success');
    };

    const share = async () => {
        if (!info) return;
        // No deep link — invitees paste the code on the signup screen, so the
        // message shares the CODE and tells them exactly where to enter it.
        const message = `Join me on Spinr! Download the app, then paste my code ${info.referral_code} during signup and we both get a reward when you take your first ride.`;
        try {
            await Share.share({ message });
        } catch {
            await Clipboard.setStringAsync(info.referral_code);
            showToast('Copied!', 'Referral code copied to clipboard', 'success');
        }
    };

    return (
        <View style={styles.container}>
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Refer & Earn</Text>
                <View style={{ width: 40 }} />
            </View>

            {loading ? (
                <View style={styles.loading}><ActivityIndicator size="large" color={colors.primary} /></View>
            ) : error ? (
                <View style={styles.errorState}>
                    <Ionicons name="cloud-offline-outline" size={48} color={colors.textDim} />
                    <Text style={styles.errorTitle}>Couldn&apos;t load your referrals</Text>
                    <Text style={styles.errorSub}>Something went wrong reaching our servers. Please try again.</Text>
                    <TouchableOpacity style={styles.retryBtn} onPress={load} accessibilityLabel="Retry loading referrals">
                        <Ionicons name="refresh" size={18} color="#fff" />
                        <Text style={styles.retryBtnText}>Try Again</Text>
                    </TouchableOpacity>
                </View>
            ) : (
                <ScrollView style={styles.content} contentContainerStyle={{ paddingBottom: insets.bottom + 32 }} showsVerticalScrollIndicator={false}>
                    <LinearGradient colors={[colors.primary, colors.primaryDark]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.hero}>
                        <Text style={styles.heroTitle}>Give a ride, get a reward</Text>
                        <Text style={styles.heroSubtitle}>Share your code — friends paste it when they sign up, and you both earn.</Text>

                        {info && (
                            <View style={styles.codeBox}>
                                <Text style={styles.codeLabel}>Your Referral Code</Text>
                                <Text style={styles.code}>{info.referral_code}</Text>
                                <TouchableOpacity style={styles.copyBtn} onPress={copyCode}>
                                    <Ionicons name="copy-outline" size={16} color="#fff" />
                                    <Text style={styles.copyBtnText}>Copy</Text>
                                </TouchableOpacity>
                            </View>
                        )}

                        <TouchableOpacity style={styles.shareBtn} onPress={share}>
                            <Ionicons name="share-social-outline" size={20} color={colors.primary} />
                            <Text style={styles.shareBtnText}>Share Your Code</Text>
                        </TouchableOpacity>
                    </LinearGradient>

                    <View style={styles.statsRow}>
                        <Stat styles={styles} value={String(info?.total_referrals ?? 0)} label="Invited" />
                        <Stat styles={styles} value={String(info?.qualified_referrals ?? 0)} label="Rewarded" />
                        <Stat
                            styles={styles}
                            value={`$${(
                                parseFloat(String(info?.referral_earnings ?? 0)) +
                                parseFloat(String(info?.referee_earnings ?? 0))
                            ).toFixed(2)}`}
                            label="Earned"
                        />
                    </View>
                    {parseFloat(String(info?.referee_earnings ?? 0)) > 0 ? (
                        <Text style={styles.emptySub}>
                            Includes your ${parseFloat(String(info?.referee_earnings ?? 0)).toFixed(2)} signup bonus
                        </Text>
                    ) : null}

                    <Text style={styles.sectionTitle}>Your Invites</Text>
                    {referees.length === 0 ? (
                        <View style={styles.empty}>
                            <Ionicons name="people-outline" size={44} color={colors.surfaceLight} />
                            <Text style={styles.emptyText}>No invites yet</Text>
                            <Text style={styles.emptySub}>Share your code to start earning!</Text>
                        </View>
                    ) : (
                        <View style={styles.list}>
                            {referees.map((r, i) => {
                                const pct = Math.min(100, Math.round(((r.completed_rides || 0) / (r.rides_required || 1)) * 100));
                                return (
                                    <View key={i} style={styles.item}>
                                        <View style={styles.avatar}><Text style={styles.avatarText}>{r.name.charAt(0).toUpperCase()}</Text></View>
                                        <View style={{ flex: 1, marginLeft: 12 }}>
                                            <Text style={styles.itemName}>{r.name}</Text>
                                            {r.qualified ? (
                                                <Text style={styles.itemEarned}>Reward earned</Text>
                                            ) : (
                                                <>
                                                    <Text style={styles.itemProgress}>
                                                        {r.completed_rides}/{r.rides_required} rides
                                                    </Text>
                                                    <View style={styles.bar}><View style={[styles.barFill, { width: `${pct}%` }]} /></View>
                                                </>
                                            )}
                                        </View>
                                        <View style={[styles.badge, r.qualified ? styles.badgeEarned : styles.badgePending]}>
                                            <Text style={[styles.badgeText, r.qualified ? styles.badgeTextEarned : styles.badgeTextPending]}>
                                                {r.qualified ? 'Earned' : 'Pending'}
                                            </Text>
                                        </View>
                                    </View>
                                );
                            })}
                        </View>
                    )}

                    {!!info?.terms && (
                        <>
                            <Text style={styles.sectionTitle}>Terms & Conditions</Text>
                            <View style={styles.termsCard}>
                                <Text style={styles.termsText}>{info.terms}</Text>
                            </View>
                        </>
                    )}
                </ScrollView>
            )}
        </View>
    );
}

function Stat({ styles, value, label }: { styles: any; value: string; label: string }) {
    return (
        <View style={styles.statCard}>
            <Text style={styles.statValue}>{value}</Text>
            <Text style={styles.statLabel}>{label}</Text>
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        header: {
            flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
            paddingBottom: 14, paddingHorizontal: 16, borderBottomWidth: 1, borderBottomColor: colors.border,
        },
        backBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.surfaceLight, justifyContent: 'center', alignItems: 'center' },
        headerTitle: { fontSize: 18, fontWeight: '600', color: colors.text },
        loading: { flex: 1, justifyContent: 'center', alignItems: 'center' },
        errorState: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 32 },
        errorTitle: { fontSize: 18, fontWeight: '700', color: colors.text, marginTop: 16, textAlign: 'center' },
        errorSub: { fontSize: 14, color: colors.textDim, marginTop: 8, textAlign: 'center', lineHeight: 20 },
        retryBtn: {
            flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 24,
            backgroundColor: colors.primary, paddingHorizontal: 24, paddingVertical: 12, borderRadius: 25,
        },
        retryBtnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
        content: { flex: 1, paddingHorizontal: 16 },
        hero: { borderRadius: 16, padding: 24, marginTop: 16, alignItems: 'center' },
        heroTitle: { fontSize: 22, fontWeight: '700', color: '#fff', marginBottom: 8 },
        heroSubtitle: { fontSize: 14, color: 'rgba(255,255,255,0.9)', textAlign: 'center', marginBottom: 20 },
        codeBox: { backgroundColor: 'rgba(255,255,255,0.2)', borderRadius: 12, padding: 16, alignItems: 'center', width: '100%', marginBottom: 16 },
        codeLabel: { fontSize: 12, color: 'rgba(255,255,255,0.8)', marginBottom: 4 },
        code: { fontSize: 26, fontWeight: '700', color: '#fff', letterSpacing: 2, marginBottom: 8 },
        copyBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: 'rgba(255,255,255,0.3)', paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20 },
        copyBtnText: { color: '#fff', fontSize: 14, fontWeight: '600' },
        shareBtn: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: '#fff', paddingHorizontal: 24, paddingVertical: 12, borderRadius: 25 },
        shareBtnText: { color: colors.primary, fontSize: 16, fontWeight: '600' },
        statsRow: { flexDirection: 'row', gap: 12, marginTop: 16 },
        statCard: { flex: 1, backgroundColor: colors.surface, borderRadius: 12, padding: 16, alignItems: 'center' },
        statValue: { fontSize: 22, fontWeight: '700', color: colors.primary },
        statLabel: { fontSize: 12, color: colors.textDim, marginTop: 4 },
        sectionTitle: { fontSize: 16, fontWeight: '600', color: colors.text, marginTop: 24, marginBottom: 12 },
        termsCard: { backgroundColor: colors.surface, borderRadius: 12, padding: 16 },
        termsText: { fontSize: 13, lineHeight: 20, color: colors.textDim },
        empty: { backgroundColor: colors.surface, borderRadius: 12, padding: 32, alignItems: 'center' },
        emptyText: { fontSize: 16, fontWeight: '600', color: colors.text, marginTop: 12 },
        emptySub: { fontSize: 14, color: colors.textDim, marginTop: 4 },
        list: { backgroundColor: colors.surface, borderRadius: 12, overflow: 'hidden' },
        item: { flexDirection: 'row', alignItems: 'center', padding: 12, borderBottomWidth: 1, borderBottomColor: colors.border },
        avatar: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.surfaceLight, justifyContent: 'center', alignItems: 'center' },
        avatarText: { fontSize: 18, fontWeight: '600', color: colors.text },
        itemName: { fontSize: 15, fontWeight: '500', color: colors.text },
        itemProgress: { fontSize: 12, color: colors.textDim, marginTop: 2 },
        itemEarned: { fontSize: 12, color: colors.success, fontWeight: '700', marginTop: 2 },
        bar: { height: 5, borderRadius: 3, backgroundColor: colors.border, overflow: 'hidden', marginTop: 6, maxWidth: 180 },
        barFill: { height: '100%', borderRadius: 3, backgroundColor: colors.primary },
        badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
        badgeEarned: { backgroundColor: 'rgba(16,185,129,0.12)' },
        badgePending: { backgroundColor: 'rgba(245,158,11,0.12)' },
        badgeText: { fontSize: 12, fontWeight: '600' },
        badgeTextEarned: { color: colors.success },
        badgeTextPending: { color: '#F59E0B' },
    });
}
