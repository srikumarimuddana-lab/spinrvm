/**
 * LiveRideMap — rendered coverage for the WebGL-stub guard.
 *
 * First render test of this component at any tier. Ports the same fix
 * `monitoring-map.tsx` and `ride-route-map.tsx` already shipped for the
 * identical bug: a browser whose WebGL context is a non-drawing stub gets
 * a MapLibre map that "loads" successfully by every signal this component
 * watches (style/tile JSON reaching the page, not a GPU actually painting),
 * so the canvas sits blank with no explanation. See
 * docs/change-log/2026-09-14-webgl-stub-detection-and-blocked-tile-notice.md
 * and docs/change-log/2026-09-14-monitoring-map-webgl-stub-guard.md.
 *
 * maplibre-gl is mocked rather than pulled in for real — jsdom has no WebGL
 * context (hasRenderingWebGL() legitimately returns false under jsdom, same
 * as every other probe test in this repo), and this file's own map-building
 * behavior (markers, gradient route lines, trail updates) is out of scope
 * here — this file tests only the new guard, and that the pre-existing path
 * still constructs a map when WebGL is fine.
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
        remove = vi.fn();
    }
    class FakePopup {
        setText = vi.fn().mockReturnThis();
        setHTML = vi.fn().mockReturnThis();
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
import LiveRideMap from "./live-map";

const ROUTE = {
    pickupLat: 52.1332,
    pickupLng: -106.67,
    dropoffLat: 52.1205,
    dropoffLng: -106.6335,
};

beforeEach(() => {
    mapCtor.mockClear();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe("LiveRideMap WebGL-stub guard", () => {
    it("never constructs a MapLibre map and shows an explanation when WebGL cannot render", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(false);
        render(<LiveRideMap {...ROUTE} />);

        expect(mapCtor).not.toHaveBeenCalled();
        expect(screen.getByRole("status")).toHaveTextContent(/live map can.t render in this browser/i);
        expect(screen.getByRole("status")).toHaveTextContent(/still live/i);
    });

    it("constructs a MapLibre map as before when WebGL renders normally", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(true);
        render(<LiveRideMap {...ROUTE} />);

        expect(mapCtor).toHaveBeenCalledTimes(1);
        expect(screen.queryByText(/live map can.t render in this browser/i)).not.toBeInTheDocument();
    });
});
