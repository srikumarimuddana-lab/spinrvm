/**
 * Real coverage for AD-01's demand overlay on the monitoring map.
 *
 * monitoring-map.tsx is stubbed out in the page smoke suite (it pulls
 * maplibre-gl), so every test that appeared to cover the live demand overlay
 * executed none of this file. The band thresholds themselves are covered by
 * demand-bands.test.ts — what was uncovered is this file's own wiring: looking
 * an area up in the response, honouring the overlay toggle, and deciding what
 * to paint an area the response does not mention.
 *
 * That last one is where the bug was. Areas absent from `/surge/status`
 * (airport sub-zones, which the endpoint skips, and brand-new areas) were
 * painted the same purple as a genuinely oversupplied market — so a
 * surge-engine outage, which returns nothing for anything, rendered as a
 * uniformly healthy city.
 */

import { describe, it, expect } from "vitest";

import { areaFillColor, areaFillOpacity } from "@/app/dashboard/monitoring/monitoring-map";
import { NO_DATA_COLOR, demandFillColor } from "@/lib/demand-bands";
import type { AreaDemandSupply } from "@/app/dashboard/monitoring/types";

const REGINA: AreaDemandSupply = {
  area_id: "regina",
  name: "Regina",
  demand_count: 12,
  supply_count: 4,
  ratio: 3.0,
  multiplier: 2.0,
  surge_active: true,
  surge_enabled: true,
  source: "auto",
};

const QUIET: AreaDemandSupply = {
  ...REGINA,
  area_id: "moose-jaw",
  name: "Moose Jaw",
  demand_count: 1,
  supply_count: 8,
  ratio: 0.125,
  multiplier: 1.0,
  surge_active: false,
};

describe("overlay toggle", () => {
  it("paints the default fill when the overlay is off", () => {
    // Even with data loaded — the operator turned it off, so the map must not
    // keep colouring by demand.
    const off = areaFillColor("regina", [REGINA], false);
    const on = areaFillColor("regina", [REGINA], true);
    expect(off).not.toBe(on);
  });

  it("paints the default fill before any data has arrived", () => {
    expect(areaFillColor("regina", undefined, true)).toBe(areaFillColor("regina", undefined, false));
  });
});

describe("missing areas are not oversupply", () => {
  it("gives an area absent from the response the no-data colour", () => {
    // The airport sub-zone case: get_surge_status skips child areas entirely.
    expect(areaFillColor("yqr-airport", [REGINA], true)).toBe(NO_DATA_COLOR);
  });

  it("does not paint a missing area the same as a quiet one", () => {
    // The actual defect. "Nobody is asking here" and "we have no idea what is
    // happening here" must not look identical on an ops map.
    const missing = areaFillColor("brand-new-area", [REGINA], true);
    const quiet = areaFillColor("moose-jaw", [REGINA, QUIET], true);
    expect(missing).not.toBe(quiet);
  });

  it("renders an empty response as no-data everywhere, not a healthy city", () => {
    // A surge-engine outage returns []. Every area should read "unknown".
    for (const id of ["regina", "saskatoon", "moose-jaw"]) {
      expect(areaFillColor(id, [], true)).toBe(NO_DATA_COLOR);
    }
  });
});

describe("per-area lookup", () => {
  it("colours each area from its own row, not the first one", () => {
    const busy = areaFillColor("regina", [REGINA, QUIET], true);
    const quiet = areaFillColor("moose-jaw", [REGINA, QUIET], true);

    expect(busy).toBe(demandFillColor(REGINA.demand_count, REGINA.supply_count, REGINA.ratio));
    expect(quiet).toBe(demandFillColor(QUIET.demand_count, QUIET.supply_count, QUIET.ratio));
    expect(busy).not.toBe(quiet);
  });

  it("delegates the threshold decision to the shared band module", () => {
    // Pinned so the map and the heatmap page's cards cannot drift into
    // disagreeing about what a given ratio means.
    expect(areaFillColor("regina", [REGINA], true)).toBe(
      demandFillColor(REGINA.demand_count, REGINA.supply_count, REGINA.ratio),
    );
  });
});

describe("opacity", () => {
  it("uses the default opacity when the overlay is off", () => {
    expect(areaFillOpacity("regina", [REGINA], false)).toBe(
      areaFillOpacity("regina", undefined, false),
    );
  });

  it("falls back to the default opacity for an unknown area", () => {
    // The no-data colour carries the meaning; the opacity should not also
    // shout, or an unmapped airport zone draws the eye more than a real crisis.
    expect(areaFillOpacity("unknown", [REGINA], true)).toBe(
      areaFillOpacity("unknown", undefined, true),
    );
  });

  it("returns a renderable number for every state", () => {
    for (const [id, data, show] of [
      ["regina", [REGINA], true],
      ["unknown", [REGINA], true],
      ["regina", undefined, false],
    ] as const) {
      const o = areaFillOpacity(id, data as AreaDemandSupply[] | undefined, show);
      expect(Number.isFinite(o)).toBe(true);
      expect(o).toBeGreaterThanOrEqual(0);
      expect(o).toBeLessThanOrEqual(1);
    }
  });
});
