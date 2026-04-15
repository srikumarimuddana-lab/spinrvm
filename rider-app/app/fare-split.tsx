import React, { useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, TextInput,
  ActivityIndicator, FlatList, KeyboardAvoidingView, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useWalletStore } from '../store/walletStore';
import { useRideStore } from '../store/rideStore';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function FareSplitScreen() {
  const router = useRouter();
  const { currentRide, estimates, selectedVehicle } = useRideStore();
  const { createFareSplit, currentSplit, isLoading } = useWalletStore();
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [phones, setPhones] = useState<string[]>(['']);
  const [alertState, setAlertState] = useState<{
    visible: boolean; title: string; message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const selectedEstimate = estimates.find((e) => e.vehicle_type.id === selectedVehicle?.id);
  const totalFare = (selectedEstimate as any)?.grand_total || selectedEstimate?.total_fare || 0;
  const splitCount = phones.filter(p => p.trim().length > 0).length + 1;
  const shareAmount = splitCount > 1 ? totalFare / splitCount : totalFare;

  const addPhone = () => {
    if (phones.length < 5) {
      setPhones([...phones, '']);
    }
  };

  const removePhone = (index: number) => {
    setPhones(phones.filter((_, i) => i !== index));
  };

  const updatePhone = (index: number, value: string) => {
    const updated = [...phones];
    updated[index] = value;
    setPhones(updated);
  };

  const handleSplit = async () => {
    const validPhones = phones.filter(p => p.trim().length >= 10);
    if (validPhones.length === 0) {
      setAlertState({
        visible: true, title: 'Add Contacts',
        message: 'Add at least one phone number to split the fare with.',
        variant: 'warning',
      });
      return;
    }

    const rideId = currentRide?.id;
    if (!rideId) {
      setAlertState({
        visible: true, title: 'No Active Ride',
        message: 'You can split the fare after booking a ride.',
        variant: 'info',
      });
      return;
    }

    try {
      await createFareSplit(rideId, validPhones);
      setAlertState({
        visible: true, title: 'Fare Split Created',
        message: `Split request sent to ${validPhones.length} contact${validPhones.length > 1 ? 's' : ''}. Each person pays $${shareAmount.toFixed(2)}.`,
        variant: 'success',
      });
    } catch (err: any) {
      setAlertState({
        visible: true, title: 'Split Failed',
        message: err.message || 'Could not create fare split',
        variant: 'danger',
      });
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Split Fare</Text>
        <View style={{ width: 44 }} />
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        {/* Split Summary */}
        <View style={styles.summaryCard}>
          <Text style={styles.summaryLabel}>Total Fare</Text>
          <Text style={styles.summaryAmount}>${totalFare.toFixed(2)}</Text>

          <View style={styles.splitPreview}>
            <View style={styles.splitDivider} />
            <View style={styles.splitInfo}>
              <Text style={styles.splitInfoLabel}>Split {splitCount} ways</Text>
              <Text style={styles.splitInfoAmount}>${shareAmount.toFixed(2)} each</Text>
            </View>
          </View>
        </View>

        {/* Contact Inputs */}
        <View style={styles.contactsSection}>
          <Text style={styles.sectionTitle}>Add people to split with</Text>

          {phones.map((phone, index) => (
            <View key={index} style={styles.phoneRow}>
              <View style={styles.phoneIconWrap}>
                <Ionicons name="person" size={18} color={colors.primary} />
              </View>
              <TextInput
                style={styles.phoneInput}
                placeholder="Phone number"
                placeholderTextColor={colors.textDim}
                keyboardType="phone-pad"
                value={phone}
                onChangeText={(val) => updatePhone(index, val)}
              />
              {phones.length > 1 && (
                <TouchableOpacity onPress={() => removePhone(index)} style={styles.removeButton}>
                  <Ionicons name="close-circle" size={22} color="#EF4444" />
                </TouchableOpacity>
              )}
            </View>
          ))}

          {phones.length < 5 && (
            <TouchableOpacity style={styles.addButton} onPress={addPhone}>
              <Ionicons name="add-circle-outline" size={22} color={colors.primary} />
              <Text style={styles.addButtonText}>Add another person</Text>
            </TouchableOpacity>
          )}
        </View>

        {/* Info Banner */}
        <View style={styles.infoBanner}>
          <Ionicons name="information-circle" size={20} color="#6B7280" />
          <Text style={styles.infoText}>
            Each person will receive a notification to accept and pay their share via wallet or card.
          </Text>
        </View>
      </KeyboardAvoidingView>

      {/* Split Button */}
      <View style={styles.footer}>
        <TouchableOpacity
          style={styles.splitButton}
          onPress={handleSplit}
          disabled={isLoading}
          activeOpacity={0.8}
        >
          {isLoading ? (
            <ActivityIndicator color="#FFF" />
          ) : (
            <>
              <Ionicons name="people" size={20} color="#FFF" />
              <Text style={styles.splitButtonText}>
                Send Split Request (${shareAmount.toFixed(2)} each)
              </Text>
            </>
          )}
        </TouchableOpacity>
      </View>

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
    backButton: {
      width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surfaceLight,
      alignItems: 'center', justifyContent: 'center',
    },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },

    summaryCard: {
      margin: 16, backgroundColor: colors.surface, borderRadius: 16, padding: 24,
      alignItems: 'center',
      shadowColor: '#000', shadowOpacity: 0.05, shadowOffset: { width: 0, height: 2 }, shadowRadius: 8,
      elevation: 2,
    },
    summaryLabel: { fontSize: 14, color: colors.textDim },
    summaryAmount: { fontSize: 36, fontWeight: '800', color: colors.text, marginVertical: 4 },
    splitPreview: { flexDirection: 'row', alignItems: 'center', marginTop: 16, gap: 12 },
    splitDivider: { width: 40, height: 2, backgroundColor: colors.primary, borderRadius: 1 },
    splitInfo: { alignItems: 'center' },
    splitInfoLabel: { fontSize: 14, color: colors.textDim },
    splitInfoAmount: { fontSize: 20, fontWeight: '700', color: colors.primary },

    contactsSection: { paddingHorizontal: 16 },
    sectionTitle: { fontSize: 16, fontWeight: '700', color: colors.text, marginBottom: 12 },

    phoneRow: {
      flexDirection: 'row', alignItems: 'center', marginBottom: 10,
      backgroundColor: colors.surface, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 4,
      borderWidth: 1, borderColor: colors.border,
    },
    phoneIconWrap: {
      width: 36, height: 36, borderRadius: 18,
      backgroundColor: colors.primary + '15', alignItems: 'center', justifyContent: 'center',
      marginRight: 10,
    },
    phoneInput: { flex: 1, fontSize: 16, color: colors.text, paddingVertical: 14 },
    removeButton: { padding: 4 },

    addButton: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      paddingVertical: 12, marginTop: 4,
    },
    addButtonText: { fontSize: 15, fontWeight: '600', color: colors.primary },

    infoBanner: {
      flexDirection: 'row', alignItems: 'flex-start', gap: 10,
      margin: 16, padding: 14, backgroundColor: '#F0F4FF', borderRadius: 12,
    },
    infoText: { flex: 1, fontSize: 13, color: '#6B7280', lineHeight: 18 },

    footer: {
      backgroundColor: colors.surface, paddingHorizontal: 20, paddingVertical: 16,
      borderTopWidth: 1, borderTopColor: colors.border,
    },
    splitButton: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      backgroundColor: colors.primary, borderRadius: 28, paddingVertical: 18, gap: 8,
    },
    splitButtonText: { fontSize: 16, fontWeight: '700', color: '#FFF' },
  });
}
