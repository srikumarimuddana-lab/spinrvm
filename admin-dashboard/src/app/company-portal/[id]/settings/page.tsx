"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
    AllowedDomainRow,
    CorporateAccount,
    addAllowedDomain,
    getCorporateAccount,
    listAllowedDomains,
    removeAllowedDomain,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Globe, Trash2 } from "lucide-react";

export default function SettingsPage() {
    const { id } = useParams<{ id: string }>();
    const [company, setCompany] = useState<CorporateAccount | null>(null);
    const [domains, setDomains] = useState<AllowedDomainRow[]>([]);
    const [newDomain, setNewDomain] = useState("");
    const [busy, setBusy] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [feedback, setFeedback] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        try {
            const [c, d] = await Promise.all([
                getCorporateAccount(id).catch(() => null),
                listAllowedDomains(id).catch(() => []),
            ]);
            setCompany(c);
            setDomains(d);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        load();
    }, [load]);

    const onAdd = async () => {
        if (!id || !newDomain.trim()) return;
        setBusy(true);
        setError(null);
        setFeedback(null);
        try {
            await addAllowedDomain(id, newDomain.trim().toLowerCase());
            setNewDomain("");
            setFeedback("Domain added.");
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed");
        } finally {
            setBusy(false);
        }
    };

    const onRemove = async (domain: string) => {
        if (!id) return;
        setBusy(true);
        setError(null);
        try {
            await removeAllowedDomain(id, domain);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-semibold">Settings</h1>
                <p className="text-muted-foreground">
                    Company details and approved email domains.
                </p>
            </header>

            <Card>
                <CardContent className="space-y-2 p-4 text-sm">
                    {loading ? (
                        <p className="text-muted-foreground">Loading…</p>
                    ) : (
                        <>
                            <Row label="Name" value={company?.name} />
                            <Row label="Legal name" value={company?.legal_name} />
                            <Row label="Business number" value={company?.business_number} />
                            <Row label="Tax region" value={company?.tax_region} />
                            <Row
                                label="Billing email"
                                value={company?.billing_email ?? "—"}
                            />
                            <Row label="Contact name" value={company?.contact_name} />
                            <Row label="Contact email" value={company?.contact_email} />
                            <Row label="Contact phone" value={company?.contact_phone} />
                            <Row label="Size tier" value={company?.size_tier} />
                            <Row
                                label="Status"
                                value={
                                    <Badge className="bg-emerald-100 text-emerald-800">
                                        {company?.status}
                                    </Badge>
                                }
                            />
                        </>
                    )}
                </CardContent>
            </Card>

            <Card>
                <CardContent className="space-y-4 p-4">
                    <div>
                        <h2 className="font-medium">Allowed email domains</h2>
                        <p className="text-xs text-muted-foreground">
                            Members whose verified email matches an allowed domain can
                            join without an explicit invite.
                        </p>
                    </div>

                    <div className="flex gap-2">
                        <div className="grow">
                            <Label htmlFor="domain" className="sr-only">
                                Domain
                            </Label>
                            <Input
                                id="domain"
                                placeholder="example.com"
                                value={newDomain}
                                onChange={(e) => setNewDomain(e.target.value)}
                            />
                        </div>
                        <Button
                            onClick={onAdd}
                            disabled={busy || !newDomain.trim()}
                        >
                            Add
                        </Button>
                    </div>

                    {feedback && (
                        <p className="rounded bg-emerald-50 p-2 text-xs text-emerald-800">
                            {feedback}
                        </p>
                    )}
                    {error && (
                        <p className="rounded bg-red-50 p-2 text-xs text-red-700">{error}</p>
                    )}

                    <ul className="divide-y divide-border">
                        {domains.map((d) => (
                            <li
                                key={d.domain}
                                className="flex items-center justify-between py-2"
                            >
                                <span className="flex items-center gap-2 text-sm">
                                    <Globe className="h-3.5 w-3.5 text-muted-foreground" />
                                    {d.domain}
                                </span>
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    onClick={() => onRemove(d.domain)}
                                    disabled={busy}
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            </li>
                        ))}
                        {domains.length === 0 && !loading && (
                            <li className="py-2 text-sm text-muted-foreground">
                                No allowed domains yet.
                            </li>
                        )}
                    </ul>
                </CardContent>
            </Card>
        </div>
    );
}

function Row({
    label,
    value,
}: {
    label: string;
    value?: React.ReactNode;
}) {
    return (
        <div className="flex items-center justify-between border-b border-border py-1.5 last:border-b-0">
            <span className="text-muted-foreground">{label}</span>
            <span className="font-medium">{value ?? "—"}</span>
        </div>
    );
}
