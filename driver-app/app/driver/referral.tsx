import React, { useState, useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    Platform,
    Alert,
    TextInput,
    Modal,
    Pressable,
    KeyboardAvoidingView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import api from '@shared/api/client';
import { useLanguageStore } from '../../store/languageStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface ReferralInfo {
    referral_code: string;
    total_referrals: number;
    referral_earnings: number;
    referral_link: string;
    terms: string;
}

interface ReferredDriver {
    name: string;
    email: string;
    referred_at: string;
    total_trips: number;
    status: string;
}

export default function ReferralScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { t } = useLanguageStore();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [referralInfo, setReferralInfo] = useState<ReferralInfo | null>(null);
    const [referredDrivers, setReferredDrivers] = useState<ReferredDriver[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [showApplyModal, setShowApplyModal] = useState(false);
    const [referralCodeInput, setReferralCodeInput] = useState('');

    useEffect(() => {
        fetchReferralInfo();
    }, []);

    const fetchReferralInfo = async () => {
        setIsLoading(true);
        try {
            const res = await api.get('/drivers/referral');
            setReferralInfo(res.data);

            // Fetch referred drivers
            const driversRes = await api.get('/drivers/referrals?limit=50');
            setReferredDrivers(driversRes.data.referred_drivers || []);
        } catch (err) {
            console.log('Error fetching referral info:', err);
        } finally {
            setIsLoading(false);
        }
    };

    const copyToClipboard = async () => {
        if (referralInfo?.referral_code) {
            await Clipboard.setStringAsync(referralInfo.referral_code);
            Alert.alert('Copied!', 'Referral code copied to clipboard');
        }
    };

    const shareReferral = async () => {
        if (referralInfo?.referral_link) {
            await Clipboard.setStringAsync(referralInfo.referral_link);
            Alert.alert(
                'Share Link',
                `Your referral link has been copied: ${referralInfo.referral_link}`
            );
        }
    };

    const applyReferralCode = async () => {
        if (!referralCodeInput.trim()) {
            Alert.alert('Error', 'Please enter a referral code');
            return;
        }

        try {
            await api.post('/drivers/referral/apply', { referral_code: referralCodeInput.trim() });
            Alert.alert('Success', 'Referral code applied successfully!');
            setShowApplyModal(false);
            setReferralCodeInput('');
        } catch (err: any) {
            const errorMessage = err.response?.data?.detail || 'Failed to apply referral code';
            Alert.alert('Error', errorMessage);
        }
    };

    return (
        <View style={styles.container}>
            {/* Header */}
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>{t('profile.referral') || 'Referral Program'}</Text>
                <View style={{ width: 40 }} />
            </View>

            <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
            <ScrollView style={styles.content} contentContainerStyle={{ paddingBottom: insets.bottom + 60 }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
                {/* Hero Section */}
                <LinearGradient
                    colors={['#E53935', '#C62828']}
                    start={{ x: 0, y: 0 }}
                    end={{ x: 1, y: 1 }}
                    style={styles.heroCard}
                >
                    <Text style={styles.heroTitle}>Invite Drivers & Earn</Text>
                    <Text style={styles.heroSubtitle}>
                        {t('referral.earn') || 'Earn $25 for each driver you refer who completes 50 rides'}
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
                        <Ionicons name="share-social-outline" size={20} color="#E53935" />
                        <Text style={styles.shareBtnText}>Share Referral Link</Text>
                    </TouchableOpacity>
                </LinearGradient>

                {/* Stats Section */}
                <View style={styles.statsRow}>
                    <View style={styles.statCard}>
                        <Text style={styles.statValue}>{referralInfo?.total_referrals || 0}</Text>
                        <Text style={styles.statLabel}>Total Referrals</Text>
                    </View>
                    <View style={styles.statCard}>
                        <Text style={styles.statValue}>${(referralInfo?.referral_earnings || 0).toFixed(2)}</Text>
                        <Text style={styles.statLabel}>Earnings</Text>
                    </View>
                </View>

                {/* Apply Referral Code */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Have a referral code?</Text>
                    <TouchableOpacity
                        style={styles.applyBtn}
                        onPress={() => setShowApplyModal(true)}
                    >
                        <Ionicons name="add-circle-outline" size={20} color={colors.primary} />
                        <Text style={styles.applyBtnText}>Apply Referral Code</Text>
                    </TouchableOpacity>
                </View>

                {/* Referred Drivers List */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Your Referrals</Text>
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
                                        <Text style={styles.referralTrips}>
                                            {driver.total_trips} trips completed
                                        </Text>
                                    </View>
                                    <View style={[
                                        styles.referralBadge,
                                        driver.status === 'active' ? styles.badgeActive : styles.badgePending
                                    ]}>
                                        <Text style={[
                                            styles.badgeText,
                                            driver.status === 'active' ? styles.badgeTextActive : styles.badgeTextPending
                                        ]}>
                                            {driver.status === 'active' ? 'Paid' : 'Pending'}
                                        </Text>
                                    </View>
                                </View>
                            ))}
                        </View>
                    ) : (
                        <View style={styles.emptyState}>
                            <Ionicons name="people-outline" size={48} color={colors.surfaceLight} />
                            <Text style={styles.emptyText}>No referrals yet</Text>
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
            </KeyboardAvoidingView>

            {/* Apply Referral Modal */}
            <Modal
                visible={showApplyModal}
                transparent
                animationType="slide"
                onRequestClose={() => setShowApplyModal(false)}
            >
                <KeyboardAvoidingView
                    style={{ flex: 1 }}
                    behavior={Platform.OS === 'ios' ? 'padding' : undefined}
                >
                <Pressable style={styles.modalOverlay} onPress={() => setShowApplyModal(false)}>
                    <Pressable style={[styles.modalContent, { paddingBottom: Math.max(insets.bottom + 12, 20) }]} onPress={(e) => e.stopPropagation()}>
                        <View style={styles.modalHeader}>
                            <Text style={styles.modalTitle}>Apply Referral Code</Text>
                            <TouchableOpacity onPress={() => setShowApplyModal(false)}>
                                <Ionicons name="close" size={24} color={colors.text} />
                            </TouchableOpacity>
                        </View>
                        <View style={styles.modalBody}>
                            <TextInput
                                style={styles.input}
                                placeholder="Enter referral code"
                                placeholderTextColor={colors.textDim}
                                value={referralCodeInput}
                                onChangeText={setReferralCodeInput}
                                autoCapitalize="characters"
                            />
                            <TouchableOpacity style={styles.submitBtn} onPress={applyReferralCode}>
                                <Text style={styles.submitBtnText}>Apply Code</Text>
                            </TouchableOpacity>
                        </View>
                    </Pressable>
                </Pressable>
                </KeyboardAvoidingView>
            </Modal>
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
        paddingBottom: 15,
        paddingHorizontal: 16,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
    },
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
        fontSize: 24,
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
        fontSize: 28,
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
        color: '#E53935',
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
        fontSize: 24,
        fontWeight: '700',
        color: colors.primary,
    },
    statLabel: {
        fontSize: 13,
        color: colors.textDim,
        marginTop: 4,
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
    applyBtn: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: colors.surface,
        padding: 16,
        borderRadius: 12,
        borderWidth: 1,
        borderColor: colors.primary,
        borderStyle: 'dashed',
    },
    applyBtnText: {
        color: colors.primary,
        fontSize: 15,
        fontWeight: '600',
        marginLeft: 8,
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
    referralBadge: {
        paddingHorizontal: 10,
        paddingVertical: 4,
        borderRadius: 12,
    },
    badgeActive: {
        backgroundColor: 'rgba(76,175,80,0.1)',
    },
    badgePending: {
        backgroundColor: 'rgba(255,152,0,0.1)',
    },
    badgeText: {
        fontSize: 12,
        fontWeight: '600',
    },
    badgeTextActive: {
        color: colors.success,
    },
    badgeTextPending: {
        color: '#FF9800',
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
    // Modal styles
    modalOverlay: {
        flex: 1,
        backgroundColor: 'rgba(0,0,0,0.5)',
        justifyContent: 'flex-end',
    },
    modalContent: {
        backgroundColor: colors.background,
        borderTopLeftRadius: 20,
        borderTopRightRadius: 20,
    },
    modalHeader: {
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: 20,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
    },
    modalTitle: {
        fontSize: 18,
        fontWeight: '600',
        color: colors.text,
    },
    modalBody: {
        padding: 20,
    },
    input: {
        backgroundColor: colors.surface,
        borderRadius: 12,
        padding: 16,
        fontSize: 16,
        color: colors.text,
        marginBottom: 16,
    },
    submitBtn: {
        backgroundColor: colors.primary,
        padding: 16,
        borderRadius: 12,
        alignItems: 'center',
    },
    submitBtnText: {
        color: '#fff',
        fontSize: 16,
        fontWeight: '600',
    },
    });
}
