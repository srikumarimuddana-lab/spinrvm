import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMonitoringSocket } from "./use-monitoring-socket";

class MockWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    static instances: MockWebSocket[] = [];

    readyState = MockWebSocket.CONNECTING;
    onopen: (() => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    sent: string[] = [];

    constructor(public url: string) {
        MockWebSocket.instances.push(this);
    }

    send(message: string) {
        this.sent.push(message);
    }

    close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.();
    }

    open() {
        this.readyState = MockWebSocket.OPEN;
        this.onopen?.();
    }

    receive(data: Record<string, unknown>) {
        this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
    }
}

describe("useMonitoringSocket", () => {
    const originalEnv = { ...process.env };

    beforeEach(() => {
        vi.useFakeTimers();
        process.env.NEXT_PUBLIC_WS_URL = "";
        process.env.NEXT_PUBLIC_API_URL = "";
        MockWebSocket.instances = [];
        vi.spyOn(Math, "random").mockReturnValue(0.5);
        vi.stubGlobal("WebSocket", MockWebSocket);
        vi.stubGlobal("crypto", { randomUUID: () => "admin-client-id" });
    });

    afterEach(() => {
        process.env = { ...originalEnv };
        vi.restoreAllMocks();
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    it("builds the admin websocket URL from NEXT_PUBLIC_API_URL when a WS URL is not set", () => {
        process.env.NEXT_PUBLIC_API_URL = "https://spinr-backend-production.up.railway.app";
        const onEvent = vi.fn();

        renderHook(() => useMonitoringSocket({ token: "token", onEvent }));

        expect(MockWebSocket.instances[0].url).toBe(
            "wss://spinr-backend-production.up.railway.app/ws/admin/admin-client-id",
        );
    });

    it("normalizes a pasted full /ws/admin URL from NEXT_PUBLIC_WS_URL", () => {
        process.env.NEXT_PUBLIC_WS_URL =
            "wss://spinr-backend-production.up.railway.app/ws/admin/some-old-id";
        const onEvent = vi.fn();

        renderHook(() => useMonitoringSocket({ token: "token", onEvent }));

        expect(MockWebSocket.instances[0].url).toBe(
            "wss://spinr-backend-production.up.railway.app/ws/admin/admin-client-id",
        );
    });

    it("does not schedule a reconnect when React intentionally closes a stale socket", () => {
        const onEvent = vi.fn();
        const { rerender } = renderHook(
            ({ token }) => useMonitoringSocket({ token, onEvent }),
            { initialProps: { token: "old-token" } },
        );

        expect(MockWebSocket.instances).toHaveLength(1);

        rerender({ token: "new-token" });

        expect(MockWebSocket.instances).toHaveLength(2);
        expect(vi.getTimerCount()).toBe(0);
    });

    it("reconnects after the current live socket closes unexpectedly", () => {
        const onEvent = vi.fn();
        renderHook(() => useMonitoringSocket({ token: "token", onEvent }));

        expect(MockWebSocket.instances).toHaveLength(1);

        act(() => {
            MockWebSocket.instances[0].close();
        });

        expect(vi.getTimerCount()).toBe(1);

        act(() => {
            vi.advanceTimersByTime(1000);
        });

        expect(MockWebSocket.instances).toHaveLength(2);
    });

    it("does not reconnect forever after a fatal auth error", () => {
        const onEvent = vi.fn();
        const { result } = renderHook(() => useMonitoringSocket({ token: "bad-token", onEvent }));

        expect(MockWebSocket.instances).toHaveLength(1);

        act(() => {
            MockWebSocket.instances[0].open();
            MockWebSocket.instances[0].receive({
                type: "error",
                message: "invalid_token_or_user_not_found",
            });
        });

        expect(result.current.status).toBe("error");
        expect(vi.getTimerCount()).toBe(0);

        act(() => {
            vi.advanceTimersByTime(30_000);
        });

        expect(MockWebSocket.instances).toHaveLength(1);
    });
});
