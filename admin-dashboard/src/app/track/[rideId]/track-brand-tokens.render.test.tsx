/**
 * /track/[rideId] — styled with the admin design system's semantic tokens
 * (UX program W4.2) while staying fixed-light for every visitor.
 *
 * The page pins the light value of each token it uses on its own root
 * (light-tokens.ts), because <html> carries `.dark` by default. This checks
 * that the pinned values still equal globals.css `:root`, that every state
 * the page can show (loading, live ride, invalid link) sits inside that
 * scope, and that no raw Tailwind palette class (the #2816 lint pattern) is
 * left in the rendered markup.
 */
import React from "react";
import { readFileSync } from "fs";
import { join } from "path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { activeRide, flush, harness, setupTrackHarness, teardownTrackHarness } from "./track-test-harness";
import { TRACK_LIGHT_TOKENS } from "./light-tokens";

vi.hoisted(() => {
    process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY = "test-key";
    process.env.NEXT_PUBLIC_API_URL = "https://api.test";
});

vi.mock("next/navigation", () => ({ useParams: () => ({ rideId: "share-token" }) }));

vi.mock("next/script", async () => {
    const R = await import("react");
    return {
        default: function FakeScript({ onLoad }: { onLoad?: () => void }) {
            R.useEffect(() => onLoad?.(), []);
            return null;
        },
    };
});

import TrackRide from "./page";

// Same pattern as eslint.config.mjs's #2816 no-restricted-syntax rule.
const RAW_PALETTE =
    /\b(bg|text|border|ring|fill|stroke|from|to|via)-(red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|gray|grey|slate|zinc|neutral|stone)-[0-9]{2,3}\b/;

function globalsRootTokens(): Record<string, string> {
    const css = readFileSync(join(__dirname, "..", "..", "globals.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const start = css.indexOf(":root {");
    const body = css.slice(start, css.indexOf("}", start));
    const out: Record<string, string> = {};
    for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[name] = value.trim();
    return out;
}

function rawPaletteClasses(root: HTMLElement): string[] {
    return [root, ...Array.from(root.querySelectorAll("*"))]
        .map((el) => el.getAttribute("class") ?? "")
        .filter((cls) => RAW_PALETTE.test(cls));
}

function expectPinnedLight(el: Element | null) {
    expect(el).toBeInstanceOf(HTMLElement);
    const style = (el as HTMLElement).style;
    for (const [name, value] of Object.entries(TRACK_LIGHT_TOKENS)) {
        expect(style.getPropertyValue(name)).toBe(value);
    }
}

beforeEach(() => {
    setupTrackHarness();
});

afterEach(() => {
    teardownTrackHarness();
});

describe("/track brand tokens", () => {
    it("pins exactly the light values globals.css :root defines", () => {
        const root = globalsRootTokens();
        expect(Object.keys(TRACK_LIGHT_TOKENS).length).toBeGreaterThan(0);
        for (const [name, value] of Object.entries(TRACK_LIGHT_TOKENS)) {
            expect(root[name]?.toLowerCase(), name).toBe(String(value).toLowerCase());
        }
    });

    it("renders a live ride inside the light token scope with no raw palette classes", async () => {
        harness.ride = activeRide("driver_accepted", { lat: 52.13, lng: -106.67 });
        const { container, unmount } = render(<TrackRide />);
        await flush();
        expect(screen.getByText("TEST 123")).toBeInTheDocument();
        expectPinnedLight(container.firstElementChild);
        expect(rawPaletteClasses(container)).toEqual([]);
        unmount();
    });

    it("keeps the loading and invalid-link states light too", async () => {
        harness.status = 404;
        const { container, unmount } = render(<TrackRide />);
        expect(screen.getByText("Loading trip…")).toBeInTheDocument();
        expectPinnedLight(container.firstElementChild);

        await flush();
        expect(screen.getByText("Tracking unavailable")).toBeInTheDocument();
        expect(screen.getByText("Tracking link is invalid or has expired.")).toBeInTheDocument();
        expectPinnedLight(container.firstElementChild);
        expect(rawPaletteClasses(container)).toEqual([]);
        unmount();
    });
});
