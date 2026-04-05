import React, { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Alert, Platform, Linking, Animated, TouchableOpacity, ActivityIndicator } from 'react-native';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
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
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';

// Use Google Maps on Android, Apple Maps (native) on iOS
const MAP_PROVIDER = Platform.OS === 'android' ? PROVIDER_GOOGLE : undefined;

const COLORS = {
  primary: SpinrConfig.theme.colors.background,
  accent: SpinrConfig.theme.colors.primary,
  text: SpinrConfig.theme.colors.text,
  textDim: SpinrConfig.theme.colors.textDim,
};

export default function DriverDashboard() {
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
  } = useDriverStore();

  const {
    isOnline,
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
            <View style={[styles.markerDot, { backgroundColor: COLORS.accent }]}>
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
    const progress = countdown / 15;
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
              <Text style={styles.rideOfferTitle}>New Ride Request!</Text>
              <View style={styles.rideTypeBadge}>
                <Ionicons name="car-sport" size={12} color={COLORS.accent} />
                <Text style={styles.rideTypeText}>Standard Ride</Text>
              </View>
            </View>
            <View style={styles.fareContainer}>
              <Text style={styles.fareLabel}>Fare</Text>
              <Text style={styles.fareAmount}>${fare}</Text>
            </View>
          </View>

          {/* Route: Pickup & Dropoff */}
          <View style={styles.routeContainer}>
            <View style={styles.routeIconColumn}>
              <View style={[styles.routeDot, { backgroundColor: COLORS.accent }]} />
              <View style={styles.routeLine} />
              <View style={[styles.routeDot, { backgroundColor: '#FF4757' }]} />
            </View>
            <View style={styles.routeDetails}>
              <View style={styles.routeRow}>
                <Text style={styles.routeLabel}>PICKUP</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>
                  {incomingRide.pickup_address || 'Pickup location'}
                </Text>
              </View>
              <View style={styles.routeDivider} />
              <View style={styles.routeRow}>
                <Text style={styles.routeLabel}>DROP-OFF</Text>
                <Text style={styles.routeAddress} numberOfLines={1}>
                  {incomingRide.dropoff_address || 'Dropoff location'}
                </Text>
              </View>
            </View>
          </View>

          {/* Trip info badges */}
          <View style={styles.tripInfoRow}>
            {incomingRide.distance_km && (
              <View style={styles.tripInfoBadge}>
                <Ionicons name="navigate-outline" size={14} color={COLORS.accent} />
                <Text style={styles.tripInfoText}>{incomingRide.distance_km.toFixed(1)} km</Text>
              </View>
            )}
            {incomingRide.duration_minutes && (
              <View style={styles.tripInfoBadge}>
                <Ionicons name="time-outline" size={14} color={COLORS.accent} />
                <Text style={styles.tripInfoText}>{Math.round(incomingRide.duration_minutes)} min</Text>
              </View>
            )}
            {incomingRide.rider_name && (
              <View style={styles.tripInfoBadge}>
                <Ionicons name="person-outline" size={14} color={COLORS.accent} />
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
              <Text style={styles.declineText}>Decline</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.acceptBtn}
              onPress={() => acceptRide(incomingRide.ride_id)}
              activeOpacity={0.8}
            >
              <Ionicons name="checkmark-circle" size={24} color="#fff" />
              <Text style={styles.acceptText}>Accept Ride</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    );
  };

  if (!location?.coords) {
    return (
      <View style={[styles.container, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator size="large" color={COLORS.accent} />
        <Text style={{ color: COLORS.text, marginTop: 12, fontSize: 15 }}>Getting your location...</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Map */}
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={MAP_PROVIDER}
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
      </MapView>

      {/* Top Bar */}
      <DriverTopBar driverData={driverData} user={user} isOnline={isOnline} />

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
          isLoading={false}
          otpInput={otpInput}
          setOtpInput={setOtpInput}
          onVerifyOTP={(otp) => verifyOTP(activeRide!.ride.id, otp)}
          onNavigate={openNavigation}
          onArriveAtPickup={() => arriveAtPickup(activeRide!.ride.id)}
          onStartRide={() => startRide(activeRide!.ride.id)}
          onCompleteRide={() => completeRide(activeRide!.ride.id)}
          onCancelRide={() => cancelRide(activeRide!.ride.id)}
          slideUpAnim={slideUpAnim}
          fadeAnim={fadeAnim}
        />
      )}
      {rideState === 'trip_completed' && (
        <TripCompletedPanel
          completedRide={completedRide}
          onDone={resetRideState}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.primary,
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
    backgroundColor: COLORS.primary,
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
    backgroundColor: '#F3F4F6',
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    overflow: 'hidden',
  },
  timerBar: {
    height: '100%',
    backgroundColor: COLORS.accent,
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
    borderColor: COLORS.accent,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: 'rgba(0,212,170,0.06)',
  },
  countdownText: {
    fontSize: 22,
    fontWeight: '800',
    color: COLORS.accent,
  },
  rideOfferTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: COLORS.text,
  },
  rideTypeBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 3,
  },
  rideTypeText: {
    fontSize: 12,
    color: COLORS.accent,
    fontWeight: '600',
  },
  fareContainer: {
    alignItems: 'flex-end',
  },
  fareLabel: {
    fontSize: 11,
    color: COLORS.textDim,
    fontWeight: '500',
  },
  fareAmount: {
    fontSize: 28,
    fontWeight: '800',
    color: COLORS.accent,
    letterSpacing: -1,
  },
  // Route display
  routeContainer: {
    flexDirection: 'row',
    marginHorizontal: 20,
    backgroundColor: '#F9FAFB',
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
    backgroundColor: '#D1D5DB',
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
    color: COLORS.textDim,
    letterSpacing: 0.5,
    marginBottom: 2,
  },
  routeAddress: {
    fontSize: 14,
    fontWeight: '500',
    color: COLORS.text,
  },
  routeDivider: {
    height: 1,
    backgroundColor: '#E5E7EB',
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
    color: COLORS.text,
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
    backgroundColor: COLORS.accent,
    borderRadius: 16,
    paddingVertical: 16,
    gap: 8,
    shadowColor: COLORS.accent,
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
