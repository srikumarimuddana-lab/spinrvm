import { useEffect, useRef, useCallback } from 'react';
import { Vibration, Alert } from 'react-native';
import { API_URL } from '@shared/config';
import { useAuthStore } from '@shared/store/authStore';

const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000]; // Exponential backoff

interface UseWebSocketOptions {
    isOnline: boolean;
    userId?: string;
    onMessage: (data: any) => void;
}

export const useWebSocket = ({ isOnline, userId, onMessage }: UseWebSocketOptions) => {
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectAttemptRef = useRef(0);
    const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Connect to WebSocket
    const connectWebSocket = useCallback(() => {
        if (!isOnline || !userId) return;

        const token = useAuthStore.getState().token;
        if (!token) {
            console.log('Cannot connect WebSocket: No auth token');
            return;
        }

        // WebSocket URL must match backend route: /ws/{client_type}/{client_id}
        const wsUrl = `${API_URL.replace('http', 'ws')}/ws/driver/${userId}`;
        console.log('Connecting to WebSocket:', wsUrl);
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
            console.log('WebSocket connected, sending auth...');
            reconnectAttemptRef.current = 0; // Reset reconnect attempts
            const token = useAuthStore.getState().token;
            console.log('Auth token exists:', !!token, 'User ID:', userId);
            ws.send(JSON.stringify({
                type: 'auth',
                token: token,
                client_type: 'driver',
            }));
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                // Log any error messages from server
                if (data.type === 'error') {
                    console.log('WebSocket auth error:', data.message);
                }
                onMessage(data);
            } catch { }
        };

        ws.onerror = (error) => {
            console.log('WebSocket error:', error);
        };

        ws.onclose = (event) => {
            console.log('WebSocket closed:', event.code, event.reason);
            // Attempt reconnection with exponential backoff
            if (isOnline && userId) {
                const delay = RECONNECT_DELAYS[Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS.length - 1)];
                console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttemptRef.current + 1})`);
                reconnectTimeoutRef.current = setTimeout(() => {
                    reconnectAttemptRef.current++;
                    connectWebSocket();
                }, delay);
            }
        };
    }, [isOnline, userId, onMessage]);

    // Disconnect WebSocket
    const disconnectWebSocket = useCallback(() => {
        if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
            reconnectTimeoutRef.current = null;
        }
        if (wsRef.current) {
            wsRef.current.close();
            wsRef.current = null;
        }
    }, []);

    // Set up WebSocket connection
    useEffect(() => {
        if (!isOnline || !userId) {
            disconnectWebSocket();
            return;
        }

        connectWebSocket();

        return () => {
            disconnectWebSocket();
        };
    }, [isOnline, userId, connectWebSocket, disconnectWebSocket]);

    // Send message through WebSocket
    const sendMessage = useCallback((message: any) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify(message));
        }
    }, []);

    return {
        sendMessage,
        connectWebSocket,
        disconnectWebSocket,
    };
};

export default useWebSocket;
