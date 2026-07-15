import React, { useState, useEffect, useMemo, useContext } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList, TextInput,
  ActivityIndicator, KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { CardField, CardFieldInput, useStripe } from '@stripe/stripe-react-native';
import { StripeKeyContext } from './_layout';
import api, { getApiErrorMessage } from '@shared/api/client';
import ConfirmSheet from '../components/ConfirmSheet';
import { showToast } from '../store/toastStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface Card {
  id: string;
  brand: string;
  last4: string;
  exp_month: number;
  exp_year: number;
  is_default: boolean;
}

export default function ManageCardsScreen() {
  const router = useRouter();
  // When opened from a stuck ride-payment ("Change Card" escape), forPayment=1
  // and rideId is set: picking/adding a card bounces back to ride-completed to
  // re-charge that trip on the chosen card.
  const { rideId, forPayment, tip, rated } = useLocalSearchParams<{
    rideId?: string;
    forPayment?: string;
    tip?: string;
    rated?: string;
  }>();
  const payForRide = forPayment === '1' && !!rideId;
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const stripeKey = useContext(StripeKeyContext);
  const { createPaymentMethod } = useStripe();

  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [saving, setSaving] = useState(false);

  // Card form — PCI-DSS: we no longer hold PAN/CVC/expiry in JS state.
  // Stripe's <CardField> keeps raw card data inside its own native view;
  // we only see the tokenized result when the user taps "Add Card".
  // cardholder_name remains a plain input because it's not sensitive.
  const [cardDetailsComplete, setCardDetailsComplete] = useState(false);
  const [cardName, setCardName] = useState('');
  const [confirmState, setConfirmState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info', buttons: [] });

  useEffect(() => {
    fetchCards();
  }, []);

  const fetchCards = async () => {
    setLoading(true);
    try {
      const res = await api.get<Card[]>('/payments/cards');
      setCards((res.data as Card[]) || []);
    } catch {
      // No cards yet — show empty state
      setCards([]);
    } finally {
      setLoading(false);
    }
  };

  const getCardIcon = (brand: string) => {
    switch (brand.toLowerCase()) {
      case 'visa': return 'card';
      case 'mastercard': return 'card';
      case 'amex': return 'card';
      default: return 'card';
    }
  };

  const handleAddCard = async () => {
    if (!cardDetailsComplete) { showToast('Missing Details', 'Please enter complete card details', 'warning'); return; }
    if (!cardName.trim()) { showToast('Missing Name', 'Please enter the cardholder name', 'warning'); return; }
    if (!createPaymentMethod) {
      showToast('Payments unavailable', 'Payment processing is still starting up. Try again in a moment.', 'warning');
      return;
    }

    setSaving(true);
    try {
      const { paymentMethod, error } = await createPaymentMethod({
        paymentMethodType: 'Card',
        paymentMethodData: {
          billingDetails: { name: cardName.trim() },
        },
      });

      if (error || !paymentMethod) {
        showToast('Processing Failed', error?.message || 'Could not process card. Please try again.', 'danger');
        return;
      }

      await api.post('/payments/cards', { payment_method_id: paymentMethod.id });
      setShowAdd(false);
      resetForm();
      // Paying for a stuck ride: charge the freshly added card immediately.
      if (payForRide) {
        showToast('Card Added', 'Charging your ride…', 'success');
        payRideWithCard(paymentMethod.id);
        return;
      }
      fetchCards();
      showToast('Card Added', 'Card added successfully', 'success');
    } catch (err: any) {
      showToast('Card Not Added', getApiErrorMessage(err, 'Could not add card. Please try again.'), 'danger');
    } finally {
      setSaving(false);
    }
  };

  // Return to the stuck ride and re-charge it on the chosen card. Carry the tip
  // and rated flag through so the re-charge collects the same tip and doesn't
  // re-rate the driver (Codex 62i6).
  const payRideWithCard = (cardId: string) => {
    router.replace({
      pathname: '/ride-completed',
      params: {
        rideId: rideId as string,
        payWithCard: cardId,
        ...(typeof tip === 'string' ? { tip } : {}),
        ...(typeof rated === 'string' ? { rated } : {}),
      },
    } as any);
  };

  const handleSetDefault = async (cardId: string) => {
    try {
      await api.post(`/payments/cards/${cardId}/default`);
      fetchCards();
    } catch (err) {
      showToast('Update Failed', getApiErrorMessage(err, 'Could not set default card. Please try again.'), 'danger');
    }
  };

  const handleDeleteCard = (cardId: string) => {
    setConfirmState({
      visible: true,
      title: 'Remove Card',
      message: 'Are you sure you want to remove this card?',
      variant: 'warning',
      buttons: [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove', style: 'destructive',
          onPress: async () => {
            try {
              await api.delete(`/payments/cards/${cardId}`);
              fetchCards();
            } catch (err) {
              showToast('Remove Failed', getApiErrorMessage(err, 'Could not remove card. Please try again.'), 'danger');
            }
          },
        },
      ],
    });
  };

  const resetForm = () => {
    // CardField has no imperative reset API — remounting it on next open
    // gives us a clean field. We just clear the cardholder name and the
    // "complete" flag; the CardField instance is keyed on `showAdd` so
    // closing + re-opening the form re-mounts it blank.
    setCardDetailsComplete(false);
    setCardName('');
  };

  const renderCard = ({ item }: { item: Card }) => (
    <View style={[styles.cardItem, item.is_default && styles.cardItemDefault]}>
      <View style={styles.cardIcon}>
        <Ionicons name={getCardIcon(item.brand) as any} size={24} color={item.is_default ? colors.primary : colors.textDim} />
      </View>
      <View style={{ flex: 1 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={styles.cardBrand}>{item.brand}</Text>
          {item.is_default && (
            <View style={styles.defaultBadge}>
              <Text style={styles.defaultBadgeText}>DEFAULT</Text>
            </View>
          )}
        </View>
        <Text style={styles.cardLast4}>•••• •••• •••• {item.last4}</Text>
        <Text style={styles.cardExpiry}>Expires {String(item.exp_month).padStart(2, '0')}/{item.exp_year}</Text>
      </View>
      <View style={styles.cardActions}>
        {payForRide ? (
          <TouchableOpacity style={styles.payWithBtn} onPress={() => payRideWithCard(item.id)}>
            <Text style={styles.payWithText}>Use &amp; Pay</Text>
          </TouchableOpacity>
        ) : (
          !item.is_default && (
            <TouchableOpacity style={styles.setDefaultBtn} onPress={() => handleSetDefault(item.id)}>
              <Text style={styles.setDefaultText}>Set Default</Text>
            </TouchableOpacity>
          )
        )}
        <TouchableOpacity onPress={() => handleDeleteCard(item.id)}>
          <Ionicons name="trash-outline" size={20} color={colors.textDim} />
        </TouchableOpacity>
      </View>
    </View>
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>{payForRide ? 'Choose a Card' : 'Payment Methods'}</Text>
        <View style={{ width: 44 }} />
      </View>

      {payForRide && (
        <View style={styles.payBanner}>
          <Ionicons name="card" size={16} color={colors.primary} />
          <Text style={styles.payBannerText}>Pick or add a card to pay for your trip.</Text>
        </View>
      )}

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
          <FlatList
            data={cards}
            renderItem={renderCard}
            keyExtractor={(item) => item.id}
            contentContainerStyle={styles.list}
            ListEmptyComponent={
              <View style={styles.emptyState}>
                <Ionicons name="card-outline" size={48} color="#CCC" />
                <Text style={styles.emptyTitle}>No cards added</Text>
                <Text style={styles.emptySubtext}>Add a credit or debit card to pay for rides</Text>
              </View>
            }
            ListFooterComponent={
              showAdd ? (
                <View style={styles.addForm}>
                  <Text style={styles.addFormTitle}>Add New Card</Text>

                  <Text style={styles.inputLabel}>Card Details</Text>
                  {stripeKey ? (
                    <CardField
                      postalCodeEnabled={false}
                      placeholders={{ number: '4242 4242 4242 4242' }}
                      cardStyle={{
                        backgroundColor: '#FFFFFF',
                        textColor: '#1A1A1A',
                        placeholderColor: '#BBBBBB',
                        borderColor: '#ECECEC',
                        borderRadius: 12,
                        borderWidth: 1,
                      }}
                      style={styles.cardField}
                      onCardChange={(d: CardFieldInput.Details) => {
                        setCardDetailsComplete(Boolean(d.complete));
                      }}
                    />
                  ) : (
                    <View style={[styles.cardField, styles.cardFieldLoading]}>
                      <ActivityIndicator size="small" color="#999" />
                      <Text style={styles.cardFieldLoadingText}>Payment module loading…</Text>
                    </View>
                  )}

                  <Text style={styles.inputLabel}>Cardholder Name</Text>
                  <TextInput
                    style={styles.input}
                    placeholder="Name on card"
                    placeholderTextColor="#BBB"
                    value={cardName}
                    onChangeText={setCardName}
                    autoCapitalize="words"
                  />

                  <View style={styles.formButtons}>
                    <TouchableOpacity style={styles.cancelFormBtn} onPress={() => { setShowAdd(false); resetForm(); }}>
                      <Text style={styles.cancelFormText}>Cancel</Text>
                    </TouchableOpacity>
                    <TouchableOpacity style={styles.saveCardBtn} onPress={handleAddCard} disabled={saving}>
                      {saving ? (
                        <ActivityIndicator size="small" color="#FFF" />
                      ) : (
                        <Text style={styles.saveCardText}>Add Card</Text>
                      )}
                    </TouchableOpacity>
                  </View>

                  <View style={styles.securityNote}>
                    <Ionicons name="lock-closed" size={14} color={colors.textDim} />
                    <Text style={styles.securityText}>Card details are securely processed via Stripe</Text>
                  </View>
                </View>
              ) : (
                <TouchableOpacity style={styles.addCardBtn} onPress={() => setShowAdd(true)}>
                  <Ionicons name="add-circle" size={22} color={colors.primary} />
                  <Text style={styles.addCardText}>Add New Card</Text>
                </TouchableOpacity>
              )
            }
          />
        </KeyboardAvoidingView>
      )}
      <ConfirmSheet
        visible={confirmState.visible}
        title={confirmState.title}
        message={confirmState.message}
        variant={confirmState.variant}
        buttons={confirmState.buttons}
        onClose={() => setConfirmState(prev => ({ ...prev, visible: false }))}
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
    loadingWrap: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    list: { padding: 20 },

    // Card Item
    cardItem: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surfaceLight, borderRadius: 16, padding: 16, marginBottom: 12,
      borderWidth: 1.5, borderColor: 'transparent',
    },
    cardItemDefault: { borderColor: colors.primary, backgroundColor: '#FEF2F2' },
    cardIcon: {
      width: 48, height: 48, borderRadius: 12, backgroundColor: colors.surface,
      justifyContent: 'center', alignItems: 'center', marginRight: 14,
    },
    cardBrand: { fontSize: 15, fontWeight: '700', color: colors.text },
    cardLast4: { fontSize: 14, color: colors.textDim, marginTop: 2, letterSpacing: 1 },
    cardExpiry: { fontSize: 12, color: colors.textDim, marginTop: 1 },
    defaultBadge: {
      backgroundColor: colors.primary, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2,
    },
    defaultBadgeText: { fontSize: 9, fontWeight: '700', color: '#FFF', letterSpacing: 0.5 },
    cardActions: { alignItems: 'flex-end', gap: 8 },
    setDefaultBtn: {
      paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6,
      backgroundColor: colors.border,
    },
    setDefaultText: { fontSize: 11, fontWeight: '600', color: colors.textDim },
    payWithBtn: {
      paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8,
      backgroundColor: colors.primary,
    },
    payWithText: { fontSize: 12, fontWeight: '700', color: '#FFF' },
    payBanner: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      paddingHorizontal: 20, paddingVertical: 12,
      backgroundColor: '#FEF2F2',
    },
    payBannerText: { fontSize: 13, fontWeight: '600', color: colors.text },

    // Empty
    emptyState: { alignItems: 'center', paddingVertical: 40 },
    emptyTitle: { fontSize: 18, fontWeight: '700', color: colors.text, marginTop: 12 },
    emptySubtext: { fontSize: 14, color: colors.textDim, marginTop: 4 },

    // Add Card Button
    addCardBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      paddingVertical: 16, borderRadius: 14, borderWidth: 2, borderColor: colors.primary,
      borderStyle: 'dashed', marginTop: 8,
    },
    addCardText: { fontSize: 15, fontWeight: '700', color: colors.primary },

    // Add Form
    addForm: {
      backgroundColor: colors.surfaceLight, borderRadius: 18, padding: 20, marginTop: 8,
    },
    addFormTitle: { fontSize: 17, fontWeight: '700', color: colors.text, marginBottom: 16 },
    inputLabel: { fontSize: 12, fontWeight: '600', color: '#888', marginBottom: 6, marginTop: 12 },
    input: {
      backgroundColor: colors.surface, borderRadius: 12, paddingHorizontal: 16, paddingVertical: 14,
      fontSize: 16, fontWeight: '500', color: colors.text,
      borderWidth: 1, borderColor: colors.border,
    },
    inputRow: { flexDirection: 'row', gap: 12 },
    brandHint: { fontSize: 12, color: colors.primary, fontWeight: '600', marginTop: 4 },
    cardField: {
      height: 52,
      marginBottom: 4,
    },
    cardFieldLoading: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      backgroundColor: colors.surface, borderRadius: 12, borderWidth: 1, borderColor: colors.border,
    },
    cardFieldLoadingText: { fontSize: 13, color: colors.textDim },
    formButtons: { flexDirection: 'row', gap: 12, marginTop: 20 },
    cancelFormBtn: {
      flex: 1, alignItems: 'center', paddingVertical: 14, borderRadius: 12,
      backgroundColor: colors.border,
    },
    cancelFormText: { fontSize: 15, fontWeight: '600', color: colors.textDim },
    saveCardBtn: {
      flex: 2, alignItems: 'center', paddingVertical: 14, borderRadius: 12,
      backgroundColor: colors.primary,
    },
    saveCardText: { fontSize: 15, fontWeight: '700', color: '#FFF' },
    securityNote: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
      marginTop: 14,
    },
    securityText: { fontSize: 11, color: colors.textDim },
  });
}
