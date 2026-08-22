"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import type { AllowedDomainRow } from "@/lib/api";
import { addAllowedDomain, listAllowedDomains, removeAllowedDomain } from "@/lib/companyApi";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Globe, Trash2 } from "lucide-react";

// Company-session settings: allowed email domains only. Company KYB/profile
// details (legal name, business number, tax region…) are staff-managed and
// are not exposed via the /company/{id} rider-token endpoints, so they live
// in the staff dashboard, not this portal.
export default function SettingsPage() {
    const { id } = useParams<{ id: string }>();
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
            setDomains(await listAllowedDomains(id));
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
                <p className="text-muted-foreground">Approved email domains for your team.</p>
            </header>

            <Card>
                <CardContent className="space-y-4 p-4">
                    <div>
                        <h2 className="font-medium">Allowed email domains</h2>
                        <p className="text-xs text-muted-foreground">
                            Members whose verified email matches an allowed domain can join
                            without an explicit invite.
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
                        <Button onClick={onAdd} disabled={busy || !newDomain.trim()}>
                            Add
                        </Button>
                    </div>

                    {feedback && (
                        <p className="rounded bg-success/15 p-2 text-xs text-success">{feedback}</p>
                    )}
                    {error && <p className="rounded bg-destructive/10 p-2 text-xs text-destructive">{error}</p>}

                    <ul className="divide-y divide-border">
                        {domains.map((d) => (
                            <li key={d.domain} className="flex items-center justify-between py-2">
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
