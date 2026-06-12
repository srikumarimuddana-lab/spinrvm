"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    RefreshCw,
    FileWarning,
    FileCheck,
    ChevronRight,
    Filter,
    Inbox,
    Camera,
} from "lucide-react";
import {
    getApprovalQueue,
    getServiceAreas,
    type ApprovalQueueItem,
    type ApprovalQueueResponse,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { QueueStats } from "./_components/queue-stats";
import { DocumentReviewer } from "../_components/document-reviewer";
import { PhotoReviewer } from "../_components/photo-reviewer";

const formatTimeInQueue = (seconds: number) => {
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    const remM = m % 60;
    if (h < 24) return remM ? `${h}h ${remM}m` : `${h}h`;
    const d = Math.floor(h / 24);
    const remH = h % 24;
    return remH ? `${d}d ${remH}h` : `${d}d`;
};

const slaTone = (seconds: number) => {
    if (seconds >= 86400) return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300 border-red-200 dark:border-red-800";
    if (seconds >= 14400) return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 border-amber-200 dark:border-amber-800";
    return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800";
};

const initials = (name: string) => {
    const parts = name.trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    return (parts[0][0] + (parts[1]?.[0] || "")).toUpperCase();
};

export default function ApprovalQueuePage() {
    const { allowed: moduleAllowed } = useRequireModule("drivers");
    const { toast } = useToast();
    const [resp, setResp] = useState<ApprovalQueueResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [serviceAreaId, setServiceAreaId] = useState<string>("all");
    const [serviceAreas, setServiceAreas] = useState<Array<{ id: string; name?: string }>>([]);
    const [refreshing, setRefreshing] = useState(false);
    const [reviewerDriver, setReviewerDriver] = useState<{ id: string; name: string } | null>(null);
    const [photoReviewDriver, setPhotoReviewDriver] = useState<{ id: string; name: string; photoUrl: string | null } | null>(null);

    const load = useCallback(async () => {
        try {
            setRefreshing(true);
            const data = await getApprovalQueue({
                limit: 200,
                service_area_id: serviceAreaId !== "all" ? serviceAreaId : undefined,
            });
            setResp(data);
        } catch (e) {
            toast({ title: "Failed to load queue", description: String((e as Error).message || e), variant: "destructive" });
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [serviceAreaId, toast]);

    useEffect(() => {
        if (!moduleAllowed) return;
        load();
    }, [moduleAllowed, load]);

    useEffect(() => {
        getServiceAreas().then((rows) => setServiceAreas(rows || [])).catch(() => {});
    }, []);

    const items: ApprovalQueueItem[] = useMemo(() => resp?.items || [], [resp]);
    const stats = resp?.stats || { total_pending: 0, oldest_in_queue_hours: 0, median_wait_hours: 0, over_24h_count: 0 };

    if (!moduleAllowed) return null;
    if (loading) return null; // loading.tsx handles initial state

    return (
        <div className="space-y-4">
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                    <h1 className="text-xl font-bold tracking-tight">Approval Queue</h1>
                    <p className="text-sm text-muted-foreground mt-0.5">
                        Oldest-first applicants and re-uploads waiting on a reviewer.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={load} disabled={refreshing}>
                        <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            <QueueStats stats={stats} />

            <div className="flex items-center gap-2 flex-wrap">
                <Filter className="h-4 w-4 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">Service area</span>
                <Select value={serviceAreaId} onValueChange={setServiceAreaId}>
                    <SelectTrigger className="h-8 w-48 text-xs">
                        <SelectValue placeholder="All areas" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All areas</SelectItem>
                        {serviceAreas.map((a) => (
                            <SelectItem key={a.id} value={a.id}>{a.name || a.id.slice(0, 8)}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>

            <div className="rounded-xl border border-border overflow-hidden bg-card">
                <div className="grid grid-cols-[1fr_120px_110px_110px_150px_120px_160px] gap-4 px-4 py-3 bg-muted/30 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    <div>Applicant</div>
                    <div>Status</div>
                    <div>Pending</div>
                    <div>Missing</div>
                    <div>Time in queue</div>
                    <div>Service area</div>
                    <div className="text-right">Actions</div>
                </div>

                {items.length === 0 ? (
                    <div className="px-6 py-16 text-center text-muted-foreground">
                        <Inbox className="h-10 w-10 mx-auto mb-3 opacity-30" />
                        <p className="text-sm font-medium">Queue is empty</p>
                        <p className="text-xs mt-1">No drivers waiting on approval right now.</p>
                    </div>
                ) : (
                    items.map((it) => (
                        <div
                            key={it.driver_id}
                            className="grid grid-cols-[1fr_120px_110px_110px_150px_120px_160px] gap-4 items-center px-4 py-3 border-t border-border hover:bg-muted/20 transition-colors"
                        >
                            <div className="flex items-center gap-3 min-w-0">
                                {it.profile_photo_url ? (
                                    // eslint-disable-next-line @next/next/no-img-element
                                    <img src={it.profile_photo_url} alt="" className="h-9 w-9 rounded-full object-cover shrink-0" />
                                ) : (
                                    <div className="h-9 w-9 rounded-full bg-muted flex items-center justify-center text-xs font-semibold text-muted-foreground shrink-0">
                                        {initials(it.name || it.email || "?")}
                                    </div>
                                )}
                                <div className="min-w-0">
                                    <p className="text-sm font-medium truncate">{it.name || "(no name)"}</p>
                                    <p className="text-xs text-muted-foreground truncate">{it.email || it.phone || it.driver_id.slice(0, 8)}</p>
                                </div>
                            </div>
                            <div>
                                <Badge
                                    variant="outline"
                                    className={
                                        it.status === "pending"
                                            ? "bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300 border-blue-200 dark:border-blue-800"
                                            : "bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300 border-amber-200 dark:border-amber-800"
                                    }
                                >
                                    {it.status}
                                </Badge>
                            </div>
                            <div className="text-sm tabular-nums">
                                {it.pending_docs_count > 0 ? (
                                    <span className="inline-flex items-center gap-1 font-medium">
                                        <FileWarning className="h-3.5 w-3.5 text-amber-600" />
                                        {it.pending_docs_count}
                                    </span>
                                ) : (
                                    <span className="text-muted-foreground">0</span>
                                )}
                            </div>
                            <div className="text-sm tabular-nums">
                                {it.missing_docs_count > 0 ? (
                                    <span className="inline-flex items-center gap-1 font-medium text-red-600 dark:text-red-400">
                                        <FileWarning className="h-3.5 w-3.5" />
                                        {it.missing_docs_count}
                                    </span>
                                ) : (
                                    <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                                        <FileCheck className="h-3.5 w-3.5" />
                                        0
                                    </span>
                                )}
                            </div>
                            <div>
                                <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border ${slaTone(it.time_in_queue_seconds)}`}>
                                    {formatTimeInQueue(it.time_in_queue_seconds)}
                                </span>
                            </div>
                            <div className="text-xs text-muted-foreground truncate">
                                {it.service_area_name || "—"}
                            </div>
                            <div className="flex items-center justify-end gap-1.5">
                                {it.has_pending_photo && (
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        className="h-8 border-amber-300 text-amber-700 hover:bg-amber-50 dark:border-amber-700 dark:text-amber-300 dark:hover:bg-amber-900/20"
                                        onClick={() => setPhotoReviewDriver({
                                            id: it.driver_id,
                                            name: it.name || it.email || it.driver_id,
                                            photoUrl: it.profile_photo_url,
                                        })}
                                    >
                                        <Camera className="h-3.5 w-3.5 mr-1" />
                                        Photo
                                    </Button>
                                )}
                                <Button
                                    size="sm"
                                    variant="default"
                                    className="h-8"
                                    onClick={() => setReviewerDriver({ id: it.driver_id, name: it.name || it.email || it.driver_id })}
                                >
                                    Docs
                                    <ChevronRight className="h-3.5 w-3.5 ml-0.5" />
                                </Button>
                            </div>
                        </div>
                    ))
                )}
            </div>

            <DocumentReviewer
                open={!!reviewerDriver}
                driverId={reviewerDriver?.id || null}
                driverName={reviewerDriver?.name}
                onClose={() => setReviewerDriver(null)}
                onAfterAction={load}
            />

            <PhotoReviewer
                open={!!photoReviewDriver}
                driverId={photoReviewDriver?.id || null}
                driverName={photoReviewDriver?.name}
                photoUrl={photoReviewDriver?.photoUrl}
                onClose={() => setPhotoReviewDriver(null)}
                onAfterAction={load}
            />
        </div>
    );
}
