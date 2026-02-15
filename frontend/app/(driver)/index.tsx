import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Dimensions,
  Animated,
  ActivityIndicator,
  Platform,
  Linking,
  Alert,
  Vibration,
} from 'react-native';
import MapView, { Marker, Polyline, PROVIDER_GOOGLE } from 'react-native-maps';
import * as Location from 'expo-location';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useAuthStore } from '../../store/authStore';
import { useDriverStore, RideState } from '../../store/driverStore';
import api from '../../api/client';
import { API_URL } from '../../config';

const { width, height } = Dimensions.get('window');

// ─── Colors (Consistent with Rider App - White and Red) ────────────────────────────────────────────────────────
const COLORS = {
  primary: '#FF3B30', // Vibrant Red
  accent: '#FF3B30',
  accentDim: '#D32F2F',
  danger: '#DC2626',
  orange: '#FF9500',
  surface: '#FFFFFF',
  surfaceLight: '#F5F5F5',
  text: '#1A1A1A',
  textDim: '#666666',
  success: '#34C759',
  gold: '#FFD700',
  overlay: 'rgba(255, 255, 255, 0.95)',
  border: '#E5E7EB',
};

export default function DriverDashboard() {
  const { user, driver: driverData, updateDriverStatus } = useAuthStore();
  const {
    rideState,
    incomingRide,
    activeRide,
    completedRide,
    countdownSeconds,
    isLoading,
    error,
    setIncomingRide,
    setCountdown,
    acceptRide,
    declineRide,
    arriveAtPickup,
    verifyOTP,
    startRide,
    completeRide,
    cancelRide,
    fetchActiveRide,
    resetRideState,
    clearError,
  } = useDriverStore();

  const [isOnline, setIsOnline] = useState(driverData?.is_online || false);
  const [location, setLocation] = useState<Location.LocationObject | null>(null);
  const [otpInput, setOtpInput] = useState('');
  const mapRef = useRef<MapView>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const locationSubRef = useRef<Location.LocationSubscription | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Animations
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const slideUpAnim = useRef(new Animated.Value(height)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // ─── Location Tracking ───────────────────────────────────────────
  const locationBufferRef = useRef<any[]>([]);

  useEffect(() => {
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') return;
      const loc = await Location.getCurrentPositionAsync({});
      setLocation(loc);
    })();
  }, []);

  useEffect(() => {
    if (!isOnline) {
      if (locationSubRef.current) {
        locationSubRef.current.remove();
        locationSubRef.current = null;
      }
      return;
    }
    (async () => {
      const sub = await Location.watchPositionAsync(
        {
          accuracy: Location.Accuracy.High,
          timeInterval: 5000,
          distanceInterval: 10,
        },
        (loc) => {
          setLocation(loc);

          // Build payload with full GPS data + ride context
          const { rideState: currentRideState, activeRide: currentActiveRide } = useDriverStore.getState();
          const rideId = currentActiveRide?.ride?.id || null;
          const phaseMap: Record<string, string> = {
            idle: 'online_idle',
            ride_offered: 'online_idle',
            navigating_to_pickup: 'navigating_to_pickup',
            arrived_at_pickup: 'arrived_at_pickup',
            trip_in_progress: 'trip_in_progress',
            trip_completed: 'online_idle',
          };

          const payload = {
            type: 'driver_location',
            lat: loc.coords.latitude,
            lng: loc.coords.longitude,
            speed: loc.coords.speed ?? null,
            heading: loc.coords.heading ?? null,
            accuracy: loc.coords.accuracy ?? null,
            altitude: loc.coords.altitude ?? null,
            ride_id: rideId,
            tracking_phase: phaseMap[currentRideState] || 'online_idle',
          };

          if (wsRef.current?.readyState === WebSocket.OPEN) {
            // Flush any buffered points first
            if (locationBufferRef.current.length > 0) {
              wsRef.current.send(JSON.stringify({
                type: 'location_batch',
                points: locationBufferRef.current,
              }));
              locationBufferRef.current = [];
            }
            wsRef.current.send(JSON.stringify(payload));
          } else {
            // Buffer the point for later upload
            locationBufferRef.current.push({
              ...payload,
              timestamp: new Date().toISOString(),
            });
            // Cap the buffer at 500 points (~40 min at 5s intervals)
            if (locationBufferRef.current.length > 500) {
              locationBufferRef.current = locationBufferRef.current.slice(-500);
            }
          }
        }
      );
      locationSubRef.current = sub;
    })();

    return () => {
      if (locationSubRef.current) {
        locationSubRef.current.remove();
        locationSubRef.current = null;
      }
    };
  }, [isOnline]);

  // ─── WebSocket Connection ────────────────────────────────────────
  useEffect(() => {
    if (!isOnline || !user) return;

    const wsUrl = API_URL.replace('http', 'ws') + '/ws';
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: 'auth',
        token: useAuthStore.getState().token,
        client_type: 'driver',
      }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleWSMessage(data);
      } catch { }
    };

    ws.onerror = () => { };
    ws.onclose = () => { };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [isOnline, user]);

  const handleWSMessage = useCallback((data: any) => {
    switch (data.type) {
      case 'new_ride_assignment':
        Vibration.vibrate([0, 500, 200, 500]);
        setIncomingRide({
          ride_id: data.ride_id,
          pickup_address: data.pickup_address,
          dropoff_address: data.dropoff_address,
          pickup_lat: data.pickup_lat || 0,
          pickup_lng: data.pickup_lng || 0,
          dropoff_lat: data.dropoff_lat || 0,
          dropoff_lng: data.dropoff_lng || 0,
          fare: data.fare || 0,
          distance_km: data.distance_km,
          duration_minutes: data.duration_minutes,
          rider_name: data.rider_name,
          rider_rating: data.rider_rating,
        });
        break;
      case 'ride_cancelled':
        Alert.alert('Ride Cancelled', 'The rider has cancelled this ride.');
        resetRideState();
        break;
    }
  }, []);

  // ─── Countdown Timer ─────────────────────────────────────────────
  useEffect(() => {
    if (rideState === 'ride_offered' && countdownSeconds > 0) {
      countdownRef.current = setInterval(() => {
        const current = useDriverStore.getState().countdownSeconds;
        if (current <= 1) {
          clearInterval(countdownRef.current!);
          setCountdown(0);
        } else {
          setCountdown(current - 1);
        }
      }, 1000);

      return () => {
        if (countdownRef.current) clearInterval(countdownRef.current);
      };
    }
  }, [rideState]);

  // ─── Slide-up animation for panels ───────────────────────────────
  useEffect(() => {
    const shouldShow = rideState !== 'idle';
    Animated.timing(slideUpAnim, {
      toValue: shouldShow ? 0 : height * 0.4,
      duration: 350,
      useNativeDriver: true,
    }).start();
    Animated.timing(fadeAnim, {
      toValue: shouldShow ? 1 : 0,
      duration: 250,
      useNativeDriver: true,
    }).start();
  }, [rideState]);

  // ─── Pulse animation for online indicator ────────────────────────
  useEffect(() => {
    if (isOnline) {
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, { toValue: 1.3, duration: 1000, useNativeDriver: true }),
          Animated.timing(pulseAnim, { toValue: 1, duration: 1000, useNativeDriver: true }),
        ])
      ).start();
    } else {
      pulseAnim.setValue(1);
    }
  }, [isOnline]);

  // ─── Fetch active ride on mount ──────────────────────────────────
  useEffect(() => {
    if (isOnline) {
      fetchActiveRide();
    }
  }, [isOnline]);

  // ─── Toggle Online/Offline ───────────────────────────────────────
  const toggleOnline = async () => {
    const next = !isOnline;
    setIsOnline(next);
    try {
      await updateDriverStatus(next);
    } catch {
      setIsOnline(!next);
    }
  };

  // ─── Navigate to pickup/dropoff ──────────────────────────────────
  const openNavigation = (lat: number, lng: number, label: string) => {
    const url = Platform.select({
      ios: `maps:0,0?q=${label}@${lat},${lng}`,
      android: `google.navigation:q=${lat},${lng}`,
    });
    if (url) Linking.openURL(url);
  };

  // ─── Map Markers ─────────────────────────────────────────────────
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
            <View style={[styles.markerDot, { backgroundColor: COLORS.danger }]}>
              <Ionicons name="flag" size={16} color="#fff" />
            </View>
          </View>
        </Marker>
      );
    }

    return markers;
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER PANELS BY STATE
  // ═══════════════════════════════════════════════════════════════════

  const renderIdlePanel = () => (
    <View style={styles.idlePanel}>
      <TouchableOpacity
        style={[styles.onlineToggle, isOnline ? styles.onlineActive : styles.onlineInactive]}
        onPress={toggleOnline}
        activeOpacity={0.8}
      >
        <Animated.View style={[styles.pulseIndicator, { transform: [{ scale: pulseAnim }] }]}>
          <View style={[styles.statusDot, { backgroundColor: isOnline ? COLORS.success : COLORS.danger }]} />
        </Animated.View>
        <View style={styles.toggleText}>
          <Text style={styles.toggleLabel}>{isOnline ? 'You\'re Online' : 'You\'re Offline'}</Text>
          <Text style={styles.toggleSub}>
            {isOnline ? 'Waiting for ride requests...' : 'Go online to start earning'}
          </Text>
        </View>
        <View style={[styles.toggleSwitch, isOnline && styles.toggleSwitchOn]}>
          <View style={[styles.toggleKnob, isOnline && styles.toggleKnobOn]} />
        </View>
      </TouchableOpacity>

      {isOnline && (
        <View style={styles.statsRow}>
          <View style={styles.statCard}>
            <FontAwesome5 name="road" size={18} color={COLORS.accent} />
            <Text style={styles.statValue}>{driverData?.total_rides || 0}</Text>
            <Text style={styles.statLabel}>Total Rides</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="star" size={18} color={COLORS.gold} />
            <Text style={styles.statValue}>{(driverData?.rating || 5.0).toFixed(1)}</Text>
            <Text style={styles.statLabel}>Rating</Text>
          </View>
          <View style={styles.statCard}>
            <MaterialCommunityIcons name="clock-outline" size={18} color={COLORS.orange} />
            <Text style={styles.statValue}>0h</Text>
            <Text style={styles.statLabel}>Online</Text>
          </View>
        </View>
      )}
    </View>
  );

  const renderRideOfferPanel = () => {
    if (!incomingRide) return null;
    const progress = countdownSeconds / 15;

    return (
      <View style={styles.rideOfferOverlay}>
        <LinearGradient colors={['rgba(10,14,33,0.95)', COLORS.primary]} style={styles.rideOfferGradient}>
          {/* Countdown Ring */}
          <View style={styles.countdownContainer}>
            <View style={styles.countdownCircle}>
              <Text style={styles.countdownText}>{countdownSeconds}</Text>
            </View>
            <View style={[styles.countdownBar, { width: `${progress * 100}%` }]} />
          </View>

          {/* Ride Info */}
          <View style={styles.rideOfferInfo}>
            <View style={styles.fareHighlight}>
              <Text style={styles.fareLabel}>ESTIMATED FARE</Text>
              <Text style={styles.fareAmount}>${(incomingRide.fare || 0).toFixed(2)}</Text>
            </View>

            <View style={styles.addressRow}>
              <View style={styles.addressDot}>
                <View style={[styles.dot, { backgroundColor: COLORS.accent }]} />
                <View style={styles.dottedLine} />
                <View style={[styles.dot, { backgroundColor: COLORS.danger }]} />
              </View>
              <View style={styles.addresses}>
                <Text style={styles.addressText} numberOfLines={1}>
                  {incomingRide.pickup_address}
                </Text>
                <View style={styles.addressDivider} />
                <Text style={styles.addressText} numberOfLines={1}>
                  {incomingRide.dropoff_address}
                </Text>
              </View>
            </View>

            {incomingRide.rider_name && (
              <View style={styles.riderInfo}>
                <View style={styles.riderAvatar}>
                  <Ionicons name="person" size={20} color={COLORS.textDim} />
                </View>
                <Text style={styles.riderName}>{incomingRide.rider_name}</Text>
                {incomingRide.rider_rating && (
                  <View style={styles.ratingBadge}>
                    <Ionicons name="star" size={12} color={COLORS.gold} />
                    <Text style={styles.ratingText}>{incomingRide.rider_rating.toFixed(1)}</Text>
                  </View>
                )}
              </View>
            )}
          </View>

          {/* Action Buttons */}
          <View style={styles.offerActions}>
            <TouchableOpacity
              style={styles.declineBtn}
              onPress={() => declineRide(incomingRide.ride_id)}
            >
              <Ionicons name="close" size={28} color={COLORS.danger} />
              <Text style={styles.declineText}>Decline</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.acceptBtn}
              onPress={() => acceptRide(incomingRide.ride_id)}
              disabled={isLoading}
            >
              <LinearGradient
                colors={[COLORS.accent, COLORS.accentDim]}
                style={styles.acceptGradient}
              >
                {isLoading ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <Ionicons name="checkmark" size={28} color="#fff" />
                    <Text style={styles.acceptText}>Accept</Text>
                  </>
                )}
              </LinearGradient>
            </TouchableOpacity>
          </View>
        </LinearGradient>
      </View>
    );
  };

  const renderActiveRidePanel = () => {
    const ride = activeRide?.ride;
    const rider = activeRide?.rider;
    if (!ride) return null;

    return (
      <Animated.View
        style={[styles.activePanel, { transform: [{ translateY: slideUpAnim }], opacity: fadeAnim }]}
      >
        <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.activePanelInner}>
          {/* Status */}
          <View style={styles.rideStatusBar}>
            <View style={[styles.statusIndicator, {
              backgroundColor:
                rideState === 'trip_in_progress' ? COLORS.accent :
                  rideState === 'arrived_at_pickup' ? COLORS.orange :
                    COLORS.gold
            }]} />
            <Text style={styles.rideStatusText}>
              {rideState === 'navigating_to_pickup' && 'Navigating to Pickup'}
              {rideState === 'arrived_at_pickup' && 'Waiting for Rider'}
              {rideState === 'trip_in_progress' && 'Trip in Progress'}
            </Text>
            {ride.total_fare && (
              <Text style={styles.rideFare}>${ride.total_fare.toFixed(2)}</Text>
            )}
          </View>

          {/* Rider Info */}
          {rider && (
            <View style={styles.riderCard}>
              <View style={styles.riderAvatar}>
                <Ionicons name="person" size={24} color={COLORS.textDim} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.riderCardName}>{rider.first_name || rider.name || 'Rider'}</Text>
                {rider.rating && (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                    <Ionicons name="star" size={12} color={COLORS.gold} />
                    <Text style={styles.textDim}>{rider.rating}</Text>
                  </View>
                )}
              </View>
              <TouchableOpacity style={styles.contactBtn}>
                <Ionicons name="chatbubble-ellipses" size={20} color={COLORS.accent} />
              </TouchableOpacity>
              <TouchableOpacity style={styles.contactBtn}>
                <Ionicons name="call" size={20} color={COLORS.accent} />
              </TouchableOpacity>
            </View>
          )}

          {/* Addresses */}
          <View style={styles.addressRow}>
            <View style={styles.addressDot}>
              <View style={[styles.dot, { backgroundColor: COLORS.accent }]} />
              <View style={styles.dottedLine} />
              <View style={[styles.dot, { backgroundColor: COLORS.danger }]} />
            </View>
            <View style={styles.addresses}>
              <Text style={styles.addressTextSm} numberOfLines={1}>
                {ride.pickup_address}
              </Text>
              <View style={styles.addressDivider} />
              <Text style={styles.addressTextSm} numberOfLines={1}>
                {ride.dropoff_address}
              </Text>
            </View>
          </View>

          {/* OTP Input for arrived state */}
          {rideState === 'arrived_at_pickup' && (
            <View style={styles.otpSection}>
              <Text style={styles.otpLabel}>Enter Rider's PIN</Text>
              <View style={styles.otpRow}>
                {[0, 1, 2, 3].map((i) => (
                  <TouchableOpacity
                    key={i}
                    style={[styles.otpBox, otpInput.length > i && styles.otpBoxFilled]}
                  >
                    <Text style={styles.otpDigit}>{otpInput[i] || ''}</Text>
                  </TouchableOpacity>
                ))}
              </View>
              <View style={styles.otpKeypad}>
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, null, 0, 'del'].map((key) => (
                  <TouchableOpacity
                    key={String(key)}
                    style={styles.keypadBtn}
                    onPress={() => {
                      if (key === 'del') {
                        setOtpInput((prev) => prev.slice(0, -1));
                      } else if (key !== null && otpInput.length < 4) {
                        const newOtp = otpInput + String(key);
                        setOtpInput(newOtp);
                        if (newOtp.length === 4) {
                          verifyOTP(ride.id, newOtp).then((ok) => {
                            if (!ok) setOtpInput('');
                          });
                        }
                      }
                    }}
                  >
                    {key === 'del' ? (
                      <Ionicons name="backspace" size={22} color={COLORS.text} />
                    ) : key !== null ? (
                      <Text style={styles.keypadText}>{key}</Text>
                    ) : (
                      <View />
                    )}
                  </TouchableOpacity>
                ))}
              </View>
            </View>
          )}

          {/* Action Buttons */}
          <View style={styles.rideActions}>
            {rideState === 'navigating_to_pickup' && (
              <>
                <TouchableOpacity
                  style={styles.navBtn}
                  onPress={() => openNavigation(ride.pickup_lat, ride.pickup_lng, 'Pickup')}
                >
                  <Ionicons name="navigate" size={20} color="#fff" />
                  <Text style={styles.navBtnText}>Navigate</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.arriveBtn}
                  onPress={() => arriveAtPickup(ride.id)}
                  disabled={isLoading}
                >
                  <LinearGradient colors={[COLORS.accent, COLORS.accentDim]} style={styles.actionGradient}>
                    {isLoading ? <ActivityIndicator color="#fff" /> : (
                      <Text style={styles.actionBtnText}>I've Arrived</Text>
                    )}
                  </LinearGradient>
                </TouchableOpacity>
              </>
            )}

            {rideState === 'arrived_at_pickup' && (
              <TouchableOpacity
                style={styles.startBtn}
                onPress={() => startRide(ride.id)}
                disabled={isLoading}
              >
                <LinearGradient colors={[COLORS.orange, '#FF6B35']} style={styles.actionGradient}>
                  {isLoading ? <ActivityIndicator color="#fff" /> : (
                    <Text style={styles.actionBtnText}>Start Without PIN</Text>
                  )}
                </LinearGradient>
              </TouchableOpacity>
            )}

            {rideState === 'trip_in_progress' && (
              <>
                <TouchableOpacity
                  style={styles.navBtn}
                  onPress={() => openNavigation(ride.dropoff_lat, ride.dropoff_lng, 'Dropoff')}
                >
                  <Ionicons name="navigate" size={20} color="#fff" />
                  <Text style={styles.navBtnText}>Navigate</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.completeBtn}
                  onPress={() => {
                    Alert.alert(
                      'Complete Ride',
                      'Are you sure you want to complete this trip?',
                      [
                        { text: 'Cancel', style: 'cancel' },
                        { text: 'Complete', onPress: () => completeRide(ride.id) },
                      ]
                    );
                  }}
                  disabled={isLoading}
                >
                  <LinearGradient colors={[COLORS.accent, COLORS.accentDim]} style={styles.actionGradient}>
                    {isLoading ? <ActivityIndicator color="#fff" /> : (
                      <Text style={styles.actionBtnText}>Complete Trip</Text>
                    )}
                  </LinearGradient>
                </TouchableOpacity>
              </>
            )}

            {/* Cancel button (for navigating & arrived states only) */}
            {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup') && (
              <TouchableOpacity
                style={styles.cancelRideBtn}
                onPress={() => {
                  Alert.alert(
                    'Cancel Ride',
                    'Are you sure you want to cancel? This may affect your rating.',
                    [
                      { text: 'No', style: 'cancel' },
                      { text: 'Yes, Cancel', style: 'destructive', onPress: () => cancelRide(ride.id) },
                    ]
                  );
                }}
              >
                <Text style={styles.cancelRideText}>Cancel Ride</Text>
              </TouchableOpacity>
            )}
          </View>
        </LinearGradient>
      </Animated.View>
    );
  };

  const renderTripCompletedPanel = () => {
    if (!completedRide) return null;
    return (
      <View style={styles.completedOverlay}>
        <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.completedPanel}>
          <View style={styles.completedIcon}>
            <Ionicons name="checkmark-circle" size={60} color={COLORS.accent} />
          </View>
          <Text style={styles.completedTitle}>Trip Completed!</Text>

          <View style={styles.fareBreakdown}>
            <View style={styles.fareRow}>
              <Text style={styles.fareItemLabel}>Base Fare</Text>
              <Text style={styles.fareItemValue}>${(completedRide.base_fare || 0).toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareItemLabel}>Distance</Text>
              <Text style={styles.fareItemValue}>${(completedRide.distance_fare || 0).toFixed(2)}</Text>
            </View>
            <View style={styles.fareRow}>
              <Text style={styles.fareItemLabel}>Time</Text>
              <Text style={styles.fareItemValue}>${(completedRide.time_fare || 0).toFixed(2)}</Text>
            </View>
            <View style={styles.fareDivider} />
            <View style={styles.fareRow}>
              <Text style={styles.fareEarningsLabel}>Your Earnings</Text>
              <Text style={styles.fareEarningsValue}>
                ${(completedRide.driver_earnings || 0).toFixed(2)}
              </Text>
            </View>
          </View>

          <View style={styles.tripStats}>
            <View style={styles.tripStat}>
              <Ionicons name="speedometer" size={18} color={COLORS.textDim} />
              <Text style={styles.tripStatValue}>{(completedRide.distance_km || 0).toFixed(1)} km</Text>
            </View>
            <View style={styles.tripStat}>
              <Ionicons name="time" size={18} color={COLORS.textDim} />
              <Text style={styles.tripStatValue}>{completedRide.duration_minutes || 0} min</Text>
            </View>
          </View>

          <TouchableOpacity
            style={styles.doneBtn}
            onPress={resetRideState}
          >
            <LinearGradient colors={[COLORS.accent, COLORS.accentDim]} style={styles.actionGradient}>
              <Text style={styles.actionBtnText}>Done</Text>
            </LinearGradient>
          </TouchableOpacity>
        </LinearGradient>
      </View>
    );
  };

  // ─── Error Toast ─────────────────────────────────────────────────
  useEffect(() => {
    if (error) {
      Alert.alert('Error', error, [{ text: 'OK', onPress: clearError }]);
    }
  }, [error]);

  // ═══════════════════════════════════════════════════════════════════
  //  MAIN RENDER
  // ═══════════════════════════════════════════════════════════════════
  return (
    <View style={styles.container}>
      {/* Map */}
      <MapView
        ref={mapRef}
        style={styles.map}
        provider={PROVIDER_GOOGLE}
        initialRegion={{
          latitude: location?.coords.latitude || 52.1332,
          longitude: location?.coords.longitude || -106.6700,
          latitudeDelta: 0.04,
          longitudeDelta: 0.04,
        }}
        showsUserLocation
        showsMyLocationButton={false}
        customMapStyle={mapDarkStyle}
      >
        {getMapMarkers()}
      </MapView>

      {/* Top Bar */}
      <View style={styles.topBar}>
        <View style={styles.topBarInner}>
          <View style={styles.driverInfo}>
            <View style={styles.avatarSmall}>
              <Ionicons name="person" size={18} color={COLORS.textDim} />
            </View>
            <View>
              <Text style={styles.driverName}>{driverData?.name || user?.first_name || 'Driver'}</Text>
              <Text style={styles.vehicleInfo}>
                {driverData?.vehicle_make} {driverData?.vehicle_model} · {driverData?.license_plate}
              </Text>
            </View>
          </View>
          <View style={[styles.onlineBadge, isOnline && styles.onlineBadgeActive]}>
            <View style={[styles.onlineDot, { backgroundColor: isOnline ? COLORS.success : COLORS.textDim }]} />
            <Text style={[styles.onlineBadgeText, isOnline && { color: COLORS.success }]}>
              {isOnline ? 'ONLINE' : 'OFFLINE'}
            </Text>
          </View>
        </View>
      </View>

      {/* My Location Button */}
      <TouchableOpacity
        style={styles.myLocationBtn}
        onPress={() => {
          if (location && mapRef.current) {
            mapRef.current.animateToRegion({
              latitude: location.coords.latitude,
              longitude: location.coords.longitude,
              latitudeDelta: 0.01,
              longitudeDelta: 0.01,
            }, 500);
          }
        }}
      >
        <Ionicons name="locate" size={22} color={COLORS.accent} />
      </TouchableOpacity>

      {/* Bottom Panels */}
      {rideState === 'idle' && renderIdlePanel()}
      {rideState === 'ride_offered' && renderRideOfferPanel()}
      {(rideState === 'navigating_to_pickup' || rideState === 'arrived_at_pickup' || rideState === 'trip_in_progress') && renderActiveRidePanel()}
      {rideState === 'trip_completed' && renderTripCompletedPanel()}
    </View>
  );
}

// ─── Dark Map Style ────────────────────────────────────────────────
const mapDarkStyle = [
  { elementType: 'geometry', stylers: [{ color: '#1d2c4d' }] },
  { elementType: 'labels.text.fill', stylers: [{ color: '#8ec3b9' }] },
  { elementType: 'labels.text.stroke', stylers: [{ color: '#1a3646' }] },
  { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#304a7d' }] },
  { featureType: 'road', elementType: 'geometry.stroke', stylers: [{ color: '#255763' }] },
  { featureType: 'road.highway', elementType: 'geometry', stylers: [{ color: '#2c6675' }] },
  { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#0e1626' }] },
  { featureType: 'poi', elementType: 'geometry', stylers: [{ color: '#283d6a' }] },
  { featureType: 'transit', elementType: 'geometry', stylers: [{ color: '#2f3948' }] },
];

// ═══════════════════════════════════════════════════════════════════
//  STYLES
// ═══════════════════════════════════════════════════════════════════

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.primary,
  },
  map: {
    ...StyleSheet.absoluteFillObject,
  },

  // ── Top Bar ──────────────────────────────────────────────────────
  topBar: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 50 : 35,
    left: 16,
    right: 16,
    zIndex: 10,
  },
  topBarInner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: COLORS.overlay,
    borderRadius: 16,
    padding: 12,
    paddingHorizontal: 16,
  },
  driverInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  avatarSmall: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: COLORS.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  driverName: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '600',
  },
  vehicleInfo: {
    color: COLORS.textDim,
    fontSize: 11,
  },
  onlineBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: COLORS.surfaceLight,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 20,
  },
  onlineBadgeActive: {
    backgroundColor: 'rgba(0, 230, 118, 0.12)',
  },
  onlineDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
  },
  onlineBadgeText: {
    color: COLORS.textDim,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 0.8,
  },

  // ── My Location ──────────────────────────────────────────────────
  myLocationBtn: {
    position: 'absolute',
    right: 16,
    bottom: 320,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: COLORS.overlay,
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
  },

  // ── Idle Panel ───────────────────────────────────────────────────
  idlePanel: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    paddingBottom: Platform.OS === 'ios' ? 30 : 20,
    paddingHorizontal: 16,
    paddingTop: 16,
  },
  onlineToggle: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    borderRadius: 20,
    gap: 14,
  },
  onlineActive: {
    backgroundColor: 'rgba(0, 212, 170, 0.12)',
    borderWidth: 1,
    borderColor: 'rgba(0, 212, 170, 0.25)',
  },
  onlineInactive: {
    backgroundColor: COLORS.overlay,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  pulseIndicator: {
    width: 40,
    height: 40,
    justifyContent: 'center',
    alignItems: 'center',
  },
  statusDot: {
    width: 14,
    height: 14,
    borderRadius: 7,
  },
  toggleText: {
    flex: 1,
  },
  toggleLabel: {
    color: COLORS.text,
    fontSize: 16,
    fontWeight: '700',
  },
  toggleSub: {
    color: COLORS.textDim,
    fontSize: 12,
    marginTop: 2,
  },
  toggleSwitch: {
    width: 50,
    height: 28,
    borderRadius: 14,
    backgroundColor: COLORS.surfaceLight,
    justifyContent: 'center',
    paddingHorizontal: 3,
  },
  toggleSwitchOn: {
    backgroundColor: COLORS.accent,
  },
  toggleKnob: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: '#fff',
  },
  toggleKnobOn: {
    alignSelf: 'flex-end',
  },
  statsRow: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 12,
  },
  statCard: {
    flex: 1,
    backgroundColor: COLORS.overlay,
    borderRadius: 14,
    padding: 14,
    alignItems: 'center',
    gap: 6,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.06)',
  },
  statValue: {
    color: COLORS.text,
    fontSize: 18,
    fontWeight: '700',
  },
  statLabel: {
    color: COLORS.textDim,
    fontSize: 10,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },

  // ── Ride Offer ───────────────────────────────────────────────────
  rideOfferOverlay: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
  },
  rideOfferGradient: {
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    padding: 20,
    paddingBottom: Platform.OS === 'ios' ? 40 : 24,
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
    borderColor: COLORS.accent,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 8,
  },
  countdownText: {
    color: COLORS.accent,
    fontSize: 24,
    fontWeight: '800',
  },
  countdownBar: {
    height: 3,
    backgroundColor: COLORS.accent,
    borderRadius: 2,
    alignSelf: 'flex-start',
  },
  rideOfferInfo: {
    marginBottom: 16,
  },
  fareHighlight: {
    alignItems: 'center',
    marginBottom: 16,
  },
  fareLabel: {
    color: COLORS.textDim,
    fontSize: 10,
    letterSpacing: 1.5,
    fontWeight: '600',
  },
  fareAmount: {
    color: COLORS.accent,
    fontSize: 36,
    fontWeight: '800',
    marginTop: 4,
  },
  addressRow: {
    flexDirection: 'row',
    gap: 12,
  },
  addressDot: {
    alignItems: 'center',
    width: 20,
    paddingTop: 4,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  dottedLine: {
    width: 2,
    height: 20,
    backgroundColor: COLORS.surfaceLight,
    marginVertical: 4,
  },
  addresses: {
    flex: 1,
  },
  addressText: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '500',
  },
  addressTextSm: {
    color: COLORS.text,
    fontSize: 13,
    fontWeight: '500',
  },
  addressDivider: {
    height: 1,
    backgroundColor: COLORS.surfaceLight,
    marginVertical: 8,
  },
  riderInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginTop: 14,
    paddingTop: 14,
    borderTopWidth: 1,
    borderTopColor: COLORS.surfaceLight,
  },
  riderAvatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: COLORS.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  riderName: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '600',
  },
  ratingBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: COLORS.surfaceLight,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
  },
  ratingText: {
    color: COLORS.gold,
    fontSize: 12,
    fontWeight: '600',
  },
  offerActions: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 4,
  },
  declineBtn: {
    flex: 0.35,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 14,
    borderRadius: 16,
    borderWidth: 1.5,
    borderColor: 'rgba(255, 71, 87, 0.3)',
    backgroundColor: 'rgba(255, 71, 87, 0.08)',
    gap: 4,
  },
  declineText: {
    color: COLORS.danger,
    fontSize: 12,
    fontWeight: '600',
  },
  acceptBtn: {
    flex: 0.65,
    borderRadius: 16,
    overflow: 'hidden',
  },
  acceptGradient: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
    gap: 8,
  },
  acceptText: {
    color: '#fff',
    fontSize: 18,
    fontWeight: '700',
  },

  // ── Active Ride Panel ────────────────────────────────────────────
  activePanel: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
  },
  activePanelInner: {
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    padding: 20,
    paddingBottom: Platform.OS === 'ios' ? 40 : 24,
  },
  rideStatusBar: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 14,
  },
  statusIndicator: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 10,
  },
  rideStatusText: {
    color: COLORS.text,
    fontSize: 16,
    fontWeight: '700',
    flex: 1,
  },
  rideFare: {
    color: COLORS.accent,
    fontSize: 18,
    fontWeight: '800',
  },
  riderCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: COLORS.surfaceLight,
    borderRadius: 14,
    padding: 12,
    marginBottom: 14,
  },
  riderCardName: {
    color: COLORS.text,
    fontSize: 15,
    fontWeight: '600',
  },
  textDim: {
    color: COLORS.textDim,
    fontSize: 12,
  },
  contactBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(0, 212, 170, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },

  // ── OTP Section ──────────────────────────────────────────────────
  otpSection: {
    alignItems: 'center',
    marginTop: 10,
    marginBottom: 6,
  },
  otpLabel: {
    color: COLORS.textDim,
    fontSize: 13,
    fontWeight: '600',
    letterSpacing: 0.5,
    marginBottom: 12,
  },
  otpRow: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 16,
  },
  otpBox: {
    width: 50,
    height: 56,
    borderRadius: 14,
    backgroundColor: COLORS.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1.5,
    borderColor: 'transparent',
  },
  otpBoxFilled: {
    borderColor: COLORS.accent,
    backgroundColor: 'rgba(0, 212, 170, 0.08)',
  },
  otpDigit: {
    color: COLORS.text,
    fontSize: 22,
    fontWeight: '700',
  },
  otpKeypad: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    width: 240,
    gap: 8,
    justifyContent: 'center',
  },
  keypadBtn: {
    width: 72,
    height: 48,
    borderRadius: 12,
    backgroundColor: COLORS.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  keypadText: {
    color: COLORS.text,
    fontSize: 20,
    fontWeight: '600',
  },

  // ── Action Buttons ───────────────────────────────────────────────
  rideActions: {
    gap: 10,
    marginTop: 8,
  },
  navBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    padding: 14,
    borderRadius: 14,
    backgroundColor: COLORS.surfaceLight,
  },
  navBtnText: {
    color: COLORS.text,
    fontSize: 15,
    fontWeight: '600',
  },
  arriveBtn: {
    borderRadius: 14,
    overflow: 'hidden',
  },
  startBtn: {
    borderRadius: 14,
    overflow: 'hidden',
  },
  completeBtn: {
    borderRadius: 14,
    overflow: 'hidden',
  },
  actionGradient: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 16,
    gap: 8,
  },
  actionBtnText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '700',
  },
  cancelRideBtn: {
    alignItems: 'center',
    padding: 10,
  },
  cancelRideText: {
    color: COLORS.danger,
    fontSize: 13,
    fontWeight: '600',
  },

  // ── Completed Panel ──────────────────────────────────────────────
  completedOverlay: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
  },
  completedPanel: {
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    padding: 24,
    paddingBottom: Platform.OS === 'ios' ? 40 : 24,
    alignItems: 'center',
  },
  completedIcon: {
    marginBottom: 10,
  },
  completedTitle: {
    color: COLORS.text,
    fontSize: 22,
    fontWeight: '800',
    marginBottom: 20,
  },
  fareBreakdown: {
    width: '100%',
    backgroundColor: COLORS.surfaceLight,
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  fareRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  fareItemLabel: {
    color: COLORS.textDim,
    fontSize: 14,
  },
  fareItemValue: {
    color: COLORS.text,
    fontSize: 14,
    fontWeight: '600',
  },
  fareDivider: {
    height: 1,
    backgroundColor: COLORS.primary,
    marginVertical: 8,
  },
  fareEarningsLabel: {
    color: COLORS.accent,
    fontSize: 16,
    fontWeight: '700',
  },
  fareEarningsValue: {
    color: COLORS.accent,
    fontSize: 20,
    fontWeight: '800',
  },
  tripStats: {
    flexDirection: 'row',
    gap: 30,
    marginBottom: 20,
  },
  tripStat: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  tripStatValue: {
    color: COLORS.textDim,
    fontSize: 14,
  },
  doneBtn: {
    width: '100%',
    borderRadius: 14,
    overflow: 'hidden',
  },

  // ── Markers ──────────────────────────────────────────────────────
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
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 3,
  },
});
