// src/app/dashboard/monitoring/toolbar.tsx
"use client";

import { MonitoringCounts, MonitoringFilters } from "./types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Navigation, Search, Wifi, WifiOff } from "lucide-react";

interface ServiceArea { id: string; name: string; }
interface VehicleType { id: string; name: string; }

interface ToolbarProps {
    counts: MonitoringCounts;
    filters: MonitoringFilters;
    onFilterChange: (f: Partial<MonitoringFilters>) => void;
    searchQuery: string;
    onSearchChange: (q: string) => void;
    followMode: boolean;
    onFollowToggle: () => void;
    serviceAreas: ServiceArea[];
    vehicleTypes: VehicleType[];
    wsStatus: "connecting" | "connected" | "disconnected" | "error";
}

export function MonitoringToolbar({
    counts,
    filters,
    onFilterChange,
    searchQuery,
    onSearchChange,
    followMode,
    onFollowToggle,
    serviceAreas,
    vehicleTypes,
    wsStatus,
}: ToolbarProps) {
    return (
        <div className="flex flex-wrap items-center gap-3 border-b border-border bg-background px-4 py-2">
            {/* Live counters */}
            <div className="flex items-center gap-2 text-sm">
                <button
                    onClick={() => onFilterChange({ showOnline: !filters.showOnline })}
                    className={`flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                        filters.showOnline
                            ? "bg-green-500/10 text-green-600 ring-1 ring-green-500/30"
                            : "text-muted-foreground hover:bg-muted"
                    }`}
                >
                    <span className="h-2 w-2 rounded-full bg-green-500" />
                    {counts.online} Online
                </button>
                <button
                    onClick={() => onFilterChange({ showOnline: !filters.showOnline })}
                    className="flex items-center gap-1 rounded bg-amber-500/10 px-2 py-1 text-amber-600 ring-1 ring-amber-500/30"
                >
                    <span className="h-2 w-2 rounded-full bg-amber-500" />
                    {counts.onRide} On Ride
                </button>
                <button
                    onClick={() => onFilterChange({ showOffline: !filters.showOffline })}
                    className={`flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                        filters.showOffline
                            ? "bg-muted text-foreground ring-1 ring-border"
                            : "text-muted-foreground hover:bg-muted"
                    }`}
                >
                    <span className="h-2 w-2 rounded-full bg-gray-400" />
                    {counts.offline} Offline
                </button>
                <button
                    onClick={() => onFilterChange({ showRides: !filters.showRides })}
                    className={`flex items-center gap-1 rounded px-2 py-1 transition-colors ${
                        filters.showRides
                            ? "bg-blue-500/10 text-blue-600 ring-1 ring-blue-500/30"
                            : "text-muted-foreground hover:bg-muted"
                    }`}
                >
                    🚗 {counts.activeRides} Rides
                </button>
            </div>

            <div className="mx-1 h-5 w-px bg-border" />

            {/* Filters */}
            <Select
                value={filters.serviceAreaId ?? "all"}
                onValueChange={(v) =>
                    onFilterChange({ serviceAreaId: v === "all" ? null : v })
                }
            >
                <SelectTrigger className="h-8 w-36 text-xs">
                    <SelectValue placeholder="All Areas" />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value="all">All Areas</SelectItem>
                    {serviceAreas.map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                            {a.name}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>

            <Select
                value={filters.vehicleTypeId ?? "all"}
                onValueChange={(v) =>
                    onFilterChange({ vehicleTypeId: v === "all" ? null : v })
                }
            >
                <SelectTrigger className="h-8 w-36 text-xs">
                    <SelectValue placeholder="All Vehicles" />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value="all">All Vehicles</SelectItem>
                    {vehicleTypes.map((v) => (
                        <SelectItem key={v.id} value={v.id}>
                            {v.name}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>

            <div className="mx-1 h-5 w-px bg-border" />

            {/* Search */}
            <div className="relative">
                <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                    value={searchQuery}
                    onChange={(e) => onSearchChange(e.target.value)}
                    placeholder="Driver name or ride ID"
                    className="h-8 w-48 pl-7 text-xs"
                />
            </div>

            <div className="ml-auto flex items-center gap-2">
                {/* WS status indicator */}
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    {wsStatus === "connected" ? (
                        <Wifi className="h-3.5 w-3.5 text-green-500" />
                    ) : (
                        <WifiOff className="h-3.5 w-3.5 text-destructive" />
                    )}
                    {wsStatus === "connected" ? "Live" : wsStatus}
                </span>

                {/* Follow mode */}
                <Button
                    size="sm"
                    variant={followMode ? "default" : "outline"}
                    onClick={onFollowToggle}
                    className="h-8 gap-1.5 text-xs"
                >
                    <Navigation className="h-3.5 w-3.5" />
                    Follow {followMode ? "ON" : "OFF"}
                </Button>
            </div>
        </div>
    );
}
