"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import {
    ArrowLeft,
    Building2,
    LayoutDashboard,
    Receipt,
    ScrollText,
    Settings,
    ShieldCheck,
    Users,
    Wallet,
} from "lucide-react";
import {
    CorporateAccount,
    getCorporateAccount,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";

interface NavItem {
    href: string;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
}

function buildNav(id: string): NavItem[] {
    return [
        { href: `/company-portal/${id}/overview`, label: "Overview", icon: LayoutDashboard },
        { href: `/company-portal/${id}/members`, label: "Members", icon: Users },
        { href: `/company-portal/${id}/allowances`, label: "Allowances", icon: Wallet },
        { href: `/company-portal/${id}/allowance-requests`, label: "Requests", icon: ScrollText },
        { href: `/company-portal/${id}/policy`, label: "Policy", icon: ShieldCheck },
        { href: `/company-portal/${id}/billing`, label: "Billing", icon: Receipt },
        { href: `/company-portal/${id}/activity`, label: "Activity", icon: LayoutDashboard },
        { href: `/company-portal/${id}/settings`, label: "Settings", icon: Settings },
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
    const [company, setCompany] = useState<CorporateAccount | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!id) return;
        getCorporateAccount(id)
            .then(setCompany)
            .catch((e) => {
                const msg = e instanceof Error ? e.message : "Failed to load";
                setError(msg);
                if (/forbidden|not a company/i.test(msg)) {
                    router.push("/company-portal");
                }
            });
    }, [id, router]);

    const nav = buildNav(id);

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
                        <div className="rounded-md bg-emerald-50 p-2">
                            <Building2 className="h-5 w-5 text-emerald-600" />
                        </div>
                        <div>
                            <div className="font-semibold leading-tight">
                                {company?.name ?? "Loading…"}
                            </div>
                            {company?.status && (
                                <Badge className="mt-1 text-[10px] bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
                                    {company.status}
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
                {error && !/forbidden|not a company/i.test(error) && (
                    <div className="m-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                        {error}
                    </div>
                )}
                <div className="p-4 md:p-8">{children}</div>
            </main>
        </div>
    );
}
