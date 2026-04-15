import React, { useEffect, useState, useMemo } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, TextInput,
  Platform, ActivityIndicator, BackHandler, Share, KeyboardAvoidingView,
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

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

export default function RideCompletedScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, rateRide, clearRide } = useRideStore();

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
  const fare = currentRide?.total_fare || 0;
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

  // Block back navigation — must complete rating & payment
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
    return () => sub.remove();
  }, []);

  const handleShareInvoice = async () => {
    const tipAmount = selectedTip || (customTip ? parseFloat(customTip) || 0 : 0);
    const total = fare + tipAmount;
    const invoice = [
      `SPINR RIDE RECEIPT`,
      `──────────────────`,
      `Date: ${currentRide?.ride_completed_at ? new Date(currentRide.ride_completed_at).toLocaleString() : new Date().toLocaleString()}`,
      ``,
      `Pickup: ${currentRide?.pickup_address || '—'}`,
      `Dropoff: ${currentRide?.dropoff_address || '—'}`,
      `Distance: ${distance.toFixed(1)} km`,
      `Duration: ${duration} min`,
      ``,
      `Base fare:     $${(currentRide?.base_fare || 0).toFixed(2)}`,
      `Distance:      $${(currentRide?.distance_fare || 0).toFixed(2)}`,
      `Time:          $${(currentRide?.time_fare || 0).toFixed(2)}`,
      `Booking fee:   $${(currentRide?.booking_fee || 0).toFixed(2)}`,
      tipAmount > 0 ? `Tip:           $${tipAmount.toFixed(2)}` : '',
      `──────────────────`,
      `TOTAL:         $${total.toFixed(2)} CAD`,
      ``,
      `Payment: Card •••• ${currentRide?.card_last4 || '4242'}`,
      ``,
      `Driver: ${currentDriver?.name || 'Driver'}`,
      `Vehicle: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || ''} ${currentDriver?.vehicle_model || ''}`,
      `Plate: ${currentDriver?.license_plate || '—'}`,
      ``,
      `Spinr Technologies Inc. · Saskatoon, SK`,
      `support@spinr.ca`,
    ].filter(Boolean).join('\n');

    try {
      await Share.share({ message: invoice, title: 'Spinr Ride Receipt' });
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

  const handleSubmit = async () => {
    if (isSubmitting) return; // prevent double tap
    setIsSubmitting(true);
    try {
      const tipAmount = selectedTip || (customTip ? parseFloat(customTip) : 0);

      // 1. Rate the driver (always, even if already paid)
      try {
        await rateRide(rideId as string, rating, comment || undefined, tipAmount > 0 ? tipAmount : undefined);
      } catch { /* rating may fail if already rated */ }

      // 2. Process payment only if not already paid
      if (!alreadyPaid) {
        try {
          await api.post(`/rides/${rideId}/process-payment`, { tip_amount: tipAmount });
          const total = (currentRide?.total_fare || 0) + (tipAmount || 0);
          Analytics.paymentCompleted({ method: 'default', amount: total });
        } catch { /* backend handles idempotency */ }
      }

      Analytics.rideCompleted({
        fare: currentRide?.total_fare || 0,
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
          <TouchableOpacity style={styles.invoiceBtn} onPress={handleShareInvoice}>
            <Ionicons name="receipt-outline" size={18} color={colors.primary} />
            <Text style={styles.invoiceBtnText}>Share Invoice</Text>
            <Ionicons name="share-outline" size={16} color={colors.textDim} />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.chatBtn}
            onPress={() => router.push(`/chat-driver?rideId=${rideId}` as any)}
          >
            <Ionicons name="chatbubble-ellipses-outline" size={18} color="#3B82F6" />
            <Text style={styles.chatBtnText}>Message Driver</Text>
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
                <View style={[styles.mapPin, { backgroundColor: colors.primary }]}>
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
          <Text style={styles.fareAmount}>${fare.toFixed(2)}</Text>
          <View style={styles.paymentBadge}>
            <Ionicons name="card" size={14} color={colors.textDim} />
            <Text style={styles.paymentText}>
              Card ending •••• {currentRide?.card_last4 || '4242'}
            </Text>
            {alreadyPaid && (
              <>
                <Ionicons name="checkmark-circle" size={14} color="#10B981" />
                <Text style={{ fontSize: 11, fontWeight: '700', color: '#10B981' }}>PAID</Text>
              </>
            )}
          </View>

          {/* Stats */}
          <View style={styles.statsRow}>
            <View style={styles.stat}>
              <Ionicons name="time-outline" size={18} color={colors.textDim} />
              <Text style={styles.statVal}>{duration} min</Text>
              <Text style={styles.statLbl}>Duration</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.stat}>
              <Ionicons name="speedometer-outline" size={18} color={colors.textDim} />
              <Text style={styles.statVal}>{distance.toFixed(1)} km</Text>
              <Text style={styles.statLbl}>Distance</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.stat}>
              <Ionicons name="cash-outline" size={18} color={colors.textDim} />
              <Text style={styles.statVal}>${fare.toFixed(2)}</Text>
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
              <TouchableOpacity key={star} onPress={() => setRating(star)} style={styles.starBtn}>
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
                />
              </View>
            </View>
          )}
        </View>

      </ScrollView>
      </KeyboardAvoidingView>

      {/* Submit Button */}
      <View style={styles.bottomBar}>
        <TouchableOpacity
          style={styles.submitBtn}
          onPress={handleSubmit}
          disabled={isSubmitting}
          activeOpacity={0.8}
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
    invoiceBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 8, width: '100%',
      backgroundColor: colors.surfaceLight, borderRadius: 14, padding: 14, marginBottom: 16,
      borderWidth: 1, borderColor: '#ECECEC',
    },
    invoiceBtnText: { flex: 1, fontSize: 14, fontWeight: '600', color: colors.text },
    postTripActions: { width: '100%', gap: 8, marginBottom: 8 },
    chatBtn: {
      flexDirection: 'row', alignItems: 'center', gap: 8, width: '100%',
      backgroundColor: '#EFF6FF', borderRadius: 14, padding: 14,
      borderWidth: 1, borderColor: '#DBEAFE',
    },
    chatBtnText: { flex: 1, fontSize: 14, fontWeight: '600', color: '#3B82F6' },

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
    starBtn: { padding: 4 },
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
    submitBtnText: { fontSize: 17, fontWeight: '700', color: '#FFF' },
  });
}
