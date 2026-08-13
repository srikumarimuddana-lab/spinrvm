/**
 * Unit tests for the demand/supply-ratio presentation logic.
 *
 * These functions were previously inlined across four components and had zero
 * coverage — the components carrying them were fully stubbed out in the only
 * test that touched those pages. Boundary conditions here (tier edges, 0/0
 * areas, division guards) are exactly the kind that regress silently on an
 * ops screen: nothing crashes, the numbers are just wrong.
 */

import { describe, it, expect } from "vitest";
import {
  DEMAND_BANDS,
  NO_DATA_COLOR,
  bandForRatio,
  bandRangeLabel,
  demandBarWidths,
  demandFillColor,
  demandFillOpacity,
  demandPressure,
  isDormant,
} from "@/lib/demand-bands";

describe("bandForRatio", () => {
  // Boundaries mirror backend/utils/surge_engine.py SURGE_TIERS exactly.
  it.each([
    [5.0, "critical"],
    [3.0, "critical"], // inclusive lower bound
    [2.999, "high"],
    [2.0, "high"],
    [1.999, "elevated"],
    [1.2, "elevated"],
    [1.199, "building"],
    [0.8, "building"],
    [0.799, "balanced"],
    [0.5, "balanced"],
    [0.499, "oversupply"],
    [0, "oversupply"],
  ])("ratio %s falls in the %s band", (ratio, key) => {
    expect(bandForRatio(ratio).key).toBe(key);
  });

  it("does not merge the 1.25x and 1.5x surge tiers into one band", () => {
    // A single 0.5-1.2 "balanced" band hid the fact that riders in the
    // 0.8-1.2 range are already paying 1.5x.
    expect(bandForRatio(0.6).key).not.toBe(bandForRatio(1.0).key);
    expect(bandForRatio(0.6).multiplier).toBe(1.25);
    expect(bandForRatio(1.0).multiplier).toBe(1.5);
  });

  it("never reports a multiplier above the 2.5x hard cap", () => {
    for (const band of DEMAND_BANDS) {
      expect(band.multiplier).toBeLessThanOrEqual(2.5);
    }
  });

  it("falls back to a band for NaN rather than throwing", () => {
    expect(bandForRatio(NaN).key).toBe("oversupply");
  });
});

describe("bandRangeLabel", () => {
  it("never suffixes a ratio with the surge-multiplier 'x' notation", () => {
    // "Critical (>=3.0x)" read as a 3x fare, above the 2.5x legal cap.
    for (const band of DEMAND_BANDS) {
      expect(bandRangeLabel(band)).not.toMatch(/[x×]/);
    }
  });

  it("describes the top and bottom bands as open-ended", () => {
    expect(bandRangeLabel(DEMAND_BANDS[0])).toBe("≥ 3.0");
    expect(bandRangeLabel(DEMAND_BANDS[DEMAND_BANDS.length - 1])).toBe("< 0.5");
  });
});

describe("isDormant", () => {
  it("treats zero demand and zero supply as inactive, not oversupplied", () => {
    expect(isDormant(0, 0)).toBe(true);
    expect(isDormant(0, 3)).toBe(false);
    expect(isDormant(3, 0)).toBe(false);
  });
});

describe("demandFillColor / demandFillOpacity", () => {
  it("paints a dormant area neutral instead of oversupply purple", () => {
    expect(demandFillColor(0, 0, 0)).toBe(NO_DATA_COLOR);
    expect(demandFillColor(0, 5, 0)).toBe(bandForRatio(0).color);
  });

  it("clamps opacity so polygons never obscure the basemap", () => {
    expect(demandFillOpacity(50, 1, 50)).toBeLessThanOrEqual(0.35);
    expect(demandFillOpacity(1, 10, 0.1)).toBeGreaterThanOrEqual(0.06);
  });

  it("survives a NaN ratio", () => {
    expect(Number.isFinite(demandFillOpacity(1, 1, NaN))).toBe(true);
  });
});

describe("demandBarWidths", () => {
  it("returns an empty track for a dormant area rather than a full green bar", () => {
    // A 100%-green bar made dead or misconfigured areas look fully healthy.
    expect(demandBarWidths(0, 0)).toEqual({ supplyPct: 0, gapPct: 0 });
  });

  it("shows the shortfall when demand exceeds supply", () => {
    const { supplyPct, gapPct } = demandBarWidths(10, 4);
    expect(supplyPct).toBeCloseTo(40);
    expect(gapPct).toBeCloseTo(60);
  });

  it("shows no shortfall when supply meets or exceeds demand", () => {
    expect(demandBarWidths(3, 10).gapPct).toBe(0);
    expect(demandBarWidths(5, 5)).toEqual({ supplyPct: 100, gapPct: 0 });
  });

  it("handles zero supply with demand present without dividing by zero", () => {
    const { supplyPct, gapPct } = demandBarWidths(7, 0);
    expect(supplyPct).toBe(0);
    expect(gapPct).toBe(100);
  });

  it("never produces NaN or a combined width above 100", () => {
    const cases: Array<[number, number]> = [
      [0, 0], [0, 5], [5, 0], [5, 5], [10, 3], [3, 10], [1, 1],
    ];
    for (const [d, s] of cases) {
      const { supplyPct, gapPct } = demandBarWidths(d, s);
      expect(Number.isNaN(supplyPct)).toBe(false);
      expect(Number.isNaN(gapPct)).toBe(false);
      expect(supplyPct + gapPct).toBeLessThanOrEqual(100.001);
    }
  });
});

describe("demandPressure", () => {
  it("never goes negative when supply exceeds demand", () => {
    expect(demandPressure(2, 9)).toBe(0);
  });

  it("reports the excess of demand over idle supply", () => {
    expect(demandPressure(20, 5)).toBe(15);
  });
});
