import React, { useState, useEffect, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList,
  TextInput, ActivityIndicator, Platform, KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useWalletStore, WalletTransaction } from '../store/walletStore';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

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

export default function WalletScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const {
    wallet, transactions, isLoading,
    fetchWallet, topUp, fetchTransactions, clearError,
  } = useWalletStore();

  const [showTopUp, setShowTopUp] = useState(false);
  const [customAmount, setCustomAmount] = useState('');
  const [topUpLoading, setTopUpLoading] = useState(false);
  const [alertState, setAlertState] = useState<{
    visible: boolean; title: string; message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
  }>({ visible: false, title: '', message: '', variant: 'info' });

  useEffect(() => {
    fetchWallet();
    fetchTransactions(30);
  }, []);

  const handleTopUp = async (amount: number) => {
    if (amount <= 0 || amount > 500) {
      setAlertState({ visible: true, title: 'Invalid Amount', message: 'Amount must be between $1 and $500', variant: 'warning' });
      return;
    }
    setTopUpLoading(true);
    try {
      await topUp(amount);
      setShowTopUp(false);
      setCustomAmount('');
      setAlertState({ visible: true, title: 'Success', message: `$${amount.toFixed(2)} added to your wallet`, variant: 'success' });
      fetchTransactions(30);
    } catch (err: any) {
      setAlertState({ visible: true, title: 'Top-up Failed', message: err.message || 'Please try again', variant: 'danger' });
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
    const isCredit = item.amount > 0;

    return (
      <View style={styles.txnRow}>
        <View style={[styles.txnIcon, { backgroundColor: color + '15' }]}>
          <Ionicons name={icon as any} size={22} color={color} />
        </View>
        <View style={styles.txnInfo}>
          <Text style={styles.txnDesc}>{item.description || item.type.replace(/_/g, ' ')}</Text>
          <Text style={styles.txnDate}>{formatDate(item.created_at)}</Text>
        </View>
        <Text style={[styles.txnAmount, { color: isCredit ? '#10B981' : '#EF4444' }]}>
          {isCredit ? '+' : ''}{item.amount < 0 ? '-' : ''}${Math.abs(item.amount).toFixed(2)}
        </Text>
      </View>
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
        <Text style={styles.balanceAmount}>
          ${(wallet?.balance ?? 0).toFixed(2)}
        </Text>
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

      {/* Top Up Modal */}
      {showTopUp && (
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={styles.topUpSection}>
            <Text style={styles.topUpTitle}>Add Funds</Text>
            <View style={styles.topUpGrid}>
              {TOP_UP_AMOUNTS.map((amt) => (
                <TouchableOpacity
                  key={amt}
                  style={styles.topUpChip}
                  onPress={() => handleTopUp(amt)}
                  disabled={topUpLoading}
                >
                  <Text style={styles.topUpChipText}>${amt}</Text>
                </TouchableOpacity>
              ))}
            </View>
            <View style={styles.customRow}>
              <TextInput
                style={styles.customInput}
                placeholder="Custom amount"
                placeholderTextColor={colors.textDim}
                keyboardType="decimal-pad"
                value={customAmount}
                onChangeText={setCustomAmount}
              />
              <TouchableOpacity
                style={[styles.customButton, !customAmount && styles.customButtonDisabled]}
                onPress={() => handleTopUp(parseFloat(customAmount) || 0)}
                disabled={!customAmount || topUpLoading}
              >
                {topUpLoading ? (
                  <ActivityIndicator color="#FFF" size="small" />
                ) : (
                  <Text style={styles.customButtonText}>Add</Text>
                )}
              </TouchableOpacity>
            </View>
            <TouchableOpacity onPress={() => setShowTopUp(false)}>
              <Text style={styles.cancelText}>Cancel</Text>
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      )}

      {/* Transaction History */}
      <View style={styles.txnHeader}>
        <Text style={styles.txnTitle}>Recent Activity</Text>
      </View>

      {isLoading && transactions.length === 0 ? (
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

      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        onClose={() => setAlertState({ ...alertState, visible: false })}
        buttons={[{ text: 'OK', onPress: () => setAlertState({ ...alertState, visible: false }) }]}
      />
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
      backgroundColor: colors.surfaceLight, borderRadius: 12, borderWidth: 1, borderColor: colors.border,
    },
    topUpChipText: { fontSize: 16, fontWeight: '700', color: colors.text },
    customRow: { flexDirection: 'row', marginTop: 12, gap: 10 },
    customInput: {
      flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 12, paddingHorizontal: 16,
      paddingVertical: 12, fontSize: 16, borderWidth: 1, borderColor: colors.border,
      color: colors.text,
    },
    customButton: {
      backgroundColor: colors.primary, borderRadius: 12, paddingHorizontal: 24,
      alignItems: 'center', justifyContent: 'center',
    },
    customButtonDisabled: { opacity: 0.5 },
    customButtonText: { fontSize: 15, fontWeight: '700', color: '#FFF' },
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
    txnDate: { fontSize: 13, color: colors.textDim, marginTop: 2 },
    txnAmount: { fontSize: 16, fontWeight: '700' },

    loadingContainer: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingTop: 40 },
    emptyContainer: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingTop: 60 },
    emptyText: { fontSize: 16, fontWeight: '600', color: colors.textDim, marginTop: 12 },
    emptySubtext: { fontSize: 14, color: '#BBB', marginTop: 4 },
  });
}
