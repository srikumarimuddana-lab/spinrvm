import React, { useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { showToast } from '../store/toastStore';
import { getApiErrorMessage } from '@shared/api/client';
import { useWorkProfileStore } from '../store/workProfileStore';

const QUICK_AMOUNTS = [25, 50, 100, 200];

export default function WorkAllowanceRequestScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const { balance, requests, fetchRequests, submitRequest, activeCompanyId } = useWorkProfileStore();

  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    fetchRequests();
  }, [activeCompanyId]);

  const pendingRequest = requests.find(r => r.status === 'pending');
  const lastApproved = requests.find(r => r.status === 'approved' || r.status === 'auto_approved');

  const parsedAmount = parseFloat(amount);
  const isValid = !isNaN(parsedAmount) && parsedAmount > 0 && reason.trim().length >= 5;

  const handleSubmit = async () => {
    if (!isValid || submitting) return;
    setSubmitting(true);
    try {
      const result = await submitRequest(parsedAmount, reason.trim());
      const autoApproved = result?.status === 'auto_approved';
      showToast(
        autoApproved ? 'Auto-approved!' : 'Request Submitted',
        autoApproved
          ? `$${parsedAmount.toFixed(2)} has been added to your allowance automatically.`
          : 'Your request has been sent to your company admin for review.',
        'success',
      );
      setAmount('');
      setReason('');
    } catch (e: any) {
      showToast('Request Failed', getApiErrorMessage(e, 'Could not submit your request. Please try again.'), 'danger');
    } finally {
      setSubmitting(false);
    }
  };

  const statusColor = (s: string) => {
    if (s === 'approved' || s === 'auto_approved') return '#10B981';
    if (s === 'denied') return '#EF4444';
    return '#F59E0B';
  };

  const statusLabel = (s: string) => {
    if (s === 'auto_approved') return 'Auto-approved';
    return s.charAt(0).toUpperCase() + s.slice(1);
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Request More Funds</Text>
        <View style={{ width: 44 }} />
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
        <ScrollView
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ paddingBottom: 40 }}
        >
          {/* Current balance summary */}
          {balance && (
            <View style={styles.balanceSummary}>
              <Ionicons name="wallet-outline" size={18} color="#3B82F6" />
              <Text style={styles.balanceSummaryText}>
                Current balance:{' '}
                <Text style={styles.balanceSummaryAmount}>
                  {balance.type === 'unlimited'
                    ? 'Unlimited'
                    : balance.remaining != null
                      ? `$${balance.remaining.toFixed(2)}`
                      : '—'}
                </Text>
              </Text>
            </View>
          )}

          {/* Pending request warning */}
          {pendingRequest && (
            <View style={styles.pendingBanner}>
              <Ionicons name="time-outline" size={18} color="#F59E0B" />
              <Text style={styles.pendingText}>
                You have a pending request for ${parseFloat(pendingRequest.amount).toFixed(2)} awaiting admin review.
                You cannot submit another until it is decided.
              </Text>
            </View>
          )}

          <View style={styles.card}>
            <Text style={styles.label}>Amount (CAD)</Text>

            {/* Quick-select chips */}
            <View style={styles.quickRow}>
              {QUICK_AMOUNTS.map(q => (
                <TouchableOpacity
                  key={q}
                  style={[styles.quickChip, amount === String(q) && styles.quickChipActive]}
                  onPress={() => setAmount(String(q))}
                >
                  <Text style={[styles.quickChipText, amount === String(q) && styles.quickChipTextActive]}>
                    ${q}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            <TextInput
              style={styles.amountInput}
              placeholder="Enter amount"
              placeholderTextColor={colors.textDim}
              value={amount}
              onChangeText={t => setAmount(t.replace(/[^0-9.]/g, ''))}
              keyboardType="decimal-pad"
            />
          </View>

          <View style={styles.card}>
            <Text style={styles.label}>Reason</Text>
            <TextInput
              style={styles.reasonInput}
              placeholder="Why do you need additional funds? (min 5 characters)"
              placeholderTextColor={colors.textDim}
              value={reason}
              onChangeText={setReason}
              multiline
              textAlignVertical="top"
              maxLength={500}
            />
            <Text style={styles.charCount}>{reason.length}/500</Text>
          </View>

          <TouchableOpacity
            style={[
              styles.submitBtn,
              (!isValid || submitting || !!pendingRequest) && styles.submitBtnDisabled,
            ]}
            onPress={handleSubmit}
            disabled={!isValid || submitting || !!pendingRequest}
            activeOpacity={0.8}
          >
            {submitting ? (
              <ActivityIndicator color="#FFF" />
            ) : (
              <>
                <Text style={styles.submitBtnText}>Submit Request</Text>
                <Ionicons name="arrow-forward" size={18} color="#FFF" />
              </>
            )}
          </TouchableOpacity>

          {/* Recent requests */}
          {requests.length > 0 && (
            <View style={styles.card}>
              <Text style={styles.sectionTitle}>Recent Requests</Text>
              {requests.slice(0, 5).map(req => (
                <View key={req.id} style={styles.reqRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.reqAmount}>${parseFloat(req.amount).toFixed(2)}</Text>
                    <Text style={styles.reqReason} numberOfLines={2}>{req.reason}</Text>
                  </View>
                  <View style={[styles.reqBadge, { backgroundColor: statusColor(req.status) + '20' }]}>
                    <Text style={[styles.reqBadgeText, { color: statusColor(req.status) }]}>
                      {statusLabel(req.status)}
                    </Text>
                  </View>
                </View>
              ))}
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>

    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surfaceLight },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12,
      backgroundColor: colors.surface,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text },

    balanceSummary: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      margin: 16, marginBottom: 0,
      backgroundColor: '#EFF6FF', borderRadius: 10,
      paddingHorizontal: 14, paddingVertical: 10,
    },
    balanceSummaryText: { fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular', color: colors.text },
    balanceSummaryAmount: { fontFamily: 'PlusJakartaSans_700Bold', color: '#1D4ED8' },

    pendingBanner: {
      flexDirection: 'row', alignItems: 'flex-start', gap: 10,
      margin: 16, marginBottom: 0,
      backgroundColor: '#FFFBEB', borderRadius: 10,
      padding: 12, borderLeftWidth: 3, borderLeftColor: '#F59E0B',
    },
    pendingText: { flex: 1, fontSize: 13, fontFamily: 'PlusJakartaSans_400Regular', color: colors.text, lineHeight: 18 },

    card: { backgroundColor: colors.surface, margin: 16, marginBottom: 0, borderRadius: 16, padding: 16 },

    label: { fontSize: 13, fontFamily: 'PlusJakartaSans_700Bold', color: colors.textDim, letterSpacing: 0.5, textTransform: 'uppercase', marginBottom: 12 },

    quickRow: { flexDirection: 'row', gap: 8, marginBottom: 12 },
    quickChip: {
      flex: 1, paddingVertical: 10, borderRadius: 10,
      borderWidth: 1.5, borderColor: colors.border, alignItems: 'center',
    },
    quickChipActive: { borderColor: colors.primary, backgroundColor: '#FFF5F5' },
    quickChipText: { fontSize: 14, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.textDim },
    quickChipTextActive: { color: colors.primary },

    amountInput: {
      height: 52, backgroundColor: colors.surfaceLight, borderRadius: 12,
      paddingHorizontal: 16, fontSize: 24, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    },

    reasonInput: {
      height: 100, backgroundColor: colors.surfaceLight, borderRadius: 12,
      paddingHorizontal: 14, paddingTop: 12, fontSize: 15,
      fontFamily: 'PlusJakartaSans_400Regular', color: colors.text,
    },
    charCount: { fontSize: 11, color: colors.textDim, textAlign: 'right', marginTop: 6 },

    submitBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 8, backgroundColor: colors.primary,
      marginHorizontal: 16, marginTop: 16,
      borderRadius: 28, paddingVertical: 16,
    },
    submitBtnDisabled: { opacity: 0.45 },
    submitBtnText: { fontSize: 16, fontFamily: 'PlusJakartaSans_600SemiBold', color: '#FFF' },

    sectionTitle: {
      fontSize: 13, fontFamily: 'PlusJakartaSans_700Bold', color: colors.textDim,
      letterSpacing: 0.5, textTransform: 'uppercase', marginBottom: 12,
    },
    reqRow: {
      flexDirection: 'row', alignItems: 'center', paddingVertical: 10,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    reqAmount: { fontSize: 15, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text },
    reqReason: { fontSize: 12, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 2 },
    reqBadge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 10 },
    reqBadgeText: { fontSize: 11, fontFamily: 'PlusJakartaSans_600SemiBold' },
  });
}
