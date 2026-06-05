// src/hooks/use-monitoring-socket.ts
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MonitoringWsEvent } from "@/app/dashboard/monitoring/types";

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

interface UseMonitoringSocketOptions {
    token: string | null;
    onEvent: (event: MonitoringWsEvent) => void;
}

const KNOWN_EVENT_TYPES = [
    "driver_location_update",
    "ride_status_changed",
    "driver_status_changed",
    "ride_requested",
    "ride_completed",
    "ride_cancelled",
    "drivers_snapshot",
    "rides_snapshot",
];

const FATAL_ERROR_MESSAGES = new Set([
    "authentication_required",
    "invalid_token_or_user_not_found",
    "admin_access_required",
    "session_revoked",
    "token_revoked",
    "ERR_TOKEN_AUDIENCE",
    "firebase_audience_not_configured",
]);

const BASE_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;
const AUTH_ACK_TIMEOUT_MS = 15_000;

function normalizeWsBaseUrl(rawBaseUrl: string): string {
    const trimmed = rawBaseUrl.trim().replace(/\/+$/, "");
    if (!trimmed) return "";

    try {
        const url = new URL(trimmed);
        if (url.protocol === "https:") url.protocol = "wss:";
        if (url.protocol === "http:") url.protocol = "ws:";
        // Operators sometimes paste a full /ws/admin/... URL into the Vercel
        // env var. The hook appends the connection-specific path itself, so
        // keep only the backend origin in that case. Non-/ws path prefixes are
        // preserved for deployments mounted behind a subpath.
        if (url.pathname === "/" || url.pathname.startsWith("/ws")) {
            url.pathname = "";
        } else {
            url.pathname = url.pathname.replace(/\/+$/, "");
        }
        url.search = "";
        url.hash = "";
        return url.toString().replace(/\/+$/, "");
    } catch {
        return trimmed;
    }
}

function reconnectDelayWithJitter(retryCount: number): number {
    const exponentialDelay = Math.min(
        BASE_RECONNECT_DELAY_MS * 2 ** retryCount,
        MAX_RECONNECT_DELAY_MS,
    );
    // Spread reconnects across a fleet of admin tabs so a backend restart does
    // not create a synchronized reconnect stampede.
    const jitterMultiplier = 0.8 + Math.random() * 0.4;
    return Math.round(exponentialDelay * jitterMultiplier);
}

export function useMonitoringSocket({ token, onEvent }: UseMonitoringSocketOptions) {
    const [status, setStatus] = useState<ConnectionStatus>("disconnected");
    const [lastError, setLastError] = useState<string | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const retryCountRef = useRef(0);
    const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const authAckTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const shouldReconnectRef = useRef(true);
    const onEventRef = useRef(onEvent);
    onEventRef.current = onEvent;

    const clientId = useRef(
        typeof crypto !== "undefined" ? crypto.randomUUID() : Math.random().toString(36).slice(2)
    );

    const clearRetryTimer = useCallback(() => {
        if (retryTimerRef.current) {
            clearTimeout(retryTimerRef.current);
            retryTimerRef.current = null;
        }
    }, []);

    const clearAuthAckTimer = useCallback(() => {
        if (authAckTimerRef.current) {
            clearTimeout(authAckTimerRef.current);
            authAckTimerRef.current = null;
        }
    }, []);

    const send = useCallback((msg: Record<string, unknown>) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify(msg));
        }
    }, []);

    const requestSnapshots = useCallback(() => {
        send({ type: "get_drivers_snapshot" });
        send({ type: "get_rides_snapshot" });
    }, [send]);

    const connect = useCallback(() => {
        if (!token) {
            setStatus("disconnected");
            setLastError(
                "Waiting for an admin access token. If this does not clear, refresh or sign in again.",
            );
            return;
        }
        clearRetryTimer();
        shouldReconnectRef.current = true;
        if (
            wsRef.current?.readyState === WebSocket.OPEN ||
            wsRef.current?.readyState === WebSocket.CONNECTING
        ) {
            return;
        }

        const fallbackScheme =
            typeof window !== "undefined" && window.location.protocol === "https:"
                ? "wss"
                : "ws";
        const fallbackHost =
            typeof window !== "undefined" ? window.location.host : "";
        const wsBase = normalizeWsBaseUrl(
            process.env.NEXT_PUBLIC_WS_URL ||
            process.env.NEXT_PUBLIC_API_URL ||
            (fallbackHost ? `${fallbackScheme}://${fallbackHost}` : ""),
        );
        if (!wsBase) {
            setStatus("error");
            setLastError(
                "WebSocket URL is not configured. Set NEXT_PUBLIC_WS_URL or NEXT_PUBLIC_API_URL and redeploy.",
            );
            return;
        }
        const url = `${wsBase}/ws/admin/${clientId.current}`;

        setStatus("connecting");
        setLastError(null);
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
            ws.send(JSON.stringify({ type: "auth", token }));
            setStatus("connecting");
            clearAuthAckTimer();
            authAckTimerRef.current = setTimeout(() => {
                if (wsRef.current === ws && ws.readyState === WebSocket.OPEN) {
                    setLastError(
                        "WebSocket opened but auth_success was not received. Check the admin token and backend /ws/admin auth logs.",
                    );
                    ws.close();
                }
            }, AUTH_ACK_TIMEOUT_MS);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data) as { type: string; message?: string } & Record<
                    string,
                    unknown
                >;
                if (data.type === "auth_success") {
                    clearAuthAckTimer();
                    setStatus("connected");
                    setLastError(null);
                    retryCountRef.current = 0;
                    requestSnapshots();
                    return;
                }
                if (data.type === "ping") {
                    ws.send(JSON.stringify({ type: "pong" }));
                    return;
                }
                if (data.type === "error") {
                    clearAuthAckTimer();
                    setStatus("error");
                    setLastError(
                        data.message
                            ? `Backend WebSocket auth error: ${data.message}`
                            : "Backend WebSocket returned an error.",
                    );
                    if (data.message && FATAL_ERROR_MESSAGES.has(data.message)) {
                        shouldReconnectRef.current = false;
                        ws.close();
                    }
                    return;
                }
                if (KNOWN_EVENT_TYPES.includes(data.type)) {
                    onEventRef.current(data as unknown as MonitoringWsEvent);
                }
            } catch {
                // ignore malformed messages
            }
        };

        ws.onerror = () => {
            setStatus("error");
            setLastError(
                "WebSocket transport error. In Chrome, a failed WebSocket may show Status N/A; check whether the handshake reached the backend.",
            );
        };

        ws.onclose = (event) => {
            clearAuthAckTimer();
            const isCurrentSocket = wsRef.current === ws;
            if (isCurrentSocket) {
                wsRef.current = null;
                if (!shouldReconnectRef.current || !token) {
                    setStatus("error");
                    return;
                }
                setStatus("disconnected");
                const code = typeof event?.code === "number" ? event.code : null;
                const reason =
                    typeof event?.reason === "string" && event.reason ? ` (${event.reason})` : "";
                setLastError(
                    code === 1006
                        ? "WebSocket abnormal close (1006): the handshake or transport was dropped; reconnecting…"
                        : code
                            ? `WebSocket closed with code ${code}${reason}; reconnecting…`
                            : "WebSocket closed; reconnecting…",
                );
                const delay = reconnectDelayWithJitter(retryCountRef.current);
                retryCountRef.current += 1;
                retryTimerRef.current = setTimeout(connect, delay);
            }
        };
    }, [token, clearAuthAckTimer, clearRetryTimer, requestSnapshots]);

    useEffect(() => {
        connect();
        return () => {
            shouldReconnectRef.current = false;
            clearRetryTimer();
            clearAuthAckTimer();
            const ws = wsRef.current;
            wsRef.current = null;
            ws?.close();
        };
    }, [connect, clearAuthAckTimer, clearRetryTimer]);

    return { status, send, requestSnapshots, lastError };
}
