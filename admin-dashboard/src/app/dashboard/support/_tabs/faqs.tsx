"use client";

/**
 * Admin portal IA audit, Findings A/B follow-up: this used to be a second,
 * independently hand-rolled FAQ CRUD implementation living alongside the
 * dedicated /dashboard/faqs page — both hit the same getFaqs/createFaq/
 * updateFaq/deleteFaq API with no data-model split (see the removed
 * "light-touch fix per product decision: point here, don't merge/remove"
 * comment this file used to carry). That decision was escalated back to
 * the product owner and approved for a full merge: this tab now renders
 * the exact same component /dashboard/faqs does — one implementation, two
 * entry points — matching the pattern records/page.tsx already established
 * for Data Transfer/Compliance/Bulk Operations/Export Approvals.
 */

import FaqsPage from "../../faqs/page";

export default function FaqsTab() {
    return <FaqsPage />;
}
