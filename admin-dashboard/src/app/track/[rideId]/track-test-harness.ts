/**
 * Test harness for the public /track/[rideId] page (render tests only — not
 * imported by the page itself).
 *
 * jsdom has no Google Maps, so this installs a minimal fake `window.google
 * .maps` that records what the page does to it: every position written to an
 * AdvancedMarkerElement, every Polyline drawn or cleared, and the map's
 * listeners. `fetch` is routed by URL: the backend's public tracking endpoint
 * serves whatever `harness.ride` holds (or `harness.status` when it isn't
 * 200), and the OSRM router answers "no route" unless `harness.route` is set.
 * requestAnimationFrame is a manual queue so marker glides advance only when
 * a test calls `runFrame(timestamp)`.
 *
 * Test files must still set the env vars and mock `next/navigation` /
 * `next/script` themselves (vi.mock is hoisted per file).
 */
import { vi } from "vitest";
import { act } from "@testing-library/react";

export interface LatLng {
    lat: number;
    lng: number;
}

export interface FakeMarker {
    position: LatLng;
    positions: LatLng[];
    map: unknown;
    content: HTMLElement;
    zIndex: number;
}

export interface FakePolyline {
    map: unknown;
    setMap: (m: unknown) => void;
}

export interface RidePayload {
    status: string;
    message?: string;
    pickup_address: string;
    dropoff_address: string;
    pickup_lat?: number;
    pickup_lng?: number;
    dropoff_lat?: number;
    dropoff_lng?: number;
    ride_code?: string;
    eta_minutes?: number | null;
    driver?: {
        name: string;
        lat?: number;
        lng?: number;
        license_plate?: string;
        vehicle_make?: string;
        vehicle_model?: string;
        vehicle_color?: string;
    } | null;
}

export const harness = {
    ride: null as RidePayload | null,
    status: 200,
    /** OSRM geometry ([lng, lat] pairs) to answer with; null → no route. */
    route: null as [number, number][] | null,
    markers: [] as FakeMarker[],
    polylines: [] as FakePolyline[],
    mapListeners: new Map<string, () => void>(),
    mapHeading: 0,
};

export const PICKUP = { lat: 52.125, lng: -106.66 };
export const DROPOFF = { lat: 52.14, lng: -106.64 };

/** A live-ride payload in the shape backend/routes/rides/sharing.py returns. */
export function activeRide(
    status: string,
    driverPos: LatLng | null,
    extra: Partial<RidePayload> = {},
): RidePayload {
    return {
        status,
        pickup_address: "1 Pickup St",
        dropoff_address: "2 Dropoff Ave",
        pickup_lat: PICKUP.lat,
        pickup_lng: PICKUP.lng,
        dropoff_lat: DROPOFF.lat,
        dropoff_lng: DROPOFF.lng,
        ride_code: "ABC123",
        eta_minutes: 7,
        driver: driverPos
            ? { name: "Sam", lat: driverPos.lat, lng: driverPos.lng, license_plate: "TEST 123" }
            : null,
        ...extra,
    };
}

/** The terminal payload: status + message + addresses, nothing else. */
export function endedRide(status: "completed" | "cancelled"): RidePayload {
    return {
        status,
        message: "This ride has ended.",
        pickup_address: "1 Pickup St",
        dropoff_address: "2 Dropoff Ave",
    };
}

// ── Manual requestAnimationFrame ─────────────────────────────────────
let rafQueue = new Map<number, FrameRequestCallback>();
let rafId = 0;

export function pendingFrames(): number {
    return rafQueue.size;
}

export function runFrame(timestamp: number) {
    const due = rafQueue;
    rafQueue = new Map();
    act(() => {
        due.forEach((cb) => cb(timestamp));
    });
}

// ── Fake Google Maps ─────────────────────────────────────────────────
function installGoogleMaps() {
    class FakeMap {
        addListener(event: string, cb: () => void) {
            harness.mapListeners.set(event, cb);
        }
        getHeading() {
            return harness.mapHeading;
        }
        panTo = vi.fn();
        fitBounds = vi.fn();
    }
    class FakeLatLngBounds {
        extend() {}
    }
    class FakeAdvancedMarkerElement implements FakeMarker {
        positions: LatLng[] = [];
        map: unknown;
        content: HTMLElement;
        zIndex: number;
        private _pos: LatLng;
        constructor(opts: { map: unknown; position: LatLng; content: HTMLElement; zIndex: number }) {
            this.map = opts.map;
            this.content = opts.content;
            this.zIndex = opts.zIndex;
            this._pos = opts.position;
            this.positions.push(opts.position);
            harness.markers.push(this);
        }
        get position() {
            return this._pos;
        }
        set position(p: LatLng) {
            this._pos = p;
            this.positions.push(p);
        }
    }
    class FakePolylineImpl implements FakePolyline {
        map: unknown;
        constructor(opts: { map: unknown }) {
            this.map = opts.map;
            harness.polylines.push(this);
        }
        setMap(m: unknown) {
            this.map = m;
        }
    }
    (window as unknown as { google: unknown }).google = {
        maps: {
            Map: FakeMap,
            LatLngBounds: FakeLatLngBounds,
            Polyline: FakePolylineImpl,
            marker: { AdvancedMarkerElement: FakeAdvancedMarkerElement },
        },
    };
}

function installFetch() {
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("router.project-osrm.org")) {
            if (!harness.route) return { ok: false, json: async () => null } as Response;
            const coordinates = harness.route;
            return { ok: true, json: async () => ({ routes: [{ geometry: { coordinates } }] }) } as Response;
        }
        if (url.includes("/api/v1/rides/track/")) {
            const ok = harness.status === 200;
            return { ok, status: harness.status, json: async () => (ok ? harness.ride : { detail: "not found" }) } as Response;
        }
        throw new Error(`unexpected fetch in track page test: ${url}`);
    }) as unknown as typeof fetch;
}

export function setReducedMotion(on: boolean) {
    Object.defineProperty(window, "matchMedia", {
        value: vi.fn((query: string) => ({ matches: on && query.includes("reduce"), media: query })),
        configurable: true,
        writable: true,
    });
}

export function setupTrackHarness() {
    harness.ride = null;
    harness.status = 200;
    harness.route = null;
    harness.markers.length = 0;
    harness.polylines.length = 0;
    harness.mapListeners.clear();
    harness.mapHeading = 0;
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
    // Only the 5 s poll interval is faked; setTimeout stays real so
    // `flush()` can wait out the fetch → json → setState chain.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    installGoogleMaps();
    installFetch();
}

export function teardownTrackHarness() {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    delete (window as unknown as { google?: unknown }).google;
    delete (window as { matchMedia?: unknown }).matchMedia;
}

/** Let pending fetch/json/setState promises settle. */
export async function flush() {
    await act(async () => {
        for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
    });
}

/** Fire the page's next 5 s status poll and let it land. */
export async function poll() {
    act(() => {
        vi.advanceTimersByTime(5000);
    });
    await flush();
}

/** The car: the only marker drawn at zIndex 2, and still on the map. */
export function car(): FakeMarker | undefined {
    return harness.markers.find((m) => m.zIndex === 2 && m.map != null);
}

export function carRotationDeg(m: FakeMarker): number | null {
    const img = m.content.querySelector("img");
    const match = img?.style.transform.match(/rotate\((-?[\d.]+)deg\)/);
    return match ? Number(match[1]) : null;
}

export function liveRouteLines(): number {
    return harness.polylines.filter((p) => p.map != null).length;
}
