/**
 * /track/[rideId] — clear "arrived" and "trip ended" states (UX program W4.2).
 *
 * Statuses come from backend/routes/rides/sharing.py `track_shared_ride`:
 * a live ride returns its status (driver_arrived included) with the driver's
 * position; a terminal one (completed / cancelled, RideStatus
 * .terminal_statuses()) returns only status, message and the two addresses;
 * an unknown or expired token is a 404.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
    activeRide,
    car,
    endedRide,
    flush,
    harness,
    liveRouteLines,
    pendingFrames,
    poll,
    setupTrackHarness,
    teardownTrackHarness,
} from "./track-test-harness";

vi.hoisted(() => {
    process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY = "test-key";
    process.env.NEXT_PUBLIC_API_URL = "https://api.test";
});

vi.mock("next/navigation", () => ({ useParams: () => ({ rideId: "share-token" }) }));

vi.mock("next/script", async () => {
    const R = await import("react");
    return {
        default: function FakeScript({ onLoad }: { onLoad?: () => void }) {
            R.useEffect(() => onLoad?.(), []);
            return null;
        },
    };
});

import TrackRide from "./page";

const DRIVER = { lat: 52.13, lng: -106.67 };
const NEARER = { lat: 52.1305, lng: -106.6695 };
// A short OSRM line near the driver (OSRM order: [lng, lat]).
const ROUTE: [number, number][] = [
    [-106.67, 52.13],
    [-106.665, 52.128],
    [-106.66, 52.125],
];

async function mount(ride = activeRide("driver_accepted", DRIVER)) {
    harness.ride = ride;
    const utils = render(<TrackRide />);
    await flush();
    return utils;
}

beforeEach(() => {
    setupTrackHarness();
});

afterEach(() => {
    teardownTrackHarness();
});

describe("/track driver arrived", () => {
    it("says 'The driver has arrived' in place of the drop-off ETA", async () => {
        const { unmount } = await mount();
        expect(screen.getByText("min away")).toBeInTheDocument();
        expect(screen.getByText("ETA 7 min")).toBeInTheDocument();

        harness.ride = activeRide("driver_arrived", DRIVER);
        await poll();

        // Viewers are usually the rider's contacts, so the copy is not "Your
        // driver". The visible headline and the screen-reader announcement
        // say exactly the same thing.
        const status = screen.getByRole("status");
        expect(status).toHaveTextContent(/^The driver has arrived$/);
        const visible = screen.getAllByText("The driver has arrived").filter((el) => el !== status);
        expect(visible).toHaveLength(1);
        expect(screen.queryByText(/your driver/i)).not.toBeInTheDocument();
        expect(screen.queryByText("min away")).not.toBeInTheDocument();
        expect(screen.queryByText("ETA 7 min")).not.toBeInTheDocument();
        // The car stays where the driver is — only the status copy changes.
        expect(car()?.position).toEqual(DRIVER);
        // Driver card (first name + plate, as before) is still there.
        expect(screen.getByText("Sam")).toBeInTheDocument();
        expect(screen.getByText("TEST 123")).toBeInTheDocument();
        unmount();
    });
});

describe("/track trip ended", () => {
    it("completed mid-trip: shows 'Trip ended' and clears the car, glide and route line", async () => {
        harness.route = ROUTE;
        const { unmount } = await mount(activeRide("in_progress", DRIVER));
        expect(car()).toBeDefined();
        expect(liveRouteLines()).toBeGreaterThan(0);

        // A glide is under way when the trip ends.
        harness.ride = activeRide("in_progress", NEARER);
        await poll();
        expect(pendingFrames()).toBe(1);

        harness.ride = endedRide("completed");
        await poll();

        expect(screen.getAllByText("Trip ended").length).toBeGreaterThan(0);
        expect(screen.getByText("Live location is no longer shared.")).toBeInTheDocument();
        expect(screen.getByRole("status")).toHaveTextContent("Trip ended");
        expect(car()).toBeUndefined();
        expect(pendingFrames()).toBe(0);
        expect(liveRouteLines()).toBe(0);
        expect(screen.queryByText("min away")).not.toBeInTheDocument();
        expect(screen.queryByText(/^ETA /)).not.toBeInTheDocument();
        // The addresses the terminal payload still carries stay visible.
        expect(screen.getByText("1 Pickup St")).toBeInTheDocument();
        expect(screen.getByText("2 Dropoff Ave")).toBeInTheDocument();
        unmount();
    });

    it("cancelled: shows 'Trip cancelled' with the same ended treatment", async () => {
        const { unmount } = await mount();
        harness.ride = endedRide("cancelled");
        await poll();
        expect(screen.getAllByText("Trip cancelled").length).toBeGreaterThan(0);
        expect(screen.getByText("Live location is no longer shared.")).toBeInTheDocument();
        expect(car()).toBeUndefined();
        unmount();
    });

    it("an OSRM answer that lands after the trip ended draws no route", async () => {
        let release!: () => void;
        harness.routeGate = new Promise<void>((r) => {
            release = r;
        });
        harness.route = ROUTE;
        const { unmount } = await mount(activeRide("in_progress", DRIVER));
        expect(liveRouteLines()).toBe(0); // request still in flight

        harness.ride = endedRide("completed");
        await poll();
        release();
        await flush();

        expect(liveRouteLines()).toBe(0);
        unmount();
    });

    it("a link opened after the trip ended shows the ended state, not an empty live map", async () => {
        const { unmount } = await mount(endedRide("completed"));
        expect(screen.getAllByText("Trip ended").length).toBeGreaterThan(0);
        expect(screen.getByText("Live location is no longer shared.")).toBeInTheDocument();
        expect(car()).toBeUndefined();
        unmount();
    });
});

describe("/track invalid or expired link", () => {
    it("keeps the existing 'Tracking unavailable' state when the token stops resolving", async () => {
        const { unmount } = await mount();
        harness.status = 404;
        await poll();
        expect(screen.getByText("Tracking unavailable")).toBeInTheDocument();
        expect(screen.getByText("Tracking link is invalid or has expired.")).toBeInTheDocument();
        unmount();
    });
});
