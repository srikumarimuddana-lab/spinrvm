"use client";

import { useEffect, useState } from "react";
import { getServiceAreas } from "@/lib/api";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { MapPin } from "lucide-react";

export interface ServiceArea {
    id: string;
    name: string;
    city?: string;
}

export function useServiceAreas() {
    const [areas, setAreas] = useState<ServiceArea[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        getServiceAreas()
            .then((data) => setAreas((data || []).map((a: any) => ({ id: a.id, name: a.name || a.city || a.id, city: a.city }))))
            .catch(() => setAreas([]))
            .finally(() => setLoading(false));
    }, []);

    return { areas, loading };
}

export function ServiceAreaFilter({
    value,
    onChange,
    areas,
}: {
    value: string;
    onChange: (v: string) => void;
    areas: ServiceArea[];
}) {
    return (
        <Select value={value} onValueChange={onChange}>
            <SelectTrigger className="w-44 h-9">
                <div className="flex items-center gap-1.5">
                    <MapPin className="h-3.5 w-3.5 text-muted-foreground" />
                    <SelectValue placeholder="All Areas" />
                </div>
            </SelectTrigger>
            <SelectContent>
                <SelectItem value="all">All Service Areas</SelectItem>
                {areas.map((a) => (
                    <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}

export function ServiceAreaSelect({
    value,
    onChange,
    areas,
    label = "Service Area",
}: {
    value: string;
    onChange: (v: string) => void;
    areas: ServiceArea[];
    label?: string;
}) {
    return (
        <div className="space-y-1.5">
            <label className="text-xs font-medium">{label}</label>
            <Select value={value || "none"} onValueChange={(v) => onChange(v === "none" ? "" : v)}>
                <SelectTrigger>
                    <SelectValue placeholder="Select area..." />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value="none">No area selected</SelectItem>
                    {areas.map((a) => (
                        <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                    ))}
                </SelectContent>
            </Select>
        </div>
    );
}
