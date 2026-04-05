import React, { useState, useEffect } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList, TextInput,
  Platform, ActivityIndicator, KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';

const COLORS = SpinrConfig.theme.colors;

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
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [saving, setSaving] = useState(false);

  // Card form
  const [cardNumber, setCardNumber] = useState('');
  const [expiry, setExpiry] = useState('');
  const [cvc, setCvc] = useState('');
  const [cardName, setCardName] = useState('');
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  useEffect(() => {
    fetchCards();
  }, []);

  const fetchCards = async () => {
    setLoading(true);
    try {
      const res = await api.get('/payments/cards');
      setCards(res.data || []);
    } catch {
      // No cards yet — show empty state
      setCards([]);
    } finally {
      setLoading(false);
    }
  };

  const formatCardNumber = (text: string) => {
    const cleaned = text.replace(/\D/g, '').slice(0, 16);
    return cleaned.replace(/(\d{4})(?=\d)/g, '$1 ');
  };

  const formatExpiry = (text: string) => {
    const cleaned = text.replace(/\D/g, '').slice(0, 4);
    if (cleaned.length >= 3) return `${cleaned.slice(0, 2)}/${cleaned.slice(2)}`;
    return cleaned;
  };

  const getCardBrand = (num: string) => {
    const n = num.replace(/\s/g, '');
    if (/^4/.test(n)) return 'Visa';
    if (/^5[1-5]/.test(n)) return 'Mastercard';
    if (/^3[47]/.test(n)) return 'Amex';
    if (/^6/.test(n)) return 'Discover';
    return 'Card';
  };

  const getCardIcon = (brand: string) => {
    switch (brand.toLowerCase()) {
      case 'visa': return 'card';
      case 'mastercard': return 'card';
      case 'amex': return 'card';
      default: return 'card';
    }
  };

  const showAlert = (title: string, message: string, variant: 'info' | 'warning' | 'danger' | 'success' = 'warning') => {
    setAlertState({ visible: true, title, message, variant });
  };

  const handleAddCard = async () => {
    const num = cardNumber.replace(/\s/g, '');
    if (num.length < 15) { showAlert('Error', 'Enter a valid card number'); return; }
    if (expiry.length < 5) { showAlert('Error', 'Enter expiry as MM/YY'); return; }
    if (cvc.length < 3) { showAlert('Error', 'Enter a valid CVC'); return; }
    if (!cardName.trim()) { showAlert('Error', 'Enter cardholder name'); return; }

    setSaving(true);
    try {
      await api.post('/payments/cards', {
        card_number: num,
        exp_month: parseInt(expiry.split('/')[0]),
        exp_year: parseInt('20' + expiry.split('/')[1]),
        cvc,
        cardholder_name: cardName.trim(),
      });
      setShowAdd(false);
      resetForm();
      fetchCards();
      showAlert('Success', 'Card added successfully', 'success');
    } catch (err: any) {
      showAlert('Error', err.response?.data?.detail || 'Failed to add card', 'danger');
    } finally {
      setSaving(false);
    }
  };

  const handleSetDefault = async (cardId: string) => {
    try {
      await api.post(`/payments/cards/${cardId}/default`);
      fetchCards();
    } catch {
      showAlert('Error', 'Failed to set default card', 'danger');
    }
  };

  const handleDeleteCard = (cardId: string) => {
    setAlertState({
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
            } catch {
              showAlert('Error', 'Failed to remove card', 'danger');
            }
          },
        },
      ],
    });
  };

  const resetForm = () => {
    setCardNumber(''); setExpiry(''); setCvc(''); setCardName('');
  };

  const renderCard = ({ item }: { item: Card }) => (
    <View style={[styles.cardItem, item.is_default && styles.cardItemDefault]}>
      <View style={styles.cardIcon}>
        <Ionicons name={getCardIcon(item.brand) as any} size={24} color={item.is_default ? COLORS.primary : '#666'} />
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
        {!item.is_default && (
          <TouchableOpacity style={styles.setDefaultBtn} onPress={() => handleSetDefault(item.id)}>
            <Text style={styles.setDefaultText}>Set Default</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={() => handleDeleteCard(item.id)}>
          <Ionicons name="trash-outline" size={20} color="#999" />
        </TouchableOpacity>
      </View>
    </View>
  );

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Payment Methods</Text>
        <View style={{ width: 44 }} />
      </View>

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="large" color={COLORS.primary} />
        </View>
      ) : (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
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

                  <Text style={styles.inputLabel}>Card Number</Text>
                  <TextInput
                    style={styles.input}
                    placeholder="1234 5678 9012 3456"
                    placeholderTextColor="#BBB"
                    value={cardNumber}
                    onChangeText={(t) => setCardNumber(formatCardNumber(t))}
                    keyboardType="number-pad"
                    maxLength={19}
                  />
                  {cardNumber.length > 4 && (
                    <Text style={styles.brandHint}>{getCardBrand(cardNumber)}</Text>
                  )}

                  <View style={styles.inputRow}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.inputLabel}>Expiry</Text>
                      <TextInput
                        style={styles.input}
                        placeholder="MM/YY"
                        placeholderTextColor="#BBB"
                        value={expiry}
                        onChangeText={(t) => setExpiry(formatExpiry(t))}
                        keyboardType="number-pad"
                        maxLength={5}
                      />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.inputLabel}>CVC</Text>
                      <TextInput
                        style={styles.input}
                        placeholder="123"
                        placeholderTextColor="#BBB"
                        value={cvc}
                        onChangeText={setCvc}
                        keyboardType="number-pad"
                        maxLength={4}
                        secureTextEntry
                      />
                    </View>
                  </View>

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
                    <Ionicons name="lock-closed" size={14} color="#999" />
                    <Text style={styles.securityText}>Card details are securely processed via Stripe</Text>
                  </View>
                </View>
              ) : (
                <TouchableOpacity style={styles.addCardBtn} onPress={() => setShowAdd(true)}>
                  <Ionicons name="add-circle" size={22} color={COLORS.primary} />
                  <Text style={styles.addCardText}>Add New Card</Text>
                </TouchableOpacity>
              )
            }
          />
        </KeyboardAvoidingView>
      )}
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

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFF' },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 12,
    borderBottomWidth: 1, borderBottomColor: '#F0F0F0',
  },
  backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
  headerTitle: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },
  loadingWrap: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  list: { padding: 20 },

  // Card Item
  cardItem: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#F9F9F9', borderRadius: 16, padding: 16, marginBottom: 12,
    borderWidth: 1.5, borderColor: 'transparent',
  },
  cardItemDefault: { borderColor: COLORS.primary, backgroundColor: '#FEF2F2' },
  cardIcon: {
    width: 48, height: 48, borderRadius: 12, backgroundColor: '#FFF',
    justifyContent: 'center', alignItems: 'center', marginRight: 14,
  },
  cardBrand: { fontSize: 15, fontWeight: '700', color: '#1A1A1A' },
  cardLast4: { fontSize: 14, color: '#666', marginTop: 2, letterSpacing: 1 },
  cardExpiry: { fontSize: 12, color: '#999', marginTop: 1 },
  defaultBadge: {
    backgroundColor: COLORS.primary, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2,
  },
  defaultBadgeText: { fontSize: 9, fontWeight: '700', color: '#FFF', letterSpacing: 0.5 },
  cardActions: { alignItems: 'flex-end', gap: 8 },
  setDefaultBtn: {
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6,
    backgroundColor: '#F0F0F0',
  },
  setDefaultText: { fontSize: 11, fontWeight: '600', color: '#666' },

  // Empty
  emptyState: { alignItems: 'center', paddingVertical: 40 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: '#1A1A1A', marginTop: 12 },
  emptySubtext: { fontSize: 14, color: '#999', marginTop: 4 },

  // Add Card Button
  addCardBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    paddingVertical: 16, borderRadius: 14, borderWidth: 2, borderColor: COLORS.primary,
    borderStyle: 'dashed', marginTop: 8,
  },
  addCardText: { fontSize: 15, fontWeight: '700', color: COLORS.primary },

  // Add Form
  addForm: {
    backgroundColor: '#F9F9F9', borderRadius: 18, padding: 20, marginTop: 8,
  },
  addFormTitle: { fontSize: 17, fontWeight: '700', color: '#1A1A1A', marginBottom: 16 },
  inputLabel: { fontSize: 12, fontWeight: '600', color: '#888', marginBottom: 6, marginTop: 12 },
  input: {
    backgroundColor: '#FFF', borderRadius: 12, paddingHorizontal: 16, paddingVertical: 14,
    fontSize: 16, fontWeight: '500', color: '#1A1A1A',
    borderWidth: 1, borderColor: '#ECECEC',
  },
  inputRow: { flexDirection: 'row', gap: 12 },
  brandHint: { fontSize: 12, color: COLORS.primary, fontWeight: '600', marginTop: 4 },
  formButtons: { flexDirection: 'row', gap: 12, marginTop: 20 },
  cancelFormBtn: {
    flex: 1, alignItems: 'center', paddingVertical: 14, borderRadius: 12,
    backgroundColor: '#F0F0F0',
  },
  cancelFormText: { fontSize: 15, fontWeight: '600', color: '#666' },
  saveCardBtn: {
    flex: 2, alignItems: 'center', paddingVertical: 14, borderRadius: 12,
    backgroundColor: COLORS.primary,
  },
  saveCardText: { fontSize: 15, fontWeight: '700', color: '#FFF' },
  securityNote: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
    marginTop: 14,
  },
  securityText: { fontSize: 11, color: '#999' },
});
