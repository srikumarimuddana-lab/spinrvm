"use client";

import { useEffect, useState } from "react";
import { getSettings, updateSettings, mfaStatus, mfaDisable, adminUploadRideOfferSound, getAiCatalog, getEmailDeliverability, type AiCatalogProvider } from "@/lib/api";
import { MfaEnrollDialog } from "@/components/mfa-enroll-dialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Save, Check, ShieldCheck, ShieldOff } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";

export default function SettingsPage() {
    const { toast } = useToast();
    const [settings, setSettings] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    const [mfaEnabled, setMfaEnabled] = useState(false);
    const [mfaAvailable, setMfaAvailable] = useState(true);
    const [mfaEnforced, setMfaEnforced] = useState(false);
    const [mfaLoading, setMfaLoading] = useState(true);
    const [showEnrollDialog, setShowEnrollDialog] = useState(false);
    const [showDisableForm, setShowDisableForm] = useState(false);
    const [disableTotp, setDisableTotp] = useState("");
    const [disablePassword, setDisablePassword] = useState("");
    const [disabling, setDisabling] = useState(false);
    const [disableError, setDisableError] = useState("");

    const [soundUploading, setSoundUploading] = useState(false);
    const [aiCatalog, setAiCatalog] = useState<AiCatalogProvider[]>([]);

    const handleUploadOfferSound = async (file: File) => {
        if (file.size > 500 * 1024) {
            toast({ title: "File too large", description: "Max 500 KB.", variant: "destructive" });
            return;
        }
        setSoundUploading(true);
        try {
            const { ride_offer_sound_url } = await adminUploadRideOfferSound(file);
            setSettings({ ...settings, ride_offer_sound_url });
            toast({ title: "Ride-offer sound uploaded" });
        } catch (err) {
            console.error(err);
            toast({ title: "Upload failed", description: String((err as Error).message || err), variant: "destructive" });
        } finally {
            setSoundUploading(false);
        }
    };

    const handleClearOfferSound = () => {
        // Persisted on next "Save settings" click. Clearing in the form
        // triggers a normal PUT with ride_offer_sound_url="" — backend
        // stores it; driver-app falls back to the bundled placeholder.
        setSettings({ ...settings, ride_offer_sound_url: "" });
    };

    const [deliverability, setDeliverability] = useState<any>(null);
    useEffect(() => {
        getEmailDeliverability(7).then(setDeliverability).catch(() => setDeliverability(null));
    }, []);

    useEffect(() => {
        getSettings()
            .then(setSettings)
            .catch(() => { })
            .finally(() => setLoading(false));

        getAiCatalog()
            .then((d) => setAiCatalog(d.providers ?? []))
            .catch(() => { });

        mfaStatus()
            .then((d) => {
                setMfaEnabled(d.mfa_enabled);
                setMfaAvailable(d.available !== false);
                setMfaEnforced(d.enforced === true);
            })
            .catch(() => { })
            .finally(() => setMfaLoading(false));
    }, []);

    const handleDisableMfa = async () => {
        setDisabling(true);
        setDisableError("");
        try {
            await mfaDisable(disableTotp, disablePassword);
            setMfaEnabled(false);
            setShowDisableForm(false);
            setDisableTotp("");
            setDisablePassword("");
            toast({ title: "MFA disabled", description: "Two-factor authentication has been removed from your account." });
        } catch (e: any) {
            setDisableError(e.message || "Failed to disable MFA. Check your code and password.");
        } finally {
            setDisabling(false);
        }
    };

    const handleSave = async () => {
        if (!settings) return;
        setSaving(true);
        setSaved(false);
        try {
            const updated = await updateSettings(settings);
            setSettings(updated);
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
            if (updated?.audit_log_id) {
                toast({ title: "Settings saved", description: `Ref: ${updated.audit_log_id}` });
            }
        } catch (err: any) {
            // A silent catch here made failed saves look like successful
            // no-ops (403 privilege gate, 422 validation, missing migration
            // column → 500). Surface the backend's error so the operator
            // knows the save did NOT go through.
            toast({
                title: "Settings not saved",
                description: err?.message || "The server rejected the update.",
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    };

    const update = (key: string, value: any) => {
        setSettings((prev: any) => ({ ...prev, [key]: value }));
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center p-12">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
                    <p className="text-muted-foreground mt-1">
                        Configure platform-wide settings.
                    </p>
                </div>
                <Button onClick={handleSave} disabled={saving}>
                    {saved ? (
                        <>
                            <Check className="mr-2 h-4 w-4" /> Saved!
                        </>
                    ) : (
                        <>
                            <Save className="mr-2 h-4 w-4" /> {saving ? "Saving..." : "Save Changes"}
                        </>
                    )}
                </Button>
            </div>

            {settings && (
                <Tabs defaultValue="integrations" className="space-y-6">
                <TabsList className="h-auto flex-wrap">
                    <TabsTrigger value="integrations">Integrations</TabsTrigger>
                    <TabsTrigger value="email">Email &amp; Alerts</TabsTrigger>
                    <TabsTrigger value="operations">Operations</TabsTrigger>
                    <TabsTrigger value="company">Company &amp; Apps</TabsTrigger>
                    <TabsTrigger value="security">Security</TabsTrigger>
                </TabsList>

                <TabsContent value="integrations" className="mt-0 grid gap-6 lg:grid-cols-2 items-start">
                    {/* Stripe */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Stripe Payments</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="space-y-2">
                                <Label>Publishable Key</Label>
                                <Input
                                    value={settings.stripe_publishable_key || ""}
                                    onChange={(e) =>
                                        update("stripe_publishable_key", e.target.value)
                                    }
                                    placeholder="pk_test_..."
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Secret Key</Label>
                                <Input
                                    type="password"
                                    value={settings.stripe_secret_key || ""}
                                    onChange={(e) =>
                                        update("stripe_secret_key", e.target.value)
                                    }
                                    placeholder="sk_test_..."
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Webhook Secret</Label>
                                <Input
                                    type="password"
                                    value={settings.stripe_webhook_secret || ""}
                                    onChange={(e) =>
                                        update("stripe_webhook_secret", e.target.value)
                                    }
                                    placeholder="whsec_..."
                                />
                                <p className="text-xs text-muted-foreground">
                                    From Stripe Dashboard &rarr; Developers &rarr; Webhooks
                                </p>
                            </div>
                            <div className="space-y-2">
                                <Label>Connect Webhook Secret</Label>
                                <Input
                                    type="password"
                                    value={settings.stripe_connect_webhook_secret || ""}
                                    onChange={(e) =>
                                        update("stripe_connect_webhook_secret", e.target.value)
                                    }
                                    placeholder="whsec_..."
                                />
                                <p className="text-xs text-muted-foreground">
                                    Signing secret for the separate Connected-accounts webhook
                                    endpoint (account.updated, payout.paid, payout.failed). Leave
                                    blank if you only use one Stripe webhook endpoint.
                                </p>
                            </div>
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label htmlFor="stripe_auto_heal_processing">
                                        Auto-heal stuck payments
                                    </Label>
                                    <p className="text-xs text-muted-foreground">
                                        When a ride is stranded in <code>processing</code> (Stripe
                                        charged but the DB write failed), let the daily reconcile mark
                                        it paid from Stripe — it never re-charges. Leave OFF until
                                        validated in staging; enabling this moves money.
                                    </p>
                                </div>
                                <Switch
                                    id="stripe_auto_heal_processing"
                                    checked={!!settings.stripe_auto_heal_processing}
                                    onCheckedChange={(v) => update("stripe_auto_heal_processing", v)}
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* Google Maps */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Google Maps</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <p className="text-xs text-muted-foreground">
                                Used by the backend proxy for Places autocomplete, geocoding,
                                route distance/duration, driver ETA ranking, and static map images.
                                Restrict the key to these APIs in GCP Console: <strong>Places API (New)</strong>,{" "}
                                <strong>Geocoding API</strong>, <strong>Directions API</strong>,{" "}
                                <strong>Distance Matrix API</strong>, and <strong>Maps Static API</strong>.
                                Omitting Distance Matrix causes ETA-based driver ranking to fall back
                                to straight-line distance.
                            </p>
                            <div className="space-y-2">
                                <Label>API Key</Label>
                                <Input
                                    type="password"
                                    value={settings.google_maps_api_key || ""}
                                    onChange={(e) =>
                                        update("google_maps_api_key", e.target.value)
                                    }
                                    placeholder="AIza..."
                                />
                                <p className="text-xs text-muted-foreground">
                                    GCP Console &rarr; APIs &amp; Services &rarr; Credentials
                                </p>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Telephony / Twilio */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Telephony (Twilio)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <p className="text-xs text-muted-foreground">
                                When not configured, OTP defaults to <strong>1234</strong> for testing.
                                The Proxy Service SID enables anonymous in-ride calling.
                            </p>
                            <div className="space-y-2">
                                <Label>Account SID</Label>
                                <Input
                                    type="password"
                                    value={settings.twilio_account_sid || ""}
                                    onChange={(e) =>
                                        update("twilio_account_sid", e.target.value)
                                    }
                                    placeholder="AC..."
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Auth Token</Label>
                                <Input
                                    type="password"
                                    value={settings.twilio_auth_token || ""}
                                    onChange={(e) =>
                                        update("twilio_auth_token", e.target.value)
                                    }
                                    placeholder="Token"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>From Number</Label>
                                <Input
                                    value={settings.twilio_from_number || ""}
                                    onChange={(e) =>
                                        update("twilio_from_number", e.target.value)
                                    }
                                    placeholder="+1234567890"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Proxy Service SID</Label>
                                <Input
                                    type="password"
                                    value={settings.twilio_proxy_service_sid || ""}
                                    onChange={(e) =>
                                        update("twilio_proxy_service_sid", e.target.value)
                                    }
                                    placeholder="KS..."
                                />
                                <p className="text-xs text-muted-foreground">
                                    From Twilio Console &rarr; Proxy &rarr; Services
                                </p>
                            </div>
                        </CardContent>
                    </Card>

                    {/* AI Assistant — provider/model selection + key. One key
                        field per provider; switching providers rebinds the
                        field. Changes take effect within ~60s (settings TTL),
                        no redeploy. */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">AI Assistant</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            {(() => {
                                const provider = settings.ai_provider || "anthropic";
                                const entry = aiCatalog.find((p) => p.provider === provider);
                                const keyField = entry?.key_field ?? `ai_api_key_${provider}`;
                                const suggestions = entry?.models ?? [];
                                const isSuggested = suggestions.some((m) => m.id === settings.ai_model);
                                const modelSelectValue = isSuggested ? settings.ai_model : "__custom__";
                                return (
                                    <>
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <Label>Enable AI assistant</Label>
                                                <p className="text-xs text-muted-foreground">
                                                    Kill switch for the rider AI mode + SupportScreen chat.
                                                </p>
                                            </div>
                                            <Switch
                                                aria-label="Enable AI assistant"
                                                checked={!!settings.ai_assistant_enabled}
                                                onCheckedChange={(v) => update("ai_assistant_enabled", v)}
                                            />
                                        </div>
                                        {!settings.ai_assistant_enabled && (
                                            <div className="flex items-center justify-between rounded-lg border border-dashed p-3">
                                                <div>
                                                    <Label>While disabled, show</Label>
                                                    <p className="text-xs text-muted-foreground">
                                                        How the apps present AI entry points when the assistant is off.
                                                    </p>
                                                </div>
                                                <Select
                                                    value={settings.ai_disabled_mode || "coming_soon"}
                                                    onValueChange={(v) => update("ai_disabled_mode", v)}
                                                >
                                                    <SelectTrigger className="w-44" aria-label="While disabled, show"><SelectValue /></SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="coming_soon">Coming-soon placeholder</SelectItem>
                                                        <SelectItem value="hidden">Hide the icon entirely</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                        )}
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label>Provider</Label>
                                                <Select
                                                    value={provider}
                                                    onValueChange={(v) => {
                                                        update("ai_provider", v);
                                                        const next = aiCatalog.find((p) => p.provider === v);
                                                        if (next?.models?.length) update("ai_model", next.models[0].id);
                                                    }}
                                                >
                                                    <SelectTrigger aria-label="Provider"><SelectValue /></SelectTrigger>
                                                    <SelectContent>
                                                        {(aiCatalog.length ? aiCatalog : [{ provider: "anthropic", label: "Anthropic (Claude)", key_field: "ai_api_key_anthropic", models: [] }]).map((p) => (
                                                            <SelectItem key={p.provider} value={p.provider}>{p.label}</SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                            <div className="space-y-2">
                                                <Label>Model</Label>
                                                <Select
                                                    value={modelSelectValue}
                                                    onValueChange={(v) => update("ai_model", v === "__custom__" ? "" : v)}
                                                >
                                                    <SelectTrigger aria-label="Model"><SelectValue /></SelectTrigger>
                                                    <SelectContent>
                                                        {suggestions.map((m) => (
                                                            <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>
                                                        ))}
                                                        <SelectItem value="__custom__">Custom model id…</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                                {modelSelectValue === "__custom__" && (
                                                    <Input
                                                        value={settings.ai_model || ""}
                                                        onChange={(e) => update("ai_model", e.target.value)}
                                                        placeholder={provider === "openrouter" ? "vendor/model-id" : "model-id"}
                                                    />
                                                )}
                                            </div>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>{entry?.label ?? provider} API key</Label>
                                            <Input
                                                type="password"
                                                value={settings[keyField] || ""}
                                                onChange={(e) => update(keyField, e.target.value)}
                                                placeholder="Paste the key for the selected provider"
                                            />
                                            <p className="text-xs text-muted-foreground">
                                                Saved per provider — switching back later reuses the stored key.
                                            </p>
                                        </div>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="ai_daily_message_cap">Daily messages per user</Label>
                                                <Input
                                                    id="ai_daily_message_cap"
                                                    type="number" min={1} max={500}
                                                    value={settings.ai_daily_message_cap ?? 50}
                                                    onChange={(e) => update("ai_daily_message_cap", parseInt(e.target.value))}
                                                />
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="ai_max_output_tokens">Max output tokens</Label>
                                                <Input
                                                    id="ai_max_output_tokens"
                                                    type="number" min={128} max={4096}
                                                    value={settings.ai_max_output_tokens ?? 1024}
                                                    onChange={(e) => update("ai_max_output_tokens", parseInt(e.target.value))}
                                                />
                                            </div>
                                        </div>
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <Label>MCP server (/mcp)</Label>
                                                <p className="text-xs text-muted-foreground">
                                                    Read-only tool access for external agent clients. Leave off unless needed.
                                                </p>
                                            </div>
                                            <Switch
                                                aria-label="MCP server (/mcp)"
                                                checked={!!settings.ai_mcp_enabled}
                                                onCheckedChange={(v) => update("ai_mcp_enabled", v)}
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="ai_mcp_daily_tool_cap">MCP daily tool calls per user</Label>
                                            <Input
                                                id="ai_mcp_daily_tool_cap"
                                                type="number" min={0} max={5000}
                                                value={settings.ai_mcp_daily_tool_cap ?? 0}
                                                onChange={(e) => update("ai_mcp_daily_tool_cap", parseInt(e.target.value))}
                                            />
                                            <p className="text-xs text-muted-foreground">
                                                0 = use the chat daily-message cap above.
                                            </p>
                                        </div>
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <Label>AI escalation opens Zoho tickets</Label>
                                                <p className="text-xs text-muted-foreground">
                                                    Off = the assistant only deep-links to human support (recommended).
                                                </p>
                                            </div>
                                            <Switch
                                                aria-label="AI escalation opens Zoho tickets"
                                                checked={!!settings.ai_escalation_creates_ticket}
                                                onCheckedChange={(v) => update("ai_escalation_creates_ticket", v)}
                                            />
                                        </div>
                                    </>
                                );
                            })()}
                        </CardContent>
                    </Card>

                    {/* Driver LMS (training platform) — feeds the Training tab
                        in the driver details drawer, matched by phone number. */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Driver LMS (Training)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <p className="text-xs text-muted-foreground">
                                Connects to the Spinr driver training LMS. When configured, the
                                driver details drawer shows each driver&apos;s registration,
                                completion percentage, certificates, and training history,
                                matched by phone number. The API key must equal the{" "}
                                <code>SPINR_INTEGRATION_API_KEY</code> set on the LMS deployment.
                            </p>
                            <div className="space-y-2">
                                <Label>LMS Base URL</Label>
                                <Input
                                    value={settings.lms_api_base_url || ""}
                                    onChange={(e) => update("lms_api_base_url", e.target.value)}
                                    placeholder="https://training.spinr.ca"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>API Key</Label>
                                <Input
                                    type="password"
                                    value={settings.lms_api_key || ""}
                                    onChange={(e) => update("lms_api_key", e.target.value)}
                                    placeholder="Shared secret"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Leave both blank to disable the integration — the drawer&apos;s
                                    Training tab will report it as not configured.
                                </p>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
                <TabsContent value="email" className="mt-0 grid gap-6 lg:grid-cols-2 items-start">
                    {/* Email — AWS SES (primary) */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Email — AWS SES (primary)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <p className="text-xs text-muted-foreground">
                                Primary provider for ride receipts, T4A links, DSAR exports and
                                safety/corporate alerts. When the access key and secret are set,
                                email is sent via SES; if SES is blank or a send fails, Spinr falls
                                back to Resend below. The <strong>From Email</strong> must be a
                                verified identity in the SES account.
                            </p>
                            <div className="space-y-2">
                                <Label>Region</Label>
                                <Input
                                    type="text"
                                    value={settings.aws_ses_region || ""}
                                    onChange={(e) =>
                                        update("aws_ses_region", e.target.value)
                                    }
                                    placeholder="ca-central-1"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Access Key ID</Label>
                                <Input
                                    type="text"
                                    value={settings.aws_ses_access_key_id || ""}
                                    onChange={(e) =>
                                        update("aws_ses_access_key_id", e.target.value)
                                    }
                                    placeholder="AKIA...."
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Secret Access Key</Label>
                                <Input
                                    type="password"
                                    value={settings.aws_ses_secret_access_key || ""}
                                    onChange={(e) =>
                                        update("aws_ses_secret_access_key", e.target.value)
                                    }
                                    placeholder="••••••••"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>From Email</Label>
                                <Input
                                    type="email"
                                    value={settings.aws_ses_from_email || ""}
                                    onChange={(e) =>
                                        update("aws_ses_from_email", e.target.value)
                                    }
                                    placeholder="receipts@spinr.ca"
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* Email — Resend (fallback guardrail) */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Email — Resend (fallback)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <p className="text-xs text-muted-foreground">
                                Guardrail provider. Used only when AWS SES above is unconfigured or a
                                send fails. If <strong>From Email</strong> is blank, the SES from
                                address (then a code default) is used as a fallback.
                            </p>
                            <div className="space-y-2">
                                <Label>API Key</Label>
                                <Input
                                    type="password"
                                    value={settings.resend_api_key || ""}
                                    onChange={(e) =>
                                        update("resend_api_key", e.target.value)
                                    }
                                    placeholder="re_...."
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>From Email</Label>
                                <Input
                                    type="email"
                                    value={settings.resend_from_email || ""}
                                    onChange={(e) =>
                                        update("resend_from_email", e.target.value)
                                    }
                                    placeholder="receipts@spinr.ca"
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* Email deliverability */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Email deliverability (last 7 days)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            {!deliverability ? (
                                <p className="text-xs text-muted-foreground">No send data yet.</p>
                            ) : (
                                <>
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                        <div className="rounded-lg border border-border/50 p-3">
                                            <p className="text-xs text-muted-foreground">Sent</p>
                                            <p className="text-lg font-bold">{deliverability.by_status?.sent ?? 0}</p>
                                        </div>
                                        <div className="rounded-lg border border-border/50 p-3">
                                            <p className="text-xs text-muted-foreground">Failed</p>
                                            <p className={`text-lg font-bold ${deliverability.failure_rate > 0.01 ? "text-red-600" : ""}`}>{deliverability.by_status?.failed ?? 0}</p>
                                        </div>
                                        <div className="rounded-lg border border-border/50 p-3">
                                            <p className="text-xs text-muted-foreground">Failure rate</p>
                                            <p className="text-lg font-bold">{((deliverability.failure_rate ?? 0) * 100).toFixed(1)}%</p>
                                        </div>
                                        <div className="rounded-lg border border-border/50 p-3">
                                            <p className="text-xs text-muted-foreground">Suppressed (total)</p>
                                            <p className="text-lg font-bold">{deliverability.suppression_list_size ?? 0}</p>
                                        </div>
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                        By provider:{" "}
                                        {Object.entries(deliverability.by_provider || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}
                                    </p>
                                    {deliverability.recent_failures?.length > 0 && (
                                        <div className="space-y-1">
                                            <p className="text-xs font-semibold">Recent failures</p>
                                            {deliverability.recent_failures.slice(0, 8).map((f: any, i: number) => (
                                                <div key={i} className="text-xs text-muted-foreground flex gap-2">
                                                    <span className="whitespace-nowrap">{new Date(f.created_at).toLocaleDateString()}</span>
                                                    <span className="font-medium">{f.email_type}</span>
                                                    <span>via {f.provider}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </>
                            )}
                        </CardContent>
                    </Card>

                    {/* Safety — distribution list for SOS / safety-incident
                        alert emails. Empty disables outbound email cleanly;
                        WS broadcast + critical log line still fire so
                        on-call paging via log aggregators keeps working. */}
                    <Card className="border-border/50 lg:col-span-2">
                        <CardHeader>
                            <CardTitle className="text-base">Safety alerts</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-3">
                            <p className="text-xs text-muted-foreground">
                                These addresses receive a transactional email whenever a safety
                                incident opens — rider SOS, driver-app safety report, or the
                                auto check-in escalation. Comma-separated. Leave blank to disable
                                email alerts (admin dashboard WS notification + critical log line
                                still fire so log-aggregator paging keeps working).
                            </p>
                            <div className="space-y-2">
                                <Label htmlFor="safety-emails">Alert recipients</Label>
                                <Textarea
                                    id="safety-emails"
                                    value={settings.safety_alert_emails || ""}
                                    onChange={(e) =>
                                        update("safety_alert_emails", e.target.value)
                                    }
                                    placeholder="safety@spinr.ca, oncall@spinr.ca"
                                    rows={2}
                                />
                                <p className="text-[11px] text-muted-foreground">
                                    Recipients with read access to the safety queue in the admin
                                    dashboard. Email body contains the reporter, ride id, and (when
                                    available) the location — same data the dashboard surfaces.
                                </p>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
                <TabsContent value="operations" className="mt-0 grid gap-6 lg:grid-cols-2 items-start">
                    {/* Dispatch & Matching */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Dispatch &amp; Matching</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <p className="text-xs text-muted-foreground">
                                Global defaults. Per-service-area overrides take precedence when set.
                            </p>
                            <div className="space-y-2">
                                <Label>Driver matching algorithm</Label>
                                <Select
                                    value={settings.driver_matching_algorithm || "nearest"}
                                    onValueChange={(v) => update("driver_matching_algorithm", v)}
                                >
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="nearest">Nearest</SelectItem>
                                        <SelectItem value="rating_based">Highest rated</SelectItem>
                                        <SelectItem value="combined">Combined (nearest + rating)</SelectItem>
                                        <SelectItem value="round_robin">Round robin</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Simultaneous offers</Label>
                                    <Input
                                        type="number"
                                        min={1} max={10}
                                        value={settings.max_simultaneous_offers ?? 3}
                                        onChange={(e) => update("max_simultaneous_offers", parseInt(e.target.value))}
                                    />
                                    <p className="text-xs text-muted-foreground">Drivers offered per ride (1–10)</p>
                                </div>
                                <div className="space-y-2">
                                    <Label>Offer timeout (s)</Label>
                                    <Input
                                        type="number"
                                        min={5} max={60}
                                        value={settings.ride_offer_timeout_seconds ?? 15}
                                        onChange={(e) => update("ride_offer_timeout_seconds", parseInt(e.target.value))}
                                    />
                                    <p className="text-xs text-muted-foreground">Seconds before offer expires (5–60)</p>
                                </div>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Search radius (km)</Label>
                                    <Input
                                        type="number"
                                        min={1} max={100}
                                        value={settings.search_radius_km ?? 10}
                                        onChange={(e) => update("search_radius_km", parseFloat(e.target.value))}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>Min driver rating</Label>
                                    <Input
                                        type="number"
                                        min={1} max={5} step={0.1}
                                        value={settings.min_driver_rating ?? 4.0}
                                        onChange={(e) => update("min_driver_rating", parseFloat(e.target.value))}
                                    />
                                </div>
                            </div>
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label htmlFor="use_eta_ranking">ETA-based ranking</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Use Google Distance Matrix to rank by real ETA instead of straight-line distance.
                                    </p>
                                </div>
                                <Switch
                                    id="use_eta_ranking"
                                    checked={settings.use_eta_ranking ?? true}
                                    onCheckedChange={(v) => update("use_eta_ranking", v)}
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* Heat Map Configuration */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Heat Map Configuration</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="flex items-center justify-between">
                                <Label htmlFor="heat_map_enabled">Enable Heat Map</Label>
                                <Switch
                                    id="heat_map_enabled"
                                    checked={settings.heat_map_enabled ?? true}
                                    onCheckedChange={(v) => update("heat_map_enabled", v)}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Default Time Range</Label>
                                <Select
                                    value={settings.heat_map_default_range || "30d"}
                                    onValueChange={(v) => update("heat_map_default_range", v)}
                                >
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="today">Today</SelectItem>
                                        <SelectItem value="7d">7 Days</SelectItem>
                                        <SelectItem value="30d">30 Days</SelectItem>
                                        <SelectItem value="90d">90 Days</SelectItem>
                                        <SelectItem value="1y">1 Year</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>Heat Intensity</Label>
                                <Select
                                    value={settings.heat_map_intensity || "medium"}
                                    onValueChange={(v) => update("heat_map_intensity", v)}
                                >
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="low">Low</SelectItem>
                                        <SelectItem value="medium">Medium</SelectItem>
                                        <SelectItem value="high">High</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Radius (px)</Label>
                                    <Input type="number" value={settings.heat_map_radius || 25}
                                        onChange={(e) => update("heat_map_radius", parseInt(e.target.value))} />
                                </div>
                                <div className="space-y-2">
                                    <Label>Blur (px)</Label>
                                    <Input type="number" value={settings.heat_map_blur || 15}
                                        onChange={(e) => update("heat_map_blur", parseInt(e.target.value))} />
                                </div>
                            </div>
                            <div className="space-y-4 pt-2">
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="heat_map_show_pickups">Show Pickups</Label>
                                    <Switch id="heat_map_show_pickups" checked={settings.heat_map_show_pickups ?? true}
                                        onCheckedChange={(v) => update("heat_map_show_pickups", v)} />
                                </div>
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="heat_map_show_dropoffs">Show Dropoffs</Label>
                                    <Switch id="heat_map_show_dropoffs" checked={settings.heat_map_show_dropoffs ?? true}
                                        onCheckedChange={(v) => update("heat_map_show_dropoffs", v)} />
                                </div>
                            </div>
                            <Separator />
                            <div className="space-y-4">
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="corporate_heat_map_enabled">Corporate Heat Map</Label>
                                    <Switch id="corporate_heat_map_enabled" checked={settings.corporate_heat_map_enabled ?? true}
                                        onCheckedChange={(v) => update("corporate_heat_map_enabled", v)} />
                                </div>
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="regular_rider_heat_map_enabled">Regular Rider Heat Map</Label>
                                    <Switch id="regular_rider_heat_map_enabled" checked={settings.regular_rider_heat_map_enabled ?? true}
                                        onCheckedChange={(v) => update("regular_rider_heat_map_enabled", v)} />
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Fare Lock — SK regulatory requirement: the price shown
                        to the rider at booking time is the price charged,
                        regardless of actual distance/time deviation. */}
                    <Card className="border-border/50 lg:col-span-2">
                        <CardHeader>
                            <CardTitle className="text-base">Fare Lock (SK Regulation)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label htmlFor="fare_lock_enabled">Lock fare at booking time</Label>
                                    <p className="text-xs text-muted-foreground">
                                        When enabled, the fare shown to the rider before the ride is the final charge —
                                        no recalculation at completion regardless of distance or time deviation.
                                        Required under Saskatchewan ride-share regulations.
                                    </p>
                                </div>
                                <Switch
                                    id="fare_lock_enabled"
                                    checked={settings.fare_lock_enabled ?? false}
                                    onCheckedChange={(v) => update("fare_lock_enabled", v)}
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* admin-dashboard visual-refresh epic (#2785 Phase 3+) —
                        canary flag for the shared shell/typography/radius
                        restyle. Off by default; effective within 60s via the
                        existing settings cache, no redeploy needed. */}
                    <Card className="border-border/50 lg:col-span-2">
                        <CardHeader>
                            <CardTitle className="text-base">Admin Dashboard Appearance (Beta)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label htmlFor="admin_theme_v2_enabled">Enable refreshed admin theme</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Canary flag for the in-progress visual refresh (shared nav/shell, typography, spacing).
                                        Off by default — takes effect for all staff within about a minute of toggling, no redeploy.
                                    </p>
                                </div>
                                <Switch
                                    id="admin_theme_v2_enabled"
                                    checked={settings.admin_theme_v2_enabled ?? false}
                                    onCheckedChange={(v) => update("admin_theme_v2_enabled", v)}
                                />
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
                <TabsContent value="company" className="mt-0 grid gap-6 lg:grid-cols-2 items-start">
                    {/* Company Info — surfaced in rider & driver apps
                        via GET /api/company-info (public endpoint). */}
                    <Card className="border-border/50 lg:col-span-2">
                        <CardHeader>
                            <CardTitle className="text-base">Company Info (shown in apps)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label>Company Name</Label>
                                <Input
                                    value={settings.company_name || ""}
                                    onChange={(e) => update("company_name", e.target.value)}
                                    placeholder="Spinr"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Phone</Label>
                                <Input
                                    value={settings.company_phone || ""}
                                    onChange={(e) => update("company_phone", e.target.value)}
                                    placeholder="+1 306 555 0100"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Email</Label>
                                <Input
                                    type="email"
                                    value={settings.company_email || ""}
                                    onChange={(e) => update("company_email", e.target.value)}
                                    placeholder="support@spinr.ca"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Website</Label>
                                <Input
                                    value={settings.company_website || ""}
                                    onChange={(e) => update("company_website", e.target.value)}
                                    placeholder="https://spinr.ca"
                                />
                            </div>
                            <div className="space-y-2 sm:col-span-2">
                                <Label>Address</Label>
                                <Textarea
                                    value={settings.company_address || ""}
                                    onChange={(e) => update("company_address", e.target.value)}
                                    placeholder="123 Example St, Saskatoon, SK S7K 1A1"
                                    className="min-h-[70px]"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Name and address above also appear in the footer of every
                                    transactional email (welcome, receipts, account notices).
                                </p>
                            </div>
                            <div className="space-y-2 sm:col-span-2">
                                <Label>Email logo URL</Label>
                                <Input
                                    value={settings.company_logo_url || ""}
                                    onChange={(e) => update("company_logo_url", e.target.value)}
                                    placeholder="Leave blank to use the built-in Spinr logo"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Shown in the header of transactional emails. Leave blank to use
                                    the built-in Spinr logo — that is the normal setting, not a
                                    placeholder. Must be a full https:// link to a publicly
                                    reachable image; anything else falls back to the built-in logo.
                                    Does not affect report PDFs.
                                </p>
                            </div>
                            <div className="space-y-2 sm:col-span-2">
                                <div className="flex items-center justify-between gap-4">
                                    <Label htmlFor="branded_receipt_enabled">
                                        Branded receipts &amp; invoices
                                    </Label>
                                    <Switch
                                        id="branded_receipt_enabled"
                                        checked={settings.branded_receipt_enabled !== false}
                                        onCheckedChange={(v) => update("branded_receipt_enabled", v)}
                                    />
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Renders ride receipts and Spinr Pass invoices with the logo and
                                    company details above. Turn off to go back to the older plain
                                    layout if something looks wrong in a real inbox — fare lines,
                                    GST/PST and totals are unaffected either way.
                                </p>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Legal Documents */}
                    <Card className="border-border/50 lg:col-span-2">
                        <CardHeader>
                            <CardTitle className="text-base">Legal Documents</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-6">
                            <div className="space-y-2">
                                <Label>Terms of Service</Label>
                                <Textarea
                                    value={settings.terms_of_service_text || ""}
                                    onChange={(e) => update("terms_of_service_text", e.target.value)}
                                    placeholder="Enter full terms of service text here..."
                                    className="min-h-[200px]"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Privacy Policy</Label>
                                <Textarea
                                    value={settings.privacy_policy_text || ""}
                                    onChange={(e) => update("privacy_policy_text", e.target.value)}
                                    placeholder="Enter full privacy policy text here..."
                                    className="min-h-[200px]"
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* Share Trip tracking URL — base of the public live-tracking
                        page that rider-app embeds in the "Share Trip" message.
                        Recipients open ${base}/${share_token}; the URL is served
                        runtime from app_settings so it rotates without a mobile
                        rebuild. Should point at the admin dashboard's /track route
                        — e.g. https://track.spinr.ca/track. */}
                    <Card className="border-border/50 lg:col-span-2">
                        <CardHeader>
                            <CardTitle className="text-base">Share Trip — Live Tracking URL</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-2">
                            <Label>Tracking base URL</Label>
                            <Input
                                value={settings.track_base_url || ""}
                                onChange={(e) => update("track_base_url", e.target.value)}
                                placeholder="https://track.spinr.ca/track"
                            />
                            <p className="text-xs text-muted-foreground">
                                Used by the rider-app "Share Trip" message. Set this to the
                                deployed admin dashboard's /track route. Leave blank to disable
                                in-app sharing.
                            </p>
                        </CardContent>
                    </Card>

                    {/* Driver-app ride-offer alert tone — uploaded via
                        a dedicated multipart endpoint that writes the URL
                        directly to settings.ride_offer_sound_url. */}
                    <Card className="border-border/50 lg:col-span-2">
                        <CardHeader>
                            <CardTitle className="text-base">Ride-offer alert sound (driver app)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="space-y-2">
                                <Label>Upload MP3 or WAV (≤500 KB)</Label>
                                <div className="flex items-center gap-2">
                                    <Input
                                        type="file"
                                        accept="audio/mpeg,audio/mp3,audio/wav"
                                        disabled={soundUploading}
                                        onChange={(e) => {
                                            const f = e.target.files?.[0];
                                            if (f) void handleUploadOfferSound(f);
                                            e.target.value = "";
                                        }}
                                    />
                                    {soundUploading && (
                                        <span className="text-xs text-muted-foreground">Uploading…</span>
                                    )}
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Drivers hear this tone every ~2.5s while a ride offer is on screen. Leave empty to use the bundled placeholder.
                                </p>
                            </div>
                            {settings.ride_offer_sound_url ? (
                                <div className="space-y-2">
                                    <Label>Current</Label>
                                    {/* Native HTML5 audio — no extra dep. */}
                                    <audio controls src={settings.ride_offer_sound_url} className="w-full" />
                                    <div className="flex gap-2">
                                        <Button
                                            type="button"
                                            variant="outline"
                                            size="sm"
                                            onClick={handleClearOfferSound}
                                        >
                                            Clear (revert to bundled)
                                        </Button>
                                    </div>
                                </div>
                            ) : (
                                <p className="text-xs text-muted-foreground italic">
                                    No custom sound set — drivers play the bundled placeholder.
                                </p>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
                <TabsContent value="security" className="mt-0 grid gap-6 lg:grid-cols-2 items-start">
                    {/* Two-Factor Authentication */}
                    {mfaAvailable && <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Two-Factor Authentication</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            {mfaLoading ? (
                                <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                            ) : mfaEnabled ? (
                                <>
                                    <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
                                        <ShieldCheck className="h-4 w-4" />
                                        MFA is enabled on your account.
                                    </div>
                                    {mfaEnforced ? (
                                        <p className="text-sm text-muted-foreground">
                                            Two-factor authentication is required for all staff accounts
                                            and cannot be disabled. If you lose access to your
                                            authenticator, use a backup code at login or ask a super
                                            admin to reset your MFA.
                                        </p>
                                    ) : showDisableForm ? (
                                        <div className="space-y-3">
                                            {disableError && (
                                                <div className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">
                                                    {disableError}
                                                </div>
                                            )}
                                            <div className="space-y-1">
                                                <Label htmlFor="disable-totp">Authenticator Code</Label>
                                                <Input
                                                    id="disable-totp"
                                                    type="text"
                                                    inputMode="numeric"
                                                    placeholder="000000"
                                                    maxLength={8}
                                                    value={disableTotp}
                                                    onChange={(e) => setDisableTotp(e.target.value.toUpperCase())}
                                                />
                                            </div>
                                            <div className="space-y-1">
                                                <Label htmlFor="disable-password">Current Password</Label>
                                                <Input
                                                    id="disable-password"
                                                    type="password"
                                                    placeholder="••••••••"
                                                    value={disablePassword}
                                                    onChange={(e) => setDisablePassword(e.target.value)}
                                                />
                                            </div>
                                            <div className="flex gap-2">
                                                <Button
                                                    variant="destructive"
                                                    onClick={handleDisableMfa}
                                                    disabled={disabling || disableTotp.length < 6 || !disablePassword}
                                                >
                                                    {disabling ? "Disabling…" : "Disable MFA"}
                                                </Button>
                                                <Button
                                                    variant="outline"
                                                    onClick={() => { setShowDisableForm(false); setDisableError(""); }}
                                                >
                                                    Cancel
                                                </Button>
                                            </div>
                                        </div>
                                    ) : (
                                        <Button
                                            variant="outline"
                                            className="gap-2"
                                            onClick={() => setShowDisableForm(true)}
                                        >
                                            <ShieldOff className="h-4 w-4" />
                                            Disable MFA
                                        </Button>
                                    )}
                                </>
                            ) : (
                                <>
                                    <p className="text-sm text-muted-foreground">
                                        Protect your account with an authenticator app. Required for all staff with elevated permissions.
                                    </p>
                                    <Button
                                        className="gap-2"
                                        onClick={() => setShowEnrollDialog(true)}
                                    >
                                        <ShieldCheck className="h-4 w-4" />
                                        Enable MFA
                                    </Button>
                                </>
                            )}
                        </CardContent>
                    </Card>}
                    {!mfaAvailable && (
                        <p className="text-sm text-muted-foreground">Two-factor authentication is not available on this deployment.</p>
                    )}
                    {/* Corporate + admin portal review, High #5: the dual-approval
                        gate for large PII-bearing exports (driver/rider bulk
                        exports, compliance reports over 1,000 rows) already exists
                        in the backend but had no settings field or UI to turn it
                        on — it could previously only be flipped via a direct SQL
                        update. Off by default (same posture as the appearance
                        beta flag above); requires a second super_admin's approval
                        on each gated export once enabled, per the existing
                        Export Approvals queue. */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Data Export Approvals</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label htmlFor="dual_approval_exports_enabled">
                                        Require a second super admin to approve large PII exports
                                    </Label>
                                    <p className="text-xs text-muted-foreground">
                                        When on, large driver/rider bulk exports and compliance reports over
                                        1,000 rows need a second super admin&apos;s sign-off before they run —
                                        see the Export Approvals queue under Records. Off by default.
                                    </p>
                                </div>
                                <Switch
                                    id="dual_approval_exports_enabled"
                                    checked={settings.dual_approval_exports_enabled ?? false}
                                    onCheckedChange={(v) => update("dual_approval_exports_enabled", v)}
                                />
                            </div>
                        </CardContent>
                    </Card>
                    {/* Corporate + admin portal review, "$100k/minute" finding: the
                        wallet /adjust endpoint accepted up to $100,000 per call with
                        no limit on repeated calls by the same admin. A daily
                        cumulative cap now blocks the total, but had no settings
                        field — the backend fell back to a $50,000 default with no
                        way to see or change it short of a direct SQL update. */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Corporate Wallet Safeguards</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="corporate_wallet_admin_adjust_daily_cap">
                                    Daily admin wallet-adjustment cap (CAD, per admin)
                                </Label>
                                <p className="text-xs text-muted-foreground">
                                    Caps how much any one admin can move via manual corporate wallet
                                    adjustments in a single UTC day, across all companies combined.
                                    Defaults to $50,000 if left blank.
                                </p>
                                <Input
                                    id="corporate_wallet_admin_adjust_daily_cap"
                                    type="number" min={1} step={100}
                                    value={settings.corporate_wallet_admin_adjust_daily_cap ?? ""}
                                    placeholder="50000"
                                    onChange={(e) =>
                                        update(
                                            "corporate_wallet_admin_adjust_daily_cap",
                                            e.target.value === "" ? null : parseFloat(e.target.value),
                                        )
                                    }
                                />
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
                </Tabs>
            )}

            <MfaEnrollDialog
                open={showEnrollDialog}
                onOpenChange={setShowEnrollDialog}
                onEnrolled={() => setMfaEnabled(true)}
            />
        </div>
    );
}
