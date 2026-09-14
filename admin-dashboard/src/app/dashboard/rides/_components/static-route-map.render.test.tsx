/**
 * StaticRouteMap — rendered coverage.
 *
 * This is the first test at any tier that actually renders either admin ride
 * map. `dashboard-rides`' visual-regression baseline only visits the ride
 * list, and `pages.smoke.test.tsx` stubs `RideDetailModal` out entirely, so
 * until now nothing exercised this component in a DOM — which is how a basemap
 * that rendered an empty box, silently, went undiagnosed across two sessions.
 *
 * The case that matters most here is the one that used to be invisible: every
 * tile failing to load. A single tile 404 at the edge of coverage is normal and
 * stays hidden; the whole source being unreachable (ad/privacy blocker, offline
 * admin, dead host, bad NEXT_PUBLIC_RASTER_TILE_URL) must say so on screen.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import StaticRouteMap from "./static-route-map";

// jsdom has no ResizeObserver and reports every element as 0x0. The component
// refuses to lay out tiles without a measured box, so both have to be supplied
// or the test renders nothing and proves nothing.
const BOX = { w: 600, h: 280 };

beforeEach(() => {
    vi.stubGlobal(
        "ResizeObserver",
        class {
            observe() {}
            unobserve() {}
            disconnect() {}
        },
    );
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
        configurable: true,
        get: () => BOX.w,
    });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
        configurable: true,
        get: () => BOX.h,
    });
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

// Saskatoon → a point a few km away, so the fit lands at a sane zoom.
const ROUTE = {
    pickupLat: 52.1332,
    pickupLng: -106.67,
    dropoffLat: 52.1205,
    dropoffLng: -106.6335,
};

const tiles = () => Array.from(document.querySelectorAll("img"));
const NOTICE = /basemap tiles blocked/i;

describe("StaticRouteMap rendering", () => {
    it("lays out raster tiles and both route pins", () => {
        render(<StaticRouteMap {...ROUTE} />);
        expect(tiles().length).toBeGreaterThan(0);
        // Pins are SVG injected per kind; their titles are the accessible hook.
        expect(document.querySelector('[title="Pickup"]')).toBeTruthy();
        expect(document.querySelector('[title="Dropoff"]')).toBeTruthy();
    });

    it("always renders attribution, even before anything loads", () => {
        render(<StaticRouteMap {...ROUTE} />);
        expect(screen.getByText(/OpenStreetMap contributors/i)).toBeInTheDocument();
    });

    it("says nothing while tiles are loading normally", () => {
        render(<StaticRouteMap {...ROUTE} />);
        expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    });

    // One tile failing at the edge of coverage is ordinary. Shouting about it
    // would train admins to ignore the notice that matters.
    it("stays quiet when only some tiles fail", () => {
        render(<StaticRouteMap {...ROUTE} />);
        const imgs = tiles();
        expect(imgs.length).toBeGreaterThan(1);
        fireEvent.error(imgs[0]);
        expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    });

    it("surfaces a notice once every tile has failed", () => {
        render(<StaticRouteMap {...ROUTE} />);
        tiles().forEach((img) => fireEvent.error(img));
        expect(screen.getByText(NOTICE)).toBeInTheDocument();
    });

    // The forensic content is the point of this renderer. A blocked basemap
    // must not take the route or pins down with it — an SGI/dispute reviewer
    // still needs to see where the ride went.
    it("keeps the pins and route visible when the basemap is blocked", () => {
        const { container } = render(
            <StaticRouteMap
                {...ROUTE}
                paths={[
                    {
                        points: [
                            { lat: 52.1332, lng: -106.67 },
                            { lat: 52.128, lng: -106.655 },
                            { lat: 52.1205, lng: -106.6335 },
                        ],
                    },
                ]}
            />,
        );
        tiles().forEach((img) => fireEvent.error(img));

        expect(screen.getByText(NOTICE)).toBeInTheDocument();
        expect(document.querySelector('[title="Pickup"]')).toBeTruthy();
        expect(document.querySelector('[title="Dropoff"]')).toBeTruthy();
        expect(container.querySelectorAll("polyline").length).toBeGreaterThan(0);
        expect(screen.getByText(/OpenStreetMap contributors/i)).toBeInTheDocument();
    });

    // Re-firing onError for an already-failed tile must not inflate the count
    // and trip the notice while other tiles are still loading fine.
    it("does not let a repeated error on one tile trip the notice", () => {
        render(<StaticRouteMap {...ROUTE} />);
        const imgs = tiles();
        expect(imgs.length).toBeGreaterThan(2);
        for (let i = 0; i < imgs.length + 5; i++) fireEvent.error(imgs[0]);
        expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    });

    it("marks the notice as a status region for assistive tech", () => {
        render(<StaticRouteMap {...ROUTE} />);
        tiles().forEach((img) => fireEvent.error(img));
        expect(screen.getByRole("status")).toHaveTextContent(NOTICE);
    });
});
