"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
    AlertTriangle,
    CheckCircle2,
    Clock,
    Eye,
    Loader2,
    Play,
    RefreshCw,
    XCircle,
    Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { useAuthStore } from "@/store/authStore";

import {
    getStuckStripeEvents,
    getStripeEventDetail,
    replayStripeEvent,
    dismissStripeEvent,
    type StuckStripeEvent,
    type StripeEventDetail,
} from "@/lib/api/stripe-events";

function formatAge(minutes: number | null): string {
    if (minutes === null) return "Unknown";
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ${minutes % 60}m`;
    const days = Math.floor(hours / 24);
    return `${days}d ${hours % 24}h`;
}

function ageSeverity(minutes: number | null): "destructive" | "secondary" | "outline" {
    if (minutes === null) return "outline";
    if (minutes > 60) return "destructive";
    if (minutes > 15) return "secondary";
    return "outline";
}

function eventTypeBadge(eventType: string | null): string {
    if (!eventType) return "bg-muted text-muted-foreground";
    if (eventType.includes("succeeded") || eventType.includes("paid"))
        return "bg-success/15 text-success";
    if (eventType.includes("failed") || eventType.includes("dispute"))
        return "bg-destructive/15 text-destructive";
    if (eventType.includes("refund"))
        return "bg-warning/15 text-warning";
    return "bg-info/15 text-info";
}

export default function StripeEventsPage() {
    const { user } = useAuthStore();
    const { toast } = useToast();

    const [events, setEvents] = useState<StuckStripeEvent[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Detail dialog
    const [detailOpen, setDetailOpen] = useState(false);
    const [detail, setDetail] = useState<StripeEventDetail | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);

    // Replay confirmation
    const [replayTarget, setReplayTarget] = useState<string | null>(null);
    const [replaying, setReplaying] = useState(false);

    // Dismiss dialog
    const [dismissTarget, setDismissTarget] = useState<string | null>(null);
    const [dismissReason, setDismissReason] = useState("");
    const [dismissing, setDismissing] = useState(false);

    // Auto-refresh
    const [autoRefresh, setAutoRefresh] = useState(true);
    const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const fetchEvents = useCallback(async () => {
        try {
            setError(null);
            const data = await getStuckStripeEvents(50, 0);
            setEvents(data.items);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : "Failed to load events";
            setError(msg);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchEvents();
    }, [fetchEvents]);

    useEffect(() => {
        if (autoRefresh) {
            intervalRef.current = setInterval(fetchEvents, 30_000);
        }
        return () => {
            if (intervalRef.current) clearInterval(intervalRef.current);
        };
    }, [autoRefresh, fetchEvents]);

    const handleViewDetail = async (eventId: string) => {
        setDetailLoading(true);
        setDetailOpen(true);
        try {
            const data = await getStripeEventDetail(eventId);
            setDetail(data);
        } catch (err: unknown) {
            toast({
                title: "Error",
                description: err instanceof Error ? err.message : "Failed to load detail",
                variant: "destructive",
            });
            setDetailOpen(false);
        } finally {
            setDetailLoading(false);
        }
    };

    const handleReplay = async () => {
        if (!replayTarget) return;
        setReplaying(true);
        try {
            await replayStripeEvent(replayTarget);
            toast({ title: "Event replayed", description: `${replayTarget} has been re-processed.` });
            setReplayTarget(null);
            fetchEvents();
        } catch (err: unknown) {
            toast({
                title: "Replay failed",
                description: err instanceof Error ? err.message : "Unknown error",
                variant: "destructive",
            });
        } finally {
            setReplaying(false);
        }
    };

    const handleDismiss = async () => {
        if (!dismissTarget || !dismissReason.trim()) return;
        setDismissing(true);
        try {
            await dismissStripeEvent(dismissTarget, dismissReason.trim());
            toast({ title: "Event dismissed", description: `${dismissTarget} marked as processed.` });
            setDismissTarget(null);
            setDismissReason("");
            fetchEvents();
        } catch (err: unknown) {
            toast({
                title: "Dismiss failed",
                description: err instanceof Error ? err.message : "Unknown error",
                variant: "destructive",
            });
        } finally {
            setDismissing(false);
        }
    };

    if (user?.role !== "super_admin") {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <p className="text-muted-foreground">Super admin access required.</p>
            </div>
        );
    }

    return (
        <div className="space-y-6 p-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Zap className="h-6 w-6" />
                        Stripe Webhook Events
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Events stuck at processed_at=NULL that need manual resolution
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setAutoRefresh(!autoRefresh)}
                    >
                        {autoRefresh ? "Pause Auto-refresh" : "Resume Auto-refresh"}
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => { setLoading(true); fetchEvents(); }}
                    >
                        <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            {error && (
                <Card className="border-destructive">
                    <CardContent className="pt-6">
                        <p className="text-destructive flex items-center gap-2">
                            <AlertTriangle className="h-4 w-4" />
                            {error}
                        </p>
                    </CardContent>
                </Card>
            )}

            {/* Summary card */}
            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-lg flex items-center gap-2">
                        <AlertTriangle className="h-5 w-5 text-warning" />
                        Stuck Events
                        {events.length > 0 && (
                            <Badge variant="destructive" className="ml-2">
                                {events.length}
                            </Badge>
                        )}
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex items-center justify-center py-12">
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        </div>
                    ) : events.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-12 text-center">
                            <CheckCircle2 className="h-10 w-10 text-success mb-3" />
                            <p className="text-lg font-medium">All clear</p>
                            <p className="text-muted-foreground text-sm mt-1">
                                No stuck Stripe events found.
                            </p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b text-left">
                                        <th className="pb-3 font-medium">Event ID</th>
                                        <th className="pb-3 font-medium">Type</th>
                                        <th className="pb-3 font-medium">Received</th>
                                        <th className="pb-3 font-medium">Age</th>
                                        <th className="pb-3 font-medium text-right">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {events.map((evt) => (
                                        <tr key={evt.event_id} className="border-b last:border-0 hover:bg-muted/50">
                                            <td className="py-3 font-mono text-xs">
                                                {evt.event_id.length > 30
                                                    ? `${evt.event_id.slice(0, 15)}...${evt.event_id.slice(-10)}`
                                                    : evt.event_id}
                                            </td>
                                            <td className="py-3">
                                                <Badge className={eventTypeBadge(evt.event_type)} variant="secondary">
                                                    {evt.event_type || "unknown"}
                                                </Badge>
                                            </td>
                                            <td className="py-3 text-muted-foreground text-xs">
                                                {evt.received_at
                                                    ? new Date(evt.received_at).toLocaleString()
                                                    : "Unknown"}
                                            </td>
                                            <td className="py-3">
                                                <Badge variant={ageSeverity(evt.age_minutes)}>
                                                    <Clock className="h-3 w-3 mr-1" />
                                                    {formatAge(evt.age_minutes)}
                                                </Badge>
                                            </td>
                                            <td className="py-3 text-right">
                                                <div className="flex items-center justify-end gap-1">
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleViewDetail(evt.event_id)}
                                                    >
                                                        <Eye className="h-4 w-4" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="text-success hover:text-success/80"
                                                        onClick={() => setReplayTarget(evt.event_id)}
                                                    >
                                                        <Play className="h-4 w-4" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="text-muted-foreground hover:text-foreground"
                                                        onClick={() => {
                                                            setDismissTarget(evt.event_id);
                                                            setDismissReason("");
                                                        }}
                                                    >
                                                        <XCircle className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Detail dialog */}
            <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
                <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Event Detail</DialogTitle>
                        <DialogDescription>
                            Full payload of the stuck Stripe event.
                        </DialogDescription>
                    </DialogHeader>
                    {detailLoading ? (
                        <div className="flex items-center justify-center py-8">
                            <Loader2 className="h-6 w-6 animate-spin" />
                        </div>
                    ) : detail ? (
                        <div className="space-y-4">
                            <div className="grid grid-cols-2 gap-4 text-sm">
                                <div>
                                    <span className="text-muted-foreground">Event ID</span>
                                    <p className="font-mono text-xs break-all">{detail.event_id}</p>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Type</span>
                                    <p>{detail.event_type}</p>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Received</span>
                                    <p>{detail.received_at ? new Date(detail.received_at).toLocaleString() : "Unknown"}</p>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Age</span>
                                    <p>{formatAge(detail.age_minutes)}</p>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Status</span>
                                    <p>
                                        <Badge variant={detail.is_stuck ? "destructive" : "secondary"}>
                                            {detail.is_stuck ? "Stuck" : "Processed"}
                                        </Badge>
                                    </p>
                                </div>
                            </div>
                            {detail.payload && (
                                <div>
                                    <span className="text-sm text-muted-foreground">Payload</span>
                                    <pre className="mt-1 p-3 bg-muted rounded-md text-xs overflow-x-auto max-h-[300px]">
                                        {JSON.stringify(detail.payload, null, 2)}
                                    </pre>
                                </div>
                            )}
                            {detail.is_stuck && (
                                <div className="flex gap-2 pt-2">
                                    <Button
                                        size="sm"
                                        onClick={() => {
                                            setDetailOpen(false);
                                            setReplayTarget(detail.event_id);
                                        }}
                                    >
                                        <Play className="h-4 w-4 mr-1" />
                                        Replay
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => {
                                            setDetailOpen(false);
                                            setDismissTarget(detail.event_id);
                                            setDismissReason("");
                                        }}
                                    >
                                        <XCircle className="h-4 w-4 mr-1" />
                                        Dismiss
                                    </Button>
                                </div>
                            )}
                        </div>
                    ) : null}
                </DialogContent>
            </Dialog>

            {/* Replay confirmation */}
            <AlertDialog open={!!replayTarget} onOpenChange={(open) => { if (!open) setReplayTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Replay Stripe Event</AlertDialogTitle>
                        <AlertDialogDescription>
                            This will re-process the event through the full webhook pipeline.
                            The event will be unclaimed and re-dispatched. Side effects
                            (wallet credits, ride updates, subscription changes) will run again.
                            Idempotent handlers will not double-process.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <p className="font-mono text-xs break-all text-muted-foreground">
                        {replayTarget}
                    </p>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={replaying}>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={handleReplay} disabled={replaying}>
                            {replaying ? (
                                <Loader2 className="h-4 w-4 animate-spin mr-1" />
                            ) : (
                                <Play className="h-4 w-4 mr-1" />
                            )}
                            Confirm Replay
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            {/* Dismiss dialog */}
            <Dialog open={!!dismissTarget} onOpenChange={(open) => { if (!open) setDismissTarget(null); }}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Dismiss Event</DialogTitle>
                        <DialogDescription>
                            Mark this event as processed without replaying. Provide a reason
                            for the audit trail.
                        </DialogDescription>
                    </DialogHeader>
                    <p className="font-mono text-xs break-all text-muted-foreground">
                        {dismissTarget}
                    </p>
                    <div className="space-y-2">
                        <Label htmlFor="dismiss-reason">Reason</Label>
                        <Input
                            id="dismiss-reason"
                            placeholder="e.g. Manually verified payment in Stripe dashboard"
                            value={dismissReason}
                            onChange={(e) => setDismissReason(e.target.value)}
                        />
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDismissTarget(null)} disabled={dismissing}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleDismiss}
                            disabled={dismissing || !dismissReason.trim()}
                        >
                            {dismissing ? (
                                <Loader2 className="h-4 w-4 animate-spin mr-1" />
                            ) : (
                                <XCircle className="h-4 w-4 mr-1" />
                            )}
                            Dismiss
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
