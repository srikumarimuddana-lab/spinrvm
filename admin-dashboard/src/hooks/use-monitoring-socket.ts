// src/hooks/use-monitoring-socket.ts
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MonitoringWsEvent } from "@/app/dashboard/monitoring/types";

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

interface UseMonitoringSocketOptions {
    token: string | null;
    onEvent: (event: MonitoringWsEvent) => void;
}

export function useMonitoringSocket({ token, onEvent }: UseMonitoringSocketOptions) {
    const [status, setStatus] = useState<ConnectionStatus>("disconnected");
    const wsRef = useRef<WebSocket | null>(null);
    const retryCountRef = useRef(0);
    const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const onEventRef = useRef(onEvent);
    onEventRef.current = onEvent; // always up-to-date without recreating the effect

    const clientId = useRef(
        typeof crypto !== "undefined" ? crypto.randomUUID() : Math.random().toString(36).slice(2)
    );

    const connect = useCallback(() => {
        if (!token) return;
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        const wsBase =
            process.env.NEXT_PUBLIC_WS_URL ||
            (typeof window !== "undefined" ? `ws://${window.location.host}` : "");
        const url = `${wsBase}/ws/admin/${clientId.current}`;

        setStatus("connecting");
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
            ws.send(JSON.stringify({ type: "auth", token }));
            setStatus("connected");
            retryCountRef.current = 0;
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data) as MonitoringWsEvent & { type: string };
                if (data.type === "ping") {
                    ws.send(JSON.stringify({ type: "pong" }));
                    return;
                }
                // Only forward known monitoring event types
                const knownTypes = [
                    "driver_location_update",
                    "ride_status_changed",
                    "driver_status_changed",
                    "ride_requested",
                    "ride_completed",
                    "ride_cancelled",
                ];
                if (knownTypes.includes(data.type)) {
                    onEventRef.current(data as MonitoringWsEvent);
                }
            } catch {
                // ignore malformed messages
            }
        };

        ws.onerror = () => setStatus("error");

        ws.onclose = () => {
            setStatus("disconnected");
            // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
            const delay = Math.min(1000 * 2 ** retryCountRef.current, 30_000);
            retryCountRef.current += 1;
            retryTimerRef.current = setTimeout(connect, delay);
        };
    }, [token]);

    useEffect(() => {
        connect();
        return () => {
            retryTimerRef.current && clearTimeout(retryTimerRef.current);
            wsRef.current?.close();
        };
    }, [connect]);

    return { status };
}
