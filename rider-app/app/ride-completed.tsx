import React, { useEffect, useRef, useState, useMemo } from 'react';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, TextInput,
  Platform, ActivityIndicator, BackHandler, Share, KeyboardAvoidingView, Clipboard,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { useRideStore } from '../store/rideStore';
import CustomAlert from '@shared/components/CustomAlert';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import Analytics from '@shared/analytics';
import { useStripe } from '@stripe/stripe-react-native';
import { attemptRidePayment, PaymentAlertButton } from '../utils/attemptRidePayment';
import { useSpinrPaymentSheet } from '../hooks/useSpinrPaymentSheet';

// PR #664 stringified Decimal money fields in API responses (e.g. total_fare,
// base_fare, tip_amount). The receipt UI needs them as numbers for arithmetic
// and as 2-dp display strings. These two helpers narrow `MoneyString | number
// | undefined` cleanly without spreading parseFloat noise across the file.
const toNum = (v: string | number | null | undefined): number =>
  typeof v === 'number' ? v : v ? parseFloat(v) || 0 : 0;
const fmt = (v: string | number | null | undefined): string =>
  toNum(v).toFixed(2);

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

function RideCompletedScreenContent() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, rateRide, clearRide } = useRideStore();

  // P0-5: confirmPayment is called when the backend returns
  // requires_action for 3DS / SCA. StripeProvider is wired at the app
  // root in app/_layout.tsx so useStripe() resolves here.
  const { confirmPayment } = useStripe();
  const { presentSheet, isLoading: sheetLoading } = useSpinrPaymentSheet();

  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState('');
  const [selectedTip, setSelectedTip] = useState<number | null>(null);
  const [customTip, setCustomTip] = useState('');
  const [tipSent, setTipSent] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [alreadyPaid, setAlreadyPaid] = useState(false);
  const [paymentProcessed, setPaymentProcessed] = useState(false);

  const [routeCoords, setRouteCoords] = useState<any[]>([]);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });
  const mapRef = React.useRef<MapView>(null);

  const tipOptions = [2, 5, 10];
  const fare = toNum(currentRide?.total_fare);
  const duration = currentRide?.duration_minutes || 0;
  const distance = currentRide?.distance_km || 0;

  useEffect(() => {
    if (rideId) fetchRide(rideId);
  }, [rideId]);

  // Check if ride was already paid (e.g. coming back to this screen)
  useEffect(() => {
    if (currentRide?.payment_status === 'paid') {
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
  }, [currentRide?.payment_status]);

  // Block back navigation — must complete rating & payment
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
    return () => sub.remove();
  }, []);

  const buildReceiptText = () => {
    const tipAmount = selectedTip || (customTip ? parseFloat(customTip) || 0 : 0);
    const total = fare + tipAmount;
    const rideDate = currentRide?.ride_completed_at
      ? new Date(currentRide.ride_completed_at).toLocaleString('en-CA', {
          weekday: 'short', year: 'numeric', month: 'short', day: 'numeric',
          hour: '2-digit', minute: '2-digit',
        })
      : new Date().toLocaleString('en-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

    return [
      `╔══════════════════════════════╗`,
      `║      SPINR RIDE RECEIPT      ║`,
      `║   Saskatoon · Regina, SK     ║`,
      `╚══════════════════════════════╝`,
      ``,
      `📅 ${rideDate}`,
      // Prefer the human-readable SPR-XXXXXX ride_code (migration 40);
      // fall back to a truncated UUID for rides that predate the code.
      `🆔 Ride: ${currentRide?.ride_code || currentRide?.id?.slice(0, 8).toUpperCase() || '—'}`,
      ``,
      `▸ FROM  ${currentRide?.pickup_address || '—'}`,
      `▸ TO    ${currentRide?.dropoff_address || '—'}`,
      ``,
      `━━━━━━ FARE BREAKDOWN ━━━━━━`,
      `Base fare:     $${fmt(currentRide?.base_fare)}`,
      `Distance fare: $${fmt(currentRide?.distance_fare)}  (${distance.toFixed(1)} km)`,
      `Time fare:     $${fmt(currentRide?.time_fare)}  (${duration} min)`,
      `Booking fee:   $${fmt(currentRide?.booking_fee)}`,
      tipAmount > 0 ? `Tip:           $${tipAmount.toFixed(2)}` : null,
      `━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
      `TOTAL:         $${total.toFixed(2)} CAD`,
      ``,
      `💳 Card •••• ${currentRide?.card_last4 || '4242'}  ✅ PAID`,
      ``,
      `👤 Driver: ${currentDriver?.name || 'Driver'}`,
      `🚙 ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || ''} ${currentDriver?.vehicle_model || ''}`,
      `🪪 Plate: ${currentDriver?.license_plate || '—'}`,
      ``,
      `━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
      `Spinr Technologies Inc.`,
      `support@spinr.ca · spinr.ca`,
      `Mon–Fri 9am–6pm CST`,
    ].filter(Boolean).join('\n');
  };

  const handleShareInvoice = async () => {
    try {
      await Share.share({ message: buildReceiptText(), title: 'Spinr Ride Receipt' });
    } catch {}
  };

  // Payment is processed when rider taps "Done" — includes tip amount

  const handleSendTip = async (amount: number) => {
    if (amount <= 0 || tipSent) return;
    try {
      await api.post(`/rides/${rideId}/tip`, { amount });
      setTipSent(true);
      setSelectedTip(amount);
    } catch {
      setAlertState({ visible: true, title: 'Error', message: 'Could not send tip.', variant: 'danger' });
    }
  };

  /**
   * Map a PaymentAttemptResult's alert (pure data) to the local
   * alertState shape (with onPress handlers). Split from the attempt
   * itself so the attempt stays pure-data and unit-testable.
   */
  const showPaymentAlert = (alert: NonNullable<Awaited<ReturnType<typeof attemptRidePayment>>['alert']>) => {
    const buttons = (alert.buttons || []).map((b: PaymentAlertButton) => ({
      text: b.text,
      style: (b.kind === 'cancel' ? 'cancel' : 'default') as 'default' | 'cancel',
      onPress: b.kind === 'change_card' ? () => router.push('/manage-cards' as any) : undefined,
    }));
    setAlertState({
      visible: true,
      title: alert.title,
      message: alert.message,
      variant: alert.variant,
      buttons: buttons.length ? buttons : [{ text: 'OK' }],
    });
  };

  const handleSubmit = async () => {
    if (isSubmitting) return; // prevent double tap
    setIsSubmitting(true);
    try {
      const tipAmount = selectedTip || (customTip ? parseFloat(customTip) || 0 : 0);

      // 1. Rate the driver first — this is fire-and-forget because
      //    rating may fail if already rated (idempotent upstream).
      try {
        await rateRide(rideId as string, rating, comment || undefined, tipAmount > 0 ? tipAmount : undefined);
      } catch { /* rating may fail if already rated */ }

      // 2. Process payment. If the rider has already paid (came back to
      //    this screen), skip the attempt entirely.
      let paymentOk = alreadyPaid;
      let chargedAmount: number | undefined;
      if (!alreadyPaid) {
        const result = await attemptRidePayment({
          api,
          stripe: { confirmPayment },
          rideId: rideId as string,
          tipAmount,
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
        const { onRideRated } = require('@shared/utils/appRating');
        await onRideRated(rating);
      } catch { /* non-critical */ }

      clearRide();
      router.replace('/(tabs)');
    } catch {
      setAlertState({ visible: true, title: 'Error', message: 'Failed to submit. Please try again.', variant: 'danger' });
    } finally {
      setIsSubmitting(false);
    }
  };

  // Google Pay path — presents the Stripe PaymentSheet modal so riders can
  // pay without re-entering a card. Only shown for card payment rides on
  // Android (Apple Pay uses the same sheet on iOS via handleSubmit in future).
  const handleGooglePay = async () => {
    if (isSubmitting || sheetLoading || alreadyPaid) return;
    const tipAmount = selectedTip || (customTip ? parseFloat(customTip) || 0 : 0);
    const result = await presentSheet({
      rideId: rideId as string,
      amount: toNum(currentRide?.total_fare),
      tipAmount,
    });
    if (!result.ok) {
      if (result.errorMessage !== 'Payment cancelled.') {
        setAlertState({ visible: true, title: 'Payment failed', message: result.errorMessage || 'Please try again.', variant: 'danger' });
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
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>

        {/* Success Header */}
        <View style={styles.successSection}>
          <View style={styles.checkCircle}>
            <Ionicons name="checkmark" size={36} color={colors.primary} />
          </View>
          <Text style={styles.title}>Ride Complete!</Text>
          <Text style={styles.subtitle} numberOfLines={1}>
            {currentRide?.dropoff_address || 'Destination'}
          </Text>
        </View>

        {/* Post-Trip Actions */}
        <View style={styles.postTripActions}>
          <View style={styles.receiptRow}>
            <TouchableOpacity
              style={[styles.invoiceBtn, { flex: 1 }]}
              onPress={handleShareInvoice}
              accessibilityRole="button"
              accessibilityLabel="Share receipt"
              accessibilityHint="Shares your trip receipt"
            >
              <Ionicons name="receipt-outline" size={18} color={colors.primary} />
              <Text style={styles.invoiceBtnText}>Share Receipt</Text>
              <Ionicons name="share-outline" size={16} color={colors.textDim} />
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.copyReceiptBtn}
              onPress={() => {
                Clipboard.setString(buildReceiptText());
                setAlertState({ visible: true, title: 'Copied!', message: 'Receipt copied to clipboard.', variant: 'success' });
              }}
              accessibilityRole="button"
              accessibilityLabel="Copy receipt to clipboard"
              hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
            >
              <Ionicons name="copy-outline" size={18} color={colors.textDim} />
            </TouchableOpacity>
          </View>
          <TouchableOpacity
            style={styles.chatBtn}
            onPress={() => router.push(`/chat-driver?rideId=${rideId}` as any)}
            accessibilityRole="button"
            accessibilityLabel="Message driver"
            accessibilityHint="Opens a chat with your driver"
          >
            <Ionicons name="chatbubble-ellipses-outline" size={18} color="#3B82F6" />
            <Text style={styles.chatBtnText}>Message Driver</Text>
            <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.viewReceiptBtn}
            onPress={() => router.push(`/receipt/${rideId}` as any)}
            accessibilityRole="button"
            accessibilityLabel="View receipt"
            accessibilityHint="Opens the detailed fare breakdown for this ride"
          >
            <Ionicons name="document-text-outline" size={18} color={colors.text} />
            <Text style={styles.viewReceiptBtnText}>View Receipt</Text>
            <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
          </TouchableOpacity>
        </View>

        {/* Route Map */}
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
              userInterfaceStyle={isDark ? "dark" : "light"}
              initialRegion={{
                latitude: (Number(currentRide.pickup_lat) + Number(currentRide.dropoff_lat)) / 2,
                longitude: (Number(currentRide.pickup_lng) + Number(currentRide.dropoff_lng)) / 2,
                latitudeDelta: Math.abs(Number(currentRide.pickup_lat) - Number(currentRide.dropoff_lat)) * 2.5 + 0.01,
                longitudeDelta: Math.abs(Number(currentRide.pickup_lng) - Number(currentRide.dropoff_lng)) * 2.5 + 0.01,
              }}
            >
              {/* Fetch route */}
              {GOOGLE_MAPS_API_KEY && (
                <MapViewDirections
                  origin={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
                  destination={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
                  apikey={GOOGLE_MAPS_API_KEY}
                  strokeWidth={0}
                  strokeColor="transparent"
                  onReady={(result: any) => {
                    setRouteCoords(result.coordinates);
                    if (mapRef.current && result.coordinates?.length > 1) {
                      mapRef.current.fitToCoordinates(result.coordinates, {
                        edgePadding: { top: 30, right: 30, bottom: 30, left: 30 },
                        animated: false,
                      });
                    }
                  }}
                />
              )}

              {/* Orange → Red gradient route */}
              {routeCoords.length > 1 && (() => {
                const total = routeCoords.length;
                const SEGS = 15;
                const chunk = Math.max(1, Math.floor(total / SEGS));
                const segments: { coords: any[]; color: string }[] = [];
                for (let i = 0; i < total - 1; i += chunk) {
                  const end = Math.min(i + chunk + 1, total);
                  const t = i / Math.max(total - 1, 1);
                  const r = Math.round(255 + (238 - 255) * t);
                  const g = Math.round(149 + (43 - 149) * t);
                  const b = Math.round(0 + (43 - 0) * t);
                  segments.push({ coords: routeCoords.slice(i, end), color: `rgb(${r},${g},${b})` });
                }
                return segments.map((seg, idx) => (
                  <Polyline
                    key={`seg-${idx}`}
                    coordinates={seg.coords}
                    strokeWidth={4}
                    strokeColor={seg.color}
                    lineCap="round"
                    lineJoin="round"
                  />
                ));
              })()}

              {/* Pickup marker */}
              <Marker
                coordinate={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
                anchor={{ x: 0.5, y: 0.5 }}
              >
                <View style={styles.mapPin}>
                  <Ionicons name="location" size={14} color="#FFF" />
                </View>
              </Marker>

              {/* Dropoff marker */}
              <Marker
                coordinate={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
                anchor={{ x: 0.5, y: 0.5 }}
              >
                <View style={[styles.mapPin, { backgroundColor: '#EF4444' }]}>
                  <Ionicons name="flag" size={14} color="#FFF" />
                </View>
              </Marker>
            </MapView>

            {/* Route label overlay */}
            <View style={styles.mapLabel}>
              <Text style={styles.mapLabelText}>YOUR ROUTE</Text>
            </View>
          </View>
        )}

        {/* Fare Card */}
        <View style={styles.fareCard}>
          <Text style={styles.fareAmount} allowFontScaling={false}>${fare.toFixed(2)}</Text>
          <View style={styles.paymentBadge}>
            <Ionicons name="card" size={14} color={colors.textDim} />
            <Text style={styles.paymentText}>
              Card ending •••• {currentRide?.card_last4 || '4242'}
            </Text>
            {alreadyPaid && (
              <>
                <Ionicons name="checkmark-circle" size={14} color="#10B981" />
                <Text style={{ fontSize: 11, fontWeight: '700', color: '#10B981' }} allowFontScaling={false}>PAID</Text>
              </>
            )}
          </View>

          {/* Stats */}
          <View style={styles.statsRow}>
            <View style={styles.stat}>
              <Ionicons name="time-outline" size={18} color={colors.textDim} />
              <Text style={styles.statVal} allowFontScaling={false}>{duration} min</Text>
              <Text style={styles.statLbl}>Duration</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.stat}>
              <Ionicons name="speedometer-outline" size={18} color={colors.textDim} />
              <Text style={styles.statVal} allowFontScaling={false}>{distance.toFixed(1)} km</Text>
              <Text style={styles.statLbl}>Distance</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.stat}>
              <Ionicons name="cash-outline" size={18} color={colors.textDim} />
              <Text style={styles.statVal} allowFontScaling={false}>${fare.toFixed(2)}</Text>
              <Text style={styles.statLbl}>Total</Text>
            </View>
          </View>
        </View>

        {/* Rate Driver */}
        <View style={styles.rateCard}>
          <View style={styles.driverRow}>
            <View style={styles.driverAvatar}>
              <Ionicons name="person" size={24} color={colors.textDim} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.driverName}>{currentDriver?.name || 'Your Driver'}</Text>
              <Text style={styles.driverMeta}>
                {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} · {currentDriver?.license_plate}
              </Text>
            </View>
          </View>

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
                  size={36}
                  color={star <= rating ? '#FFB800' : '#DDD'}
                />
              </TouchableOpacity>
            ))}
          </View>
          <Text style={styles.ratingText}>
            {rating === 5 ? 'Excellent!' : rating === 4 ? 'Great' : rating === 3 ? 'Good' : rating === 2 ? 'Fair' : 'Poor'}
          </Text>

          {/* Comment */}
          <TextInput
            style={styles.commentInput}
            placeholder="Leave a comment (optional)"
            placeholderTextColor="#BBB"
            value={comment}
            onChangeText={setComment}
            multiline
            maxLength={200}
          />
        </View>

        {/* Tip Section */}
        <View style={styles.tipCard}>
          <Text style={styles.tipTitle}>Add a tip for {currentDriver?.name?.split(' ')[0] || 'your driver'}</Text>
          {tipSent ? (
            <View style={styles.tipDone}>
              <Ionicons name="heart" size={20} color="#10B981" />
              <Text style={styles.tipDoneText}>${selectedTip?.toFixed(2)} tip sent!</Text>
            </View>
          ) : (
            <View style={styles.tipRow}>
              {tipOptions.map((amt) => (
                <TouchableOpacity
                  key={amt}
                  style={[styles.tipBtn, selectedTip === amt && styles.tipBtnActive]}
                  onPress={() => { setSelectedTip(amt); setCustomTip(''); }}
                  accessibilityRole="radio"
                  accessibilityLabel={`Tip $${amt}`}
                  accessibilityState={{ checked: selectedTip === amt }}
                >
                  <Text style={[styles.tipBtnText, selectedTip === amt && styles.tipBtnTextActive]}>${amt}</Text>
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
          )}
        </View>

      </ScrollView>
      </KeyboardAvoidingView>

      {/* Submit Button */}
      <View style={styles.bottomBar}>
        {/* Google Pay button — Android only, card payments, not yet paid */}
        {Platform.OS === 'android' && !alreadyPaid && currentRide?.payment_method === 'card' && (
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
          onPress={handleSubmit}
          disabled={isSubmitting || sheetLoading}
          activeOpacity={0.8}
          accessibilityRole="button"
          accessibilityLabel={alreadyPaid ? 'Rate and finish' : `Pay and finish`}
          accessibilityState={{ disabled: isSubmitting || sheetLoading, busy: isSubmitting }}
        >
          {isSubmitting ? (
            <ActivityIndicator size="small" color="#FFF" />
          ) : (
            <>
              <Text style={styles.submitBtnText}>
                {alreadyPaid
                  ? 'Rate & Done'
                  : `Pay $${(fare + (selectedTip || (customTip ? parseFloat(customTip) || 0 : 0))).toFixed(2)} & Done`
                }
              </Text>
              <Ionicons name={alreadyPaid ? 'checkmark' : 'card'} size={18} color="#FFF" />
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

export default function RideCompletedScreen() {
  return (
    <ErrorBoundary>
      <RideCompletedScreenContent />
    </ErrorBoundary>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    content: { paddingHorizontal: 20, paddingTop: 20, paddingBottom: 20 },

    // Success
    successSection: { alignItems: 'center', marginBottom: 20 },
    checkCircle: {
      width: 80, height: 80, borderRadius: 40, backgroundColor: '#FEF2F2',
      justifyContent: 'center', alignItems: 'center', marginBottom: 14,
    },
    title: { fontSize: 24, fontWeight: '800', color: colors.text, marginBottom: 4 },
    subtitle: { fontSize: 14, color: colors.textDim },

    // Invoice
    receiptRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
    invoiceBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 8,
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14,
      borderWidth: 1, borderColor: '#ECECEC',
    },
    invoiceBtnText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.text },
    copyReceiptBtn: {
      backgroundColor: colors.surfaceLight, borderRadius: 14, paddingHorizontal: 16,
      alignItems: 'center', justifyContent: 'center',
      borderWidth: 1, borderColor: '#ECECEC',
    },
    postTripActions: { width: '100%', gap: 8, marginBottom: 8 },
    chatBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 8, width: '100%',
      backgroundColor: '#EFF6FF', borderRadius: 14, padding: 14,
      borderWidth: 1, borderColor: '#DBEAFE',
    },
    chatBtnText: { flex: 1, fontSize: 14, fontWeight: '600', color: '#3B82F6' },
    viewReceiptBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 8, width: '100%',
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14,
      borderWidth: 1, borderColor: colors.border,
    },
    viewReceiptBtnText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.text },

    // Route Map
    mapCard: {
      width: '100%', height: 220, borderRadius: 18, overflow: 'hidden',
      marginBottom: 16, backgroundColor: colors.border,
    },
    map: { flex: 1 },
    mapPin: {
      width: 28, height: 28, borderRadius: 14,
      backgroundColor: '#10B981', justifyContent: 'center', alignItems: 'center',
      borderWidth: 2, borderColor: '#FFF',
      elevation: 3, shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.2, shadowRadius: 2,
    },
    mapLabel: {
      position: 'absolute', bottom: 8, left: 8,
      backgroundColor: 'rgba(255,255,255,0.9)', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6,
    },
    mapLabelText: { fontSize: 10, fontWeight: '700', color: colors.text, letterSpacing: 0.5 },

    // Fare Card
    fareCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 20, padding: 20, alignItems: 'center', marginBottom: 16,
    },
    fareAmount: { fontSize: 42, fontWeight: '800', color: colors.primary, marginBottom: 8 },
    paymentBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 6,
      backgroundColor: colors.surface, paddingHorizontal: 14, paddingVertical: 6, borderRadius: 20,
      marginBottom: 16,
    },
    paymentText: { fontSize: 12, fontWeight: '600', color: colors.textDim },
    statsRow: { flexDirection: 'row', width: '100%' },
    stat: { flex: 1, alignItems: 'center' },
    statVal: { fontSize: 16, fontWeight: '700', color: colors.text, marginTop: 4 },
    statLbl: { fontSize: 10, color: colors.textDim, marginTop: 2 },
    statDivider: { width: 1, backgroundColor: '#E8E8E8' },

    // Rate Card
    rateCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 20, padding: 20, marginBottom: 16,
    },
    driverRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 16 },
    driverAvatar: {
      width: 48, height: 48, borderRadius: 24, backgroundColor: '#E8E8E8',
      justifyContent: 'center', alignItems: 'center', marginRight: 12,
    },
    driverName: { fontSize: 16, fontWeight: '700', color: colors.text },
    driverMeta: { fontSize: 12, color: colors.textDim, marginTop: 2 },
    rateLabel: { fontSize: 15, fontWeight: '600', color: colors.text, textAlign: 'center', marginBottom: 12 },
    starsRow: { flexDirection: 'row', justifyContent: 'center', gap: 8, marginBottom: 6 },
    starBtn: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
    ratingText: { fontSize: 13, color: colors.textDim, textAlign: 'center', marginBottom: 14 },
    commentInput: {
      backgroundColor: colors.surface, borderRadius: 14, padding: 14, fontSize: 14, color: colors.text,
      minHeight: 60, textAlignVertical: 'top', borderWidth: 1, borderColor: '#ECECEC',
    },

    // Tip
    tipCard: {
      backgroundColor: colors.surfaceLight, borderRadius: 20, padding: 20,
    },
    tipTitle: { fontSize: 15, fontWeight: '600', color: colors.text, marginBottom: 14, textAlign: 'center' },
    tipRow: { flexDirection: 'row', gap: 10, justifyContent: 'center' },
    tipBtn: {
      paddingHorizontal: 20, paddingVertical: 12, borderRadius: 14,
      backgroundColor: colors.surface, borderWidth: 1.5, borderColor: colors.border,
      minHeight: 44, justifyContent: 'center', alignItems: 'center',
    },
    tipBtnActive: { backgroundColor: `${colors.primary}15`, borderColor: colors.primary },
    tipBtnText: { fontSize: 16, fontWeight: '700', color: colors.text },
    tipBtnTextActive: { color: colors.primary },
    tipCustom: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surface, borderRadius: 14, borderWidth: 1.5, borderColor: colors.border,
      paddingHorizontal: 12, minWidth: 80,
    },
    tipCustomActive: { borderColor: colors.primary },
    tipDollar: { fontSize: 16, fontWeight: '600', color: colors.textDim },
    tipCustomInput: { fontSize: 16, fontWeight: '600', color: colors.text, paddingVertical: 12, paddingHorizontal: 4, minWidth: 44 },
    tipDone: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      paddingVertical: 12, backgroundColor: '#F0FFF4', borderRadius: 12,
    },
    tipDoneText: { fontSize: 15, fontWeight: '600', color: '#059669' },

    // Bottom
    bottomBar: {
      paddingHorizontal: 20, paddingVertical: 14,
      borderTopWidth: 1, borderTopColor: colors.border,
    },
    submitBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      backgroundColor: colors.primary, paddingVertical: 16, borderRadius: 28,
    },
    googlePayBtn: {
      backgroundColor: '#3C4043', marginBottom: 10,
    },
    submitBtnText: { fontSize: 17, fontWeight: '700', color: '#FFF' },
  });
}
