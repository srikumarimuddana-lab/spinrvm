import React, { useState, useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    Platform,
    TextInput,
    Alert,
    ActivityIndicator,
    Linking,
    KeyboardAvoidingView,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';
import { useAuthStore } from '@shared/store/authStore';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function PayoutScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const {
        driverBalance,
        hasBankAccount,
        bankAccount,
        fetchDriverBalance,
        fetchBankAccount,
        requestPayout,
        isLoading,
        error,
        clearError,
    } = useDriverStore();

    const [payoutAmount, setPayoutAmount] = useState('');
    const [stripeOnboarding, setStripeOnboarding] = useState(false);
    const [gstNumber, setGstNumber] = useState('');
    const [showGstForm, setShowGstForm] = useState(false);
    const [savingGst, setSavingGst] = useState(false);
    const [stripeAccountStatus, setStripeAccountStatus] = useState<string | null>(null);
    const [initialLoading, setInitialLoading] = useState(true);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        setInitialLoading(true);
        try {
            await Promise.all([
                fetchDriverBalance(),
                fetchBankAccount(),
                loadStripeStatus(),
                loadGstNumber(),
            ]);
        } catch (err) {
            // Errors are handled individually in each function
        } finally {
            setInitialLoading(false);
        }
    };

    const loadStripeStatus = async () => {
        try {
            const res = await api.get('/drivers/balance');
            setStripeAccountStatus(
                res.data.stripe_account_onboarded ? 'active' : 'not_onboarded'
            );
        } catch {
            setStripeAccountStatus('not_onboarded');
        }
    };

    const loadGstNumber = async () => {
        try {
            const res = await api.get('/drivers/me');
            setGstNumber(res.data.gst_number || '');
        } catch {
            // Not critical
        }
    };

    useEffect(() => {
        if (error) {
            Alert.alert('Error', error);
            clearError();
        }
    }, [error]);

    const handleStripeOnboarding = async () => {
        setStripeOnboarding(true);
        try {
            const res = await api.post('/drivers/stripe-onboard');
            const { url, mock } = res.data;

            if (mock) {
                Alert.alert(
                    'Demo Mode',
                    'Stripe is not configured yet. In production, you will be redirected to Stripe to complete identity verification and add your bank account.',
                );
            } else if (url) {
                await Linking.openURL(url);
            }
        } catch (err: any) {
            Alert.alert('Error', err.response?.data?.detail || 'Failed to start Stripe onboarding');
        } finally {
            setStripeOnboarding(false);
        }
    };

    const handleSaveGst = async () => {
        // Validate GST/BN format: 9 digits or 15 chars (9-digit BN + RT0001)
        const cleaned = gstNumber.replace(/\s/g, '');
        if (cleaned && !/^\d{9}(RT\d{4})?$/.test(cleaned)) {
            Alert.alert('Invalid Format', 'Enter your 9-digit Business Number (BN) or full GST number (e.g., 123456789RT0001)');
            return;
        }

        setSavingGst(true);
        try {
            await api.put('/drivers/me', { gst_number: cleaned || null });
            setShowGstForm(false);
            Alert.alert('Saved', 'GST/BN number updated successfully');
        } catch (err: any) {
            Alert.alert('Error', err.response?.data?.detail || 'Failed to save GST number');
        } finally {
            setSavingGst(false);
        }
    };

    const handleRequestPayout = async () => {
        const amount = parseFloat(payoutAmount);
        if (isNaN(amount) || amount <= 0) {
            Alert.alert('Error', 'Please enter a valid amount');
            return;
        }
        if (amount < 10) {
            Alert.alert('Error', 'Minimum payout amount is $10');
            return;
        }
        if (driverBalance && amount > driverBalance.available_balance) {
            Alert.alert('Error', `Insufficient balance. Available: $${driverBalance.available_balance.toFixed(2)}`);
            return;
        }

        const result = await requestPayout(amount);
        if (result.success) {
            setPayoutAmount('');
            Alert.alert('Success', 'Payout request submitted. Funds will arrive in 2-3 business days.');
        }
    };

    const formatCurrency = (amount: number) => `$${amount.toFixed(2)}`;

    const isStripeReady = stripeAccountStatus === 'active' || hasBankAccount;

    if (initialLoading) {
        return (
            <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
                <ActivityIndicator size="large" color={colors.primary} />
            </View>
        );
    }

    return (
        <View style={styles.container}>
            {/* Header */}
            <LinearGradient colors={[colors.surface, colors.background]} style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Payouts</Text>
                    <TouchableOpacity onPress={() => router.push('/driver/payout-history' as any)}>
                        <Ionicons name="time" size={22} color={colors.text} />
                    </TouchableOpacity>
                </View>
            </LinearGradient>

            <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
            <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + 140 }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
                {/* Balance Card */}
                <View style={styles.balanceCard}>
                    <Text style={styles.balanceLabel}>AVAILABLE BALANCE</Text>
                    <Text style={styles.balanceAmount}>
                        {driverBalance ? formatCurrency(driverBalance.available_balance) : '$0.00'}
                    </Text>

                    <View style={styles.balanceDetails}>
                        <View style={styles.balanceItem}>
                            <Text style={styles.balanceItemLabel}>Total Earnings</Text>
                            <Text style={styles.balanceItemValue}>
                                {driverBalance ? formatCurrency(driverBalance.total_earnings) : '$0.00'}
                            </Text>
                        </View>
                        <View style={styles.balanceDivider} />
                        <View style={styles.balanceItem}>
                            <Text style={styles.balanceItemLabel}>Pending</Text>
                            <Text style={styles.balanceItemValue}>
                                {driverBalance ? formatCurrency(driverBalance.pending_payouts) : '$0.00'}
                            </Text>
                        </View>
                        <View style={styles.balanceDivider} />
                        <View style={styles.balanceItem}>
                            <Text style={styles.balanceItemLabel}>Paid Out</Text>
                            <Text style={styles.balanceItemValue}>
                                {driverBalance ? formatCurrency(driverBalance.total_paid_out) : '$0.00'}
                            </Text>
                        </View>
                    </View>
                </View>

                {/* Stripe Connect Setup */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Payment Account</Text>
                    {stripeAccountStatus === 'active' ? (
                        <View style={styles.stripeCard}>
                            <View style={styles.stripeIconContainer}>
                                <Ionicons name="checkmark-circle" size={28} color={colors.success} />
                            </View>
                            <View style={{ flex: 1 }}>
                                <Text style={styles.stripeTitle}>Stripe Connected</Text>
                                <Text style={styles.stripeSubtitle}>
                                    Identity verified. Bank account linked.
                                </Text>
                            </View>
                            <TouchableOpacity onPress={handleStripeOnboarding}>
                                <Text style={{ color: colors.primary, fontSize: 13, fontWeight: '600' }}>Update</Text>
                            </TouchableOpacity>
                        </View>
                    ) : (
                        <TouchableOpacity
                            style={styles.stripeSetupCard}
                            onPress={handleStripeOnboarding}
                            disabled={stripeOnboarding}
                        >
                            {stripeOnboarding ? (
                                <ActivityIndicator size="small" color={colors.primary} />
                            ) : (
                                <>
                                    <View style={styles.stripeSetupIcon}>
                                        <Ionicons name="shield-checkmark" size={32} color={colors.primary} />
                                    </View>
                                    <Text style={styles.stripeSetupTitle}>Set Up Payouts with Stripe</Text>
                                    <Text style={styles.stripeSetupDesc}>
                                        Stripe will securely verify your identity and collect your banking details. This includes:
                                    </Text>
                                    <View style={styles.requirementsList}>
                                        <View style={styles.requirementItem}>
                                            <Ionicons name="person" size={16} color={colors.textDim} />
                                            <Text style={styles.requirementText}>Government-issued photo ID</Text>
                                        </View>
                                        <View style={styles.requirementItem}>
                                            <Ionicons name="camera" size={16} color={colors.textDim} />
                                            <Text style={styles.requirementText}>Selfie for proof of liveness</Text>
                                        </View>
                                        <View style={styles.requirementItem}>
                                            <Ionicons name="home" size={16} color={colors.textDim} />
                                            <Text style={styles.requirementText}>Home address verification</Text>
                                        </View>
                                        <View style={styles.requirementItem}>
                                            <Ionicons name="card" size={16} color={colors.textDim} />
                                            <Text style={styles.requirementText}>Bank account or debit card</Text>
                                        </View>
                                    </View>
                                    <View style={styles.stripeSetupBtn}>
                                        <Text style={styles.stripeSetupBtnText}>Continue to Stripe</Text>
                                        <Ionicons name="arrow-forward" size={18} color="#fff" />
                                    </View>
                                </>
                            )}
                        </TouchableOpacity>
                    )}
                </View>

                {/* GST / Business Number */}
                <View style={styles.section}>
                    <View style={styles.sectionHeader}>
                        <Text style={styles.sectionTitle}>Tax Information</Text>
                        {!showGstForm && (
                            <TouchableOpacity onPress={() => setShowGstForm(true)}>
                                <Text style={styles.addLink}>{gstNumber ? 'Edit' : 'Add'}</Text>
                            </TouchableOpacity>
                        )}
                    </View>

                    {showGstForm ? (
                        <View style={styles.gstForm}>
                            <Text style={styles.inputLabel}>GST/HST Number (Business Number)</Text>
                            <Text style={styles.gstHelpText}>
                                If you're registered for GST/HST, enter your 9-digit Business Number (BN) or full program account (e.g., 123456789RT0001). Leave blank if not registered.
                            </Text>
                            <TextInput
                                style={styles.textInput}
                                placeholder="123456789RT0001"
                                placeholderTextColor={colors.textDim}
                                value={gstNumber}
                                onChangeText={setGstNumber}
                                autoCapitalize="characters"
                                maxLength={15}
                            />
                            <Text style={styles.gstNote}>
                                Drivers earning over $30,000/year must register for GST/HST with CRA.
                            </Text>
                            <View style={styles.gstFormButtons}>
                                <TouchableOpacity
                                    style={styles.cancelBtn}
                                    onPress={() => setShowGstForm(false)}
                                >
                                    <Text style={styles.cancelBtnText}>Cancel</Text>
                                </TouchableOpacity>
                                <TouchableOpacity
                                    style={styles.saveBtn}
                                    onPress={handleSaveGst}
                                    disabled={savingGst}
                                >
                                    {savingGst ? (
                                        <ActivityIndicator size="small" color="#fff" />
                                    ) : (
                                        <Text style={styles.saveBtnText}>Save</Text>
                                    )}
                                </TouchableOpacity>
                            </View>
                        </View>
                    ) : (
                        <View style={styles.gstCard}>
                            <Ionicons name="document-text" size={22} color={colors.textDim} />
                            <View style={{ flex: 1, marginLeft: 12 }}>
                                <Text style={styles.gstLabel}>GST/HST Number</Text>
                                <Text style={styles.gstValue}>
                                    {gstNumber || 'Not provided'}
                                </Text>
                            </View>
                        </View>
                    )}
                </View>

                {/* G14: Show a clear CTA when Stripe isn't set up yet */}
                {!isStripeReady && !initialLoading && (
                    <View style={styles.section}>
                        <View style={[styles.payoutCard, { alignItems: 'center', paddingVertical: 24 }]}>
                            <Ionicons name="card-outline" size={40} color={colors.textDim} />
                            <Text style={[styles.sectionTitle, { marginTop: 12, textAlign: 'center' }]}>
                                Set Up Payouts
                            </Text>
                            <Text style={{ color: colors.textDim, fontSize: 13, textAlign: 'center', marginTop: 4, marginBottom: 16, lineHeight: 18 }}>
                                Connect your bank account via Stripe to start receiving your earnings.
                            </Text>
                            <TouchableOpacity
                                style={[styles.payoutButton, { paddingHorizontal: 24, paddingVertical: 12, opacity: stripeOnboarding ? 0.6 : 1 }]}
                                onPress={handleStripeOnboarding}
                                disabled={stripeOnboarding}
                            >
                                <Text style={styles.payoutButtonText}>
                                    {stripeOnboarding ? 'Opening Stripe...' : 'Connect Bank Account'}
                                </Text>
                            </TouchableOpacity>
                        </View>
                    </View>
                )}

                {/* Payout Request */}
                {isStripeReady && driverBalance && driverBalance.available_balance > 0 && (
                    <View style={styles.section}>
                        <Text style={styles.sectionTitle}>Request Payout</Text>
                        <View style={styles.payoutCard}>
                            <View style={styles.payoutInputRow}>
                                <Text style={styles.dollarSign}>$</Text>
                                <TextInput
                                    style={styles.payoutInput}
                                    placeholder="Amount"
                                    placeholderTextColor={colors.textDim}
                                    keyboardType="decimal-pad"
                                    value={payoutAmount}
                                    onChangeText={setPayoutAmount}
                                />
                                <TouchableOpacity
                                    style={[
                                        styles.payoutButton,
                                        (!payoutAmount || isLoading) && styles.payoutButtonDisabled,
                                    ]}
                                    onPress={handleRequestPayout}
                                    disabled={!payoutAmount || isLoading}
                                >
                                    {isLoading ? (
                                        <ActivityIndicator size="small" color="#fff" />
                                    ) : (
                                        <Text style={styles.payoutButtonText}>Request</Text>
                                    )}
                                </TouchableOpacity>
                            </View>
                            <TouchableOpacity
                                onPress={() => setPayoutAmount(driverBalance.available_balance.toString())}
                            >
                                <Text style={styles.maxAmount}>
                                    Available: {formatCurrency(driverBalance.available_balance)} · Min $10.00
                                </Text>
                            </TouchableOpacity>
                        </View>
                    </View>
                )}

                {/* Info Note */}
                <View style={styles.infoNote}>
                    <Ionicons name="information-circle" size={20} color={colors.textDim} />
                    <Text style={styles.infoText}>
                        Payouts are processed via Stripe within 2-3 business days. Minimum payout is $10. Stripe handles all identity verification and banking securely.
                    </Text>
                </View>
            </ScrollView>
            </KeyboardAvoidingView>
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
        backBtn: {
            width: 40,
            height: 40,
            borderRadius: 20,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        headerTitle: { color: colors.text, fontSize: 20, fontWeight: '700' },

        balanceCard: {
            backgroundColor: colors.primary,
            marginHorizontal: 16,
            marginTop: 16,
            borderRadius: 20,
            padding: 24,
        },
        balanceLabel: {
            color: 'rgba(255,255,255,0.7)',
            fontSize: 11,
            letterSpacing: 1.5,
            fontWeight: '600',
            textAlign: 'center',
        },
        balanceAmount: {
            color: '#fff',
            fontSize: 48,
            fontWeight: '800',
            textAlign: 'center',
            marginVertical: 8,
        },
        balanceDetails: {
            flexDirection: 'row',
            marginTop: 16,
            paddingTop: 16,
            borderTopWidth: 1,
            borderTopColor: 'rgba(255,255,255,0.2)',
        },
        balanceItem: { flex: 1, alignItems: 'center' },
        balanceItemLabel: {
            color: 'rgba(255,255,255,0.7)',
            fontSize: 10,
            marginBottom: 4,
        },
        balanceItemValue: {
            color: '#fff',
            fontSize: 14,
            fontWeight: '600',
        },
        balanceDivider: {
            width: 1,
            backgroundColor: 'rgba(255,255,255,0.2)',
        },

        section: {
            paddingHorizontal: 16,
            marginTop: 24,
        },
        sectionHeader: {
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 12,
        },
        sectionTitle: {
            color: colors.text,
            fontSize: 17,
            fontWeight: '700',
            marginBottom: 12,
        },
        addLink: {
            color: colors.primary,
            fontSize: 14,
            fontWeight: '600',
            marginBottom: 12,
        },

        // Stripe Connected Card
        stripeCard: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 16,
            flexDirection: 'row',
            alignItems: 'center',
        },
        stripeIconContainer: { marginRight: 12 },
        stripeTitle: { color: colors.text, fontSize: 16, fontWeight: '600' },
        stripeSubtitle: { color: colors.textDim, fontSize: 13, marginTop: 2 },

        // Stripe Setup Card
        stripeSetupCard: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 20,
            alignItems: 'center',
            borderWidth: 1,
            borderColor: colors.border,
        },
        stripeSetupIcon: {
            width: 64,
            height: 64,
            borderRadius: 32,
            backgroundColor: `${colors.primary}15`,
            justifyContent: 'center',
            alignItems: 'center',
            marginBottom: 12,
        },
        stripeSetupTitle: {
            color: colors.text,
            fontSize: 18,
            fontWeight: '700',
            marginBottom: 8,
            textAlign: 'center',
        },
        stripeSetupDesc: {
            color: colors.textDim,
            fontSize: 13,
            textAlign: 'center',
            lineHeight: 18,
            marginBottom: 16,
        },
        requirementsList: {
            width: '100%',
            marginBottom: 20,
        },
        requirementItem: {
            flexDirection: 'row',
            alignItems: 'center',
            paddingVertical: 6,
            gap: 10,
        },
        requirementText: {
            color: colors.text,
            fontSize: 14,
        },
        stripeSetupBtn: {
            backgroundColor: colors.primary,
            paddingHorizontal: 28,
            paddingVertical: 14,
            borderRadius: 12,
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
        },
        stripeSetupBtnText: {
            color: '#fff',
            fontSize: 16,
            fontWeight: '700',
        },

        // GST Form
        gstForm: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 16,
        },
        gstCard: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 16,
            flexDirection: 'row',
            alignItems: 'center',
        },
        gstLabel: {
            color: colors.textDim,
            fontSize: 12,
        },
        gstValue: {
            color: colors.text,
            fontSize: 15,
            fontWeight: '500',
            marginTop: 2,
        },
        gstHelpText: {
            color: colors.textDim,
            fontSize: 12,
            lineHeight: 17,
            marginBottom: 12,
            marginTop: 4,
        },
        gstNote: {
            color: colors.textDim,
            fontSize: 11,
            marginTop: 8,
            fontStyle: 'italic',
        },
        gstFormButtons: {
            flexDirection: 'row',
            gap: 12,
            marginTop: 16,
        },
        cancelBtn: {
            flex: 1,
            paddingVertical: 12,
            borderRadius: 10,
            backgroundColor: colors.surfaceLight,
            alignItems: 'center',
        },
        cancelBtnText: { color: colors.textDim, fontSize: 14, fontWeight: '600' },
        saveBtn: {
            flex: 2,
            paddingVertical: 12,
            borderRadius: 10,
            backgroundColor: colors.primary,
            alignItems: 'center',
        },
        saveBtnText: { color: '#fff', fontSize: 14, fontWeight: '600' },

        inputLabel: {
            color: colors.text,
            fontSize: 13,
            marginBottom: 4,
            fontWeight: '600',
        },
        textInput: {
            backgroundColor: colors.surfaceLight,
            borderRadius: 12,
            paddingHorizontal: 14,
            paddingVertical: 12,
            fontSize: 15,
            color: colors.text,
        },

        // Payout
        payoutCard: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 16,
        },
        payoutInputRow: {
            flexDirection: 'row',
            alignItems: 'center',
        },
        dollarSign: {
            fontSize: 24,
            fontWeight: '700',
            color: colors.text,
            marginRight: 4,
        },
        payoutInput: {
            flex: 1,
            fontSize: 24,
            fontWeight: '700',
            color: colors.text,
            paddingVertical: 8,
        },
        payoutButton: {
            backgroundColor: colors.primary,
            paddingHorizontal: 24,
            paddingVertical: 12,
            borderRadius: 12,
        },
        payoutButtonDisabled: {
            opacity: 0.5,
        },
        payoutButtonText: {
            color: '#fff',
            fontSize: 16,
            fontWeight: '700',
        },
        maxAmount: {
            color: colors.primary,
            fontSize: 13,
            marginTop: 8,
        },

        infoNote: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            marginHorizontal: 16,
            marginTop: 24,
            padding: 14,
            backgroundColor: colors.surface,
            borderRadius: 12,
            gap: 8,
        },
        infoText: {
            flex: 1,
            color: colors.textDim,
            fontSize: 13,
            lineHeight: 18,
        },
    });
}
