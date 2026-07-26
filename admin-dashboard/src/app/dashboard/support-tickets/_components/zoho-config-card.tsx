"use client";

import { useEffect, useState } from "react";
import {
    getZohoConfig,
    updateZohoConfig,
    testZohoConnection,
    ZohoConfigStatus,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { CheckCircle2, XCircle, Plug } from "lucide-react";

const DATA_CENTERS = [
    { value: "ca", label: "Canada (zohocloud.ca)" },
    { value: "com", label: "United States (zoho.com)" },
    { value: "eu", label: "Europe (zoho.eu)" },
    { value: "in", label: "India (zoho.in)" },
    { value: "com.au", label: "Australia (zoho.com.au)" },
    { value: "jp", label: "Japan (zoho.jp)" },
];

/** Connection + OAuth config for Zoho Desk. Secrets are write-only — the
 *  backend returns presence flags only, so empty secret inputs mean
 *  "leave unchanged". */
export function ZohoConfigCard({ onSaved }: { onSaved?: (s: ZohoConfigStatus) => void }) {
    const { toast } = useToast();
    const [status, setStatus] = useState<ZohoConfigStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);

    const [enabled, setEnabled] = useState(false);
    const [autoSync, setAutoSync] = useState(false);
    const [dataCenter, setDataCenter] = useState("ca");
    const [orgId, setOrgId] = useState("");
    const [departmentId, setDepartmentId] = useState("");
    const [fromEmail, setFromEmail] = useState("");
    const [clientId, setClientId] = useState("");
    const [clientSecret, setClientSecret] = useState("");
    const [refreshToken, setRefreshToken] = useState("");

    const load = async () => {
        try {
            const s = await getZohoConfig();
            setStatus(s);
            setEnabled(s.enabled);
            setAutoSync(!!s.auto_sync_enabled);
            setDataCenter(s.data_center || "ca");
            setOrgId(s.org_id || "");
            setDepartmentId(s.default_department_id || "");
            setFromEmail(s.default_from_email || "");
        } catch {
            toast({ title: "Failed to load Zoho config", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const save = async () => {
        setSaving(true);
        try {
            const body: Record<string, unknown> = {
                enabled,
                auto_sync_enabled: autoSync,
                data_center: dataCenter,
                org_id: orgId,
                default_department_id: departmentId,
                default_from_email: fromEmail,
            };
            // Only send secrets the admin actually typed.
            if (clientId.trim()) body.client_id = clientId.trim();
            if (clientSecret.trim()) body.client_secret = clientSecret.trim();
            if (refreshToken.trim()) body.refresh_token = refreshToken.trim();
            const s = await updateZohoConfig(body);
            setStatus(s);
            setClientId("");
            setClientSecret("");
            setRefreshToken("");
            toast({ title: "Zoho Desk configuration saved" });
            onSaved?.(s);
        } catch (e: any) {
            toast({ title: "Save failed", description: e?.message, variant: "destructive" });
        } finally {
            setSaving(false);
        }
    };

    const test = async () => {
        setTesting(true);
        try {
            const r = await testZohoConnection();
            toast({
                title: "Connection OK",
                description: `${r.departments?.length ?? 0} department(s) reachable`,
            });
        } catch (e: any) {
            toast({ title: "Connection failed", description: e?.message, variant: "destructive" });
        } finally {
            setTesting(false);
        }
    };

    if (loading) return <Card><CardContent className="p-6 text-muted-foreground">Loading…</CardContent></Card>;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Plug className="h-5 w-5" /> Zoho Desk Connection
                    {status?.connected ? (
                        <Badge className="ml-2 bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
                            <CheckCircle2 className="mr-1 h-3 w-3" /> Connected
                        </Badge>
                    ) : (
                        <Badge variant="secondary" className="ml-2 bg-amber-100 text-amber-800 hover:bg-amber-100">
                            <XCircle className="mr-1 h-3 w-3" /> Not connected
                        </Badge>
                    )}
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex items-center justify-between rounded-lg border p-3">
                    <div>
                        <p className="font-medium">Enable integration</p>
                        <p className="text-sm text-muted-foreground">Turn the Help Desk on for staff.</p>
                    </div>
                    <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="Enable integration" />
                </div>

                <div className="flex items-center justify-between rounded-lg border p-3">
                    <div>
                        <p className="font-medium">Auto-sync from Zoho</p>
                        <p className="text-sm text-muted-foreground">
                            When off, tickets sync only when you press “Sync now”. Leave off to avoid frequent background pulls.
                        </p>
                    </div>
                    <Switch checked={autoSync} onCheckedChange={setAutoSync} disabled={!enabled} aria-label="Auto-sync from Zoho" />
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1">
                        <Label htmlFor="zoho-data-center">Data center</Label>
                        <Select value={dataCenter} onValueChange={setDataCenter}>
                            <SelectTrigger id="zoho-data-center" aria-label="Data center"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                {DATA_CENTERS.map((d) => (
                                    <SelectItem key={d.value} value={d.value}>{d.label}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="zoho-org-id">Org ID</Label>
                        <Input id="zoho-org-id" value={orgId} onChange={(e) => setOrgId(e.target.value)} placeholder="e.g. 700123456" />
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="zoho-department-id">Default Department ID (optional)</Label>
                        <Input id="zoho-department-id" value={departmentId} onChange={(e) => setDepartmentId(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="zoho-from-email">Reply-from email</Label>
                        <Input
                            id="zoho-from-email"
                            type="email"
                            value={fromEmail}
                            onChange={(e) => setFromEmail(e.target.value)}
                            placeholder="support@spinr.ca"
                        />
                        <p className="text-xs text-muted-foreground">
                            Must be a verified support address in your Zoho Desk portal. Required to send email replies.
                        </p>
                    </div>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1">
                        <Label htmlFor="zoho-client-id">Client ID {status?.has_client_id && <span className="text-xs text-emerald-600">(saved)</span>}</Label>
                        <Input id="zoho-client-id" value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder={status?.has_client_id ? "•••••• (unchanged)" : ""} />
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="zoho-client-secret">Client Secret {status?.has_client_secret && <span className="text-xs text-emerald-600">(saved)</span>}</Label>
                        <Input id="zoho-client-secret" type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} placeholder={status?.has_client_secret ? "•••••• (unchanged)" : ""} />
                    </div>
                    <div className="space-y-1 sm:col-span-2">
                        <Label htmlFor="zoho-refresh-token">Refresh Token {status?.has_refresh_token && <span className="text-xs text-emerald-600">(saved)</span>}</Label>
                        <Input id="zoho-refresh-token" type="password" value={refreshToken} onChange={(e) => setRefreshToken(e.target.value)} placeholder={status?.has_refresh_token ? "•••••• (unchanged)" : ""} />
                        <p className="text-xs text-muted-foreground">
                            Generate a self-client refresh token in the Zoho API console with the
                            <code className="mx-1">Desk.tickets.ALL</code>,
                            <code className="mx-1">Desk.search.READ</code>,
                            <code className="mx-1">Desk.agents.READ</code>,
                            <code className="mx-1">Desk.settings.READ</code> and
                            <code className="mx-1">Desk.basic.READ</code> scopes.
                            <code className="ml-1">Desk.search.READ</code> powers the dashboard counts and
                            <code className="mx-1">Desk.agents.READ</code> the assignee filters/assignment controls.
                        </p>
                    </div>
                </div>

                <div className="flex gap-2">
                    <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
                    <Button variant="outline" onClick={test} disabled={testing || !status?.connected}>
                        {testing ? "Testing…" : "Test connection"}
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}
