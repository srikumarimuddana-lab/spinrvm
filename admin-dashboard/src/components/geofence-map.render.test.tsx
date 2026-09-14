/**
 * GeofenceMap — rendered coverage for the WebGL-stub guard.
 *
 * First render test of this component at any tier. Fourth and last port of
 * the same fix already shipped for ride-route-map.tsx, monitoring-map.tsx,
 * live-map.tsx, and driver-map.tsx — see
 * docs/change-log/2026-09-14-monitoring-map-webgl-stub-guard.md for the
 * root-cause writeup.
 *
 * Unlike driver-map.tsx (which keeps a legend bar visible), this component's
 * entire surface -- draw/redraw/finish/cancel/clear controls, the drawing
 * instructions banner -- only makes sense with a working map, so the guard
 * replaces the whole render output rather than swapping out one region.
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
        getCanvas = vi.fn(() => ({ style: {} }));
        setLayoutProperty = vi.fn();
        resize = vi.fn();
        remove = vi.fn();
    }
    class FakeControl {}
    return {
        __esModule: true,
        default: { Map: FakeMap, NavigationControl: FakeControl },
        Map: FakeMap,
        NavigationControl: FakeControl,
    };
});

vi.mock("@/lib/map/webgl-support", () => ({
    hasRenderingWebGL: vi.fn(),
}));

import { hasRenderingWebGL } from "@/lib/map/webgl-support";
import GeofenceMap from "./geofence-map";

beforeEach(() => {
    mapCtor.mockClear();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe("GeofenceMap WebGL-stub guard", () => {
    it("never constructs a MapLibre map and shows an explanation when WebGL cannot render", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(false);
        render(<GeofenceMap />);

        expect(mapCtor).not.toHaveBeenCalled();
        expect(screen.getByRole("status")).toHaveTextContent(/geofence map can.t render in this browser/i);
    });

    it("hides the drawing toolbar when WebGL cannot render, even when editable", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(false);
        render(<GeofenceMap readonly={false} />);

        expect(screen.queryByTitle("Draw polygon")).not.toBeInTheDocument();
        expect(screen.queryByTitle("Redraw polygon from scratch")).not.toBeInTheDocument();
    });

    it("constructs a MapLibre map as before when WebGL renders normally", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(true);
        render(<GeofenceMap />);

        expect(mapCtor).toHaveBeenCalledTimes(1);
        expect(screen.queryByText(/geofence map can.t render in this browser/i)).not.toBeInTheDocument();
    });
});
