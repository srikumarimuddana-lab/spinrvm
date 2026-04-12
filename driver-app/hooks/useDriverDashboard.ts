import { useState, useEffect, useRef, useCallback } from 'react';
import { Animated } from 'react-native';
import * as Location from 'expo-location';
import { Platform, Alert, Vibration, Linking, AppState } from 'react-native';

export type ConnectionState = 'connected' | 'reconnecting' | 'disconnected';
import { router } from 'expo-router';

import { useAuthStore } from '@shared/store/authStore';
import { useDriverStore } from '../store/driverStore';
import api from '@shared/api/client';
import { API_URL } from '@shared/config';
import { onForegroundMessage } from '@shared/services/firebase';
import { Dimensions } from 'react-native';

const { height } = Dimensions.get('window');
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000];

interface UseDriverDashboardReturn {
  // State
  isOnline: boolean;
  connectionState: ConnectionState;
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
  const { user, driver: driverData, updateDriverStatus, refreshProfile } = useAuthStore();
  const {
    rideState,
    incomingRide,
    activeRide,
    setIncomingRide,
    resetRideState,
    fetchActiveRide,
    fetchEarnings,
    applyDriverConfig,
    earnings,
  } = useDriverStore();

  // State
  const [isOnline, setIsOnline] = useState(driverData?.is_online || false);
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
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
  // Refs used inside WebSocket callbacks to avoid stale closure values
  const isOnlineRef = useRef(isOnline);
  const locationRef = useRef<Location.LocationObject | null>(null);

  // Animations
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const slideUpAnim = useRef(new Animated.Value(height)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // Keep refs in sync so WebSocket callbacks always see current values
  useEffect(() => { isOnlineRef.current = isOnline; }, [isOnline]);

  // ─── Animate ActiveRidePanel in/out based on rideState ──────────
  // The panel starts at translateY=screenHeight + opacity=0 (invisible).
  // Without this animation, the panel IS mounted in the React tree and
  // HAS the correct ride data + buttons, but is pushed off-screen and
  // invisible — which is why drivers saw a "blank map" after accepting.
  useEffect(() => {
    const isActive = rideState === 'navigating_to_pickup' ||
                     rideState === 'arrived_at_pickup' ||
                     rideState === 'trip_in_progress';
    if (isActive) {
      Animated.parallel([
        Animated.spring(slideUpAnim, {
          toValue: 0,
          useNativeDriver: true,
          friction: 8,
        }),
        Animated.timing(fadeAnim, {
          toValue: 1,
          duration: 300,
          useNativeDriver: true,
        }),
      ]).start();
    } else {
      // Reset to hidden when not in an active ride state
      slideUpAnim.setValue(height);
      fadeAnim.setValue(0);
    }
  }, [rideState]);

  // ─── Refresh user + driver profile on mount and on foreground ────
  // The Zustand store only pulls /auth/me on app init, so admin-side
  // changes (e.g. flipping is_verified or the derived onboarding status)
  // never reach a long-running session. That stale state silently
  // disables the GO button because canGoOnline depends on
  // driver_onboarding_status === 'verified' AND driver.is_verified.
  useEffect(() => {
    refreshProfile();
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') refreshProfile();
    });
    return () => sub.remove();
  }, [refreshProfile]);

  // ─── Fetch driver operational config from backend ────────────────
  // `GET /drivers/config` returns server-tuned values for the
  // ride-offer countdown and the pickup-geofence radius. Fetched once
  // per authenticated session — if it fails the store keeps its
  // module-level fallbacks (15s countdown, 100m pickup radius) so the
  // driver flow never breaks on a transient backend hiccup.
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get('/drivers/config');
        if (cancelled) return;
        applyDriverConfig(res.data || {});
      } catch (e) {
        console.log('[driver-config] fetch failed, using fallbacks:', e);
      }
    })();
    return () => { cancelled = true; };
  }, [user, applyDriverConfig]);

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
        if (lastKnown) { setLocation(lastKnown); locationRef.current = lastKnown; }
      } catch {}

      // 2. Accurate position (non-blocking)
      Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced })
        .then(loc => {
          setLocation(loc);
          locationRef.current = loc;
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
          locationRef.current = loc;

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
    if (!isOnlineRef.current || !user) return;

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
      setConnectionState('connected');
      const currentToken = useAuthStore.getState().token;
      ws.send(JSON.stringify({
        type: 'auth',
        token: currentToken,
        client_type: 'driver',
      }));
      // Re-send last known location so backend has fresh position after reconnect
      const loc = locationRef.current;
      if (loc) {
        ws.send(JSON.stringify({
          type: 'driver_location',
          lat: loc.coords.latitude,
          lng: loc.coords.longitude,
          speed: loc.coords.speed ?? null,
          heading: loc.coords.heading ?? null,
          accuracy: loc.coords.accuracy ?? null,
          altitude: loc.coords.altitude ?? null,
          ride_id: null,
          tracking_phase: 'online_idle',
        }));
      }
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
      // Use ref — not the closure value — so going offline stops reconnects immediately
      if (isOnlineRef.current && user) {
        setConnectionState('reconnecting');
        const baseDelay = RECONNECT_DELAYS[Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS.length - 1)];
        const jitter = Math.random() * 1000 - 500; // ±500 ms
        const delay = Math.max(500, baseDelay + jitter);
        console.log(`Reconnecting in ${Math.round(delay)}ms (attempt ${reconnectAttemptRef.current + 1})`);
        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectAttemptRef.current++;
          connectWebSocket();
        }, delay);
      } else {
        setConnectionState('disconnected');
      }
    };
  }, [user, handleWSMessage]);

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
      setConnectionState('disconnected');
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
  }, [isOnline, user, connectWebSocket]);

  // Re-connect when app returns to foreground (mobile networks drop on background)
  useEffect(() => {
    const sub = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active' && isOnlineRef.current && user) {
        const ws = wsRef.current;
        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
          console.log('App foregrounded — reconnecting WebSocket');
          if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
          }
          reconnectAttemptRef.current = 0;
          connectWebSocket();
        }
      }
    });
    return () => sub.remove();
  }, [user, connectWebSocket]);

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

  // ─── Foreground FCM Message Handler ──────────────────────────────
  // FCM token registration + permissions live in app/_layout.tsx via
  // @shared/services/firebase. Here we only subscribe to foreground
  // messages so we can bridge them into the driver store — ride offers
  // that arrive via FCM (e.g. when the WebSocket connection is stale
  // or the device was briefly backgrounded) follow the same path as
  // the WebSocket `new_ride_assignment` handler above.
  //
  // Background / quit-state FCM messages are handled by the top-level
  // setBackgroundMessageHandler in _layout.tsx and surfaced as OS
  // notifications via the `ride-offers` Android channel.
  useEffect(() => {
    if (!isOnline || !user) return;

    const unsubscribe = onForegroundMessage((remoteMessage: any) => {
      const data = remoteMessage?.data || {};
      console.log('[Push] Driver foreground FCM:', data?.type || remoteMessage?.notification?.title);

      if (data?.type === 'new_ride_assignment' && data?.ride_id) {
        Vibration.vibrate([0, 500, 200, 500]);
        // Hydrate an incoming ride offer from the FCM payload. Backend
        // sends coordinates/addresses as strings in the `data` field
        // (FCM data-only messages are all string-typed).
        setIncomingRide({
          ride_id: data.ride_id,
          pickup_address: data.pickup_address || '',
          dropoff_address: data.dropoff_address || '',
          pickup_lat: parseFloat(data.pickup_lat || '0'),
          pickup_lng: parseFloat(data.pickup_lng || '0'),
          dropoff_lat: parseFloat(data.dropoff_lat || '0'),
          dropoff_lng: parseFloat(data.dropoff_lng || '0'),
          fare: parseFloat(data.fare || '0'),
          distance_km: data.distance_km ? parseFloat(data.distance_km) : undefined,
          duration_minutes: data.duration_minutes ? parseFloat(data.duration_minutes) : undefined,
          rider_name: data.rider_name,
          rider_rating: data.rider_rating ? parseFloat(data.rider_rating) : undefined,
        });
      } else if (data?.type === 'ride_cancelled') {
        Alert.alert('Ride Cancelled', 'The rider has cancelled this ride.');
        resetRideState();
      }
    });

    return () => {
      if (typeof unsubscribe === 'function') unsubscribe();
    };
  }, [isOnline, user, setIncomingRide, resetRideState]);

  return {
    // State
    isOnline,
    connectionState,
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
