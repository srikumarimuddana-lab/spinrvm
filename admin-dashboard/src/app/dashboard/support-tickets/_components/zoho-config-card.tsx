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
 *  "leave unchanged".
 *
 *  The credential inputs stay unmounted behind an explicit "Replace
 *  credentials" toggle. Rendering a text field labelled like an identifier
 *  next to a password field makes browsers and password managers treat the
 *  card as a login form: on 2026-08-13 an autofill dropped the admin's own
 *  email + password into Client ID / Client Secret, a routine save wrote them
 *  over the working Zoho OAuth credentials, and every subsequent token
 *  refresh failed with Zoho's opaque `general_error`. Not rendering the
 *  inputs unless the admin is deliberately changing credentials removes the
 *  autofill target entirely; the autoComplete/ignore attributes below are the
 *  second layer for when they are on screen. */
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
    const [signatureEnabled, setSignatureEnabled] = useState(false);
    const [signature, setSignature] = useState("");
    const [signaturePreview, setSignaturePreview] = useState("");
    const [clientId, setClientId] = useState("");
    const [clientSecret, setClientSecret] = useState("");
    const [refreshToken, setRefreshToken] = useState("");
    const [editingCredentials, setEditingCredentials] = useState(false);

    const clearCredentialInputs = () => {
        setClientId("");
        setClientSecret("");
        setRefreshToken("");
    };

    const closeCredentialEditor = () => {
        clearCredentialInputs();
        setEditingCredentials(false);
    };

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
            setSignatureEnabled(!!s.helpdesk_signature_enabled);
            setSignature(s.helpdesk_email_signature || "");
            setSignaturePreview(s.helpdesk_signature_preview || "");
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
                helpdesk_signature_enabled: signatureEnabled,
                helpdesk_email_signature: signature,
            };
            // Only send secrets the admin actually typed.
            if (clientId.trim()) body.client_id = clientId.trim();
            if (clientSecret.trim()) body.client_secret = clientSecret.trim();
            if (refreshToken.trim()) body.refresh_token = refreshToken.trim();
            const s = await updateZohoConfig(body);
            setStatus(s);
            setSignaturePreview(s.helpdesk_signature_preview || "");
            closeCredentialEditor();
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

                <div className="space-y-3 rounded-lg border p-3">
                    <div className="flex items-center justify-between">
                        <div>
                            <p className="font-medium">Email signature</p>
                            <p className="text-sm text-muted-foreground">
                                Auto-generated from your company settings (name, logo, address).
                            </p>
                        </div>
                        <Switch checked={signatureEnabled} onCheckedChange={setSignatureEnabled} disabled={!enabled} aria-label="Enable email signature" />
                    </div>
                    {signatureEnabled && (
                        <>
                            <div className="space-y-1">
                                <Label htmlFor="zoho-sig-tagline">Tagline (optional)</Label>
                                <Input
                                    id="zoho-sig-tagline"
                                    value={signature}
                                    onChange={(e) => setSignature(e.target.value)}
                                    placeholder="We're here to help — replies usually within a few hours."
                                />
                                <p className="text-xs text-muted-foreground">
                                    Short message shown under your team name. Leave blank to omit.
                                </p>
                            </div>
                            {signaturePreview && (
                                <div className="space-y-1">
                                    <p className="text-xs font-medium text-muted-foreground">Preview</p>
                                    <div
                                        className="rounded-md border bg-white p-3 dark:bg-zinc-950"
                                        dangerouslySetInnerHTML={{ __html: signaturePreview }}
                                    />
                                </div>
                            )}
                        </>
                    )}
                </div>

                <div className="space-y-3 rounded-lg border p-3">
                    <div className="flex items-center justify-between gap-4">
                        <div>
                            <p className="font-medium">OAuth credentials</p>
                            <p className="text-sm text-muted-foreground">
                                {[
                                    status?.has_client_id && "Client ID",
                                    status?.has_client_secret && "Client Secret",
                                    status?.has_refresh_token && "Refresh Token",
                                ].filter(Boolean).length === 3
                                    ? "Client ID, Client Secret and Refresh Token are saved."
                                    : "Not fully configured — add the values from your Zoho API console."}
                            </p>
                        </div>
                        {editingCredentials ? (
                            <Button variant="ghost" onClick={closeCredentialEditor}>Cancel</Button>
                        ) : (
                            <Button variant="outline" onClick={() => setEditingCredentials(true)}>
                                {status?.has_refresh_token ? "Replace credentials" : "Add credentials"}
                            </Button>
                        )}
                    </div>

                    {/* Rendered only on demand — see the note above the component. */}
                    {editingCredentials && (
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-1">
                                <Label htmlFor="zoho-client-id">Client ID {status?.has_client_id && <span className="text-xs text-emerald-600 dark:text-emerald-400">(saved)</span>}</Label>
                                <Input
                                    id="zoho-client-id"
                                    name="zoho-oauth-client-id"
                                    autoComplete="off"
                                    data-1p-ignore
                                    data-lpignore="true"
                                    data-form-type="other"
                                    value={clientId}
                                    onChange={(e) => setClientId(e.target.value)}
                                    placeholder={status?.has_client_id ? "•••••• (unchanged)" : "1000.…"}
                                />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="zoho-client-secret">Client Secret {status?.has_client_secret && <span className="text-xs text-emerald-600 dark:text-emerald-400">(saved)</span>}</Label>
                                <Input
                                    id="zoho-client-secret"
                                    name="zoho-oauth-client-secret"
                                    type="password"
                                    autoComplete="new-password"
                                    data-1p-ignore
                                    data-lpignore="true"
                                    data-form-type="other"
                                    value={clientSecret}
                                    onChange={(e) => setClientSecret(e.target.value)}
                                    placeholder={status?.has_client_secret ? "•••••• (unchanged)" : ""}
                                />
                            </div>
                            <div className="space-y-1 sm:col-span-2">
                                <Label htmlFor="zoho-refresh-token">Refresh Token {status?.has_refresh_token && <span className="text-xs text-emerald-600 dark:text-emerald-400">(saved)</span>}</Label>
                                <Input
                                    id="zoho-refresh-token"
                                    name="zoho-oauth-refresh-token"
                                    type="password"
                                    autoComplete="new-password"
                                    data-1p-ignore
                                    data-lpignore="true"
                                    data-form-type="other"
                                    value={refreshToken}
                                    onChange={(e) => setRefreshToken(e.target.value)}
                                    placeholder={status?.has_refresh_token ? "•••••• (unchanged)" : "1000.…"}
                                />
                                <p className="text-xs text-muted-foreground">
                                    Paste these from the Zoho API console — never let your browser fill them in.
                                    Leave a field blank to keep the saved value. Generate a self-client refresh
                                    token with the
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
                    )}
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
