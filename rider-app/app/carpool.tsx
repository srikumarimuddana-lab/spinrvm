import React, { useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView,
  TextInput, ActivityIndicator, KeyboardAvoidingView, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useWalletStore } from '../store/walletStore';
import { useRideStore } from '../store/rideStore';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { SpinrConfig } from '@shared/config/spinr.config';

const MAX_PARTICIPANTS = 4;
const PHONE_REGEX = /^\+?1?\s?\(?[0-9]{3}\)?[\s.-]?[0-9]{3}[\s.-]?[0-9]{4}$/;

function formatPhone(raw: string): string {
  const digits = raw.replace(/\D/g, '').slice(-10);
  if (digits.length === 10) {
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
  }
  return raw;
}

export default function CarpoolScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId?: string }>();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const { currentRide } = useRideStore();
  const { createFareSplit, currentSplit, isLoading } = useWalletStore();

  const [phones, setPhones] = useState<string[]>(['']);
  const [splitCreated, setSplitCreated] = useState(false);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const activeRideId = rideId ?? currentRide?.id;
  const totalFare = currentRide?.total_fare ?? 0;
  const participantCount = phones.filter(p => p.trim()).length + 1; // +1 for initiating rider
  const sharePerPerson = participantCount > 0 ? totalFare / participantCount : totalFare;

  const addPhone = () => {
    if (phones.length < MAX_PARTICIPANTS - 1) {
      setPhones(prev => [...prev, '']);
    }
  };

  const removePhone = (idx: number) => {
    setPhones(prev => prev.filter((_, i) => i !== idx));
  };

  const updatePhone = (idx: number, value: string) => {
    setPhones(prev => {
      const next = [...prev];
      next[idx] = value;
      return next;
    });
  };

  const handleCreateSplit = async () => {
    if (!activeRideId) {
      setAlertState({ visible: true, title: 'No Active Ride', message: 'Carpool splits require an active ride.', variant: 'warning' });
      return;
    }

    const validPhones = phones.filter(p => p.trim());
    if (validPhones.length === 0) {
      setAlertState({ visible: true, title: 'Add Participants', message: 'Add at least one phone number to split with.', variant: 'warning' });
      return;
    }

    const invalidPhone = validPhones.find(p => !PHONE_REGEX.test(p.replace(/\s/g, '')));
    if (invalidPhone) {
      setAlertState({ visible: true, title: 'Invalid Phone', message: `"${invalidPhone}" is not a valid Canadian phone number.`, variant: 'danger' });
      return;
    }

    try {
      const split = await createFareSplit(activeRideId, validPhones);
      setSplitCreated(true);
      setAlertState({
        visible: true,
        title: 'Carpool Split Created!',
        message: `Each of the ${split.split_count} participants will receive a payment request for $${split.your_share.toFixed(2)}.`,
        variant: 'success',
        buttons: [{ text: 'Done', onPress: () => router.back() }],
      });
    } catch (err: any) {
      setAlertState({ visible: true, title: 'Error', message: err.message || 'Failed to create fare split.', variant: 'danger' });
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Carpool & Split Fare</Text>
        <View style={{ width: 44 }} />
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">

          {/* Hero */}
          <View style={styles.heroCard}>
            <View style={styles.heroIconWrap}>
              <Ionicons name="people" size={32} color={colors.primary} />
            </View>
            <Text style={styles.heroTitle}>Split This Ride</Text>
            <Text style={styles.heroSubtitle}>
              Invite friends to share the cost. Each participant pays their share via Spinr Wallet or card.
            </Text>
          </View>

          {/* Fare Breakdown */}
          <View style={styles.fareCard}>
            <Text style={styles.fareTitle}>Fare Breakdown</Text>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Total fare</Text>
              <Text style={styles.fareValue}>${totalFare.toFixed(2)} CAD</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Participants</Text>
              <Text style={styles.fareValue}>{participantCount} {participantCount === 1 ? 'person' : 'people'}</Text>
            </View>
            <View style={[styles.fareRow, styles.fareTotalRow]}>
              <Text style={styles.fareTotalLabel}>Each person pays</Text>
              <Text style={styles.fareTotalValue}>${sharePerPerson.toFixed(2)}</Text>
            </View>
          </View>

          {/* Participant phones */}
          <Text style={styles.sectionTitle}>Invite Participants</Text>
          <Text style={styles.sectionSub}>
            Add up to {MAX_PARTICIPANTS - 1} phone numbers. Each person will receive a payment request.
          </Text>

          {phones.map((phone, idx) => (
            <View key={idx} style={styles.phoneRow}>
              <View style={[styles.phoneIcon, { backgroundColor: `${colors.primary}15` }]}>
                <Ionicons name="person-add" size={18} color={colors.primary} />
              </View>
              <TextInput
                style={styles.phoneInput}
                placeholder={`Participant ${idx + 1} phone (306) 555-xxxx`}
                placeholderTextColor={colors.textDim}
                value={phone}
                onChangeText={(v) => updatePhone(idx, v)}
                keyboardType="phone-pad"
                maxLength={20}
              />
              {phones.length > 1 && (
                <TouchableOpacity onPress={() => removePhone(idx)} style={styles.removeBtn}>
                  <Ionicons name="close-circle" size={22} color={colors.textDim} />
                </TouchableOpacity>
              )}
            </View>
          ))}

          {phones.length < MAX_PARTICIPANTS - 1 && (
            <TouchableOpacity style={styles.addPersonBtn} onPress={addPhone}>
              <Ionicons name="add-circle-outline" size={20} color={colors.primary} />
              <Text style={styles.addPersonText}>Add another participant</Text>
            </TouchableOpacity>
          )}

          {/* How it works */}
          <View style={styles.howItWorks}>
            <Text style={styles.howTitle}>How it works</Text>
            {[
              { icon: 'send-outline',         text: 'Participants receive a payment request via SMS' },
              { icon: 'card-outline',          text: 'They pay their share using Spinr Wallet or card' },
              { icon: 'checkmark-circle-outline', text: 'Once all shares are paid, the ride is fully settled' },
            ].map((step, i) => (
              <View key={i} style={styles.howRow}>
                <View style={styles.howIconWrap}>
                  <Ionicons name={step.icon as any} size={18} color={colors.primary} />
                </View>
                <Text style={styles.howText}>{step.text}</Text>
              </View>
            ))}
          </View>

          {/* CTA */}
          <TouchableOpacity
            style={[styles.createBtn, (isLoading || splitCreated) && styles.createBtnDisabled]}
            onPress={handleCreateSplit}
            disabled={isLoading || splitCreated}
            activeOpacity={0.8}
          >
            {isLoading ? (
              <ActivityIndicator color="#FFF" />
            ) : (
              <>
                <Ionicons name="people" size={20} color="#FFF" />
                <Text style={styles.createBtnText}>
                  {splitCreated ? 'Split Created ✓' : `Split with ${participantCount} People`}
                </Text>
              </>
            )}
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>

      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },

    content: { padding: 20, paddingBottom: 40 },

    heroCard: {
      alignItems: 'center', backgroundColor: colors.surfaceLight,
      borderRadius: 20, padding: 24, marginBottom: 16,
    },
    heroIconWrap: {
      width: 72, height: 72, borderRadius: 36,
      backgroundColor: `${colors.primary}15`,
      justifyContent: 'center', alignItems: 'center', marginBottom: 14,
    },
    heroTitle: { fontSize: 20, fontWeight: '800', color: colors.text, marginBottom: 8 },
    heroSubtitle: { fontSize: 14, color: colors.textSecondary, textAlign: 'center', lineHeight: 20 },

    fareCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 16, padding: 16, marginBottom: 20,
    },
    fareTitle: { fontSize: 13, fontWeight: '700', color: colors.textDim, letterSpacing: 0.5, marginBottom: 12 },
    fareRow: {
      flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 5,
    },
    fareLabel: { fontSize: 14, color: colors.textSecondary },
    fareValue: { fontSize: 14, fontWeight: '600', color: colors.text },
    fareTotalRow: {
      marginTop: 8, paddingTop: 12,
      borderTopWidth: 1, borderTopColor: colors.border,
    },
    fareTotalLabel: { fontSize: 16, fontWeight: '700', color: colors.text },
    fareTotalValue: { fontSize: 20, fontWeight: '800', color: colors.primary },

    sectionTitle: { fontSize: 16, fontWeight: '700', color: colors.text, marginBottom: 4 },
    sectionSub: { fontSize: 13, color: colors.textDim, marginBottom: 16, lineHeight: 18 },

    phoneRow: {
      flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10,
    },
    phoneIcon: {
      width: 42, height: 42, borderRadius: 12, justifyContent: 'center', alignItems: 'center',
    },
    phoneInput: {
      flex: 1, backgroundColor: colors.surfaceLight, borderRadius: 12, paddingHorizontal: 14,
      paddingVertical: 12, fontSize: 15, color: colors.text,
      borderWidth: 1, borderColor: colors.border,
    },
    removeBtn: { padding: 4 },

    addPersonBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 12, marginBottom: 20,
    },
    addPersonText: { fontSize: 15, fontWeight: '600', color: colors.primary },

    howItWorks: {
      backgroundColor: colors.surfaceLight, borderRadius: 16, padding: 16, marginBottom: 24,
    },
    howTitle: { fontSize: 13, fontWeight: '700', color: colors.textDim, letterSpacing: 0.5, marginBottom: 12 },
    howRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, marginBottom: 12 },
    howIconWrap: {
      width: 36, height: 36, borderRadius: 10,
      backgroundColor: `${colors.primary}15`, justifyContent: 'center', alignItems: 'center',
    },
    howText: { flex: 1, fontSize: 14, color: colors.textSecondary, lineHeight: 20 },

    createBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10,
      backgroundColor: colors.primary, paddingVertical: 16, borderRadius: 28,
    },
    createBtnDisabled: { opacity: 0.6 },
    createBtnText: { fontSize: 17, fontWeight: '700', color: '#FFF' },
  });
}
