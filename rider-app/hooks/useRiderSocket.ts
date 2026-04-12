import { useEffect, useRef, useCallback, useState } from 'react';
import { AppState, Alert, Vibration } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useRideStore } from '../store/rideStore';
import { API_URL } from '@shared/config';

/**
 * Real-time WebSocket client for the rider app.
 *
 * Connects to `/ws/rider/{userId}` when the rider has an active ride,
 * authenticates with the JWT, and routes incoming server messages
 * directly into `useRideStore` so the ride-flow screens update
 * instantly instead of waiting for the 15 s poll fallback.
 *
 * **Incoming message types handled:**
 *
 * | type                    | Effect                                                |
 * |-------------------------|-------------------------------------------------------|
 * | `ping`                  | Reply `pong` (heartbeat keepalive)                    |
 * | `driver_location_update`| Update `currentDriver.{lat,lng}` in the store         |
 * | `driver_accepted`       | Fetch full ride → screens transition to driver-arriving|
 * | `driver_arrived`        | Same — screens transition to driver-arrived            |
 * | `ride_started`          | Same — screens transition to ride-in-progress          |
 * | `ride_completed`        | Same — screens transition to ride-completed             |
 * | `ride_cancelled`        | Clear ride + alert                                     |
 * | `ride_status_changed`   | Generic catch-all: apply status + fetchRide fallback   |
 * | `chat_message`          | Log (chat screen polls its own messages for now)       |
 *
 * **Reconnection:** exponential backoff [1s, 2s, 5s, 10s, 30s] with
 * ±500 ms jitter. Reconnects automatically on AppState `active`.
 */

export type RiderSocketState = 'connected' | 'reconnecting' | 'disconnected';

const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000];

export function useRiderSocket() {
  const user = useAuthStore((s) => s.user);
  const currentRide = useRideStore((s) => s.currentRide);
  const router = useRouter();

  const [connectionState, setConnectionState] = useState<RiderSocketState>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Refs to avoid stale closures inside callbacks. Updated via effects.
  const userIdRef = useRef<string | null>(null);
  const rideIdRef = useRef<string | null>(null);

  // Keep refs in sync.
  useEffect(() => { userIdRef.current = user?.id ?? null; }, [user?.id]);
  useEffect(() => { rideIdRef.current = currentRide?.id ?? null; }, [currentRide?.id]);

  // ── Message handler ─────────────────────────────────────────────
  const handleMessage = useCallback((data: any) => {
    const { fetchRide, updateDriverLocation, applyRideStatusFromWS, clearRide } = useRideStore.getState();
    const rideId = rideIdRef.current;

    switch (data.type) {
      // Heartbeat
      case 'ping':
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'pong' }));
        }
        break;

      // ── Real-time driver position (most frequent message) ───────
      case 'driver_location_update':
        if (data.lat != null && data.lng != null) {
          updateDriverLocation(
            data.lat,
            data.lng,
            data.speed ?? null,
            data.heading ?? null,
          );
        }
        break;

      // ── Ride lifecycle transitions ──────────────────────────────
      case 'driver_accepted':
        if (rideId) fetchRide(rideId);
        break;

      case 'driver_arrived':
        Vibration.vibrate([0, 300, 150, 300]);
        if (rideId) fetchRide(rideId);
        break;

      case 'ride_started':
        if (rideId) fetchRide(rideId);
        break;

      case 'ride_completed':
        if (rideId) {
          applyRideStatusFromWS(rideId, 'completed', {
            total_fare: data.total_fare,
          });
          fetchRide(rideId);
        }
        break;

      case 'ride_cancelled':
        Alert.alert(
          'Ride Cancelled',
          data.reason || 'Your ride has been cancelled.',
        );
        clearRide();
        break;

      // Generic status change (catch-all from the backend's
      // ride_status_update handler at websocket.py:236-245).
      case 'ride_status_changed':
        if (data.ride_id && data.status) {
          applyRideStatusFromWS(data.ride_id, data.status);
          fetchRide(data.ride_id);
        }
        break;

      // Chat — log only. The chat screen has its own fetcher.
      case 'chat_message':
        console.log('[WS] Chat message received:', data.text?.slice(0, 40));
        break;

      // Auth errors — shouldn't happen after connect, but handle
      // gracefully in case the token expires mid-session.
      case 'error':
        console.log('[WS] Server error:', data.message);
        break;

      default:
        console.log('[WS] Unhandled rider message type:', data.type);
    }
  }, []);

  // ── Connect / disconnect ────────────────────────────────────────
  const connect = useCallback(() => {
    const userId = userIdRef.current;
    const rideId = rideIdRef.current;
    if (!userId || !rideId) return;

    const token = useAuthStore.getState().token;
    if (!token) {
      console.log('[WS] Cannot connect: no auth token');
      return;
    }

    const wsUrl = `${API_URL.replace('http', 'ws')}/ws/rider/${userId}`;
    console.log('[WS] Rider connecting:', wsUrl);
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('[WS] Rider connected, authenticating...');
      reconnectAttemptRef.current = 0;
      setConnectionState('connected');
      ws.send(JSON.stringify({
        type: 'auth',
        token,
        client_type: 'rider',
      }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleMessage(data);
      } catch { /* malformed JSON — ignore */ }
    };

    ws.onerror = (error) => {
      console.log('[WS] Rider error:', error);
    };

    ws.onclose = () => {
      console.log('[WS] Rider closed');
      // Only reconnect if we still have a ride + user.
      if (userIdRef.current && rideIdRef.current) {
        setConnectionState('reconnecting');
        const baseDelay = RECONNECT_DELAYS[
          Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS.length - 1)
        ];
        const jitter = Math.random() * 1000 - 500;
        const delay = Math.max(500, baseDelay + jitter);
        console.log(`[WS] Rider reconnecting in ${Math.round(delay)}ms (attempt ${reconnectAttemptRef.current + 1})`);
        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectAttemptRef.current++;
          connect();
        }, delay);
      } else {
        setConnectionState('disconnected');
      }
    };
  }, [handleMessage]);

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnectionState('disconnected');
  }, []);

  // ── Lifecycle: connect when ride starts, disconnect when it ends ─
  useEffect(() => {
    if (!user?.id || !currentRide?.id) {
      disconnect();
      return;
    }
    connect();
    return () => disconnect();
  }, [user?.id, currentRide?.id, connect, disconnect]);

  // ── Foreground reconnect ────────────────────────────────────────
  useEffect(() => {
    const sub = AppState.addEventListener('change', (nextState) => {
      if (
        nextState === 'active' &&
        userIdRef.current &&
        rideIdRef.current
      ) {
        const ws = wsRef.current;
        if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
          console.log('[WS] App foregrounded — rider reconnecting');
          if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
          }
          reconnectAttemptRef.current = 0;
          connect();
        }
      }
    });
    return () => sub.remove();
  }, [connect]);

  // ── Public API ──────────────────────────────────────────────────
  const sendMessage = useCallback((msg: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  return { connectionState, sendMessage };
}

export default useRiderSocket;
