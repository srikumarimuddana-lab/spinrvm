import React, { useState, useEffect, useMemo, useContext } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList,
  TextInput, ActivityIndicator, KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useStripe } from '@stripe/stripe-react-native';
import { StripeKeyContext } from './_layout';
import { useWalletStore, WalletTransaction, WalletTransactionMeta } from '../store/walletStore';
import { showToast } from '../store/toastStore';
import { getApiErrorMessage } from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { isWalletTopUpAmountValid } from '../utils/walletTopUpSchema';

const TOP_UP_AMOUNTS = [10, 25, 50, 100];

const TXN_ICONS: Record<string, string> = {
  top_up: 'arrow-down-circle',
  ride_payment: 'car',
  ride_refund: 'refresh-circle',
  bonus: 'gift',
  referral: 'people',
  cashout: 'arrow-up-circle',
  fare_split_received: 'arrow-down',
  fare_split_sent: 'arrow-up',
  quest_reward: 'trophy',
};

const TXN_COLORS: Record<string, string> = {
  top_up: '#10B981',
  ride_payment: '#EF4444',
  ride_refund: '#10B981',
  bonus: '#F59E0B',
  referral: '#8B5CF6',
  cashout: '#EF4444',
  fare_split_received: '#10B981',
  fare_split_sent: '#EF4444',
  quest_reward: '#F59E0B',
};

// Only actual ride transactions carry a ride id in reference_id and can open
// the ride-details page. Rewards/top-ups/fare-splits (referral, bonus, top_up,
// cashout, quest_reward, fare_split_*) reference a non-ride record, so opening
// ride-details for them would render a broken/empty page.
const RIDE_TXN_TYPES = new Set(['ride_payment', 'ride_refund']);

export default function WalletScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const stripeKey = useContext(StripeKeyContext);
  const { initPaymentSheet, presentPaymentSheet } = useStripe();
  const {
    wallet, transactions, walletLoading, transactionsLoading, error: storeError,
    fetchWallet, topUp, fetchTransactions, clearError,
  } = useWalletStore();

  const [showTopUp, setShowTopUp] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<number | null>(null);
  const [customAmount, setCustomAmount] = useState('');
  const [topUpLoading, setTopUpLoading] = useState(false);
  const effectiveAmount = selectedPreset ?? (parseFloat(customAmount) || 0);
  const canTopUp = isWalletTopUpAmountValid(effectiveAmount) && !topUpLoading;

  useEffect(() => {
    clearError();
    void Promise.all([fetchWallet(), fetchTransactions(30)]).catch(() => {});
    // clearError/fetchWallet/fetchTransactions are all zustand actions (stable).
  }, [clearError, fetchWallet, fetchTransactions]);

  const selectPreset = (amt: number) => {
    setSelectedPreset(amt);
    setCustomAmount('');
  };

  const onCustomAmountChange = (text: string) => {
    setCustomAmount(text);
    setSelectedPreset(null);
  };

  const handleTopUp = async () => {
    if (!stripeKey) {
      showToast('Payment Not Available', 'Payment processing is not set up yet. Please add a card in Cards settings first, or try again later.', 'warning');
      return;
    }
    if (!isWalletTopUpAmountValid(effectiveAmount)) {
      showToast('Invalid Amount', 'Please select or enter an amount between $1 and $500.', 'warning');
      return;
    }
    setTopUpLoading(true);
    try {
      const { paymentIntent, ephemeralKey, customer } = await topUp(effectiveAmount);

      const { error: initError } = await initPaymentSheet({
        merchantDisplayName: 'Spinr',
        customerId: customer,
        customerEphemeralKeySecret: ephemeralKey,
        paymentIntentClientSecret: paymentIntent,
        allowsDelayedPaymentMethods: false,
        googlePay: {
          merchantCountryCode: 'CA',
          testEnv: process.env.EXPO_PUBLIC_ENV !== 'production',
        },
        applePay: { merchantCountryCode: 'CA' },
        returnURL: 'spinr://stripe-redirect',
      });

      if (initError) {
        showToast('Payment Error', initError.message, 'danger');
        return;
      }

      const { error: presentError } = await presentPaymentSheet();
      if (presentError) {
        if (presentError.code !== 'Canceled') {
          showToast('Payment Failed', presentError.message, 'danger');
        }
        return;
      }

      setShowTopUp(false);
      setSelectedPreset(null);
      setCustomAmount('');
      showToast('Payment Successful', `$${effectiveAmount.toFixed(2)} will be added to your wallet shortly.`, 'success');
      setTimeout(() => { fetchWallet(); fetchTransactions(30); }, 2000);
    } catch (err: any) {
      showToast('Top-up Failed', getApiErrorMessage(err, 'Could not complete your top-up. Please try again.'), 'danger');
    } finally {
      setTopUpLoading(false);
    }
  };

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-CA', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const renderTransaction = ({ item }: { item: WalletTransaction }) => {
    const icon = TXN_ICONS[item.type] || 'swap-horizontal';
    const color = TXN_COLORS[item.type] || colors.textDim;
    const amountNum = parseFloat(item.amount);
    const isCredit = amountNum > 0;
    const meta = item.metadata as WalletTransactionMeta | null | undefined;
    const hasRideDetails = item.type === 'ride_payment' && meta?.pickup_address;
    // Only ride transactions link to ride-details; a referral/top-up/reward's
    // reference_id is not a ride id.
    const canOpenRide = RIDE_TXN_TYPES.has(item.type) && !!item.reference_id;
    const bookingId =
      meta?.ride_code || (item.reference_id ? item.reference_id.slice(0, 8).toUpperCase() : null);

    const displayDesc = item.description || item.type.replace(/_/g, ' ');

    return (
      <TouchableOpacity
        style={styles.txnRow}
        activeOpacity={canOpenRide ? 0.6 : 1}
        onPress={canOpenRide ? () => router.push(`/ride-details?rideId=${item.reference_id}` as any) : undefined}
      >
        <View style={[styles.txnIcon, { backgroundColor: color + '15' }]}>
          <Ionicons name={icon as any} size={22} color={color} />
        </View>
        <View style={styles.txnInfo}>
          <Text style={styles.txnDesc}>{displayDesc}</Text>
          {hasRideDetails && (
            <View style={styles.txnMeta}>
              <View style={styles.txnMetaRow}>
                <Ionicons name="location" size={11} color="#10B981" />
                <Text style={styles.txnMetaText} numberOfLines={1}>{meta!.pickup_address}</Text>
              </View>
              <View style={styles.txnMetaRow}>
                <Ionicons name="flag" size={11} color="#EF4444" />
                <Text style={styles.txnMetaText} numberOfLines={1}>{meta!.dropoff_address}</Text>
              </View>
              {bookingId && (
                <View style={styles.txnBookingRow}>
                  <Text style={styles.txnBookingId}>{bookingId}</Text>
                </View>
              )}
            </View>
          )}
          <Text style={styles.txnDate}>{formatDate(item.created_at)}</Text>
        </View>
        <View style={styles.txnAmountCol}>
          <Text style={[styles.txnAmount, { color: isCredit ? '#10B981' : '#EF4444' }]}>
            {isCredit ? '+' : ''}{amountNum < 0 ? '-' : ''}${Math.abs(amountNum).toFixed(2)}
          </Text>
          {canOpenRide && (
            <Ionicons name="chevron-forward" size={14} color={colors.border} style={{ marginTop: 2 }} />
          )}
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Wallet</Text>
        <View style={{ width: 44 }} />
      </View>

      {/* Balance Card */}
      <View style={styles.balanceCard}>
        <Text style={styles.balanceLabel}>Available Balance</Text>
        {storeError ? (
          <TouchableOpacity onPress={() => {
            clearError();
            void Promise.all([fetchWallet(), fetchTransactions(30)]).catch(() => {});
          }}>
            <Text style={[styles.balanceAmount, { fontSize: 18 }]}>Balance unavailable</Text>
            <Text style={{ color: '#FFF', opacity: 0.8, textAlign: 'center', marginTop: 4 }}>Tap to retry</Text>
          </TouchableOpacity>
        ) : (
          <Text style={styles.balanceAmount}>
            ${parseFloat(wallet?.balance ?? '0').toFixed(2)}
          </Text>
        )}
        <Text style={styles.balanceCurrency}>{wallet?.currency || 'CAD'}</Text>

        <View style={styles.balanceActions}>
          <TouchableOpacity style={styles.actionButton} onPress={() => setShowTopUp(true)}>
            <Ionicons name="add-circle" size={22} color="#FFF" />
            <Text style={styles.actionText}>Top Up</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.actionButton, styles.actionSecondary]}
            onPress={() => router.push('/manage-cards' as any)}
          >
            <Ionicons name="card" size={22} color={colors.primary} />
            <Text style={[styles.actionText, { color: colors.primary }]}>Cards</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Top Up Panel */}
      {showTopUp && (
        <KeyboardAvoidingView behavior="padding">
          <View style={styles.topUpSection}>
            <Text style={styles.topUpTitle}>Add Funds</Text>
            <View style={styles.topUpGrid}>
              {TOP_UP_AMOUNTS.map((amt) => (
                <TouchableOpacity
                  key={amt}
                  style={[styles.topUpChip, selectedPreset === amt && styles.topUpChipSelected]}
                  onPress={() => selectPreset(amt)}
                  disabled={topUpLoading}
                >
                  <Text style={[styles.topUpChipText, selectedPreset === amt && styles.topUpChipTextSelected]}>
                    ${amt}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
            <View style={styles.customRow}>
              <TextInput
                style={[styles.customInput, customAmount ? styles.customInputActive : null]}
                placeholder="Custom amount"
                placeholderTextColor={colors.textDim}
                keyboardType="decimal-pad"
                value={customAmount}
                onChangeText={onCustomAmountChange}
                editable={!topUpLoading}
              />
            </View>
            <TouchableOpacity
              style={[styles.addFundsButton, !canTopUp && styles.addFundsButtonDisabled]}
              onPress={handleTopUp}
              disabled={!canTopUp}
              activeOpacity={0.7}
            >
              {topUpLoading ? (
                <ActivityIndicator color="#FFF" size="small" />
              ) : (
                <>
                  <Ionicons name="wallet-outline" size={20} color="#FFF" />
                  <Text style={styles.addFundsButtonText}>
                    {effectiveAmount >= 1 ? `Add $${effectiveAmount.toFixed(2)}` : 'Select an Amount'}
                  </Text>
                </>
              )}
            </TouchableOpacity>
            <TouchableOpacity onPress={() => { setShowTopUp(false); setSelectedPreset(null); setCustomAmount(''); }}>
              <Text style={styles.cancelText}>Cancel</Text>
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      )}

      {/* Transaction History */}
      <View style={styles.txnHeader}>
        <Text style={styles.txnTitle}>Recent Activity</Text>
      </View>

      {(walletLoading || transactionsLoading) && transactions.length === 0 ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : transactions.length === 0 ? (
        <View style={styles.emptyContainer}>
          <Ionicons name="wallet-outline" size={48} color="#CCC" />
          <Text style={styles.emptyText}>No transactions yet</Text>
          <Text style={styles.emptySubtext}>Top up your wallet to get started</Text>
        </View>
      ) : (
        <FlatList
          data={transactions}
          keyExtractor={(item) => item.id}
          renderItem={renderTransaction}
          contentContainerStyle={styles.txnList}
          showsVerticalScrollIndicator={false}
        />
      )}

    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.background },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12, backgroundColor: colors.surface,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backButton: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceLight, alignItems: 'center', justifyContent: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },

    balanceCard: {
      margin: 16, backgroundColor: colors.primary, borderRadius: 20,
      padding: 24, alignItems: 'center',
    },
    balanceLabel: { fontSize: 14, color: 'rgba(255,255,255,0.8)', marginBottom: 4 },
    balanceAmount: { fontSize: 42, fontWeight: '800', color: '#FFF' },
    balanceCurrency: { fontSize: 14, color: 'rgba(255,255,255,0.7)', marginTop: 2 },
    balanceActions: { flexDirection: 'row', marginTop: 20, gap: 12 },
    actionButton: {
      flexDirection: 'row', alignItems: 'center', gap: 6,
      backgroundColor: 'rgba(255,255,255,0.2)', paddingHorizontal: 20, paddingVertical: 10,
      borderRadius: 24,
    },
    actionSecondary: { backgroundColor: '#FFF' },
    actionText: { fontSize: 15, fontWeight: '600', color: '#FFF' },

    topUpSection: {
      margin: 16, backgroundColor: colors.surface, borderRadius: 16, padding: 20,
      shadowColor: '#000', shadowOpacity: 0.05, shadowOffset: { width: 0, height: 2 }, shadowRadius: 8,
      elevation: 3,
    },
    topUpTitle: { fontSize: 17, fontWeight: '700', color: colors.text, marginBottom: 16 },
    topUpGrid: { flexDirection: 'row', gap: 10, flexWrap: 'wrap' },
    topUpChip: {
      flex: 1, minWidth: 70, alignItems: 'center', paddingVertical: 14,
      backgroundColor: colors.surfaceLight, borderRadius: 12, borderWidth: 1.5, borderColor: colors.border,
    },
    topUpChipSelected: {
      backgroundColor: colors.primary + '15', borderColor: colors.primary,
    },
    topUpChipText: { fontSize: 16, fontWeight: '700', color: colors.text },
    topUpChipTextSelected: { color: colors.primary },
    customRow: { flexDirection: 'row', marginTop: 12, gap: 10 },
    customInput: {
      flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 12, paddingHorizontal: 16,
      paddingVertical: 12, fontSize: 16, borderWidth: 1.5, borderColor: colors.border,
      color: colors.text,
    },
    customInputActive: { borderColor: colors.primary },
    addFundsButton: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
      backgroundColor: colors.primary, borderRadius: 14, paddingVertical: 16,
      marginTop: 16,
    },
    addFundsButtonDisabled: { opacity: 0.45 },
    addFundsButtonText: { fontSize: 16, fontWeight: '700', color: '#FFF' },
    cancelText: { textAlign: 'center', color: colors.textDim, marginTop: 12, fontSize: 14 },

    txnHeader: { paddingHorizontal: 16, paddingVertical: 8 },
    txnTitle: { fontSize: 17, fontWeight: '700', color: colors.text },
    txnList: { paddingHorizontal: 16, paddingBottom: 40 },

    txnRow: {
      flexDirection: 'row', alignItems: 'center', paddingVertical: 14,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    txnIcon: {
      width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center',
      marginRight: 12,
    },
    txnInfo: { flex: 1 },
    txnDesc: { fontSize: 15, fontWeight: '500', color: colors.text, textTransform: 'capitalize' },
    txnMeta: {
      marginTop: 6, paddingTop: 6, borderTopWidth: 1, borderTopColor: colors.border,
    },
    txnMetaRow: {
      flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 2,
    },
    txnMetaText: {
      fontSize: 12, color: colors.textDim, flex: 1,
    },
    txnBookingRow: {
      flexDirection: 'row', marginTop: 4,
    },
    txnBookingId: {
      fontSize: 11, fontWeight: '600', color: colors.textDim,
      backgroundColor: colors.surfaceLight, paddingHorizontal: 8, paddingVertical: 2,
      borderRadius: 4, overflow: 'hidden', letterSpacing: 0.5,
    },
    txnDate: { fontSize: 13, color: colors.textDim, marginTop: 4 },
    txnAmountCol: { alignItems: 'flex-end' },
    txnAmount: { fontSize: 16, fontWeight: '700' },

    loadingContainer: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingTop: 40 },
    emptyContainer: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingTop: 60 },
    emptyText: { fontSize: 16, fontWeight: '600', color: colors.textDim, marginTop: 12 },
    emptySubtext: { fontSize: 14, color: '#BBB', marginTop: 4 },

  });
}
