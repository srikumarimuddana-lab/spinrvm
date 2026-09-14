/**
 * DriverMap — rendered coverage for the WebGL-stub guard.
 *
 * First render test of this component at any tier. Ports the same fix
 * `monitoring-map.tsx`, `live-map.tsx`, and `ride-route-map.tsx` already
 * shipped for the identical bug — see
 * docs/change-log/2026-09-14-monitoring-map-webgl-stub-guard.md for the
 * full root-cause writeup.
 *
 * Note: as of this diff, DriverMap has no importer anywhere in
 * admin-dashboard (grepped repo-wide) — it is not currently reachable from
 * any route. The fix is still applied, matching this backlog's explicit
 * "still owed" list (src/lib/map/webgl-support.ts's own module docstring)
 * and so the component is correct if/when it is wired up.
 *
 * maplibre-gl is mocked rather than pulled in for real — jsdom has no WebGL
 * context (hasRenderingWebGL() legitimately returns false under jsdom).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const mapCtor = vi.fn();

vi.mock("maplibre-gl", () => {
    class FakeMap {
        constructor(opts: unknown) {
            mapCtor(opts);
        }
        on = vi.fn();
        off = vi.fn();
        once = vi.fn();
        addControl = vi.fn();
        addSource = vi.fn();
        addLayer = vi.fn();
        getSource = vi.fn();
        remove = vi.fn();
    }
    class FakeMarker {
        setLngLat = vi.fn().mockReturnThis();
        setPopup = vi.fn().mockReturnThis();
        addTo = vi.fn().mockReturnThis();
        togglePopup = vi.fn();
        remove = vi.fn();
    }
    class FakePopup {
        setText = vi.fn().mockReturnThis();
        setHTML = vi.fn().mockReturnThis();
        isOpen = vi.fn(() => false);
        remove = vi.fn();
    }
    class FakeControl {}
    return {
        __esModule: true,
        default: { Map: FakeMap, Marker: FakeMarker, Popup: FakePopup, NavigationControl: FakeControl },
        Map: FakeMap,
        Marker: FakeMarker,
        Popup: FakePopup,
        NavigationControl: FakeControl,
    };
});

vi.mock("@/lib/map/webgl-support", () => ({
    hasRenderingWebGL: vi.fn(),
}));

import { hasRenderingWebGL } from "@/lib/map/webgl-support";
import DriverMap from "./driver-map";

beforeEach(() => {
    mapCtor.mockClear();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe("DriverMap WebGL-stub guard", () => {
    it("never constructs a MapLibre map and shows an explanation when WebGL cannot render", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(false);
        render(<DriverMap drivers={[]} />);

        expect(mapCtor).not.toHaveBeenCalled();
        expect(screen.getByRole("status")).toHaveTextContent(/live map can.t render in this browser/i);
        expect(screen.getByRole("status")).toHaveTextContent(/still live/i);
    });

    it("keeps the legend bar (driver counts) visible when the map can't render", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(false);
        render(
            <DriverMap
                drivers={[{ id: "d1", name: "Alice", is_online: true, current_lat: 52.1, current_lng: -106.6 }]}
            />,
        );

        expect(screen.getByText(/drivers on map/i)).toBeInTheDocument();
        expect(screen.getByText(/online \(1\)/i)).toBeInTheDocument();
    });

    it("constructs a MapLibre map as before when WebGL renders normally", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(true);
        render(<DriverMap drivers={[]} />);

        expect(mapCtor).toHaveBeenCalledTimes(1);
        expect(screen.queryByText(/live map can.t render in this browser/i)).not.toBeInTheDocument();
    });
});
