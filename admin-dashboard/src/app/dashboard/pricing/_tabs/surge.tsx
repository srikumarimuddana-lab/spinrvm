"use client";

import { useEffect, useState } from "react";
import { getServiceAreas, updateServiceArea, resetSurgeToAuto, getSurgeStatus } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { TrendingUp, Zap, AlertTriangle, Bot, Hand, RotateCcw } from "lucide-react";

export default function SurgeTab() {
    const [areas, setAreas] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState<string | null>(null);

    const fetchAreas = () => {
        setLoading(true);
        Promise.all([
            getServiceAreas().catch(() => []),
            getSurgeStatus().catch(() => []),
        ]).then(([areaList, surgeStatuses]) => {
            // Merge demand/supply data from surge status into areas
            const statusMap = new Map((surgeStatuses || []).map((s: any) => [s.area_id, s]));
            const merged = (areaList || []).map((a: any) => {
                const status = statusMap.get(a.id);
                if (status) {
                    return { ...a, demand_count: status.demand_count, supply_count: status.supply_count, ratio: status.ratio };
                }
                return a;
            });
            setAreas(merged);
        }).finally(() => setLoading(false));
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
                    a.id === area.id
                        ? { ...a, surge_active: !a.surge_active, surge_source: "manual" }
                        : a
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
                    a.id === area.id
                        ? { ...a, surge_multiplier: multiplier, surge_source: "manual" }
                        : a
                )
            );
        } catch {
        } finally {
            setSaving(null);
        }
    };

    const handleResetToAuto = async (area: any) => {
        setSaving(area.id);
        try {
            const updated = await resetSurgeToAuto(area.id);
            setAreas((prev) =>
                prev.map((a) =>
                    a.id === area.id
                        ? { ...a, ...updated, surge_source: "auto" }
                        : a
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
                    Surge pricing is managed automatically based on demand/supply ratio.
                    You can manually override any area, or let the engine adjust automatically.
                </p>
            </div>

            <Card className="border-amber-500/30 bg-amber-500/5">
                <CardContent className="flex items-start gap-3 pt-6">
                    <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
                    <div>
                        <p className="text-sm font-medium">Hybrid Surge Model</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                            The surge engine recalculates every 2 minutes based on ride demand
                            vs. available drivers per area. Areas marked <strong>AUTO</strong> are
                            managed by the engine. Set a manual multiplier to override — the area
                            switches to <strong>MANUAL</strong> and the engine skips it. Use
                            &quot;Reset to Auto&quot; to return control to the engine.
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
                    {areas.map((area) => {
                        const isManual = area.surge_source === "manual";
                        return (
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
                                    <div className="flex items-center gap-2">
                                        {/* Source badge: Auto or Manual */}
                                        <Badge
                                            variant="outline"
                                            className={
                                                isManual
                                                    ? "border-orange-500/40 text-orange-600 dark:text-orange-400"
                                                    : "border-blue-500/40 text-blue-600 dark:text-blue-400"
                                            }
                                        >
                                            {isManual ? (
                                                <span className="flex items-center gap-1">
                                                    <Hand className="h-3 w-3" /> Manual
                                                </span>
                                            ) : (
                                                <span className="flex items-center gap-1">
                                                    <Bot className="h-3 w-3" /> Auto
                                                </span>
                                            )}
                                        </Badge>
                                        {/* Active/Inactive badge */}
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
                                    </div>
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
                                    {/* Live demand/supply counts */}
                                    {(area.demand_count !== undefined || area.supply_count !== undefined) && (
                                        <div className="flex items-center justify-between py-2 px-3 bg-muted/50 rounded-lg text-xs">
                                            <div className="flex items-center gap-3">
                                                <span className="flex items-center gap-1">
                                                    <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                                                    Demand: <strong>{area.demand_count ?? 0}</strong> riders
                                                </span>
                                                <span className="flex items-center gap-1">
                                                    <span className="w-2 h-2 rounded-full bg-green-500" />
                                                    Supply: <strong>{area.supply_count ?? 0}</strong> drivers
                                                </span>
                                            </div>
                                            {area.ratio !== undefined && (
                                                <span className="text-muted-foreground">
                                                    Ratio: <strong>{area.ratio}</strong>
                                                </span>
                                            )}
                                        </div>
                                    )}

                                    {/* Reset to Auto button (only shown for manual areas) */}
                                    {isManual && (
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            className="w-full text-blue-600 hover:text-blue-700 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-950"
                                            onClick={() => handleResetToAuto(area)}
                                            disabled={saving === area.id}
                                        >
                                            <RotateCcw className="mr-2 h-3.5 w-3.5" />
                                            Reset to Auto
                                        </Button>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
