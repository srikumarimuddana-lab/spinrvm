import React, { useEffect, useRef, useState, useMemo } from 'react';
import { computeTipOptions, isTipLadderReady, reconcileSelectedTip } from '../components/tipPresets';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, TextInput, Modal,
  Platform, ActivityIndicator, BackHandler, KeyboardAvoidingView, Animated, Image,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { PROVIDER_GOOGLE } from 'react-native-maps';
import { RouteLine } from '@shared/components/RouteLine';
import { RoutePins } from '@shared/components/RoutePins';
import { useRideStore } from '../store/rideStore';
import { useAuthStore } from '@shared/store/authStore';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import api, { getApiErrorMessage } from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { Analytics } from '@shared/analytics';
import { useStripe } from '@stripe/stripe-react-native';
import { attemptRidePayment, PaymentAlertButton } from '../utils/attemptRidePayment';
import { useSpinrPaymentSheet } from '../hooks/useSpinrPaymentSheet';
import { useCompletedRouteRefresh } from '@shared/hooks/useCompletedRouteRefresh';
import { routeQualityLabel, toReactNativeRouteSections, toReactNativeSegments } from '@shared/utils/routeSegments';
import { useAnimatedValue } from '../hooks/useAnimatedValue';
import { onRideRated } from '@shared/utils/appRating';

// PR #664 stringified Decimal money fields in API responses (e.g. total_fare,
// base_fare, tip_amount). The receipt UI needs them as numbers for arithmetic
// and as 2-dp display strings. These two helpers narrow `MoneyString | number
// | undefined` cleanly without spreading parseFloat noise across the file.
const toNum = (v: string | number | null | undefined): number =>
  typeof v === 'number' ? v : v ? parseFloat(v) || 0 : 0;

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

function RideCompletedScreenContent() {
  const router = useRouter();
  // payWithCard is set when the rider returns from the "Change Card" escape
  // (manage-cards) having picked a different card for this stuck trip. tip/rated
  // are carried back so the remounted screen re-charges the SAME tip the rider
  // already chose (and was credited to the driver via /rate on the first
  // attempt) instead of resetting to 0, and skips re-rating (which would
  // overwrite the original stars/comment). See Codex 62i6.
  const { rideId, payWithCard, tip: tipParam, rated: ratedParam } = useLocalSearchParams<{
    rideId: string;
    payWithCard?: string;
    tip?: string;
    rated?: string;
  }>();
  const { currentRide, currentDriver, fetchRide, rateRide, clearRide } = useRideStore();

  // P0-5: confirmPayment is called when the backend returns
  // requires_action for 3DS / SCA. StripeProvider is wired at the app
  // root in app/_layout.tsx so useStripe() resolves here.
  const { confirmPayment } = useStripe();
  const { presentSheet, isLoading: sheetLoading } = useSpinrPaymentSheet();

  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const insets = useSafeAreaInsets();

  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState('');
  const [selectedTip, setSelectedTip] = useState<number | null>(null);
  // Rehydrate the tip the rider chose before the Change Card detour so the
  // re-charge collects it and the receipt matches (Codex 62i6).
  const [customTip, setCustomTip] = useState(
    typeof tipParam === 'string' && parseFloat(tipParam) > 0 ? tipParam : '',
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitPhase, setSubmitPhase] = useState<'idle' | 'rating' | 'confirming'>('idle');
  const [alreadyPaid, setAlreadyPaid] = useState(false);

  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons: { text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }[];
  }>({ visible: false, title: '', message: '', variant: 'info', buttons: [] });
  const mapRef = React.useRef<MapView>(null);
  const [routeMapReady, setRouteMapReady] = useState(false);
  const successScale = useAnimatedValue(0);
  const successOpacity = useAnimatedValue(0);

  const actualSections = useMemo(
    () => toReactNativeRouteSections(currentRide?.actual_route_segments),
    [currentRide?.actual_route_segments],
  );
  const plannedSegments = useMemo(() => {
    // Read the field once so the compiler's inferred dependency matches the
    // declared one exactly (react-hooks/preserve-manual-memoization): the
    // previous two-site read — one optional-chained (`currentRide?.…`), one
    // not (`currentRide.…`) — made the inferred dependency the coarser
    // `currentRide` object instead of the declared `.planned_route_polyline`
    // field, so manual memoization silently wasn't being preserved.
    const polyline = currentRide?.planned_route_polyline;
    return toReactNativeSegments(polyline ? [polyline] : []);
  }, [currentRide?.planned_route_polyline]);
  const isV2Route = toNum(currentRide?.route_schema_version) >= 2;
  const hasActualRoute = actualSections.length > 0;
  const mapCoordinates = useMemo(
    () => hasActualRoute
      ? actualSections.reduce<any[]>((all, section) => all.concat(section.coordinates), [])
      : (isV2Route ? [] : plannedSegments).reduce<any[]>((all, segment) => all.concat(segment), []),
    [actualSections, hasActualRoute, isV2Route, plannedSegments],
  );
  const routeLabel = hasActualRoute ? 'Actual route' : isV2Route ? 'Actual route' : 'Planned route';
  const routeQuality = routeQualityLabel(currentRide?.route_quality);
  const routeIsProcessing =
    currentRide?.route_geometry_status === 'pending' || currentRide?.route_geometry_status === 'processing';
  const routeStatus = hasActualRoute
    ? routeQuality
    : isV2Route
      ? routeIsProcessing
        ? 'Actual route processing'
        : 'Actual route unavailable'
      : 'Planned route preview';

  useCompletedRouteRefresh(currentRide, async () => {
    if (rideId) await fetchRide(rideId);
  });

  useEffect(() => {
    if (!routeMapReady || mapCoordinates.length < 2) return;
    mapRef.current?.fitToCoordinates(mapCoordinates, {
      edgePadding: { top: 30, right: 30, bottom: 30, left: 30 },
      animated: false,
    });
  }, [routeMapReady, mapCoordinates]);

  useEffect(() => {
    Animated.parallel([
      Animated.spring(successScale, { toValue: 1, tension: 60, friction: 8, useNativeDriver: true }),
      Animated.timing(successOpacity, { toValue: 1, duration: 400, useNativeDriver: true }),
    ]).start();
    // Both are stable Animated.Value instances (useAnimatedValue, created once).
  }, [successScale, successOpacity]);

  const fare = toNum((currentRide as any)?.grand_total || currentRide?.total_fare);
  // Fare-scaled rather than a flat $2/$5/$10 ladder: a $10 preset on a $5 ride
  // reads as absurd, and flat presets under-suggest on long trips. The rules
  // that make this correct live in components/tipPresets.ts so they can be
  // tested directly.
  //
  // Depends on `fare`, NOT on `currentRide`: the store hands back a fresh object
  // on every WS event and on the 3s route-refresh poll, so a `[currentRide]` dep
  // would recompute the ladder for reasons that have nothing to do with money.
  const tipOptions = useMemo(() => computeTipOptions(fare), [fare]);

  // The ladder degenerates to [1,2,3] before the fare loads, which looks like a
  // deliberate low-value ladder rather than a placeholder. This screen is
  // reachable before that — a push-notification tap cold-starts into it — so the
  // presets must not be tappable yet.
  const tipReady = isTipLadderReady(fare);

  // Derived during render, NOT synced via an effect. If the fare resolves after
  // the rider already tapped, the amounts change underneath them and the stored
  // selection may match no button. Reading it through reconcile means the
  // charged amount can never diverge from the highlighted chip — not even for
  // the one frame an effect would take to catch up. (Also what
  // react-hooks/set-state-in-effect is asking for.)
  const effectiveTip = reconcileSelectedTip(selectedTip, tipOptions);
  // The lifecycle duration is recorded independently of GPS coverage. A gap in
  // location reporting must never turn a completed 40-minute ride into a
  // shorter trip in the rider's summary.
  const duration = toNum(currentRide?.actual_duration_minutes ?? currentRide?.duration_minutes);
  // GPS-measured distance first, billed/booking distance as legacy fallback —
  // the same source order as the ride-details history screen, so the number
  // shown here never changes when the rider reopens the ride later.
  const distance = currentRide?.actual_distance_km ?? currentRide?.distance_km ?? 0;
  // A card hold placed at booking is captured on submit — the payment method is
  // already chosen, so we don't show a payment picker (Google Pay) at the end.
  const hasHold =
    (currentRide as any)?.auth_status === 'authorized' || (currentRide as any)?.auth_status === 'fare_only';

  useEffect(() => {
    if (rideId) fetchRide(rideId);
    // fetchRide is a zustand store action (stable reference).
  }, [rideId, fetchRide]);

  // Check if ride was already paid (e.g. coming back to this screen)
  useEffect(() => {
    if (currentRide?.payment_status === 'paid') {
      // alreadyPaid isn't a dep of this effect (only payment_status is), and
      // this only ever sets true (never flips back), so no loop risk.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAlreadyPaid(true);
    }
  }, [currentRide?.payment_status]);

  // Auto-dismiss: if the rider lands on this screen and the ride is already
  // fully paid (stale routing from store race or navigation stack), clear
  // state and go home so they're not trapped in a loop. Only fires once,
  // on the first time ride data loads — so it won't interfere with the
  // normal pay-then-navigate flow in handleSubmit.
  const initialPaymentChecked = useRef(false);
  useEffect(() => {
    if (initialPaymentChecked.current || !currentRide?.payment_status) return;
    initialPaymentChecked.current = true;
    const ps = currentRide.payment_status;
    if (ps === 'paid' || ps === 'waived_admin') {
      clearRide();
      router.replace('/(tabs)');
    }
    // clearRide is a zustand action (stable); router is expo-router's
    // stable singleton. initialPaymentChecked ref already guards this to
    // fire at most once, so adding these doesn't change that.
  }, [currentRide?.payment_status, clearRide, router]);

  // Block back navigation — must complete rating & payment
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
    return () => sub.remove();
  }, []);

  const [driverPhotoError, setDriverPhotoError] = useState(false);
  const [lostItemText, setLostItemText] = useState('');
  const [lostItemVisible, setLostItemVisible] = useState(false);
  const [lostItemSending, setLostItemSending] = useState(false);
  const [emailSending, setEmailSending] = useState(false);
  const handleEmailReceipt = async () => {
    if (emailSending) return;
    setEmailSending(true);
    try {
      await api.post(`/rides/${rideId}/email-receipt`);
      const user = useAuthStore.getState().user;
      showToast('Receipt Sent', `Receipt emailed to ${user?.email || 'your registered email'}.`, 'success');
    } catch (e: any) {
      showToast('Email Not Sent', getApiErrorMessage(e, 'Could not send receipt email. Please try again.'), 'danger');
    } finally {
      setEmailSending(false);
    }
  };

  const handleLostFound = () => {
    setLostItemVisible(true);
  };

  const submitLostItem = async () => {
    if (!lostItemText.trim() || lostItemSending) return;
    setLostItemSending(true);
    try {
      await api.post(`/rides/${rideId}/lost-and-found`, {
        item_description: lostItemText.trim(),
        item_category: 'other',
      });
      setLostItemVisible(false);
      setLostItemText('');
      showToast('Report Submitted', "Your driver has been notified. They'll get back to you if the item is found.", 'success');
    } catch (e: any) {
      showToast('Report Not Sent', getApiErrorMessage(e, 'Could not submit report. Please try again.'), 'danger');
    } finally {
      setLostItemSending(false);
    }
  };

  // Tip is bundled into the single payment transaction in handleSubmit —
  // no separate API call. Selection is purely UI state until "Pay & Done".

  /**
   * Map a PaymentAttemptResult's alert (pure data) to the local
   * confirmSheet shape (with onPress handlers). Split from the attempt
   * itself so the attempt stays pure-data and unit-testable.
   */
  // handleSubmit is defined below but referenced by the Retry button here;
  // a ref breaks the definition cycle without reordering the whole component.
  const handleSubmitRef = useRef<(overrideCardId?: string) => void>(() => {});
  // The card the rider chose via "Change Card" — Retry must re-charge on THIS
  // card, not silently fall back to the booking/default card (Codex P2).
  const activeOverrideCardRef = useRef<string | undefined>(undefined);
  // Rating + tip is posted to /rate, which ACCUMULATES tip into driver_earnings
  // on every call. Across a retry (button or auto card-change) we must post it
  // at most once or the driver is over-credited while only one tip is charged
  // (Codex P1). This latches after the first attempt.
  // Seed from the route: if the first attempt already posted /rate before its
  // payment failed, the remounted screen must NOT re-rate (re-rating overwrites
  // the rider's original stars/comment and /rate accumulates driver_earnings).
  const hasRatedRef = useRef(ratedParam === '1');

  const showPaymentAlert = (alert: NonNullable<Awaited<ReturnType<typeof attemptRidePayment>>['alert']>) => {
    const buttons = (alert.buttons || []).map((b: PaymentAlertButton) => ({
      text: b.text,
      style: (b.kind === 'cancel' ? 'cancel' : 'default') as 'default' | 'cancel',
      onPress:
        b.kind === 'change_card'
          // Carry the ride + the already-chosen tip + whether we've rated so the
          // re-charge collects the same tip and doesn't re-rate (Codex 62i6).
          ? () => {
              const _tip = effectiveTip || (customTip ? parseFloat(customTip) || 0 : 0);
              router.push(
                `/manage-cards?rideId=${rideId}&forPayment=1&tip=${_tip}&rated=${hasRatedRef.current ? 1 : 0}` as any,
              );
            }
          : b.kind === 'support'
            // Pre-fill support with the stuck ride + a payment-failed topic.
            ? () => router.push(`/support?rideId=${rideId}&topic=payment_failed` as any)
            : b.kind === 'retry'
              ? () => handleSubmitRef.current?.(activeOverrideCardRef.current)
              : undefined,
    }));
    setConfirmSheet({
      visible: true,
      title: alert.title,
      message: alert.message,
      variant: alert.variant,
      buttons: buttons.length ? buttons : [{ text: 'OK' }],
    });
  };

  const handleSubmit = async (overrideCardId?: string) => {
    if (isSubmitting) return; // prevent double tap
    if (overrideCardId) activeOverrideCardRef.current = overrideCardId;
    setIsSubmitting(true);
    setSubmitPhase('rating');
    try {
      const tipAmount = effectiveTip || (customTip ? parseFloat(customTip) || 0 : 0);

      // 1. Rate the driver first — fire-and-forget, and ONLY once across
      //    retries. /rate accumulates tip into driver_earnings on every call,
      //    so re-rating on a card-change retry would over-credit the driver
      //    while the eventual payment charges a single tip (Codex P1).
      if (!hasRatedRef.current) {
        try {
          await rateRide(rideId as string, rating, comment || undefined, tipAmount > 0 ? tipAmount : undefined);
        } catch { /* rating may fail if already rated */ }
        hasRatedRef.current = true;
      }

      // 2. Process payment. If the rider has already paid (came back to
      //    this screen), skip the attempt entirely.
      setSubmitPhase('confirming');
      let paymentOk = alreadyPaid;
      let chargedAmount: number | undefined;
      if (!alreadyPaid) {
        const result = await attemptRidePayment({
          api,
          stripe: { confirmPayment },
          rideId: rideId as string,
          tipAmount,
          paymentMethodId: overrideCardId,
        });
        paymentOk = result.ok;
        chargedAmount = result.charged;
        if (!paymentOk && result.alert) {
          showPaymentAlert(result.alert);
        }
      }

      // If payment failed, stay on this screen so the rider can fix
      // their card and retry. Alert has already been shown.
      if (!paymentOk) {
        return;
      }

      const total = toNum(currentRide?.total_fare) + toNum(tipAmount);
      Analytics.paymentCompleted({ method: 'default', amount: chargedAmount ?? total });

      Analytics.rideCompleted({
        fare: toNum(currentRide?.total_fare),
        distance_km: currentRide?.distance_km,
      });

      // 3. Trigger app store rating prompt after good rides
      try {
        await onRideRated(rating);
      } catch { /* non-critical */ }

      clearRide();
      router.replace('/(tabs)');
    } catch (e) {
      showToast('Submit Failed', getApiErrorMessage(e, 'Failed to submit. Please try again.'), 'danger');
    } finally {
      setIsSubmitting(false);
      setSubmitPhase('idle');
    }
  };
  useEffect(() => {
    handleSubmitRef.current = handleSubmit;
  });

  // Returned from the "Change Card" escape with a card chosen for this trip —
  // auto-retry the charge on it once. The ref guard stops a re-run on every
  // re-render while the param is still in the route.
  const retriedCardRef = useRef<string | null>(null);
  useEffect(() => {
    if (!payWithCard || alreadyPaid) return;
    if (retriedCardRef.current === payWithCard) return;
    retriedCardRef.current = payWithCard;
    // Routed through handleSubmitRef (already the established pattern in
    // this file for the Retry button, line ~279) instead of calling
    // handleSubmit directly — handleSubmit is a plain async function that
    // closes over many reactive values (isSubmitting, effectiveTip,
    // customTip, alreadyPaid, rating, comment, confirmPayment, currentRide,
    // clearRide, router, …); wrapping all of that in a useCallback with a
    // complete, correct dep list would be high-risk for a payment-path
    // function. handleSubmitRef.current is kept current every render by the
    // effect above (runs with no deps array), so by the time this effect
    // body runs it's always the latest handleSubmit — same behavior as
    // calling handleSubmit directly, without needing it in this effect's
    // deps (refs are exempt from exhaustive-deps).
    handleSubmitRef.current?.(payWithCard);
  }, [payWithCard, alreadyPaid]);

  // Google Pay path — presents the Stripe PaymentSheet modal so riders can
  // pay without re-entering a card. Only shown for card payment rides on
  // Android (Apple Pay uses the same sheet on iOS via handleSubmit in future).
  const handleGooglePay = async () => {
    if (isSubmitting || sheetLoading || alreadyPaid) return;
    const tipAmount = effectiveTip || (customTip ? parseFloat(customTip) || 0 : 0);
    const result = await presentSheet({
      rideId: rideId as string,
      amount: toNum(currentRide?.total_fare),
      tipAmount,
    });
    if (!result.ok) {
      if (result.errorMessage !== 'Payment cancelled.') {
        showToast('Payment Failed', result.errorMessage || 'Please try again.', 'danger');
      }
      return;
    }
    try {
      await rateRide(rideId as string, rating, comment || undefined, tipAmount > 0 ? tipAmount : undefined);
    } catch { /* already rated guard */ }
    const total = toNum(currentRide?.total_fare) + toNum(tipAmount);
    Analytics.paymentCompleted({ method: 'google_pay', amount: total });
    Analytics.rideCompleted({ fare: toNum(currentRide?.total_fare), distance_km: currentRide?.distance_km });
    clearRide();
    router.replace('/(tabs)');
  };

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>

        {/* ═══ Animated Success Header ═══ */}
        <Animated.View style={[styles.successSection, { opacity: successOpacity, transform: [{ scale: successScale }] }]}>
          <View style={styles.checkRing}>
            <View style={styles.checkCircle}>
              <Ionicons name="checkmark" size={32} color="#FFF" />
            </View>
          </View>
          <Text style={styles.title}>You&apos;ve arrived!</Text>
          <Text style={styles.subtitle} numberOfLines={1}>
            {currentRide?.dropoff_address || 'Destination'}
          </Text>
          <View style={styles.rideDateRow}>
            <Ionicons name="calendar-outline" size={13} color={colors.textDim} />
            <Text style={styles.rideDateText}>
              {currentRide?.ride_completed_at
                ? new Date(currentRide.ride_completed_at).toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' })
                : new Date().toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' })}
            </Text>
            <View style={styles.rideCodeBadge}>
              <Text style={styles.rideCodeText}>
                {currentRide?.ride_code || currentRide?.id?.slice(0, 8).toUpperCase() || '---'}
              </Text>
            </View>
          </View>
        </Animated.View>

        {/* ═══ 1. Rate Driver ═══ */}
        <View style={styles.rateCard}>
          <View style={styles.driverRow}>
            <View style={styles.driverAvatarWrap}>
              <View style={styles.driverAvatar}>
                {currentDriver?.photo_url && !driverPhotoError ? (
                  <Image
                    source={{ uri: currentDriver.photo_url }}
                    style={styles.driverAvatarImg}
                    resizeMode="cover"
                    onError={() => setDriverPhotoError(true)}
                  />
                ) : (
                  <Ionicons name="person" size={26} color="#FFF" />
                )}
              </View>
              <View style={styles.driverRatingBadge}>
                <Ionicons name="star" size={9} color="#FFF" />
                <Text style={styles.driverRatingText}>{currentDriver?.rating || '5.0'}</Text>
              </View>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.driverName}>{currentDriver?.name || 'Your Driver'}</Text>
              <Text style={styles.driverMeta}>
                {[currentDriver?.vehicle_color, currentDriver?.vehicle_make, currentDriver?.vehicle_model].filter(Boolean).join(' ')}
              </Text>
              <View style={styles.plateRow}>
                <View style={styles.plateBadge}>
                  <Text style={styles.plateText}>{currentDriver?.license_plate || '---'}</Text>
                </View>
                {(currentDriver as any)?.total_rides > 0 && (
                  <Text style={styles.tripCountText}>{(currentDriver as any).total_rides} trips</Text>
                )}
              </View>
            </View>
          </View>

          <View style={styles.rateDivider} />

          <Text style={styles.rateLabel}>How was your ride?</Text>
          <View style={styles.starsRow}>
            {[1, 2, 3, 4, 5].map((star) => (
              <TouchableOpacity
                key={star}
                onPress={() => setRating(star)}
                style={styles.starBtn}
                accessibilityLabel={`Rate ${star} star${star > 1 ? 's' : ''}`}
                accessibilityRole="button"
                accessibilityState={{ selected: rating === star }}
              >
                <Ionicons
                  name={star <= rating ? 'star' : 'star-outline'}
                  size={42}
                  color={star <= rating ? '#FFB800' : colors.border}
                />
              </TouchableOpacity>
            ))}
          </View>
          <Text style={styles.ratingText}>
            {rating === 5 ? 'Excellent ride!' : rating === 4 ? 'Great experience' : rating === 3 ? 'It was okay' : rating === 2 ? 'Could be better' : 'Needs improvement'}
          </Text>

          <TextInput
            style={styles.commentInput}
            placeholder="Tell us more (optional)..."
            placeholderTextColor={colors.textDim}
            value={comment}
            onChangeText={setComment}
            multiline
            maxLength={200}
          />
        </View>

        {/* ═══ 2. Tip Section ═══ */}
        <View style={styles.tipCard}>
          <View style={styles.tipHeader}>
            <View style={styles.tipIconWrap}>
              <Ionicons name="heart" size={18} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.tipTitle}>Say thanks with a tip</Text>
              <Text style={styles.tipSubtitle}>100% goes to {currentDriver?.name?.split(' ')[0] || 'your driver'}</Text>
            </View>
          </View>
          <View style={styles.tipRow}>
              {tipOptions.map((amt, idx) => (
                <TouchableOpacity
                  key={amt}
                  style={[
                    styles.tipBtn,
                    effectiveTip === amt && styles.tipBtnActive,
                    !tipReady && styles.tipBtnDisabled,
                  ]}
                  // Disabled until the fare loads: these amounts are derived from
                  // it, so tapping the [1,2,3] placeholder would pick a tip that
                  // silently changes (or vanishes) a moment later.
                  disabled={!tipReady}
                  onPress={() => setSelectedTip(effectiveTip === amt ? null : amt)}
                  accessibilityRole="radio"
                  accessibilityLabel={`Tip $${amt}`}
                  accessibilityState={{ checked: effectiveTip === amt, disabled: !tipReady }}
                >
                  {/* Keyed on position, not amount: the amounts are fare-derived
                      now, so a value-based map (2/5/10) would fall through to the
                      default emoji on almost every ride. */}
                  <Text style={[styles.tipBtnEmoji, effectiveTip === amt && styles.tipBtnEmojiActive]}>
                    {idx === 0 ? '☕' : idx === 1 ? '🍕' : '🎉'}
                  </Text>
                  <Text style={[styles.tipBtnText, effectiveTip === amt && styles.tipBtnTextActive]}>${amt}</Text>
                </TouchableOpacity>
              ))}
              <View style={[styles.tipCustom, customTip ? styles.tipCustomActive : null]}>
                <Text style={styles.tipDollar}>$</Text>
                <TextInput
                  style={styles.tipCustomInput}
                  placeholder="Other"
                  placeholderTextColor="#BBB"
                  keyboardType="decimal-pad"
                  value={customTip}
                  onChangeText={(t) => { setCustomTip(t); setSelectedTip(null); }}
                  returnKeyType="done"
                />
              </View>
            </View>
        </View>

        {/* ═══ 3. Fare + Stats ═══ */}
        <View style={styles.fareCard}>
          <View style={styles.fareTopRow}>
            <View>
              <Text style={styles.fareLabel}>TRIP TOTAL</Text>
              <Text style={styles.fareAmount} allowFontScaling={false}>${fare.toFixed(2)}</Text>
            </View>
            <View style={styles.paymentBadge}>
              <Ionicons
                name={currentRide?.payment_method === 'wallet' ? 'wallet' : currentRide?.payment_method === 'company_allowance' ? 'business' : 'card'}
                size={14}
                color={colors.primary}
              />
              <Text style={styles.paymentText}>
                {currentRide?.payment_method === 'wallet'
                  ? 'Spinr Wallet'
                  : currentRide?.payment_method === 'company_allowance'
                    ? 'Company Account'
                    : currentRide?.card_last4
                    ? `•••• ${currentRide.card_last4}`
                    : 'Card'}
              </Text>
              {alreadyPaid && (
                <View style={styles.paidChip}>
                  <Ionicons name="checkmark-circle" size={12} color="#FFF" />
                  <Text style={styles.paidChipText}>PAID</Text>
                </View>
              )}
            </View>
          </View>

          <View style={styles.statsRow}>
            <View style={styles.statCard}>
              <View style={[styles.statIconWrap, { backgroundColor: '#EFF6FF' }]}>
                <Ionicons name="time-outline" size={16} color="#3B82F6" />
              </View>
              <Text style={styles.statVal} allowFontScaling={false}>{duration} min</Text>
              <Text style={styles.statLbl}>Duration</Text>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.statIconWrap, { backgroundColor: '#F0FDF4' }]}>
                <Ionicons name="navigate-outline" size={16} color="#10B981" />
              </View>
              <Text style={styles.statVal} allowFontScaling={false}>{distance.toFixed(1)} km</Text>
              <Text style={styles.statLbl}>Distance</Text>
            </View>
            <View style={styles.statCard}>
              <View style={[styles.statIconWrap, { backgroundColor: '#FEF3C7' }]}>
                <Ionicons name="flash-outline" size={16} color="#D97706" />
              </View>
              <Text style={styles.statVal} allowFontScaling={false}>
                {duration > 0 ? (distance / (duration / 60)).toFixed(0) : '0'} km/h
              </Text>
              <Text style={styles.statLbl}>Avg Speed</Text>
            </View>
          </View>
        </View>

        {/* ═══ 4. Route Map — actual GPS geometry only; planned preview is explicit ═══ */}
        {currentRide && Number(currentRide.pickup_lat) && Number(currentRide.dropoff_lat) && (
          <View style={styles.mapCard}>
            <MapView
              ref={mapRef}
              style={styles.map}
              provider={MAP_PROVIDER}
              scrollEnabled={false}
              zoomEnabled={false}
              rotateEnabled={false}
              pitchEnabled={false}
              userInterfaceStyle={isDark ? 'dark' : 'light'}
              initialRegion={{
                latitude: (Number(currentRide.pickup_lat) + Number(currentRide.dropoff_lat)) / 2,
                longitude: (Number(currentRide.pickup_lng) + Number(currentRide.dropoff_lng)) / 2,
                latitudeDelta: Math.abs(Number(currentRide.pickup_lat) - Number(currentRide.dropoff_lat)) * 2.5 + 0.01,
                longitudeDelta: Math.abs(Number(currentRide.pickup_lng) - Number(currentRide.dropoff_lng)) * 2.5 + 0.01,
              }}
              onMapReady={() => setRouteMapReady(true)}
            >
              {/* Real reconstructed route as one shared orange→red gradient.
                  v2 sections are passed SEPARATELY (paths) so a GPS gap is never
                  bridged by a false chord; the legacy planned polyline is one
                  continuous path. No straight fallback: an unmeasured route draws
                  nothing. */}
              {hasActualRoute ? (
                <RouteLine paths={actualSections.map((s) => s.coordinates)} />
              ) : (
                <RouteLine path={mapCoordinates} />
              )}
              <RoutePins
                pickup={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
                dropoff={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
                completion={currentRide.actual_completion_point || null}
              />
            </MapView>

            {/* Address overlay */}
            <View style={styles.mapOverlay}>
              <View style={styles.mapRouteStatus}>
                <Ionicons name={hasActualRoute ? 'navigate-circle-outline' : 'map-outline'} size={14} color="#2563EB" />
                <Text style={styles.mapRouteStatusText} numberOfLines={1}>{routeLabel} · {routeStatus}</Text>
              </View>
              <View style={styles.mapAddrRow}>
                <View style={[styles.mapAddrDot, { backgroundColor: '#10B981' }]} />
                <Text style={styles.mapAddrText} numberOfLines={1}>{currentRide?.pickup_address || 'Pickup'}</Text>
              </View>
              <View style={styles.mapAddrDivider} />
              <View style={styles.mapAddrRow}>
                <View style={[styles.mapAddrDot, { backgroundColor: '#EF4444' }]} />
                <Text style={styles.mapAddrText} numberOfLines={1}>{currentRide?.dropoff_address || 'Dropoff'}</Text>
              </View>
            </View>
          </View>
        )}

        {/* ═══ 5. Post-Trip Actions ═══ */}
        <View style={styles.actionsCard}>
          <TouchableOpacity
            style={styles.actionBtn}
            onPress={() => router.push(`/chat-driver?rideId=${rideId}` as any)}
            activeOpacity={0.7}
          >
            <View style={[styles.actionIcon, { backgroundColor: '#EFF6FF' }]}>
              <Ionicons name="chatbubble-ellipses" size={20} color="#3B82F6" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.actionBtnTitle}>Message Driver</Text>
              <Text style={styles.actionBtnDesc}>Forgot something? Chat with your driver</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.border} />
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.actionBtn}
            onPress={handleEmailReceipt}
            disabled={emailSending}
            activeOpacity={0.7}
          >
            <View style={[styles.actionIcon, { backgroundColor: '#F0FDF4' }]}>
              <Ionicons name="mail" size={20} color="#10B981" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.actionBtnTitle}>Email Receipt</Text>
              <Text style={styles.actionBtnDesc}>Get a detailed breakdown in your inbox</Text>
            </View>
            {emailSending ? (
              <ActivityIndicator size="small" color={colors.textDim} />
            ) : (
              <Ionicons name="chevron-forward" size={18} color={colors.border} />
            )}
          </TouchableOpacity>

          <TouchableOpacity
            style={[styles.actionBtn, { borderBottomWidth: 0 }]}
            onPress={handleLostFound}
            activeOpacity={0.7}
          >
            <View style={[styles.actionIcon, { backgroundColor: '#FEF2F2' }]}>
              <Ionicons name="bag-handle" size={20} color="#EF4444" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.actionBtnTitle}>Report Lost Item</Text>
              <Text style={styles.actionBtnDesc}>Left something in the car? We&apos;ll help</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.border} />
          </TouchableOpacity>
        </View>

        {/* Spinr branding */}
        <View style={styles.brandingRow}>
          <Ionicons name="car-sport" size={14} color={colors.textDim} />
          <Text style={styles.brandingText}>Spinr · 0% driver commission</Text>
        </View>

      </ScrollView>
      </KeyboardAvoidingView>

      {/* Submit Button */}
      <View style={styles.bottomBar}>
        {hasHold && !alreadyPaid && (
          <Text style={styles.holdHint}>
            Charged to the card you chose at booking. Any tip is included in the same charge.
          </Text>
        )}
        {/* Google Pay button — Android card rides with NO pre-auth hold (the held
            method is captured on submit, so no payment picker is shown then). */}
        {Platform.OS === 'android' && !alreadyPaid && !hasHold && currentRide?.payment_method === 'card' && (
          <TouchableOpacity
            style={[styles.submitBtn, styles.googlePayBtn]}
            onPress={handleGooglePay}
            disabled={isSubmitting || sheetLoading}
            activeOpacity={0.8}
            accessibilityRole="button"
            accessibilityLabel="Pay with Google Pay"
            accessibilityState={{ disabled: isSubmitting || sheetLoading }}
          >
            {sheetLoading ? (
              <ActivityIndicator size="small" color="#FFF" />
            ) : (
              <>
                <Ionicons name="logo-google" size={18} color="#FFF" />
                <Text style={styles.submitBtnText}>Pay with Google Pay</Text>
              </>
            )}
          </TouchableOpacity>
        )}
        <TouchableOpacity
          style={styles.submitBtn}
          onPress={() => handleSubmit()}
          disabled={isSubmitting || sheetLoading}
          activeOpacity={0.8}
          accessibilityRole="button"
          accessibilityLabel={alreadyPaid ? 'Rate and finish' : `Pay and finish`}
          accessibilityState={{ disabled: isSubmitting || sheetLoading, busy: isSubmitting }}
        >
          {isSubmitting ? (
            <>
              <ActivityIndicator size="small" color="#FFF" />
              <Text style={styles.submitBtnText}>
                {submitPhase === 'confirming' ? 'Confirming payment…' : 'Submitting…'}
              </Text>
            </>
          ) : (
            <>
              <Text style={styles.submitBtnText}>
                {alreadyPaid
                  ? 'Rate & Done'
                  : `Pay $${(fare + (effectiveTip || (customTip ? parseFloat(customTip) || 0 : 0))).toFixed(2)} & Done`
                }
              </Text>
              <Ionicons name={alreadyPaid ? 'checkmark' : 'card'} size={18} color="#FFF" />
            </>
          )}
        </TouchableOpacity>
      </View>
      <Modal visible={lostItemVisible} transparent animationType="fade" onRequestClose={() => setLostItemVisible(false)}>
        <KeyboardAvoidingView
          style={styles.modalKeyboardAvoider}
          behavior="padding"
          keyboardVerticalOffset={Platform.OS === 'ios' ? insets.top : 0}
        >
          <View style={[styles.modalOverlay, { paddingBottom: Math.max(insets.bottom, 24) }]}>
            <ScrollView
              style={styles.modalScroll}
              contentContainerStyle={styles.modalScrollContent}
              keyboardDismissMode={Platform.OS === 'ios' ? 'interactive' : 'on-drag'}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              <View style={styles.modalCard}>
                <View style={styles.modalHeader}>
                  <View style={[styles.actionIcon, { backgroundColor: '#FEF2F2' }]}>
                    <Ionicons name="bag-handle" size={22} color="#EF4444" />
                  </View>
                  <Text style={styles.modalTitle}>Report Lost Item</Text>
                </View>
                <Text style={styles.modalDesc}>
                  Describe what you left behind. Your driver will be notified and can contact you through the app.
                </Text>
                <TextInput
                  style={styles.modalInput}
                  placeholder="e.g. Black backpack with laptop inside"
                  placeholderTextColor={colors.textDim}
                  value={lostItemText}
                  onChangeText={setLostItemText}
                  multiline
                  maxLength={500}
                  autoFocus
                />
                <View style={styles.modalBtnRow}>
                  <TouchableOpacity style={styles.modalCancelBtn} onPress={() => { setLostItemVisible(false); setLostItemText(''); }}>
                    <Text style={styles.modalCancelText}>Cancel</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.modalSubmitBtn, !lostItemText.trim() && { opacity: 0.5 }]}
                    onPress={submitLostItem}
                    disabled={!lostItemText.trim() || lostItemSending}
                  >
                    {lostItemSending ? (
                      <ActivityIndicator size="small" color="#FFF" />
                    ) : (
                      <Text style={styles.modalSubmitText}>Submit Report</Text>
                    )}
                  </TouchableOpacity>
                </View>
              </View>
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </Modal>
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

export default function RideCompletedScreen() {
  return (
    <ErrorBoundary>
      <RideCompletedScreenContent />
    </ErrorBoundary>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: '#F5F5F5' },
    content: { paddingHorizontal: 16, paddingTop: 20, paddingBottom: 20, gap: 10 },

    // ── Success header ──
    successSection: { alignItems: 'center', paddingVertical: 10 },
    checkRing: {
      width: 80, height: 80, borderRadius: 40,
      borderWidth: 3, borderColor: `${colors.primary}20`,
      justifyContent: 'center', alignItems: 'center', marginBottom: 14,
    },
    checkCircle: {
      width: 62, height: 62, borderRadius: 31, backgroundColor: colors.primary,
      justifyContent: 'center', alignItems: 'center',
    },
    title: {
      fontSize: 24, fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text, marginBottom: 4,
    },
    subtitle: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim, marginBottom: 8, paddingHorizontal: 20, textAlign: 'center',
    },
    rideDateRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    rideDateText: {
      fontSize: 12, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim,
    },
    rideCodeBadge: {
      backgroundColor: colors.surface, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
      borderWidth: 1, borderColor: colors.border,
    },
    rideCodeText: {
      fontSize: 11, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.textDim, letterSpacing: 0.5,
    },

    // ── Rate card ──
    rateCard: {
      backgroundColor: colors.surface, borderRadius: 16, padding: 20,
      borderWidth: 1, borderColor: colors.border,
    },
    driverRow: { flexDirection: 'row', alignItems: 'center' },
    driverAvatarWrap: { marginRight: 14, position: 'relative' as const },
    driverAvatar: {
      width: 56, height: 56, borderRadius: 28, backgroundColor: colors.primary,
      justifyContent: 'center', alignItems: 'center', overflow: 'hidden' as const,
    },
    driverAvatarImg: { width: 56, height: 56, borderRadius: 28 },
    driverRatingBadge: {
      position: 'absolute', bottom: -4, right: -4,
      flexDirection: 'row', alignItems: 'center', gap: 2,
      backgroundColor: '#FFB800', borderRadius: 10,
      paddingHorizontal: 6, paddingVertical: 2,
      borderWidth: 2, borderColor: colors.surface,
    },
    driverRatingText: {
      fontSize: 10, fontFamily: 'PlusJakartaSans_700Bold', color: '#FFF',
    },
    driverName: {
      fontSize: 17, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    },
    driverMeta: {
      fontSize: 13, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 2,
    },
    plateRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 },
    plateBadge: {
      backgroundColor: colors.surfaceLight, paddingHorizontal: 8, paddingVertical: 3,
      borderRadius: 6, borderWidth: 1, borderColor: colors.border,
    },
    plateText: {
      fontSize: 11, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text, letterSpacing: 1,
    },
    tripCountText: {
      fontSize: 11, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim,
    },
    rateDivider: { height: 1, backgroundColor: colors.border, marginVertical: 18 },
    rateLabel: {
      fontSize: 16, fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text, textAlign: 'center', marginBottom: 14,
    },
    starsRow: { flexDirection: 'row', justifyContent: 'center', gap: 8, marginBottom: 8 },
    starBtn: { width: 50, height: 50, alignItems: 'center', justifyContent: 'center' },
    ratingText: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.primary, textAlign: 'center', marginBottom: 16,
    },
    commentInput: {
      backgroundColor: colors.surfaceLight, borderRadius: 12, padding: 14,
      fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular', color: colors.text,
      minHeight: 60, textAlignVertical: 'top',
      borderWidth: 1, borderColor: colors.border,
    },

    // ── Tip ──
    tipCard: {
      backgroundColor: colors.surface, borderRadius: 16, padding: 20,
      borderWidth: 1, borderColor: colors.border,
    },
    tipHeader: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 },
    tipIconWrap: {
      width: 40, height: 40, borderRadius: 20,
      backgroundColor: `${colors.primary}12`, justifyContent: 'center', alignItems: 'center',
    },
    tipTitle: {
      fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text,
      marginBottom: 0, textAlign: 'left',
    },
    tipSubtitle: {
      fontSize: 12, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 2,
    },
    tipRow: { flexDirection: 'row', gap: 10, justifyContent: 'center' },
    tipBtn: {
      paddingHorizontal: 18, paddingVertical: 12, borderRadius: 14,
      backgroundColor: colors.surface, borderWidth: 1.5, borderColor: colors.border,
      minHeight: 60, justifyContent: 'center', alignItems: 'center',
    },
    tipBtnActive: {
      backgroundColor: colors.text, borderColor: colors.text,
    },
    // Reads as "not ready yet" rather than "unavailable" — the fare is still
    // loading and these amounts are derived from it.
    tipBtnDisabled: { opacity: 0.4 },
    tipBtnEmoji: { fontSize: 18, marginBottom: 2 },
    tipBtnEmojiActive: {},
    tipBtnText: {
      fontSize: 15, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    },
    tipBtnTextActive: { color: '#FFF' },
    tipCustom: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surface, borderRadius: 14, borderWidth: 1.5, borderColor: colors.border,
      paddingHorizontal: 14, minWidth: 80, minHeight: 60,
    },
    tipCustomActive: { borderColor: colors.text, backgroundColor: colors.text },
    tipDollar: { fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.textDim },
    tipCustomInput: {
      fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text,
      paddingVertical: 10, paddingHorizontal: 4, minWidth: 44,
    },
    // ── Fare card ──
    fareCard: {
      backgroundColor: colors.surface, borderRadius: 16, padding: 20,
      borderWidth: 1, borderColor: colors.border,
    },
    fareTopRow: {
      flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16,
    },
    fareLabel: {
      fontSize: 11, fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim, letterSpacing: 1, marginBottom: 4,
    },
    fareAmount: {
      fontSize: 34, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    },
    paymentBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 6,
      backgroundColor: colors.surfaceLight, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10,
      borderWidth: 1, borderColor: colors.border,
    },
    paymentText: {
      fontSize: 12, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text,
    },
    paidChip: {
      flexDirection: 'row', alignItems: 'center', gap: 3,
      backgroundColor: '#10B981', paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6,
      marginLeft: 4,
    },
    paidChipText: {
      fontSize: 10, fontFamily: 'PlusJakartaSans_700Bold', color: '#FFF',
    },
    statsRow: { flexDirection: 'row', gap: 8 },
    statCard: {
      flex: 1, alignItems: 'center', backgroundColor: colors.surfaceLight,
      borderRadius: 12, paddingVertical: 12, paddingHorizontal: 6,
      borderWidth: 1, borderColor: colors.border,
    },
    statIconWrap: {
      width: 30, height: 30, borderRadius: 8,
      justifyContent: 'center', alignItems: 'center', marginBottom: 6,
    },
    statVal: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    },
    statLbl: {
      fontSize: 10, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim, marginTop: 2,
    },

    // ── Route map ──
    mapCard: {
      height: 220, borderRadius: 16, overflow: 'hidden',
      backgroundColor: colors.border, borderWidth: 1, borderColor: colors.border,
    },
    map: { flex: 1 },
        mapOverlay: {
      position: 'absolute', bottom: 8, left: 8, right: 8,
      backgroundColor: 'rgba(255,255,255,0.95)', borderRadius: 10, padding: 10, paddingHorizontal: 14,
        },
        mapRouteStatus: { flexDirection: 'row', alignItems: 'center', gap: 5, marginBottom: 7 },
        mapRouteStatusText: {
          flex: 1, fontSize: 11, fontFamily: 'PlusJakartaSans_600SemiBold', color: '#2563EB',
        },
    mapAddrRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    mapAddrDot: { width: 8, height: 8, borderRadius: 4 },
    mapAddrText: {
      flex: 1, fontSize: 12, fontFamily: 'PlusJakartaSans_500Medium', color: '#1A1A1A',
    },
    mapAddrDivider: { height: 1, backgroundColor: '#E5E7EB', marginVertical: 6, marginLeft: 16 },

    // ── Actions ──
    actionsCard: {
      backgroundColor: colors.surface, borderRadius: 16, overflow: 'hidden',
      borderWidth: 1, borderColor: colors.border,
    },
    actionBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 14,
      paddingVertical: 15, paddingHorizontal: 16,
      borderBottomWidth: 1, borderBottomColor: colors.border,
    },
    actionIcon: {
      width: 42, height: 42, borderRadius: 12,
      justifyContent: 'center', alignItems: 'center',
    },
    actionBtnTitle: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text,
    },
    actionBtnDesc: {
      fontSize: 12, fontFamily: 'PlusJakartaSans_400Regular', color: colors.textDim, marginTop: 1,
    },

    // ── Branding ──
    brandingRow: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
      gap: 6, paddingVertical: 12, opacity: 0.35,
    },
    brandingText: {
      fontSize: 12, fontFamily: 'PlusJakartaSans_500Medium', color: colors.textDim,
    },

    // ── Bottom bar ──
    bottomBar: {
      paddingHorizontal: 16, paddingVertical: 12,
      backgroundColor: colors.surface,
      borderTopWidth: 1, borderTopColor: colors.border,
    },
    submitBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      backgroundColor: '#EF4444', paddingVertical: 16, borderRadius: 14,
    },
    googlePayBtn: {
      backgroundColor: '#3C4043', marginBottom: 10,
    },
    holdHint: {
      fontSize: 12, lineHeight: 16, color: colors.textDim,
      fontFamily: 'PlusJakartaSans_400Regular',
      textAlign: 'center', marginBottom: 10,
    },
    submitBtnText: {
      fontSize: 16, fontFamily: 'PlusJakartaSans_700Bold', color: '#FFF',
    },

    // ── Lost item modal ──
    modalKeyboardAvoider: {
      flex: 1,
    },
    modalOverlay: {
      flex: 1, backgroundColor: 'rgba(0,0,0,0.5)',
      paddingHorizontal: 24, paddingTop: 24,
    },
    modalScroll: {
      flex: 1, width: '100%',
    },
    modalScrollContent: {
      flexGrow: 1, justifyContent: 'center', alignItems: 'center',
    },
    modalCard: {
      backgroundColor: colors.surface, borderRadius: 20, padding: 24,
      width: '100%', maxWidth: 400,
    },
    modalHeader: {
      flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 12,
    },
    modalTitle: {
      fontSize: 18, fontFamily: 'PlusJakartaSans_700Bold', color: colors.text,
    },
    modalDesc: {
      fontSize: 14, fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim, lineHeight: 20, marginBottom: 16,
    },
    modalInput: {
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14,
      fontSize: 15, fontFamily: 'PlusJakartaSans_400Regular', color: colors.text,
      minHeight: 80, textAlignVertical: 'top',
      borderWidth: 1, borderColor: colors.border, marginBottom: 16,
    },
    modalBtnRow: {
      flexDirection: 'row', gap: 10,
    },
    modalCancelBtn: {
      flex: 1, paddingVertical: 14, borderRadius: 12,
      backgroundColor: colors.surfaceLight, alignItems: 'center',
      borderWidth: 1, borderColor: colors.border,
    },
    modalCancelText: {
      fontSize: 15, fontFamily: 'PlusJakartaSans_600SemiBold', color: colors.text,
    },
    modalSubmitBtn: {
      flex: 1, paddingVertical: 14, borderRadius: 12,
      backgroundColor: '#EF4444', alignItems: 'center',
    },
    modalSubmitText: {
      fontSize: 15, fontFamily: 'PlusJakartaSans_700Bold', color: '#FFF',
    },
  });
}
