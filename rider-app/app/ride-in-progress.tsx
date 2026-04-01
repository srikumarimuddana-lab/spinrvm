import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Dimensions,
  Share,
  Alert,
  Linking,
  Platform,
} from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import BottomSheet, { BottomSheetScrollView } from '@gorhom/bottom-sheet';
import { useRideStore } from '../store/rideStore';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import { CarMarker } from '@shared/components/CarMarker';

const { width } = Dimensions.get('window');

export default function RideInProgressScreen() {
  const router = useRouter();
  const { rideId } = useLocalSearchParams<{ rideId: string }>();
  const { currentRide, currentDriver, fetchRide, cancelRide, clearRide, triggerEmergency } = useRideStore();
  const [eta, setEta] = useState(15);
  const [estimatedTime, setEstimatedTime] = useState('12:45 PM');
  const [currentLocation, setCurrentLocation] = useState('4th Avenue North');
  const [isSharingLocation, setIsSharingLocation] = useState(false);
  const [tripRouteCoords, setTripRouteCoords] = useState<any[]>([]);
  const mapRef = React.useRef<MapView>(null);
  const bottomSheetRef = React.useRef<BottomSheet>(null);

  const snapPoints = React.useMemo(() => ['30%', '50%', '85%'], []);

  useEffect(() => {
    if (currentRide && mapRef.current) {
        if (currentDriver?.lat && currentDriver?.lng) {
          mapRef.current.fitToCoordinates(
            [
              { latitude: currentDriver.lat, longitude: currentDriver.lng },
              { latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }
            ],
            {
              edgePadding: { top: 50, right: 50, bottom: 250, left: 50 },
              animated: true,
            }
          );
        } else {
             mapRef.current.animateToRegion({
                latitude: currentRide.pickup_lat || currentRide.dropoff_lat,
                longitude: currentRide.pickup_lng || currentRide.dropoff_lng,
                latitudeDelta: 0.05,
                longitudeDelta: 0.05,
             });
        }
    }
  }, [currentRide?.dropoff_lat, currentRide?.dropoff_lng, currentDriver?.lat, currentDriver?.lng]);

  useEffect(() => {
    if (rideId) {
      fetchRide(rideId);
      // Poll every 5 seconds for driver position + ride status updates
      const interval = setInterval(() => fetchRide(rideId), 5000);
      return () => clearInterval(interval);
    }
  }, [rideId]);

  useEffect(() => {
    // Calculate estimated arrival time
    const now = new Date();
    now.setMinutes(now.getMinutes() + eta);
    setEstimatedTime(now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }));
  }, [eta]);

  useEffect(() => {
    if (currentRide?.status === 'completed') {
      router.replace({ pathname: '/ride-completed', params: { rideId } });
    }
  }, [currentRide?.status]);

  const handleSafety = () => {
    Alert.alert(
      'Emergency',
      'Are you sure you want to contact emergency services?',
      [
        { text: 'Cancel', style: 'cancel' },
        { 
          text: 'Call 911', 
          style: 'destructive',
          onPress: () => {
            if (rideId) triggerEmergency(rideId as string);
            Linking.openURL('tel:911');
          }
        }
      ]
    );
  };

  const handleShareTrip = async () => {
    const liveTrackingUrl = `https://spinr-track.app/${rideId || 'demo'}`;
    const tripDetails = `
🚗 TRACK MY SPINR RIDE - LIVE LOCATION

👤 DRIVER: ${currentDriver?.name || 'Unknown'}
⭐ RATING: ${currentDriver?.rating || 'New'}

🚙 VEHICLE: ${currentDriver?.vehicle_color || ''} ${currentDriver?.vehicle_make || 'Unknown'} ${currentDriver?.vehicle_model || 'Vehicle'}
📋 LICENSE PLATE: ${currentDriver?.license_plate || 'Pending'}

📍 CURRENT LOCATION: ${currentLocation}
📍 HEADING TO: ${currentRide?.dropoff_address || '1055 Canada Place'}

⏱️ ESTIMATED ARRIVAL: ${estimatedTime} (${eta} min left)

🔴 LIVE TRACKING LINK:
${liveTrackingUrl}

I've shared my live location with you for safety.
`.trim();

    try {
      await Share.share({
        message: tripDetails,
        title: 'Track My Spinr Ride',
      });
      setIsSharingLocation(true);
      Alert.alert(
        'Trip Shared!',
        'Your live location is now being shared. They can track your journey in real-time.'
      );
    } catch (error) {
      console.log(error);
    }
  };

  const handleCopyTrackingLink = async () => {
    const trackingLink = `https://spinr-track.app/${rideId || 'demo'}`;
    await Clipboard.setStringAsync(trackingLink);
    Alert.alert('Copied!', 'Live tracking link copied to clipboard');
  };

  // No free cancel during ride — rider pays full fare if they end early

  const handleLocation = () => {
    // Center on current location
  };

  const progressPercent = ((15 - eta) / 15) * 100;

  return (
    <View style={styles.container}>
      {/* Header Status */}
      <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
        <View style={styles.statusPill}>
          <View style={styles.greenDot} />
          <Text style={styles.statusText}>Ride Started - Enjoy your trip</Text>
        </View>
      </SafeAreaView>

      {/* Map Area */}
      <View style={styles.mapContainer}>
        {currentRide ? (
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
            showsUserLocation
            showsMyLocationButton={false}
          >
            {/* Route: pickup → dropoff (always show, even without driver coords) */}
            {process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY && (
              <MapViewDirections
                origin={
                  currentDriver?.lat && currentDriver?.lng
                    ? { latitude: currentDriver.lat, longitude: currentDriver.lng }
                    : { latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }
                }
                destination={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
                apikey={process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY}
                strokeWidth={0}
                strokeColor="transparent"
                onReady={(result: any) => {
                  setEta(Math.ceil(result.duration));
                  setTripRouteCoords(result.coordinates);
                  // Fit map to route
                  if (mapRef.current && result.coordinates?.length > 1) {
                    mapRef.current.fitToCoordinates(result.coordinates, {
                      edgePadding: { top: 80, right: 50, bottom: 280, left: 50 },
                      animated: true,
                    });
                  }
                }}
              />
            )}

            {/* Orange → Red gradient route */}
            {tripRouteCoords.length > 1 && (() => {
              const total = tripRouteCoords.length;
              const SEGS = 20;
              const chunk = Math.max(1, Math.floor(total / SEGS));
              const segments: { coords: any[]; color: string }[] = [];
              for (let i = 0; i < total - 1; i += chunk) {
                const end = Math.min(i + chunk + 1, total);
                const t = i / Math.max(total - 1, 1);
                const r = Math.round(255 + (238 - 255) * t);
                const g = Math.round(149 + (43 - 149) * t);
                const b = Math.round(0 + (43 - 0) * t);
                segments.push({ coords: tripRouteCoords.slice(i, end), color: `rgb(${r},${g},${b})` });
              }
              return segments.map((seg, idx) => (
                <Polyline
                  key={`trip-seg-${idx}`}
                  coordinates={seg.coords}
                  strokeWidth={5}
                  strokeColor={seg.color}
                  lineCap="round"
                  lineJoin="round"
                />
              ));
            })()}

            {/* Pickup Marker (green) */}
            <Marker
              coordinate={{ latitude: currentRide.pickup_lat, longitude: currentRide.pickup_lng }}
              anchor={{ x: 0.5, y: 0.5 }}
            >
              <View style={styles.pickupMarker}>
                <Ionicons name="location" size={16} color="#FFF" />
              </View>
            </Marker>

            {/* Destination Marker (red) */}
            <Marker
              coordinate={{ latitude: currentRide.dropoff_lat, longitude: currentRide.dropoff_lng }}
              anchor={{ x: 0.5, y: 0.5 }}
            >
              <View style={styles.dropoffMarker}>
                <Ionicons name="flag" size={16} color="#FFF" />
              </View>
            </Marker>

            {/* Driver Car Marker */}
            {currentDriver?.lat && currentDriver?.lng && (
              <CarMarker
                coordinate={{ latitude: currentDriver.lat, longitude: currentDriver.lng }}
                heading={(currentDriver as any).heading}
                size={6}
                zIndex={100}
              />
            )}
          </MapView>
        ) : (
          <View style={styles.mapPlaceholder}>
             <Text>Loading Map...</Text>
          </View>
        )}

        {/* Location button */}
        <TouchableOpacity style={styles.locationButton} onPress={handleLocation}>
          <Ionicons name="navigate" size={22} color="#1A1A1A" />
        </TouchableOpacity>
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
        <BottomSheetScrollView contentContainerStyle={styles.bottomSheetContent}>
          <View>
            {/* ETA Hero */}
            <View style={styles.etaHero}>
              <View style={{ flex: 1 }}>
                <Text style={styles.etaLabel}>ARRIVING AT</Text>
                <Text style={styles.etaTime}>{estimatedTime}</Text>
              </View>
              <View style={styles.etaBadge}>
                <Text style={styles.etaBadgeNum}>{eta}</Text>
                <Text style={styles.etaBadgeUnit}>min</Text>
              </View>
            </View>

            {/* Progress Bar */}
            <View style={styles.progressContainer}>
              <View style={[styles.progressBar, { width: `${Math.min(progressPercent, 100)}%` }]} />
            </View>

            {/* Driver Card */}
            <View style={styles.driverCard}>
              <View style={styles.driverRow}>
                <View style={styles.driverAvatar}>
                  <Ionicons name="person" size={26} color="#666" />
                  {currentDriver?.rating && (
                    <View style={styles.ratingPill}>
                      <Ionicons name="star" size={9} color="#FFB800" />
                      <Text style={styles.ratingPillText}>{currentDriver.rating}</Text>
                    </View>
                  )}
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.driverName}>{currentDriver?.name || 'Your Driver'}</Text>
                  <Text style={styles.driverMeta}>{currentDriver?.total_rides || 0} trips completed</Text>
                </View>
                <TouchableOpacity
                  style={styles.msgIconBtn}
                  onPress={() => router.push({ pathname: '/chat-driver', params: { rideId } } as any)}
                >
                  <Ionicons name="chatbubble" size={20} color={SpinrConfig.theme.colors.primary} />
                </TouchableOpacity>
              </View>

              {/* Vehicle Info */}
              <View style={styles.vehicleBar}>
                <Ionicons name="car" size={16} color={SpinrConfig.theme.colors.primary} />
                <Text style={styles.vehicleDetail}>
                  {currentDriver?.vehicle_color} {currentDriver?.vehicle_make} {currentDriver?.vehicle_model}
                </Text>
                <View style={styles.plateBadge}>
                  <Text style={styles.plateNum}>{currentDriver?.license_plate || 'N/A'}</Text>
                </View>
              </View>
            </View>

            {/* Trip Route Card */}
            <View style={styles.tripCard}>
              <View style={styles.tripRow}>
                <View style={styles.tripDots}>
                  <View style={[styles.tripDot, { backgroundColor: '#10B981' }]} />
                  <View style={styles.tripConnector} />
                  <View style={[styles.tripDot, { backgroundColor: SpinrConfig.theme.colors.primary }]} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.tripRouteLabel}>PICKUP</Text>
                  <Text style={styles.tripRouteAddr} numberOfLines={1}>{currentRide?.pickup_address || 'Pickup'}</Text>
                  <View style={{ height: 20 }} />
                  <Text style={styles.tripRouteLabel}>DESTINATION</Text>
                  <Text style={styles.tripRouteAddr} numberOfLines={1}>{currentRide?.dropoff_address || 'Destination'}</Text>
                </View>
              </View>

              {/* Fare + Distance */}
              <View style={styles.fareRow}>
                <View style={styles.fareItem}>
                  <Ionicons name="cash-outline" size={16} color="#666" />
                  <Text style={styles.fareValue}>${(currentRide?.total_fare || 0).toFixed(2)}</Text>
                  <Text style={styles.fareLabel}>Fare</Text>
                </View>
                <View style={styles.fareDivider} />
                <View style={styles.fareItem}>
                  <Ionicons name="speedometer-outline" size={16} color="#666" />
                  <Text style={styles.fareValue}>{(currentRide?.distance_km || 0).toFixed(1)} km</Text>
                  <Text style={styles.fareLabel}>Distance</Text>
                </View>
                <View style={styles.fareDivider} />
                <View style={styles.fareItem}>
                  <Ionicons name="time-outline" size={16} color="#666" />
                  <Text style={styles.fareValue}>{eta} min</Text>
                  <Text style={styles.fareLabel}>ETA</Text>
                </View>
              </View>
            </View>

            {/* Live Sharing */}
            {isSharingLocation && (
              <View style={styles.liveSharingBanner}>
                <View style={styles.liveIndicator}>
                  <View style={styles.liveDot} />
                  <Text style={styles.liveText}>LIVE</Text>
                </View>
                <Text style={styles.sharingText}>Location sharing active</Text>
                <TouchableOpacity onPress={handleCopyTrackingLink}>
                  <Ionicons name="copy-outline" size={18} color="#666" />
                </TouchableOpacity>
              </View>
            )}

            {/* Action Row */}
            <View style={styles.actionRow}>
              <TouchableOpacity style={styles.actionBtn} onPress={handleShareTrip}>
                <Ionicons name="share-outline" size={20} color="#1A1A1A" />
                <Text style={styles.actionBtnText}>Share Trip</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.actionBtn} onPress={handleSafety}>
                <Ionicons name="shield-checkmark" size={20} color={SpinrConfig.theme.colors.primary} />
                <Text style={styles.actionBtnText}>Safety</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.actionBtn} onPress={() => {
                Alert.alert(
                  'End ride early?',
                  `You will be charged the full agreed fare of $${(currentRide?.total_fare || 0).toFixed(2)}. This cannot be undone.`,
                  [
                    { text: 'Continue Ride', style: 'cancel' },
                    {
                      text: 'End & Pay Full Fare', style: 'destructive',
                      onPress: async () => {
                        try { await api.post(`/drivers/rides/${currentRide?.id}/complete`); }
                        catch(e) { console.log(e); }
                        if (rideId) fetchRide(rideId);
                      },
                    },
                  ]
                );
              }}>
                <Ionicons name="stop-circle-outline" size={20} color="#999" />
                <Text style={styles.actionBtnText}>End Ride</Text>
              </TouchableOpacity>
            </View>

            {/* DEV CONTROLS */}
            {__DEV__ && currentRide && (
              <View style={styles.devBar}>
                <Text style={styles.devLabel}>DEV: {currentRide.status}</Text>
                <TouchableOpacity style={styles.devBtn} onPress={async () => {
                  try { await api.post(`/drivers/rides/${currentRide.id}/complete`); }
                  catch(e) { console.log('dev complete:', e); }
                  if (rideId) fetchRide(rideId);
                }}>
                  <Text style={styles.devBtnText}>Complete Ride</Text>
                </TouchableOpacity>
              </View>
            )}
          </View>
        </BottomSheetScrollView>
      </BottomSheet>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#E8E8E8' },
  headerSafeArea: {
    position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10,
    alignItems: 'center', paddingTop: 8,
  },
  statusPill: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#FFF', paddingHorizontal: 20, paddingVertical: 12, borderRadius: 24,
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.1, shadowRadius: 4, elevation: 3,
  },
  greenDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: '#10B981', marginRight: 10 },
  statusText: { fontSize: 15, fontWeight: '600', color: '#1A1A1A' },
  mapContainer: { flex: 1, position: 'relative' },
  map: { ...StyleSheet.absoluteFillObject },
  mapPlaceholder: { flex: 1, backgroundColor: '#F0F0F0', justifyContent: 'center', alignItems: 'center' },
  locationButton: {
    position: 'absolute', right: 16, bottom: 16,
    width: 48, height: 48, borderRadius: 24, backgroundColor: '#FFF',
    justifyContent: 'center', alignItems: 'center',
    elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.15, shadowRadius: 4,
  },
  bottomSheetBackground: { backgroundColor: '#FFF', borderTopLeftRadius: 24, borderTopRightRadius: 24 },
  sheetHandleIndicator: { width: 40, backgroundColor: '#DDD' },
  bottomSheetContent: { paddingHorizontal: 20, paddingTop: 4, paddingBottom: 40 },

  // ETA Hero
  etaHero: {
    flexDirection: 'row', alignItems: 'center', marginBottom: 12,
  },
  etaLabel: { fontSize: 11, fontWeight: '600', color: '#999', letterSpacing: 0.5, marginBottom: 2 },
  etaTime: { fontSize: 28, fontWeight: '800', color: '#1A1A1A' },
  etaBadge: {
    width: 56, height: 56, borderRadius: 28,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center', alignItems: 'center',
  },
  etaBadgeNum: { fontSize: 20, fontWeight: '800', color: '#FFF', lineHeight: 22 },
  etaBadgeUnit: { fontSize: 10, fontWeight: '600', color: 'rgba(255,255,255,0.8)', marginTop: -2 },

  // Progress
  progressContainer: { height: 4, backgroundColor: '#F0F0F0', borderRadius: 2, marginBottom: 16 },
  progressBar: { height: 4, backgroundColor: SpinrConfig.theme.colors.primary, borderRadius: 2 },

  // Driver Card
  driverCard: { backgroundColor: '#F9F9F9', borderRadius: 16, padding: 16, marginBottom: 14 },
  driverRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 12 },
  driverAvatar: {
    width: 48, height: 48, borderRadius: 24, backgroundColor: '#E8E8E8',
    justifyContent: 'center', alignItems: 'center', marginRight: 12, position: 'relative',
  },
  ratingPill: {
    position: 'absolute', bottom: -4, left: -2,
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#FFF', paddingHorizontal: 5, paddingVertical: 2, borderRadius: 8,
    shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.1, shadowRadius: 2,
  },
  ratingPillText: { fontSize: 10, fontWeight: '700', color: '#1A1A1A', marginLeft: 2 },
  driverName: { fontSize: 16, fontWeight: '700', color: '#1A1A1A' },
  driverMeta: { fontSize: 12, color: '#999', marginTop: 2 },
  msgIconBtn: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: `${SpinrConfig.theme.colors.primary}15`,
    justifyContent: 'center', alignItems: 'center',
  },
  vehicleBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingTop: 12, borderTopWidth: 1, borderTopColor: '#ECECEC',
  },
  vehicleDetail: { flex: 1, fontSize: 13, fontWeight: '500', color: '#444' },
  plateBadge: {
    backgroundColor: '#1A1A1A', borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4,
  },
  plateNum: { fontSize: 13, fontWeight: '800', color: '#FFF', letterSpacing: 1.5 },

  // Trip Card
  tripCard: { backgroundColor: '#F9F9F9', borderRadius: 16, padding: 16, marginBottom: 14 },
  tripRow: { flexDirection: 'row' },
  tripDots: { alignItems: 'center', marginRight: 12, paddingTop: 2 },
  tripDot: { width: 10, height: 10, borderRadius: 5 },
  tripConnector: { width: 2, flex: 1, backgroundColor: '#DDD', marginVertical: 4 },
  tripRouteLabel: { fontSize: 10, fontWeight: '600', color: '#999', letterSpacing: 0.5, marginBottom: 2 },
  tripRouteAddr: { fontSize: 14, fontWeight: '500', color: '#1A1A1A' },
  fareRow: {
    flexDirection: 'row', marginTop: 14, paddingTop: 14,
    borderTopWidth: 1, borderTopColor: '#ECECEC',
  },
  fareItem: { flex: 1, alignItems: 'center' },
  fareValue: { fontSize: 15, fontWeight: '700', color: '#1A1A1A', marginTop: 4 },
  fareLabel: { fontSize: 10, color: '#999', marginTop: 2 },
  fareDivider: { width: 1, backgroundColor: '#ECECEC' },

  // Live Sharing
  liveSharingBanner: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#F0FFF4', paddingHorizontal: 14, paddingVertical: 10,
    borderRadius: 12, marginBottom: 14, borderWidth: 1, borderColor: '#D1FAE5',
  },
  liveIndicator: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#EF4444', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6, marginRight: 10,
  },
  liveDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#FFF', marginRight: 4 },
  liveText: { fontSize: 10, fontWeight: '700', color: '#FFF', letterSpacing: 0.5 },
  sharingText: { flex: 1, fontSize: 14, fontWeight: '500', color: '#059669' },

  // Action Row
  actionRow: { flexDirection: 'row', gap: 10, marginBottom: 8 },
  actionBtn: {
    flex: 1, alignItems: 'center', justifyContent: 'center', gap: 4,
    backgroundColor: '#F5F5F5', paddingVertical: 14, borderRadius: 14,
  },
  actionBtnDanger: { backgroundColor: '#FEF2F2' },
  actionBtnText: { fontSize: 12, fontWeight: '600', color: '#1A1A1A' },

  // Markers
  pickupMarker: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: '#10B981', justifyContent: 'center', alignItems: 'center',
    borderWidth: 2, borderColor: '#FFF',
    elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.2, shadowRadius: 3,
  },
  dropoffMarker: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: SpinrConfig.theme.colors.primary, justifyContent: 'center', alignItems: 'center',
    borderWidth: 2, borderColor: '#FFF',
    elevation: 4, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.2, shadowRadius: 3,
  },

  // Dev
  devBar: {
    flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8,
    marginTop: 16, padding: 12, backgroundColor: '#FEF3C7', borderRadius: 12,
    borderWidth: 1, borderColor: '#F59E0B',
  },
  devLabel: { fontSize: 11, fontWeight: '700', color: '#92400E', marginRight: 4 },
  devBtn: { backgroundColor: '#F59E0B', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
  devBtnText: { fontSize: 12, fontWeight: '700', color: '#FFF' },
});
