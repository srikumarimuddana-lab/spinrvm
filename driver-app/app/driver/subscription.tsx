import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView,
  ActivityIndicator, Alert, Platform, Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = SpinrConfig.theme.colors;

interface Plan {
  id: string;
  name: string;
  price: number;
  duration_days: number;
  rides_per_day: number;
  description: string;
  features: string[];
}

interface Subscription {
  id: string;
  plan_name: string;
  price: number;
  rides_per_day: number;
  status: string;
  started_at: string;
  expires_at: string;
}

export default function SubscriptionScreen() {
  const router = useRouter();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [currentSub, setCurrentSub] = useState<any>(null);
  const [freeMode, setFreeMode] = useState(false);
  const [freeMessage, setFreeMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [subscribing, setSubscribing] = useState<string | null>(null);

  useEffect(() => { loadData(); }, []);

  // Handle deep-link return from Stripe Checkout.
  // URL format: spinr-driver://subscription/success?session_id=cs_xxx
  useEffect(() => {
    const handleUrl = async (event: { url: string }) => {
      const url = event.url;
      if (!url.includes('subscription/success')) return;

      const match = url.match(/session_id=([^&]+)/);
      if (!match) return;
      const sessionId = match[1];

      setLoading(true);
      try {
        const res = await api.get(`/drivers/subscription/verify-session?session_id=${sessionId}`);
        if (res.data?.status === 'active') {
          Alert.alert('Payment Successful!', 'Your Spinr Pass is now active. Go online and start earning!');
        } else {
          Alert.alert('Processing...', 'Your payment is being confirmed. This may take a moment.');
        }
      } catch (e) {
        console.log('[Subscription] verify-session error:', e);
      }
      loadData();
    };

    const sub = Linking.addEventListener('url', handleUrl);

    // Also check if the app was opened via the URL (cold start)
    Linking.getInitialURL().then((url) => {
      if (url) handleUrl({ url });
    });

    return () => sub.remove();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [plansRes, subRes] = await Promise.all([
        api.get('/drivers/subscription/plans'),
        api.get('/drivers/subscription/current'),
      ]);
      const data = plansRes.data;
      // Backend returns {plans, free_mode, message} when Spinr Pass is off
      if (data && typeof data === 'object' && 'free_mode' in data) {
        setPlans(data.plans || []);
        setFreeMode(data.free_mode || false);
        setFreeMessage(data.message || '');
      } else {
        // Fallback for old response format (plain array)
        setPlans(Array.isArray(data) ? data : []);
        setFreeMode(false);
      }
      setCurrentSub(subRes.data);
    } catch (e) { console.log('Sub load error:', e); }
    finally { setLoading(false); }
  };

  const handleSubscribe = async (plan: Plan) => {
    if (currentSub?.has_subscription) {
      Alert.alert(
        'Switch Plan?',
        `You currently have "${currentSub.subscription.plan_name}". Switch to "${plan.name}" for $${plan.price.toFixed(2)}?`,
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Switch', onPress: () => doSubscribe(plan) },
        ]
      );
    } else {
      Alert.alert(
        'Subscribe',
        `Subscribe to "${plan.name}" for $${plan.price.toFixed(2)}/${getDurationLabel(plan.duration_days).toLowerCase()}?`,
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Subscribe', onPress: () => doSubscribe(plan) },
        ]
      );
    }
  };

  const doSubscribe = async (plan: Plan) => {
    setSubscribing(plan.id);
    try {
      const res = await api.post('/drivers/subscription/subscribe', { plan_id: plan.id });

      if (res.data?.checkout_url) {
        // Stripe Checkout path — open the payment page in the browser.
        // After payment, Stripe redirects to spinr-driver://subscription/success
        // which brings the driver back to the app.
        await Linking.openURL(res.data.checkout_url);

        // The user is now in the browser paying. When they come back
        // (via deep-link), the app will call verify-session. For now
        // we poll briefly to catch the webhook activation.
        const sessionId = res.data.session_id;
        if (sessionId) {
          // Give the webhook ~5s to fire, then verify.
          setTimeout(async () => {
            try {
              const verifyRes = await api.get(`/drivers/subscription/verify-session?session_id=${sessionId}`);
              if (verifyRes.data?.status === 'active') {
                Alert.alert('Subscribed!', `You're now on the ${plan.name} plan. Go online and start earning!`);
              }
            } catch { /* webhook may not have fired yet — loadData will catch up */ }
            loadData();
          }, 5000);
        }
      } else {
        // Dev/test mode — subscription activated immediately
        Alert.alert('Subscribed!', `You're now on the ${plan.name} plan. Go online and start earning!`);
        loadData();
      }
    } catch (e: any) {
      Alert.alert('Error', e.response?.data?.detail || 'Failed to subscribe');
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
              Alert.alert('Cancelled', 'Your subscription has been cancelled.');
              loadData();
            } catch (e: any) {
              Alert.alert('Error', e.response?.data?.detail || 'Failed to cancel');
            }
          },
        },
      ]
    );
  };

  const getDurationLabel = (days: number) => {
    if (days === 1) return 'Day';
    if (days === 7) return 'Week';
    if (days === 30) return 'Month';
    if (days === 365) return 'Year';
    return `${days} days`;
  };

  const formatDate = (d: string) => {
    try { return new Date(d).toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: 'numeric' }); }
    catch { return d; }
  };

  if (loading) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={COLORS.primary} />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>

        {/* Header */}
        <View style={styles.header}>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Spinr Pass</Text>
          <View style={{ width: 44 }} />
        </View>

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
                <Text style={styles.statLabel}>Rides left today</Text>
              </View>
              <View style={styles.statDivider} />
              <View style={styles.currentStat}>
                <Text style={styles.statValue}>{currentSub.today_rides}</Text>
                <Text style={styles.statLabel}>Rides today</Text>
              </View>
              <View style={styles.statDivider} />
              <View style={styles.currentStat}>
                <Text style={styles.statValue}>{formatDate(currentSub.subscription.expires_at)}</Text>
                <Text style={styles.statLabel}>Expires</Text>
              </View>
            </View>

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
                <View style={styles.currentTag}>
                  <Text style={styles.currentTagText}>CURRENT</Text>
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
                  <Ionicons name={plan.rides_per_day === -1 ? 'infinite' : 'car'} size={18} color={COLORS.primary} />
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
            <Text style={styles.freeTitle}>It's Free Right Now!</Text>
            <Text style={styles.freeMessage}>{freeMessage}</Text>
            <View style={styles.freeBadge}>
              <Ionicons name="checkmark-circle" size={16} color="#10B981" />
              <Text style={styles.freeBadgeText}>No subscription needed</Text>
            </View>
          </View>
        )}

        {plans.length === 0 && !freeMode && (
          <View style={styles.empty}>
            <Ionicons name="card-outline" size={48} color="#DDD" />
            <Text style={styles.emptyText}>No plans available in your area yet</Text>
          </View>
        )}

      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFF' },
  content: { paddingBottom: 40 },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#F0F0F0',
  },
  backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
  headerTitle: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },

  // Current subscription
  currentCard: {
    backgroundColor: COLORS.primary, margin: 16, borderRadius: 20, padding: 20, alignItems: 'center',
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
  cancelLink: { marginTop: 14 },
  cancelLinkText: { fontSize: 13, color: 'rgba(255,255,255,0.6)' },

  // Section
  sectionTitle: { fontSize: 20, fontWeight: '800', color: '#1A1A1A', paddingHorizontal: 20, marginTop: 20 },
  sectionSubtitle: { fontSize: 14, color: '#888', paddingHorizontal: 20, marginTop: 4, marginBottom: 16 },

  // Plan card
  planCard: {
    backgroundColor: '#F9F9F9', marginHorizontal: 16, marginBottom: 12,
    borderRadius: 18, padding: 20, borderWidth: 1.5, borderColor: 'transparent',
  },
  planCardActive: { borderColor: COLORS.primary, backgroundColor: '#FEF2F2' },
  currentTag: {
    position: 'absolute', top: 12, right: 12,
    backgroundColor: COLORS.primary, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
  },
  currentTagText: { fontSize: 9, fontWeight: '700', color: '#FFF', letterSpacing: 0.5 },
  planHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 },
  planName: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },
  planDesc: { fontSize: 13, color: '#888', marginTop: 2, maxWidth: 180 },
  priceWrap: { alignItems: 'flex-end' },
  planPrice: { fontSize: 24, fontWeight: '800', color: COLORS.primary },
  planDuration: { fontSize: 12, color: '#999' },
  planDetails: { gap: 8, marginBottom: 16 },
  planDetail: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  planDetailText: { fontSize: 14, color: '#444' },
  subscribeBtn: {
    backgroundColor: COLORS.primary, borderRadius: 14, paddingVertical: 14, alignItems: 'center',
  },
  subscribeBtnText: { fontSize: 15, fontWeight: '700', color: '#FFF' },

  empty: { alignItems: 'center', paddingVertical: 40 },
  emptyText: { fontSize: 15, color: '#999', marginTop: 12 },

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
});
