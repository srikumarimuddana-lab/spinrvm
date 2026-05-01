"use client";

import { useEffect, useState } from "react";
import { getSettings, updateSettings, mfaStatus, mfaDisable } from "@/lib/api";
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
import { useRequireModule } from "@/hooks/useRequireModule";

export default function SettingsPage() {
    const { allowed } = useRequireModule("settings");
    const { toast } = useToast();
    const [settings, setSettings] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    const [mfaEnabled, setMfaEnabled] = useState(false);
    const [mfaLoading, setMfaLoading] = useState(true);
    const [showEnrollDialog, setShowEnrollDialog] = useState(false);
    const [showDisableForm, setShowDisableForm] = useState(false);
    const [disableTotp, setDisableTotp] = useState("");
    const [disablePassword, setDisablePassword] = useState("");
    const [disabling, setDisabling] = useState(false);
    const [disableError, setDisableError] = useState("");

    useEffect(() => {
        getSettings()
            .then(setSettings)
            .catch(() => { })
            .finally(() => setLoading(false));

        mfaStatus()
            .then((d) => setMfaEnabled(d.mfa_enabled))
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

    if (!allowed) return null;

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
                                    placeholder="Stripe publishable key"
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
                                    placeholder="Stripe secret key"
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
                                    placeholder="Stripe webhook secret"
                                />
                                <p className="text-xs text-muted-foreground">
                                    From Stripe Dashboard &rarr; Developers &rarr; Webhooks
                                </p>
                            </div>
                        </CardContent>
                    </Card>

                    {/* SMS / Twilio */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">SMS / Twilio</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <p className="text-xs text-muted-foreground">
                                When not configured, OTP defaults to <strong>1234</strong> for testing.
                            </p>
                            <div className="space-y-2">
                                <Label>Account SID</Label>
                                <Input
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
                    <Card className="border-border/50">
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
                                    {showDisableForm ? (
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
