import React, { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Animated,
} from 'react-native';
import CustomToggle from '../components/CustomToggle';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { useFocusEffect } from 'expo-router/react-navigation';
import { Ionicons } from '@expo/vector-icons';
import { useRideStore } from '../store/rideStore';
import { useStripe } from '@stripe/stripe-react-native';
import { selectDefaultCardId } from '../utils/selectDefaultCard';
import { shouldApplyWorkModeDefault } from '../utils/workModeDefault';
import { useWalletStore } from '../store/walletStore';
import { useWorkProfileStore } from '../store/workProfileStore';
import { showToast } from '../store/toastStore';
import type { Ride, RideRequiresAction } from '../store/rideStore';
import ConfirmSheet from '../components/ConfirmSheet';
import api, { getApiErrorMessage, isEngineError } from '@shared/api/client';
import { recordNonFatal } from '../utils/crashlytics';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { Analytics } from '@shared/analytics';
import { useResponsive } from '@shared/utils/responsive';
import { useScheduledRideReminder } from '../hooks/useScheduledRideReminder';
import { useAnimatedValue, useAnimatedValues } from '../hooks/useAnimatedValue';

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
  const { pickup, dropoff, selectedVehicle, estimates, createRide, isLoading, scheduledTime, appliedPromo } = useRideStore();
  const { confirmPayment } = useStripe();
  const { wallet, fetchWallet } = useWalletStore();
  const [selectedPayment, setSelectedPayment] = useState('card');
  const [savedCards, setSavedCards] = useState<SavedCard[]>([]);
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null);
  const { profiles: workProfiles, workModeEnabled, fetchProfiles: fetchWorkProfiles, activeCompanyId } = useWorkProfileStore();
  const corporateAccounts: CorporateAccount[] = workProfiles
    .map(p => ({ id: p.company.id ?? '', company_name: p.company.name ?? '' }))
    .filter(a => a.id);
  const firstCorporateAccountId = corporateAccounts[0]?.id;
  const [useCorporate, setUseCorporate] = useState(workModeEnabled);
  // True once the rider picks a payment source by hand. A ref, not state:
  // it must not itself trigger a render, and the work-mode sync effect reads
  // it without needing to depend on it.
  const riderChosePaymentRef = useRef(false);
  const [selectedCorporateId, setSelectedCorporateId] = useState<string | null>(
    workModeEnabled ? (activeCompanyId ?? null) : null,
  );
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons: { text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }[];
  }>({ visible: false, title: '', message: '', variant: 'info', buttons: [] });

  const { colors } = useTheme();
  const { sf } = useResponsive();
  const styles = useMemo(() => createStyles(colors, sf), [colors, sf]);
  const { scheduleReminder } = useScheduledRideReminder();
  const [fareExpanded, setFareExpanded] = useState(false);
  const fareHeightAnim = useAnimatedValue(0);

  const selectedEstimate = estimates.find((e) => e.vehicle_type.id === selectedVehicle?.id);

  // Staggered fade-in + slide-up for each section
  const sectionAnims = useAnimatedValues(3, 0);
  const slideAnims = useAnimatedValues(3, 20);

  useEffect(() => {
    const animations = sectionAnims.map((anim, i) =>
      Animated.parallel([
        Animated.timing(anim, { toValue: 1, duration: 350, delay: i * 100, useNativeDriver: true }),
        Animated.timing(slideAnims[i], { toValue: 0, duration: 350, delay: i * 100, useNativeDriver: true }),
      ]),
    );
    Animated.stagger(80, animations).start();
    // sectionAnims/slideAnims are stable arrays (useAnimatedValues, created once).
  }, [sectionAnims, slideAnims]);

  useEffect(() => {
    fetchWorkProfiles();
    fetchWallet();
    // Both are zustand store actions (stable references).
  }, [fetchWorkProfiles, fetchWallet]);

  // Refetch saved cards on focus so a card added on /manage-cards (pushed,
  // then returned via router.back()) shows up and is auto-selected without
  // remounting this screen. Keep an explicit still-valid selection; otherwise
  // fall back to the default card (the backend marks the first card default).
  const loadSavedCards = useCallback(() => {
    api.get('/payments/cards').then((res) => {
      const cards: SavedCard[] = Array.isArray(res.data) ? res.data : [];
      setSavedCards(cards);
      setSelectedCardId((prev) => selectDefaultCardId(prev, cards));
    }).catch((e) => {
      console.warn('[PaymentConfirm] Failed to load saved cards:', e);
    });
  }, []);

  useFocusEffect(loadSavedCards);

  // Keep corporate toggle in sync when work mode is toggled elsewhere
  useEffect(() => {
    // `riderChosePaymentRef` is the guard: once the rider has picked a payment
    // source by hand, this effect must never move it back.
    //
    // Without it, work mode re-asserts corporate billing on any later re-run —
    // and this effect re-runs when activeCompanyId or firstCorporateAccountId
    // changes, which a late fetchWorkProfiles() resolution does. Sequence:
    // rider opens this screen, deliberately taps their personal card, the
    // profile fetch lands a moment later, the toggle silently flips back, and
    // the COMPANY is billed for a personal trip. Neither the rider nor the
    // company is told. That is a wrong charge, not a cosmetic glitch.
    //
    // Defaulting to corporate for a work-mode rider who hasn't chosen yet is
    // still correct — that is the point of work mode — so the guard only
    // suppresses the override, never the initial default.
    if (shouldApplyWorkModeDefault({
      workModeEnabled,
      hasCorporateAccounts: corporateAccounts.length > 0,
      riderChosePayment: riderChosePaymentRef.current,
    })) {
      // Keeps the corporate toggle in sync when work mode is enabled
      // elsewhere; neither useCorporate nor selectedCorporateId is a dep of
      // this effect (workModeEnabled/corporateAccounts.length are), so no
      // loop.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setUseCorporate(true);
      setSelectedCorporateId(prev => prev ?? activeCompanyId ?? firstCorporateAccountId ?? null);
    }
    // Narrowed to firstCorporateAccountId rather than the whole
    // `corporateAccounts` array: that array is a plain `.map().filter()`
    // literal recreated with a new reference every render (not memoized),
    // so depending on it directly would re-run this effect — and re-call
    // setUseCorporate(true) — on every render while work mode is on, not
    // just when the account list's first entry actually changes.
    // activeCompanyId is added because it's real reactive store state (not
    // a stable action): previously, if workModeEnabled/corporateAccounts
    // became true before activeCompanyId caught up (both null/no accounts
    // at that instant), a later activeCompanyId update couldn't re-trigger
    // this effect to fill in selectedCorporateId — a real, if narrow, gap.
  }, [workModeEnabled, corporateAccounts.length, firstCorporateAccountId, activeCompanyId]);

  const [isBooking, setIsBooking] = useState(false);
  const handleBookRide = async () => {
    if (isBooking || isLoading) return;
    setIsBooking(true);
    try {
      const corpId = useCorporate && selectedCorporateId ? selectedCorporateId : null;
      const pmId = selectedPayment === 'card' ? (selectedCardId ?? undefined) : undefined;
      let ride = await createRide(selectedPayment, corpId, pmId);

      // SCA two-step: the card hold needs an on-device 3DS / Apple Pay confirm.
      // Run the Stripe sheet, then re-book passing the now-confirmed hold. The
      // manual-capture PaymentIntent lands in `requires_capture` (not
      // `succeeded`) after authentication — accept both.
      if ((ride as RideRequiresAction).requires_action) {
        const auth = (ride as RideRequiresAction).payment_authorization;
        const { error: confirmError, paymentIntent } = await confirmPayment(auth.client_secret);
        if (confirmError) {
          showToast('Authentication needed', confirmError.message || 'Card authentication was not completed.', 'danger');
          return;
        }
        const piStatus = (paymentIntent?.status || '').toString().toLowerCase();
        if (piStatus !== 'requirescapture' && piStatus !== 'requires_capture' && piStatus !== 'succeeded') {
          showToast('Authentication needed', 'Card authentication was not completed. Please try again.', 'danger');
          return;
        }
        ride = await createRide(selectedPayment, corpId, pmId, auth.payment_intent_id);
      }

      // After the (optional) SCA step, booking must have produced a real ride.
      if ((ride as RideRequiresAction).requires_action) {
        showToast('Booking failed', 'Card authorization could not be completed. Please try again.', 'danger');
        return;
      }
      const bookedRide = ride as Ride;

      if ((bookedRide as any).promo_error) {
        showToast('Promo not applied', (bookedRide as any).promo_error, 'warning');
      }
      Analytics.rideRequested({
        vehicle_type: selectedVehicle?.name ?? 'unknown',
        estimated_fare: totalFare,
      });
      Analytics.paymentInitiated({ method: selectedPayment, amount: totalFare });

      // Schedule a local 15-min reminder if this is a scheduled ride.
      // The backend cron also fires an FCM `scheduled_ride_reminder`; this
      // local notification is a client-side fallback.
      if (scheduledTime && bookedRide.id) {
        scheduleReminder(bookedRide.id, scheduledTime).catch(() => {});
      }

      if (scheduledTime) {
        router.replace('/(tabs)');
      } else {
        router.replace({ pathname: '/driver-arriving', params: { rideId: bookedRide.id } } as any);
      }
    } catch (error: any) {
      // Same capture as ride-options' proceedWithBooking: this catch also
      // swallows client-side crashes (notably confirmPayment() when the
      // Stripe native module is absent or older than the JS bundle), which
      // rideStore's recordNonFatal never sees because they happen outside its
      // try. Without this the only trace was a toast.
      if (isEngineError(error)) {
        console.error('[booking] client-side crash during booking', error);
        recordNonFatal(error, { screen: 'payment-confirm', action: 'handleBookRide' });
      }
      // Not user-facing: `error.message` here only drives the 409-detection
      // control-flow branch below (routing to the rider's already-active
      // ride instead of retrying booking). The actual user-visible message a
      // few lines down already goes through getApiErrorMessage().
      // eslint-disable-next-line no-restricted-syntax
      const is409 = error?.response?.status === 409 || error?.message?.includes('already active');
      if (is409) {
        const active = await useRideStore.getState().fetchActiveRide();
        if (active?.active && active.ride) {
          const s = active.ride.status;
          if (s === 'searching' || s === 'driver_assigned' || s === 'driver_accepted') {
            router.replace({ pathname: '/driver-arriving', params: { rideId: active.ride.id } } as any);
          } else if (s === 'driver_arrived') {
            router.replace({ pathname: '/driver-arrived', params: { rideId: active.ride.id } } as any);
          } else if (s === 'in_progress') {
            router.replace({ pathname: '/ride-in-progress', params: { rideId: active.ride.id } } as any);
          } else if (s === 'completed') {
            router.replace({ pathname: '/ride-completed', params: { rideId: active.ride.id } } as any);
          }
          return;
        }
      }
      const is402 = error?.response?.status === 402;
      const errBody = error?.response?.data;
      const unpaidRideId = errBody?.error?.details?.unpaid_ride_id;
      if (is402 && unpaidRideId) {
        setConfirmSheet({
          visible: true,
          title: 'Unpaid Ride',
          message: 'You have an unpaid ride. Please settle the payment before booking a new ride.',
          variant: 'danger',
          buttons: [
            { text: 'Pay Now', onPress: () => router.push({ pathname: '/ride-completed', params: { rideId: unpaidRideId } } as any) },
            { text: 'Cancel', style: 'cancel' as const },
          ],
        });
      } else {
        showToast('Booking Failed', getApiErrorMessage(error, 'Could not complete your booking. Please try again.'), 'danger');
      }
    } finally {
      setIsBooking(false);
    }
  };

  // Use server-computed discount_amount — correct for percentage promos and free_ride.
  const promoDiscount = appliedPromo?.discount_amount ?? 0;
  const totalFare = Math.max(0, parseFloat((selectedEstimate as any)?.grand_total || selectedEstimate?.total_fare || '0') - promoDiscount);

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

      <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
      <ScrollView style={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
        {/* Ride Summary */}
        <Animated.View style={[styles.rideSummary, { opacity: sectionAnims[0], transform: [{ translateY: slideAnims[0] }] }]}>
          <View style={styles.vehicleInfo}>
            <View style={styles.vehicleIcon}>
              <Ionicons name="car" size={28} color={colors.primary} />
            </View>
            <View style={styles.vehicleDetails}>
              <Text style={styles.vehicleName}>{selectedVehicle?.name}</Text>
              <Text style={styles.vehicleDesc} allowFontScaling={false}>{selectedEstimate?.duration_minutes} min • {selectedEstimate?.distance_km} km</Text>
            </View>
            <Text style={styles.totalPrice} allowFontScaling={false}>${parseFloat(selectedEstimate?.total_fare || '0').toFixed(2)}</Text>
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
        </Animated.View>

        <Animated.View style={[styles.section, { opacity: sectionAnims[1], transform: [{ translateY: slideAnims[1] }] }]}>
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

          {/* No saved cards yet: tapping "Credit Card" takes the rider
              straight to the add-card screen instead of selecting an empty
              option. On return, the focus refetch auto-selects the new card. */}
          {savedCards.length === 0 && (
            <TouchableOpacity
              style={styles.paymentOption}
              onPress={() => { riderChosePaymentRef.current = true; setSelectedPayment('card'); setUseCorporate(false); router.push('/manage-cards' as any); }}
              accessibilityRole="button"
              accessibilityLabel="Add a credit card"
              accessibilityHint="Opens the manage cards screen"
            >
              <View style={styles.paymentIconContainer}>
                <Ionicons name="card" size={24} color={colors.textDim} />
              </View>
              <View style={styles.paymentInfo}>
                <Text style={styles.paymentName}>Credit Card</Text>
                <Text style={styles.paymentDetails}>Tap to add a card</Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
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
              <Text style={styles.walletBalance}>
                Balance: ${parseFloat(wallet?.balance ?? '0').toFixed(2)}
              </Text>
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
              <CustomToggle
                value={useCorporate}
                onValueChange={(v) => { riderChosePaymentRef.current = true; setUseCorporate(v); }}
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

        </Animated.View>

      </ScrollView>
      </KeyboardAvoidingView>

      {/* Footer — fare breakdown + subtotal + book */}
      <Animated.View style={[styles.footer, { opacity: sectionAnims[2], transform: [{ translateY: slideAnims[2] }] }]}>
        {/* Collapsible fare breakdown */}
        {selectedEstimate && (
          <>
            <TouchableOpacity
              style={styles.fareHeader}
              onPress={() => {
                const next = !fareExpanded;
                setFareExpanded(next);
                Animated.spring(fareHeightAnim, {
                  toValue: next ? 1 : 0,
                  tension: 100,
                  friction: 12,
                  useNativeDriver: false,
                }).start();
              }}
              activeOpacity={0.7}
            >
              <View style={{ flex: 1 }}>
                <Text style={styles.fareTotalLabel}>Estimated Total</Text>
                <Text style={styles.fareToggleHint}>{fareExpanded ? 'Hide details' : 'View fare details'}</Text>
              </View>
              <Text style={styles.fareTotalValue} allowFontScaling={false}>
                ${totalFare.toFixed(2)}
              </Text>
              <Animated.View style={{ transform: [{ rotate: fareHeightAnim.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '180deg'] }) }], marginLeft: 8 }}>
                <Ionicons name="chevron-down" size={18} color={colors.textDim} />
              </Animated.View>
            </TouchableOpacity>

            {fareExpanded && (
              <View style={{ marginTop: 8 }}>
                <View style={styles.fareDivider} />
                {(selectedEstimate.fare_breakdown || []).map((line: any, i: number) => (
                  line.amount != null ? (
                    <View key={i} style={[styles.fareRow, line.type === 'ride' && { alignItems: 'flex-start' }]}>
                      {line.type === 'ride' ? (
                        <View>
                          <Text style={styles.fareLabel}>{line.label}</Text>
                          <Text style={styles.fareDriverBadge}>100% goes to your driver · ride local, support local</Text>
                        </View>
                      ) : (
                        <Text style={[styles.fareLabel, line.type === 'tax' && { color: '#6B7280' }, line.type === 'modifier' && { color: '#EF4444' }]}>
                          {line.label}
                        </Text>
                      )}
                      {line.amount != null ? (
                        <Text style={[styles.fareValue, line.type === 'modifier' && { color: '#EF4444' }]} allowFontScaling={false}>
                          ${parseFloat(String(line.amount)).toFixed(2)}
                        </Text>
                      ) : (
                        <Text style={[styles.fareValue, { color: '#EF4444' }]}>Applied</Text>
                      )}
                    </View>
                  ) : null
                ))}
                {appliedPromo && promoDiscount > 0 && (
                  <View style={[styles.fareRow, { marginTop: 2 }]}>
                    <Text style={[styles.fareLabel, { color: '#10B981' }]}>Promo ({appliedPromo.code})</Text>
                    <Text style={[styles.fareValue, { color: '#10B981' }]} allowFontScaling={false}>-${promoDiscount.toFixed(2)}</Text>
                  </View>
                )}
              </View>
            )}
            <View style={[styles.fareDivider, { marginTop: 12 }]} />
          </>
        )}
        {selectedPayment === 'card' && (
          <View style={styles.holdNote}>
            <Ionicons name="lock-closed-outline" size={15} color={colors.textDim} style={{ marginRight: 8, marginTop: 1 }} />
            {/*
              The hold equals the fare shown above — the backend authorizes
              grand_total exactly (RIDE_AUTH_BUFFER_CAD is 0). This used to read
              `totalFare + 10` because the hold carried a $10 tip buffer, which
              made a $5 ride announce a $15 hold. Do NOT reintroduce arithmetic
              here: the buffer is a server-side decision this screen cannot see,
              so a hardcoded number can only ever drift out of sync with it.
            */}
            <Text style={styles.holdNoteText}>
              A temporary hold of ${totalFare.toFixed(2)} is placed on your card — the same as your fare,
              not an extra charge. You&apos;re only charged once the ride ends.
            </Text>
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
      </Animated.View>
      <ConfirmSheet
        visible={confirmSheet.visible}
        title={confirmSheet.title}
        message={confirmSheet.message}
        variant={confirmSheet.variant}
        buttons={confirmSheet.buttons.length ? confirmSheet.buttons : [{ text: 'OK' }]}
        onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))}
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

function createStyles(colors: ThemeColors, sf: (size: number) => number = (s) => s) {
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
      fontSize: sf(18),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    content: {
      flex: 1,
    },
    rideSummary: {
      backgroundColor: colors.surface,
      marginBottom: 12,
      marginHorizontal: 12,
      marginTop: 12,
      padding: 20,
      borderRadius: 16,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.06,
      shadowRadius: 8,
      elevation: 2,
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
      fontSize: sf(18),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    vehicleDesc: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    totalPrice: {
      fontSize: sf(22),
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
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      marginBottom: 2,
    },
    routeAddress: {
      fontSize: sf(15),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    section: {
      backgroundColor: colors.surface,
      padding: 20,
      marginBottom: 12,
      marginHorizontal: 12,
      borderRadius: 16,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.06,
      shadowRadius: 8,
      elevation: 2,
    },
    sectionTitle: {
      fontSize: sf(16),
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
      fontSize: sf(15),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    paymentDetails: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
    },
    walletBalance: {
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
      marginTop: 2,
    },
    holdNote: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      backgroundColor: '#F3F4F6',
      borderRadius: 10,
      paddingVertical: 10,
      paddingHorizontal: 12,
      marginTop: 12,
    },
    holdNoteText: {
      flex: 1,
      fontSize: sf(12),
      lineHeight: 17,
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
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.primary,
      marginLeft: 8,
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
      fontSize: sf(16),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    totalAmount: {
      fontSize: sf(24),
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
      fontSize: sf(17),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#FFFFFF',
    },
    discountRow: {
      flexDirection: 'row',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 8,
    },
    discountLabel: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: '#10B981',
    },
    discountAmount: {
      fontSize: sf(16),
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
      fontSize: sf(13),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    fareBreakdown: {
      backgroundColor: colors.surface,
      padding: 20,
      marginBottom: 12,
      marginHorizontal: 12,
      borderRadius: 16,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.06,
      shadowRadius: 8,
      elevation: 2,
    },
    fareBreakdownTitle: {
      fontSize: sf(16),
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
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: '#6B7280',
    },
    fareDriverBadge: {
      fontSize: sf(11),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: '#10B981',
      marginTop: 2,
    },
    fareValue: {
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    fareDivider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 10,
    },
    fareHeader: {
      flexDirection: 'row' as const,
      alignItems: 'center' as const,
      paddingVertical: 4,
    },
    fareToggleHint: {
      fontSize: sf(12),
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 2,
    },
    fareTotalLabel: {
      fontSize: sf(16),
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
    },
    fareTotalValue: {
      fontSize: sf(18),
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
      fontSize: sf(15),
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    corporateSubtitle: {
      fontSize: sf(13),
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
      fontSize: sf(14),
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
  });
}
