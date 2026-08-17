/**
 * Real coverage for the monitoring toolbar.
 *
 * Both this component and monitoring-map.tsx are replaced by empty stubs in the
 * page smoke suite — so the tests that "cover" AD-01 (the live demand overlay)
 * run, pass, and never execute a line of either file. This is the file that
 * makes the toggles, the filter wiring and the accessible state real coverage
 * instead of a green checkmark.
 *
 * The demand toggle in particular is the whole entry point to AD-01: if it
 * stops emitting `showDemand`, the overlay silently never turns on and nothing
 * else in the suite would notice.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { MonitoringToolbar } from "@/app/dashboard/monitoring/toolbar";
import type { MonitoringCounts, MonitoringFilters } from "@/app/dashboard/monitoring/types";

const COUNTS: MonitoringCounts = { online: 12, onRide: 3, offline: 5, activeRides: 4 };

const FILTERS: MonitoringFilters = {
  showOnline: true,
  showOffline: false,
  showRides: true,
  showDemand: false,
  serviceAreaId: null,
  vehicleTypeId: null,
};

const onFilterChange = vi.fn();
const onSearchChange = vi.fn();
const onFollowToggle = vi.fn();

function renderToolbar(overrides: Partial<MonitoringFilters> = {}, props: Record<string, unknown> = {}) {
  return render(
    <MonitoringToolbar
      counts={COUNTS}
      filters={{ ...FILTERS, ...overrides }}
      onFilterChange={onFilterChange}
      searchQuery=""
      onSearchChange={onSearchChange}
      followMode={false}
      onFollowToggle={onFollowToggle}
      serviceAreas={[{ id: "area-1", name: "Regina" }]}
      vehicleTypes={[{ id: "vt-1", name: "Standard" }]}
      wsStatus="connected"
      {...props}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("live counters", () => {
  it("shows each count from props", () => {
    renderToolbar();
    expect(screen.getByText(/12 Online/)).toBeTruthy();
    expect(screen.getByText(/3 On Ride/)).toBeTruthy();
    expect(screen.getByText(/5 Offline/)).toBeTruthy();
    expect(screen.getByText(/4 Rides/)).toBeTruthy();
  });
});

describe("filter toggles", () => {
  it("turns the demand overlay on", () => {
    // AD-01's entry point. Nothing else in the suite exercises it.
    renderToolbar();
    fireEvent.click(screen.getByRole("button", { name: /Demand/i }));
    expect(onFilterChange).toHaveBeenCalledWith({ showDemand: true });
  });

  it("turns the demand overlay back off", () => {
    renderToolbar({ showDemand: true });
    fireEvent.click(screen.getByRole("button", { name: /Demand/i }));
    expect(onFilterChange).toHaveBeenCalledWith({ showDemand: false });
  });

  it.each([
    [/Online/, "showOnline", true, false],
    [/Offline/, "showOffline", false, true],
    [/Rides/, "showRides", true, false],
  ])("toggles %s", (label, key, current, expected) => {
    renderToolbar({ [key]: current } as Partial<MonitoringFilters>);
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(onFilterChange).toHaveBeenCalledWith({ [key]: expected });
  });
});

describe("accessible toggle state", () => {
  it.each([
    [/Demand/i, "showDemand"],
    [/Online/, "showOnline"],
    [/Offline/, "showOffline"],
    [/Rides/, "showRides"],
  ])("exposes %s state via aria-pressed, not colour alone", (label, key) => {
    // These toggles signal on/off purely by background colour, which is
    // invisible to a screen reader and to anyone who cannot distinguish the
    // tint. aria-pressed is the only thing carrying the state.
    const { unmount } = renderToolbar({ [key]: true } as Partial<MonitoringFilters>);
    expect(screen.getByRole("button", { name: label }).getAttribute("aria-pressed")).toBe("true");
    unmount();

    renderToolbar({ [key]: false } as Partial<MonitoringFilters>);
    expect(screen.getByRole("button", { name: label }).getAttribute("aria-pressed")).toBe("false");
  });

  it("hides decorative emoji from assistive tech", () => {
    // "🚗 4 Rides" should announce as "4 Rides", not "automobile 4 Rides".
    const { container } = renderToolbar();
    const hidden = container.querySelectorAll('[aria-hidden="true"]');
    const texts = Array.from(hidden).map((n) => n.textContent);
    expect(texts).toContain("🚗");
    expect(texts).toContain("🔥");
  });
});

describe("service area filter", () => {
  it("labels the area select for assistive tech", () => {
    renderToolbar();
    expect(screen.getByLabelText(/Filter by service area/i)).toBeTruthy();
  });

  it("disables the vehicle filter when the selected area has no vehicle types", () => {
    // Otherwise the operator picks from an empty list and reasonably concludes
    // the filter is broken rather than that the area has nothing configured.
    renderToolbar({ serviceAreaId: "area-1" }, { vehicleTypes: [] });
    const trigger = screen.getByLabelText(/vehicle/i);
    expect(trigger.hasAttribute("disabled") || trigger.getAttribute("data-disabled") !== null).toBe(
      true,
    );
  });
});

describe("search", () => {
  it("reports what the operator typed", () => {
    renderToolbar();
    const input = screen.getByPlaceholderText(/Driver name or ride ID/i);
    fireEvent.change(input, { target: { value: "SK-1234" } });
    expect(onSearchChange).toHaveBeenCalledWith("SK-1234");
  });
});

describe("websocket status", () => {
  it("distinguishes connected from disconnected", () => {
    // The operator's only cue that the map has stopped updating.
    const { unmount } = renderToolbar({}, { wsStatus: "connected" });
    const connected = document.body.textContent ?? "";
    unmount();

    renderToolbar({}, { wsStatus: "disconnected" });
    expect(document.body.textContent).not.toBe(connected);
  });
});
