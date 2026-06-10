"use client";

import { useEffect, useState } from "react";
import { getSettings, updateSettings, mfaStatus, mfaDisable, adminUploadRideOfferSound } from "@/lib/api";
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

    useEffect(() => {
        getSettings()
            .then(setSettings)
            .catch(() => { })
            .finally(() => setLoading(false));

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
        } catch {
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
                <div className="grid gap-6 lg:grid-cols-2">
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
                        </CardContent>
                    </Card>

                    {/* Email / Resend */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Email (Resend)</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <p className="text-xs text-muted-foreground">
                                Used to send ride receipt emails to riders. If <strong>From Email</strong> is
                                blank, the <code>email_from</code> setting is used as a fallback.
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
                            </div>
                        </CardContent>
                    </Card>

                    {/* Share Trip tracking URL — base of the public live-tracking
                        page that rider-app embeds in the "Share Trip" message.
                        Recipients open ${base}/${share_token}; the URL is served
                        runtime from app_settings so it rotates without a mobile
                        rebuild. Should point at the admin dashboard's /track route
                        — e.g. https://spinrvm.vercel.app/track. */}
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
                                placeholder="https://spinrvm.vercel.app/track"
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
                </div>
            )}

            <MfaEnrollDialog
                open={showEnrollDialog}
                onOpenChange={setShowEnrollDialog}
                onEnrolled={() => setMfaEnabled(true)}
            />
        </div>
    );
}
