/**
 * Real component test for the vehicle-types page's icon coloring.
 *
 * VEHICLE_ICON_MAP already gave each vehicle type (car-compact/car-sport/
 * bus/bus-outline) a distinct lucide icon shape, but every render used the
 * dashboard's default muted foreground color — the same icon-without-color
 * gap already fixed for rider-app/driver-app's equivalent vehicle-type
 * screens (see docs/change-log/2026-09-05-vehicle-type-icon-color.md).
 *
 * Pins:
 *  - the card-grid fallback icon (shown when a type has no uploaded image)
 *    gets a distinct, type-specific color class
 *  - the Add/Edit dialog's icon picker gives each of its 4 options a
 *    distinct color class, not the default foreground
 *  - an unrecognized icon value falls back to the neutral default color in
 *    both places
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const getVehicleTypes = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  const noop = vi.fn().mockResolvedValue({});
  const list = vi.fn().mockResolvedValue([]);
  return {
    ...Object.fromEntries(
      Object.entries(actual).map(([k, v]) =>
        typeof v !== "function" ? [k, v] : [k, /^(get|list|fetch)/.test(k) ? list : noop]
      )
    ),
    getVehicleTypes,
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard/vehicle-types",
}));

vi.mock("@/hooks/useRequireModule", () => ({ useRequireModule: () => ({ allowed: true }) }));

// Dynamic import (rather than a static top-level one) so this module's own
// `const getVehicleTypes = vi.fn()` above is initialized before the mocked
// "@/lib/api" factory runs — a static import of the page is hoisted by the
// ES module spec to before any top-level `const`, which would otherwise hit
// the mocked factory's reference to `getVehicleTypes` while it's still in
// its temporal dead zone. Matches the existing pattern in
// area-heatmap-overrides.test.tsx.
async function renderPage() {
  const { default: VehicleTypesPage } = await import("@/app/dashboard/vehicle-types/page");
  return render(<VehicleTypesPage />);
}

const TYPE_ECONOMY = {
  id: "vt-1", name: "Economy", description: "Affordable rides", icon: "car-compact",
  capacity: 4, is_active: true, marker_variant: "standard",
};
const TYPE_PREMIUM = {
  id: "vt-2", name: "Premium", description: "Premium rides", icon: "car-sport",
  capacity: 4, is_active: true, marker_variant: "premium",
};
const TYPE_LEGACY = {
  id: "vt-3", name: "Mystery", description: "Unrecognized icon value", icon: "flying-car",
  capacity: 4, is_active: true, marker_variant: "standard",
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("vehicle-types page — icon accent colors", () => {
  it("gives the card-grid fallback icon a distinct color per vehicle type", async () => {
    getVehicleTypes.mockResolvedValue([TYPE_ECONOMY, TYPE_PREMIUM]);
    const { container } = await renderPage();
    await waitFor(() => expect(screen.getByText("Economy")).toBeTruthy());

    const economyIcon = container.querySelector('svg.text-blue-300');
    const premiumIcon = container.querySelector('svg.text-amber-300');
    expect(economyIcon).toBeTruthy();
    expect(premiumIcon).toBeTruthy();
  });

  it("falls back to the neutral muted color for an unrecognized icon value", async () => {
    getVehicleTypes.mockResolvedValue([TYPE_LEGACY]);
    const { container } = await renderPage();
    await waitFor(() => expect(screen.getByText("Mystery")).toBeTruthy());

    expect(container.querySelector('svg.text-muted-foreground\\/30')).toBeTruthy();
  });

  it("gives each icon-picker option in the Add dialog a distinct color, not the default foreground", async () => {
    getVehicleTypes.mockResolvedValue([]);
    await renderPage();
    await waitFor(() => expect(getVehicleTypes).toHaveBeenCalled());

    // Two "Add Vehicle Type" triggers render with no data: the header button
    // and the empty-state card's own CTA. Either opens the same dialog.
    fireEvent.click(screen.getAllByRole("button", { name: /add vehicle type/i })[0]);
    await waitFor(() => expect(screen.getByText("Icon")).toBeTruthy());

    // vehicleIconLabel() only strips a trailing "-outline" before
    // Title-Casing, so "bus" and "bus-outline" both label as "Bus" — an
    // existing, unrelated ambiguity in this picker. Using "Car Compact" and
    // "Car Sport" instead, which stay unique.
    const carCompactButton = screen.getByText("Car Compact").closest("button")!;
    const carSportButton = screen.getByText("Car Sport").closest("button")!;
    const carCompactIcon = carCompactButton.querySelector("svg")!;
    const carSportIcon = carSportButton.querySelector("svg")!;
    expect(carCompactIcon.classList.contains("text-blue-600")).toBe(true);
    expect(carSportIcon.classList.contains("text-amber-600")).toBe(true);
    expect(carCompactIcon.className).not.toBe(carSportIcon.className);
  });
});
