"use client";

/**
 * Support & Issues — 7 sub-views (Support Tickets, Disputes, Complaints,
 * Lost & Found, Flags, FAQs, Legal) behind one "support" module gate.
 *
 * Admin portal IA audit, Finding G: this page's tabs previously had no nav
 * representation at all (compare Help Desk, whose 2 sub-views are real
 * sidebar children) and no URL sync, so a tab couldn't be bookmarked, deep
 * -linked, or highlighted in the sidebar. Switched to the same
 * Tabs + useSearchParams pattern records/page.tsx already uses; sidebar.tsx
 * now gives all 7 sub-views real nav children.
 *
 * Findings A/B follow-up: Disputes and FAQs originally kept their own
 * standalone top-level nav entries here instead of becoming children,
 * because both were covered by a documented "point to the dedicated page,
 * don't merge" product decision. That decision was escalated and approved
 * for a full merge — support/_tabs/disputes.tsx and support/_tabs/faqs.tsx
 * now render the exact same components their old standalone pages did
 * (/dashboard/faqs and /dashboard/disputes redirect here, matching the
 * records/page.tsx precedent), so those entries were removed in favour of
 * the children below like every other sub-view.
 */

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { LifeBuoy, HelpCircle, PackageSearch, Flag, FileWarning, BookOpen, ScrollText } from "lucide-react";
import dynamic from "next/dynamic";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRequireModule } from "@/hooks/useRequireModule";

const TicketsTab = dynamic(() => import("./_tabs/tickets"), { ssr: false, loading: () => <TabLoader /> });
const DisputesTab = dynamic(() => import("./_tabs/disputes"), { ssr: false, loading: () => <TabLoader /> });
const LostAndFoundTab = dynamic(() => import("./_tabs/lost-and-found"), { ssr: false, loading: () => <TabLoader /> });
const FlagsTab = dynamic(() => import("./_tabs/flags"), { ssr: false, loading: () => <TabLoader /> });
const ComplaintsTab = dynamic(() => import("./_tabs/complaints"), { ssr: false, loading: () => <TabLoader /> });
const FaqsTab = dynamic(() => import("./_tabs/faqs"), { ssr: false, loading: () => <TabLoader /> });
const LegalDocumentsTab = dynamic(() => import("./_tabs/legal-documents"), { ssr: false, loading: () => <TabLoader /> });

type TabSlug = "tickets" | "disputes" | "complaints" | "lost-found" | "flags" | "faqs" | "legal";

const TAB_ORDER: TabSlug[] = ["tickets", "disputes", "complaints", "lost-found", "flags", "faqs", "legal"];

const TAB_META: Record<TabSlug, { label: string; icon: typeof LifeBuoy; Component: React.ComponentType }> = {
    tickets: { label: "Support Tickets", icon: LifeBuoy, Component: TicketsTab },
    disputes: { label: "Disputes", icon: HelpCircle, Component: DisputesTab },
    complaints: { label: "Complaints", icon: FileWarning, Component: ComplaintsTab },
    "lost-found": { label: "Lost & Found", icon: PackageSearch, Component: LostAndFoundTab },
    flags: { label: "Flags", icon: Flag, Component: FlagsTab },
    faqs: { label: "FAQs", icon: BookOpen, Component: FaqsTab },
    legal: { label: "Legal", icon: ScrollText, Component: LegalDocumentsTab },
};

function isValidTab(value: string | null): value is TabSlug {
    return !!value && (TAB_ORDER as string[]).includes(value);
}

function TabLoader() {
    return <div className="flex items-center justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>;
}

function SupportPageInner() {
    const { allowed } = useRequireModule("support");
    const router = useRouter();
    const searchParams = useSearchParams();

    const requestedTab = searchParams.get("tab");
    const initialTab = isValidTab(requestedTab) ? requestedTab : "tickets";
    const [activeTab, setActiveTab] = useState<TabSlug>(initialTab);

    const onTabChange = (value: string) => {
        if (!isValidTab(value)) return;
        setActiveTab(value);
        router.replace(`/dashboard/support?tab=${value}`, { scroll: false });
    };

    if (!allowed) return null;

    return (
        <div className="px-1 sm:px-0">
            <div className="mb-4">
                <h1 className="text-xl sm:text-2xl font-bold flex items-center gap-2">
                    <LifeBuoy className="h-5 w-5 sm:h-6 sm:w-6" /> Support & Issues
                </h1>
                <p className="text-xs sm:text-sm text-muted-foreground mt-1">Manage tickets, disputes, lost items, and user flags</p>
            </div>

            <Tabs value={activeTab} onValueChange={onTabChange}>
                <TabsList className="max-w-full justify-start overflow-x-auto scrollbar-none">
                    {TAB_ORDER.map((slug) => {
                        const { icon: Icon, label } = TAB_META[slug];
                        return (
                            <TabsTrigger key={slug} value={slug} className="gap-1.5">
                                <Icon className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                                {label}
                            </TabsTrigger>
                        );
                    })}
                </TabsList>
                {TAB_ORDER.map((slug) => {
                    const { Component } = TAB_META[slug];
                    return (
                        <TabsContent key={slug} value={slug} className="mt-4">
                            <Component />
                        </TabsContent>
                    );
                })}
            </Tabs>
        </div>
    );
}

export default function SupportPage() {
    // useSearchParams requires a Suspense boundary in the App Router.
    return (
        <Suspense fallback={null}>
            <SupportPageInner />
        </Suspense>
    );
}
