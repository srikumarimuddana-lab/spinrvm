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

export function useMonitoringSocket({ token, onEvent }: UseMonitoringSocketOptions) {
    const [status, setStatus] = useState<ConnectionStatus>("disconnected");
    const wsRef = useRef<WebSocket | null>(null);
    const retryCountRef = useRef(0);
    const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const onEventRef = useRef(onEvent);
    onEventRef.current = onEvent;

    const clientId = useRef(
        typeof crypto !== "undefined" ? crypto.randomUUID() : Math.random().toString(36).slice(2)
    );

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
        if (!token) return;
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        const fallbackScheme =
            typeof window !== "undefined" && window.location.protocol === "https:"
                ? "wss"
                : "ws";
        const fallbackHost =
            typeof window !== "undefined" ? window.location.host : "";
        const wsBase =
            process.env.NEXT_PUBLIC_WS_URL ||
            (fallbackHost ? `${fallbackScheme}://${fallbackHost}` : "");
        const url = `${wsBase}/ws/admin/${clientId.current}`;

        setStatus("connecting");
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
            ws.send(JSON.stringify({ type: "auth", token }));
            setStatus("connecting");
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data) as { type: string } & Record<string, unknown>;
                if (data.type === "auth_success") {
                    setStatus("connected");
                    retryCountRef.current = 0;
                    requestSnapshots();
                    return;
                }
                if (data.type === "ping") {
                    ws.send(JSON.stringify({ type: "pong" }));
                    return;
                }
                if (data.type === "error") {
                    setStatus("error");
                    return;
                }
                if (KNOWN_EVENT_TYPES.includes(data.type)) {
                    onEventRef.current(data as unknown as MonitoringWsEvent);
                }
            } catch {
                // ignore malformed messages
            }
        };

        ws.onerror = () => setStatus("error");

        ws.onclose = () => {
            setStatus("disconnected");
            const delay = Math.min(1000 * 2 ** retryCountRef.current, 30_000);
            retryCountRef.current += 1;
            retryTimerRef.current = setTimeout(connect, delay);
        };
    }, [token, requestSnapshots]);

    useEffect(() => {
        connect();
        return () => {
            retryTimerRef.current && clearTimeout(retryTimerRef.current);
            wsRef.current?.close();
        };
    }, [connect]);

    return { status, send, requestSnapshots };
}
