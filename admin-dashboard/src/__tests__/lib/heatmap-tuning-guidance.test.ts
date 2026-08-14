/**
 * The per-area tuning form exposes eleven numeric knobs. Two of them are the
 * ones an operator reaches for first and the two that carry a real cost:
 *
 *   - k_floor is the k-anonymity guarantee, and the obvious "fix" for an empty
 *     map is to lower it — which trades a PIPEDA guarantee for coverage.
 *   - refresh_seconds applies to every online driver in the area at once, so
 *     halving it doubles their battery/data use and this area's heatmap load.
 *
 * These tests pin that both are called out against what the area would
 * otherwise inherit, and that the quiet cases stay quiet — a form that warns
 * about everything trains operators to dismiss it.
 */

import { describe, it, expect } from "vitest";

import {
  TUNING_PLAYBOOK,
  tuningWarnings,
  warningsFor,
} from "@/lib/heatmap-tuning-guidance";

const INHERITED = {
  k_floor: 3,
  refresh_seconds: 90,
  cell_lat_deg: 0.004,
  cell_lng_deg: 0.006,
  baseline_window_days: 28,
};

describe("tuningWarnings", () => {
  it("stays silent when nothing is overridden", () => {
    // Inheriting the platform value is never worth a warning — that value is
    // the reviewed one.
    expect(tuningWarnings({}, INHERITED)).toEqual([]);
  });

  it("warns when the privacy floor is lowered below the platform value", () => {
    const warnings = tuningWarnings({ k_floor: 1 }, INHERITED);
    const floor = warningsFor("k_floor", warnings);

    expect(floor).toHaveLength(1);
    expect(floor[0].severity).toBe("warning");
    // It must name the alternative, or it's just an obstacle: the operator
    // still has an empty map to fix.
    expect(floor[0].message).toMatch(/Usually busy/i);
  });

  it("does not warn when the privacy floor is RAISED", () => {
    // Raising it is strictly safer for privacy; only coverage suffers, and the
    // playbook already covers that direction.
    expect(tuningWarnings({ k_floor: 5 }, INHERITED)).toEqual([]);
  });

  it("does not warn when the floor matches what it inherits", () => {
    // Ticking "override" without changing the value is a no-op in effect.
    expect(tuningWarnings({ k_floor: 3 }, INHERITED)).toEqual([]);
  });

  it("quantifies the cost of a shorter refresh interval", () => {
    const warnings = tuningWarnings({ refresh_seconds: 30 }, INHERITED);
    const refresh = warningsFor("refresh_seconds", warnings);

    expect(refresh).toHaveLength(1);
    expect(refresh[0].severity).toBe("warning");
    // 90s -> 30s is 3x the polling. A vague "this uses more battery" doesn't
    // let anyone judge whether it's acceptable.
    expect(refresh[0].message).toContain("3.0×");
    expect(refresh[0].message).toMatch(/every online driver/i);
  });

  it("does not warn when the refresh interval is lengthened", () => {
    expect(tuningWarnings({ refresh_seconds: 180 }, INHERITED)).toEqual([]);
  });

  it("flags smaller cells as sparser, not more detailed", () => {
    // The counterintuitive one: shrinking cells while the floor is unchanged
    // makes MORE cells fall under it, so the map empties out and the change
    // looks like it did nothing.
    const warnings = tuningWarnings(
      { cell_lat_deg: 0.001, cell_lng_deg: 0.002 },
      INHERITED
    );

    expect(warnings).toHaveLength(2);
    expect(warnings.every((w) => w.severity === "info")).toBe(true);
    expect(warningsFor("cell_lat_deg", warnings)[0].message).toMatch(/sparser/i);
  });

  it("reports every applicable warning at once, not just the first", () => {
    const warnings = tuningWarnings(
      { k_floor: 1, refresh_seconds: 45, cell_lat_deg: 0.002 },
      INHERITED
    );
    expect(warnings.map((w) => w.key).sort()).toEqual([
      "cell_lat_deg",
      "k_floor",
      "refresh_seconds",
    ]);
  });

  it("stays silent when the inherited value is missing rather than guessing", () => {
    // A partial config response must not produce a warning comparing against
    // undefined — that would render "below the platform value (undefined)".
    expect(tuningWarnings({ k_floor: 1 }, {})).toEqual([]);
  });

  it("only warns about keys it actually understands", () => {
    // Adding a knob to HEATMAP_SPEC surfaces it in the form automatically; it
    // must not start producing bogus warnings until someone writes one.
    expect(tuningWarnings({ some_future_key: 1 }, { some_future_key: 5 })).toEqual([]);
  });
});

describe("TUNING_PLAYBOOK", () => {
  it("states a trade-off for every entry", () => {
    // The entire point is that none of these knobs is free. An entry with an
    // action and no cost would read as a recommendation.
    expect(TUNING_PLAYBOOK.length).toBeGreaterThan(0);
    for (const entry of TUNING_PLAYBOOK) {
      expect(entry.symptom.length).toBeGreaterThan(0);
      expect(entry.action.length).toBeGreaterThan(0);
      expect(entry.tradeoff.length).toBeGreaterThan(0);
    }
  });

  it("never recommends lowering the privacy floor", () => {
    // Coverage problems have other fixes; this one is not on the menu.
    for (const entry of TUNING_PLAYBOOK) {
      expect(entry.action).not.toMatch(/lower.*(privacy|k_floor|k-anonymity)/i);
    }
  });
});
