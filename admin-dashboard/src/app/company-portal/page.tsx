"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Building2, ChevronRight, LogOut } from "lucide-react";
import { getMyWorkProfiles, portalHome } from "@/lib/companyApi";
import { useCompanyAuthStore, CompanyMembershipProfile } from "@/store/companyAuthStore";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * Company selector — the caller's OWN memberships (GET /rider/work-profile),
 * never a staff-wide company list. Auto-enters when there's exactly one.
 */
export default function CompanyPortalLandingPage() {
    const router = useRouter();
    const setMemberships = useCompanyAuthStore((s) => s.setMemberships);
    const logout = useCompanyAuthStore((s) => s.logout);
    const [profiles, setProfiles] = useState<CompanyMembershipProfile[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        getMyWorkProfiles()
            .then((rows) => {
                setProfiles(rows);
                setMemberships(rows);
                if (rows.length === 1) {
                    router.replace(portalHome(rows[0].company.id, rows[0].membership.role));
                }
            })
            .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
            .finally(() => setLoading(false));
    }, [router, setMemberships]);

    return (
        <div className="mx-auto max-w-3xl p-6 md:p-10">
            <header className="mb-8 flex items-start justify-between">
                <div>
                    <h1 className="text-2xl font-semibold">Company Portal</h1>
                    <p className="text-muted-foreground">
                        Select a company to book rides and manage your team.
                    </p>
                </div>
                <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => logout().then(() => router.replace("/company-login"))}
                >
                    <LogOut className="mr-1 h-4 w-4" /> Sign out
                </Button>
            </header>

            {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
            {error && (
                <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
            )}

            <div className="space-y-3">
                {profiles.map((p) => (
                    <Link
                        key={p.company.id}
                        href={portalHome(p.company.id, p.membership.role)}
                        className="block"
                    >
                        <Card className="transition-colors hover:bg-muted/40">
                            <CardContent className="flex items-center justify-between gap-4 p-4">
                                <div className="flex items-center gap-3">
                                    <div className="rounded-md bg-emerald-50 p-2">
                                        <Building2 className="h-5 w-5 text-emerald-600" />
                                    </div>
                                    <div>
                                        <div className="font-medium">{p.company.name}</div>
                                        <div className="text-xs text-muted-foreground">
                                            Signed in as {p.membership.role}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-3">
                                    <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
                                        {p.membership.role}
                                    </Badge>
                                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                                </div>
                            </CardContent>
                        </Card>
                    </Link>
                ))}
                {!loading && profiles.length === 0 && !error && (
                    <Card>
                        <CardContent className="p-6 text-center text-sm text-muted-foreground">
                            No company memberships on this account. Ask your company admin
                            for an invite link, then sign in here again.
                        </CardContent>
                    </Card>
                )}
            </div>
        </div>
    );
}
