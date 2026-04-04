import { useState, useEffect, useRef, useCallback } from 'react';
import { Animated } from 'react-native';
import * as Location from 'expo-location';
import { Platform, Alert, Vibration, Linking } from 'react-native';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { router } from 'expo-router';

// expo-notifications throws at import time inside Expo Go since SDK 53 because
// push-token APIs were removed from Expo Go. Lazy-load it only in real builds
// (standalone / dev-client) so the dashboard still mounts in Expo Go.
const isExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;
let Notifications: any = null;
if (!isExpoGo) {
  try {
    Notifications = require('expo-notifications');
  } catch (e) {
    console.log('expo-notifications unavailable:', e);
  }
}
import { useAuthStore } from '@shared/store/authStore';
import { useDriverStore } from '../store/driverStore';
import api from '@shared/api/client';
import { API_URL } from '@shared/config';
import { Dimensions } from 'react-native';

const { height } = Dimensions.get('window');
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000];

interface UseDriverDashboardReturn {
  // State
  isOnline: boolean;
  location: Location.LocationObject | null;
  otpInput: string;
  setOtpInput: (value: string) => void;

  // Actions
  toggleOnline: () => Promise<void>;
  openNavigation: (lat: number, lng: number, label: string) => void;
  uploadLocationBatch: () => Promise<void>;

  // Refs for external use
  mapRef: React.RefObject<any>;
  currentRegionRef: React.RefObject<{ latitudeDelta: number; longitudeDelta: number }>;
  wsRef: React.RefObject<WebSocket | null>;
  locationSubRef: React.RefObject<Location.LocationSubscription | null>;
  countdownRef: React.RefObject<ReturnType<typeof setInterval> | null>;
  reconnectTimeoutRef: React.RefObject<ReturnType<typeof setTimeout> | null>;
  reconnectAttemptRef: React.RefObject<number>;
  locationBufferRef: React.RefObject<any[]>;

  // Animations
  pulseAnim: any;
  slideUpAnim: any;
  fadeAnim: any;
}

export const useDriverDashboard = (): UseDriverDashboardReturn => {
  const { user, driver: driverData, updateDriverStatus } = useAuthStore();
  const {
    rideState,
    incomingRide,
    activeRide,
    setIncomingRide,
    resetRideState,
    fetchActiveRide,
    fetchEarnings,
    earnings,
  } = useDriverStore();

  // State
  const [isOnline, setIsOnline] = useState(driverData?.is_online || false);
  const [location, setLocation] = useState<Location.LocationObject | null>(null);
  const [otpInput, setOtpInput] = useState('');

  // Refs
  const mapRef = useRef<any>(null);
  const currentRegionRef = useRef({ latitudeDelta: 0.04, longitudeDelta: 0.04 });
  const wsRef = useRef<WebSocket | null>(null);
  const locationSubRef = useRef<Location.LocationSubscription | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const locationBufferRef = useRef<any[]>([]);

  // Animations
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const slideUpAnim = useRef(new Animated.Value(height)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // ─── Location Tracking ───────────────────────────────────────────
  useEffect(() => {
    (async () => {
      // 0. Load cached location from previous session
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        const saved = await AsyncStorage.getItem('spinr_driver_last_location');
        if (saved) {
          const { lat, lng } = JSON.parse(saved);
          setLocation({ coords: { latitude: lat, longitude: lng, heading: 0, speed: 0, accuracy: 100, altitude: 0 }, timestamp: Date.now() } as any);
        }
      } catch {}

      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') return;

      // 1. Fast: OS cached location
      try {
        const lastKnown = await Location.getLastKnownPositionAsync();
        if (lastKnown) setLocation(lastKnown);
      } catch {}

      // 2. Accurate position (non-blocking)
      Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced })
        .then(loc => {
          setLocation(loc);
          // Save for next cold start
          try {
            const AsyncStorage = require('@react-native-async-storage/async-storage').default;
            AsyncStorage.setItem('spinr_driver_last_location', JSON.stringify({ lat: loc.coords.latitude, lng: loc.coords.longitude }));
          } catch {}
        })
        .catch(() => {});
    })();
  }, []);

  // ─── Batch Location Upload ───────────────────────────────────────
  const uploadLocationBatch = useCallback(async () => {
    if (locationBufferRef.current.length === 0) return;

    const pointsToUpload = [...locationBufferRef.current];
    locationBufferRef.current = [];

    try {
      await api.post('/drivers/location-batch', {
        points: pointsToUpload,
      });
      console.log(`Uploaded ${pointsToUpload.length} location points`);
    } catch (err) {
      console.log('Location batch upload failed:', err);
      locationBufferRef.current = [...pointsToUpload, ...locationBufferRef.current];
    }
  }, []);

  // Upload batch every 30 seconds
  useEffect(() => {
    if (!isOnline) return;
    const interval = setInterval(uploadLocationBatch, 30000);
    return () => clearInterval(interval);
  }, [isOnline, uploadLocationBatch]);

  // Location subscription
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
            wsRef.current.send(JSON.stringify(payload));
          }

          locationBufferRef.current.push({
            ...payload,
            timestamp: new Date().toISOString(),
          });

          if (locationBufferRef.current.length > 500) {
            locationBufferRef.current = locationBufferRef.current.slice(-500);
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
  }, [isOnline, uploadLocationBatch]);

  // ─── WebSocket Message Handler ───────────────────────────────────
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
  }, [setIncomingRide, resetRideState]);

  // ─── WebSocket Connection ────────────────────────────────────────
  const connectWebSocket = useCallback(() => {
    if (!isOnline || !user) return;

    const token = useAuthStore.getState().token;
    if (!token) {
      console.log('Cannot connect WebSocket: No auth token');
      return;
    }

    const wsUrl = `${API_URL.replace('http', 'ws')}/ws/driver/${user.id}`;
    console.log('Connecting to WebSocket:', wsUrl);
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('WebSocket connected, sending auth...');
      reconnectAttemptRef.current = 0;
      const token = useAuthStore.getState().token;
      console.log('Auth token exists:', !!token, 'User ID:', user?.id);
      ws.send(JSON.stringify({
        type: 'auth',
        token: token,
        client_type: 'driver',
      }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'error') {
          console.log('WebSocket auth error:', data.message);
        }
        handleWSMessage(data);
      } catch { }
    };

    ws.onerror = (error) => {
      console.log('WebSocket error:', error);
    };

    ws.onclose = (event) => {
      console.log('WebSocket closed:', event.code, event.reason);
      if (isOnline && user) {
        const delay = RECONNECT_DELAYS[Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS.length - 1)];
        console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttemptRef.current + 1})`);
        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectAttemptRef.current++;
          connectWebSocket();
        }, delay);
      }
    };
  }, [isOnline, user, handleWSMessage]);

  useEffect(() => {
    if (!isOnline || !user) {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      return;
    }

    connectWebSocket();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [isOnline, user, connectWebSocket, handleWSMessage]);

  // ─── Toggle Online/Offline ───────────────────────────────────────
  const toggleOnline = async () => {
    if (!driverData?.vehicle_make || !driverData?.license_plate) {
      Alert.alert(
        "Profile Incomplete",
        "You must provide vehicle details before going online.",
        [
          {
            text: "Add Vehicle Info",
            onPress: () => router.push('/vehicle-info' as any)
          },
          { text: "Cancel", style: "cancel" }
        ]
      );
      return;
    }

    if (!driverData?.is_verified) {
      Alert.alert(
        "Account Not Verified",
        "Your account is not verified yet. Please complete your profile and wait for admin approval before going online.",
        [
          {
            text: "Check Status",
            onPress: () => router.push('/driver/profile' as any)
          },
          { text: "OK", style: "default" }
        ]
      );
      return;
    }

    const next = !isOnline;
    setIsOnline(next);
    try {
      await updateDriverStatus(next);
    } catch (err: any) {
      console.log('Toggle online error:', err);
      setIsOnline(!next);

      // 402 = no subscription
      if (err.response?.status === 402) {
        Alert.alert(
          "Spinr Pass Required",
          err.response?.data?.detail || "You need an active subscription to go online.",
          [
            { text: "Subscribe", onPress: () => router.push('/driver/subscription' as any) },
            { text: "Cancel", style: "cancel" },
          ]
        );
      } else {
        Alert.alert(
          "Cannot Go Online",
          err.response?.data?.detail || "Failed to update status. Please try again."
        );
      }
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

  // ─── Fetch active ride on mount ──────────────────────────────────
  useEffect(() => {
    if (isOnline) {
      fetchActiveRide();
      fetchEarnings('today');
    }
  }, [isOnline]);

  // ─── Push Notifications Setup ────────────────────────────────────
  useEffect(() => {
    if (!isOnline) return;
    // Skip entirely in Expo Go — push APIs were removed from Expo Go in SDK 53.
    if (!Notifications) {
      console.log('[Push] Skipping — Notifications not available (Expo Go)');
      return;
    }

    const setupNotifications = async () => {
      const { status: existingStatus } = await Notifications.getPermissionsAsync();
      let finalStatus = existingStatus;

      if (existingStatus !== 'granted') {
        const { status } = await Notifications.requestPermissionsAsync();
        finalStatus = status;
      }

      if (finalStatus !== 'granted') {
        console.log('Push notification permission not granted');
        return;
      }

      const token = await Notifications.getExpoPushTokenAsync();
      console.log('Push token:', token.data);

      try {
        await api.post('/drivers/push-token', {
          push_token: token.data,
          platform: Platform.OS,
        });
      } catch (err) {
        console.log('Failed to register push token:', err);
      }
    };

    setupNotifications();

    const notificationListener = Notifications.addNotificationReceivedListener((notification: any) => {
      const data = notification.request.content.data;
      console.log('Push notification received:', data);

      if (data?.type === 'new_ride_offer') {
        Vibration.vibrate([0, 500, 200, 500]);
        fetchActiveRide();
      }
    });

    const responseListener = Notifications.addNotificationResponseReceivedListener((response: any) => {
      const data = response.notification.request.content.data;
      console.log('Notification tapped:', data);

      if (data?.ride_id) {
        fetchActiveRide();
      }
    });

    return () => {
      notificationListener.remove();
      responseListener.remove();
    };
  }, [isOnline]);

  return {
    // State
    isOnline,
    location,
    otpInput,
    setOtpInput,

    // Actions
    toggleOnline,
    openNavigation,
    uploadLocationBatch,

    // Refs
    mapRef,
    currentRegionRef,
    wsRef,
    locationSubRef,
    countdownRef,
    reconnectTimeoutRef,
    reconnectAttemptRef,
    locationBufferRef,

    // Animations
    pulseAnim,
    slideUpAnim,
    fadeAnim,
  };
};

export default useDriverDashboard;
