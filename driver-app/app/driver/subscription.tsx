import React, { useEffect, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView,
  ActivityIndicator, Alert,
} from 'react-native';
import * as ExpoLinking from 'expo-linking';
import * as WebBrowser from 'expo-web-browser';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import api, { getApiErrorMessage } from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { showToast } from '../../hooks/useToast';
import { ScreenHeader } from '../../components/ScreenHeader';

interface Plan {
  id: string;
  name: string;
  price: number;
  duration_days: number;
  rides_per_day: number;
  description: string;
  features: string[];
}

interface Payment {
  id: string;
  plan_name: string;
  amount: string;
  subtotal: string;
  gst_amount: string;
  pst_amount: string;
  hst_amount: string;
  province: string;
  currency: string;
  billing_reason: string | null;
  created_at: string;
}

const PAGE_SIZE = 10;

export default function SubscriptionScreen() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [currentSub, setCurrentSub] = useState<any>(null);
  const [freeMode, setFreeMode] = useState(false);
  const [freeMessage, setFreeMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [subscribing, setSubscribing] = useState<string | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [paymentsTotal, setPaymentsTotal] = useState(0);
  const [paymentsOffset, setPaymentsOffset] = useState(0);
  const [paymentsLoadingMore, setPaymentsLoadingMore] = useState(false);
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Declared before the `useEffect` below (react-hooks/immutability /
  // React Compiler flags referencing a function before its source-order
  // declaration) — same expression, same effect timing, no behavior change.
  const loadData = async () => {
    setLoading(true);
    try {
      const [plansRes, subRes, paymentsRes] = await Promise.all([
        api.get<{ plans?: Plan[]; free_mode?: boolean; message?: string } | Plan[]>('/drivers/subscription/plans'),
        api.get<any>('/drivers/subscription/current'),
        api.get<{ payments: Payment[]; total: number }>(
          `/drivers/subscription/payments?limit=${PAGE_SIZE}&offset=0`
        ).catch(() => ({ data: { payments: [], total: 0 } })),
      ]);
      const data = plansRes.data;
      if (data && typeof data === 'object' && 'free_mode' in data) {
        const d = data as { plans?: Plan[]; free_mode?: boolean; message?: string };
        setPlans(d.plans || []);
        setFreeMode(d.free_mode || false);
        setFreeMessage(d.message || '');
      } else {
        setPlans(Array.isArray(data) ? data : []);
        setFreeMode(false);
      }
      setCurrentSub(subRes.data);
      const firstPage = paymentsRes.data?.payments || [];
      const total = paymentsRes.data?.total || 0;
      setPayments(firstPage);
      setPaymentsTotal(total);
      setPaymentsOffset(firstPage.length);
    } catch (e) { console.log('Sub load error:', e); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadData(); }, []);

  // The Stripe-checkout deep-link return (spinr-driver://subscription/success)
  // is owned by the dedicated app/subscription/success.tsx landing screen,
  // which verifies the session and routes back here with fresh data. The
  // in-app-browser happy path is handled inline in doSubscribe() via
  // WebBrowser.openAuthSessionAsync. A `Linking` listener here would be a third,
  // overlapping path that double-verified and double-toasted on the Android
  // Custom-Tab case where the OS dispatches the deep link to the app, so it was
  // removed.

  const loadMorePayments = async () => {
    if (paymentsLoadingMore || paymentsOffset >= paymentsTotal) return;
    setPaymentsLoadingMore(true);
    try {
      const res = await api.get<{ payments: Payment[]; total: number }>(
        `/drivers/subscription/payments?limit=${PAGE_SIZE}&offset=${paymentsOffset}`
      );
      const next = res.data?.payments || [];
      setPayments(prev => [...prev, ...next]);
      setPaymentsTotal(res.data?.total || paymentsTotal);
      setPaymentsOffset(prev => prev + next.length);
    } catch { /* silent — user can scroll again */ }
    finally { setPaymentsLoadingMore(false); }
  };

  const handleSubscribe = async (plan: Plan) => {
    if (currentSub?.has_subscription) {
      Alert.alert(
        'Switch Plan?',
        `You currently have "${currentSub.subscription.plan_name}". Switch to "${plan.name}" for $${plan.price.toFixed(2)}?${quotaDisclaimer(plan)}`,
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Switch', style: 'destructive', onPress: () => doSubscribe(plan) },
        ]
      );
    } else {
      Alert.alert(
        'Subscribe',
        `Subscribe to "${plan.name}" for $${plan.price.toFixed(2)}/${getDurationLabel(plan.duration_days).toLowerCase()}?${quotaDisclaimer(plan)}`,
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Subscribe', style: 'destructive', onPress: () => doSubscribe(plan) },
        ]
      );
    }
  };

  const doSubscribe = async (plan: Plan) => {
    setSubscribing(plan.id);
    try {
      // createURL generates the correct scheme for the current environment:
      // 'spinr-driver://subscription/success' in production builds,
      // 'exp://host:port/--/subscription/success' in Expo Go.
      const successUrl = ExpoLinking.createURL('subscription/success');
      const res = await api.post<{ checkout_url?: string; session_id?: string }>(
        '/drivers/subscription/subscribe',
        { plan_id: plan.id, success_url: successUrl },
      );

      if (res.data?.checkout_url) {
        // openAuthSessionAsync opens an in-app browser (SFSafariViewController on
        // iOS, Chrome Custom Tab on Android) that properly intercepts the redirect
        // back to successUrl, unlike Linking.openURL which opens the external
        // browser where custom-scheme redirects are unreliable.
        const result = await WebBrowser.openAuthSessionAsync(
          res.data.checkout_url,
          successUrl,
        );

        if (result.type === 'success') {
          const sessionId = result.url?.match(/session_id=([^&]+)/)?.[1];
          if (sessionId) {
            setLoading(true);
            try {
              const verifyRes = await api.get<{ status?: string }>(
                `/drivers/subscription/verify-session?session_id=${sessionId}`,
              );
              if (verifyRes.data?.status === 'active') {
                showToast('success', 'Subscribed!', `You're now on the ${plan.name} plan. Go online and start earning!`);
              } else {
                showToast('info', 'Processing...', 'Your payment is being confirmed. This may take a moment.');
              }
            } catch {
              showToast('info', 'Processing...', 'Your payment is being confirmed. This may take a moment.');
            }
            loadData();
          }
        }
        // result.type === 'cancel' means the driver closed the browser — no action needed.
      } else {
        // Dev/test mode — subscription activated immediately (no Stripe checkout)
        showToast('success', 'Subscribed!', `You're now on the ${plan.name} plan. Go online and start earning!`);
        loadData();
      }
    } catch (e: any) {
      showToast('error', 'Error', getApiErrorMessage(e, 'Could not subscribe. Please try again.'));
    } finally { setSubscribing(null); }
  };

  const handleCancel = () => {
    Alert.alert(
      'Cancel Subscription',
      'Are you sure? You can still drive until your current plan expires.',
      [
        { text: 'Keep Plan', style: 'cancel' },
        {
          text: 'Cancel Plan', style: 'destructive',
          onPress: async () => {
            try {
              await api.post('/drivers/subscription/cancel');
              showToast('info', 'Cancelled', 'Your subscription has been cancelled.');
              loadData();
            } catch (e: any) {
              showToast('error', 'Error', getApiErrorMessage(e, 'Could not cancel your subscription. Please try again.'));
            }
          },
        },
      ]
    );
  };

  const [resendingId, setResendingId] = useState<string | null>(null);

  const resendInvoice = async (paymentId: string) => {
    setResendingId(paymentId);
    try {
      await api.post(`/drivers/subscription/payments/${paymentId}/resend-invoice`);
      showToast('success', 'Invoice Sent', 'Invoice emailed to your address on file.');
    } catch (e: any) {
      showToast('error', 'Error', getApiErrorMessage(e, 'Could not send the invoice. Please try again.'));
    } finally {
      setResendingId(null);
    }
  };

  // Passes are named by their length in days — never "monthly"/"weekly". A
  // 1-day pass is sold as 24 hours of validity.
  const getDurationLabel = (days: number) => {
    if (days === 1) return '1 day';
    return `${days} days`;
  };

  // Daily allowance reset is always < 24h away → show hours (or minutes when close).
  const formatResetCountdown = (hours?: number): string | null => {
    if (typeof hours !== 'number') return null;
    if (hours < 1) {
      const mins = Math.max(1, Math.round(hours * 60));
      return `${mins}m`;
    }
    return `${Math.round(hours)}h`;
  };

  // Pass expiry can be days away → roll up to days past 24h.
  const formatExpiryCountdown = (hours?: number): string | null => {
    if (typeof hours !== 'number') return null;
    if (hours < 1) return 'under 1h';
    if (hours < 24) return `${Math.round(hours)}h`;
    const days = Math.round(hours / 24);
    return `${days} day${days === 1 ? '' : 's'}`;
  };

  // Shown before subscribing so the driver understands how the allowance is
  // counted. A 1-day pass covers its rides across 24 hours (expires by the
  // hour); every longer pass counts per CALENDAR DAY (resets at midnight, no
  // roll-over) — not the whole period's rides up front.
  const quotaDisclaimer = (plan: Plan): string => {
    if (plan.rides_per_day === -1) return '';
    // Only an exact 1-day pass is the 24h/by-the-hour case; the backend treats
    // any other duration (including a misconfigured 0) as a calendar-day pass.
    if (plan.duration_days === 1) {
      return (
        `\n\nHeads up: this 1-day pass covers ${plan.rides_per_day} rides over the ` +
        `24 hours from purchase — it expires by the hour, not at midnight.`
      );
    }
    const label = getDurationLabel(plan.duration_days);
    return (
      `\n\nHeads up: this ${label} pass gives you ${plan.rides_per_day} rides per ` +
      `calendar day — your allowance resets at midnight and unused rides don't ` +
      `carry over to the next day.`
    );
  };

  const formatDate = (d: string) => {
    try { return new Date(d).toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: 'numeric' }); }
    catch { return d; }
  };

  if (loading) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <ScreenHeader title="Spinr Pass" />
      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 40 }]}
        showsVerticalScrollIndicator={false}
        onMomentumScrollEnd={({ nativeEvent: { layoutMeasurement, contentOffset, contentSize } }) => {
          if (layoutMeasurement.height + contentOffset.y >= contentSize.height - 300) {
            loadMorePayments();
          }
        }}
        scrollEventThrottle={400}
      >

        {/* Current Subscription */}
        {currentSub?.has_subscription && (
          <View style={styles.currentCard}>
            <View style={styles.currentBadge}>
              <Ionicons name="checkmark-circle" size={16} color="#FFF" />
              <Text style={styles.currentBadgeText}>ACTIVE PLAN</Text>
            </View>
            <Text style={styles.currentPlan}>{currentSub.subscription.plan_name}</Text>
            <Text style={styles.currentPrice}>${currentSub.subscription.price?.toFixed(2)}</Text>

            <View style={styles.currentStats}>
              <View style={styles.currentStat}>
                <Text style={styles.statValue}>
                  {currentSub.rides_remaining === 'unlimited' ? '∞' : currentSub.rides_remaining}
                </Text>
                <Text style={styles.statLabel}>{currentSub.hourly_pass ? 'Rides left' : 'Rides left today'}</Text>
              </View>
              <View style={styles.statDivider} />
              <View style={styles.currentStat}>
                <Text style={styles.statValue}>{currentSub.today_rides}</Text>
                <Text style={styles.statLabel}>{currentSub.hourly_pass ? 'Rides used' : 'Rides today'}</Text>
              </View>
              <View style={styles.statDivider} />
              <View style={styles.currentStat}>
                <Text style={styles.statValue}>{formatDate(currentSub.subscription.expires_at)}</Text>
                <Text style={styles.statLabel}>Expires</Text>
              </View>
            </View>

            {/* Hours-left countdowns: daily allowance reset + pass expiry */}
            {(formatResetCountdown(currentSub.hours_until_reset) || formatExpiryCountdown(currentSub.hours_until_expiry)) && (
              <View style={styles.countdownRow}>
                {/* A 1-day pass never refills mid-pass — its "reset" is the pass
                    end, already shown by "Pass ends in", so skip the duplicate. */}
                {!currentSub.hourly_pass && currentSub.rides_remaining !== 'unlimited' && formatResetCountdown(currentSub.hours_until_reset) && (
                  <View style={styles.countdownItem}>
                    <Ionicons name="refresh" size={14} color="rgba(255,255,255,0.85)" />
                    <Text style={styles.countdownText}>
                      Rides reset in {formatResetCountdown(currentSub.hours_until_reset)}
                    </Text>
                  </View>
                )}
                {formatExpiryCountdown(currentSub.hours_until_expiry) && (
                  <View style={styles.countdownItem}>
                    <Ionicons name="time-outline" size={14} color="rgba(255,255,255,0.85)" />
                    <Text style={styles.countdownText}>
                      Pass ends in {formatExpiryCountdown(currentSub.hours_until_expiry)}
                    </Text>
                  </View>
                )}
              </View>
            )}

            <TouchableOpacity style={styles.cancelLink} onPress={handleCancel}>
              <Text style={styles.cancelLinkText}>Cancel subscription</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* Plans */}
        <Text style={styles.sectionTitle}>
          {currentSub?.has_subscription ? 'Switch Plan' : 'Choose a Plan'}
        </Text>
        <Text style={styles.sectionSubtitle}>
          0% commission — keep 100% of your fares. Pay a flat subscription fee.
        </Text>

        {plans.map((plan) => {
          const isCurrentPlan = currentSub?.subscription?.plan_id === plan.id;

          return (
            <View key={plan.id} style={[styles.planCard, isCurrentPlan && styles.planCardActive]}>
              {isCurrentPlan && (
                <View style={styles.currentTagRow}>
                  <View style={styles.currentTag}>
                    <Text style={styles.currentTagText}>CURRENT</Text>
                  </View>
                </View>
              )}

              <View style={styles.planHeader}>
                <View>
                  <Text style={styles.planName}>{plan.name}</Text>
                  {plan.description ? <Text style={styles.planDesc}>{plan.description}</Text> : null}
                </View>
                <View style={styles.priceWrap}>
                  <Text style={styles.planPrice}>${plan.price.toFixed(2)}</Text>
                  <Text style={styles.planDuration}>/{getDurationLabel(plan.duration_days).toLowerCase()}</Text>
                </View>
              </View>

              <View style={styles.planDetails}>
                <View style={styles.planDetail}>
                  <Ionicons name={plan.rides_per_day === -1 ? 'infinite' : 'car'} size={18} color={colors.primary} />
                  <Text style={styles.planDetailText}>
                    {plan.rides_per_day === -1 ? 'Unlimited rides per day' : `${plan.rides_per_day} rides per day`}
                  </Text>
                </View>
                <View style={styles.planDetail}>
                  <Ionicons name="cash-outline" size={18} color="#10B981" />
                  <Text style={styles.planDetailText}>0% commission — keep all fares</Text>
                </View>
                {(plan.features || []).map((f, i) => (
                  <View key={i} style={styles.planDetail}>
                    <Ionicons name="checkmark-circle" size={18} color="#10B981" />
                    <Text style={styles.planDetailText}>{f}</Text>
                  </View>
                ))}
              </View>

              {!isCurrentPlan && (
                <TouchableOpacity
                  style={styles.subscribeBtn}
                  onPress={() => handleSubscribe(plan)}
                  disabled={subscribing === plan.id}
                >
                  {subscribing === plan.id ? (
                    <ActivityIndicator color="#FFF" size="small" />
                  ) : (
                    <Text style={styles.subscribeBtnText}>
                      {currentSub?.has_subscription ? 'Switch to this plan' : 'Subscribe'}
                    </Text>
                  )}
                </TouchableOpacity>
              )}
            </View>
          );
        })}

        {plans.length === 0 && freeMode && (
          <View style={styles.freeCard}>
            <Text style={styles.freeEmoji}>🎉</Text>
            <Text style={styles.freeTitle}>It&apos;s Free Right Now!</Text>
            <Text style={styles.freeMessage}>{freeMessage}</Text>
            <View style={styles.freeBadge}>
              <Ionicons name="checkmark-circle" size={16} color="#10B981" />
              <Text style={styles.freeBadgeText}>No subscription needed</Text>
            </View>
          </View>
        )}

        {plans.length === 0 && !freeMode && (
          <View style={styles.empty}>
            <Ionicons name="card-outline" size={48} color={colors.border} />
            <Text style={styles.emptyText}>No plans available in your area yet</Text>
          </View>
        )}

        {/* Payment History */}
        {payments.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>Payment History</Text>
            <Text style={styles.sectionSubtitle}>
              {paymentsTotal > 0 ? `${paymentsTotal} charge${paymentsTotal === 1 ? '' : 's'} · tax included` : 'Spinr Pass charges'}
            </Text>
            {payments.map((p) => (
              <View key={p.id} style={styles.paymentRow}>
                <View style={styles.paymentIcon}>
                  <Ionicons name="receipt-outline" size={20} color={colors.primary} />
                </View>
                <View style={styles.paymentInfo}>
                  <Text style={styles.paymentPlan}>{p.plan_name || 'Spinr Pass'}</Text>
                  <Text style={styles.paymentMeta}>
                    {p.billing_reason === 'subscription_cycle' ? 'Auto-renewal' : 'One-time purchase'}
                    {'  ·  '}
                    {formatDate(p.created_at)}
                  </Text>
                  {parseFloat(p.subtotal) > 0 && (
                    <Text style={styles.paymentTax}>
                      {`Subtotal $${parseFloat(p.subtotal).toFixed(2)}`}
                      {parseFloat(p.gst_amount) > 0 ? `  ·  GST $${parseFloat(p.gst_amount).toFixed(2)}` : ''}
                      {parseFloat(p.pst_amount) > 0 ? `  ·  PST $${parseFloat(p.pst_amount).toFixed(2)}` : ''}
                      {parseFloat(p.hst_amount) > 0 ? `  ·  HST $${parseFloat(p.hst_amount).toFixed(2)}` : ''}
                    </Text>
                  )}
                  <TouchableOpacity
                    onPress={() => resendInvoice(p.id)}
                    disabled={resendingId === p.id}
                    style={styles.resendBtn}
                  >
                    {resendingId === p.id ? (
                      <ActivityIndicator size="small" color={colors.primary} />
                    ) : (
                      <Text style={[styles.resendText, { color: colors.primary }]}>Send Invoice</Text>
                    )}
                  </TouchableOpacity>
                </View>
                <View style={styles.paymentAmountCol}>
                  <Text style={styles.paymentAmount}>
                    ${parseFloat(p.amount).toFixed(2)}
                  </Text>
                  <Text style={styles.paymentCurrency}>{p.currency}</Text>
                </View>
              </View>
            ))}
            {paymentsOffset < paymentsTotal && (
              <View style={styles.loadMoreRow}>
                {paymentsLoadingMore ? (
                  <ActivityIndicator size="small" color={colors.primary} />
                ) : (
                  <TouchableOpacity onPress={loadMorePayments}>
                    <Text style={styles.loadMoreText}>Load more</Text>
                  </TouchableOpacity>
                )}
              </View>
            )}
          </>
        )}

      </ScrollView>

    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    content: { paddingTop: 8 },

    // Current subscription
    currentCard: {
      backgroundColor: colors.primary, margin: 16, borderRadius: 20, padding: 20, alignItems: 'center',
    },
    currentBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 4,
      backgroundColor: 'rgba(255,255,255,0.2)', paddingHorizontal: 12, paddingVertical: 4, borderRadius: 12, marginBottom: 10,
    },
    currentBadgeText: { fontSize: 11, fontWeight: '700', color: '#FFF', letterSpacing: 0.5 },
    currentPlan: { fontSize: 24, fontWeight: '800', color: '#FFF', marginBottom: 4 },
    currentPrice: { fontSize: 16, color: 'rgba(255,255,255,0.8)' },
    currentStats: {
      flexDirection: 'row', marginTop: 16, paddingTop: 16,
      borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.2)', width: '100%',
    },
    currentStat: { flex: 1, alignItems: 'center' },
    statValue: { fontSize: 18, fontWeight: '800', color: '#FFF' },
    statLabel: { fontSize: 10, color: 'rgba(255,255,255,0.7)', marginTop: 2 },
    statDivider: { width: 1, backgroundColor: 'rgba(255,255,255,0.2)' },
    countdownRow: {
      flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 8,
      marginTop: 14, paddingTop: 14, borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.2)', width: '100%',
    },
    countdownItem: {
      flexDirection: 'row', alignItems: 'center', gap: 5,
      backgroundColor: 'rgba(255,255,255,0.15)', paddingHorizontal: 10, paddingVertical: 5, borderRadius: 10,
    },
    countdownText: { fontSize: 12, fontWeight: '600', color: '#FFF' },
    cancelLink: { marginTop: 14 },
    cancelLinkText: { fontSize: 13, color: 'rgba(255,255,255,0.6)' },

    // Section
    sectionTitle: { fontSize: 20, fontWeight: '800', color: colors.text, paddingHorizontal: 20, marginTop: 20 },
    sectionSubtitle: { fontSize: 14, color: colors.textDim, paddingHorizontal: 20, marginTop: 4, marginBottom: 16 },

    // Plan card
    planCard: {
      backgroundColor: colors.surfaceLight, marginHorizontal: 16, marginBottom: 12,
      borderRadius: 18, padding: 20, borderWidth: 1.5, borderColor: 'transparent',
    },
    planCardActive: { borderColor: colors.primary, backgroundColor: colors.surfaceLight },
    currentTagRow: { flexDirection: 'row', justifyContent: 'flex-end', marginBottom: 8 },
    currentTag: {
      backgroundColor: colors.primary, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
    },
    currentTagText: { fontSize: 9, fontWeight: '700', color: '#FFF', letterSpacing: 0.5 },
    planHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 },
    planName: { fontSize: 18, fontWeight: '700', color: colors.text },
    planDesc: { fontSize: 13, color: colors.textDim, marginTop: 2, maxWidth: 180 },
    priceWrap: { alignItems: 'flex-end' },
    planPrice: { fontSize: 24, fontWeight: '800', color: colors.primary },
    planDuration: { fontSize: 12, color: colors.textSecondary },
    planDetails: { gap: 8, marginBottom: 16 },
    planDetail: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    planDetailText: { fontSize: 14, color: colors.textDim },
    subscribeBtn: {
      backgroundColor: colors.primary, borderRadius: 14, paddingVertical: 14, alignItems: 'center',
    },
    subscribeBtnText: { fontSize: 15, fontWeight: '700', color: '#FFF' },

    empty: { alignItems: 'center', paddingVertical: 40 },
    emptyText: { fontSize: 15, color: colors.textSecondary, marginTop: 12 },

    // Free mode celebration card
    freeCard: {
      backgroundColor: '#ECFDF5', marginHorizontal: 16, marginTop: 8,
      borderRadius: 20, padding: 28, alignItems: 'center',
      borderWidth: 1.5, borderColor: '#A7F3D0',
    },
    freeEmoji: { fontSize: 48, marginBottom: 12 },
    freeTitle: { fontSize: 22, fontWeight: '800', color: '#065F46', marginBottom: 8 },
    freeMessage: { fontSize: 15, color: '#047857', textAlign: 'center', lineHeight: 22, marginBottom: 16 },
    freeBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 6,
      backgroundColor: '#D1FAE5', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 12,
    },
    freeBadgeText: { fontSize: 13, fontWeight: '700', color: '#065F46' },

    // Payment history
    paymentRow: {
      flexDirection: 'row', alignItems: 'flex-start', marginHorizontal: 16, marginBottom: 8,
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14, gap: 12,
    },
    paymentIcon: {
      width: 40, height: 40, borderRadius: 12, backgroundColor: colors.surface,
      justifyContent: 'center', alignItems: 'center', flexShrink: 0,
    },
    paymentInfo: { flex: 1 },
    paymentPlan: { fontSize: 14, fontWeight: '600', color: colors.text },
    paymentMeta: { fontSize: 12, color: colors.textDim, marginTop: 2 },
    paymentTax: { fontSize: 11, color: colors.textDim, marginTop: 3, opacity: 0.8 },
    resendBtn: { marginTop: 6, alignSelf: 'flex-start' },
    resendText: { fontSize: 12, fontWeight: '600' },
    paymentAmountCol: { alignItems: 'flex-end', flexShrink: 0 },
    paymentAmount: { fontSize: 15, fontWeight: '700', color: colors.text },
    paymentCurrency: { fontSize: 10, color: colors.textDim, marginTop: 1 },
    loadMoreRow: { alignItems: 'center', paddingVertical: 16 },
    loadMoreText: { fontSize: 14, color: colors.primary, fontWeight: '600' },
  });
}
