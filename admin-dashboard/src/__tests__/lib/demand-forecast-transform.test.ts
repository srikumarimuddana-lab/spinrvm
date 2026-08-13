/**
 * Unit tests for the demand-forecast response transform.
 *
 * Regression suite for a shipped-but-dead feature: the Unmet Demand section
 * mapped `predicted_demand`/`demand`/`time`/`slots`, none of which the backend
 * emits (it returns `{forecast: [{hour, day_name, predicted_rides, is_peak}]}`).
 * Every bar therefore rendered at the floor with a "0 predicted" tooltip,
 * regardless of the real forecast — and the speculative `??` fallbacks are why
 * it looked fine instead of failing loudly.
 */

import { describe, it, expect } from "vitest";
import {
  toForecastSlots,
  forecastBarHeightPct,
  type ForecastSlot,
} from "@/lib/demand-forecast-transform";

// Shape copied from backend/utils/demand_forecast.py's actual return value.
const BACKEND_RESPONSE = {
  hours_ahead: 6,
  area_id: "area-1",
  forecast: [
    { timestamp: "2026-08-13T17:00:00Z", hour: 17, day_name: "Thursday", predicted_rides: 12.5, data_basis: "historical", is_peak: true },
    { timestamp: "2026-08-13T18:00:00Z", hour: 18, day_name: "Thursday", predicted_rides: 4, data_basis: "historical", is_peak: false },
    { timestamp: "2026-08-13T19:00:00Z", hour: 19, day_name: "Thursday", predicted_rides: 0, data_basis: "default", is_peak: false },
  ],
};

describe("toForecastSlots", () => {
  it("reads the fields the backend actually returns", () => {
    const slots = toForecastSlots(BACKEND_RESPONSE);
    expect(slots).toHaveLength(3);
    expect(slots[0].predictedRides).toBe(12.5);
    expect(slots[0].hour).toBe(17);
    expect(slots[0].isPeak).toBe(true);
  });

  it("does not silently produce all-zero values for a valid response", () => {
    // The exact symptom of the original bug.
    const slots = toForecastSlots(BACKEND_RESPONSE);
    expect(slots.some((s) => s.predictedRides > 0)).toBe(true);
  });

  it("formats a zero-padded hour label", () => {
    const slots = toForecastSlots({ forecast: [{ hour: 7, predicted_rides: 1 }] });
    expect(slots[0].label).toBe("07:00");
  });

  it("returns an empty array for missing, null, or malformed payloads", () => {
    expect(toForecastSlots(null)).toEqual([]);
    expect(toForecastSlots(undefined)).toEqual([]);
    expect(toForecastSlots({})).toEqual([]);
    expect(toForecastSlots({ forecast: undefined })).toEqual([]);
    // Not an array — must not throw.
    expect(toForecastSlots({ forecast: "nope" } as never)).toEqual([]);
  });

  it("defaults missing per-slot fields instead of emitting NaN", () => {
    const slots = toForecastSlots({ forecast: [{}] });
    expect(slots[0].predictedRides).toBe(0);
    expect(Number.isNaN(slots[0].predictedRides)).toBe(false);
    expect(slots[0].isPeak).toBe(false);
  });
});

describe("forecastBarHeightPct", () => {
  const slots: ForecastSlot[] = toForecastSlots(BACKEND_RESPONSE);

  it("scales the tallest bar to 100%", () => {
    expect(forecastBarHeightPct(slots[0], slots)).toBeCloseTo(100);
  });

  it("scales a mid bar proportionally, not to the floor", () => {
    const pct = forecastBarHeightPct(slots[1], slots);
    expect(pct).toBeGreaterThan(10);
    expect(pct).toBeLessThan(100);
  });

  it("keeps a visible floor for a non-zero-but-tiny slot", () => {
    const tiny = toForecastSlots({ forecast: [{ hour: 1, predicted_rides: 0.01 }, { hour: 2, predicted_rides: 100 }] });
    expect(forecastBarHeightPct(tiny[0], tiny)).toBeGreaterThanOrEqual(4);
  });

  it("renders an honest flat baseline when nothing is predicted", () => {
    const zeros = toForecastSlots({ forecast: [{ hour: 1, predicted_rides: 0 }] });
    expect(forecastBarHeightPct(zeros[0], zeros)).toBe(2);
  });

  it("never returns NaN", () => {
    expect(Number.isNaN(forecastBarHeightPct(slots[2], slots))).toBe(false);
    expect(Number.isNaN(forecastBarHeightPct(slots[0], []))).toBe(false);
  });
});
