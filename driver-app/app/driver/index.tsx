import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, Alert, Platform, Linking, Animated } from 'react-native';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import { Ionicons } from '@expo/vector-icons';
import { useDriverStore } from '../../store/driverStore';
import {
  DriverTopBar,
  DriverIdlePanel,
  ActiveRidePanel,
  TripCompletedPanel,
  MapControls,
} from '../../components/dashboard';
import { useDriverDashboard } from '../../hooks/useDriverDashboard';
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
  const { user, driver: driverData } = useDriverStore();
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

    return (
      <View style={styles.rideOfferOverlay}>
        {/* Countdown and ride details */}
        <View style={styles.rideOfferContent}>
          <View style={styles.countdownContainer}>
            <View style={[styles.countdownCircle, { borderColor: COLORS.accent }]}>
              <Text style={[styles.countdownText, { color: COLORS.accent }]}>{countdown}</Text>
            </View>
            <View style={[styles.countdownBar, { width: `${progress * 100}%`, backgroundColor: COLORS.accent }]} />
          </View>
          <Text style={styles.rideOfferTitle}>New Ride Offer!</Text>
          <Text style={styles.rideOfferFare}>${(incomingRide.fare || 0).toFixed(2)}</Text>
          <Text style={styles.rideOfferAddress}>{incomingRide.pickup_address}</Text>
          <View style={styles.offerActions}>
            <TouchableOpacity
              style={styles.declineBtn}
              onPress={() => declineRide(incomingRide.ride_id)}
            >
              <Ionicons name="close" size={28} color="#FF4757" />
              <Text style={styles.declineText}>Decline</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.acceptBtn}
              onPress={() => acceptRide(incomingRide.ride_id)}
            >
              <Ionicons name="checkmark" size={28} color="#fff" />
              <Text style={styles.acceptText}>Accept</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    );
  };

  return (
    <View style={styles.container}>
      {/* Map */}
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={MAP_PROVIDER}
        initialRegion={{
          latitude: location?.coords.latitude || 52.1332,
          longitude: location?.coords.longitude || -106.6700,
          latitudeDelta: 0.04,
          longitudeDelta: 0.04,
        }}
        showsUserLocation
        showsMyLocationButton={false}
        onRegionChange={(region) => {
          currentRegionRef.current = {
            latitudeDelta: region.latitudeDelta,
            longitudeDelta: region.longitudeDelta,
          };
        }}
      >
        {getMapMarkers()}
      </MapView>

      {/* Top Bar */}
      <DriverTopBar driverData={driverData} user={user} isOnline={isOnline} />

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
          earnings={earnings}
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
  rideOfferOverlay: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: '50%',
    justifyContent: 'flex-end',
  },
  rideOfferContent: {
    backgroundColor: COLORS.primary,
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    padding: 24,
    alignItems: 'center',
  },
  countdownContainer: {
    alignItems: 'center',
    marginBottom: 16,
  },
  countdownCircle: {
    width: 60,
    height: 60,
    borderRadius: 30,
    borderWidth: 3,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 8,
  },
  countdownText: {
    fontSize: 24,
    fontWeight: '800',
  },
  countdownBar: {
    height: 3,
    borderRadius: 2,
    alignSelf: 'flex-start',
  },
  rideOfferTitle: {
    fontSize: 20,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 8,
  },
  rideOfferFare: {
    fontSize: 36,
    fontWeight: '800',
    color: COLORS.accent,
    marginBottom: 16,
  },
  rideOfferAddress: {
    fontSize: 14,
    color: COLORS.textDim,
    textAlign: 'center',
    marginBottom: 24,
  },
  offerActions: {
    flexDirection: 'row',
    gap: 16,
    width: '100%',
  },
  declineBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255, 71, 87, 0.1)',
    borderRadius: 16,
    paddingVertical: 16,
    gap: 8,
  },
  declineText: {
    fontSize: 16,
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
  },
  acceptText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#fff',
  },
});
