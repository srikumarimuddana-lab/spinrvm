/**
 * ACTION_ITEMS.md C115 — the Follow toggle's entire purpose is a
 * map-visual effect (panning to the followed driver), so it used to
 * silently no-op forever whenever MonitoringMap's WebGL guard bailed out
 * (page.tsx's mapHandlesRef.current stayed null, with nothing telling
 * this toolbar to disable/explain the control). This asserts the new
 * `mapCanRender` prop's disabled+explained behavior, and that omitting it
 * (or passing true) leaves the button working exactly as before.
 *
 * See monitoring-jump-buttons-availability.render.test.tsx for the other
 * affected control (the service-area jump buttons in page.tsx), and
 * monitoring-map.render.test.tsx for coverage of the signal's own source
 * (MonitoringMap.onCanRenderChange firing true/false).
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { MonitoringToolbar } from "./toolbar";
import type { MonitoringCounts, MonitoringFilters } from "./types";

const COUNTS: MonitoringCounts = { online: 0, onRide: 0, offline: 0, activeRides: 0 };
const FILTERS: MonitoringFilters = {
    showOnline: true,
    showOffline: false,
    showRides: true,
    showDemand: false,
    serviceAreaId: null,
    vehicleTypeId: null,
};

function renderToolbar(mapCanRender?: boolean) {
    const onFollowToggle = vi.fn();
    render(
        <MonitoringToolbar
            counts={COUNTS}
            filters={FILTERS}
            onFilterChange={vi.fn()}
            searchQuery=""
            onSearchChange={vi.fn()}
            followMode={false}
            onFollowToggle={onFollowToggle}
            serviceAreas={[]}
            vehicleTypes={[]}
            wsStatus="connected"
            {...(mapCanRender === undefined ? {} : { mapCanRender })}
        />,
    );
    return { onFollowToggle };
}

describe("MonitoringToolbar Follow toggle — map availability", () => {
    it("stays enabled and unexplained when mapCanRender is omitted (regression: behaves as before)", () => {
        const { onFollowToggle } = renderToolbar();
        const button = screen.getByRole("button", { name: /Follow/i });
        expect(button).not.toBeDisabled();
        expect(screen.queryByText(/map unavailable/i)).not.toBeInTheDocument();

        fireEvent.click(button);
        expect(onFollowToggle).toHaveBeenCalledTimes(1);
    });

    it("stays enabled when mapCanRender is explicitly true", () => {
        renderToolbar(true);
        expect(screen.getByRole("button", { name: /Follow/i })).not.toBeDisabled();
    });

    it("disables Follow and explains why when the map can't render", () => {
        const { onFollowToggle } = renderToolbar(false);
        const button = screen.getByRole("button", { name: /Follow/i });

        expect(button).toBeDisabled();
        expect(button.getAttribute("title")).toMatch(/map can.t render/i);
        expect(screen.getByText(/map unavailable/i)).toBeInTheDocument();

        fireEvent.click(button);
        expect(onFollowToggle).not.toHaveBeenCalled();
    });
});
