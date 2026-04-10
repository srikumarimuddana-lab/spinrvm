import React, { useEffect, useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, Share, Platform, BackHandler,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import BottomSheet, { BottomSheetScrollView } from '@gorhom/bottom-sheet';
import { useRideStore } from '../store/rideStore';
import { CarMarker } from '@shared/components/CarMarker';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';
import { SOSButton } from '@shared/components/SOSButton';
import { FreeCancelTimer } from '../components/FreeCancelTimer';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;
const COLORS = SpinrConfig.theme.colors;

export default function DriverArrivedScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, cancelRide, clearRide } = useRideStore();
  const mapRef = React.useRef<MapView>(null);
  const bottomSheetRef = React.useRef<BottomSheet>(null);
  const snapPoints = useMemo(() => ['42%', '70%', '92%'], []);
  const [routeCoords, setRouteCoords] = React.useState<any[]>([]);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });
  const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

  const fare = currentRide?.total_fare || 0;
  // Use server-provided cancellation fee; fall back to $3 default (matches app_settings).
  // The old Math.min(5, fare * 0.2) formula did not match the server-side value.
  const cancellationFee = (currentRide as any)?.cancellation_fee ?? 3.0;
  const freeCancelWindowSeconds = (currentRide as any)?.free_cancel_window_seconds ?? 120;
  const pickupOtp = currentRide?.pickup_otp || '----';

  const handleCancelPress = () => {
    setAlertState({
      visible: true,
      title: 'Driver is waiting',
      message: `Your driver has arrived. A cancellation fee of $${cancellationFee.toFixed(2)} will be charged.`,
      variant: 'warning',
      buttons: [
        { text: 'Keep Ride', style: 'cancel' },
        {
          text: `Cancel & Pay $${cancellationFee.toFixed(2)}`, style: 'destructive',
          onPress: async () => { await cancelRide(); clearRide(); router.replace('/(tabs)' as any); },
        },
      ],
    });
  };

  const cancelDialogMessage = `Your driver has arrived and is waiting. A cancellation fee of $${cancellationFee.toFixed(2)} will be charged.`;

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => { handleCancelPress(); return true; });
    return () => sub.remove();
  }, [currentRide?.total_fare]);

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
      const interval = setInterval(() => fetchRide(rideId), 3000);
      return () => clearInterval(interval);
    }
  }, [rideId]);

  useEffect(() => {
    if (currentRide?.status === 'in_progress') {
      router.replace({ pathname: '/ride-in-progress', params: { rideId } } as any);
    }
  }, [currentRide?.status]);

  useEffect(() => {
    if (currentRide && mapRef.current) {
      const coords = [{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }];
      if (currentDriver?.lat && currentDriver?.lng) {
        coords.push({ latitude: currentDriver.lat, longitude: currentDriver.lng });
      }
      mapRef.current.fitToCoordinates(coords, {
        edgePadding: { top: 100, right: 60, bottom: 320, left: 60 },
        animated: true,
      });
    }
  }, [currentRide?.pickup_lat, currentDriver?.lat]);

  const handleMessage = () => router.push({ pathname: '/chat-driver', params: { rideId } } as any);

  const handleShareTrip = async () => {
    const info = [
      `🚗 Spinr Ride — Driver Arrived`,
      ``,
      `Driver: ${currentDriver?.name || 'Unknown'}`,
      `Rating: ${currentDriver?.rating || 'New'} ⭐`,
      `Vehicle: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || ''} ${currentDriver?.vehicle_model || ''}`,
      `Plate: ${currentDriver?.license_plate || 'N/A'}`,
      ``,
      `📍 Pickup: ${currentRide?.pickup_address || ''}`,
      `📍 Dropoff: ${currentRide?.dropoff_address || ''}`,
      `🔑 OTP: ${pickupOtp}`,
    ].join('\n');
    try { await Share.share({ message: info }); } catch {}
  };

  const handleCopyOtp = async () => {
    await Clipboard.setStringAsync(pickupOtp);
    setAlertState({ visible: true, title: 'Copied!', message: 'OTP copied to clipboard', variant: 'success' });
  };

  return (
    <View style={styles.container}>
      {/* Full-screen Map */}
      {currentRide ? (
        <MapView
          ref={mapRef}
          style={StyleSheet.absoluteFillObject}
          provider={MAP_PROVIDER}
          initialRegion={{
            latitude: currentRide.pickup_lat,
            longitude: currentRide.pickup_lng,
            latitudeDelta: 0.004,
            longitudeDelta: 0.004,
          }}
          showsUserLocation
          showsMyLocationButton={false}
        >
          {/* Route: pickup → dropoff */}
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
                    edgePadding: { top: 100, right: 60, bottom: 320, left: 60 },
                    animated: true,
                  });
                }
              }}
            />
          )}
          {/* Orange → Red gradient */}
          {routeCoords.length > 1 && (() => {
            const total = routeCoords.length;
            const SEGS = 15;
            const chunk = Math.max(1, Math.floor(total / SEGS));
            const segs: { c: any[]; color: string }[] = [];
            for (let i = 0; i < total - 1; i += chunk) {
              const end = Math.min(i + chunk + 1, total);
              const t = i / Math.max(total - 1, 1);
              const r = Math.round(255 + (238 - 255) * t);
              const g = Math.round(149 + (43 - 149) * t);
              const b = Math.round(0 + (43 - 0) * t);
              segs.push({ c: routeCoords.slice(i, end), color: `rgb(${r},${g},${b})` });
            }
            return segs.map((s, idx) => (
              <Polyline key={`rs-${idx}`} coordinates={s.c} strokeWidth={4} strokeColor={s.color} lineCap="round" lineJoin="round" />
            ));
          })()}

          {/* Pickup pin with pulse */}
          <Marker coordinate={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }} anchor={{ x: 0.5, y: 0.5 }}>
            <View style={styles.pickupMarkerWrap}>
              <View style={styles.pickupPulse} />
              <View style={styles.pickupPin}>
                <Ionicons name="location" size={18} color="#FFF" />
              </View>
            </View>
          </Marker>

          {/* Dropoff pin */}
          <Marker coordinate={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }} anchor={{ x: 0.5, y: 0.5 }}>
            <View style={styles.dropoffPin}>
              <Ionicons name="flag" size={16} color="#FFF" />
            </View>
          </Marker>

          {/* Driver car at pickup */}
          {currentDriver?.lat && currentDriver?.lng && (
            <CarMarker
              coordinate={{ latitude: currentDriver.lat, longitude: currentDriver.lng }}
              heading={(currentDriver as any).heading}
              size={44}
              zIndex={100}
            />
          )}
        </MapView>
      ) : null}

      {/* Header */}
      <SafeAreaView edges={['top']} style={styles.headerOverlay}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.hBtn} onPress={handleCancelPress}>
            <Ionicons name="arrow-back" size={22} color="#1A1A1A" />
          </TouchableOpacity>
          <View style={styles.arrivedChip}>
            <View style={styles.pulseGreen} />
            <Text style={styles.arrivedChipText}>Driver has arrived</Text>
          </View>
          <SOSButton rideId={rideId as string} onTrigger={async (id, lat, lng) => {
            try { await api.post(`/rides/${id}/emergency`, { latitude: lat, longitude: lng }); } catch {}
          }} />
        </View>
      </SafeAreaView>

      {/* Bottom Sheet */}
      <BottomSheet
        ref={bottomSheetRef}
        index={1}
        snapPoints={snapPoints}
        backgroundStyle={styles.sheetBg}
        handleIndicatorStyle={styles.sheetHandle}
      >
        <BottomSheetScrollView contentContainerStyle={styles.sheetContent}>

          {/* OTP Card */}
          <View style={styles.otpCard}>
            <View style={styles.otpHeader}>
              <Ionicons name="key" size={18} color="rgba(255,255,255,0.8)" />
              <Text style={styles.otpTitle}>Pickup PIN</Text>
            </View>
            <TouchableOpacity onPress={handleCopyOtp} activeOpacity={0.8}>
              <View style={styles.otpDigits}>
                {pickupOtp.split('').map((d, i) => (
                  <View key={i} style={styles.otpBox}>
                    <Text style={styles.otpNum}>{d}</Text>
                  </View>
                ))}
              </View>
            </TouchableOpacity>
            <Text style={styles.otpSub}>Tap to copy · Share this with your driver to start the trip</Text>
          </View>

          {/* Driver Card */}
          <View style={styles.driverCard}>
            <View style={styles.driverTop}>
              <View style={styles.avatar}>
                <Ionicons name="person" size={26} color="#888" />
                {currentDriver?.rating && (
                  <View style={styles.ratingPill}>
                    <Ionicons name="star" size={9} color="#FFB800" />
                    <Text style={styles.ratingNum}>{currentDriver.rating}</Text>
                  </View>
                )}
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.driverName}>{currentDriver?.name || 'Your Driver'}</Text>
                <Text style={styles.driverMeta}>
                  {currentDriver?.total_rides || 0} trips · Arrived at pickup
                </Text>
              </View>
              <TouchableOpacity style={styles.msgBtn} onPress={handleMessage}>
                <Ionicons name="chatbubble" size={18} color={COLORS.primary} />
              </TouchableOpacity>
            </View>

            {/* Vehicle */}
            <View style={styles.vehicleBar}>
              <View style={styles.vehicleIcon}>
                <Ionicons name="car" size={16} color={COLORS.primary} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.vehicleName}>
                  {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
                </Text>
              </View>
              <View style={styles.plateBadge}>
                <Text style={styles.plateNum}>{currentDriver?.license_plate || 'N/A'}</Text>
              </View>
            </View>
          </View>

          {/* Trip Summary */}
          <View style={styles.tripCard}>
            <View style={styles.tripRow}>
              <View style={styles.tripDots}>
                <View style={[styles.tripDot, { backgroundColor: '#10B981' }]} />
                <View style={styles.tripLine} />
                <View style={[styles.tripDot, { backgroundColor: COLORS.primary }]} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.tripLabel}>PICKUP</Text>
                <Text style={styles.tripAddr} numberOfLines={1}>{currentRide?.pickup_address || 'Pickup location'}</Text>
                <View style={{ height: 18 }} />
                <Text style={styles.tripLabel}>DROPOFF</Text>
                <Text style={styles.tripAddr} numberOfLines={1}>{currentRide?.dropoff_address || 'Destination'}</Text>
              </View>
            </View>

            {/* Fare Row */}
            <View style={styles.fareRow}>
              <View style={styles.fareItem}>
                <Text style={styles.fareVal}>${fare.toFixed(2)}</Text>
                <Text style={styles.fareLbl}>Fare</Text>
              </View>
              <View style={styles.fareDivider} />
              <View style={styles.fareItem}>
                <Text style={styles.fareVal}>{(currentRide?.distance_km || 0).toFixed(1)} km</Text>
                <Text style={styles.fareLbl}>Distance</Text>
              </View>
              <View style={styles.fareDivider} />
              <View style={styles.fareItem}>
                <Text style={styles.fareVal}>{currentRide?.duration_minutes || '--'} min</Text>
                <Text style={styles.fareLbl}>Est. Time</Text>
              </View>
            </View>
          </View>

          {/* Actions */}
          <View style={styles.actionsRow}>
            <TouchableOpacity style={styles.actionPrimary} onPress={handleMessage}>
              <Ionicons name="chatbubble" size={18} color="#FFF" />
              <Text style={styles.actionPrimaryText}>Message Driver</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.actionIcon} onPress={handleShareTrip}>
              <Ionicons name="share-outline" size={20} color="#1A1A1A" />
            </TouchableOpacity>
            <TouchableOpacity style={styles.actionIcon} onPress={handleCopyOtp}>
              <Ionicons name="copy-outline" size={20} color="#1A1A1A" />
            </TouchableOpacity>
          </View>

          {/* Cancellation policy timer */}
          <View style={{ marginBottom: 8 }}>
            <FreeCancelTimer
              driverAcceptedAt={(currentRide as any)?.driver_accepted_at}
              freeCancelWindowSeconds={freeCancelWindowSeconds}
              cancellationFee={cancellationFee}
            />
          </View>

          {/* Cancel link */}
          <TouchableOpacity style={styles.cancelLink} onPress={handleCancelPress}>
            <Text style={styles.cancelLinkText}>
              Cancel Ride
            </Text>
          </TouchableOpacity>

          {/* DEV */}
          {__DEV__ && (
            <View style={styles.devBar}>
              <Text style={styles.devLabel}>DEV: {currentRide?.status}</Text>
              <TouchableOpacity style={styles.devBtn} onPress={async () => {
                try { await api.post(`/drivers/rides/${currentRide?.id}/start`); } catch(e) { console.log(e); }
                if (rideId) fetchRide(rideId);
              }}>
                <Text style={styles.devBtnText}>Start Ride (skip OTP)</Text>
              </TouchableOpacity>
            </View>
          )}
        </BottomSheetScrollView>
      </BottomSheet>
      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#E8E8E8' },

  // Map markers
  pickupMarkerWrap: { alignItems: 'center', justifyContent: 'center', width: 56, height: 56 },
  pickupPulse: {
    position: 'absolute', width: 56, height: 56, borderRadius: 28,
    backgroundColor: 'rgba(238, 43, 43, 0.12)',
  },
  pickupPin: {
    width: 36, height: 36, borderRadius: 18,
    backgroundColor: COLORS.primary, justifyContent: 'center', alignItems: 'center',
    borderWidth: 3, borderColor: '#FFF',
    elevation: 5, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.25, shadowRadius: 4,
  },

  dropoffPin: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: COLORS.primary, justifyContent: 'center', alignItems: 'center',
    borderWidth: 2, borderColor: '#FFF',
    elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.2, shadowRadius: 3,
  },

  // Header
  headerOverlay: { position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10 },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 10,
  },
  hBtn: {
    width: 44, height: 44, borderRadius: 22, backgroundColor: '#FFF',
    justifyContent: 'center', alignItems: 'center',
    elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.12, shadowRadius: 4,
  },
  arrivedChip: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#FFF', paddingHorizontal: 16, paddingVertical: 10, borderRadius: 24,
    elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.12, shadowRadius: 4,
  },
  pulseGreen: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#10B981', marginRight: 8 },
  arrivedChipText: { fontSize: 14, fontWeight: '700', color: '#1A1A1A' },

  // Sheet
  sheetBg: { borderTopLeftRadius: 24, borderTopRightRadius: 24 },
  sheetHandle: { backgroundColor: '#DDD', width: 40, height: 4, borderRadius: 2 },
  sheetContent: { paddingHorizontal: 20, paddingBottom: 40, paddingTop: 4 },

  // OTP Card
  otpCard: {
    backgroundColor: COLORS.primary, borderRadius: 20, padding: 20,
    alignItems: 'center', marginBottom: 16,
  },
  otpHeader: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 14 },
  otpTitle: { fontSize: 14, fontWeight: '600', color: 'rgba(255,255,255,0.85)' },
  otpDigits: { flexDirection: 'row', gap: 10, marginBottom: 12 },
  otpBox: {
    width: 52, height: 60, backgroundColor: 'rgba(255,255,255,0.18)',
    borderRadius: 14, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.15)',
  },
  otpNum: { fontSize: 28, fontWeight: '800', color: '#FFF' },
  otpSub: { fontSize: 12, color: 'rgba(255,255,255,0.6)', textAlign: 'center' },

  // Driver Card
  driverCard: { backgroundColor: '#F9F9F9', borderRadius: 18, padding: 16, marginBottom: 14 },
  driverTop: { flexDirection: 'row', alignItems: 'center', marginBottom: 14 },
  avatar: {
    width: 50, height: 50, borderRadius: 25, backgroundColor: '#E8E8E8',
    justifyContent: 'center', alignItems: 'center', marginRight: 12, position: 'relative',
  },
  ratingPill: {
    position: 'absolute', bottom: -3, left: -3,
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#FFF', paddingHorizontal: 5, paddingVertical: 2, borderRadius: 8,
    shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.1, shadowRadius: 2, elevation: 2,
  },
  ratingNum: { fontSize: 10, fontWeight: '700', color: '#1A1A1A', marginLeft: 2 },
  driverName: { fontSize: 17, fontWeight: '700', color: '#1A1A1A' },
  driverMeta: { fontSize: 12, color: '#888', marginTop: 2 },
  msgBtn: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: `${COLORS.primary}12`, justifyContent: 'center', alignItems: 'center',
  },

  vehicleBar: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingTop: 14, borderTopWidth: 1, borderTopColor: '#ECECEC',
  },
  vehicleIcon: {
    width: 32, height: 32, borderRadius: 8, backgroundColor: `${COLORS.primary}12`,
    justifyContent: 'center', alignItems: 'center',
  },
  vehicleName: { fontSize: 13, fontWeight: '500', color: '#444' },
  plateBadge: {
    backgroundColor: '#1A1A1A', borderRadius: 6, paddingHorizontal: 10, paddingVertical: 5,
  },
  plateNum: { fontSize: 13, fontWeight: '800', color: '#FFF', letterSpacing: 1.5 },

  // Trip Card
  tripCard: { backgroundColor: '#F9F9F9', borderRadius: 18, padding: 16, marginBottom: 14 },
  tripRow: { flexDirection: 'row' },
  tripDots: { alignItems: 'center', marginRight: 12, paddingTop: 2 },
  tripDot: { width: 10, height: 10, borderRadius: 5 },
  tripLine: { width: 2, flex: 1, backgroundColor: '#DDD', marginVertical: 3 },
  tripLabel: { fontSize: 10, fontWeight: '600', color: '#999', letterSpacing: 0.5, marginBottom: 2 },
  tripAddr: { fontSize: 14, fontWeight: '500', color: '#1A1A1A' },

  fareRow: {
    flexDirection: 'row', marginTop: 14, paddingTop: 14,
    borderTopWidth: 1, borderTopColor: '#ECECEC',
  },
  fareItem: { flex: 1, alignItems: 'center' },
  fareVal: { fontSize: 15, fontWeight: '700', color: '#1A1A1A' },
  fareLbl: { fontSize: 10, color: '#999', marginTop: 2 },
  fareDivider: { width: 1, backgroundColor: '#ECECEC' },

  // Actions
  actionsRow: { flexDirection: 'row', gap: 10, marginBottom: 10 },
  actionPrimary: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    backgroundColor: COLORS.primary, paddingVertical: 15, borderRadius: 16,
  },
  actionPrimaryText: { fontSize: 15, fontWeight: '700', color: '#FFF' },
  actionIcon: {
    width: 50, height: 50, borderRadius: 14,
    backgroundColor: '#F5F5F5', justifyContent: 'center', alignItems: 'center',
  },

  cancelLink: { alignItems: 'center', paddingVertical: 10, marginBottom: 4 },
  cancelLinkText: { fontSize: 13, fontWeight: '500', color: '#999' },

  // Dev
  devBar: {
    flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8,
    marginTop: 8, padding: 12, backgroundColor: '#FEF3C7', borderRadius: 12,
    borderWidth: 1, borderColor: '#F59E0B',
  },
  devLabel: { fontSize: 11, fontWeight: '700', color: '#92400E', marginRight: 4 },
  devBtn: { backgroundColor: '#F59E0B', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
  devBtnText: { fontSize: 12, fontWeight: '700', color: '#FFF' },
});
