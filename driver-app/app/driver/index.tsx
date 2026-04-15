import React, { useState, useEffect, useRef, useMemo } from 'react';
import { View, Text, StyleSheet, Alert, Platform, Linking, Animated, TouchableOpacity, ActivityIndicator } from 'react-native';
import MapView, { Marker, Polyline, Heatmap, PROVIDER_GOOGLE } from 'react-native-maps';
import MapViewDirections from 'react-native-maps-directions';
import { Ionicons } from '@expo/vector-icons';
import { useDriverStore } from '../../store/driverStore';
import { useAuthStore } from '@shared/store/authStore';
import {
  DriverTopBar,
  DriverIdlePanel,
  ActiveRidePanel,
  TripCompletedPanel,
  MapControls,
} from '../../components/dashboard';
import { useDriverDashboard } from '../../hooks/useDriverDashboard';
import { CarMarker } from '../../components/CarMarker';
import { SOSButton } from '@shared/components/SOSButton';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { useLanguageStore } from '../../store/languageStore';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const GOOGLE_MAPS_API_KEY = process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY || '';

// Use Google Maps on Android, Apple Maps (native) on iOS
const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

export default function DriverDashboard() {
  const { colors, isDark } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // Driver + user live on the shared auth store — useDriverStore does not
  // hold these fields, so reading them from there always returned null and
  // left the GO button permanently disabled.
  const driverData = useAuthStore(s => s.driver);
  const user = useAuthStore(s => s.user);
  const {
    rideState,
    incomingRide,
    activeRide,
    completedRide,
    countdownSeconds,
    configuredCountdownSeconds,
    setCountdown,
    acceptRide,
    declineRide,
    arriveAtPickup,
    verifyOTP,
    startRide,
    completeRide,
    cancelRide,
    resetRideState,
    clearError,
    earnings,
    rateRider,
  } = useDriverStore();

  const { t } = useLanguageStore();

  const {
    isOnline,
    connectionState,
    location,
    otpInput,
    setOtpInput,
    toggleOnline,
    openNavigation,
    mapRef,
    currentRegionRef,
    pulseAnim,
    slideUpAnim,
    fadeAnim,
  } = useDriverDashboard();

  // Route polyline coordinates for active rides
  const [routeCoords, setRouteCoords] = useState<{ latitude: number; longitude: number }[]>([]);

  // Live ETA from Google Directions — updated every 30s via the
  // directionsKey mechanism below.
  const [routeEtaMinutes, setRouteEtaMinutes] = useState<number | null>(null);
  const [routeDistanceKm, setRouteDistanceKm] = useState<number | null>(null);

  // Force MapViewDirections to re-compute every 30 seconds by changing
  // a key prop. This gives the driver a road-aware ETA that accounts
  // for traffic and route changes without hammering the Directions API.
  const [directionsKey, setDirectionsKey] = useState(0);
  useEffect(() => {
    if (rideState !== 'navigating_to_pickup' && rideState !== 'trip_in_progress') return;
    const interval = setInterval(() => setDirectionsKey((k) => k + 1), 30000);
    return () => clearInterval(interval);
  }, [rideState]);

  // Demand heatmap — controlled by admin per service area
  const [heatmapPoints, setHeatmapPoints] = useState<{ latitude: number; longitude: number; weight: number }[]>([]);

  // Fetch heatmap data when idle (backend returns empty if admin disabled it)
  useEffect(() => {
    if (rideState !== 'idle') {
      setHeatmapPoints([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get('/drivers/demand-heatmap');
        if (cancelled) return;
        if (!res.data.enabled) {
          setHeatmapPoints([]);
          return;
        }
        const pts = (res.data.points || []).map((p: number[]) => ({
          latitude: p[0],
          longitude: p[1],
          weight: p[2] || 1,
        }));
        setHeatmapPoints(pts);
      } catch (e) {
        console.log('Heatmap fetch error:', e);
      }
    })();
    return () => { cancelled = true; };
  }, [rideState]);

  const [countdown, setCountdownState] = useState(countdownSeconds);
  // Countdown timer effect
  useEffect(() => {
    if (rideState === 'ride_offered' && countdown > 0) {
      const interval = setInterval(() => {
        setCountdownState((prev) => {
          if (prev <= 1) {
            clearInterval(interval);
            setCountdown(0);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [rideState]);

  // Sync with store countdown
  useEffect(() => {
    setCountdownState(countdownSeconds);
  }, [countdownSeconds]);

  // Clear route + ETA when ride state changes (new phase = new route)
  useEffect(() => {
    setRouteCoords([]);
    setRouteEtaMinutes(null);
    setRouteDistanceKm(null);
    setDirectionsKey(0);
  }, [rideState]);

  // Error handling
  useEffect(() => {
    const { error } = useDriverStore.getState();
    if (error) {
      Alert.alert('Error', error, [{ text: 'OK', onPress: clearError }]);
    }
  }, [useDriverStore.getState().error]);

  // Map markers
  const getMapMarkers = () => {
    const markers: any[] = [];
    const ride = activeRide?.ride || incomingRide;
    if (!ride) return markers;

    if (ride.pickup_lat && ride.pickup_lng) {
      markers.push(
        <Marker
          key="pickup"
          coordinate={{ latitude: ride.pickup_lat, longitude: ride.pickup_lng }}
          title="Pickup"
          description={ride.pickup_address}
        >
          <View style={styles.markerContainer}>
            <View style={[styles.markerDot, { backgroundColor: colors.primary }]}>
              <Ionicons name="location" size={16} color="#fff" />
            </View>
          </View>
        </Marker>
      );
    }

    if (ride.dropoff_lat && ride.dropoff_lng) {
      markers.push(
        <Marker
          key="dropoff"
          coordinate={{ latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }}
          title="Dropoff"
          description={ride.dropoff_address}
        >
          <View style={styles.markerContainer}>
            <View style={[styles.markerDot, { backgroundColor: '#FF4757' }]}>
              <Ionicons name="flag" size={16} color="#fff" />
            </View>
          </View>
        </Marker>
      );
    }

    return markers;
  };

  // Ride Offer Panel
  const renderRideOfferPanel = () => {
    if (!incomingRide) return null;
    // Timer-bar progress tracks remaining countdown as a fraction of the
    // configured max. Previously this was `/ 15` hardcoded, so bumping
    // ride_offer_timeout_seconds in backend settings would have left
    // the visual bar stuck past 100%.
    const maxCountdown = configuredCountdownSeconds || 15;
    const progress = Math.max(0, Math.min(1, countdown / maxCountdown));
    const fare = (incomingRide.fare || 0).toFixed(2);

    return (
      <View style={styles.rideOfferOverlay}>
        <View style={styles.rideOfferContent}>
          {/* Countdown timer bar at top */}
          <View style={styles.timerBarContainer}>
            <View style={[styles.timerBar, { width: `${progress * 100}%` }]} />
          </View>

          {/* Header: Countdown + Title */}
          <View style={styles.offerHeader}>
            <View style={styles.countdownCircle}>
              <Text style={styles.countdownText}>{countdown}</Text>
            </View>
            <View style={{ flex: 1, marginLeft: 14 }}>
              <Text style={styles.rideOfferTitle}>{t('rideOffer.newRideRequest')}</Text>
              <View style={styles.rideTypeBadge}>
                <Ionicons name="car-sport" size={12} color={colors.primary} />
                <Text style={styles.rideTypeText}>{t('rideOffer.standardRide')}</Text>
              </View>
            </View>
            <View style={styles.fareContainer}>
              <Text style={styles.fareLabel}>{t('rideOffer.fare')}</Text>
              <Text style={styles.fareAmount}>${fare}</Text>
            </View>
          </View>

          {/* Route: Pickup & Dropoff */}
          <View style={styles.routeContainer}>
            <View style={styles.routeIconColumn}>
              <View style={[styles.routeDot, { backgroundColor: colors.primary }]} />
              <View style={styles.routeLine} />
              <View style={[styles.routeDot, { backgroundColor: '#FF4757' }]} />
            </View>
            <View style={styles.routeDetails}>
              <View style={styles.routeRow}>
                <Text style={styles.routeLabel}>{t('rideOffer.pickup')}</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>
                  {incomingRide.pickup_address || t('rideOffer.pickupLocation')}
                </Text>
              </View>
              <View style={styles.routeDivider} />
              <View style={styles.routeRow}>
                <Text style={styles.routeLabel}>{t('rideOffer.dropoff')}</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>
                  {incomingRide.dropoff_address || t('rideOffer.dropoffLocation')}
                </Text>
              </View>
            </View>
          </View>

          {/* Trip info badges */}
          <View style={styles.tripInfoRow}>
            {incomingRide.distance_km && (
              <View style={styles.tripInfoBadge}>
                <Ionicons name="navigate-outline" size={14} color={colors.primary} />
                <Text style={styles.tripInfoText}>{incomingRide.distance_km.toFixed(1)} km</Text>
              </View>
            )}
            {incomingRide.duration_minutes && (
              <View style={styles.tripInfoBadge}>
                <Ionicons name="time-outline" size={14} color={colors.primary} />
                <Text style={styles.tripInfoText}>{Math.round(incomingRide.duration_minutes)} min</Text>
              </View>
            )}
            {incomingRide.rider_name && (
              <View style={styles.tripInfoBadge}>
                <Ionicons name="person-outline" size={14} color={colors.primary} />
                <Text style={styles.tripInfoText}>{incomingRide.rider_name}</Text>
                {incomingRide.rider_rating && (
                  <>
                    <Ionicons name="star" size={12} color="#FFD700" />
                    <Text style={styles.tripInfoText}>{incomingRide.rider_rating.toFixed(1)}</Text>
                  </>
                )}
              </View>
            )}
          </View>

          {/* Accept / Decline buttons */}
          <View style={styles.offerActions}>
            <TouchableOpacity
              style={styles.declineBtn}
              onPress={() => declineRide(incomingRide.ride_id)}
              activeOpacity={0.8}
            >
              <Ionicons name="close-circle" size={24} color="#FF4757" />
              <Text style={styles.declineText}>{t('rideOffer.decline')}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.acceptBtn}
              onPress={() => acceptRide(incomingRide.ride_id)}
              activeOpacity={0.8}
            >
              <Ionicons name="checkmark-circle" size={24} color="#fff" />
              <Text style={styles.acceptText}>{t('rideOffer.acceptRide')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    );
  };

  if (!location?.coords) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={{ color: colors.text, marginTop: 12, fontSize: 15 }}>{t('home.gettingLocation')}</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Offline indicator — slides in from the top when network drops */}
      <OfflineBanner />

      {/* Map */}
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={MAP_PROVIDER}
        userInterfaceStyle={isDark ? "dark" : "light"}
        initialRegion={{
          latitude: location.coords.latitude,
          longitude: location.coords.longitude,
          latitudeDelta: 0.04,
          longitudeDelta: 0.04,
        }}
        showsUserLocation={false}
        showsMyLocationButton={false}
        onRegionChange={(region) => {
          currentRegionRef.current = {
            latitudeDelta: region.latitudeDelta,
            longitudeDelta: region.longitudeDelta,
          };
        }}
      >
        {/* Driver car marker */}
        {location?.coords && (
          <CarMarker
            coordinate={{
              latitude: location.coords.latitude,
              longitude: location.coords.longitude,
            }}
            heading={location.coords.heading}
            isOnline={isOnline}
          />
        )}
        {getMapMarkers()}

        {/* Route polyline during active rides */}
        {GOOGLE_MAPS_API_KEY && activeRide?.ride && (rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup' || rideState === 'trip_in_progress') && (() => {
          const ride = activeRide.ride;
          // During pickup phase: driver → pickup
          // During trip phase: pickup → dropoff (or driver → dropoff)
          const origin = rideState === 'trip_in_progress' && location?.coords
            ? { latitude: location.coords.latitude, longitude: location.coords.longitude }
            : location?.coords
              ? { latitude: location.coords.latitude, longitude: location.coords.longitude }
              : { latitude: ride.pickup_lat, longitude: ride.pickup_lng };

          const destination = rideState === 'trip_in_progress'
            ? { latitude: ride.dropoff_lat, longitude: ride.dropoff_lng }
            : { latitude: ride.pickup_lat, longitude: ride.pickup_lng };

          return (
            <>
              <MapViewDirections
                key={directionsKey}
                origin={origin}
                destination={destination}
                apikey={GOOGLE_MAPS_API_KEY}
                strokeWidth={0}
                strokeColor="transparent"
                onReady={(result) => {
                  setRouteCoords(result.coordinates);
                  // Capture live ETA + road-distance from the Directions API.
                  // result.duration is in minutes, result.distance in km.
                  if (result.duration != null) setRouteEtaMinutes(Math.round(result.duration));
                  if (result.distance != null) setRouteDistanceKm(Math.round(result.distance * 10) / 10);
                  // Only fit-to-coordinates on the first computation (key=0)
                  // to avoid the map jumping every 30s.
                  if (directionsKey === 0 && mapRef.current && result.coordinates?.length > 1) {
                    mapRef.current.fitToCoordinates(result.coordinates, {
                      edgePadding: { top: 100, right: 60, bottom: 300, left: 60 },
                      animated: true,
                    });
                  }
                }}
                onError={(err) => console.log('Directions error:', err)}
              />
              {routeCoords.length > 1 && (
                <>
                  {/* Shadow line */}
                  <Polyline
                    coordinates={routeCoords}
                    strokeWidth={7}
                    strokeColor="rgba(0, 0, 0, 0.08)"
                    lineCap="round"
                    lineJoin="round"
                  />
                  {/* Main route line */}
                  <Polyline
                    coordinates={routeCoords}
                    strokeWidth={4}
                    strokeColor={rideState === 'trip_in_progress' ? '#10B981' : colors.primary}
                    lineCap="round"
                    lineJoin="round"
                  />
                </>
              )}
            </>
          );
        })()}

        {/* Demand heatmap overlay — admin-controlled per service area */}
        {heatmapPoints.length > 0 && Platform.OS !== 'web' && (
          <Heatmap
            points={heatmapPoints}
            radius={35}
            opacity={0.65}
            gradient={{
              colors: ['#00D4AA', '#FFD700', '#FF6B35', '#FF2D2D'],
              startPoints: [0.1, 0.4, 0.65, 0.9],
              colorMapSize: 256,
            }}
          />
        )}
      </MapView>

      {/* Top Bar */}
      <DriverTopBar driverData={driverData} user={user} isOnline={isOnline} connectionState={connectionState} />

      {/* SOS Button — visible during active ride */}
      {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup' || rideState === 'trip_in_progress') && activeRide?.ride?.id && (
        <View style={{ position: 'absolute', top: Platform.OS === 'ios' ? 100 : 80, right: 16, zIndex: 50 }}>
          <SOSButton
            rideId={activeRide.ride.id}
            onTrigger={async (rideId, lat, lng) => {
              try { await api.post(`/rides/${rideId}/emergency`, { latitude: lat, longitude: lng }); } catch {}
            }}
          />
        </View>
      )}

      {/* Map Controls */}
      <MapControls
        mapRef={mapRef}
        location={location}
        currentRegionRef={currentRegionRef}
      />

      {/* Bottom Panels */}
      {rideState === 'idle' && (
        <DriverIdlePanel
          isOnline={isOnline}
          driverData={driverData}
          earnings={earnings ?? undefined}
          onToggleOnline={toggleOnline}
          pulseAnim={pulseAnim}
        />
      )}
      {rideState === 'ride_offered' && renderRideOfferPanel()}
      {(rideState === 'navigating_to_pickup' ||
        rideState === 'arrived_at_pickup' ||
        rideState === 'trip_in_progress') && (
        <ActiveRidePanel
          rideState={rideState}
          ride={activeRide?.ride || null}
          rider={activeRide?.rider || null}
          driverLocation={location}
          isLoading={false}
          otpInput={otpInput}
          setOtpInput={setOtpInput}
          onVerifyOTP={(otp) => verifyOTP(activeRide!.ride.id, otp)}
          onNavigate={openNavigation}
          // Pass current coordinates so driverStore.arriveAtPickup can run
          // its 100m haversine geofence check. Without the coords the check
          // silently skips and drivers can mark "arrived" from anywhere.
          onArriveAtPickup={() => arriveAtPickup(
            activeRide!.ride.id,
            location?.coords.latitude,
            location?.coords.longitude,
          )}
          onStartRide={() => startRide(activeRide!.ride.id)}
          onCompleteRide={() => completeRide(activeRide!.ride.id)}
          onCancelRide={() => cancelRide(activeRide!.ride.id)}
          routeEtaMinutes={routeEtaMinutes}
          routeDistanceKm={routeDistanceKm}
          slideUpAnim={slideUpAnim}
          fadeAnim={fadeAnim}
        />
      )}
      {rideState === 'trip_completed' && (
        <TripCompletedPanel
          completedRide={completedRide}
          onDone={resetRideState}
          onRateRider={rateRider}
        />
      )}
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.background,
    },
    map: {
      ...StyleSheet.absoluteFillObject,
    },
    markerContainer: {
      alignItems: 'center',
    },
    markerDot: {
      width: 32,
      height: 32,
      borderRadius: 16,
      justifyContent: 'center',
      alignItems: 'center',
      borderWidth: 2,
      borderColor: '#fff',
    },
    // ── Rich Ride Offer Panel ──
    rideOfferOverlay: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      justifyContent: 'flex-end',
    },
    rideOfferContent: {
      backgroundColor: colors.surface,
      borderTopLeftRadius: 28,
      borderTopRightRadius: 28,
      paddingBottom: 34,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: -8 },
      shadowOpacity: 0.12,
      shadowRadius: 20,
      elevation: 20,
    },
    timerBarContainer: {
      height: 4,
      backgroundColor: colors.surfaceLight,
      borderTopLeftRadius: 28,
      borderTopRightRadius: 28,
      overflow: 'hidden',
    },
    timerBar: {
      height: '100%',
      backgroundColor: colors.primary,
      borderRadius: 4,
    },
    offerHeader: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: 20,
      paddingTop: 18,
      paddingBottom: 14,
    },
    countdownCircle: {
      width: 52,
      height: 52,
      borderRadius: 26,
      borderWidth: 3,
      borderColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: 'rgba(0,212,170,0.06)',
    },
    countdownText: {
      fontSize: 22,
      fontWeight: '800',
      color: colors.primary,
    },
    rideOfferTitle: {
      fontSize: 18,
      fontWeight: '700',
      color: colors.text,
    },
    rideTypeBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
      marginTop: 3,
    },
    rideTypeText: {
      fontSize: 12,
      color: colors.primary,
      fontWeight: '600',
    },
    fareContainer: {
      alignItems: 'flex-end',
    },
    fareLabel: {
      fontSize: 11,
      color: colors.textDim,
      fontWeight: '500',
    },
    fareAmount: {
      fontSize: 28,
      fontWeight: '800',
      color: colors.primary,
      letterSpacing: -1,
    },
    // Route display
    routeContainer: {
      flexDirection: 'row',
      marginHorizontal: 20,
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      padding: 14,
      marginBottom: 12,
    },
    routeIconColumn: {
      alignItems: 'center',
      width: 20,
      paddingTop: 4,
    },
    routeDot: {
      width: 10,
      height: 10,
      borderRadius: 5,
    },
    routeLine: {
      width: 2,
      flex: 1,
      backgroundColor: colors.border,
      marginVertical: 4,
    },
    routeDetails: {
      flex: 1,
      marginLeft: 12,
    },
    routeRow: {
      paddingVertical: 2,
    },
    routeLabel: {
      fontSize: 10,
      fontWeight: '700',
      color: colors.textDim,
      letterSpacing: 0.5,
      marginBottom: 2,
    },
    routeAddress: {
      fontSize: 14,
      fontWeight: '500',
      color: colors.text,
    },
    routeDivider: {
      height: 1,
      backgroundColor: colors.border,
      marginVertical: 8,
    },
    // Trip info badges
    tripInfoRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
      paddingHorizontal: 20,
      marginBottom: 16,
    },
    tripInfoBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      backgroundColor: '#F0FDF9',
      paddingHorizontal: 10,
      paddingVertical: 6,
      borderRadius: 10,
      borderWidth: 1,
      borderColor: '#D1FAE5',
    },
    tripInfoText: {
      fontSize: 12,
      fontWeight: '600',
      color: colors.text,
    },
    // Action buttons
    offerActions: {
      flexDirection: 'row',
      gap: 12,
      paddingHorizontal: 20,
    },
    declineBtn: {
      flex: 1,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: '#FEF2F2',
      borderRadius: 16,
      paddingVertical: 16,
      gap: 8,
      borderWidth: 1,
      borderColor: '#FECACA',
    },
    declineText: {
      fontSize: 15,
      fontWeight: '600',
      color: '#FF4757',
    },
    acceptBtn: {
      flex: 2,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: colors.primary,
      borderRadius: 16,
      paddingVertical: 16,
      gap: 8,
      shadowColor: colors.primary,
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.3,
      shadowRadius: 8,
      elevation: 6,
    },
    acceptText: {
      fontSize: 16,
      fontWeight: '700',
      color: '#fff',
    },
  });
}
