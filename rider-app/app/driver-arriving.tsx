import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Dimensions,
  Share,
  Linking,
  Platform,
  ActivityIndicator,
  BackHandler,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import BottomSheet, { BottomSheetScrollView, BottomSheetView } from '@gorhom/bottom-sheet';
import { useRideStore } from '../store/rideStore';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';
import { SOSButton } from '@shared/components/SOSButton';
import { CarMarker } from '@shared/components/CarMarker';

const { width } = Dimensions.get('window');

export default function DriverArrivingScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, simulateDriverArrival, triggerEmergency, isLoading, error } = useRideStore();
  const [eta, setEta] = useState(4);
  const [mapError, setMapError] = useState<string | null>(null);
  const [driverRouteCoords, setDriverRouteCoords] = useState<any[]>([]);
  const [rideRouteCoords, setRideRouteCoords] = useState<any[]>([]);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });
  const mapRef = React.useRef<MapView>(null);
  const bottomSheetRef = React.useRef<BottomSheet>(null);

  const snapPoints = React.useMemo(() => ['30%', '50%', '85%'], []);

  useEffect(() => {
    if (currentRide && mapRef.current) {
      if (currentDriver?.lat && currentDriver?.lng) {
        mapRef.current.fitToCoordinates(
          [
            { latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng },
            { latitude: currentDriver.lat, longitude: currentDriver.lng }
          ],
          {
            edgePadding: { top: 50, right: 50, bottom: 250, left: 50 },
            animated: true,
          }
        );
      } else {
        mapRef.current.animateToRegion({
          latitude: currentRide.pickup_lat,
          longitude: currentRide.pickup_lng,
          latitudeDelta: 0.02,
          longitudeDelta: 0.02,
        });
      }
    }
  }, [currentRide?.pickup_lat, currentRide?.pickup_lng, currentDriver?.lat, currentDriver?.lng]);

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
      const interval = setInterval(() => fetchRide(rideId), 3000);
      return () => clearInterval(interval);
    }
  }, [rideId]);

  useEffect(() => {
    // Check status changes
    if (currentRide?.status === 'driver_arrived') {
      router.replace({ pathname: '/driver-arrived', params: { rideId } });
    } else if (currentRide?.status === 'in_progress') {
      router.replace({ pathname: '/ride-in-progress', params: { rideId } });
    }
  }, [currentRide?.status]);

  const { cancelRide, clearRide } = useRideStore();

  const handleBack = () => {
    const status = currentRide?.status;
    const fare = currentRide?.total_fare || 0;
    const cancellationFee = Math.min(5, fare * 0.2); // 20% of fare or $5, whichever is less

    if (status === 'in_progress') {
      // Ride started — full fare
      setAlertState({
        visible: true,
        title: 'Ride in progress',
        message: `Your ride is in progress. If you cancel now, you will be charged the full fare of $${fare.toFixed(2)}.`,
        variant: 'warning',
        buttons: [
          { text: 'Continue Ride', style: 'cancel' },
          {
            text: `Cancel & Pay $${fare.toFixed(2)}`, style: 'destructive',
            onPress: async () => {
              await cancelRide();
              clearRide();
              router.replace('/ride-options' as any);
            },
          },
        ],
      });
    } else if (status === 'driver_arrived') {
      // Driver at pickup — cancellation fee
      setAlertState({
        visible: true,
        title: 'Driver is waiting',
        message: `Your driver has arrived at the pickup. A cancellation fee of $${cancellationFee.toFixed(2)} will be charged.`,
        variant: 'warning',
        buttons: [
          { text: 'Keep Ride', style: 'cancel' },
          {
            text: `Cancel & Pay $${cancellationFee.toFixed(2)}`, style: 'destructive',
            onPress: async () => {
              await cancelRide();
              clearRide();
              router.replace('/ride-options' as any);
            },
          },
        ],
      });
    } else if (status === 'driver_assigned' || status === 'driver_accepted') {
      // Driver on the way — free cancel
      setAlertState({
        visible: true,
        title: 'Cancel ride?',
        message: 'Your driver is on the way. You can cancel for free right now.',
        variant: 'warning',
        buttons: [
          { text: 'Keep Ride', style: 'cancel' },
          {
            text: 'Cancel (Free)', style: 'destructive',
            onPress: async () => {
              await cancelRide();
              clearRide();
              router.replace('/ride-options' as any);
            },
          },
        ],
      });
    } else {
      // Still searching — free cancel
      setAlertState({
        visible: true,
        title: 'Cancel search?',
        message: 'Stop looking for a driver? No charge.',
        variant: 'info',
        buttons: [
          { text: 'Keep searching', style: 'cancel' },
          {
            text: 'Cancel',
            onPress: async () => {
              await cancelRide();
              clearRide();
              router.replace('/ride-options' as any);
            },
          },
        ],
      });
    }
  };

  // Android hardware back
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      handleBack();
      return true;
    });
    return () => sub.remove();
  }, [currentRide?.status]);

  const handleEmergency = () => {
    setAlertState({
      visible: true,
      title: 'Emergency',
      message: 'Are you sure you want to contact emergency services?',
      variant: 'danger',
      buttons: [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Call 911',
          style: 'destructive',
          onPress: () => {
            if (rideId) triggerEmergency(rideId as string);
            Linking.openURL('tel:911');
          },
        },
      ],
    });
  };

  const handleMessage = () => {
    router.push({ pathname: '/chat-driver', params: { rideId } });
  };

  const handleCall = () => {
    // Initiate call
  };

  const handleShareTrip = async () => {
    const driverInfo = `
🚗 SPINR RIDE - TRIP DETAILS

👤 DRIVER: ${currentDriver?.name || 'Assigning...'}
⭐ RATING: ${currentDriver?.rating || 'New'}

🚙 VEHICLE: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || 'Unknown'} ${currentDriver?.vehicle_model || 'Vehicle'}
📋 LICENSE PLATE: ${currentDriver?.license_plate || 'Pending'}

📍 PICKUP: ${currentRide?.pickup_address || 'University of Saskatchewan'}
📍 DESTINATION: ${currentRide?.dropoff_address || '123 Main St, Saskatoon'}

⏱️ ETA: ${eta} minutes

Track my live location: https://spinr-track.app/${rideId || 'demo'}

I'm sharing this ride for safety. If you don't hear from me, please check on me.
    `.trim();

    try {
      await Share.share({
        message: driverInfo,
        title: 'My Spinr Ride Details',
      });
    } catch (error) {
      console.log('Share error:', error);
    }
  };

  const handleCopyDetails = async () => {
    const details = `Driver: ${currentDriver?.name || 'Assigning...'} | Vehicle: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || 'Unknown'} ${currentDriver?.vehicle_model || 'Vehicle'} | Plate: ${currentDriver?.license_plate || 'Pending'} | Rating: ${currentDriver?.rating || 'New'}`;
    await Clipboard.setStringAsync(details);
    setAlertState({ visible: true, title: 'Copied!', message: 'Driver details copied to clipboard', variant: 'success' });
  };

  // simulateDriverArrival demo function removed for production

  return (
    <View style={styles.container}>
      {/* Header */}
      <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backButton} onPress={handleBack}>
            <Ionicons name="arrow-back" size={24} color="#1A1A1A" />
          </TouchableOpacity>

          <View style={styles.etaPill}>
            <View style={styles.greenDot} />
            <Text style={styles.etaText}>Arriving in {eta} min</Text>
          </View>

          <SOSButton rideId={rideId as string} onTrigger={triggerEmergency} />
        </View>
      </SafeAreaView>

      {/* Map Area */}
      <View style={styles.mapContainer}>
        {isLoading ? (
          <View style={styles.mapPlaceholder}>
            <ActivityIndicator size="large" color={SpinrConfig.theme.colors.primary} />
            <Text style={styles.loadingText}>Loading ride details...</Text>
          </View>
        ) : error ? (
          <View style={styles.mapPlaceholder}>
            <Ionicons name="alert-circle" size={48} color="#EF4444" />
            <Text style={styles.errorText}>Error: {error}</Text>
            <TouchableOpacity style={styles.retryButton} onPress={() => rideId && fetchRide(rideId)}>
              <Text style={styles.retryButtonText}>Retry</Text>
            </TouchableOpacity>
          </View>
        ) : currentRide ? (
          <MapView
            {...({ ref: mapRef } as any)}
            provider={Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined}
            style={styles.map}
            initialRegion={{
              latitude: currentRide.pickup_lat,
              longitude: currentRide.pickup_lng,
              latitudeDelta: 0.05,
              longitudeDelta: 0.05,
            }}
          >
            {/* Route from driver to pickup — blue dashed */}
            {currentDriver?.lat && currentDriver?.lng && (
              <MapViewDirections
                origin={{ latitude: currentDriver.lat, longitude: currentDriver.lng }}
                destination={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
                apikey={process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || ''}
                strokeWidth={0}
                strokeColor="transparent"
                onReady={(result: any) => {
                  setEta(Math.ceil(result.duration));
                  setDriverRouteCoords(result.coordinates);
                }}
              />
            )}
            {driverRouteCoords.length > 1 && (
              <Polyline
                coordinates={driverRouteCoords}
                strokeWidth={4}
                strokeColor="#3B82F6"
                lineDashPattern={[8, 6]}
                lineCap="round"
              />
            )}

            {/* Route from pickup to dropoff — orange to red gradient */}
            <MapViewDirections
              origin={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
              destination={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
              apikey={process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || ''}
              strokeWidth={0}
              strokeColor="transparent"
              onReady={(result: any) => setRideRouteCoords(result.coordinates)}
            />
            {rideRouteCoords.length > 1 && (() => {
              const total = rideRouteCoords.length;
              const SEGS = 20;
              const chunk = Math.max(1, Math.floor(total / SEGS));
              const segments: { coords: any[]; color: string }[] = [];
              for (let i = 0; i < total - 1; i += chunk) {
                const end = Math.min(i + chunk + 1, total);
                const t = i / Math.max(total - 1, 1);
                const r = Math.round(255 + (238 - 255) * t);
                const g = Math.round(149 + (43 - 149) * t);
                const b = Math.round(0 + (43 - 0) * t);
                segments.push({ coords: rideRouteCoords.slice(i, end), color: `rgb(${r},${g},${b})` });
              }
              return segments.map((seg, idx) => (
                <Polyline
                  key={`ride-seg-${idx}`}
                  coordinates={seg.coords}
                  strokeWidth={4}
                  strokeColor={seg.color}
                  lineCap="round"
                  lineJoin="round"
                />
              ));
            })()}

            {/* Pickup Marker */}
            <Marker coordinate={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}>
              <View style={styles.pickupMarker}>
                <View style={[styles.pickupDot, { backgroundColor: '#10B981' }]} />
              </View>
            </Marker>

            {/* Dropoff Marker */}
            <Marker coordinate={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}>
              <View style={styles.pickupMarker}>
                <View style={[styles.pickupDot, { backgroundColor: SpinrConfig.theme.colors.primary }]} />
              </View>
            </Marker>

            {/* Driver Car Marker */}
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
          <View style={styles.mapPlaceholder}>
            <Text>Loading map...</Text>
          </View>
        )}
      </View>

      {/* Bottom Sheet */}
      <BottomSheet
        ref={bottomSheetRef}
        index={1}
        snapPoints={snapPoints}
        enablePanDownToClose={false}
        backgroundStyle={styles.bottomSheetBackground}
        handleIndicatorStyle={styles.sheetHandleIndicator}
      >
        {/* @ts-ignore - gorhom/bottom-sheet v4 has a known children typing bug with React 18 */}
        <BottomSheetScrollView>
          <BottomSheetView style={styles.bottomSheetContent}>
            {/* Safety Banner - Share Trip */}
            <TouchableOpacity style={styles.shareTripBanner} onPress={handleShareTrip}>
              <View style={styles.shareTripIcon}>
                <Ionicons name="share-social" size={18} color="#FFF" />
              </View>
              <View style={styles.shareTripContent}>
                <Text style={styles.shareTripTitle}>Share your trip</Text>
                <Text style={styles.shareTripSubtitle}>Let friends & family track your live location</Text>
              </View>
              <Ionicons name="chevron-forward" size={20} color={SpinrConfig.theme.colors.primary} />
            </TouchableOpacity>

            {/* Driver card — only shown AFTER the driver has actually
                accepted the ride. Before that, we show a waiting placeholder
                so the rider doesn't see a driver name/plate while the driver
                is still deciding. The backend sets status='driver_assigned'
                on dispatch and flips to 'driver_accepted' only when the
                driver taps Accept in the driver-app. */}
            {(currentRide?.status === 'driver_accepted' ||
              currentRide?.status === 'driver_arrived' ||
              currentRide?.status === 'in_progress') ? (
              <>
                <View style={styles.driverDetailsCard}>
                  <View style={styles.driverCardHeader}>
                    <Text style={styles.driverCardTitle}>YOUR DRIVER</Text>
                    <TouchableOpacity style={styles.copyButton} onPress={handleCopyDetails}>
                      <Ionicons name="copy-outline" size={16} color="#666" />
                      <Text style={styles.copyText}>Copy Details</Text>
                    </TouchableOpacity>
                  </View>

                  <View style={styles.driverSection}>
                    <View style={styles.driverAvatar}>
                      <Ionicons name="person" size={28} color="#666" />
                      <View style={styles.ratingBadge}>
                        <Ionicons name="star" size={10} color="#FFB800" />
                        <Text style={styles.ratingText}>{currentDriver?.rating || 'New'}</Text>
                      </View>
                    </View>

                    <View style={styles.driverInfo}>
                      <Text style={styles.driverName}>{currentDriver?.name || 'Assigning...'}</Text>
                      <Text style={styles.totalTrips}>{currentDriver?.total_rides || 0} trips completed</Text>
                    </View>
                  </View>

                  {/* Vehicle Details - Clear for Screenshot */}
                  <View style={styles.vehicleSection}>
                    <View style={styles.vehicleRow}>
                      <Ionicons name="car" size={20} color={SpinrConfig.theme.colors.primary} />
                      <View style={styles.vehicleTextContainer}>
                        <Text style={styles.vehicleLabel}>VEHICLE</Text>
                        <Text style={styles.vehicleValue}>
                          {currentDriver?.vehicle_color || ''} {currentDriver?.vehicle_make || 'Unknown'} {currentDriver?.vehicle_model || 'Vehicle'}
                        </Text>
                      </View>
                    </View>

                    <View style={styles.plateRow}>
                      <View style={styles.plateIconContainer}>
                        <Text style={styles.plateIcon}>🪪</Text>
                      </View>
                      <View style={styles.vehicleTextContainer}>
                        <Text style={styles.vehicleLabel}>LICENSE PLATE</Text>
                        <Text style={styles.plateValue}>{currentDriver?.license_plate || 'Pending'}</Text>
                      </View>
                    </View>
                  </View>
                </View>

                {/* Pickup PIN — shown after driver accepts so the rider can
                    prepare to share it when the driver arrives. The driver
                    enters this 4-digit code on their app to verify the right
                    rider before starting the trip. */}
                {currentRide?.pickup_otp && (
                  <View style={styles.pinCard}>
                    <Text style={styles.pinLabel}>SHARE THIS PIN WITH YOUR DRIVER</Text>
                    <View style={styles.pinBoxes}>
                      {[0, 1, 2, 3].map((i) => (
                        <View key={i} style={styles.pinBox}>
                          <Text style={styles.pinDigit}>
                            {currentRide.pickup_otp?.[i] || '•'}
                          </Text>
                        </View>
                      ))}
                    </View>
                    <Text style={styles.pinHint}>
                      The driver will ask for this when they arrive at pickup
                    </Text>
                  </View>
                )}
              </>
            ) : (
              <View style={styles.waitingDriverCard}>
                <ActivityIndicator size="small" color={SpinrConfig.theme.colors.primary} />
                <View style={styles.waitingDriverTextContainer}>
                  <Text style={styles.waitingDriverTitle}>Finding your driver</Text>
                  <Text style={styles.waitingDriverSubtitle}>
                    We're confirming a driver for your trip. Details will appear here
                    as soon as they accept.
                  </Text>
                </View>
              </View>
            )}

            {/* Action Buttons */}
            <View style={styles.actionButtons}>
              <TouchableOpacity style={styles.messageButton} onPress={handleMessage}>
                <Ionicons name="chatbubble" size={20} color="#FFF" />
                <Text style={styles.messageButtonText}>Message</Text>
              </TouchableOpacity>

              <TouchableOpacity style={styles.shareButton} onPress={handleShareTrip}>
                <Ionicons name="share-outline" size={22} color="#1A1A1A" />
              </TouchableOpacity>
            </View>

            {/* Progress Bar */}
            <View style={styles.progressContainer}>
              <View style={[styles.progressBar, { width: '70%' }]} />
            </View>

            {/* Trip Details */}
            <View style={styles.tripDetails}>
              <View style={styles.tripRow}>
                <View style={styles.tripIndicator}>
                  <View style={styles.grayDot} />
                  <View style={styles.tripLine} />
                  <View style={styles.redDot} />
                </View>

                <View style={styles.tripAddresses}>
                  <View style={styles.addressRow}>
                    <Text style={styles.addressLabel}>PICKED UP</Text>
                    <Text style={styles.addressText} numberOfLines={1}>
                      {currentRide?.pickup_address || 'University of Saskatchewan'}
                    </Text>
                  </View>

                  <View style={[styles.addressRow, { marginTop: 16 }]}>
                    <Text style={styles.dropoffLabel}>DROPPING OFF</Text>
                    <Text style={styles.dropoffText} numberOfLines={1}>
                      {currentRide?.dropoff_address || '123 Main St, Saskatoon'}
                    </Text>
                    <Text style={styles.etaArrival}>Est. arrival 4:15 PM</Text>
                  </View>
                </View>
              </View>
            </View>

            {/* DEV CONTROLS — remove in production */}
            {__DEV__ && (
              <View style={styles.devBar}>
                <Text style={styles.devLabel}>DEV: {currentRide?.status || 'unknown'}</Text>
                {(!currentRide?.status || currentRide?.status === 'searching') && (
                  <TouchableOpacity style={styles.devBtn} onPress={async () => {
                    try { await simulateDriverArrival(); } catch(e) { console.log(e); }
                    if (rideId) fetchRide(rideId);
                  }}>
                    <Text style={styles.devBtnText}>Assign Driver</Text>
                  </TouchableOpacity>
                )}
                {(currentRide?.status === 'driver_assigned' || currentRide?.status === 'driver_accepted') && (
                  <TouchableOpacity style={styles.devBtn} onPress={async () => {
                    try {
                                            await api.post(`/drivers/rides/${currentRide.id}/arrive`);
                    } catch(e) { console.log(e); }
                    if (rideId) fetchRide(rideId);
                  }}>
                    <Text style={styles.devBtnText}>Driver Arrive</Text>
                  </TouchableOpacity>
                )}
                {currentRide?.status === 'driver_arrived' && (
                  <TouchableOpacity style={styles.devBtn} onPress={async () => {
                    try {
                                            await api.post(`/drivers/rides/${currentRide.id}/start`);
                    } catch(e) { console.log(e); }
                    if (rideId) fetchRide(rideId);
                  }}>
                    <Text style={styles.devBtnText}>Start Ride</Text>
                  </TouchableOpacity>
                )}
                {currentRide?.status === 'in_progress' && (
                  <TouchableOpacity style={styles.devBtn} onPress={async () => {
                    try {
                                            await api.post(`/drivers/rides/${currentRide.id}/complete`);
                    } catch(e) { console.log(e); }
                    if (rideId) fetchRide(rideId);
                  }}>
                    <Text style={styles.devBtnText}>Complete Ride</Text>
                  </TouchableOpacity>
                )}
              </View>
            )}
          </BottomSheetView>
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
  container: {
    flex: 1,
    backgroundColor: '#E8E8E8',
  },
  headerSafeArea: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 10,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  backButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FFF',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  etaPill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  greenDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#10B981',
    marginRight: 8,
  },
  etaText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  emergencyButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#FFF',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  mapContainer: {
    flex: 1,
  },
  map: {
    ...StyleSheet.absoluteFillObject,
  },
  mapPlaceholder: {
    flex: 1,
    backgroundColor: '#D4E4D4',
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingText: {
    marginTop: 16,
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  errorText: {
    marginTop: 12,
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#EF4444',
    textAlign: 'center',
    paddingHorizontal: 20,
  },
  retryButton: {
    marginTop: 16,
    paddingHorizontal: 24,
    paddingVertical: 12,
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 24,
  },
  retryButtonText: {
    color: '#FFF',
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
  },
  driverMarkerOuter: {
    alignItems: 'center',
  },
  driverMarkerInner: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 3,
    borderColor: '#FFF',
  },
  etaBadge: {
    backgroundColor: '#FFF',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
    marginTop: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  etaBadgeText: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#1A1A1A',
  },
  pickupMarker: {
    position: 'absolute',
    top: '25%',
    right: '25%',
  },
  pickupDot: {
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderWidth: 4,
    borderColor: '#FFF',
  },
  bottomSheetBackground: {
    backgroundColor: '#FFF',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
  },
  sheetHandleIndicator: {
    width: 40,
    backgroundColor: '#E0E0E0',
  },
  bottomSheetContent: {
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 40,
  },
  driverSection: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  driverAvatar: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#E8E8E8',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
    position: 'relative',
  },
  ratingBadge: {
    position: 'absolute',
    bottom: -4,
    left: -4,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  ratingText: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    marginLeft: 2,
  },
  driverInfo: {
    flex: 1,
  },
  driverName: {
    fontSize: 20,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  vehicleInfo: {
    fontSize: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 2,
  },
  plateContainer: {
    backgroundColor: '#F5F5F5',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
    alignSelf: 'flex-start',
    marginTop: 6,
  },
  plateText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
    letterSpacing: 1,
  },
  actionButtons: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 16,
  },
  messageButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: SpinrConfig.theme.colors.primary,
    paddingVertical: 14,
    borderRadius: 28,
    gap: 8,
  },
  messageButtonText: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#FFF',
  },
  callButton: {
    width: 52,
    height: 52,
    borderRadius: 26,
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  progressContainer: {
    height: 4,
    backgroundColor: '#F0F0F0',
    borderRadius: 2,
    marginBottom: 20,
  },
  progressBar: {
    height: 4,
    backgroundColor: SpinrConfig.theme.colors.primary,
    borderRadius: 2,
  },
  tripDetails: {
    marginBottom: 16,
  },
  tripRow: {
    flexDirection: 'row',
  },
  tripIndicator: {
    alignItems: 'center',
    marginRight: 12,
    paddingTop: 4,
  },
  grayDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#CCC',
  },
  tripLine: {
    width: 2,
    height: 50,
    backgroundColor: '#E0E0E0',
    marginVertical: 4,
  },
  redDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: SpinrConfig.theme.colors.primary,
  },
  tripAddresses: {
    flex: 1,
  },
  addressRow: {},
  addressLabel: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#999',
    letterSpacing: 0.5,
  },
  addressText: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#999',
    textDecorationLine: 'line-through',
  },
  dropoffLabel: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: SpinrConfig.theme.colors.primary,
    letterSpacing: 0.5,
  },
  dropoffText: {
    fontSize: 17,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  etaArrival: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 2,
  },
  demoButton: {
    backgroundColor: '#F0F0F0',
    borderRadius: 12,
    padding: 12,
    alignItems: 'center',
  },
  demoButtonText: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  shareTripBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF5F5',
    padding: 14,
    borderRadius: 14,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#FFE0E0',
  },
  shareTripIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  shareTripContent: {
    flex: 1,
  },
  shareTripTitle: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  shareTripSubtitle: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 1,
  },
  driverDetailsCard: {
    backgroundColor: '#F9F9F9',
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  driverCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  driverCardTitle: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: SpinrConfig.theme.colors.primary,
    letterSpacing: 0.5,
  },
  copyButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFF',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    gap: 4,
  },
  copyText: {
    fontSize: 12,
    fontFamily: 'PlusJakartaSans_500Medium',
    color: '#666',
  },
  totalTrips: {
    fontSize: 13,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: '#666',
    marginTop: 2,
  },
  vehicleSection: {
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#E8E8E8',
  },
  vehicleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  vehicleTextContainer: {
    marginLeft: 12,
    flex: 1,
  },
  vehicleLabel: {
    fontSize: 10,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#999',
    letterSpacing: 0.5,
    marginBottom: 2,
  },
  vehicleValue: {
    fontSize: 15,
    fontFamily: 'PlusJakartaSans_600SemiBold',
    color: '#1A1A1A',
  },
  plateRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  plateIconContainer: {
    width: 20,
    height: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  plateIcon: {
    fontSize: 16,
  },
  plateValue: {
    fontSize: 18,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    letterSpacing: 2,
  },
  // Waiting-for-driver-to-accept placeholder (shown before status=driver_accepted)
  waitingDriverCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
    backgroundColor: '#FFF',
    borderRadius: 20,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#EEE',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.04,
    shadowRadius: 8,
    elevation: 2,
  },
  waitingDriverTextContainer: {
    flex: 1,
  },
  waitingDriverTitle: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
    marginBottom: 4,
  },
  waitingDriverSubtitle: {
    fontSize: 13,
    color: '#666',
    lineHeight: 18,
  },
  // Pickup-PIN display (shown after status=driver_accepted so rider can
  // prepare to share the code when the driver arrives)
  pinCard: {
    backgroundColor: '#FFF',
    borderRadius: 20,
    padding: 20,
    marginBottom: 16,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#EEE',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.04,
    shadowRadius: 8,
    elevation: 2,
  },
  pinLabel: {
    fontSize: 11,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: SpinrConfig.theme.colors.primary,
    letterSpacing: 1.2,
    marginBottom: 14,
  },
  pinBoxes: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 12,
  },
  pinBox: {
    width: 52,
    height: 62,
    borderRadius: 14,
    backgroundColor: '#F8F9FA',
    borderWidth: 1.5,
    borderColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  pinDigit: {
    fontSize: 28,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#1A1A1A',
  },
  pinHint: {
    fontSize: 12,
    color: '#888',
    textAlign: 'center',
  },
  shareButton: {
    width: 52,
    height: 52,
    borderRadius: 26,
    borderWidth: 1.5,
    borderColor: '#E0E0E0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  devBar: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 16,
    padding: 12,
    backgroundColor: '#FEF3C7',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#F59E0B',
  },
  devLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: '#92400E',
    marginRight: 4,
  },
  devBtn: {
    backgroundColor: '#F59E0B',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
  },
  devBtnText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#FFF',
  },
});
