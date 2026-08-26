"use client";

/**
 * Admin portal IA audit, Findings A/B follow-up: see faqs.tsx's header
 * comment — the same "light-touch, point here, don't merge" product
 * decision covered Disputes too (identical comment previously lived here),
 * and was escalated + approved for the same fix. This tab now renders the
 * exact same component /dashboard/disputes does.
 */

import DisputesPage from "../../disputes/page";

export default function DisputesTab() {
    return <DisputesPage />;
}
