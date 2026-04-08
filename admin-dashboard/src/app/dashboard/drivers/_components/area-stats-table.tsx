"use client";

import { formatCurrency } from "@/lib/utils";
import { MapPin } from "lucide-react";

interface AreaStat {
    service_area_id: string;
    service_area_name: string;
    total: number;
    online: number;
    verified: number;
    unverified: number;
    total_rides: number;
    total_earnings: number;
}

export default function AreaStatsTable({ areaStats, loading, onAreaClick }: {
    areaStats: AreaStat[];
    loading: boolean;
    onAreaClick: (areaId: string) => void;
}) {
    if (loading) {
        return (
            <div className="bg-card border rounded-xl p-5">
                <div className="flex items-center gap-2.5 mb-4">
                    <div className="w-9 h-9 rounded-lg bg-muted animate-pulse" />
                    <div className="h-4 w-32 rounded bg-muted animate-pulse" />
                </div>
                <div className="space-y-2">
                    {[1, 2, 3].map(i => <div key={i} className="h-10 bg-muted rounded animate-pulse" />)}
                </div>
            </div>
        );
    }

    if (!areaStats.length) return null;

    return (
        <div className="bg-card border rounded-xl p-5">
            <div className="flex items-center gap-2.5 mb-4">
                <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
                    <MapPin className="h-4.5 w-4.5 text-primary" />
                </div>
                <div>
                    <h3 className="text-sm font-semibold">Drivers by Service Area</h3>
                    <p className="text-xs text-muted-foreground">Click an area to filter drivers</p>
                </div>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b">
                            <th className="text-left py-2 px-3 text-xs font-semibold text-muted-foreground">Service Area</th>
                            <th className="text-right py-2 px-3 text-xs font-semibold text-muted-foreground">Drivers</th>
                            <th className="text-right py-2 px-3 text-xs font-semibold text-muted-foreground">Online</th>
                            <th className="text-right py-2 px-3 text-xs font-semibold text-muted-foreground">Verified</th>
                            <th className="text-right py-2 px-3 text-xs font-semibold text-muted-foreground">Unverified</th>
                            <th className="text-right py-2 px-3 text-xs font-semibold text-muted-foreground">Rides</th>
                            <th className="text-right py-2 px-3 text-xs font-semibold text-muted-foreground">Earnings</th>
                        </tr>
                    </thead>
                    <tbody>
                        {areaStats.map(area => (
                            <tr key={area.service_area_id}
                                onClick={() => onAreaClick(area.service_area_id === "unassigned" ? "" : area.service_area_id)}
                                className="border-b last:border-0 hover:bg-muted/50 cursor-pointer transition-colors">
                                <td className="py-2.5 px-3 font-medium">{area.service_area_name}</td>
                                <td className="py-2.5 px-3 text-right font-semibold">{area.total}</td>
                                <td className="py-2.5 px-3 text-right">
                                    <span className="text-emerald-600 font-medium">{area.online}</span>
                                </td>
                                <td className="py-2.5 px-3 text-right">
                                    <span className="text-green-600 font-medium">{area.verified}</span>
                                </td>
                                <td className="py-2.5 px-3 text-right">
                                    <span className="text-amber-600 font-medium">{area.unverified}</span>
                                </td>
                                <td className="py-2.5 px-3 text-right">{area.total_rides.toLocaleString()}</td>
                                <td className="py-2.5 px-3 text-right text-emerald-600 font-medium">{formatCurrency(area.total_earnings)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
