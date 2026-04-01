import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, TextInput,
  Alert, Platform, ActivityIndicator, BackHandler,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';
import api from '@shared/api/client';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

const COLORS = SpinrConfig.theme.colors;

export default function RideCompletedScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, rateRide, clearRide } = useRideStore();

  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState('');
  const [selectedTip, setSelectedTip] = useState<number | null>(null);
  const [customTip, setCustomTip] = useState('');
  const [tipSent, setTipSent] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [alreadyPaid, setAlreadyPaid] = useState(false);
  const [paymentProcessed, setPaymentProcessed] = useState(false);

  const [routeCoords, setRouteCoords] = useState<any[]>([]);
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

  // Payment is processed when rider taps "Done" — includes tip amount

  const handleSendTip = async (amount: number) => {
    if (amount <= 0 || tipSent) return;
    try {
      await api.post(`/rides/${rideId}/tip`, { amount });
      setTipSent(true);
      setSelectedTip(amount);
    } catch {
      Alert.alert('Error', 'Could not send tip.');
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
        } catch { /* backend handles idempotency */ }
      }

      clearRide();
      router.replace('/(tabs)');
    } catch {
      Alert.alert('Error', 'Failed to submit. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>

        {/* Success Header */}
        <View style={styles.successSection}>
          <View style={styles.checkCircle}>
            <Ionicons name="checkmark" size={36} color={COLORS.primary} />
          </View>
          <Text style={styles.title}>Ride Complete!</Text>
          <Text style={styles.subtitle} numberOfLines={1}>
            {currentRide?.dropoff_address || 'Destination'}
          </Text>
        </View>

        {/* Route Map */}
        {currentRide?.pickup_lat && currentRide?.dropoff_lat && (
          <View style={styles.mapCard}>
            <MapView
              ref={mapRef}
              style={styles.map}
              provider={MAP_PROVIDER}
              scrollEnabled={false}
              zoomEnabled={false}
              rotateEnabled={false}
              pitchEnabled={false}
              initialRegion={{
                latitude: (currentRide.pickup_lat + currentRide.dropoff_lat) / 2,
                longitude: (currentRide.pickup_lng + currentRide.dropoff_lng) / 2,
                latitudeDelta: Math.abs(currentRide.pickup_lat - currentRide.dropoff_lat) * 2 + 0.01,
                longitudeDelta: Math.abs(currentRide.pickup_lng - currentRide.dropoff_lng) * 2 + 0.01,
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
                <View style={[styles.mapPin, { backgroundColor: COLORS.primary }]}>
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
            <Ionicons name="card" size={14} color="#666" />
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
              <Ionicons name="time-outline" size={18} color="#999" />
              <Text style={styles.statVal}>{duration} min</Text>
              <Text style={styles.statLbl}>Duration</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.stat}>
              <Ionicons name="speedometer-outline" size={18} color="#999" />
              <Text style={styles.statVal}>{distance.toFixed(1)} km</Text>
              <Text style={styles.statLbl}>Distance</Text>
            </View>
            <View style={styles.statDivider} />
            <View style={styles.stat}>
              <Ionicons name="cash-outline" size={18} color="#999" />
              <Text style={styles.statVal}>${fare.toFixed(2)}</Text>
              <Text style={styles.statLbl}>Total</Text>
            </View>
          </View>
        </View>

        {/* Rate Driver */}
        <View style={styles.rateCard}>
          <View style={styles.driverRow}>
            <View style={styles.driverAvatar}>
              <Ionicons name="person" size={24} color="#888" />
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
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFF' },
  content: { paddingHorizontal: 20, paddingTop: 20, paddingBottom: 20 },

  // Success
  successSection: { alignItems: 'center', marginBottom: 20 },
  checkCircle: {
    width: 80, height: 80, borderRadius: 40, backgroundColor: '#FEF2F2',
    justifyContent: 'center', alignItems: 'center', marginBottom: 14,
  },
  title: { fontSize: 24, fontWeight: '800', color: '#1A1A1A', marginBottom: 4 },
  subtitle: { fontSize: 14, color: '#888' },

  // Route Map
  mapCard: {
    width: '100%', height: 180, borderRadius: 18, overflow: 'hidden',
    marginBottom: 16, backgroundColor: '#F0F0F0',
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
  mapLabelText: { fontSize: 10, fontWeight: '700', color: '#1A1A1A', letterSpacing: 0.5 },

  // Fare Card
  fareCard: {
    backgroundColor: '#F9F9F9', borderRadius: 20, padding: 20, alignItems: 'center', marginBottom: 16,
  },
  fareAmount: { fontSize: 42, fontWeight: '800', color: COLORS.primary, marginBottom: 8 },
  paymentBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: '#FFF', paddingHorizontal: 14, paddingVertical: 6, borderRadius: 20,
    marginBottom: 16,
  },
  paymentText: { fontSize: 12, fontWeight: '600', color: '#666' },
  statsRow: { flexDirection: 'row', width: '100%' },
  stat: { flex: 1, alignItems: 'center' },
  statVal: { fontSize: 16, fontWeight: '700', color: '#1A1A1A', marginTop: 4 },
  statLbl: { fontSize: 10, color: '#999', marginTop: 2 },
  statDivider: { width: 1, backgroundColor: '#E8E8E8' },

  // Rate Card
  rateCard: {
    backgroundColor: '#F9F9F9', borderRadius: 20, padding: 20, marginBottom: 16,
  },
  driverRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 16 },
  driverAvatar: {
    width: 48, height: 48, borderRadius: 24, backgroundColor: '#E8E8E8',
    justifyContent: 'center', alignItems: 'center', marginRight: 12,
  },
  driverName: { fontSize: 16, fontWeight: '700', color: '#1A1A1A' },
  driverMeta: { fontSize: 12, color: '#888', marginTop: 2 },
  rateLabel: { fontSize: 15, fontWeight: '600', color: '#1A1A1A', textAlign: 'center', marginBottom: 12 },
  starsRow: { flexDirection: 'row', justifyContent: 'center', gap: 8, marginBottom: 6 },
  starBtn: { padding: 4 },
  ratingText: { fontSize: 13, color: '#888', textAlign: 'center', marginBottom: 14 },
  commentInput: {
    backgroundColor: '#FFF', borderRadius: 14, padding: 14, fontSize: 14, color: '#1A1A1A',
    minHeight: 60, textAlignVertical: 'top', borderWidth: 1, borderColor: '#ECECEC',
  },

  // Tip
  tipCard: {
    backgroundColor: '#F9F9F9', borderRadius: 20, padding: 20,
  },
  tipTitle: { fontSize: 15, fontWeight: '600', color: '#1A1A1A', marginBottom: 14, textAlign: 'center' },
  tipRow: { flexDirection: 'row', gap: 10, justifyContent: 'center' },
  tipBtn: {
    paddingHorizontal: 20, paddingVertical: 12, borderRadius: 14,
    backgroundColor: '#FFF', borderWidth: 1.5, borderColor: '#E5E5E5',
  },
  tipBtnActive: { backgroundColor: `${COLORS.primary}15`, borderColor: COLORS.primary },
  tipBtnText: { fontSize: 16, fontWeight: '700', color: '#1A1A1A' },
  tipBtnTextActive: { color: COLORS.primary },
  tipCustom: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#FFF', borderRadius: 14, borderWidth: 1.5, borderColor: '#E5E5E5',
    paddingHorizontal: 12, minWidth: 80,
  },
  tipCustomActive: { borderColor: COLORS.primary },
  tipDollar: { fontSize: 16, fontWeight: '600', color: '#999' },
  tipCustomInput: { fontSize: 16, fontWeight: '600', color: '#1A1A1A', paddingVertical: 12, paddingHorizontal: 4, minWidth: 44 },
  tipDone: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    paddingVertical: 12, backgroundColor: '#F0FFF4', borderRadius: 12,
  },
  tipDoneText: { fontSize: 15, fontWeight: '600', color: '#059669' },

  // Bottom
  bottomBar: {
    paddingHorizontal: 20, paddingVertical: 14,
    borderTopWidth: 1, borderTopColor: '#F0F0F0',
  },
  submitBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    backgroundColor: COLORS.primary, paddingVertical: 16, borderRadius: 28,
  },
  submitBtnText: { fontSize: 17, fontWeight: '700', color: '#FFF' },
});
