"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Building2, ChevronRight } from "lucide-react";
import { getCorporateAccounts, CorporateAccount } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function CompanyPortalLandingPage() {
    const [companies, setCompanies] = useState<CorporateAccount[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        getCorporateAccounts()
            .then((rows) => setCompanies(rows.filter((c) => c.status === "active")))
            .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
            .finally(() => setLoading(false));
    }, []);

    return (
        <div className="mx-auto max-w-3xl p-6 md:p-10">
            <header className="mb-8">
                <h1 className="text-2xl font-semibold">Company Portal</h1>
                <p className="text-muted-foreground">
                    Select a company to manage members, policies, and billing.
                </p>
            </header>

            {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
            {error && (
                <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
            )}

            <div className="space-y-3">
                {companies.map((c) => (
                    <Link
                        key={c.id}
                        href={`/company-portal/${c.id}/overview`}
                        className="block"
                    >
                        <Card className="transition-colors hover:bg-muted/40">
                            <CardContent className="flex items-center justify-between gap-4 p-4">
                                <div className="flex items-center gap-3">
                                    <div className="rounded-md bg-emerald-50 p-2">
                                        <Building2 className="h-5 w-5 text-emerald-600" />
                                    </div>
                                    <div>
                                        <div className="font-medium">{c.name}</div>
                                        <div className="text-xs text-muted-foreground">
                                            {c.legal_name ?? c.tax_region ?? c.size_tier}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-3">
                                    <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
                                        {c.status}
                                    </Badge>
                                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                                </div>
                            </CardContent>
                        </Card>
                    </Link>
                ))}
                {!loading && companies.length === 0 && !error && (
                    <Card>
                        <CardContent className="p-6 text-center text-sm text-muted-foreground">
                            No active companies found.
                        </CardContent>
                    </Card>
                )}
            </div>
        </div>
    );
}
