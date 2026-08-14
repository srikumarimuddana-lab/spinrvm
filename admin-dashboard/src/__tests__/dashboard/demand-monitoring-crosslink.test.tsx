/**
 * The demand cards must lead somewhere actionable.
 *
 * "Regina is at 2.1 demand:supply" tells an operator an area is under pressure
 * but not *where in it* or *which drivers are idle nearby* — the two things any
 * response needs. Before this, getting there meant navigating to Live
 * Monitoring, re-finding the area in a dropdown and re-enabling the demand
 * overlay, so in practice the two screens read as unrelated.
 *
 * Two things are worth pinning, because both fail silently:
 *   1. The link carries BOTH the area and the demand flag. Dropping either
 *      lands the operator on an unfiltered map with the overlay off, which
 *      looks like the link simply didn't work.
 *   2. It is hidden from an admin without the `rides` module — the module that
 *      gates Live Monitoring. An unconditional link sends anyone else to /403,
 *      which reads as a broken link rather than a permission boundary.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";

const AREA = {
  area_id: "area-regina",
  name: "Regina",
  demand_count: 12,
  supply_count: 4,
  ratio: 3.0,
  multiplier: 2.0,
  surge_active: true,
  surge_enabled: true,
  source: "auto",
};

// Mutable so each test can pick the viewer's grants before importing the page.
const authState: { user: { role: string; modules: string[] } | null } = {
  user: { role: "operations", modules: ["rides"] },
};

vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector?: (s: typeof authState) => unknown) =>
    typeof selector === "function" ? selector(authState) : authState,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard/heatmap",
}));

vi.mock("@/components/heat-map", () => ({ default: () => <div data-sub="HeatMap" /> }));

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
    getHeatMapSettings: vi.fn().mockResolvedValue({}),
    getHeatMapData: vi.fn().mockResolvedValue({ points: [] }),
    getServiceAreas: vi.fn().mockResolvedValue([]),
    getDemandForecast: vi.fn().mockResolvedValue([]),
    getSurgeStatus: vi.fn().mockResolvedValue([AREA]),
  };
});

/** Mount the page and switch the live-demand section on (it is off by default,
 *  deliberately — the poll hits two expensive admin endpoints). */
async function mountWithDemand() {
  const { default: Page } = await import("@/app/dashboard/heatmap/page");
  await act(async () => { render(<Page />); });
  await act(async () => {
    fireEvent.click(screen.getByLabelText(/Live updates/i));
  });
  await waitFor(() => expect(screen.getByText("Regina")).toBeTruthy());
}

beforeEach(() => {
  vi.clearAllMocks();
  authState.user = { role: "operations", modules: ["rides"] };
});

describe("demand card → live map cross-link", () => {
  it("deep-links with the area preselected and the demand overlay on", async () => {
    await mountWithDemand();

    const link = screen.getByRole("link", { name: /View Regina on the live map/i });
    const href = link.getAttribute("href") ?? "";
    // Both params matter: area alone lands on the map with the overlay off,
    // demand alone lands on an unfiltered map. Either reads as a dead link.
    expect(href).toContain("/dashboard/monitoring");
    expect(href).toContain("area=area-regina");
    expect(href).toContain("demand=1");
  });

  it("hides the link from an admin without the rides module", async () => {
    // Live Monitoring is gated by `rides`. An admin who can reach this page
    // without that module must not be shown a link to /403.
    authState.user = { role: "operations", modules: ["dashboard"] };
    await mountWithDemand();

    expect(screen.queryByRole("link", { name: /on the live map/i })).toBeNull();
    // The card itself is still there — only the link is withheld.
    expect(screen.getByText("Regina")).toBeTruthy();
  });

  it("shows the link to a super admin regardless of the modules array", async () => {
    authState.user = { role: "super_admin", modules: [] };
    await mountWithDemand();

    expect(screen.getByRole("link", { name: /View Regina on the live map/i })).toBeTruthy();
  });
});
