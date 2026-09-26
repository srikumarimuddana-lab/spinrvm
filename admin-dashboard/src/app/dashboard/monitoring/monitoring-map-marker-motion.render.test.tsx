/**
 * MonitoringMap — driver markers glide between location updates (UX
 * program W4.1) instead of teleporting, via src/lib/map/marker-interpolation.
 *
 * The util's maths is unit-tested in src/lib/__tests__/marker-interpolation
 * .test.ts; this file covers the WIRING in monitoring-map.tsx: that the
 * first placement of a marker is exact (the dashboard-monitoring visual
 * baseline depends on it), that a later move is spread over animation
 * frames, and that the snap / Reduce Motion / hidden-marker / unmount paths
 * set the position directly with no frame loop left running.
 *
 * maplibre-gl is mocked (jsdom has no WebGL); the fake map records its event
 * handlers so the test can fire "load" and get the imperative handles the
 * page uses. requestAnimationFrame is replaced with a manual queue so frames
 * run only when the test says so, with a timestamp it chooses.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

const markers: FakeMarkerShape[] = [];
const mapHandlers = new Map<string, Array<() => void>>();

interface FakeMarkerShape {
    setLngLat: ReturnType<typeof vi.fn>;
    addTo: ReturnType<typeof vi.fn>;
    remove: ReturnType<typeof vi.fn>;
}

vi.mock("maplibre-gl", () => {
    class FakeMap {
        on = vi.fn((event: string, cb: () => void) => {
            const list = mapHandlers.get(event) ?? [];
            list.push(cb);
            mapHandlers.set(event, list);
        });
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
        el = document.createElement("div");
        setLngLat = vi.fn().mockReturnThis();
        addTo = vi.fn().mockReturnThis();
        remove = vi.fn();
        getElement = vi.fn(() => this.el);
        constructor() {
            markers.push(this);
        }
    }
    class FakeControl {}
    return {
        __esModule: true,
        default: { Map: FakeMap, Marker: FakeMarker, NavigationControl: FakeControl, FullscreenControl: FakeControl },
        Map: FakeMap,
        Marker: FakeMarker,
        NavigationControl: FakeControl,
        FullscreenControl: FakeControl,
        setWorkerUrl: vi.fn(),
    };
});

vi.mock("@/lib/map/webgl-support", () => ({
    hasRenderingWebGL: vi.fn(() => true),
}));

import { MonitoringMap, type MapHandles } from "./monitoring-map";
import type { MonitoringDriver, MonitoringFilters, MonitoringRide, SelectedItem } from "./types";

const FILTERS: MonitoringFilters = {
    showOnline: true,
    showOffline: true,
    showRides: true,
    showDemand: false,
    serviceAreaId: null,
    vehicleTypeId: null,
};

function driver(overrides: Partial<MonitoringDriver> = {}): MonitoringDriver {
    return {
        id: "d1",
        name: "Test Driver",
        phone: "+13065550100",
        photo_url: null,
        lat: 52.13,
        lng: -106.67,
        is_online: true,
        is_available: true,
        vehicle_make: null,
        vehicle_model: null,
        vehicle_color: null,
        license_plate: null,
        vehicle_type_id: null,
        rating: null,
        total_rides: 0,
        active_ride_id: null,
        service_area_id: null,
        ...overrides,
    };
}

// ── Manual requestAnimationFrame ────────────────────────────────────
let rafQueue = new Map<number, FrameRequestCallback>();
let rafId = 0;
function runFrame(timestamp: number) {
    const due = rafQueue;
    rafQueue = new Map();
    due.forEach((cb) => cb(timestamp));
}

function lastLngLat(m: FakeMarkerShape): [number, number] {
    return m.setLngLat.mock.calls[m.setLngLat.mock.calls.length - 1][0];
}

function mountMap(initial: MonitoringDriver[] = [driver()], filters: MonitoringFilters = FILTERS) {
    const onReady = vi.fn();
    const driversMap = { current: new Map(initial.map((d) => [d.id, d])) };
    const ridesMap = { current: new Map<string, MonitoringRide>() };
    const utils = render(
        <MonitoringMap
            driversMap={driversMap}
            ridesMap={ridesMap}
            filters={filters}
            searchQuery=""
            selected={null as SelectedItem}
            followMode={false}
            onSelectDriver={vi.fn()}
            onSelectRide={vi.fn()}
            onReady={onReady}
        />,
    );
    act(() => {
        (mapHandlers.get("load") ?? []).forEach((cb) => cb());
    });
    const handles = onReady.mock.calls[0][0] as MapHandles;
    return { ...utils, handles };
}

beforeEach(() => {
    markers.length = 0;
    mapHandlers.clear();
    rafQueue = new Map();
    rafId = 0;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
        rafId += 1;
        rafQueue.set(rafId, cb);
        return rafId;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
        rafQueue.delete(id);
    });
});

afterEach(() => {
    vi.unstubAllGlobals();
    delete (window as { matchMedia?: unknown }).matchMedia;
});

describe("MonitoringMap driver marker motion", () => {
    it("places a new marker exactly, with no animation frame requested", () => {
        const { unmount } = mountMap();
        expect(markers).toHaveLength(1);
        expect(lastLngLat(markers[0])).toEqual([-106.67, 52.13]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("re-applying the same position (poll refresh, onReady replay) never animates", () => {
        const { handles, unmount } = mountMap();
        handles.updateDriverMarker(driver());
        expect(lastLngLat(markers[0])).toEqual([-106.67, 52.13]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("glides a short move over frames and lands exactly on the target", () => {
        const { handles, unmount } = mountMap();
        const m = markers[0];
        const t0 = performance.now();
        handles.updateDriverMarker(driver({ lat: 52.131, lng: -106.669 }));

        // Not moved synchronously — the glide starts on the next frame.
        expect(lastLngLat(m)).toEqual([-106.67, 52.13]);
        expect(rafQueue.size).toBe(1);

        runFrame(t0 + 500);
        const [midLng, midLat] = lastLngLat(m);
        expect(midLat).toBeCloseTo(52.1305, 5);
        expect(midLng).toBeCloseTo(-106.6695, 5);
        expect(rafQueue.size).toBe(1);

        runFrame(t0 + 1500);
        expect(lastLngLat(m)).toEqual([-106.669, 52.131]);
        expect(rafQueue.size).toBe(0); // loop stops once nothing is moving
        unmount();
    });

    it("retargets a glide in progress from where the marker is drawn now", () => {
        const { handles, unmount } = mountMap();
        const m = markers[0];
        const t0 = performance.now();
        handles.updateDriverMarker(driver({ lat: 52.131, lng: -106.67 }));
        runFrame(t0 + 500);
        const [, drawnLat] = lastLngLat(m);

        handles.updateDriverMarker(driver({ lat: 52.132, lng: -106.67 }));
        const t1 = performance.now();
        runFrame(t1); // first frame of the new glide starts where the old one was
        expect(lastLngLat(m)[1]).toBeCloseTo(drawnLat, 5);
        runFrame(t1 + 2000);
        expect(lastLngLat(m)).toEqual([-106.67, 52.132]);
        unmount();
    });

    it("snaps a jump over 500 m straight to the target", () => {
        const { handles, unmount } = mountMap();
        handles.updateDriverMarker(driver({ lat: 52.14, lng: -106.67 })); // ~1.1 km
        expect(lastLngLat(markers[0])).toEqual([-106.67, 52.14]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("sets the position directly under prefers-reduced-motion", () => {
        Object.defineProperty(window, "matchMedia", {
            value: vi.fn((query: string) => ({ matches: query.includes("reduce"), media: query })),
            configurable: true,
            writable: true,
        });
        const { handles, unmount } = mountMap();
        handles.updateDriverMarker(driver({ lat: 52.131, lng: -106.669 }));
        expect(lastLngLat(markers[0])).toEqual([-106.669, 52.131]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("does not glide a marker that is hidden by the current filters", () => {
        const { handles, unmount } = mountMap([driver()], { ...FILTERS, showOnline: false });
        handles.updateDriverMarker(driver({ lat: 52.131, lng: -106.669 }));
        expect(lastLngLat(markers[0])).toEqual([-106.669, 52.131]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("cancels a running glide on unmount", () => {
        const { handles, unmount } = mountMap();
        handles.updateDriverMarker(driver({ lat: 52.131, lng: -106.669 }));
        expect(rafQueue.size).toBe(1);
        unmount();
        expect(rafQueue.size).toBe(0);
    });

    it("drops a removed driver's glide instead of moving a detached marker", () => {
        const { handles, unmount } = mountMap();
        const m = markers[0];
        const t0 = performance.now();
        handles.updateDriverMarker(driver({ lat: 52.131, lng: -106.669 }));
        const callsBefore = m.setLngLat.mock.calls.length;
        handles.removeDriverMarker("d1");
        runFrame(t0 + 500);
        expect(m.setLngLat.mock.calls.length).toBe(callsBefore);
        expect(rafQueue.size).toBe(0);
        unmount();
    });
});
