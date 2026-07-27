import React, { useEffect, useMemo, useState } from 'react';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import {
  View, Text, Image, StyleSheet, TouchableOpacity, Share, Platform, BackHandler, ActivityIndicator,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { RouteLine } from '@shared/components/RouteLine';
import { RoutePins } from '@shared/components/RoutePins';
import BottomSheet, { BottomSheetScrollView } from '../components/SafeBottomSheet';
import { useAppResumeKey } from '../hooks/useAppResumeKey';
import { useRideStore } from '../store/rideStore';
import { RideStatus } from '../constants/rideStatus';
import { CarMarker } from '@shared/components/CarMarker';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import ConfirmSheet from '../components/ConfirmSheet';
import CancelReasonSheet from '../components/CancelReasonSheet';
import { SOSButton } from '@shared/components/SOSButton';
import { FreeCancelTimer } from '../components/FreeCancelTimer';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

export default function DriverArrivedScreen() {
  return (
    <ErrorBoundary>
      <DriverArrivedScreenContent />
    </ErrorBoundary>
  );
}

function DriverArrivedScreenContent() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, cancelRide, clearRide, triggerEmergency, wsConnected } = useRideStore();
  const mapRef = React.useRef<MapView>(null);
  const bottomSheetRef = React.useRef<any>(null);
  const snapPoints = useMemo(() => ['42%', '70%'], []);
  const resumeKey = useAppResumeKey();
  const [routeCoords, setRouteCoords] = React.useState<any[]>([]);
  const [driverPhotoError, setDriverPhotoError] = useState(false);
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean;
    title: string;
    message?: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', variant: 'info' });
  const [reasonVisible, setReasonVisible] = useState(false);
  const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const fare = parseFloat((currentRide as any)?.grand_total || currentRide?.total_fare || '0');
  // Use server-provided cancellation fee; fall back to $3 default (matches app_settings).
  // The old Math.min(5, fare * 0.2) formula did not match the server-side value.
  const cancellationFee = (currentRide as any)?.cancellation_fee ?? 3.0;
  const freeCancelWindowSeconds = (currentRide as any)?.free_cancel_window_seconds ?? 120;
  const pickupOtp = currentRide?.pickup_otp || '----';

  const handleCancelPress = () => {
    setConfirmSheet({
      visible: true,
      title: 'Driver is waiting',
      message: `Your driver has arrived. A cancellation fee of $${cancellationFee.toFixed(2)} will be charged.`,
      variant: 'warning',
      buttons: [
        {
          text: `Cancel & Pay $${cancellationFee.toFixed(2)}`, style: 'destructive',
          // Collect a reason before cancelling.
          onPress: () => setReasonVisible(true),
        },
        { text: 'Keep Ride', style: 'cancel' },
      ],
    });
  };

  const doCancel = async (reason: string) => {
    try {
      await cancelRide(reason);
      clearRide();
      router.replace('/(tabs)' as any);
    } catch (err) {
      showToast('Could not cancel', getApiErrorMessage(err, 'Could not cancel your ride. Please try again.'), 'danger');
    }
  };

  const cancelDialogMessage = `Your driver has arrived and is waiting. A cancellation fee of $${cancellationFee.toFixed(2)} will be charged.`;

  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => { handleCancelPress(); return true; });
    return () => sub.remove();
  }, [currentRide?.total_fare, (currentRide as any)?.grand_total]);

  useEffect(() => {
    if (!rideId) return;
    fetchRide(rideId);
    if (wsConnected) return;
    const interval = setInterval(() => fetchRide(rideId), 15000);
    return () => clearInterval(interval);
  }, [rideId, wsConnected]);

  useEffect(() => {
    if (currentRide?.status === RideStatus.IN_PROGRESS) {
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
    try { await Share.share({ message: info }); } catch (err) { console.error('[driver-arrived]', err); }
  };

  const handleCopyOtp = async () => {
    await Clipboard.setStringAsync(pickupOtp);
    showToast('Copied!', 'OTP copied to clipboard.', 'success');
  };

  return (
    <View style={styles.container}>
      {/* Full-screen Map */}
      {currentRide ? (
        <MapView
          ref={mapRef}
          style={StyleSheet.absoluteFill}
          provider={MAP_PROVIDER}
          initialRegion={{
            latitude: currentRide.pickup_lat,
            longitude: currentRide.pickup_lng,
            latitudeDelta: 0.004,
            longitudeDelta: 0.004,
          }}
          showsUserLocation
          showsMyLocationButton={false}
          userInterfaceStyle={isDark ? "dark" : "light"}
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
          {/* Real pickup → dropoff route, drawn via the shared gradient line + pins. */}
          <RouteLine
            path={routeCoords}
            pickup={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
            destination={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
          />
          <RoutePins
            pickup={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
            dropoff={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
          />

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
      ) : (
        <View style={styles.mapErrorContainer}>
          <ActivityIndicator size="large" color="#EE2B2B" />
          <Text style={styles.mapErrorText}>Loading map…</Text>
          {rideId ? (
            <TouchableOpacity style={styles.mapRetryButton} onPress={() => fetchRide(rideId)}>
              <Text style={styles.mapRetryText}>Retry</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      )}

      {/* Header */}
      <SafeAreaView edges={['top']} style={styles.headerOverlay}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.hBtn} onPress={handleCancelPress}>
            <Ionicons name="arrow-back" size={22} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.arrivedChip}>
            <View style={styles.pulseGreen} />
            <Text style={styles.arrivedChipText} allowFontScaling={false}>Driver has arrived</Text>
          </View>
          <SOSButton rideId={rideId as string} onTrigger={triggerEmergency} />
        </View>
      </SafeAreaView>

      {/* Bottom Sheet */}
      <BottomSheet
        key={resumeKey}
        ref={bottomSheetRef}
        index={1}
        snapPoints={snapPoints}
        backgroundStyle={styles.sheetBg}
        handleIndicatorStyle={styles.sheetHandle}
        enableOverDrag={false}
      >
        <BottomSheetScrollView bounces={false} overScrollMode="never" contentContainerStyle={styles.sheetContent}>

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
                    <Text style={styles.otpNum} allowFontScaling={false}>{d}</Text>
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
                {currentDriver?.photo_url && !driverPhotoError ? (
                  <Image
                    source={{ uri: currentDriver.photo_url }}
                    style={styles.avatarImg}
                    resizeMode="cover"
                    onError={() => setDriverPhotoError(true)}
                  />
                ) : (
                  <Ionicons name="person" size={26} color="#888" />
                )}
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.driverName}>{currentDriver?.name || 'Your Driver'}</Text>
                <View style={styles.ratingRow}>
                  <Ionicons name="star" size={13} color="#FFB800" />
                  <Text style={styles.driverMeta}>
                    {currentDriver?.rating ? `${currentDriver.rating} rating` : 'No ratings yet'}
                  </Text>
                </View>
              </View>
              <TouchableOpacity style={styles.msgBtn} onPress={handleMessage}>
                <Ionicons name="chatbubble" size={18} color={colors.primary} />
              </TouchableOpacity>
            </View>

            {/* Vehicle */}
            <View style={styles.vehicleBar}>
              <View style={styles.vehicleIcon}>
                <Ionicons name="car" size={16} color={colors.primary} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.vehicleLabel}>VEHICLE</Text>
                <Text style={styles.vehicleName}>
                  {[currentDriver?.vehicle_color, currentDriver?.vehicle_make, currentDriver?.vehicle_model]
                    .filter(Boolean).join(' ') || 'Vehicle info unavailable'}
                </Text>
              </View>
              <View style={styles.plateWrap}>
                <Text style={styles.plateLabel}>PLATE</Text>
                <View style={styles.plateBadge}>
                  <Text style={styles.plateNum}>{currentDriver?.license_plate || 'N/A'}</Text>
                </View>
              </View>
            </View>
          </View>

          {/* Trip Summary */}
          <View style={styles.tripCard}>
            <View style={styles.tripRow}>
              <View style={styles.tripDots}>
                <View style={[styles.tripDot, { backgroundColor: '#10B981' }]} />
                <View style={styles.tripLine} />
                <View style={[styles.tripDot, { backgroundColor: colors.primary }]} />
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
              <Ionicons name="share-outline" size={20} color={colors.text} />
            </TouchableOpacity>
            <TouchableOpacity style={styles.actionIcon} onPress={handleCopyOtp}>
              <Ionicons name="copy-outline" size={20} color={colors.text} />
            </TouchableOpacity>
          </View>

          {/* Cancellation policy timer */}
          <View style={{ marginBottom: 8 }}>
            <FreeCancelTimer
              driverAcceptedAt={(currentRide as any)?.driver_accepted_at}
              rideStatus={currentRide?.status}
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
      <ConfirmSheet
        visible={confirmSheet.visible}
        title={confirmSheet.title}
        message={confirmSheet.message}
        variant={confirmSheet.variant}
        buttons={confirmSheet.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))}
      />
      <CancelReasonSheet
        visible={reasonVisible}
        message={cancelDialogMessage}
        onConfirm={doCancel}
        onClose={() => setReasonVisible(false)}
      />
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: '#E8E8E8' },
    mapErrorContainer: {
      ...StyleSheet.absoluteFill,
      backgroundColor: '#D4E4D4',
      justifyContent: 'center',
      alignItems: 'center',
    },
    mapErrorText: { marginTop: 12, fontSize: 16, fontWeight: '500', color: '#555' },
    mapRetryButton: {
      marginTop: 16, paddingHorizontal: 28, paddingVertical: 12,
      backgroundColor: '#EE2B2B', borderRadius: 24,
    },
    mapRetryText: { color: '#FFF', fontSize: 15, fontWeight: '700' },

    // Header
    headerOverlay: { position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10 },
    header: {
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingHorizontal: 16, paddingVertical: 10,
    },
    hBtn: {
      width: 44, height: 44, borderRadius: 22, backgroundColor: colors.surface,
      justifyContent: 'center', alignItems: 'center',
      elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.12, shadowRadius: 4,
    },
    arrivedChip: {
      flexDirection: 'row', alignItems: 'center',
      backgroundColor: colors.surface, paddingHorizontal: 16, paddingVertical: 10, borderRadius: 24,
      elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.12, shadowRadius: 4,
    },
    pulseGreen: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#10B981', marginRight: 8 },
    arrivedChipText: { fontSize: 14, fontWeight: '700', color: colors.text },

    // Sheet
    sheetBg: { borderTopLeftRadius: 24, borderTopRightRadius: 24 },
    sheetHandle: { backgroundColor: '#DDD', width: 40, height: 4, borderRadius: 2 },
    sheetContent: { paddingHorizontal: 20, paddingBottom: 40, paddingTop: 4 },

    // OTP Card
    otpCard: {
      backgroundColor: colors.primary, borderRadius: 20, padding: 20,
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
    driverCard: { backgroundColor: colors.surfaceLight, borderRadius: 18, padding: 16, marginBottom: 14 },
    driverTop: { flexDirection: 'row', alignItems: 'center', marginBottom: 14 },
    avatar: {
      width: 50, height: 50, borderRadius: 25, backgroundColor: '#E8E8E8',
      justifyContent: 'center', alignItems: 'center', marginRight: 12, overflow: 'hidden',
    },
    avatarImg: { width: 50, height: 50, borderRadius: 25 },
    driverName: { fontSize: 17, fontWeight: '700', color: colors.text },
    ratingRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 3 },
    driverMeta: { fontSize: 12, color: '#888', marginLeft: 2 },
    msgBtn: {
      width: 44, height: 44, borderRadius: 22,
      backgroundColor: `${colors.primary}12`, justifyContent: 'center', alignItems: 'center',
    },

    vehicleBar: {
      flexDirection: 'row', alignItems: 'center', gap: 10,
      paddingTop: 14, borderTopWidth: 1, borderTopColor: '#ECECEC',
    },
    vehicleIcon: {
      width: 32, height: 32, borderRadius: 8, backgroundColor: `${colors.primary}12`,
      justifyContent: 'center', alignItems: 'center',
    },
    vehicleLabel: { fontSize: 9, fontWeight: '700', color: '#888', letterSpacing: 0.8, marginBottom: 2 },
    vehicleName: { fontSize: 13, fontWeight: '600', color: colors.text },
    plateWrap: { alignItems: 'center' },
    plateLabel: { fontSize: 9, fontWeight: '700', color: '#888', letterSpacing: 0.8, marginBottom: 3 },
    plateBadge: {
      backgroundColor: colors.text, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 5,
    },
    plateNum: { fontSize: 13, fontWeight: '800', color: '#FFF', letterSpacing: 1.5 },

    // Trip Card
    tripCard: { backgroundColor: colors.surfaceLight, borderRadius: 18, padding: 16, marginBottom: 14 },
    tripRow: { flexDirection: 'row' },
    tripDots: { alignItems: 'center', marginRight: 12, paddingTop: 2 },
    tripDot: { width: 10, height: 10, borderRadius: 5 },
    tripLine: { width: 2, flex: 1, backgroundColor: '#DDD', marginVertical: 3 },
    tripLabel: { fontSize: 10, fontWeight: '600', color: colors.textDim, letterSpacing: 0.5, marginBottom: 2 },
    tripAddr: { fontSize: 14, fontWeight: '500', color: colors.text },

    fareRow: {
      flexDirection: 'row', marginTop: 14, paddingTop: 14,
      borderTopWidth: 1, borderTopColor: '#ECECEC',
    },
    fareItem: { flex: 1, alignItems: 'center' },
    fareVal: { fontSize: 15, fontWeight: '700', color: colors.text },
    fareLbl: { fontSize: 10, color: colors.textDim, marginTop: 2 },
    fareDivider: { width: 1, backgroundColor: '#ECECEC' },

    // Actions
    actionsRow: { flexDirection: 'row', gap: 10, marginBottom: 10 },
    actionPrimary: {
      flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
      backgroundColor: colors.primary, paddingVertical: 15, borderRadius: 16,
    },
    actionPrimaryText: { fontSize: 15, fontWeight: '700', color: '#FFF' },
    actionIcon: {
      width: 50, height: 50, borderRadius: 14,
      backgroundColor: colors.surfaceLight, justifyContent: 'center', alignItems: 'center',
    },

    cancelLink: { alignItems: 'center', paddingVertical: 10, marginBottom: 4 },
    cancelLinkText: { fontSize: 13, fontWeight: '500', color: colors.textDim },

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
}
