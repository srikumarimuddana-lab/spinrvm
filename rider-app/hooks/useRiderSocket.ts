import { useEffect, useRef, useCallback, useState } from 'react';
import { AppState, Vibration } from 'react-native';
import { showToast } from '../store/toastStore';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useRideStore } from '../store/rideStore';
import { API_URL } from '@shared/config';
import { RideStatus } from '../constants/rideStatus';

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
 * | `route_finalized`       | Refetch ride so finalized segments/quality/snapshot show|
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

  const [connectionState, _setConnectionState] = useState<RiderSocketState>('disconnected');
  const setConnectionState = useCallback((s: RiderSocketState) => {
    _setConnectionState(s);
    useRideStore.getState().setWsConnected(s === 'connected');
  }, []);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Generation counter to serialise connect() attempts. connect() awaits
  // ensureFreshToken() before creating the socket, so wsRef.current stays null
  // across that await window — two near-simultaneous triggers (mount effect +
  // AppState foreground during a token-expiry reconnect) could otherwise both
  // pass the OPEN/CONNECTING check and open duplicate sockets. Each connect()
  // claims a generation up front and only proceeds if it's still the latest
  // after the await; disconnect() bumps the counter to cancel any in-flight
  // attempt.
  const connectGenRef = useRef(0);
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
            data.eta_seconds ?? null,
          );
        }
        break;

      // ── Ride lifecycle transitions ──────────────────────────────
      // Apply the status optimistically first so the UI transitions
      // instantly, then fetchRide fills in the full details (driver
      // info, fare, etc.). Without the optimistic update the rider
      // stays on "searching" for 1-2s while the HTTP call completes.
      case 'driver_accepted':
        if (rideId) {
          applyRideStatusFromWS(rideId, RideStatus.DRIVER_ACCEPTED);
          fetchRide(rideId);
        }
        break;

      case 'driver_arrived':
        Vibration.vibrate([0, 300, 150, 300]);
        if (rideId) {
          applyRideStatusFromWS(rideId, RideStatus.DRIVER_ARRIVED);
          fetchRide(rideId);
        }
        break;

      case 'ride_started':
        if (rideId) {
          applyRideStatusFromWS(rideId, RideStatus.IN_PROGRESS);
          fetchRide(rideId);
        }
        break;

      case 'ride_completed':
        if (rideId) {
          applyRideStatusFromWS(rideId, RideStatus.COMPLETED, {
            total_fare: data.total_fare,
            grand_total: data.grand_total,
          });
          fetchRide(rideId);
        }
        break;

      // Route finalization (Contract A). Emitted a few seconds after completion,
      // once the durable route geometry + quality + snapshot are persisted. The
      // ride-completed / ride-details screens fetched the route at mount (before
      // finalization) so without this they show the "still processing" copy and
      // planned-dashed line forever. Refetch so the real segments/quality/
      // snapshot render; the store guards against refetch loops by revision and
      // no-ops when the finalized ride isn't the one on screen.
      case 'route_finalized':
        if (data.ride_id) {
          useRideStore.getState().handleRouteFinalizedFromWS(
            data.ride_id,
            Number(data.route_revision ?? 0),
          );
        }
        break;

      case 'ride_cancelled': {
        const currentId = useRideStore.getState().currentRide?.id;
        if (data.ride_id && currentId && data.ride_id !== currentId) break;
        // Rider cancelled this ride locally — the cancel button already cleared
        // the ride and navigated home. The server echoes ride_cancelled back to
        // confirm; handling it again here re-shows the toast and re-navigates,
        // restarting the toast's enter animation (the flicker the rider sees).
        // _clearedRideId marks a ride the client already finished with locally,
        // so skip the duplicate handling. Driver/auto/no-show cancels leave the
        // ride active locally, so they fall through and notify as before.
        if (data.ride_id && useRideStore.getState()._clearedRideId === data.ride_id) break;
        const cancelMessages: Record<string, string> = {
          driver_cancelled: 'Your driver has cancelled the ride. We apologize for the inconvenience.',
          rider_cancelled: 'Your ride has been cancelled.',
          noshow: `You were marked as a no-show. A $${data.noshow_fee?.toFixed(2) ?? '4.50'} fee has been charged.`,
          auto_cancelled: 'No drivers were available. Please try again.',
        };
        showToast(
          'Ride Cancelled',
          cancelMessages[data.reason] || 'Your ride has been cancelled.',
          data.reason === 'noshow' ? 'danger' : 'warning',
        );
        clearRide();
        router.replace('/(tabs)' as any);
        break;
      }

      // Driver didn't respond in time — backend is re-dispatching.
      // R-P1-16: Show Alert first so the rider knows what happened,
      // then refetch so the UI transitions back to "searching".
      case 'driver_timeout':
        showToast(
          'Driver Unavailable',
          'The driver did not respond in time. Finding another driver\u2026',
          'info',
        );
        if (rideId) fetchRide(rideId);
        break;

      // Generic status change (catch-all from the backend's
      // ride_status_update handler at websocket.py:236-245).
      case 'ride_status_changed':
        if (data.ride_id && data.status) {
          // Forward the server-authoritative monotonic ride version (V3, issue
          // #11) so the store can drop stale / out-of-order events. Omitted when
          // the backend didn't stamp one (older backend) — the store then
          // applies unconditionally, exactly as before.
          applyRideStatusFromWS(
            data.ride_id,
            data.status,
            typeof data.version === 'number' ? { version: data.version } : undefined,
          );
          fetchRide(data.ride_id);
        }
        break;

      // Chat — push into the store so the chat screen updates live.
      case 'chat_message':
        if (typeof data.text === 'string' && typeof data.sender === 'string') {
          useRideStore.getState().addChatMessage(data as import('../store/rideStore').ChatMessage);
          Vibration.vibrate(100);
        } else {
          console.warn('[WS] Malformed chat_message payload:', data);
        }
        break;

      case 'auth_success':
        break;

      case 'pong':
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
  const connect = useCallback(async () => {
    // Bail synchronously if a socket is already live or connecting.
    if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) {
      return;
    }
    // Claim a generation. If a newer connect() or a disconnect() bumps this
    // before our await resolves, we abort instead of opening a stale/duplicate
    // socket.
    const myGen = ++connectGenRef.current;

    // Refresh the access token proactively before opening the socket.
    // Without this, a reconnect after token expiry (~15 min) sends the
    // old JWT — the server rejects auth, onclose fires immediately, and
    // the reconnect loop spins forever with a stale token, freezing the
    // live driver position on the rider's map.
    try {
      const { ensureFreshToken } = require('@shared/api/client');
      await ensureFreshToken();
    } catch (err) {
      console.warn('[WS] ensureFreshToken failed, proceeding with current token:', err);
    }

    // Superseded by a newer connect() or cancelled by disconnect() during the
    // token refresh above — abort.
    if (myGen !== connectGenRef.current) return;

    const userId = userIdRef.current;
    const rideId = rideIdRef.current;
    if (!userId || !rideId) return;

    const token = useAuthStore.getState().token;
    if (!token) {
      console.log('[WS] Cannot connect: no auth token');
      return;
    }

    const wsScheme = API_URL.startsWith('https') ? 'wss' : 'ws';
    const wsUrl = `${API_URL.replace(/^https?/, wsScheme)}/ws/rider/${userId}`;
    // Guard: an empty/scheme-less API_URL produces "/ws/rider/..." which the
    // native WebSocket module rejects with a FATAL IllegalArgumentException
    // ("Expected URL scheme 'http'/'https' but no colon was found"), taking the
    // whole app down on launch. Skip connecting (polling remains the fallback)
    // rather than crash; reconnect once a valid URL is configured.
    if (!/^wss?:\/\/.+/.test(wsUrl)) {
      console.error('[WS] Backend URL not configured — skipping WebSocket connect:', wsUrl);
      setConnectionState('disconnected');
      return;
    }
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
      // Re-sync ride state: any events sent while disconnected are not
      // buffered by the server, so pull from the HTTP source of truth.
      const rideId = rideIdRef.current;
      if (rideId) {
        useRideStore.getState().fetchRide(rideId);
      }
    };

    ws.onmessage = (event) => {
      try {
        let data = JSON.parse(event.data);
        // Unwrap sequenced envelope from ws_pubsub ({"seq": N, "data": {...}})
        if (data && typeof data === 'object' && 'seq' in data && 'data' in data) {
          data = data.data;
        }
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
    // Bump the generation so any connect() awaiting ensureFreshToken (no socket
    // created yet, so closing wsRef wouldn't reach it) aborts when it resumes.
    connectGenRef.current++;
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

  const wsConnected = connectionState === 'connected';
  return { connectionState, wsConnected, sendMessage };
}

export default useRiderSocket;
