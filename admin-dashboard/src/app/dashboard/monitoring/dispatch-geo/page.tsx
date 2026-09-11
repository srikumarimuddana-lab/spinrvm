"use client";

import { useCallback, useEffect, useState } from "react";
import {
    AlertTriangle,
    CheckCircle2,
    CircleSlash,
    Clock,
    Compass,
    History,
    RefreshCw,
    ShieldAlert,
    Wrench,
} from "lucide-react";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/use-toast";

import {
    getDispatchGeoStatus,
    rebuildDispatchGeoIndex,
    type DispatchGeoStatus,
} from "@/lib/api";

// Same cadence as the Redis & Infra page's stats poll — this is a comparably
// cheap read (Redis GET calls + a resolved app_settings lookup), not the
// per-dispatch RPC path, so a 10s interval is safe here too.
const STATUS_POLL_MS = 10_000;

function formatTimestamp(iso: string | null | undefined): string {
    if (!iso) return "–";
    try {
        return new Date(iso).toLocaleString();
    } catch {
        return iso;
    }
}

function providerBadge(provider: string | null | undefined) {
    const label = provider ?? "–";
    if (provider === "legacy") {
        return (
            <Badge variant="secondary" className="gap-1">
                <CircleSlash className="h-3 w-3" /> {label}
            </Badge>
        );
    }
    if (provider === "h3") {
        return (
            <Badge variant="default" className="gap-1">
                <CheckCircle2 className="h-3 w-3" /> {label}
            </Badge>
        );
    }
    return (
        <Badge variant="outline" className="gap-1">
            <Compass className="h-3 w-3" /> {label}
        </Badge>
    );
}

export default function DispatchGeoMonitoringPage() {
    const { toast } = useToast();

    const [status, setStatus] = useState<DispatchGeoStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    const [refreshing, setRefreshing] = useState(false);
    const [rebuilding, setRebuilding] = useState(false);

    const fetchStatus = useCallback(async () => {
        try {
            const res = await getDispatchGeoStatus();
            setStatus(res);
            setErrorMsg(null);
        } catch (err: any) {
            setErrorMsg(err?.message ?? "Failed to load dispatch geo status");
        }
    }, []);

    useEffect(() => {
        fetchStatus().finally(() => setLoading(false));
    }, [fetchStatus]);

    useEffect(() => {
        const id = setInterval(fetchStatus, STATUS_POLL_MS);
        return () => clearInterval(id);
    }, [fetchStatus]);

    const handleManualRefresh = async () => {
        setRefreshing(true);
        try {
            await fetchStatus();
            toast({ title: "Refreshed", description: "Dispatch geo status updated" });
        } finally {
            setRefreshing(false);
        }
    };

    const handleRebuild = async () => {
        setRebuilding(true);
        try {
            const res = await rebuildDispatchGeoIndex();
            toast({
                title: res.skipped ? "Rebuild skipped" : "H3 index rebuilt",
                description: res.skipped
                    ? "Another replica currently holds the lock — safe to retry."
                    : `${res.driver_count ?? 0} drivers indexed${res.failed ? `, ${res.failed} failed` : ""}.`,
            });
            await fetchStatus();
        } catch (err: any) {
            toast({
                title: "Rebuild failed",
                description: err?.message ?? "Unknown error",
                variant: "destructive",
            });
        } finally {
            setRebuilding(false);
        }
    };

    if (loading) {
        return (
            <div className="flex h-full items-center justify-center text-muted-foreground">
                Loading dispatch geo status…
            </div>
        );
    }

    const mismatch =
        !!status && status.configured_provider !== status.effective_provider;

    return (
        <div className="flex flex-col gap-4 p-6">
            <PageHeader
                title="Dispatch Geo-Provider Status"
                description="Which candidate-lookup strategy (legacy box, PostGIS, H3) is configured vs. actually serving rides right now."
                actions={
                    <div className="flex gap-2">
                        <Button
                            variant="outline"
                            onClick={handleRebuild}
                            disabled={rebuilding}
                            className="gap-2"
                        >
                            <Wrench className={`h-4 w-4 ${rebuilding ? "animate-spin" : ""}`} />
                            Rebuild H3 Index
                        </Button>
                        <Button
                            variant="outline"
                            onClick={handleManualRefresh}
                            disabled={refreshing}
                            className="gap-2"
                        >
                            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
                            Refresh
                        </Button>
                    </div>
                }
            />

            {errorMsg && (
                <Card className="border-destructive/50">
                    <CardContent className="pt-6 flex items-center gap-2 text-destructive">
                        <AlertTriangle className="h-5 w-5" />
                        {errorMsg}
                    </CardContent>
                </Card>
            )}

            {status?.status_summary && (
                <Card className="border-warning/50">
                    <CardContent className="pt-6 flex items-center gap-2 text-warning">
                        <AlertTriangle className="h-5 w-5" />
                        {status.status_summary}
                    </CardContent>
                </Card>
            )}

            {/* Configured vs effective provider */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            Configured Provider
                        </CardTitle>
                        <Compass className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>{providerBadge(status?.configured_provider)}</CardContent>
                </Card>
                <Card className={mismatch ? "border-warning/50" : undefined}>
                    <CardHeader className="flex flex-row items-center justify-between pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            Effective Provider
                        </CardTitle>
                        {mismatch ? (
                            <ShieldAlert className="h-4 w-4 text-warning" />
                        ) : (
                            <CheckCircle2 className="h-4 w-4 text-muted-foreground" />
                        )}
                    </CardHeader>
                    <CardContent>
                        {providerBadge(status?.effective_provider)}
                        {mismatch && (
                            <p className="mt-2 text-xs text-warning">
                                Serving on a different provider than configured — a failover is in effect.
                            </p>
                        )}
                    </CardContent>
                </Card>
            </div>

            {/* H3 index readiness */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Compass className="h-5 w-5" />
                        H3 Index Readiness
                    </CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Whether the Redis H3 dispatch index is healthy enough to serve as the{" "}
                        <code>h3</code> provider. TTL {status?.index_ttl_seconds ?? "–"}s · requires{" "}
                        <code>{status?.required_eviction_policy ?? "–"}</code> eviction policy with{" "}
                        {status?.memory_headroom_percent ?? "–"}% headroom.
                    </p>
                </CardHeader>
                <CardContent className="space-y-3">
                    {status?.h3_would_serve ? (
                        <Badge variant="default" className="gap-1">
                            <CheckCircle2 className="h-3 w-3" /> Ready
                        </Badge>
                    ) : (
                        <Badge variant="secondary" className="gap-1">
                            <CircleSlash className="h-3 w-3" /> Not ready
                        </Badge>
                    )}
                    {(status?.blockers?.length ?? 0) > 0 && (
                        <ul className="list-inside list-disc text-xs text-muted-foreground">
                            {status!.blockers.map((b) => (
                                <li key={b}>{b}</li>
                            ))}
                        </ul>
                    )}
                </CardContent>
            </Card>

            {/* Last failover */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <ShieldAlert className="h-5 w-5" />
                        Last Failover
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {status?.last_failover ? (
                        <div className="flex flex-wrap items-center gap-2 text-sm">
                            <span className="font-medium">
                                {status.last_failover.from_provider} → {status.last_failover.to_provider}
                            </span>
                            <span className="text-muted-foreground">
                                {status.last_failover.reason}
                            </span>
                            <span className="flex items-center gap-1 text-xs text-muted-foreground">
                                <Clock className="h-3 w-3" /> {formatTimestamp(status.last_failover.at)}
                            </span>
                        </div>
                    ) : (
                        <p className="text-sm text-muted-foreground">No failovers recorded.</p>
                    )}
                </CardContent>
            </Card>

            {/* Recent events */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <History className="h-5 w-5" />
                        Recent Events
                    </CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Last {status?.events?.length ?? 0} dispatch geo-provider events (failovers,
                        recoveries, shadow-mode divergences).
                    </p>
                </CardHeader>
                <CardContent>
                    {(status?.events?.length ?? 0) === 0 ? (
                        <p className="py-4 text-center text-sm text-muted-foreground">
                            No events recorded.
                        </p>
                    ) : (
                        <div className="flex flex-col gap-2">
                            {status!.events.map((e, i) => (
                                <div
                                    key={`${e.kind}-${e.at}-${i}`}
                                    className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs"
                                >
                                    <span className="font-mono font-medium">{e.kind}</span>
                                    <span className="text-muted-foreground">{e.reason || "—"}</span>
                                    <span className="text-muted-foreground">{formatTimestamp(e.at)}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
