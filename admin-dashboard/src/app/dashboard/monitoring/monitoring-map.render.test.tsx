/**
 * MonitoringMap — rendered coverage for the WebGL-stub guard.
 *
 * This is the first test at any tier that renders MonitoringMap itself
 * (monitoring-map-demand-fill.test.ts only imports its two pure helper
 * exports; pages.smoke.test.tsx stubs this whole component out because it
 * pulls maplibre-gl). That gap is exactly how the reported bug went
 * unexplained: a browser whose WebGL context is a non-drawing stub gets a
 * MapLibre map that "loads" successfully by every signal this component
 * watches (style/tile JSON reaching the page, not a GPU actually painting),
 * so the canvas sits blank with no error, no retry banner, and no
 * explanation — see docs/change-log/2026-09-14-webgl-stub-detection-and-
 * blocked-tile-notice.md, which found and fixed the identical gap on the
 * ride-detail Route Views map first.
 *
 * maplibre-gl is mocked rather than pulled in for real — jsdom has no WebGL
 * context (hasRenderingWebGL() legitimately returns false under jsdom, same
 * as the ride-detail probe tests), and MonitoringMap is a live, imperative,
 * ref-driven map (driver/ride markers, service-area layers, an OSRM route
 * fetch) whose full behaviour is out of scope here — this file tests only
 * the new guard, and that the pre-existing path still constructs a map when
 * WebGL is fine.
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
        getLayer = vi.fn();
        setPaintProperty = vi.fn();
        setLayoutProperty = vi.fn();
        panTo = vi.fn();
        flyTo = vi.fn();
        remove = vi.fn();
    }
    class FakeMarker {
        setLngLat = vi.fn().mockReturnThis();
        addTo = vi.fn().mockReturnThis();
        remove = vi.fn();
        getElement = vi.fn(() => document.createElement("div"));
    }
    class FakeControl {}
    return {
        __esModule: true,
        default: { Map: FakeMap, Marker: FakeMarker, NavigationControl: FakeControl, FullscreenControl: FakeControl },
        Map: FakeMap,
        Marker: FakeMarker,
        NavigationControl: FakeControl,
        FullscreenControl: FakeControl,
        // maplibre-base.ts calls this at module load (v6 Turbopack-worker fix) —
        // without it, importing maplibre-base.ts in a test throws.
        setWorkerUrl: vi.fn(),
    };
});

vi.mock("@/lib/map/webgl-support", () => ({
    hasRenderingWebGL: vi.fn(),
}));

import { hasRenderingWebGL } from "@/lib/map/webgl-support";
import { MonitoringMap } from "./monitoring-map";
import type { MonitoringDriver, MonitoringRide, MonitoringFilters, SelectedItem } from "./types";

const FILTERS: MonitoringFilters = {
    showOnline: true,
    showOffline: true,
    showRides: true,
    showDemand: false,
    serviceAreaId: null,
    vehicleTypeId: null,
};

function baseProps() {
    return {
        driversMap: { current: new Map<string, MonitoringDriver>() },
        ridesMap: { current: new Map<string, MonitoringRide>() },
        filters: FILTERS,
        searchQuery: "",
        selected: null as SelectedItem,
        followMode: false,
        onSelectDriver: vi.fn(),
        onSelectRide: vi.fn(),
        onReady: vi.fn(),
    };
}

// ACTION_ITEMS.md C115 — onCanRenderChange is the reactive "map can render"
// signal page.tsx uses to disable/explain the service-area jump buttons and
// Follow toggle instead of leaving them silently no-op'd (see
// monitoring-map-controls-availability.render.test.tsx for that consumer
// side). Covered here, alongside the guard itself, since it's the same
// webglOk value driving both.
describe("MonitoringMap onCanRenderChange", () => {
    it("fires false, once, when WebGL cannot render", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(false);
        const onCanRenderChange = vi.fn();
        render(<MonitoringMap {...baseProps()} onCanRenderChange={onCanRenderChange} />);

        expect(onCanRenderChange).toHaveBeenCalledTimes(1);
        expect(onCanRenderChange).toHaveBeenCalledWith(false);
    });

    it("fires true, once, when WebGL renders normally", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(true);
        const onCanRenderChange = vi.fn();
        render(<MonitoringMap {...baseProps()} onCanRenderChange={onCanRenderChange} />);

        expect(onCanRenderChange).toHaveBeenCalledTimes(1);
        expect(onCanRenderChange).toHaveBeenCalledWith(true);
    });

    it("is optional — omitting it doesn't throw either way", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(false);
        expect(() => render(<MonitoringMap {...baseProps()} />)).not.toThrow();
    });
});

beforeEach(() => {
    mapCtor.mockClear();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe("MonitoringMap WebGL-stub guard", () => {
    it("never constructs a MapLibre map and shows an explanation when WebGL cannot render", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(false);
        render(<MonitoringMap {...baseProps()} />);

        expect(mapCtor).not.toHaveBeenCalled();
        expect(screen.getByRole("status")).toHaveTextContent(/live map can.t render in this browser/i);
        expect(screen.getByRole("status")).toHaveTextContent(/still live/i);
    });

    it("constructs a MapLibre map as before when WebGL renders normally", () => {
        vi.mocked(hasRenderingWebGL).mockReturnValue(true);
        render(<MonitoringMap {...baseProps()} />);

        expect(mapCtor).toHaveBeenCalledTimes(1);
        expect(screen.queryByText(/live map can.t render in this browser/i)).not.toBeInTheDocument();
    });
});
