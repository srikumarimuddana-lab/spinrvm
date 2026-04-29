import { useState, useEffect, useRef, useCallback } from 'react';
import { Animated } from 'react-native';
import * as Location from 'expo-location';
import { Platform, Vibration, Linking, AppState } from 'react-native';

export type ConnectionState = 'connected' | 'reconnecting' | 'disconnected';
import { router } from 'expo-router';

import { useAuthStore } from '@shared/store/authStore';
import { useDriverStore } from '../store/driverStore';
import api from '@shared/api/client';
import { useDriverConfig } from '@shared/hooks/queries';
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
  wsError: string | null;

  // Alert state
  dashAlert: {
    visible: boolean; title: string; message?: string;
    variant: 'info' | 'success' | 'danger' | 'warning';
    buttons?: Array<{ text: string; style?: 'default'|'cancel'|'destructive'; onPress?: () => void }>;
  };
  showDashAlert: (
    title: string,
    message: string,
    variant?: 'info'|'success'|'danger'|'warning',
    buttons?: Array<{ text: string; style?: 'default'|'cancel'|'destructive'; onPress?: () => void }>
  ) => void;
  closeDashAlert: () => void;

  // Actions
  toggleOnline: () => Promise<void>;
  openNavigation: (lat: number, lng: number, label: string) => void;
  uploadLocationBatch: () => Promise<void>;
  refreshLocation: (useCache: boolean) => Promise<Location.LocationObject | null>;

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
    hydrateDriverRideState,
    fetchEarnings,
    applyDriverConfig,
    earnings,
  } = useDriverStore();

  // State
  const [isOnline, setIsOnline] = useState(driverData?.is_online || false);
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const [location, setLocation] = useState<Location.LocationObject | null>(null);
  const [otpInput, setOtpInput] = useState('');
  const [wsError, setWsError] = useState<string | null>(null);

  // Alert state (replaces Alert.alert() so consumers can render <CustomAlert>)
  const [dashAlert, setDashAlert] = useState<{
    visible: boolean; title: string; message?: string;
    variant: 'info' | 'success' | 'danger' | 'warning';
    buttons?: Array<{ text: string; style?: 'default'|'cancel'|'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', variant: 'info' });

  const showDashAlert = (
    title: string,
    message: string,
    variant: 'info'|'success'|'danger'|'warning' = 'info',
    buttons?: Array<{ text: string; style?: 'default'|'cancel'|'destructive'; onPress?: () => void }>
  ) => setDashAlert({ visible: true, title, message, variant, buttons });

  const closeDashAlert = () => setDashAlert(a => ({ ...a, visible: false }));

  // Refs
  const mapRef = useRef<any>(null);
  const currentRegionRef = useRef({ latitudeDelta: 0.04, longitudeDelta: 0.04 });
  const wsRef = useRef<WebSocket | null>(null);
  const locationSubRef = useRef<Location.LocationSubscription | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const locationBufferRef = useRef<any[]>([]);
  const locationRetryCountRef = useRef(0);
  const MAX_LOCATION_RETRIES = 3;
  // Refs used inside WebSocket callbacks to avoid stale closure values.
  // Also used to stabilize the connectWebSocket useCallback — if we closed
  // over `user` / `handleWSMessage` directly, any store state change would
  // recreate the callback, re-fire the mount effect, and tear down the
  // socket (observed as a perpetual "Reconnecting…" banner).
  const isOnlineRef = useRef(isOnline);
  const locationRef = useRef<Location.LocationObject | null>(null);
  // Throttle setLocation re-renders: map updates at most every 10 s.
  // WS payloads still fire every watchPositionAsync callback (~5 s).
  const lastRenderMsRef = useRef<number>(0);
  const userRef = useRef(user);
  useEffect(() => { userRef.current = user; }, [user]);

  // Animations
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const slideUpAnim = useRef(new Animated.Value(height)).current;
  const fadeAnim = useRef(new Animated.Value(0)).current;

  // Keep refs in sync so WebSocket callbacks always see current values
  useEffect(() => { isOnlineRef.current = isOnline; }, [isOnline]);

  // ─── Animate ActiveRidePanel in/out based on rideState ──────────
  // Track whether the first active-ride state transition has already
  // happened. On mount, if the store already has an active ride (hydrated
  // or fetched from the API before this effect fires), we snap the panel
  // to its final position instead of animating from off-screen — otherwise
  // the driver sees a blank map for ~500ms while the panel slides up.
  const hasDoneInitialActiveAnim = useRef(false);

  useEffect(() => {
    const isActive = rideState === 'navigating_to_pickup' ||
                     rideState === 'arrived_at_pickup' ||
                     rideState === 'trip_in_progress';
    if (isActive) {
      if (!hasDoneInitialActiveAnim.current) {
        // First time the panel becomes active — snap into view immediately
        // so there's no blank-map window when state is restored on cold start.
        hasDoneInitialActiveAnim.current = true;
        slideUpAnim.setValue(0);
        fadeAnim.setValue(1);
      } else {
        // Subsequent transitions (user accepted a new ride) — animate in.
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
      }
    } else {
      // Reset to hidden when not in an active ride state
      hasDoneInitialActiveAnim.current = false;
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

  // ─── Driver operational config — TanStack Query owned ────────────
  // `useDriverConfig` is a long-staleTime hook: server-tuned values for
  // the ride-offer countdown and pickup-geofence radius. Persisted cache
  // means a cold start applies the last-known config without a network
  // round-trip. When a fresh value lands we mirror it into the driver
  // store so existing consumers keep reading from the same place.
  // On error the store retains its module-level fallbacks (15s countdown,
  // 100m pickup radius) so the flow never breaks on a transient hiccup.
  const driverConfigQuery = useDriverConfig();
  useEffect(() => {
    if (driverConfigQuery.data) applyDriverConfig(driverConfigQuery.data);
  }, [driverConfigQuery.data, applyDriverConfig]);

  // ─── Location Tracking ───────────────────────────────────────────
  // Refresh on mount AND whenever the app returns to the foreground so
  // the map doesn't keep showing a stale fix from the previous session
  // (e.g. driver left home yesterday, opened app at work — without the
  // AppState refresh the map would still point at home until
  // watchPositionAsync starts after Go Online).
  const refreshLocation = useCallback(async (useCache: boolean) => {
    if (useCache) {
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        const saved = await AsyncStorage.getItem('spinr_driver_last_location');
        if (saved) {
          const { lat, lng } = JSON.parse(saved);
          setLocation({ coords: { latitude: lat, longitude: lng, heading: 0, speed: 0, accuracy: 100, altitude: 0 }, timestamp: Date.now() } as any);
        }
      } catch {}
    }

    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== 'granted') return null;

    if (useCache) {
      try {
        const lastKnown = await Location.getLastKnownPositionAsync();
        if (lastKnown) { setLocation(lastKnown); locationRef.current = lastKnown; }
      } catch {}
    }

    try {
      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      setLocation(loc);
      locationRef.current = loc;
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        AsyncStorage.setItem('spinr_driver_last_location', JSON.stringify({ lat: loc.coords.latitude, lng: loc.coords.longitude }));
      } catch {}
      return loc;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    refreshLocation(true);
    const sub = AppState.addEventListener('change', (next) => {
      // Skip cache on resume — we want a fresh fix, not yesterday's.
      if (next === 'active') refreshLocation(false);
    });
    return () => sub.remove();
  }, [refreshLocation]);

  // ─── Batch Location Upload ───────────────────────────────────────
  // G16: Persist buffer to AsyncStorage so crash doesn't lose GPS
  // breadcrumbs. On upload success, clear both in-memory + persisted.
  // On cold start, the buffer initializes from in-memory (empty) but
  // a recovery effect below loads any persisted points.
  const LOCATION_BUFFER_KEY = 'spinr_location_buffer';

  const uploadLocationBatch = useCallback(async () => {
    if (locationBufferRef.current.length === 0) return;

    const pointsToUpload = [...locationBufferRef.current];
    locationBufferRef.current = [];

    try {
      await api.post('/drivers/location-batch', {
        points: pointsToUpload,
      });
      locationRetryCountRef.current = 0;
      // Clear persisted buffer on success
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        await AsyncStorage.removeItem(LOCATION_BUFFER_KEY);
      } catch {}
    } catch (err) {
      locationRetryCountRef.current += 1;
      if (locationRetryCountRef.current >= MAX_LOCATION_RETRIES) {
        console.warn(`[Location] Batch upload failed after ${MAX_LOCATION_RETRIES} retries — clearing buffer`);
        locationRetryCountRef.current = 0;
        locationBufferRef.current = [];
        try {
          const AsyncStorage = require('@react-native-async-storage/async-storage').default;
          await AsyncStorage.removeItem(LOCATION_BUFFER_KEY);
        } catch {}
      } else {
        locationBufferRef.current = [...pointsToUpload, ...locationBufferRef.current];
        // Persist to AsyncStorage so crash doesn't lose them
        try {
          const AsyncStorage = require('@react-native-async-storage/async-storage').default;
          await AsyncStorage.setItem(LOCATION_BUFFER_KEY, JSON.stringify(locationBufferRef.current.slice(-500)));
        } catch {}
      }
    }
  }, []);

  // Recover persisted location buffer on cold start (G16)
  useEffect(() => {
    (async () => {
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        const saved = await AsyncStorage.getItem(LOCATION_BUFFER_KEY);
        if (saved) {
          const points = JSON.parse(saved);
          if (Array.isArray(points) && points.length > 0) {
            locationBufferRef.current = [...points, ...locationBufferRef.current];
          }
        }
      } catch {}
    })();
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
          locationRef.current = loc;
          const now = Date.now();
          if (now - lastRenderMsRef.current >= 10000) {
            lastRenderMsRef.current = now;
            setLocation(loc);
          }

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
        showDashAlert(
          'Ride Cancelled',
          data.is_auto
            ? 'No driver was available — the ride was automatically cancelled.'
            : 'The rider has cancelled this ride.',
          'warning'
        );
        resetRideState();
        break;

      // G20: Reply to server heartbeat so the backend doesn't mark
      // this connection as dead after HEARTBEAT_TIMEOUT (10s).
      case 'ping':
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'pong' }));
        }
        break;

      // G3: Rider-to-driver chat messages. Backend websocket.py forwards
      // chat_message to `driver_{user_id}`. Push into driverStore so
      // chat.tsx updates in real-time without polling. [SPR-01]
      case 'chat_message':
        if (
          data.text !== undefined &&
          typeof data.text === 'string' &&
          typeof data.sender === 'string'
        ) {
          useDriverStore.getState().addChatMessage(data);
          Vibration.vibrate(100);
        } else {
          console.warn('[WS] Malformed chat message payload:', data);
        }
        break;

      // Rider tipped a completed ride. Backend rides.py emits this right
      // after persisting the tip so the driver sees it immediately
      // instead of only after the next earnings refresh.
      case 'tip_received': {
        const amount = typeof data.amount === 'number' ? data.amount : 0;
        if (amount > 0) {
          Vibration.vibrate([0, 200, 100, 200]);
          const riderName = typeof data.rider_name === 'string' && data.rider_name
            ? data.rider_name
            : 'Your rider';
          showDashAlert(
            'You got a tip! 💸',
            `${riderName} tipped you $${amount.toFixed(2)}.`,
            'success',
          );
          // Pull the fresh totals so the earnings chip updates on screen.
          fetchEarnings('today');
        }
        break;
      }

      // Unified status event from socket_manager.broadcast_ride_status().
      // Currently emitted to drivers on admin-initiated cancellations.
      // Handlers are idempotent: specific events (ride_cancelled, etc.)
      // may already have acted; these are reconciliation / catch-up paths.
      case 'ride_status_changed': {
        const status = data.status as string | undefined;
        if (status === 'cancelled') {
          resetRideState();
        } else if (status === 'completed' && typeof data.total_fare === 'number') {
          fetchEarnings('today');
        }
        break;
      }

      default:
        console.warn('[WS] Unknown message type received:', data.type);
        break;
    }
  }, [setIncomingRide, resetRideState]);

  // Stable ref to the message handler so connectWebSocket's identity
  // doesn't flip every time the handler closure changes.
  const handleWSMessageRef = useRef(handleWSMessage);
  useEffect(() => { handleWSMessageRef.current = handleWSMessage; }, [handleWSMessage]);

  // ─── WebSocket Connection ────────────────────────────────────────
  // Empty dep array: this callback reads everything through refs (userRef,
  // isOnlineRef, locationRef, handleWSMessageRef) so its identity is stable
  // for the lifetime of the hook. Previously this depended on [user,
  // handleWSMessage] and recreated on unrelated store changes — the mount
  // effect saw a "new" connectWebSocket, closed the socket, and reconnected,
  // leaving the banner stuck on "Reconnecting…".
  const connectWebSocket = useCallback(() => {
    const currentUser = userRef.current;
    if (!isOnlineRef.current || !currentUser) return;

    const token = useAuthStore.getState().token;
    if (!token) {
      return;
    }

    // Match URL scheme: https backends (prod or DEV pointing at prod) use wss://,
    // http backends (local dev) use ws://. Checking __DEV__ is wrong — Railway's
    // edge proxy rejects plain ws:// with "Invalid Sec-WebSocket-Accept response".
    const useSecure = API_URL.startsWith('https');
    const wsUrl = `${API_URL.replace(/^https?/, useSecure ? 'wss' : 'ws')}/ws/driver/${currentUser.id}`;
    // Flip the banner to 'reconnecting' the moment we start connecting.
    // The initial state is 'disconnected', which renders as the red
    // "Connection Lost" banner — if we leave it there during the TLS +
    // auth-handshake window (easily 1–2 s on Railway cold starts), the
    // driver sees an alarming banner while the socket is actually coming
    // up fine. 'reconnecting' renders as the amber "Reconnecting…" state
    // which correctly signals work-in-progress.
    setConnectionState('reconnecting');
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    // Don't claim 'connected' at onopen — the backend requires a post-open
    // `auth` message and will close the socket if it fails. We flip to
    // 'connected' on the first non-error server message, which proves auth
    // passed. Until then, stay in 'reconnecting' (or initial 'disconnected').
    // Track per-connection auth state. Set to true only after server confirms
    // auth_success so we never send location before the session is verified.
    let wsAuthenticated = false;

    ws.onopen = () => {
      const currentToken = useAuthStore.getState().token;
      ws.send(JSON.stringify({
        type: 'auth',
        token: currentToken,
        client_type: 'driver',
      }));
      // Location resend is deferred to after auth_success is confirmed in
      // onmessage. Sending it here (before the server has processed auth)
      // could cause the server to close the socket if the message is
      // processed in the brief window before the main receive loop starts.
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'error') {
          setWsError(data.message || 'Connection error');
          return;
        }
        // auth_success is the definitive signal that the backend accepted
        // the connection. Only flip to 'connected' and reset reconnect
        // counter on this specific message — not on every ping/pong/etc.
        if (data.type === 'auth_success' && wsRef.current === ws) {
          const wasReconnect = reconnectAttemptRef.current > 0;
          reconnectAttemptRef.current = 0;
          setConnectionState('connected');
          setWsError(null);
          wsAuthenticated = true;
          // Re-sync active ride state — server buffers no WS events, so any
          // transitions that fired while disconnected are recovered via HTTP.
          if (wasReconnect) {
            fetchActiveRide();
          }
          // Now that auth is confirmed, send the cached location so the
          // backend has a fresh position immediately after reconnect.
          const loc = locationRef.current;
          if (loc && ws.readyState === WebSocket.OPEN) {
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
        }
        handleWSMessageRef.current(data);
      } catch { }
    };

    ws.onerror = (_error) => {
    };

    ws.onclose = (event) => {
      if (isOnlineRef.current && userRef.current) {
        setConnectionState('reconnecting');
        const baseDelay = RECONNECT_DELAYS[Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS.length - 1)];
        const jitter = Math.random() * 1000 - 500; // ±500 ms
        const delay = Math.max(500, baseDelay + jitter);
        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectAttemptRef.current++;
          connectWebSocket();
        }, delay);
      } else {
        setConnectionState('disconnected');
      }
    };
  }, []);

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
    // connectWebSocket is stable (empty deps, reads state through refs), so
    // we intentionally leave it out — listing it would tear down the socket
    // on unrelated re-renders. We DO need user.id to trigger reconnect when
    // the signed-in user changes; use the id specifically, not the object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOnline, user?.id]);

  // Uber/Lyft-style presence: proactively close the WebSocket when the
  // app backgrounds so the backend clears Redis presence immediately
  // (via the disconnect handler), instead of waiting for the 90 s TTL
  // to expire. Reconnect on return to foreground. Mobile OSes suspend
  // background sockets silently — without this the driver shows as
  // online to admins and can even receive ride offers after the app
  // was swiped away.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active' && isOnlineRef.current && userRef.current) {
        const ws = wsRef.current;
        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
          if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
          }
          reconnectAttemptRef.current = 0;
          connectWebSocket();
        }
      } else if (nextState === 'background' && wsRef.current) {
        // iOS fires 'inactive' for transient foreground interruptions —
        // permission dialogs (e.g. background-location prompt on Go
        // Online), incoming calls, Control Center, the app switcher
        // preview. Closing the WS on those events caused the backend
        // disconnect handler to flip the driver offline milliseconds
        // after they tapped Go Online (the permission prompt itself
        // triggered a close). Only treat a true 'background' transition
        // as "driver has left the app". 1001 = "going away".
        try {
          wsRef.current.close(1001, 'app_backgrounded');
        } catch {}
      }
    });
    return () => sub.remove();
    // connectWebSocket is stable; user read via ref. Empty deps so this
    // listener is registered exactly once per hook instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Toggle Online/Offline ───────────────────────────────────────
  const toggleOnline = async () => {
    if (!driverData?.vehicle_make || !driverData?.license_plate) {
      showDashAlert(
        "Profile Incomplete",
        "You must provide vehicle details before going online.",
        'warning',
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
      showDashAlert(
        "Account Not Verified",
        "Your account is not verified yet. Please complete your profile and wait for admin approval before going online.",
        'warning',
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
      setIsOnline(!next);

      // 402 = no subscription
      if (err.response?.status === 402) {
        showDashAlert(
          "Spinr Pass Required",
          err.response?.data?.detail || "You need an active subscription to go online.",
          'warning',
          [
            { text: "Subscribe", onPress: () => router.push('/driver/subscription' as any) },
            { text: "Cancel", style: "cancel" },
          ]
        );
      } else {
        showDashAlert(
          "Cannot Go Online",
          err.response?.data?.detail || "Failed to update status. Please try again.",
          'danger'
        );
      }
      return;
    }

    // Ask for background location AFTER the status update resolved.
    // Keeping this in the same try block would roll the UI back to
    // offline on any permission failure even though the backend
    // already has is_online=true — the mismatch drivers see as
    // "tapped Go Online, app shows offline a second later".
    // Android is strict about background-location ("Allow all the
    // time" vs "While using") and reject flows can throw from this
    // call; a rejection should NOT undo a successful Go Online, it
    // should just warn that background tracking is unavailable.
    if (next) {
      try {
        await Location.requestBackgroundPermissionsAsync();
      } catch {
        showDashAlert(
          "Background location needed",
          "You're online, but background location is required to keep getting ride offers while the app is minimized. Enable 'Allow all the time' in Settings.",
          'warning'
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

  // ─── Crash recovery + background-push hydration ──────────────────
  // 1. Check AsyncStorage for a ride offer received while the app was
  //    backgrounded or killed. The background FCM handler in _layout.tsx
  //    writes the full offer to PENDING_OFFER_KEY; we consume it here so
  //    the offer panel appears instantly without a network round-trip.
  // 2. hydrateDriverRideState() restores any persisted active-ride state.
  // 3. fetchActiveRide() confirms live server state and may override both.
  useEffect(() => {
    if (!user) return;
    (async () => {
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        const raw = await AsyncStorage.getItem('spinr_pending_ride_offer');
        if (raw) {
          await AsyncStorage.removeItem('spinr_pending_ride_offer');
          const offer = JSON.parse(raw);
          Vibration.vibrate([0, 500, 200, 500]);
          setIncomingRide(offer);
        }
      } catch (e) {
        console.warn('[Push] Failed to hydrate pending ride offer on mount:', e);
      }
      hydrateDriverRideState().then(() => fetchActiveRide());
    })();
  }, [user]);

  // ─── Fetch earnings when online ─────────────────────────────────
  useEffect(() => {
    if (isOnline) {
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
        showDashAlert('Ride Cancelled', 'The rider has cancelled this ride.', 'warning');
        resetRideState();
      } else if (data?.type === 'subscription_expiring') {
        showDashAlert(
          'Spinr Pass Expiring',
          'Your Spinr Pass expires soon — renew to keep earning',
          'warning',
          [
            { text: 'Renew Now', onPress: () => router.push('/driver/subscription' as any) },
            { text: 'Later', style: 'cancel' },
          ]
        );
      } else if (data?.type === 'document_expiry_warning') {
        showDashAlert(
          'Document Expiring',
          'A document is expiring — tap to update it',
          'warning',
          [
            { text: 'Update Now', onPress: () => router.push('/driver/documents' as any) },
            { text: 'Later', style: 'cancel' },
          ]
        );
      } else if (data?.type) {
        console.warn('[Push] Unknown notification type — navigating to notifications list:', data.type);
        router.push('/driver/notifications' as any);
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
    wsError,

    // Alert state
    dashAlert,
    showDashAlert,
    closeDashAlert,

    // Actions
    toggleOnline,
    openNavigation,
    uploadLocationBatch,
    refreshLocation,

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
