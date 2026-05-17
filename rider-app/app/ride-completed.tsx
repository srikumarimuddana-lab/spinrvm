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
import * as Print from 'expo-print';
import * as Sharing from 'expo-sharing';

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
  const [submitPhase, setSubmitPhase] = useState<'idle' | 'rating' | 'confirming'>('idle');
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
  const fare = toNum((currentRide as any)?.grand_total || currentRide?.total_fare);
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
      ...(currentRide?.fare_breakdown || [])
        .filter((line: any) => line.amount != null)
        .map((line: any) => `${line.label.padEnd(15)}$${parseFloat(String(line.amount)).toFixed(2)}`),
      tipAmount > 0 ? `${'Tip'.padEnd(15)}$${tipAmount.toFixed(2)}` : null,
      `━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
      `TOTAL:         $${parseFloat(currentRide?.grand_total || String(total)).toFixed(2)} CAD`,
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

  const buildReceiptHtml = () => {
    const esc = (s: string) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const tipAmt = selectedTip || (customTip ? parseFloat(customTip) || 0 : 0);
    const rideDate = currentRide?.ride_completed_at
      ? new Date(currentRide.ride_completed_at).toLocaleString('en-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      : new Date().toLocaleString('en-CA', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    const rideCode = currentRide?.ride_code || currentRide?.id?.slice(0, 8).toUpperCase() || '—';
    const breakdown = (currentRide as any)?.fare_breakdown;
    const lines: Array<{ label: string; amount: number }> = Array.isArray(breakdown) && breakdown.length > 0
      ? breakdown.filter((l: any) => toNum(l.amount) > 0).map((l: any) => ({ label: l.label, amount: toNum(l.amount) }))
      : [
          { label: 'Ride fare', amount: toNum(currentRide?.base_fare) + toNum(currentRide?.distance_fare) + toNum(currentRide?.time_fare) },
          { label: 'Booking fee', amount: toNum((currentRide as any)?.booking_fee) },
        ].filter(l => l.amount > 0);
    const dAmt = toNum((currentRide as any)?.discount_amount);
    const dCode = (currentRide as any)?.promo_code as string | undefined;
    const pMethod = (currentRide as any)?.payment_method as string | undefined;
    const pLabel = pMethod === 'wallet' ? 'Spinr Wallet'
      : pMethod === 'company' || pMethod === 'corporate' ? ((currentRide as any)?.company_name || 'Company account')
      : currentRide?.card_last4 ? `Card •••• ${currentRide.card_last4}` : 'Payment card';
    const fareRowsHtml = lines.map(l =>
      `<div class="row"><span class="lbl">${esc(l.label)}</span><span class="amt">$${l.amount.toFixed(2)}</span></div>`
    ).join('');
    const promoHtml = dAmt > 0
      ? `<div class="row"><span class="lbl" style="color:#10B981;">🏷 ${esc(dCode ? `Promo (${dCode})` : 'Promo')}</span><span class="amt" style="color:#10B981;">−$${dAmt.toFixed(2)}</span></div>`
      : '';
    const tipHtml = tipAmt > 0
      ? `<div class="row"><span class="lbl">Tip</span><span class="amt">$${tipAmt.toFixed(2)}</span></div>`
      : '';
    const total = toNum((currentRide as any)?.grand_total || currentRide?.total_fare);

    return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
      *{box-sizing:border-box;margin:0;padding:0}
      body{font-family:-apple-system,Helvetica,Arial,sans-serif;background:#fff;color:#111;max-width:480px;margin:0 auto;padding:0}
      .header{text-align:center;padding:28px 24px 20px;border-bottom:1px solid #eee}
      .brand{font-size:32px;font-weight:900;color:#FF5A1F;letter-spacing:-1px}
      .receipt-title{font-size:11px;letter-spacing:2px;color:#999;margin-top:4px;text-transform:uppercase}
      .date{font-size:12px;color:#888;margin-top:8px}
      .ride-code{font-size:11px;color:#bbb;margin-top:2px}
      .section{padding:16px 24px;border-bottom:1px solid #f0f0f0}
      .route-row{display:flex;align-items:flex-start;gap:10px;margin-bottom:8px}
      .dot{width:10px;height:10px;border-radius:50%;margin-top:3px;flex-shrink:0}
      .row{display:flex;justify-content:space-between;align-items:center;padding:4px 0}
      .lbl{font-size:13px;color:#555}
      .amt{font-size:13px;font-weight:600;color:#111}
      .divider{border:none;border-top:1px dashed #e0e0e0;margin:8px 0}
      .total-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0 4px}
      .total-lbl{font-size:16px;font-weight:700}
      .total-amt{font-size:28px;font-weight:900;color:#FF5A1F}
      .badge{display:inline-block;background:#f5f5f5;padding:5px 12px;border-radius:20px;font-size:12px;color:#555;margin-top:8px}
      .paid{display:inline-block;background:#dcfce7;color:#15803d;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;margin-left:6px;vertical-align:middle}
      .stats{display:flex;gap:0;padding:14px 24px}
      .stat{flex:1;text-align:center}
      .stat-val{font-size:16px;font-weight:700}
      .stat-lbl{font-size:10px;color:#999;margin-top:2px}
      .stat-div{width:1px;background:#eee}
      .driver-section{padding:14px 24px}
      .driver-name{font-size:14px;font-weight:700}
      .driver-meta{font-size:12px;color:#666;margin-top:2px}
      .footer{text-align:center;padding:20px 24px;font-size:11px;color:#bbb;line-height:1.7}
    </style></head><body>
    <div class="header">
      <div class="brand">spinr</div>
      <div class="receipt-title">Ride Receipt</div>
      <div class="date">${esc(rideDate)}</div>
      <div class="ride-code">Ride ${esc(rideCode)}</div>
    </div>
    <div class="section">
      <div class="route-row"><div class="dot" style="background:#10B981"></div><div style="font-size:13px;color:#333">${esc(currentRide?.pickup_address || '—')}</div></div>
      <div class="route-row"><div class="dot" style="background:#EF4444"></div><div style="font-size:13px;color:#333">${esc(currentRide?.dropoff_address || '—')}</div></div>
    </div>
    <div class="section">
      ${fareRowsHtml}${promoHtml}${tipHtml}
      <hr class="divider">
      <div class="total-row"><span class="total-lbl">Total</span><span class="total-amt">$${total.toFixed(2)} CAD</span></div>
      <div><span class="badge">💳 ${esc(pLabel)}<span class="paid">PAID</span></span></div>
    </div>
    <div class="stats">
      <div class="stat"><div class="stat-val">${toNum(currentRide?.duration_minutes)} min</div><div class="stat-lbl">Duration</div></div>
      <div class="stat-div"></div>
      <div class="stat"><div class="stat-val">${toNum(currentRide?.distance_km).toFixed(1)} km</div><div class="stat-lbl">Distance</div></div>
      ${tipAmt > 0 ? `<div class="stat-div"></div><div class="stat"><div class="stat-val">$${tipAmt.toFixed(2)}</div><div class="stat-lbl">Tip</div></div>` : ''}
    </div>
    <div class="driver-section">
      <div class="driver-name">${esc(currentDriver?.name || 'Driver')}</div>
      <div class="driver-meta">${esc([currentDriver?.vehicle_color, currentDriver?.vehicle_make, currentDriver?.vehicle_model].filter(Boolean).join(' '))} · ${esc(currentDriver?.license_plate || '')}</div>
    </div>
    <div class="footer">Spinr Technologies Inc.<br>support@spinr.ca · spinr.ca<br>Saskatchewan, Canada</div>
    </body></html>`;
  };

  const handleShareInvoice = async () => {
    try {
      const html = buildReceiptHtml();
      const { uri } = await Print.printToFileAsync({ html, base64: false });
      const canShare = await Sharing.isAvailableAsync();
      if (canShare) {
        await Sharing.shareAsync(uri, { mimeType: 'application/pdf', dialogTitle: 'Share Spinr Receipt', UTI: 'com.adobe.pdf' });
      } else {
        await Share.share({ message: buildReceiptText(), title: 'Spinr Ride Receipt' });
      }
    } catch {
      setAlertState({ visible: true, title: 'Error', message: 'Could not share receipt.', variant: 'danger' });
    }
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
    setSubmitPhase('rating');
    try {
      const tipAmount = selectedTip || (customTip ? parseFloat(customTip) || 0 : 0);

      // 1. Rate the driver first — this is fire-and-forget because
      //    rating may fail if already rated (idempotent upstream).
      try {
        await rateRide(rideId as string, rating, comment || undefined, tipAmount > 0 ? tipAmount : undefined);
      } catch { /* rating may fail if already rated */ }

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
      setSubmitPhase('idle');
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

  const displayTip = selectedTip || (customTip ? parseFloat(customTip) || 0 : 0);
  const payMethod = (currentRide as any)?.payment_method as string | undefined;
  const payLabel = payMethod === 'wallet' ? 'Spinr Wallet'
    : payMethod === 'company' || payMethod === 'corporate'
      ? ((currentRide as any)?.company_name || 'Company account')
      : currentRide?.card_last4 ? `Card •••• ${currentRide.card_last4}` : 'Payment card';
  const payIcon = payMethod === 'wallet' ? 'wallet-outline'
    : payMethod === 'company' || payMethod === 'corporate' ? 'business-outline'
    : 'card-outline';

  const fareLines: Array<{ label: string; amount: number }> = (() => {
    const breakdown = (currentRide as any)?.fare_breakdown;
    if (Array.isArray(breakdown) && breakdown.length > 0) {
      return breakdown.filter((l: any) => toNum(l.amount) > 0).map((l: any) => ({ label: l.label, amount: toNum(l.amount) }));
    }
    const lines = [];
    const ridePortion = toNum(currentRide?.base_fare) + toNum(currentRide?.distance_fare) + toNum(currentRide?.time_fare);
    if (ridePortion > 0) lines.push({ label: 'Ride fare', amount: ridePortion });
    const booking = toNum((currentRide as any)?.booking_fee);
    if (booking > 0) lines.push({ label: 'Booking fee', amount: booking });
    return lines;
  })();
  const promoAmount = toNum((currentRide as any)?.discount_amount);
  const promoCode = (currentRide as any)?.promo_code as string | undefined;

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>

        {/* ── Header ────────────────────────────────────────────────── */}
        <View style={styles.successSection}>
          <View style={styles.checkCircle}>
            <Ionicons name="checkmark" size={34} color={colors.primary} />
          </View>
          <Text style={styles.title}>Ride Complete!</Text>
          <Text style={styles.subtitle} numberOfLines={2}>{currentRide?.dropoff_address || 'Destination'}</Text>
        </View>

        {/* ── Rate your ride ───────────────────────────────────────── */}
        <View style={styles.rateCard}>
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
                <Ionicons name={star <= rating ? 'star' : 'star-outline'} size={36} color={star <= rating ? '#FFB800' : colors.border} />
              </TouchableOpacity>
            ))}
          </View>
          <Text style={styles.ratingText}>
            {rating === 5 ? 'Excellent!' : rating === 4 ? 'Great' : rating === 3 ? 'Good' : rating === 2 ? 'Fair' : 'Poor'}
          </Text>
          <TextInput
            style={styles.commentInput}
            placeholder="Leave a comment (optional)"
            placeholderTextColor={colors.textDim}
            value={comment}
            onChangeText={setComment}
            multiline
            maxLength={200}
          />
        </View>

        {/* ── Tip ──────────────────────────────────────────────────── */}
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
                  onPress={() => { setSelectedTip(selectedTip === amt ? null : amt); setCustomTip(''); }}
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
                  placeholderTextColor={colors.textDim}
                  keyboardType="decimal-pad"
                  value={customTip}
                  onChangeText={(t) => { setCustomTip(t); setSelectedTip(null); }}
                  returnKeyType="done"
                />
              </View>
            </View>
          )}
        </View>

        {/* ── Fare & Payment card ───────────────────────────────────── */}
        <View style={styles.fareCard}>
          <View style={styles.driverRow}>
            <View style={styles.driverAvatar}>
              <Ionicons name="person" size={18} color={colors.textDim} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.driverName}>{currentDriver?.name || 'Your Driver'}</Text>
              <Text style={styles.driverMeta}>
                {[currentDriver?.vehicle_color, currentDriver?.vehicle_make, currentDriver?.license_plate].filter(Boolean).join(' · ')}
              </Text>
            </View>
            <View style={styles.statsBadge}>
              <Text style={styles.statsBadgeText}>{duration} min · {distance.toFixed(1)} km</Text>
            </View>
          </View>

          <View style={styles.divider} />

          {fareLines.map((line, i) => (
            <View key={i} style={styles.fareRow}>
              <Text style={styles.fareRowLabel}>{line.label}</Text>
              <Text style={styles.fareRowAmount}>${line.amount.toFixed(2)}</Text>
            </View>
          ))}

          {promoAmount > 0 && (
            <View style={styles.fareRow}>
              <View style={styles.promoLabel}>
                <Ionicons name="pricetag" size={12} color="#10B981" />
                <Text style={[styles.fareRowLabel, { color: '#10B981' }]}>{promoCode ? `Promo (${promoCode})` : 'Promo'}</Text>
              </View>
              <Text style={[styles.fareRowAmount, { color: '#10B981' }]}>−${promoAmount.toFixed(2)}</Text>
            </View>
          )}

          <View style={styles.divider} />

          <View style={styles.totalRow}>
            <Text style={styles.totalLabel}>Total</Text>
            <Text style={styles.totalAmount} allowFontScaling={false}>${fare.toFixed(2)}</Text>
          </View>

          <View style={styles.paymentBadge}>
            <Ionicons name={payIcon as any} size={14} color={colors.textDim} />
            <Text style={styles.paymentText}>{payLabel}</Text>
            {alreadyPaid && (
              <>
                <Ionicons name="checkmark-circle" size={14} color="#10B981" />
                <Text style={styles.paidText}>PAID</Text>
              </>
            )}
          </View>
        </View>

        {/* ── Secondary actions ─────────────────────────────────────── */}
        <View style={styles.secondaryRow}>
          <TouchableOpacity style={styles.secondaryBtn} onPress={handleShareInvoice} accessibilityRole="button" accessibilityLabel="Share receipt as PDF">
            <Ionicons name="document-attach-outline" size={18} color={colors.textDim} />
            <Text style={styles.secondaryBtnText}>Share PDF</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.secondaryBtn}
            accessibilityRole="button"
            accessibilityLabel="Copy receipt text"
            onPress={() => { Clipboard.setString(buildReceiptText()); setAlertState({ visible: true, title: 'Copied!', message: 'Receipt copied to clipboard.', variant: 'success' }); }}
          >
            <Ionicons name="copy-outline" size={18} color={colors.textDim} />
            <Text style={styles.secondaryBtnText}>Copy</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.secondaryBtn} onPress={() => router.push(`/chat-driver?rideId=${rideId}` as any)} accessibilityRole="button" accessibilityLabel="Message driver">
            <Ionicons name="chatbubble-ellipses-outline" size={18} color={colors.textDim} />
            <Text style={styles.secondaryBtnText}>Message</Text>
          </TouchableOpacity>
        </View>

        {/* ── Route Map (last) ──────────────────────────────────────── */}
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
            >
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
                        edgePadding: { top: 32, right: 32, bottom: 32, left: 32 },
                        animated: false,
                      });
                    }
                  }}
                />
              )}
              {routeCoords.length > 1 && (() => {
                const total = routeCoords.length;
                const SEGS = 15;
                const chunk = Math.max(1, Math.floor(total / SEGS));
                const segments: { coords: any[]; color: string }[] = [];
                for (let i = 0; i < total - 1; i += chunk) {
                  const end = Math.min(i + chunk + 1, total);
                  const t = i / Math.max(total - 1, 1);
                  segments.push({ coords: routeCoords.slice(i, end), color: `rgb(${Math.round(255 + (238 - 255) * t)},${Math.round(149 + (43 - 149) * t)},${Math.round(43 * t)})` });
                }
                return segments.map((seg, idx) => (
                  <Polyline key={`seg-${idx}`} coordinates={seg.coords} strokeWidth={4} strokeColor={seg.color} lineCap="round" lineJoin="round" />
                ));
              })()}
              <Marker coordinate={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                <View style={styles.mapPin}><Ionicons name="location" size={14} color="#FFF" /></View>
              </Marker>
              <Marker coordinate={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }} anchor={{ x: 0.5, y: 0.5 }}>
                <View style={[styles.mapPin, { backgroundColor: '#EF4444' }]}><Ionicons name="flag" size={14} color="#FFF" /></View>
              </Marker>
            </MapView>
            <View style={styles.mapLabel}><Text style={styles.mapLabelText}>YOUR ROUTE</Text></View>
          </View>
        )}

      </ScrollView>
      </KeyboardAvoidingView>

      {/* ── Bottom bar ────────────────────────────────────────────── */}
      <View style={styles.bottomBar}>
        {Platform.OS === 'android' && !alreadyPaid && payMethod === 'card' && (
          <TouchableOpacity
            style={[styles.submitBtn, styles.googlePayBtn]}
            onPress={handleGooglePay}
            disabled={isSubmitting || sheetLoading}
            activeOpacity={0.8}
            accessibilityRole="button"
            accessibilityLabel="Pay with Google Pay"
            accessibilityState={{ disabled: isSubmitting || sheetLoading }}
          >
            {sheetLoading
              ? <ActivityIndicator size="small" color="#FFF" />
              : <><Ionicons name="logo-google" size={18} color="#FFF" /><Text style={styles.submitBtnText}>Pay with Google Pay</Text></>}
          </TouchableOpacity>
        )}
        <TouchableOpacity
          style={styles.submitBtn}
          onPress={handleSubmit}
          disabled={isSubmitting || sheetLoading}
          activeOpacity={0.8}
          accessibilityRole="button"
          accessibilityLabel={alreadyPaid ? 'Rate and finish' : 'Pay and finish'}
          accessibilityState={{ disabled: isSubmitting || sheetLoading, busy: isSubmitting }}
        >
          {isSubmitting ? (
            <>
              <ActivityIndicator size="small" color="#FFF" />
              <Text style={styles.submitBtnText}>{submitPhase === 'confirming' ? 'Confirming payment…' : 'Submitting…'}</Text>
            </>
          ) : (
            <>
              <Text style={styles.submitBtnText}>
                {alreadyPaid ? 'Rate & Done' : `Pay $${(fare + displayTip).toFixed(2)} & Done`}
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
    content: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 24, gap: 12 },

    // Header
    successSection: { alignItems: 'center', paddingVertical: 8 },
    checkCircle: {
      width: 72, height: 72, borderRadius: 36,
      backgroundColor: `${colors.primary}18`,
      justifyContent: 'center', alignItems: 'center', marginBottom: 12,
    },
    title: { fontSize: 22, fontWeight: '800', color: colors.text, marginBottom: 4 },
    subtitle: { fontSize: 13, color: colors.textDim, textAlign: 'center', paddingHorizontal: 24 },

    // Map
    mapCard: { width: '100%', height: 210, borderRadius: 18, overflow: 'hidden', backgroundColor: colors.border },
    map: { flex: 1 },
    mapPin: {
      width: 28, height: 28, borderRadius: 14, backgroundColor: '#10B981',
      justifyContent: 'center', alignItems: 'center', borderWidth: 2, borderColor: '#FFF',
      elevation: 3, shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.2, shadowRadius: 2,
    },
    mapLabel: {
      position: 'absolute', bottom: 8, left: 8,
      backgroundColor: 'rgba(255,255,255,0.92)', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6,
    },
    mapLabelText: { fontSize: 10, fontWeight: '700', color: '#333', letterSpacing: 0.5 },

    // Fare card
    fareCard: { backgroundColor: colors.surfaceLight, borderRadius: 20, padding: 16 },
    driverRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 12 },
    driverAvatar: {
      width: 36, height: 36, borderRadius: 18,
      backgroundColor: colors.surface, justifyContent: 'center', alignItems: 'center',
      borderWidth: 1, borderColor: colors.border,
    },
    driverName: { fontSize: 14, fontWeight: '700', color: colors.text },
    driverMeta: { fontSize: 11, color: colors.textDim, marginTop: 1 },
    statsBadge: {
      backgroundColor: colors.surface, paddingHorizontal: 10, paddingVertical: 5,
      borderRadius: 10, borderWidth: 1, borderColor: colors.border,
    },
    statsBadgeText: { fontSize: 11, fontWeight: '600', color: colors.textDim },
    divider: { height: 1, backgroundColor: colors.border, marginVertical: 10 },
    fareRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 3 },
    fareRowLabel: { fontSize: 13, color: colors.textDim },
    fareRowAmount: { fontSize: 13, fontWeight: '600', color: colors.text },
    promoLabel: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    totalRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
    totalLabel: { fontSize: 15, fontWeight: '700', color: colors.text },
    totalAmount: { fontSize: 28, fontWeight: '800', color: colors.primary },
    paymentBadge: {
      flexDirection: 'row', alignItems: 'center', gap: 6,
      backgroundColor: colors.surface, paddingHorizontal: 12, paddingVertical: 8,
      borderRadius: 12, borderWidth: 1, borderColor: colors.border, alignSelf: 'flex-start',
    },
    paymentText: { fontSize: 12, fontWeight: '600', color: colors.textDim },
    paidText: { fontSize: 11, fontWeight: '700', color: '#10B981' },

    // Rating card
    rateCard: { backgroundColor: colors.surfaceLight, borderRadius: 20, padding: 16 },
    rateLabel: { fontSize: 15, fontWeight: '700', color: colors.text, textAlign: 'center', marginBottom: 12 },
    starsRow: { flexDirection: 'row', justifyContent: 'center', gap: 6, marginBottom: 6 },
    starBtn: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
    ratingText: { fontSize: 13, color: colors.textDim, textAlign: 'center', marginBottom: 12 },
    commentInput: {
      backgroundColor: colors.surface, borderRadius: 12, padding: 12, fontSize: 13, color: colors.text,
      minHeight: 56, textAlignVertical: 'top', borderWidth: 1, borderColor: colors.border,
    },

    // Tip card
    tipCard: { backgroundColor: colors.surfaceLight, borderRadius: 20, padding: 16 },
    tipTitle: { fontSize: 14, fontWeight: '600', color: colors.text, marginBottom: 12, textAlign: 'center' },
    tipRow: { flexDirection: 'row', gap: 8, justifyContent: 'center' },
    tipBtn: {
      paddingHorizontal: 18, paddingVertical: 11, borderRadius: 12,
      backgroundColor: colors.surface, borderWidth: 1.5, borderColor: colors.border,
      minHeight: 44, justifyContent: 'center', alignItems: 'center',
    },
    tipBtnActive: { backgroundColor: `${colors.primary}15`, borderColor: colors.primary },
    tipBtnText: { fontSize: 15, fontWeight: '700', color: colors.text },
    tipBtnTextActive: { color: colors.primary },
    tipCustom: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surface, borderRadius: 12, borderWidth: 1.5, borderColor: colors.border,
      paddingHorizontal: 10, minWidth: 76,
    },
    tipCustomActive: { borderColor: colors.primary },
    tipDollar: { fontSize: 15, fontWeight: '600', color: colors.textDim },
    tipCustomInput: { fontSize: 15, fontWeight: '600', color: colors.text, paddingVertical: 11, paddingHorizontal: 4, minWidth: 44 },
    tipDone: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      paddingVertical: 12, backgroundColor: '#F0FFF4', borderRadius: 12,
    },
    tipDoneText: { fontSize: 14, fontWeight: '600', color: '#059669' },

    // Secondary actions
    secondaryRow: { flexDirection: 'row', gap: 8 },
    secondaryBtn: {
      flex: 1, alignItems: 'center', gap: 5, paddingVertical: 12,
      backgroundColor: colors.surfaceLight, borderRadius: 14, borderWidth: 1, borderColor: colors.border,
    },
    secondaryBtnText: { fontSize: 11, fontWeight: '600', color: colors.textDim },

    // Bottom bar
    bottomBar: {
      paddingHorizontal: 16, paddingVertical: 12,
      borderTopWidth: 1, borderTopColor: colors.border,
      backgroundColor: colors.surface,
    },
    submitBtn: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      backgroundColor: colors.primary, paddingVertical: 16, borderRadius: 28,
    },
    googlePayBtn: { backgroundColor: '#3C4043', marginBottom: 8 },
    submitBtnText: { fontSize: 17, fontWeight: '700', color: '#FFF' },
  });
}
