/**
 * Real component test for the per-area heatmap override form.
 *
 * Written as a mount-and-interact test rather than another entry in the page
 * smoke suite deliberately: that suite stubs the components it "covers", so a
 * form whose whole job is deciding which keys get sent would have had zero
 * real coverage there. Only the network layer is mocked here.
 *
 * The behaviour under test is the override/inherit distinction. "Inherits 3"
 * and "explicitly set to 3" render almost identically but diverge the moment
 * the platform value changes, so getting the saved payload wrong is a silent
 * failure — the form would look right and the area would quietly stop
 * tracking the global.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";

const getAreaHeatmapConfig = vi.fn();
const updateServiceArea = vi.fn();

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
    getAreaHeatmapConfig,
    updateServiceArea,
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard/service-areas",
}));

vi.mock("@/hooks/useRequireModule", () => ({ useRequireModule: () => true }));

const CONFIG = {
  area_id: "area-1",
  area_name: "Saskatoon",
  // k_floor overridden to 9; everything else inherits.
  overrides: { k_floor: 9 },
  inherited: {
    k_floor: 3,
    cell_lat_deg: 0.004,
    cell_lng_deg: 0.006,
    decay_half_life_days: 3,
    refresh_seconds: 90,
    live_window_days: 7,
    now_window_minutes: 10,
    baseline_window_days: 28,
    scheduled_lookahead_hours: 2,
    forecast_hours_ahead: 6,
    forecast_lookback_days: 28,
  },
  effective: { k_floor: 9, refresh_seconds: 90, baseline_window_days: 28 },
  spec: {
    k_floor: { kind: "int", min: 1, max: 50, default: 3, global_key: "heatmap_k_floor" },
    refresh_seconds: { kind: "int", min: 30, max: 600, default: 90, global_key: "heatmap_refresh_seconds" },
    baseline_window_days: { kind: "int", min: 7, max: 90, default: 28, global_key: null },
  },
};

async function mountForm() {
  const { AreaHeatmapOverrides } = await import("@/app/dashboard/service-areas/page");
  await act(async () => {
    render(
      <AreaHeatmapOverrides
        areaId="area-1"
        areaName="Saskatoon"
        onSuccess={vi.fn()}
        onError={vi.fn()}
      />
    );
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  getAreaHeatmapConfig.mockResolvedValue(structuredClone(CONFIG));
  updateServiceArea.mockResolvedValue({});
});

describe("loading and display", () => {
  it("shows which values are overridden and which are inherited", async () => {
    await mountForm();
    // The overridden key shows the platform value it is diverging from...
    expect(screen.getByText(/platform: 3/)).toBeTruthy();
    // ...while untouched keys say plainly that they follow the global.
    expect(screen.getByText(/inherits 90/)).toBeTruthy();
  });

  it("summarises the override count so the area's state is obvious at a glance", async () => {
    await mountForm();
    expect(screen.getByText(/1 value overridden/)).toBeTruthy();
  });

  it("disables inputs for inherited keys so a stray edit can't silently override", async () => {
    await mountForm();
    const refresh = screen.getByRole("spinbutton", { name: /Refresh interval/i }) as HTMLInputElement;
    expect(refresh.disabled).toBe(true);
    const kFloor = screen.getByRole("spinbutton", { name: /Privacy floor/i }) as HTMLInputElement;
    expect(kFloor.disabled).toBe(false);
  });

  it("blocks editing entirely when the config fails to load", async () => {
    // Falling through to an editable form showing defaults would let the next
    // save overwrite real per-area tuning the operator never saw.
    getAreaHeatmapConfig.mockRejectedValue(new Error("boom"));
    await mountForm();
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("spinbutton", { name: /Privacy floor/i })).toBeNull();
  });
});

describe("override toggling", () => {
  it("seeds a newly-overridden key with the inherited value", async () => {
    await mountForm();
    const toggle = screen.getByRole("checkbox", { name: /Override Refresh interval/i });
    await act(async () => { fireEvent.click(toggle); });
    const refresh = screen.getByRole("spinbutton", { name: /Refresh interval/i }) as HTMLInputElement;
    expect(refresh.disabled).toBe(false);
    expect(refresh.value).toBe("90");
  });

  it("clears an override back to inheriting", async () => {
    await mountForm();
    const toggle = screen.getByRole("checkbox", { name: /Override Privacy floor/i });
    await act(async () => { fireEvent.click(toggle); });
    expect(screen.getByText(/inherits 3/)).toBeTruthy();
  });
});

describe("saving", () => {
  it("omits cleared keys so the area goes back to tracking the global", async () => {
    // This is the case that fails silently if the payload is built wrong: the
    // form would look correct while the area stayed pinned to a stale value.
    await mountForm();
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: /Override Privacy floor/i })); });
    await act(async () => { fireEvent.click(screen.getByText(/Save Saskatoon tuning/)); });

    await waitFor(() => expect(updateServiceArea).toHaveBeenCalled());
    const [areaId, payload] = updateServiceArea.mock.calls[0];
    expect(areaId).toBe("area-1");
    expect(payload.heatmap_config).toEqual({});
  });

  it("sends the full override set, including a newly added key", async () => {
    await mountForm();
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: /Override .Usually busy. window/i })); });
    await act(async () => {
      fireEvent.change(screen.getByRole("spinbutton", { name: /.Usually busy. window/i }), { target: { value: "56" } });
    });
    await act(async () => { fireEvent.click(screen.getByText(/Save Saskatoon tuning/)); });

    await waitFor(() => expect(updateServiceArea).toHaveBeenCalled());
    expect(updateServiceArea.mock.calls[0][1].heatmap_config).toEqual({
      k_floor: 9,
      baseline_window_days: 56,
    });
  });

  it("clamps a typed out-of-range value instead of sending a 422", async () => {
    // HTML min/max don't constrain typed input, and the backend rejects
    // out-of-range outright — clamping here keeps the operator out of an
    // error they can't see the cause of.
    await mountForm();
    await act(async () => {
      fireEvent.change(screen.getByRole("spinbutton", { name: /Privacy floor/i }), { target: { value: "999" } });
    });
    await act(async () => { fireEvent.click(screen.getByText(/Save Saskatoon tuning/)); });

    await waitFor(() => expect(updateServiceArea).toHaveBeenCalled());
    expect(updateServiceArea.mock.calls[0][1].heatmap_config.k_floor).toBe(50);
  });

  it("does not save until something changes", async () => {
    await mountForm();
    const button = screen.getByText(/^Saved$/) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("re-reads the server state after saving rather than trusting local state", async () => {
    await mountForm();
    await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: /Override Refresh interval/i })); });
    await act(async () => { fireEvent.click(screen.getByText(/Save Saskatoon tuning/)); });
    // Once on mount, once after the save — so a value the backend clamped or
    // rejected is reflected instead of the optimistic local copy.
    await waitFor(() => expect(getAreaHeatmapConfig).toHaveBeenCalledTimes(2));
  });
});
