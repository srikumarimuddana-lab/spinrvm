/**
 * ACTION_ITEMS.md C115 — the service-area "jump" pill buttons in page.tsx
 * are not a separate component; their entire purpose is a map-visual
 * effect (`handleAreaFit` → `mapHandlesRef.current?.fitArea(...)`), so
 * they used to silently no-op forever whenever MonitoringMap's WebGL
 * guard bailed out (mapHandlesRef.current stayed null, with nothing
 * telling page.tsx to disable/explain them). Real coverage needs the
 * actual page mounted with MonitoringMap swapped for a controllable stub
 * — everything else here follows the same mocking pattern
 * pages.smoke.test.tsx already uses for this exact page.
 *
 * See monitoring-toolbar-availability.render.test.tsx for the other
 * affected control (the Follow toggle), and monitoring-map.render.test.tsx
 * for coverage of the signal's own source (onCanRenderChange firing).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    usePathname: () => "/dashboard/monitoring",
    redirect: vi.fn(),
    notFound: vi.fn(),
}));

vi.mock("@/store/authStore", () => {
    const state = {
        token: "fake-token",
        user: { id: "admin-001", email: "admin@spinr.ca", role: "super_admin", modules: ["dashboard"] },
        isLoading: false,
        setToken: vi.fn(),
        setUser: vi.fn(),
        logout: vi.fn(),
    };
    return {
        useAuthStore: (selector?: (s: typeof state) => unknown) =>
            typeof selector === "function" ? selector(state) : state,
    };
});

vi.mock("@/hooks/use-monitoring-socket", () => ({
    useMonitoringSocket: () => ({ status: "connected", requestSnapshots: vi.fn(), lastError: null }),
}));

// Same "wrap the real module" technique as pages.smoke.test.tsx: every
// get*/list*/fetch* export resolves an empty array by default (page.tsx's
// initial load never crashes), except getServiceAreas, which gets its own
// dedicated mock instance — sharing the generic one would mean this test's
// override of it also silently overrides getMonitoringDrivers/
// getMonitoringRides/etc, since pages.smoke's shared-instance pattern
// assumes no single test needs to control one get* export independently.
vi.mock("@/lib/api", async (importOriginal) => {
    const actual = await importOriginal<Record<string, unknown>>();
    const noop = vi.fn().mockResolvedValue({});
    const list = vi.fn().mockResolvedValue([]);
    const overrides: Record<string, unknown> = {
        getServiceAreas: vi.fn().mockResolvedValue([]),
    };
    return {
        ...Object.fromEntries(
            Object.entries(actual).map(([key, val]) => {
                if (typeof val !== "function") return [key, val];
                if (key in overrides) return [key, overrides[key]];
                if (key.startsWith("get") || key.startsWith("list") || key.startsWith("fetch"))
                    return [key, list];
                return [key, noop];
            }),
        ),
    };
});

vi.mock("@/app/dashboard/monitoring/driver-panel", () => ({
    DriverPanel: () => <div data-sub="DriverPanel" />,
}));
vi.mock("@/app/dashboard/monitoring/ride-panel", () => ({
    RidePanel: () => <div data-sub="RidePanel" />,
}));

// Controllable MonitoringMap stub. Fires onCanRenderChange once on mount
// (mirroring the real component's one-shot effect), and only fires onReady
// when canRender is true — the real component's onReady also never fires
// when its WebGL guard bails out, which is the exact gap this whole fix
// closes.
const mockMonitoringMapState = { canRender: true };
vi.mock("@/app/dashboard/monitoring/monitoring-map", () => ({
    MonitoringMap: (props: {
        onCanRenderChange?: (canRender: boolean) => void;
        onReady?: (handles: unknown) => void;
    }) => {
        React.useEffect(() => {
            props.onCanRenderChange?.(mockMonitoringMapState.canRender);
            if (mockMonitoringMapState.canRender) {
                props.onReady?.({
                    updateDriverMarker: vi.fn(),
                    removeDriverMarker: vi.fn(),
                    updateRideMarkers: vi.fn(),
                    removeRideMarkers: vi.fn(),
                    panTo: vi.fn(),
                    fitArea: vi.fn(),
                });
            }
            // Mount-only, matching the real component's [webglOk] effect.
            // eslint-disable-next-line react-hooks/exhaustive-deps
        }, []);
        return <div data-sub="MonitoringMap" />;
    },
}));

import { getServiceAreas } from "@/lib/api";
import MonitoringPage from "./page";

const JUMPABLE_AREA = {
    id: "area-1",
    name: "Regina",
    polygon: null,
    center_lat: 50.45,
    center_lng: -104.6,
};

// No polygon AND no center — page.tsx's own fallbackCenter derivation
// leaves this area with neither, so it never gets a jump button at all
// (see page.tsx's jumpableAreas filter).
const UNJUMPABLE_AREA = {
    id: "area-2",
    name: "No Pin Yet",
    polygon: null,
    center_lat: null,
    center_lng: null,
};

describe("MonitoringPage service-area jump buttons — map availability", () => {
    beforeEach(() => {
        mockMonitoringMapState.canRender = true;
        vi.mocked(getServiceAreas).mockResolvedValue([JUMPABLE_AREA]);
    });

    afterEach(() => {
        vi.clearAllMocks();
    });

    it("keeps jump buttons enabled and unexplained when the map can render (regression: behaves as before)", async () => {
        render(<MonitoringPage />);

        const button = await screen.findByRole("button", { name: "Regina" });
        expect(button).not.toBeDisabled();
        expect(screen.queryByText(/jump buttons disabled/i)).not.toBeInTheDocument();
    });

    it("disables jump buttons and explains why when the map can't render", async () => {
        mockMonitoringMapState.canRender = false;
        render(<MonitoringPage />);

        const button = await screen.findByRole("button", { name: "Regina" });
        expect(button).toBeDisabled();
        expect(button.getAttribute("title")).toMatch(/map can.t render/i);
        expect(screen.getByText(/jump buttons disabled/i)).toBeInTheDocument();
    });

    it("never shows the explanation when there are service areas but none are jumpable", async () => {
        // Regression for a real gap caught in review: serviceAreas.length > 0
        // used to gate the explanatory text too, so an area with neither a
        // polygon nor a fallback centre could show "jump buttons disabled"
        // with zero buttons ever having rendered.
        mockMonitoringMapState.canRender = false;
        vi.mocked(getServiceAreas).mockResolvedValue([UNJUMPABLE_AREA]);
        render(<MonitoringPage />);

        // Let the async getServiceAreas() load settle before asserting an
        // absence (a bare queryBy* here could pass vacuously pre-resolution).
        await waitFor(() => expect(getServiceAreas).toHaveBeenCalled());
        await act(async () => {});

        expect(screen.queryByRole("button", { name: "No Pin Yet" })).not.toBeInTheDocument();
        expect(screen.queryByText(/jump buttons disabled/i)).not.toBeInTheDocument();
    });
});
