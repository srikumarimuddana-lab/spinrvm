import React, { useState, useEffect, useMemo, useContext } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, FlatList, TextInput,
  ActivityIndicator, KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons, FontAwesome, MaterialCommunityIcons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
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
  cardholder_name?: string | null;
}

/**
 * Per-brand card-face styling. `gradient` is the diagonal fill for the card
 * face; `logo` maps to a FontAwesome brand glyph (`cc-*`); `label` is the
 * human-readable brand name shown on the face. Unknown brands fall back to a
 * neutral graphite face with a generic chip-card glyph.
 */
type BrandStyle = {
  gradient: [string, string, string];
  logo: React.ComponentProps<typeof FontAwesome>['name'];
  label: string;
};

const BRAND_STYLES: Record<string, BrandStyle> = {
  visa:            { gradient: ['#1A1F71', '#2A3FA0', '#0F1451'], logo: 'cc-visa',        label: 'VISA' },
  mastercard:      { gradient: ['#232526', '#3a1c1c', '#141414'], logo: 'cc-mastercard',  label: 'Mastercard' },
  amex:            { gradient: ['#0077A0', '#00A9CE', '#005B7F'], logo: 'cc-amex',         label: 'Amex' },
  'american express': { gradient: ['#0077A0', '#00A9CE', '#005B7F'], logo: 'cc-amex',      label: 'Amex' },
  discover:        { gradient: ['#4B4B4B', '#F76B1C', '#2E2E2E'], logo: 'cc-discover',     label: 'Discover' },
  jcb:             { gradient: ['#0B4DA2', '#1C7C3C', '#8E1B1B'], logo: 'cc-jcb',          label: 'JCB' },
  diners:          { gradient: ['#004C97', '#0076CE', '#00325F'], logo: 'cc-diners-club',  label: 'Diners' },
  'diners club':   { gradient: ['#004C97', '#0076CE', '#00325F'], logo: 'cc-diners-club',  label: 'Diners' },
  unionpay:        { gradient: ['#0A4A9E', '#D01F2E', '#0A7A3E'], logo: 'credit-card',     label: 'UnionPay' },
};

const DEFAULT_BRAND_STYLE: BrandStyle = {
  gradient: ['#434343', '#2b2b2b', '#000000'],
  logo: 'credit-card',
  label: 'Card',
};

const brandStyle = (brand: string): BrandStyle =>
  BRAND_STYLES[(brand || '').trim().toLowerCase()] ?? DEFAULT_BRAND_STYLE;

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
  const { colors, isDark } = useTheme();
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

  const handleDeleteCard = (card: Card) => {
    // Any card can be removed. If this is the default and other cards remain,
    // the backend auto-promotes the most recently added card to default, so
    // the user is never left with cards but no default. Removing the only card
    // simply empties the wallet.
    const isDefaultWithOthers = card.is_default && cards.length > 1;
    setConfirmState({
      visible: true,
      title: 'Remove Card',
      message: isDefaultWithOthers
        ? 'This is your default card. Another card will become your default after it is removed.'
        : 'Are you sure you want to remove this card?',
      variant: 'warning',
      buttons: [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove', style: 'destructive',
          onPress: async () => {
            try {
              await api.delete(`/payments/cards/${card.id}`);
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

  /** The realistic gradient card face — the visual centrepiece of each row. */
  const renderCardFace = (item: Card) => {
    const bs = brandStyle(item.brand);
    return (
      <LinearGradient
        colors={bs.gradient}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.cardFace}
      >
        {/* Decorative sheen circles for depth */}
        <View style={styles.sheenTop} pointerEvents="none" />
        <View style={styles.sheenBottom} pointerEvents="none" />

        {/* Top row: contactless + default pill */}
        <View style={styles.faceTop}>
          <MaterialCommunityIcons name="contactless-payment" size={26} color="rgba(255,255,255,0.85)" />
          {item.is_default && (
            <View style={styles.defaultPill}>
              <Ionicons name="star" size={10} color="#1A1A1A" />
              <Text style={styles.defaultPillText}>DEFAULT</Text>
            </View>
          )}
        </View>

        {/* EMV chip */}
        <View style={styles.chip}>
          <View style={styles.chipLine} />
          <View style={styles.chipLineH} />
        </View>

        {/* Masked number */}
        <Text style={styles.faceNumber}>
          ••••  ••••  ••••  <Text style={styles.faceNumberLast4}>{item.last4}</Text>
        </Text>

        {/* Bottom row: holder / expiry / brand logo */}
        <View style={styles.faceBottom}>
          <View style={styles.faceBottomLeft}>
            <Text style={styles.faceMetaLabel}>CARD HOLDER</Text>
            <Text style={styles.faceMetaValue} numberOfLines={1}>
              {(item.cardholder_name || '').trim().toUpperCase() || 'SPINR RIDER'}
            </Text>
          </View>
          <View style={styles.faceExpiry}>
            <Text style={styles.faceMetaLabel}>EXPIRES</Text>
            <Text style={styles.faceMetaValue}>
              {String(item.exp_month).padStart(2, '0')}/{String(item.exp_year).slice(-2)}
            </Text>
          </View>
          <FontAwesome name={bs.logo} size={38} color="#FFFFFF" style={styles.faceLogo} />
        </View>
      </LinearGradient>
    );
  };

  const renderCard = ({ item }: { item: Card }) => (
    <View style={styles.cardRow}>
      {renderCardFace(item)}

      {/* Action strip beneath the card face */}
      <View style={styles.actionStrip}>
        <Text style={styles.actionBrand}>
          {brandStyle(item.brand).label} •••• {item.last4}
        </Text>
        <View style={styles.actionButtons}>
          {payForRide ? (
            <TouchableOpacity style={styles.payWithBtn} onPress={() => payRideWithCard(item.id)} activeOpacity={0.85}>
              <Ionicons name="flash" size={14} color="#FFF" />
              <Text style={styles.payWithText}>Use &amp; Pay</Text>
            </TouchableOpacity>
          ) : (
            !item.is_default && (
              <TouchableOpacity style={styles.setDefaultBtn} onPress={() => handleSetDefault(item.id)} activeOpacity={0.7}>
                <Ionicons name="star-outline" size={13} color={colors.primary} />
                <Text style={styles.setDefaultText}>Set Default</Text>
              </TouchableOpacity>
            )
          )}
          <TouchableOpacity style={styles.deleteBtn} onPress={() => handleDeleteCard(item)} activeOpacity={0.7}>
            <Ionicons name="trash-outline" size={18} color={colors.error} />
          </TouchableOpacity>
        </View>
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
        <Text style={styles.headerTitle}>{payForRide ? 'Choose a Card' : 'Wallet'}</Text>
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
            showsVerticalScrollIndicator={false}
            ListHeaderComponent={
              !payForRide && cards.length > 0 ? (
                <View style={styles.listHeader}>
                  <Text style={styles.listHeaderTitle}>Your cards</Text>
                  <Text style={styles.listHeaderSub}>
                    {cards.length} saved • tap a card to manage it
                  </Text>
                </View>
              ) : null
            }
            ListEmptyComponent={
              <View style={styles.emptyState}>
                {/* Ghost card illustration */}
                <View style={styles.ghostCard}>
                  <View style={styles.ghostChip} />
                  <View style={[styles.ghostLine, { width: '70%' }]} />
                  <View style={[styles.ghostLine, { width: '45%', marginTop: 8 }]} />
                  <MaterialCommunityIcons
                    name="credit-card-plus-outline"
                    size={30}
                    color={colors.primary}
                    style={styles.ghostPlus}
                  />
                </View>
                <Text style={styles.emptyTitle}>No cards yet</Text>
                <Text style={styles.emptySubtext}>
                  Add a credit or debit card for a faster, cashless checkout on every ride.
                </Text>
              </View>
            }
            ListFooterComponent={
              showAdd ? (
                <View style={styles.addForm}>
                  <View style={styles.addFormHeader}>
                    <MaterialCommunityIcons name="credit-card-plus" size={20} color={colors.primary} />
                    <Text style={styles.addFormTitle}>Add New Card</Text>
                  </View>

                  <Text style={styles.inputLabel}>Card Details</Text>
                  {stripeKey ? (
                    <CardField
                      postalCodeEnabled={false}
                      placeholders={{ number: '4242 4242 4242 4242' }}
                      cardStyle={{
                        backgroundColor: isDark ? '#2C2C2E' : '#FFFFFF',
                        textColor: isDark ? '#F2F2F7' : '#1A1A1A',
                        placeholderColor: isDark ? '#8E8E93' : '#BBBBBB',
                        borderColor: colors.border,
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
                      <ActivityIndicator size="small" color={colors.textDim} />
                      <Text style={styles.cardFieldLoadingText}>Payment module loading…</Text>
                    </View>
                  )}

                  <Text style={styles.inputLabel}>Cardholder Name</Text>
                  <TextInput
                    style={styles.input}
                    placeholder="Name on card"
                    placeholderTextColor={colors.textSecondary}
                    value={cardName}
                    onChangeText={setCardName}
                    autoCapitalize="words"
                  />

                  <View style={styles.formButtons}>
                    <TouchableOpacity style={styles.cancelFormBtn} onPress={() => { setShowAdd(false); resetForm(); }}>
                      <Text style={styles.cancelFormText}>Cancel</Text>
                    </TouchableOpacity>
                    <TouchableOpacity style={styles.saveCardBtn} onPress={handleAddCard} disabled={saving} activeOpacity={0.85}>
                      {saving ? (
                        <ActivityIndicator size="small" color="#FFF" />
                      ) : (
                        <Text style={styles.saveCardText}>Add Card</Text>
                      )}
                    </TouchableOpacity>
                  </View>

                  <View style={styles.securityNote}>
                    <Ionicons name="lock-closed" size={13} color={colors.success} />
                    <Text style={styles.securityText}>Secured & encrypted by Stripe · PCI-DSS compliant</Text>
                  </View>

                  {/* Accepted-brand strip for trust */}
                  <View style={styles.acceptedRow}>
                    <FontAwesome name="cc-visa" size={26} color={colors.textDim} />
                    <FontAwesome name="cc-mastercard" size={26} color={colors.textDim} />
                    <FontAwesome name="cc-amex" size={26} color={colors.textDim} />
                    <FontAwesome name="cc-discover" size={26} color={colors.textDim} />
                  </View>
                </View>
              ) : (
                <TouchableOpacity style={styles.addCardBtn} onPress={() => setShowAdd(true)} activeOpacity={0.8}>
                  <View style={styles.addCardIconWrap}>
                    <Ionicons name="add" size={22} color={colors.primary} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.addCardText}>Add a new card</Text>
                    <Text style={styles.addCardSub}>Credit or debit · Visa, Mastercard, Amex</Text>
                  </View>
                  <Ionicons name="chevron-forward" size={20} color={colors.textDim} />
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
    container: { flex: 1, backgroundColor: colors.background },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 12,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    loadingWrap: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    list: { padding: 20, paddingBottom: 40 },

    // List header
    listHeader: { marginBottom: 8 },
    listHeaderTitle: { fontSize: 22, fontWeight: '800', color: colors.text, letterSpacing: -0.3 },
    listHeaderSub: { fontSize: 13, color: colors.textDim, marginTop: 2 },

    // Card row (face + action strip)
    cardRow: { marginBottom: 22 },

    // Realistic card face
    cardFace: {
      borderRadius: 20,
      padding: 20,
      aspectRatio: 1.586,           // standard ISO 7810 ID-1 card ratio
      justifyContent: 'space-between',
      overflow: 'hidden',
      // Elevation for a "floating card" feel
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 8 },
      shadowOpacity: 0.28,
      shadowRadius: 16,
      elevation: 8,
    },
    sheenTop: {
      position: 'absolute', top: -70, right: -50,
      width: 180, height: 180, borderRadius: 90,
      backgroundColor: 'rgba(255,255,255,0.10)',
    },
    sheenBottom: {
      position: 'absolute', bottom: -90, left: -40,
      width: 200, height: 200, borderRadius: 100,
      backgroundColor: 'rgba(255,255,255,0.06)',
    },
    faceTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
    defaultPill: {
      flexDirection: 'row', alignItems: 'center', gap: 4,
      backgroundColor: colors.gold, borderRadius: 20, paddingHorizontal: 8, paddingVertical: 3,
    },
    defaultPillText: { fontSize: 9, fontWeight: '800', color: '#1A1A1A', letterSpacing: 0.6 },

    chip: {
      width: 44, height: 34, borderRadius: 7,
      backgroundColor: '#E6C77A',
      justifyContent: 'center', alignItems: 'center',
      // Subtle metallic border
      borderWidth: 1, borderColor: 'rgba(255,255,255,0.35)',
    },
    chipLine: {
      width: 24, height: 12, borderRadius: 2,
      borderWidth: 1.2, borderColor: 'rgba(120,90,20,0.55)',
    },
    chipLineH: {
      position: 'absolute', width: 44, height: 1.2,
      backgroundColor: 'rgba(120,90,20,0.55)',
    },

    faceNumber: {
      fontSize: 20, fontWeight: '600', color: '#FFFFFF',
      letterSpacing: 2,
      textShadowColor: 'rgba(0,0,0,0.25)', textShadowOffset: { width: 0, height: 1 }, textShadowRadius: 2,
    },
    faceNumberLast4: { fontWeight: '800', letterSpacing: 3 },

    faceBottom: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between' },
    faceBottomLeft: { flex: 1, marginRight: 10 },
    faceExpiry: { marginRight: 12 },
    faceMetaLabel: { fontSize: 8, fontWeight: '700', color: 'rgba(255,255,255,0.6)', letterSpacing: 0.8 },
    faceMetaValue: { fontSize: 13, fontWeight: '700', color: '#FFFFFF', marginTop: 2, letterSpacing: 0.5 },
    faceLogo: {
      textShadowColor: 'rgba(0,0,0,0.3)', textShadowOffset: { width: 0, height: 1 }, textShadowRadius: 3,
    },

    // Action strip under each card
    actionStrip: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 6, paddingTop: 12,
    },
    actionBrand: { flex: 1, fontSize: 13, fontWeight: '600', color: colors.textDim, letterSpacing: 0.5 },
    actionButtons: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    setDefaultBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 4,
      paddingHorizontal: 12, paddingVertical: 7, borderRadius: 20,
      backgroundColor: colors.dangerBg,
    },
    setDefaultText: { fontSize: 12, fontWeight: '700', color: colors.primary },
    payWithBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 5,
      paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20,
      backgroundColor: colors.primary,
    },
    payWithText: { fontSize: 13, fontWeight: '800', color: '#FFF' },
    deleteBtn: {
      width: 34, height: 34, borderRadius: 17,
      justifyContent: 'center', alignItems: 'center',
      backgroundColor: colors.surfaceLight,
    },

    payBanner: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      paddingHorizontal: 20, paddingVertical: 12,
      backgroundColor: colors.dangerBg,
    },
    payBannerText: { fontSize: 13, fontWeight: '600', color: colors.text },

    // Empty state
    emptyState: { alignItems: 'center', paddingVertical: 24 },
    ghostCard: {
      width: '78%', aspectRatio: 1.586, borderRadius: 20,
      borderWidth: 2, borderColor: colors.border, borderStyle: 'dashed',
      backgroundColor: colors.surfaceLight,
      padding: 20, justifyContent: 'center', marginBottom: 24,
    },
    ghostChip: {
      width: 44, height: 34, borderRadius: 7, backgroundColor: colors.border, marginBottom: 18,
    },
    ghostLine: { height: 12, borderRadius: 6, backgroundColor: colors.border },
    ghostPlus: { position: 'absolute', right: 18, bottom: 16 },
    emptyTitle: { fontSize: 20, fontWeight: '800', color: colors.text, marginTop: 4 },
    emptySubtext: {
      fontSize: 14, color: colors.textDim, marginTop: 8,
      textAlign: 'center', lineHeight: 20, paddingHorizontal: 24,
    },

    // Add Card button (rich list-item style)
    addCardBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 14,
      paddingVertical: 16, paddingHorizontal: 16, borderRadius: 16,
      borderWidth: 1.5, borderColor: colors.primary, borderStyle: 'dashed',
      backgroundColor: colors.dangerBg,
      marginTop: 4,
    },
    addCardIconWrap: {
      width: 40, height: 40, borderRadius: 20,
      backgroundColor: colors.surface,
      justifyContent: 'center', alignItems: 'center',
    },
    addCardText: { fontSize: 15, fontWeight: '700', color: colors.text },
    addCardSub: { fontSize: 12, color: colors.textDim, marginTop: 2 },

    // Add Form
    addForm: {
      backgroundColor: colors.surfaceLight, borderRadius: 18, padding: 20, marginTop: 4,
      borderWidth: 1, borderColor: colors.border,
    },
    addFormHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 16 },
    addFormTitle: { fontSize: 17, fontWeight: '700', color: colors.text },
    inputLabel: { fontSize: 12, fontWeight: '600', color: colors.textDim, marginBottom: 6, marginTop: 12 },
    input: {
      backgroundColor: colors.surface, borderRadius: 12, paddingHorizontal: 16, paddingVertical: 14,
      fontSize: 16, fontWeight: '500', color: colors.text,
      borderWidth: 1, borderColor: colors.border,
    },
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
      marginTop: 16,
    },
    securityText: { fontSize: 11, color: colors.textDim },
    acceptedRow: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 14,
      marginTop: 14, opacity: 0.7,
    },
  });
}
