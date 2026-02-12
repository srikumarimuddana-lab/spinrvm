"use client";

import { useEffect, useState } from "react";
import { getSettings, updateSettings } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
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
                </div>
            )}
        </div>
    );
}
