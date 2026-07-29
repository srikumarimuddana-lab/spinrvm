"use client";

/**
 * Records & Compliance — consolidates the four admin surfaces that all do
 * the same underlying job (move or report on regulated driver/rider data)
 * but previously lived as four unrelated-looking entries buried in the
 * sidebar's System group: Data Transfer, Compliance, Bulk Operations, and
 * Export Approvals.
 *
 * Per-tab permission model (this is the part worth reading before editing):
 * each of the four tab bodies below is the SAME component the old
 * standalone page rendered, imported and reused as-is — not rewritten, not
 * duplicated. Each of those components already does its own internal
 * permission check:
 *   - DataTransferPage:    useRequireSuperAdmin() — redirects to /403
 *   - CompliancePage:      useRequireModule("compliance") — redirects to /403
 *   - BulkOperationsPage:  inline role === "super_admin" check — renders a
 *                          denial card, no redirect
 *   - ExportApprovalsPage: inline role === "super_admin" check — renders a
 *                          denial card, no redirect
 *
 * Two of those four REDIRECT the whole page away on failure. That's fine
 * for a page occupying its own route, but wrong here — a compliance-role
 * staffer who lacks Data Transfer's super_admin requirement must not be
 * bounced to /403 just because that tab's content exists somewhere on the
 * page. The fix is that Radix Tabs only mounts the ACTIVE TabsContent by
 * default (no forceMount below) — so a tab's inner permission hook only
 * ever runs if that tab is actually selected. This page additionally hides
 * the TabsTrigger for any tab the user can't access at all, so they can
 * never select into (and therefore never mount, therefore never get
 * redirected by) a tab they don't have permission for. The embedded
 * pages' own checks remain as a defense-in-depth backstop — if this page's
 * visibility logic ever drifts from theirs, the worst case is a hidden tab
 * becoming reachable via direct tab-param URL, and the embedded page's own
 * check still catches it.
 */

import { Suspense, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuthStore } from "@/store/authStore";

import DataTransferPage from "../data-transfer/page";
import CompliancePage from "../compliance/page";
import BulkOperationsPage from "../bulk-operations/page";
import ExportApprovalsPage from "../export-approvals/page";

type TabSlug = "data-transfer" | "compliance" | "bulk-operations" | "export-approvals";

const TAB_META: Record<TabSlug, { label: string; Component: React.ComponentType }> = {
    "data-transfer": { label: "Search & Export", Component: DataTransferPage },
    compliance: { label: "Regulatory Reports", Component: CompliancePage },
    "bulk-operations": { label: "Bulk Import", Component: BulkOperationsPage },
    "export-approvals": { label: "Approval Queue", Component: ExportApprovalsPage },
};

const TAB_ORDER: TabSlug[] = ["data-transfer", "compliance", "bulk-operations", "export-approvals"];

function isValidTab(value: string | null): value is TabSlug {
    return !!value && (TAB_ORDER as string[]).includes(value);
}

function RecordsPageInner() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const user = useAuthStore((s) => s.user);
    const isLoading = useAuthStore((s) => s.isLoading);

    // Mirrors the exact checks each embedded page's own hook/inline gate
    // uses — kept as plain booleans here (no redirect side effect) since
    // multiple tabs with different requirements share this one page.
    const isSuperAdmin = user?.role === "super_admin";
    const hasComplianceModule = isSuperAdmin || user?.role === "admin" || (user?.modules ?? []).includes("compliance");

    const canView: Record<TabSlug, boolean> = {
        "data-transfer": isSuperAdmin,
        compliance: hasComplianceModule,
        "bulk-operations": isSuperAdmin,
        "export-approvals": isSuperAdmin,
    };

    const visibleTabs = useMemo(() => TAB_ORDER.filter((slug) => canView[slug]), [isSuperAdmin, hasComplianceModule]); // eslint-disable-line react-hooks/exhaustive-deps

    const requestedTab = searchParams.get("tab");
    const initialTab = isValidTab(requestedTab) && canView[requestedTab] ? requestedTab : visibleTabs[0];
    const [activeTab, setActiveTab] = useState<TabSlug | undefined>(initialTab);

    const onTabChange = (value: string) => {
        if (!isValidTab(value)) return;
        setActiveTab(value);
        router.replace(`/dashboard/records?tab=${value}`, { scroll: false });
    };

    if (isLoading) return null;

    if (visibleTabs.length === 0) {
        return (
            <div className="mx-auto flex max-w-md flex-col items-center gap-3 p-12 text-center">
                <ShieldAlert className="h-10 w-10 text-muted-foreground" />
                <h2 className="text-lg font-semibold">No access to Records &amp; Compliance</h2>
                <p className="text-sm text-muted-foreground">
                    None of the tools in this module — Search &amp; Export, Regulatory Reports, Bulk Import, or the
                    Approval Queue — are granted to your account.
                </p>
            </div>
        );
    }

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-semibold">Records &amp; Compliance</h1>
                <p className="text-muted-foreground">
                    Search, export, import, and report on regulated driver/rider data — SGI compliance forms, tax
                    reports, bulk data tools, and the dual-approval queue that gates the largest exports.
                </p>
            </div>

            <Tabs value={activeTab} onValueChange={onTabChange}>
                <TabsList>
                    {visibleTabs.map((slug) => (
                        <TabsTrigger key={slug} value={slug}>
                            {TAB_META[slug].label}
                        </TabsTrigger>
                    ))}
                </TabsList>
                {visibleTabs.map((slug) => {
                    const { Component } = TAB_META[slug];
                    return (
                        <TabsContent key={slug} value={slug} className="-mx-6">
                            {/* Each embedded page renders its own p-6 wrapper, hence the
                                negative margin above to avoid doubling the page padding. */}
                            <Component />
                        </TabsContent>
                    );
                })}
            </Tabs>
        </div>
    );
}

export default function RecordsPage() {
    // useSearchParams requires a Suspense boundary in the App Router.
    return (
        <Suspense fallback={null}>
            <RecordsPageInner />
        </Suspense>
    );
}
