"use client";

import { useEffect, useState } from "react";
import { getSettings, updateSettings } from "@/lib/api";
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
import { Save, Check } from "lucide-react";

export default function SettingsPage() {
    const [settings, setSettings] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        getSettings()
            .then(setSettings)
            .catch(() => { })
            .finally(() => setLoading(false));
    }, []);

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
                    {/* Driver Matching */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Driver Matching</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="space-y-2">
                                <Label>Matching Algorithm</Label>
                                <Select
                                    value={settings.driver_matching_algorithm || "nearest"}
                                    onValueChange={(v) =>
                                        update("driver_matching_algorithm", v)
                                    }
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="nearest">Nearest</SelectItem>
                                        <SelectItem value="rating_based">Rating Based</SelectItem>
                                        <SelectItem value="round_robin">Round Robin</SelectItem>
                                        <SelectItem value="combined">Combined</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>Search Radius (km)</Label>
                                <Input
                                    type="number"
                                    value={settings.search_radius_km || 10}
                                    onChange={(e) =>
                                        update("search_radius_km", parseFloat(e.target.value))
                                    }
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Min Driver Rating</Label>
                                <Input
                                    type="number"
                                    step="0.1"
                                    min="1"
                                    max="5"
                                    value={settings.min_driver_rating || 4.0}
                                    onChange={(e) =>
                                        update("min_driver_rating", parseFloat(e.target.value))
                                    }
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* Cancellation Fees */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Cancellation Fees</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="space-y-2">
                                <Label>Admin Cancellation Fee ($)</Label>
                                <Input
                                    type="number"
                                    step="0.50"
                                    value={settings.cancellation_fee_admin ?? 0.5}
                                    onChange={(e) =>
                                        update("cancellation_fee_admin", parseFloat(e.target.value))
                                    }
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Driver Cancellation Fee ($)</Label>
                                <Input
                                    type="number"
                                    step="0.50"
                                    value={settings.cancellation_fee_driver ?? 2.5}
                                    onChange={(e) =>
                                        update(
                                            "cancellation_fee_driver",
                                            parseFloat(e.target.value)
                                        )
                                    }
                                />
                            </div>
                        </CardContent>
                    </Card>

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
                                    From Stripe Dashboard → Developers → Webhooks
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
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="today">Today</SelectItem>
                                        <SelectItem value="7d">7 Days</SelectItem>
                                        <SelectItem value="30d">30 Days</SelectItem>
                                        <SelectItem value="90d">90 Days</SelectItem>
                                        <SelectItem value="1y">1 Year</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Radius (px)</Label>
                                    <Input
                                        type="number"
                                        value={settings.heat_map_radius || 25}
                                        onChange={(e) => update("heat_map_radius", parseInt(e.target.value))}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>Blur (px)</Label>
                                    <Input
                                        type="number"
                                        value={settings.heat_map_blur || 15}
                                        onChange={(e) => update("heat_map_blur", parseInt(e.target.value))}
                                    />
                                </div>
                            </div>

                            <div className="space-y-4 pt-2">
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="heat_map_show_pickups">Show Pickups by Default</Label>
                                    <Switch
                                        id="heat_map_show_pickups"
                                        checked={settings.heat_map_show_pickups ?? true}
                                        onCheckedChange={(v) => update("heat_map_show_pickups", v)}
                                    />
                                </div>
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="heat_map_show_dropoffs">Show Dropoffs by Default</Label>
                                    <Switch
                                        id="heat_map_show_dropoffs"
                                        checked={settings.heat_map_show_dropoffs ?? true}
                                        onCheckedChange={(v) => update("heat_map_show_dropoffs", v)}
                                    />
                                </div>
                            </div>

                            <Separator />

                            <div className="space-y-4">
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="corporate_heat_map_enabled">Enable Corporate Heat Map</Label>
                                    <Switch
                                        id="corporate_heat_map_enabled"
                                        checked={settings.corporate_heat_map_enabled ?? true}
                                        onCheckedChange={(v) => update("corporate_heat_map_enabled", v)}
                                    />
                                </div>
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="regular_rider_heat_map_enabled">Enable Regular Rider Heat Map</Label>
                                    <Switch
                                        id="regular_rider_heat_map_enabled"
                                        checked={settings.regular_rider_heat_map_enabled ?? true}
                                        onCheckedChange={(v) => update("regular_rider_heat_map_enabled", v)}
                                    />
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* General */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">General</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="space-y-2">
                                <Label>App Name</Label>
                                <Input
                                    value={settings.app_name || "Spinr"}
                                    onChange={(e) => update("app_name", e.target.value)}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Support Email</Label>
                                <Input
                                    type="email"
                                    value={settings.support_email || ""}
                                    onChange={(e) => update("support_email", e.target.value)}
                                    placeholder="support@spinr.app"
                                />
                            </div>
                        </CardContent>
                    </Card>

                    {/* Heat Map */}
                    <Card className="border-border/50">
                        <CardHeader>
                            <CardTitle className="text-base">Heat Map</CardTitle>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label>Enable Heat Map</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Show heat map feature in admin dashboard
                                    </p>
                                </div>
                                <Switch
                                    checked={settings.heat_map_enabled ?? true}
                                    onCheckedChange={(checked) =>
                                        update("heat_map_enabled", checked)
                                    }
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Default Date Range</Label>
                                <Select
                                    value={settings.heat_map_default_range || "30d"}
                                    onValueChange={(v) =>
                                        update("heat_map_default_range", v)
                                    }
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
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
                                    onValueChange={(v) =>
                                        update("heat_map_intensity", v)
                                    }
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
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
                                    <Input
                                        type="number"
                                        value={settings.heat_map_radius || 25}
                                        onChange={(e) =>
                                            update("heat_map_radius", parseInt(e.target.value))
                                        }
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>Blur (px)</Label>
                                    <Input
                                        type="number"
                                        value={settings.heat_map_blur || 15}
                                        onChange={(e) =>
                                            update("heat_map_blur", parseInt(e.target.value))
                                        }
                                    />
                                </div>
                            </div>
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label>Show Pickups</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Display pickup heat points
                                    </p>
                                </div>
                                <Switch
                                    checked={settings.heat_map_show_pickups ?? true}
                                    onCheckedChange={(checked) =>
                                        update("heat_map_show_pickups", checked)
                                    }
                                />
                            </div>
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label>Show Dropoffs</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Display dropoff heat points
                                    </p>
                                </div>
                                <Switch
                                    checked={settings.heat_map_show_dropoffs ?? true}
                                    onCheckedChange={(checked) =>
                                        update("heat_map_show_dropoffs", checked)
                                    }
                                />
                            </div>
                            <Separator />
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label>Corporate Heat Map</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Enable corporate-specific view
                                    </p>
                                </div>
                                <Switch
                                    checked={settings.corporate_heat_map_enabled ?? true}
                                    onCheckedChange={(checked) =>
                                        update("corporate_heat_map_enabled", checked)
                                    }
                                />
                            </div>
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label>Regular Rider Heat Map</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Enable regular rider view
                                    </p>
                                </div>
                                <Switch
                                    checked={settings.regular_rider_heat_map_enabled ?? true}
                                    onCheckedChange={(checked) =>
                                        update("regular_rider_heat_map_enabled", checked)
                                    }
                                />
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
                                <p className="text-xs text-muted-foreground">
                                    Supports basic formatting or markdown. Sent directly to driver/rider apps.
                                </p>
                            </div>
                            <div className="space-y-2">
                                <Label>Privacy Policy</Label>
                                <Textarea
                                    value={settings.privacy_policy_text || ""}
                                    onChange={(e) => update("privacy_policy_text", e.target.value)}
                                    placeholder="Enter full privacy policy text here..."
                                    className="min-h-[200px]"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Supports basic formatting or markdown. Sent directly to driver/rider apps.
                                </p>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
}
