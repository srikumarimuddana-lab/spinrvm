"use client";

import { useEffect, useState } from "react";
import { getServiceAreas, updateServiceArea } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { TrendingUp, Zap, AlertTriangle } from "lucide-react";

export default function SurgePage() {
    const [areas, setAreas] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState<string | null>(null);

    const fetchAreas = () => {
        setLoading(true);
        getServiceAreas()
            .then(setAreas)
            .catch(() => { })
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        fetchAreas();
    }, []);

    const handleToggleSurge = async (area: any) => {
        setSaving(area.id);
        try {
            await updateServiceArea(area.id, {
                surge_active: !area.surge_active,
            });
            setAreas((prev) =>
                prev.map((a) =>
                    a.id === area.id ? { ...a, surge_active: !a.surge_active } : a
                )
            );
        } catch {
        } finally {
            setSaving(null);
        }
    };

    const handleUpdateMultiplier = async (area: any, multiplier: number) => {
        if (multiplier < 1 || multiplier > 10) return;
        setSaving(area.id);
        try {
            await updateServiceArea(area.id, { surge_multiplier: multiplier });
            setAreas((prev) =>
                prev.map((a) =>
                    a.id === area.id ? { ...a, surge_multiplier: multiplier } : a
                )
            );
        } catch {
        } finally {
            setSaving(null);
        }
    };

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Surge Pricing</h1>
                <p className="text-muted-foreground mt-1">
                    Manually control surge pricing per service area. Toggle on/off and set
                    the multiplier.
                </p>
            </div>

            <Card className="border-amber-500/30 bg-amber-500/5">
                <CardContent className="flex items-start gap-3 pt-6">
                    <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
                    <div>
                        <p className="text-sm font-medium">Hybrid Surge Model</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                            Surge pricing is fully admin-controlled. Toggle it on for a service
                            area and set the multiplier. All active fares in that area will be
                            multiplied by the surge factor.
                        </p>
                    </div>
                </CardContent>
            </Card>

            {loading ? (
                <div className="flex items-center justify-center p-12">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                </div>
            ) : areas.length === 0 ? (
                <Card className="border-border/50">
                    <CardContent className="py-12 text-center text-muted-foreground">
                        No service areas configured. Add service areas first.
                    </CardContent>
                </Card>
            ) : (
                <div className="grid gap-4 md:grid-cols-2">
                    {areas.map((area) => (
                        <Card
                            key={area.id}
                            className={`border-border/50 transition-all ${area.surge_active
                                    ? "border-amber-500/40 ring-1 ring-amber-500/20"
                                    : ""
                                }`}
                        >
                            <CardHeader className="flex flex-row items-center justify-between pb-3">
                                <div>
                                    <CardTitle className="text-base">{area.name}</CardTitle>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        {area.city || "—"}
                                    </p>
                                </div>
                                <Badge
                                    variant="secondary"
                                    className={
                                        area.surge_active
                                            ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                                            : "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400"
                                    }
                                >
                                    {area.surge_active ? (
                                        <span className="flex items-center gap-1">
                                            <Zap className="h-3 w-3" /> Active
                                        </span>
                                    ) : (
                                        "Inactive"
                                    )}
                                </Badge>
                            </CardHeader>
                            <Separator />
                            <CardContent className="pt-4 space-y-4">
                                <div className="flex items-center justify-between">
                                    <Label className="text-sm">Enable Surge</Label>
                                    <Switch
                                        checked={!!area.surge_active}
                                        onCheckedChange={() => handleToggleSurge(area)}
                                        disabled={saving === area.id}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label className="text-sm">
                                        Multiplier:{" "}
                                        <span className="font-bold text-amber-500">
                                            {area.surge_multiplier || 1.0}x
                                        </span>
                                    </Label>
                                    <div className="flex items-center gap-2">
                                        <Input
                                            type="number"
                                            step="0.1"
                                            min="1"
                                            max="10"
                                            defaultValue={area.surge_multiplier || 1.0}
                                            onBlur={(e) =>
                                                handleUpdateMultiplier(area, parseFloat(e.target.value))
                                            }
                                            className="w-24"
                                            disabled={saving === area.id}
                                        />
                                        <div className="flex gap-1">
                                            {[1.5, 2.0, 2.5, 3.0].map((m) => (
                                                <Button
                                                    key={m}
                                                    variant="outline"
                                                    size="sm"
                                                    className="h-8 px-2 text-xs"
                                                    onClick={() => handleUpdateMultiplier(area, m)}
                                                    disabled={saving === area.id}
                                                >
                                                    {m}x
                                                </Button>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}
        </div>
    );
}
