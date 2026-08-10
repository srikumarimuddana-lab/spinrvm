import React, { useState, useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    Platform,
    TextInput,
    ActivityIndicator,
    KeyboardAvoidingView,
} from 'react-native';
import { showToast } from '../../hooks/useToast';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useDriverStore } from '../../store/driverStore';
import { useAuthStore } from '@shared/store/authStore';
import api, { getApiErrorMessage } from '@shared/api/client';
import * as WebBrowser from 'expo-web-browser';
import { useDriverMe, useUpdateDriverMe } from '@shared/hooks/queries';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';

function PayoutScreen() {
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
    // Hosted Stripe onboarding opens in the system browser, so track the brief
    // "opening…" state while we mint the AccountLink and hand off.
    const [stripeOnboarding, setStripeOnboarding] = useState(false);
    const [sendingT4A, setSendingT4A] = useState(false);
    const [sendingCSV, setSendingCSV] = useState(false);
    // gst_bn (CRA Business Number) is the canonical column on the driver row
    // served by useDriverMe and accepted by PUT /drivers/me — keep a local form
    // state for the input but seed it from the cached server value (also
    // re-seeds from the background refetch). NOTE: the column is gst_bn, not
    // gst_number — the latter is silently dropped by the backend schema.
    const { data: driverMeRaw } = useDriverMe();
    const driverMe = driverMeRaw as { gst_bn?: string; sin_last4?: string | null } | undefined;
    const updateDriverMe = useUpdateDriverMe();
    const [gstNumber, setGstNumber] = useState('');
    const [showGstForm, setShowGstForm] = useState(false);
    const [stripeAccountStatus, setStripeAccountStatus] = useState<string | null>(null);
    // Stripe's own KYC flag. Kept because it still gates PAYOUTS — Stripe will
    // not enable them without an identity on file — but it is NOT the T4A
    // signal: Stripe never gives the number back (`individual.id_number` is
    // write-only on Connect), so a slip cannot be filed from it.
    const [stripeIdOnFile, setStripeIdOnFile] = useState(false);
    // Our own copy, which is what T4A is actually filed from. Only ever the
    // last 4 — the full number is Vault-encrypted server-side and no endpoint
    // returns it.
    const sinOnFile = !!driverMe?.sin_last4;
    const [sinInput, setSinInput] = useState('');
    const [showSinForm, setShowSinForm] = useState(false);
    const [initialLoading, setInitialLoading] = useState(true);
    const [bonuses, setBonuses] = useState<{ id: string; amount: string; kind: string; description: string; created_at: string }[]>([]);

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
                loadBonuses(),
                // GST is sourced from useDriverMe — no manual fetch needed.
            ]);
        } catch (err) {
            // Errors are handled individually in each function
        } finally {
            setInitialLoading(false);
        }
    };

    const loadBonuses = async () => {
        try {
            const res = await api.get<{ bonuses?: typeof bonuses }>('/drivers/bonuses');
            setBonuses(res.data?.bonuses || []);
        } catch {
            setBonuses([]);
        }
    };

    const loadStripeStatus = async () => {
        try {
            const res = await api.get<{ stripe_account_onboarded?: boolean; stripe_id_number_provided?: boolean }>('/drivers/balance');
            setStripeAccountStatus(
                res.data.stripe_account_onboarded ? 'active' : 'not_onboarded'
            );
            setStripeIdOnFile(!!res.data.stripe_id_number_provided);
        } catch {
            setStripeAccountStatus('not_onboarded');
            setStripeIdOnFile(false);
        }
    };

    // Seed the GST input from the cached driver row whenever it changes.
    // The hook has its own cache + background refetch, so this replaces
    // the legacy `loadGstNumber()` round-trip entirely.
    useEffect(() => {
        if (driverMe) setGstNumber(driverMe.gst_bn || '');
    }, [driverMe]);

    useEffect(() => {
        if (error) {
            showToast('error', 'Error', error);
            clearError();
        }
    }, [error]);

    const handleStripeOnboarding = async () => {
        // Hosted onboarding (Option A): open Stripe's AccountLink in the system
        // browser. A bare WebView dead-ends at Stripe's popup-based KYC step
        // (the popup has no window.opener and the page hangs); the system
        // browser supports the popup + redirect + camera/identity steps, so
        // onboarding can actually complete. Stripe collects the government ID
        // and bank account here; the SIN is NOT among them if the driver
        // already gave us one, because the backend pre-fills
        // individual.id_number just before minting this link. The
        // backend return_url bounces back to spinr-driver://driver/payout.
        try {
            setStripeOnboarding(true);
            const res = await api.post<{ url?: string; mock?: boolean }>('/drivers/stripe-onboard');
            const url = res.data?.url;
            if (!url || res.data?.mock) {
                showToast('info', 'Unavailable', 'Payouts setup is not available yet. Please try again later.');
                return;
            }
            // Redirect target must match the backend _STRIPE_RETURN_DEEP_LINK
            // (backend/routes/drivers.py); openAuthSessionAsync auto-dismisses
            // the browser when Stripe's return_url bounces to this scheme.
            await WebBrowser.openAuthSessionAsync(url, 'spinr-driver://driver/payout');
            // Pull the driver's LIVE Stripe status on return — don't wait on the
            // account.updated webhook (it can be delayed, or never arrive if the
            // Connect webhook isn't configured). This mirrors the status
            // server-side AND tells us what to show the driver right now.
            try {
                const sync = await api.post<{ onboarded?: boolean; payouts_enabled?: boolean }>(
                    '/drivers/stripe-sync',
                );
                if (sync.data?.onboarded) {
                    showToast(
                        'success',
                        'Verification submitted',
                        sync.data.payouts_enabled
                            ? 'Stripe approved your account — payouts are enabled.'
                            : 'Details received. Stripe is reviewing them; this can take a few minutes.',
                    );
                } else {
                    showToast('info', 'Not finished', 'Stripe onboarding wasn’t completed. You can resume anytime.');
                }
            } catch {
                // Sync failed (e.g. transient) — fall through; loadData still
                // reflects whatever the webhook may have already mirrored.
            }
            // Refresh balance / bank / status so the screen reflects the new state.
            await loadData();
        } catch (err: any) {
            showToast('error', 'Error', getApiErrorMessage(err, 'Could not start verification. Please try again.'));
        } finally {
            setStripeOnboarding(false);
        }
    };

    const handleSaveGst = async () => {
        // Validate GST/BN format: 9 digits or 15 chars (9-digit BN + RT0001)
        const cleaned = gstNumber.replace(/\s/g, '');
        if (cleaned && !/^\d{9}(RT\d{4})?$/.test(cleaned)) {
            showToast('warning', 'Invalid Format', 'Enter your 9-digit Business Number (BN) or full GST number (e.g., 123456789RT0001)');
            return;
        }

        try {
            // Canonical column is gst_bn; also set gst_registered so the T4A
            // export and admin KYC view stay in sync. (gst_bn:null is dropped by
            // the backend's exclude_none, so this is an add/update, not a clear.)
            await updateDriverMe.mutateAsync({ gst_bn: cleaned || null, gst_registered: !!cleaned });
            setShowGstForm(false);
            showToast('success', 'Saved', 'GST/BN number updated successfully');
        } catch (err: any) {
            showToast('error', 'Save Failed', getApiErrorMessage(err, 'Could not save your GST number. Please try again.'));
        }
    };

    const handleSaveSin = async () => {
        const cleaned = sinInput.replace(/\D/g, '');
        // Length only. The checksum and the leading-digit rule live on the
        // server (backend/utils/sin.py) and are returned as a clean 422 —
        // duplicating them here would drift, and the server is authoritative.
        if (cleaned.length !== 9) {
            showToast('warning', 'Invalid Format', 'Your SIN is 9 digits.');
            return;
        }
        try {
            await updateDriverMe.mutateAsync({ sin: cleaned });
            // Drop it from component state immediately. The server returns
            // only sin_last4, so nothing re-seeds this field and the full
            // number should not linger in memory behind a closed form.
            setSinInput('');
            setShowSinForm(false);
            showToast('success', 'Saved', 'Your SIN is stored securely for your T4A.');
        } catch (err: any) {
            setSinInput('');
            showToast('error', 'Save Failed', getApiErrorMessage(err, 'Could not save your SIN. Please try again.'));
        }
    };

    const handleRequestPayout = async () => {
        const amount = parseFloat(payoutAmount);
        if (isNaN(amount) || amount <= 0) {
            showToast('error', 'Invalid Amount', 'Please enter a valid amount');
            return;
        }
        if (amount < 10) {
            showToast('error', 'Amount Too Low', 'Minimum payout amount is $10');
            return;
        }
        if (driverBalance && amount > parseFloat(driverBalance.payable_balance)) {
            showToast('error', 'Insufficient Balance', `Available balance: $${parseFloat(driverBalance.payable_balance).toFixed(2)}`);
            return;
        }

        const result = await requestPayout(amount);
        if (result.success) {
            setPayoutAmount('');
            showToast('success', 'Payout Requested', 'Funds will arrive in 2–3 business days.');
        }
    };

    const handleEmailT4A = async () => {
        setSendingT4A(true);
        try {
            // CRA T4A is generated per tax year — default to the most recently
            // completed year so drivers don't get an in-progress year's partial total.
            const year = new Date().getFullYear() - 1;
            // Tax documents are delivered by email only (no in-app download); the
            // backend renders the PDF and emails it as an attachment.
            const res = await api.post<{ message?: string }>(`/drivers/t4a/${year}/email`);
            showToast('success', 'Check Your Email', res.data?.message || `Your T4A summary for ${year} is on its way.`);
        } catch (err: any) {
            showToast('error', 'Send Failed', getApiErrorMessage(err, 'Could not send your T4A. Please try again.'));
        } finally {
            setSendingT4A(false);
        }
    };

    const handleEmailCSV = async () => {
        setSendingCSV(true);
        try {
            const year = new Date().getFullYear();
            const res = await api.post<{ message?: string }>(`/drivers/earnings/export/email?year=${year}`);
            showToast('success', 'Check Your Email', res.data?.message || `Your earnings export for ${year} is on its way.`);
        } catch (err: any) {
            showToast('error', 'Send Failed', getApiErrorMessage(err, 'Could not send your earnings export. Please try again.'));
        } finally {
            setSendingCSV(false);
        }
    };

    const formatCurrency = (amount: string | number) => `$${parseFloat(String(amount)).toFixed(2)}`;

    const nextPayoutLabel = useMemo(() => {
        const now = new Date();
        const dayOfWeek = now.getDay();
        const daysUntilFriday = (5 - dayOfWeek + 7) % 7 || 7;
        if (daysUntilFriday === 1) return 'Tomorrow (Friday)';
        if (daysUntilFriday === 7 && dayOfWeek === 5) return 'Today (Friday)';
        const target = new Date(now);
        target.setDate(target.getDate() + daysUntilFriday);
        return target.toLocaleDateString('en-CA', { weekday: 'long', month: 'short', day: 'numeric' });
    }, []);

    const isStripeReady = stripeAccountStatus === 'active' || hasBankAccount;
    // CRA hard requirement: rideshare drivers must be GST/HST-registered before
    // any payout (the backend rejects payouts without a valid BN). Mirror the
    // server-side ^\d{9}(RT\d{4})?$ check so we gate the UI before the request.
    const gstOnFile = /^\d{9}(RT\d{4})?$/.test((gstNumber || '').replace(/\s/g, '').toUpperCase());

    // Guided-setup checklist. Every payout prerequisite lives here as a single
    // ordered list so the driver sees exactly what's done vs. what still needs a
    // tap — instead of the same requirement repeated across scattered cards.
    //
    // ORDER IS LOAD-BEARING: the SIN step comes FIRST, before Stripe onboarding.
    // The backend hands our stored SIN to Stripe just before minting the
    // onboarding link, which takes id_number out of what Stripe's form asks
    // for. Put Stripe first and there is nothing to hand over yet, so Stripe
    // asks — and then our form asks again. Being asked twice for a SIN is
    // exactly what this ordering exists to prevent.
    const allReady = isStripeReady && sinOnFile && gstOnFile;
    // The backend hard-gates /stripe-onboard (422) until a SIN is on file —
    // either our encrypted copy or, for legacy drivers, the one Stripe
    // already holds (stripeIdOnFile). Mirror that gate here so the driver
    // sees a locked step with a reason instead of an error toast.
    const sinSatisfiesStripeGate = sinOnFile || stripeIdOnFile;
    const setupSteps: {
        key: string;
        icon: keyof typeof Ionicons.glyphMap;
        title: string;
        subtitle: string;
        done: boolean;
        locked: boolean;
        onPress: () => void;
    }[] = [
        {
            key: 'sin',
            icon: 'shield-checkmark',
            title: 'Add your SIN for tax slips',
            // Copy corrected: this used to read "Added securely through Stripe
            // — we never see or store it". Stripe does collect one for its own
            // identity checks, but it never returns it, so Spinr could not file
            // a T4A from it. We now collect and encrypt our own copy, and
            // saying otherwise to a driver would be untrue.
            // Says where it goes, because it goes to two places. Telling a
            // driver it is "only" for our T4A while also sending it to Stripe
            // would be the same kind of untruth as the line this replaced.
            subtitle: sinOnFile
                // Changes are admin-approved only (the server rejects a second
                // write), so say where to go instead of offering a dead end.
                ? `On file · ••••${driverMe?.sin_last4} · Contact support to change it`
                : 'Encrypted for your T4A, and passed to Stripe so you are only asked once',
            done: sinOnFile,
            // Not gated on Stripe: this is our own record for tax filing, and a
            // driver can supply it before or after payouts onboarding.
            locked: false,
            onPress: () => setShowSinForm(true),
        },
        {
            key: 'stripe',
            icon: 'card',
            title: 'Connect payout account',
            subtitle: isStripeReady
                ? 'Connected with Stripe'
                : sinSatisfiesStripeGate
                    ? 'Link your bank and verify your identity with Stripe'
                    : 'Add your SIN first — it unlocks this step',
            done: isStripeReady,
            // Mirrors the backend 422 gate on /stripe-onboard: no SIN, no link.
            locked: !isStripeReady && !sinSatisfiesStripeGate,
            onPress: handleStripeOnboarding,
        },
        {
            key: 'gst',
            icon: 'document-text',
            title: 'Add GST/HST number',
            subtitle: gstOnFile
                ? `Registered · ${gstNumber}`
                : 'CRA Business Number — rideshare drivers register from their first fare',
            done: gstOnFile,
            locked: false,
            onPress: () => setShowGstForm(true),
        },
    ];

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
                    <TouchableOpacity
                        onPress={() => {
                            // After Stripe onboarding returns via the
                            // spinr-driver://driver/payout deep link, expo-router
                            // rebuilds the stack with only this route, so there's
                            // nothing to pop — router.back() becomes a no-op and the
                            // screen looks "stuck". Fall back to the driver home so
                            // the back button always has somewhere to go.
                            if (router.canGoBack()) router.back();
                            else router.replace('/driver/' as any);
                        }}
                        style={styles.backBtn}
                    >
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Payouts</Text>
                    <TouchableOpacity onPress={() => router.push('/driver/payout-history' as any)}>
                        <Ionicons name="time" size={22} color={colors.text} />
                    </TouchableOpacity>
                </View>
            </LinearGradient>

            <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
            <ScrollView contentContainerStyle={{ paddingBottom: Math.max(insets.bottom, 16) + 24 }} keyboardShouldPersistTaps="handled"
                    automaticallyAdjustKeyboardInsets={true} showsVerticalScrollIndicator={false}>
                {/* Balance Card */}
                <View style={styles.balanceCard}>
                    <Text style={styles.balanceLabel}>AVAILABLE BALANCE</Text>
                    <Text style={styles.balanceAmount}>
                        {driverBalance ? formatCurrency(driverBalance.payable_balance) : '$0.00'}
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

                {/* Bonuses & Rewards — quest/referral payable earnings, included in balance */}
                {bonuses.length > 0 && (
                    <View style={styles.section}>
                        <Text style={styles.sectionTitle}>Bonuses & Rewards</Text>
                        {bonuses.map((b) => (
                            <View
                                key={b.id}
                                style={{
                                    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
                                    paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: colors.border,
                                }}
                            >
                                <View style={{ flex: 1, paddingRight: 12 }}>
                                    <Text style={{ fontSize: 14, fontWeight: '600', color: colors.text }} numberOfLines={1}>
                                        {b.description || (b.kind === 'quest' ? 'Quest reward' : 'Bonus')}
                                    </Text>
                                    <Text style={{ fontSize: 12, color: colors.textDim, marginTop: 2 }}>
                                        {b.kind === 'referral' ? 'Referral bonus' : 'Quest bonus'} · {new Date(b.created_at).toLocaleDateString()}
                                    </Text>
                                </View>
                                <Text style={{ fontSize: 15, fontWeight: '800', color: colors.success }}>
                                    +{formatCurrency(b.amount)}
                                </Text>
                            </View>
                        ))}
                        <Text style={{ fontSize: 12, color: colors.textDim, marginTop: 10 }}>
                            Bonuses are included in your available balance and paid out with your earnings.
                        </Text>
                    </View>
                )}

                {/* Next Payout Schedule — only once fully set up and able to cash out */}
                {allReady && (
                    <View style={styles.payoutScheduleCard}>
                        <Ionicons name="calendar-outline" size={20} color={colors.primary} />
                        <View style={{ flex: 1, marginLeft: 12 }}>
                            <Text style={styles.payoutScheduleLabel}>Next payout</Text>
                            <Text style={styles.payoutScheduleDate}>{nextPayoutLabel}</Text>
                        </View>
                        <Text style={styles.payoutScheduleNote}>Weekly</Text>
                    </View>
                )}

                {/* Setup checklist — one guided list of every payout prerequisite */}
                {!allReady && (
                    <View style={styles.section}>
                        <Text style={styles.sectionTitle}>Set up payouts</Text>
                        <Text style={styles.sectionSubtitle}>
                            Complete these steps to cash out your balance.
                        </Text>
                        <View style={styles.checklistCard}>
                            {setupSteps.map((step, i) => {
                                const state = step.done ? 'done' : step.locked ? 'locked' : 'todo';
                                const busy = step.key !== 'gst' && stripeOnboarding;
                                const inner = (
                                    <>
                                        <View
                                            style={[
                                                styles.stepIcon,
                                                state === 'done' && styles.stepIconDone,
                                                state === 'todo' && styles.stepIconTodo,
                                            ]}
                                        >
                                            <Ionicons
                                                name={
                                                    state === 'done'
                                                        ? 'checkmark'
                                                        : state === 'locked'
                                                            ? 'lock-closed'
                                                            : step.icon
                                                }
                                                size={16}
                                                color={
                                                    state === 'done'
                                                        ? '#fff'
                                                        : state === 'todo'
                                                            ? colors.primary
                                                            : colors.textDim
                                                }
                                            />
                                        </View>
                                        <View style={{ flex: 1 }}>
                                            <Text
                                                style={[
                                                    styles.stepTitle,
                                                    state !== 'todo' && styles.stepTitleMuted,
                                                ]}
                                            >
                                                {step.title}
                                            </Text>
                                            <Text style={styles.stepSubtitle}>{step.subtitle}</Text>
                                        </View>
                                        {busy ? (
                                            <ActivityIndicator size="small" color={colors.primary} />
                                        ) : state === 'todo' ? (
                                            <Ionicons name="chevron-forward" size={18} color={colors.primary} />
                                        ) : state === 'done' ? (
                                            <Ionicons name="checkmark-circle" size={20} color={colors.success} />
                                        ) : null}
                                    </>
                                );
                                const rowStyle = [styles.stepRow, i > 0 && styles.stepRowBorder];
                                return state === 'todo' ? (
                                    <TouchableOpacity
                                        key={step.key}
                                        style={rowStyle}
                                        onPress={step.onPress}
                                        disabled={busy}
                                        activeOpacity={0.7}
                                    >
                                        {inner}
                                    </TouchableOpacity>
                                ) : (
                                    <View key={step.key} style={rowStyle}>
                                        {inner}
                                    </View>
                                );
                            })}
                        </View>
                    </View>
                )}

                {/* Payouts-ready confirmation */}
                {allReady && (
                    <View style={styles.section}>
                        <View style={styles.readyCard}>
                            <Ionicons name="checkmark-circle" size={22} color={colors.success} />
                            <Text style={styles.readyText}>
                                You're all set — your balance is ready to cash out.
                            </Text>
                        </View>
                    </View>
                )}

                {/* SIN form — opened from the setup checklist. Spinr stores its
                    own encrypted copy because Stripe never returns the one it
                    holds, and a T4A cannot be filed without the number. */}
                {showSinForm && (
                    <View style={styles.section}>
                        <Text style={styles.sectionTitle}>Social Insurance Number</Text>
                        <View style={styles.gstForm}>
                            <Text style={styles.inputLabel}>SIN</Text>
                            <Text style={styles.gstHelpText}>
                                {/* Once on file the SIN is locked server-side (403 on a second
                                    write; changes go through support -> admin), and the checklist
                                    row stops opening this form — so no "replace it" copy here. */}
                                Enter your 9-digit Social Insurance Number.
                            </Text>
                            <TextInput
                                style={styles.textInput}
                                placeholder="•••••••••"
                                placeholderTextColor={colors.textDim}
                                value={sinInput}
                                onChangeText={setSinInput}
                                keyboardType="number-pad"
                                maxLength={11}
                                secureTextEntry
                                autoComplete="off"
                                textContentType="none"
                            />
                            <Text style={styles.gstNote}>
                                Stored encrypted and used only to prepare your year-end T4A slip. It is
                                never shown back to you or to Spinr staff — only the last 4 digits are
                                ever displayed.
                            </Text>
                            <View style={styles.gstFormButtons}>
                                <TouchableOpacity
                                    style={styles.cancelBtn}
                                    onPress={() => {
                                        setSinInput('');
                                        setShowSinForm(false);
                                    }}
                                >
                                    <Text style={styles.cancelBtnText}>Cancel</Text>
                                </TouchableOpacity>
                                <TouchableOpacity
                                    style={styles.saveBtn}
                                    onPress={handleSaveSin}
                                    disabled={updateDriverMe.isPending}
                                >
                                    {updateDriverMe.isPending ? (
                                        <ActivityIndicator size="small" color="#fff" />
                                    ) : (
                                        <Text style={styles.saveBtnText}>Save</Text>
                                    )}
                                </TouchableOpacity>
                            </View>
                        </View>
                    </View>
                )}

                {/* GST/HST number form — opened from the setup checklist */}
                {showGstForm && (
                    <View style={styles.section}>
                        <Text style={styles.sectionTitle}>GST/HST number</Text>
                        <View style={styles.gstForm}>
                            <Text style={styles.inputLabel}>Business Number (BN)</Text>
                            <Text style={styles.gstHelpText}>
                                Enter your 9-digit CRA Business Number (BN) or full program account (e.g., 123456789RT0001).
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
                                Required to receive payouts. As a rideshare driver you must register for
                                GST/HST with the CRA from your first fare — the $30,000 small-supplier
                                threshold does not apply to ride-sharing.
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
                                    disabled={updateDriverMe.isPending}
                                >
                                    {updateDriverMe.isPending ? (
                                        <ActivityIndicator size="small" color="#fff" />
                                    ) : (
                                        <Text style={styles.saveBtnText}>Save</Text>
                                    )}
                                </TouchableOpacity>
                            </View>
                        </View>
                    </View>
                )}

                {/* Request Payout */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Request payout</Text>
                    {!allReady ? (
                        <View style={styles.infoRow}>
                            <Ionicons name="lock-closed" size={18} color={colors.textDim} />
                            <Text style={styles.infoRowText}>
                                Finish the setup steps above to request a payout.
                            </Text>
                        </View>
                    ) : !driverBalance || parseFloat(driverBalance.payable_balance) <= 0 ? (
                        <View style={styles.infoRow}>
                            <Ionicons name="wallet-outline" size={18} color={colors.textDim} />
                            <Text style={styles.infoRowText}>
                                No balance to pay out yet. Completed rides will show up here.
                            </Text>
                        </View>
                    ) : (
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
                                onPress={() => setPayoutAmount(driverBalance.payable_balance)}
                            >
                                <Text style={styles.maxAmount}>
                                    Available: {formatCurrency(driverBalance.payable_balance)} · Min $10.00
                                </Text>
                            </TouchableOpacity>
                        </View>
                    )}
                </View>

                {/* Tax Documents */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Tax Documents</Text>
                    <View style={styles.payoutCard}>
                        <TouchableOpacity
                            style={[styles.docRow, sendingT4A && styles.docRowDisabled]}
                            onPress={handleEmailT4A}
                            disabled={sendingT4A}
                        >
                            <View style={styles.docRowLeft}>
                                <Ionicons name="document-text-outline" size={22} color={colors.primary} />
                                <View style={{ marginLeft: 12 }}>
                                    <Text style={styles.docRowTitle}>Email T4A</Text>
                                    <Text style={styles.docRowSub}>Annual earnings slip sent to your email</Text>
                                </View>
                            </View>
                            {sendingT4A ? (
                                <ActivityIndicator size="small" color={colors.primary} />
                            ) : (
                                <Ionicons name="mail-outline" size={20} color={colors.primary} />
                            )}
                        </TouchableOpacity>

                        <View style={styles.docDivider} />

                        <TouchableOpacity
                            style={[styles.docRow, sendingCSV && styles.docRowDisabled]}
                            onPress={handleEmailCSV}
                            disabled={sendingCSV}
                        >
                            <View style={styles.docRowLeft}>
                                <Ionicons name="grid-outline" size={22} color={colors.primary} />
                                <View style={{ marginLeft: 12 }}>
                                    <Text style={styles.docRowTitle}>Email Earnings CSV</Text>
                                    <Text style={styles.docRowSub}>Trip-by-trip earnings export sent to your email</Text>
                                </View>
                            </View>
                            {sendingCSV ? (
                                <ActivityIndicator size="small" color={colors.primary} />
                            ) : (
                                <Ionicons name="mail-outline" size={20} color={colors.primary} />
                            )}
                        </TouchableOpacity>
                    </View>
                </View>

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

        payoutScheduleCard: {
            flexDirection: 'row',
            alignItems: 'center',
            marginHorizontal: 16,
            marginTop: 12,
            padding: 14,
            backgroundColor: colors.surface,
            borderRadius: 12,
            borderWidth: 1,
            borderColor: colors.border,
        },
        payoutScheduleLabel: {
            fontSize: 12,
            color: colors.textDim,
        },
        payoutScheduleDate: {
            fontSize: 15,
            fontWeight: '700',
            color: colors.text,
            marginTop: 1,
        },
        payoutScheduleNote: {
            fontSize: 12,
            fontWeight: '600',
            color: colors.primary,
            backgroundColor: `${colors.primary}1A`,
            paddingHorizontal: 10,
            paddingVertical: 4,
            borderRadius: 8,
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
        sectionSubtitle: {
            color: colors.textDim,
            fontSize: 13,
            lineHeight: 18,
            marginTop: -6,
            marginBottom: 12,
        },

        // Setup checklist — actionable rows (accent icon + chevron) read clearly
        // apart from completed/locked rows (muted, flat, no chevron).
        checklistCard: {
            backgroundColor: colors.surface,
            borderRadius: 16,
            paddingHorizontal: 16,
        },
        stepRow: {
            flexDirection: 'row',
            alignItems: 'center',
            paddingVertical: 16,
            gap: 12,
        },
        stepRowBorder: {
            borderTopWidth: 1,
            borderTopColor: colors.border,
        },
        stepIcon: {
            width: 32,
            height: 32,
            borderRadius: 16,
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        stepIconTodo: {
            backgroundColor: `${colors.primary}1A`,
        },
        stepIconDone: {
            backgroundColor: colors.success,
        },
        stepTitle: {
            color: colors.text,
            fontSize: 15,
            fontWeight: '600',
        },
        stepTitleMuted: {
            color: colors.textDim,
        },
        stepSubtitle: {
            color: colors.textDim,
            fontSize: 12,
            lineHeight: 17,
            marginTop: 2,
        },

        // "All set" confirmation
        readyCard: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            backgroundColor: `${colors.success}14`,
            borderRadius: 14,
            padding: 14,
        },
        readyText: {
            flex: 1,
            color: colors.text,
            fontSize: 14,
            fontWeight: '500',
        },

        // Muted, flat, non-actionable info row (clearly NOT a button).
        infoRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            paddingVertical: 12,
        },
        infoRowText: {
            flex: 1,
            color: colors.textDim,
            fontSize: 13,
            lineHeight: 18,
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

        docRow: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingVertical: 14,
        },
        docRowDisabled: { opacity: 0.6 },
        docRowLeft: {
            flexDirection: 'row',
            alignItems: 'center',
            flex: 1,
        },
        docRowTitle: {
            color: colors.text,
            fontSize: 15,
            fontWeight: '600',
        },
        docRowSub: {
            color: colors.textDim,
            fontSize: 12,
            marginTop: 2,
        },
        docDivider: {
            height: 1,
            backgroundColor: colors.border,
        },
    });
}

export default function PayoutScreenWithBoundary() {
  return (
    <ErrorBoundary>
      <PayoutScreen />
    </ErrorBoundary>
  );
}
