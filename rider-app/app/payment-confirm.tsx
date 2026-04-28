import React, { useState, useMemo, useEffect } from 'react';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  TextInput,
  KeyboardAvoidingView,
  Platform,
  Switch,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import { useWorkProfileStore } from '../store/workProfileStore';
import CustomAlert from '@shared/components/CustomAlert';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import Analytics from '@shared/analytics';
import { useScheduledRideReminder } from '../hooks/useScheduledRideReminder';

interface CorporateAccount {
  id: string;
  company_name: string;
}

interface SavedCard {
  id: string;
  brand: string;
  last4: string;
  exp_month: number;
  exp_year: number;
  is_default: boolean;
}

function PaymentConfirmScreenContent() {
  const router = useRouter();
  const { pickup, dropoff, selectedVehicle, estimates, createRide, isLoading, scheduledTime } = useRideStore();
  const [selectedPayment, setSelectedPayment] = useState('card');
  const [savedCards, setSavedCards] = useState<SavedCard[]>([]);
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null);
  const [promoExpanded, setPromoExpanded] = useState(false);
  const [promoCode, setPromoCode] = useState('');
  const [promoDiscount, setPromoDiscount] = useState(0);
  const [promoValidating, setPromoValidating] = useState(false);
  const [promoApplied, setPromoApplied] = useState(false);
  const [promoMessage, setPromoMessage] = useState('');
  const { profiles: workProfiles, workModeEnabled, fetchProfiles: fetchWorkProfiles, activeCompanyId } = useWorkProfileStore();
  const corporateAccounts: CorporateAccount[] = workProfiles
    .map(p => ({ id: p.company.id ?? '', company_name: p.company.name ?? '' }))
    .filter(a => a.id);
  const [useCorporate, setUseCorporate] = useState(workModeEnabled);
  const [selectedCorporateId, setSelectedCorporateId] = useState<string | null>(
    workModeEnabled ? (activeCompanyId ?? null) : null,
  );
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { scheduleReminder } = useScheduledRideReminder();

  const selectedEstimate = estimates.find((e) => e.vehicle_type.id === selectedVehicle?.id);

  useEffect(() => {
    fetchWorkProfiles();
  }, []);

  useEffect(() => {
    api.get('/payments/cards').then((res) => {
      const cards: SavedCard[] = Array.isArray(res.data) ? res.data : [];
      setSavedCards(cards);
      const defaultCard = cards.find((c) => c.is_default) ?? cards[0];
      if (defaultCard) setSelectedCardId(defaultCard.id);
    }).catch(() => {});
  }, []);

  // Keep corporate toggle in sync when work mode is toggled elsewhere
  useEffect(() => {
    if (workModeEnabled && corporateAccounts.length > 0) {
      setUseCorporate(true);
      setSelectedCorporateId(prev => prev ?? activeCompanyId ?? corporateAccounts[0]?.id ?? null);
    }
  }, [workModeEnabled, corporateAccounts.length]);

  const [isBooking, setIsBooking] = useState(false);
  const handleBookRide = async () => {
    if (isBooking || isLoading) return;
    setIsBooking(true);
    try {
      const corpId = useCorporate && selectedCorporateId ? selectedCorporateId : null;
      const pmId = selectedPayment === 'card' ? (selectedCardId ?? undefined) : undefined;
      const ride = await createRide(selectedPayment, corpId, pmId);
      Analytics.rideRequested({
        vehicle_type: selectedVehicle?.name ?? 'unknown',
        estimated_fare: totalFare,
      });
      Analytics.paymentInitiated({ method: selectedPayment, amount: totalFare });

      // Schedule a local 15-min reminder if this is a scheduled ride.
      // The backend cron also fires an FCM `scheduled_ride_reminder`; this
      // local notification is a client-side fallback.
      if (scheduledTime && ride.id) {
        scheduleReminder(ride.id, scheduledTime).catch(() => {});
      }

      if (scheduledTime) {
        router.replace('/(tabs)');
      } else {
        router.replace('/driver-arriving?rideId=' + ride.id);
      }
    } catch (error: any) {
      setAlertState({ visible: true, title: 'Error', message: error.message || 'Failed to book ride', variant: 'danger' });
    } finally {
      setIsBooking(false);
    }
  };

  const handleApplyPromo = async () => {
    const code = promoCode.trim();
    if (!code) return;
    setPromoValidating(true);
    setPromoMessage('');
    try {
      const fare = selectedEstimate?.total_fare || 0;
      const res = await api.post('/promo/validate', { code, ride_fare: fare });
      setPromoDiscount(res.data.discount_amount);
      setPromoApplied(true);
      setPromoMessage(`-$${res.data.discount_amount.toFixed(2)} discount applied!`);
    } catch (error: any) {
      const msg = error?.response?.data?.detail || 'Invalid promo code';
      setPromoMessage(msg);
      setPromoDiscount(0);
      setPromoApplied(false);
    } finally {
      setPromoValidating(false);
    }
  };

  const totalFare = (selectedEstimate?.total_fare || 0) - promoDiscount;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity
          style={styles.backButton}
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Confirm booking</Text>
        <View style={{ width: 44 }} />
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
      <ScrollView style={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
        {/* Ride Summary */}
        <View style={styles.rideSummary}>
          <View style={styles.vehicleInfo}>
            <View style={styles.vehicleIcon}>
              <Ionicons name="car" size={28} color={colors.primary} />
            </View>
            <View style={styles.vehicleDetails}>
              <Text style={styles.vehicleName}>{selectedVehicle?.name}</Text>
              <Text style={styles.vehicleDesc} allowFontScaling={false}>{selectedEstimate?.duration_minutes} min • {selectedEstimate?.distance_km} km</Text>
            </View>
            <Text style={styles.totalPrice} allowFontScaling={false}>${selectedEstimate?.total_fare.toFixed(2)}</Text>
          </View>

          <View style={styles.routeContainer}>
            <View style={styles.routePoint}>
              <View style={[styles.routeDot, { backgroundColor: '#10B981' }]} />
              <View style={styles.routeInfo}>
                <Text style={styles.routeLabel}>Pickup</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>{pickup?.address}</Text>
              </View>
            </View>
            <View style={styles.routeLine} />
            <View style={styles.routePoint}>
              <View style={[styles.routeDot, { backgroundColor: colors.primary }]} />
              <View style={styles.routeInfo}>
                <Text style={styles.routeLabel}>Dropoff</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>{dropoff?.address}</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Fare Breakdown */}
        {selectedEstimate && (
          <View style={styles.fareBreakdown}>
            <Text style={styles.fareBreakdownTitle}>Fare Breakdown</Text>

            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Base fare</Text>
              <Text style={styles.fareValue} allowFontScaling={false}>${selectedEstimate.base_fare.toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Distance ({selectedEstimate.distance_km} km)</Text>
              <Text style={styles.fareValue} allowFontScaling={false}>${selectedEstimate.distance_fare.toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Time ({selectedEstimate.duration_minutes} min)</Text>
              <Text style={styles.fareValue} allowFontScaling={false}>${selectedEstimate.time_fare.toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareLabel}>Booking fee</Text>
              <Text style={styles.fareValue} allowFontScaling={false}>${selectedEstimate.booking_fee.toFixed(2)}</Text>
            </View>

            <View style={styles.fareDivider} />

            {/* Dynamic area fees from API */}
            {(selectedEstimate as any).area_fees?.map((fee: any, i: number) => (
              <View key={fee.id || i} style={styles.fareRow}>
                <Text style={styles.fareLabel}>{fee.name || fee.type}</Text>
                <Text style={styles.fareValue} allowFontScaling={false}>${Number(fee.calculated_value || 0).toFixed(2)}</Text>
              </View>
            ))}

            {/* Dynamic tax breakdown from API */}
            {(selectedEstimate as any).tax_breakdown && Object.entries((selectedEstimate as any).tax_breakdown).map(([name, info]: [string, any]) => (
              <View key={name} style={styles.fareRow}>
                <Text style={styles.fareLabel}>{name} ({info.rate}%)</Text>
                <Text style={styles.fareValue} allowFontScaling={false}>${Number(info.amount || 0).toFixed(2)}</Text>
              </View>
            ))}

            {(selectedEstimate.surge_multiplier ?? 1) > 1.0 && (
              <View style={styles.fareRow}>
                <Text style={[styles.fareLabel, { color: '#EF4444' }]}>Surge ({selectedEstimate.surge_multiplier}x)</Text>
                <Text style={[styles.fareValue, { color: '#EF4444' }]}>Applied</Text>
              </View>
            )}

            <View style={styles.fareDivider} />
            <View style={styles.fareRow}>
              <Text style={styles.fareTotalLabel}>Estimated Total</Text>
              <Text style={styles.fareTotalValue} allowFontScaling={false}>
                ${((selectedEstimate as any).grand_total || selectedEstimate.total_fare).toFixed(2)}
              </Text>
            </View>
          </View>
        )}

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Payment Method</Text>

          {/* Saved cards from Stripe */}
          {savedCards.map((card) => {
            const isSelected = selectedPayment === 'card' && selectedCardId === card.id;
            return (
              <TouchableOpacity
                key={card.id}
                style={[styles.paymentOption, isSelected && styles.paymentOptionSelected]}
                onPress={() => { setSelectedPayment('card'); setSelectedCardId(card.id); }}
                accessibilityRole="radio"
                accessibilityLabel={`${card.brand.charAt(0).toUpperCase() + card.brand.slice(1)} card ending in ${card.last4}, expires ${card.exp_month}/${String(card.exp_year).slice(-2)}`}
                accessibilityState={{ checked: isSelected }}
              >
                <View style={styles.paymentIconContainer}>
                  <Ionicons name="card" size={24} color={isSelected ? colors.primary : colors.textDim} />
                </View>
                <View style={styles.paymentInfo}>
                  <Text style={styles.paymentName}>{card.brand.charAt(0).toUpperCase() + card.brand.slice(1)}</Text>
                  <Text style={styles.paymentDetails}>•••• {card.last4}  {card.exp_month}/{String(card.exp_year).slice(-2)}</Text>
                </View>
                {isSelected && (
                  <View style={styles.paymentCheck}>
                    <Ionicons name="checkmark" size={18} color="#FFFFFF" />
                  </View>
                )}
              </TouchableOpacity>
            );
          })}

          {/* Show generic card option if no saved cards loaded yet */}
          {savedCards.length === 0 && (
            <TouchableOpacity
              style={[styles.paymentOption, selectedPayment === 'card' && styles.paymentOptionSelected]}
              onPress={() => setSelectedPayment('card')}
              accessibilityRole="radio"
              accessibilityLabel="Credit card"
              accessibilityState={{ checked: selectedPayment === 'card' }}
            >
              <View style={styles.paymentIconContainer}>
                <Ionicons name="card" size={24} color={selectedPayment === 'card' ? colors.primary : colors.textDim} />
              </View>
              <View style={styles.paymentInfo}>
                <Text style={styles.paymentName}>Credit Card</Text>
              </View>
              {selectedPayment === 'card' && (
                <View style={styles.paymentCheck}>
                  <Ionicons name="checkmark" size={18} color="#FFFFFF" />
                </View>
              )}
            </TouchableOpacity>
          )}

          {/* Wallet */}
          <TouchableOpacity
            style={[styles.paymentOption, selectedPayment === 'wallet' && styles.paymentOptionSelected]}
            onPress={() => setSelectedPayment('wallet')}
            accessibilityRole="radio"
            accessibilityLabel="Spinr Wallet"
            accessibilityState={{ checked: selectedPayment === 'wallet' }}
          >
            <View style={styles.paymentIconContainer}>
              <Ionicons name="wallet" size={24} color={selectedPayment === 'wallet' ? colors.primary : colors.textDim} />
            </View>
            <View style={styles.paymentInfo}>
              <Text style={styles.paymentName}>Spinr Wallet</Text>
            </View>
            {selectedPayment === 'wallet' && (
              <View style={styles.paymentCheck}>
                <Ionicons name="checkmark" size={18} color="#FFFFFF" />
              </View>
            )}
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.addPaymentButton}
            onPress={() => router.push('/manage-cards' as any)}
            accessibilityRole="button"
            accessibilityLabel="Add payment method"
            accessibilityHint="Opens the manage cards screen"
          >
            <Ionicons name="add" size={20} color={colors.primary} />
            <Text style={styles.addPaymentText}>Add Payment Method</Text>
          </TouchableOpacity>
        </View>

        {/* Corporate Billing */}
        {corporateAccounts.length > 0 && (
          <View style={styles.corporateSection}>
            <View style={styles.corporateRow}>
              <View style={styles.corporateIconWrap}>
                <Ionicons name="business" size={20} color={colors.primary} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.corporateTitle}>Bill to Business</Text>
                <Text style={styles.corporateSubtitle}>
                  {useCorporate && selectedCorporateId
                    ? corporateAccounts.find(a => a.id === selectedCorporateId)?.company_name
                    : 'Use a corporate account'}
                </Text>
              </View>
              <Switch
                value={useCorporate}
                onValueChange={(v) => setUseCorporate(v)}
                trackColor={{ false: colors.border, true: colors.primary }}
                thumbColor="#FFF"
                accessibilityLabel="Bill to business"
                accessibilityHint="Toggle to charge this ride to a corporate account"
              />
            </View>
            {useCorporate && corporateAccounts.length > 1 && (
              <View style={styles.corporatePicker}>
                {corporateAccounts.map((acct) => (
                  <TouchableOpacity
                    key={acct.id}
                    style={[styles.corporateOption, selectedCorporateId === acct.id && styles.corporateOptionSelected]}
                    onPress={() => setSelectedCorporateId(acct.id)}
                    accessibilityRole="radio"
                    accessibilityLabel={acct.company_name}
                    accessibilityState={{ checked: selectedCorporateId === acct.id }}
                  >
                    <Text style={[styles.corporateOptionText, selectedCorporateId === acct.id && { color: colors.primary }]}>
                      {acct.company_name}
                    </Text>
                    {selectedCorporateId === acct.id && <Ionicons name="checkmark" size={16} color={colors.primary} />}
                  </TouchableOpacity>
                ))}
              </View>
            )}
          </View>
        )}

        {/* Promo Code */}
        {!promoExpanded ? (
          <TouchableOpacity
            style={styles.promoButton}
            onPress={() => setPromoExpanded(true)}
            accessibilityRole="button"
            accessibilityLabel={promoApplied ? `Promo applied, saving $${promoDiscount.toFixed(2)}` : 'Add promo code'}
            accessibilityHint="Opens the promo code entry field"
          >
            <Ionicons name="pricetag" size={20} color={colors.primary} />
            <Text style={styles.promoText}>
              {promoApplied ? `Promo applied: -$${promoDiscount.toFixed(2)}` : 'Add promo code'}
            </Text>
            <Ionicons name="chevron-forward" size={20} color={colors.textDim} />
          </TouchableOpacity>
        ) : (
          <View style={styles.promoSection}>
            <Text style={styles.promoSectionTitle}>Promo Code</Text>
            <View style={styles.promoInputRow}>
              <TextInput
                style={styles.promoInput}
                placeholder="Enter code"
                placeholderTextColor={colors.textDim}
                value={promoCode}
                onChangeText={(t) => { setPromoCode(t.toUpperCase()); setPromoApplied(false); setPromoMessage(''); }}
                autoCapitalize="characters"
                autoFocus
                returnKeyType="done"
                onSubmitEditing={handleApplyPromo}
              />
              <TouchableOpacity
                style={[styles.promoApplyButton, (!promoCode.trim() || promoValidating) && styles.promoApplyDisabled]}
                onPress={handleApplyPromo}
                disabled={!promoCode.trim() || promoValidating}
                accessibilityRole="button"
                accessibilityLabel="Apply promo code"
                accessibilityState={{ disabled: !promoCode.trim() || promoValidating, busy: promoValidating }}
              >
                {promoValidating ? (
                  <ActivityIndicator color="#FFF" size="small" />
                ) : (
                  <Text style={styles.promoApplyText}>Apply</Text>
                )}
              </TouchableOpacity>
            </View>
            {promoMessage ? (
              <Text style={[styles.promoMessage, promoApplied && styles.promoMessageSuccess]}>
                {promoMessage}
              </Text>
            ) : null}
          </View>
        )}

        {/* Fare Split Option */}
        <TouchableOpacity
          style={styles.splitButton}
          onPress={() => router.push('/fare-split' as any)}
          accessibilityRole="button"
          accessibilityLabel="Split fare"
          accessibilityHint="Share the cost of this ride with friends"
        >
          <View style={styles.splitIconContainer}>
            <Ionicons name="people" size={20} color={colors.primary} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.splitText}>Split Fare</Text>
            <Text style={styles.splitSubtext}>Share the cost with friends</Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={colors.textDim} />
        </TouchableOpacity>
      </ScrollView>
      </KeyboardAvoidingView>

      {/* Book Button */}
      <View style={styles.footer}>
        <View style={styles.totalRow}>
          <Text style={styles.totalLabel}>Subtotal</Text>
          <Text style={styles.totalAmount} allowFontScaling={false}>${selectedEstimate?.total_fare.toFixed(2)}</Text>
        </View>
        {promoDiscount > 0 && (
          <View style={styles.discountRow}>
            <Text style={styles.discountLabel}>Promo discount</Text>
            <Text style={styles.discountAmount} allowFontScaling={false}>-${promoDiscount.toFixed(2)}</Text>
          </View>
        )}
        {promoDiscount > 0 && (
          <View style={[styles.totalRow, { marginTop: 4 }]}>
            <Text style={[styles.totalLabel, { fontFamily: 'PlusJakartaSans_700Bold', color: colors.text }]}>Total</Text>
            <Text style={styles.totalAmount} allowFontScaling={false}>${totalFare.toFixed(2)}</Text>
          </View>
        )}
        {scheduledTime && (
          <View style={styles.scheduledBadge}>
            <Ionicons name="calendar-outline" size={16} color={colors.primary} />
            <Text style={styles.scheduledText} allowFontScaling={false}>
              Scheduled: {scheduledTime.toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' })}{' '}
              at {scheduledTime.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' })}
            </Text>
          </View>
        )}
        <TouchableOpacity
          style={[styles.bookButton, (isLoading || isBooking) && { opacity: 0.6 }]}
          onPress={handleBookRide}
          disabled={isLoading || isBooking}
          activeOpacity={0.8}
          accessibilityRole="button"
          accessibilityLabel={scheduledTime ? `Schedule ${selectedVehicle?.name}` : `Book ${selectedVehicle?.name}`}
          accessibilityHint={`Total $${totalFare.toFixed(2)}`}
          accessibilityState={{ disabled: isLoading || isBooking, busy: isBooking }}
        >
          {isLoading || isBooking ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <>
              <Text style={styles.bookButtonText}>
                {scheduledTime ? 'Schedule' : 'Book'} {selectedVehicle?.name}
              </Text>
              <Ionicons name="arrow-forward" size={20} color="#FFFFFF" />
            </>
          )}
        </TouchableOpacity>
      </View>
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

export default function PaymentConfirmScreen() {
  return (
    <ErrorBoundary>
      <PaymentConfirmScreenContent />
    </ErrorBoundary>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surfaceLight,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
      backgroundColor: colors.surface,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    backButton: {
      width: 44,
      height: 44,
      justifyContent: 'center',
      alignItems: 'center',
    },
    headerTitle: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    content: {
      flex: 1,
    },
    rideSummary: {
      backgroundColor: colors.surface,
      marginBottom: 12,
      padding: 20,
    },
    vehicleInfo: {
      flexDirection: 'row',
      alignItems: 'center',
      marginBottom: 20,
    },
    vehicleIcon: {
      width: 56,
      height: 56,
      borderRadius: 28,
      backgroundColor: '#FFF0F0',
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 14,
    },
    vehicleDetails: {
      flex: 1,
    },
    vehicleName: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    vehicleDesc: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    totalPrice: {
      fontSize: 22,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.primary,
    },
    routeContainer: {
      paddingTop: 16,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    routePoint: {
      flexDirection: 'row',
      alignItems: 'flex-start',
    },
    routeDot: {
      width: 12,
      height: 12,
      borderRadius: 6,
      marginTop: 4,
      marginRight: 12,
    },
    routeLine: {
      width: 2,
      height: 24,
      backgroundColor: colors.border,
      marginLeft: 5,
    },
    routeInfo: {
      flex: 1,
    },
    routeLabel: {
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      marginBottom: 2,
    },
    routeAddress: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    section: {
      backgroundColor: colors.surface,
      padding: 20,
      marginBottom: 12,
    },
    sectionTitle: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 16,
    },
    paymentOption: {
      flexDirection: 'row',
      alignItems: 'center',
      padding: 16,
      marginBottom: 12,
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      borderWidth: 2,
      borderColor: 'transparent',
    },
    paymentOptionSelected: {
      backgroundColor: '#FFF5F5',
      borderColor: colors.primary,
    },
    paymentIconContainer: {
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 12,
    },
    paymentInfo: {
      flex: 1,
    },
    paymentName: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    paymentDetails: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    paymentCheck: {
      width: 28,
      height: 28,
      borderRadius: 14,
      backgroundColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
    },
    addPaymentButton: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      paddingVertical: 12,
    },
    addPaymentText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.primary,
      marginLeft: 8,
    },
    promoButton: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      padding: 20,
      marginBottom: 20,
    },
    promoText: {
      flex: 1,
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
      marginLeft: 12,
    },
    footer: {
      backgroundColor: colors.surface,
      paddingHorizontal: 20,
      paddingVertical: 16,
      borderTopWidth: 1,
      borderTopColor: colors.border,
    },
    totalRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 16,
    },
    totalLabel: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    totalAmount: {
      fontSize: 24,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    bookButton: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: colors.primary,
      borderRadius: 28,
      paddingVertical: 18,
      gap: 8,
    },
    bookButtonText: {
      fontSize: 17,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#FFFFFF',
    },
    promoSection: {
      backgroundColor: colors.surface,
      padding: 20,
      marginBottom: 20,
    },
    promoSectionTitle: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 12,
    },
    promoInputRow: {
      flexDirection: 'row',
      gap: 10,
    },
    promoInput: {
      flex: 1,
      height: 48,
      backgroundColor: colors.surfaceLight,
      borderRadius: 12,
      paddingHorizontal: 16,
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
      letterSpacing: 1,
    },
    promoApplyButton: {
      backgroundColor: colors.primary,
      paddingHorizontal: 24,
      borderRadius: 12,
      justifyContent: 'center',
      alignItems: 'center',
    },
    promoApplyDisabled: {
      opacity: 0.5,
    },
    promoApplyText: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#FFFFFF',
    },
    promoMessage: {
      marginTop: 8,
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: '#EF4444',
    },
    promoMessageSuccess: {
      color: '#10B981',
    },
    discountRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 8,
    },
    discountLabel: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: '#10B981',
    },
    discountAmount: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#10B981',
    },
    scheduledBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      backgroundColor: '#F0F7FF',
      paddingVertical: 10,
      paddingHorizontal: 14,
      borderRadius: 10,
      marginBottom: 12,
    },
    scheduledText: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    fareBreakdown: {
      backgroundColor: colors.surface,
      padding: 20,
      marginBottom: 12,
    },
    fareBreakdownTitle: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 14,
    },
    fareRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      paddingVertical: 6,
    },
    fareLabel: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#6B7280',
    },
    fareValue: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    fareDivider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 10,
    },
    fareTotalLabel: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    fareTotalValue: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.primary,
    },
    corporateSection: {
      backgroundColor: colors.surface,
      padding: 16,
      marginBottom: 12,
    },
    corporateRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
    },
    corporateIconWrap: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: colors.primary + '15',
      alignItems: 'center',
      justifyContent: 'center',
    },
    corporateTitle: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    corporateSubtitle: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 2,
    },
    corporatePicker: {
      marginTop: 12,
      borderTopWidth: 1,
      borderTopColor: colors.border,
      paddingTop: 12,
    },
    corporateOption: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingVertical: 10,
      paddingHorizontal: 12,
      borderRadius: 10,
    },
    corporateOptionSelected: {
      backgroundColor: colors.primary + '10',
    },
    corporateOptionText: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    splitButton: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surface,
      padding: 16,
      marginBottom: 20,
      gap: 12,
    },
    splitIconContainer: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: colors.primary + '15',
      alignItems: 'center',
      justifyContent: 'center',
    },
    splitText: {
      fontSize: 15,
      fontWeight: '600',
      color: colors.text,
    },
    splitSubtext: {
      fontSize: 13,
      color: colors.textDim,
      marginTop: 2,
    },
  });
}
