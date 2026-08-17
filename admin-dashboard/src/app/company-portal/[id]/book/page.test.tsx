/**
 * Corporate portal "book a ride for a customer" page.
 *
 * Covers the location-bias fix: without it, Places autocomplete searches
 * unrestricted Canada-wide (a Regina booker typing "airport" could surface
 * Saskatoon/Calgary/Toronto ahead of Regina's), matching the exact gap
 * rider-app/app/search-destination.tsx already closed for mobile. This
 * proves the portal now sends the same location=/radius= bias the backend
 * maps proxy turns into a hard 50km locationRestriction (see
 * backend/utils/google_places_new.py::build_autocomplete_payload).
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
    useParams: () => ({ id: "co-1" }),
}));

const companyRequest = vi.fn();
const companyBookingFareEstimate = vi.fn();
const createCompanyBooking = vi.fn();
const getPortalVehicleTypes = vi.fn().mockResolvedValue([
    { id: "veh-standard", name: "Standard", is_active: true },
]);

vi.mock("@/lib/companyApi", () => ({
    companyRequest: (...a: unknown[]) => companyRequest(...a),
    companyBookingFareEstimate: (...a: unknown[]) => companyBookingFareEstimate(...a),
    createCompanyBooking: (...a: unknown[]) => createCompanyBooking(...a),
    getPortalVehicleTypes: (...a: unknown[]) => getPortalVehicleTypes(...a),
}));

// ---------- stub UI primitives (avoid pulling in the real design system) ----------
vi.mock("@/components/ui/card", () => ({
    Card: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
    CardContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
    CardHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
    CardTitle: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}));
vi.mock("@/components/ui/button", () => ({
    Button: ({ children, ...p }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
        <button {...p}>{children}</button>
    ),
}));
vi.mock("@/components/ui/input", () => ({
    Input: (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} />,
}));

async function importPage() {
    const mod = await import("./page");
    return mod.default;
}

describe("company portal booking — address autocomplete location bias", () => {
    beforeEach(() => {
        vi.resetModules();
        companyRequest.mockReset();
        companyBookingFareEstimate.mockReset();
        createCompanyBooking.mockReset();
        // Deterministic browser geolocation — Regina, SK.
        Object.defineProperty(global.navigator, "geolocation", {
            configurable: true,
            value: {
                getCurrentPosition: (success: PositionCallback) =>
                    success({
                        coords: { latitude: 50.4452, longitude: -104.6189 },
                    } as GeolocationPosition),
            },
        });
    });

    it("biases the pickup search with the booker's geolocation", async () => {
        companyRequest.mockResolvedValue({ predictions: [] });
        const Page = await importPage();
        render(<Page />);

        const [pickupInput] = screen.getAllByPlaceholderText("Search address…");
        fireEvent.change(pickupInput, { target: { value: "Regina airport" } });

        await waitFor(
            () => {
                expect(companyRequest).toHaveBeenCalled();
            },
            { timeout: 1000 }
        );

        const url = companyRequest.mock.calls[0][0] as string;
        expect(url).toContain("input=Regina+airport");
        expect(url).toContain("location=50.4452%2C-104.6189");
        expect(url).toContain("radius=50000");
    });

    it("biases the dropoff search with the selected pickup's coordinates, not just geolocation", async () => {
        companyRequest.mockImplementation(async (url: string) => {
            if (url.includes("/places/details")) {
                return { lat: 50.43, lng: -104.66 }; // a specific pickup point
            }
            return { predictions: [] };
        });
        const Page = await importPage();
        render(<Page />);

        const [pickupInput, dropoffInput] = screen.getAllByPlaceholderText("Search address…");

        // Type + pick a pickup suggestion so the component has a real point to
        // anchor dropoff search on.
        companyRequest.mockResolvedValueOnce({
            predictions: [{ place_id: "p1", description: "123 Main St, Regina" }],
        });
        fireEvent.change(pickupInput, { target: { value: "123 Main" } });
        const suggestion = await screen.findByText("123 Main St, Regina");
        fireEvent.mouseDown(suggestion);
        fireEvent.click(suggestion);

        await waitFor(() => {
            expect(screen.getByText(/123 Main St, Regina/)).toBeInTheDocument();
        });

        companyRequest.mockClear();
        companyRequest.mockResolvedValue({ predictions: [] });
        fireEvent.change(dropoffInput, { target: { value: "airport" } });

        await waitFor(() => {
            expect(companyRequest).toHaveBeenCalled();
        });

        const dropoffUrl = companyRequest.mock.calls.find((c) => (c[0] as string).includes("input=airport"))?.[0] as
            | string
            | undefined;
        expect(dropoffUrl).toBeDefined();
        // Anchored on the PICKUP point just selected (50.43,-104.66), not the
        // raw browser geolocation (50.4452,-104.6189) — proves the chained
        // bias, matching rider-app's "search near the other leg" pattern.
        expect(dropoffUrl).toContain("location=50.43%2C-104.66");
    });
});
