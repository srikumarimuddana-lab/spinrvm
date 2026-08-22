"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import {
    ArrowLeft,
    Building2,
    CalendarPlus,
    LayoutDashboard,
    ListChecks,
    Receipt,
    ScrollText,
    Settings,
    ShieldCheck,
    Users,
    Wallet,
} from "lucide-react";
import { getMyWorkProfiles } from "@/lib/companyApi";
import { useCompanyAuthStore, CompanyMembershipProfile } from "@/store/companyAuthStore";
import { Badge } from "@/components/ui/badge";

interface NavItem {
    href: string;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    adminOnly?: boolean;
}

// Booking is every member's job (that's the product); org management stays
// owner/admin. The backend guards enforce this regardless — hiding the nav
// items is UX, not security.
function buildNav(id: string): NavItem[] {
    return [
        { href: `/company-portal/${id}/book`, label: "Book a ride", icon: CalendarPlus },
        { href: `/company-portal/${id}/bookings`, label: "Bookings", icon: ListChecks },
        { href: `/company-portal/${id}/overview`, label: "Overview", icon: LayoutDashboard, adminOnly: true },
        { href: `/company-portal/${id}/members`, label: "Members", icon: Users, adminOnly: true },
        { href: `/company-portal/${id}/sections`, label: "Sections", icon: Users, adminOnly: true },
        { href: `/company-portal/${id}/allowances`, label: "Allowances", icon: Wallet, adminOnly: true },
        { href: `/company-portal/${id}/allowance-requests`, label: "Requests", icon: ScrollText, adminOnly: true },
        { href: `/company-portal/${id}/policy`, label: "Policy", icon: ShieldCheck, adminOnly: true },
        { href: `/company-portal/${id}/billing`, label: "Billing", icon: Receipt, adminOnly: true },
        { href: `/company-portal/${id}/activity`, label: "Activity", icon: LayoutDashboard, adminOnly: true },
        { href: `/company-portal/${id}/settings`, label: "Settings", icon: Settings, adminOnly: true },
    ];
}

export default function CompanyPortalLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    const params = useParams();
    const pathname = usePathname();
    const router = useRouter();
    const id = typeof params?.id === "string" ? params.id : "";
    const memberships = useCompanyAuthStore((s) => s.memberships);
    const setMemberships = useCompanyAuthStore((s) => s.setMemberships);
    const [error, setError] = useState<string | null>(null);

    // Company identity comes from the caller's OWN membership (work-profile),
    // not the staff corporate-accounts endpoint (403 for rider tokens).
    const profile: CompanyMembershipProfile | undefined = useMemo(
        () => memberships.find((m) => m.company.id === id),
        [memberships, id]
    );

    useEffect(() => {
        if (!id || profile) return;
        getMyWorkProfiles()
            .then((rows) => {
                setMemberships(rows);
                if (!rows.some((m) => m.company.id === id)) {
                    setError("You are not a member of this company.");
                    router.push("/company-portal");
                }
            })
            .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
    }, [id, profile, router, setMemberships]);

    const isCompanyAdmin = profile?.membership.role === "owner" || profile?.membership.role === "admin";

    // M2.6 verification gate: a non-active company (pending_verification /
    // suspended / closed) only gets the verification page — every other
    // portal path redirects there. Cached memberships from before the status
    // field existed are refetched by the effect above on next login; treat
    // missing status as active to avoid false lockouts on stale caches.
    const companyStatus = profile?.company.status ?? "active";
    const verificationPath = `/company-portal/${id}/verification`;
    const gated = Boolean(profile) && companyStatus !== "active";

    useEffect(() => {
        if (gated && pathname !== verificationPath) {
            router.replace(verificationPath);
        }
    }, [gated, pathname, verificationPath, router]);

    const nav = gated
        ? [{ href: verificationPath, label: "Verification", icon: ShieldCheck } as NavItem]
        : buildNav(id).filter((item) => isCompanyAdmin || !item.adminOnly);

    return (
        <div className="flex min-h-screen flex-col md:flex-row">
            <aside className="md:w-64 md:shrink-0 md:border-r border-border bg-muted/20">
                <div className="p-4 md:p-6">
                    <Link
                        href="/company-portal"
                        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    >
                        <ArrowLeft className="h-3.5 w-3.5" /> Switch company
                    </Link>
                    <div className="flex items-center gap-2">
                        <div className="rounded-md bg-emerald-50 dark:bg-emerald-900/20 p-2">
                            <Building2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                        </div>
                        <div>
                            <div className="font-semibold leading-tight">
                                {profile?.company.name ?? "Loading…"}
                            </div>
                            {profile?.membership.role && (
                                <Badge className="mt-1 text-[10px] bg-success/15 text-success hover:bg-success/15">
                                    {profile.membership.role}
                                </Badge>
                            )}
                        </div>
                    </div>
                </div>
                <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:gap-0.5 md:overflow-visible md:px-3">
                    {nav.map((item) => {
                        const Icon = item.icon;
                        const active = pathname === item.href;
                        return (
                            <Link
                                key={item.href}
                                href={item.href}
                                className={
                                    "flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors " +
                                    (active
                                        ? "bg-primary/10 font-medium text-primary"
                                        : "text-muted-foreground hover:bg-muted hover:text-foreground")
                                }
                            >
                                <Icon className="h-4 w-4" />
                                {item.label}
                            </Link>
                        );
                    })}
                </nav>
            </aside>

            <main className="flex-1">
                {error && (
                    <div className="m-4 rounded border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                        {error}
                    </div>
                )}
                <div className="p-4 md:p-8">{children}</div>
            </main>
        </div>
    );
}
