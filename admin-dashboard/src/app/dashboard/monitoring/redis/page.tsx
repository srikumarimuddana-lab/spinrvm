"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    Activity,
    AlertTriangle,
    CheckCircle2,
    CircleSlash,
    Database,
    Gauge,
    HardDrive,
    Radio,
    RefreshCw,
    Server,
    ShieldAlert,
    Trash2,
    WifiOff,
    Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
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
import { useToast } from "@/components/ui/use-toast";

import {
    flushRedisPrefix,
    getInfrastructureStats,
    getRedisConnectivity,
    getRedisHealth,
    getWebsocketHealth,
    type InfrastructureStats,
    type RedisConnectivityProbe,
    type RedisHealthResponse,
    type RedisPrefixCount,
    type WebsocketHealth,
} from "@/lib/api";

// Poll interval for the lightweight (O(1)) stats call. The SCAN-based
// prefix counts are NOT on this loop — only refreshed on page load and
// manual refresh to avoid SCAN spam against Redis.
const STATS_POLL_MS = 10_000;
// Infrastructure stats are cheap too; same cadence is fine.
const INFRA_POLL_MS = 10_000;

function formatDuration(seconds: number | null | undefined): string {
    if (seconds == null) return "–";
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    if (seconds < 86400) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return `${h}h ${m}m`;
    }
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    return `${d}d ${h}h`;
}

function formatNumber(n: number | null | undefined): string {
    if (n == null) return "–";
    if (n < 1000) return String(n);
    if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
    if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
    return `${(n / 1_000_000_000).toFixed(2)}B`;
}

function memoryColor(percent: number | null | undefined): string {
    if (percent == null) return "bg-muted";
    if (percent >= 85) return "bg-destructive";
    if (percent >= 60) return "bg-warning";
    return "bg-success";
}

function connectivityBadge(status: "ok" | "degraded" | "error" | "unset") {
    if (status === "ok")
        return (
            <Badge variant="default" className="gap-1">
                <CheckCircle2 className="h-3 w-3" /> OK
            </Badge>
        );
    if (status === "degraded")
        return (
            <Badge variant="secondary" className="gap-1">
                <AlertTriangle className="h-3 w-3" /> Pub/sub broken
            </Badge>
        );
    if (status === "error")
        return (
            <Badge variant="destructive" className="gap-1">
                <ShieldAlert className="h-3 w-3" /> Error
            </Badge>
        );
    return (
        <Badge variant="secondary" className="gap-1">
            <CircleSlash className="h-3 w-3" /> Unset
        </Badge>
    );
}

function circuitBadgeVariant(
    state: "closed" | "open" | "half_open",
): { variant: "default" | "destructive" | "secondary"; label: string; icon: React.ElementType } {
    if (state === "open") return { variant: "destructive", label: "OPEN — Supabase unreachable", icon: ShieldAlert };
    if (state === "half_open") return { variant: "secondary", label: "HALF-OPEN — probing recovery", icon: Activity };
    return { variant: "default", label: "CLOSED — healthy", icon: CheckCircle2 };
}

export default function RedisMonitoringPage() {
    const { toast } = useToast();

    const [redis, setRedis] = useState<RedisHealthResponse | null>(null);
    const [infra, setInfra] = useState<InfrastructureStats | null>(null);
    const [ws, setWs] = useState<WebsocketHealth | null>(null);
    const [connectivity, setConnectivity] = useState<RedisConnectivityProbe[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [scanRefreshing, setScanRefreshing] = useState(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    const [flushCandidate, setFlushCandidate] = useState<RedisPrefixCount | null>(null);
    const [flushing, setFlushing] = useState(false);

    const fetchRedis = useCallback(async () => {
        try {
            const res = await getRedisHealth();
            setRedis(res);
            setErrorMsg(null);
        } catch (err: any) {
            setErrorMsg(err?.message ?? "Failed to load Redis stats");
        }
    }, []);

    const fetchInfra = useCallback(async () => {
        try {
            const res = await getInfrastructureStats();
            setInfra(res);
        } catch (err: any) {
            // non-fatal — leave previous state
            console.error("[monitoring] infra fetch failed", err);
        }
    }, []);

    const fetchWs = useCallback(async () => {
        try {
            const res = await getWebsocketHealth();
            setWs(res);
        } catch (err: any) {
            // non-fatal — leave previous state
            console.error("[monitoring] ws health fetch failed", err);
        }
    }, []);

    // Expensive (PING + pub/sub round-trip per URL) — load + manual refresh
    // only, never on the poll loop, to avoid burning Redis/Upstash quota.
    const fetchConnectivity = useCallback(async () => {
        try {
            const res = await getRedisConnectivity();
            setConnectivity(res.connectivity);
        } catch (err: any) {
            console.error("[monitoring] connectivity probe failed", err);
        }
    }, []);

    // Initial load
    useEffect(() => {
        Promise.all([fetchRedis(), fetchInfra(), fetchWs(), fetchConnectivity()]).finally(() =>
            setLoading(false),
        );
    }, [fetchRedis, fetchInfra, fetchWs, fetchConnectivity]);

    // Stats polling (O(1) — safe on an interval)
    useEffect(() => {
        const id = setInterval(fetchRedis, STATS_POLL_MS);
        return () => clearInterval(id);
    }, [fetchRedis]);

    useEffect(() => {
        const id = setInterval(fetchInfra, INFRA_POLL_MS);
        return () => clearInterval(id);
    }, [fetchInfra]);

    useEffect(() => {
        const id = setInterval(fetchWs, INFRA_POLL_MS);
        return () => clearInterval(id);
    }, [fetchWs]);

    const handleManualRefresh = async () => {
        setScanRefreshing(true);
        try {
            await Promise.all([fetchRedis(), fetchInfra(), fetchWs(), fetchConnectivity()]);
            toast({ title: "Refreshed", description: "Redis + infra stats updated" });
        } finally {
            setScanRefreshing(false);
        }
    };

    const handleFlush = async () => {
        if (!flushCandidate) return;
        setFlushing(true);
        try {
            const res = await flushRedisPrefix(flushCandidate.prefix);
            toast({
                title: "Prefix flushed",
                description: `Deleted ${res.deleted_keys} keys under ${flushCandidate.prefix}`,
            });
            setFlushCandidate(null);
            await fetchRedis();
        } catch (err: any) {
            toast({
                title: "Flush failed",
                description: err?.message ?? "Unknown error",
                variant: "destructive",
            });
        } finally {
            setFlushing(false);
        }
    };

    const stats = redis?.stats;
    const prefixCounts = redis?.prefix_counts ?? [];

    const hitRateLabel = useMemo(() => {
        if (stats?.hit_rate_percent == null) return "–";
        return `${stats.hit_rate_percent.toFixed(1)}%`;
    }, [stats]);

    const memoryPercent = stats?.used_memory_percent ?? null;
    const circuitInfo = circuitBadgeVariant(infra?.db_circuit_breaker?.state ?? "closed");
    const CircuitIcon = circuitInfo.icon;

    if (loading) {
        return (
            <div className="flex h-full items-center justify-center text-muted-foreground">
                Loading monitoring data…
            </div>
        );
    }

    return (
        <div className="flex flex-col gap-4 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-semibold">Redis & Infrastructure</h1>
                    <p className="text-sm text-muted-foreground">
                        Cache memory, hit rate, evictions, and per-replica process stats.
                    </p>
                </div>
                <Button
                    variant="outline"
                    onClick={handleManualRefresh}
                    disabled={scanRefreshing}
                    className="gap-2"
                >
                    <RefreshCw className={`h-4 w-4 ${scanRefreshing ? "animate-spin" : ""}`} />
                    Refresh (incl. key scan)
                </Button>
            </div>

            {errorMsg && (
                <Card className="border-destructive/50">
                    <CardContent className="pt-6 flex items-center gap-2 text-destructive">
                        <AlertTriangle className="h-5 w-5" />
                        {errorMsg}
                    </CardContent>
                </Card>
            )}

            {/* Backend-offline banner */}
            {stats && !stats.connected && (
                <Card className="border-warning/50">
                    <CardContent className="pt-6 flex items-center gap-2 text-warning">
                        <CircleSlash className="h-5 w-5" />
                        <div>
                            <p className="font-medium">
                                Redis not connected — using in-process fallback.
                            </p>
                            <p className="text-xs text-muted-foreground">
                                Rate limits, OTP lockouts, and cache entries are per-replica and
                                will not survive restart. Configure <code>REDIS_URL</code> on Railway.
                            </p>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Eviction alert */}
            {stats?.connected && (stats.evicted_keys_total ?? 0) > 0 && (
                <Card className="border-warning/50">
                    <CardContent className="pt-6 flex items-center gap-2 text-warning">
                        <AlertTriangle className="h-5 w-5" />
                        <span>
                            Redis has evicted{" "}
                            <strong>{formatNumber(stats.evicted_keys_total)}</strong> keys since
                            start. Consider raising <code>maxmemory</code> on the Railway plugin.
                        </span>
                    </CardContent>
                </Card>
            )}

            {/* Top-line KPI cards */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
                {/* Memory */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            Memory Usage
                        </CardTitle>
                        <HardDrive className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-semibold">
                            {stats?.used_memory_human ?? "–"}
                        </div>
                        <div className="text-xs text-muted-foreground">
                            of {stats?.maxmemory_human ?? "unlimited"} ({stats?.maxmemory_policy ?? "–"})
                        </div>
                        <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-muted">
                            <div
                                className={`h-full transition-all ${memoryColor(memoryPercent)}`}
                                style={{
                                    width: `${memoryPercent != null ? Math.min(100, memoryPercent) : 0}%`,
                                }}
                            />
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                            {memoryPercent != null ? `${memoryPercent.toFixed(1)}% used` : "No maxmemory set"}
                        </div>
                    </CardContent>
                </Card>

                {/* Hit rate */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            Hit Rate
                        </CardTitle>
                        <Zap className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-semibold">{hitRateLabel}</div>
                        <div className="text-xs text-muted-foreground">
                            {formatNumber(stats?.keyspace_hits_total)} hits ·{" "}
                            {formatNumber(stats?.keyspace_misses_total)} misses
                        </div>
                    </CardContent>
                </Card>

                {/* Keys */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            Total Keys
                        </CardTitle>
                        <Database className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-semibold">
                            {formatNumber(stats?.total_keys)}
                        </div>
                        <div className="text-xs text-muted-foreground">
                            {formatNumber(stats?.expired_keys_total)} expired ·{" "}
                            {formatNumber(stats?.evicted_keys_total)} evicted
                        </div>
                    </CardContent>
                </Card>

                {/* Circuit breaker */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            Supabase Circuit
                        </CardTitle>
                        <Gauge className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <Badge variant={circuitInfo.variant} className="gap-1">
                            <CircuitIcon className="h-3 w-3" />
                            {infra?.db_circuit_breaker?.state?.toUpperCase() ?? "–"}
                        </Badge>
                        <p className="mt-2 text-xs text-muted-foreground">{circuitInfo.label}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                            {infra?.db_circuit_breaker?.recent_failures ?? 0} recent failures
                        </p>
                    </CardContent>
                </Card>
            </div>

            {/* Redis connectivity (PING + pub/sub round-trip) — off the poll loop */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Activity className="h-5 w-5" />
                        Redis Connectivity
                    </CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Live PING + pub/sub round-trip per configured URL — the only check that
                        distinguishes &quot;up&quot; from &quot;up but pub/sub broken&quot;. Runs on
                        load and manual refresh only (it costs connection quota), so use Refresh to
                        re-probe.
                    </p>
                </CardHeader>
                <CardContent>
                    {!connectivity ? (
                        <p className="py-4 text-center text-sm text-muted-foreground">
                            Probing…
                        </p>
                    ) : (
                        <div className="flex flex-col gap-2">
                            {connectivity.map((p) => (
                                <div
                                    key={p.label}
                                    className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/30 px-3 py-2"
                                >
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-2">
                                            <code className="text-xs font-medium">{p.label}</code>
                                            {p.same_as ? (
                                                <span className="text-xs text-muted-foreground">
                                                    (same as {p.same_as})
                                                </span>
                                            ) : null}
                                        </div>
                                        <p className="truncate text-xs text-muted-foreground">
                                            {p.endpoint ?? "not configured"}
                                            {p.ping_ms != null ? ` · ping ${p.ping_ms}ms` : ""}
                                            {p.pubsub ? ` · pub/sub ${p.pubsub.ok ? "ok" : "FAILED"}` : ""}
                                        </p>
                                        {(p.error || p.warning || p.pubsub?.error) && (
                                            <p className="text-xs text-red-600 dark:text-red-400">
                                                {p.error || p.pubsub?.error || p.warning}
                                            </p>
                                        )}
                                    </div>
                                    {connectivityBadge(p.status)}
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* WebSocket fan-out degraded banner — the live-map failure mode */}
            {ws?.fanout?.configured && !ws.fanout.active && (
                <Card className="border-destructive/50">
                    <CardContent className="pt-6 flex items-start gap-2 text-destructive">
                        <WifiOff className="mt-0.5 h-5 w-5 shrink-0" />
                        <div>
                            <p className="font-medium">
                                WebSocket fan-out is LOCAL-ONLY on this replica — live monitoring is degraded.
                            </p>
                            <p className="text-xs text-muted-foreground">
                                The cross-replica pub/sub channel <code>{ws.fanout.channel}</code> is not
                                connected{ws.fanout.last_error ? <> (<code>{ws.fanout.last_error}</code>)</> : null},
                                so the admin map only sees clients on the same worker. Fix Redis connectivity
                                (see the connectivity probe above) and restart the backend — pub/sub does not
                                self-heal after a boot-time failure.
                            </p>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* WebSocket health */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Radio className="h-5 w-5" />
                        WebSocket Health
                    </CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Cross-replica fan-out status and live socket counts for the uvicorn worker
                        that served this request. Counts are <strong>per-worker</strong>, not
                        host-wide
                        {ws?.workers_hint ? (
                            <> — this backend runs {ws.workers_hint} workers, so the host total is
                            spread across them and each refresh may hit a different one</>
                        ) : null}
                        .
                    </p>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-5">
                        {/* Fan-out status */}
                        <div className="rounded-md border bg-muted/30 px-3 py-2 lg:col-span-1">
                            <p className="text-xs text-muted-foreground">Fan-out</p>
                            {!ws?.fanout ? (
                                <p className="font-semibold">–</p>
                            ) : ws.fanout.active ? (
                                <Badge variant="default" className="mt-1 gap-1">
                                    <CheckCircle2 className="h-3 w-3" /> Live
                                </Badge>
                            ) : ws.fanout.configured ? (
                                <Badge variant="destructive" className="mt-1 gap-1">
                                    <WifiOff className="h-3 w-3" /> Local-only
                                </Badge>
                            ) : (
                                <Badge variant="secondary" className="mt-1 gap-1">
                                    <CircleSlash className="h-3 w-3" /> Single-machine
                                </Badge>
                            )}
                        </div>
                        <InfraStat label="Total sockets" value={formatNumber(ws?.connections?.total)} />
                        <InfraStat label="Admins" value={formatNumber(ws?.connections?.admins)} />
                        <InfraStat label="Drivers" value={formatNumber(ws?.connections?.drivers)} />
                        <InfraStat label="Riders" value={formatNumber(ws?.connections?.riders)} />
                    </div>
                    <p className="mt-3 text-xs text-muted-foreground">
                        Channel <code>{ws?.fanout?.channel ?? "–"}</code>
                        {ws?.fanout?.backend_scheme ? <> · backend <code>{ws.fanout.backend_scheme}://</code></> : null}
                        {ws?.replica_hostname ? <> · host <code>{ws.replica_hostname}</code></> : null}
                        {ws?.worker_pid ? <> · worker pid <code>{ws.worker_pid}</code></> : null}
                        {ws?.fanout?.last_error ? <> · last error <code>{ws.fanout.last_error}</code></> : null}
                    </p>
                </CardContent>
            </Card>

            {/* Prefix breakdown */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Database className="h-5 w-5" />
                        Keys by Prefix
                    </CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Refreshed only on manual refresh — uses SCAN, so not on the poll loop.
                        Flush actions are restricted to caches + idempotency keys; session / OTP
                        / rate-limit prefixes can&apos;t be purged from here to prevent accidental
                        mass-logouts.
                    </p>
                </CardHeader>
                <CardContent>
                    {prefixCounts.length === 0 ? (
                        <p className="py-6 text-center text-sm text-muted-foreground">
                            No keys found.
                        </p>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b text-left text-xs uppercase text-muted-foreground">
                                        <th className="py-2 pr-4 font-medium">Prefix</th>
                                        <th className="py-2 pr-4 font-medium">Description</th>
                                        <th className="py-2 pr-4 font-medium text-right">Keys</th>
                                        <th className="py-2 font-medium text-right">Action</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {prefixCounts.map((p) => (
                                        <tr
                                            key={p.prefix}
                                            className="border-b last:border-0 hover:bg-muted/30"
                                        >
                                            <td className="py-2 pr-4 font-mono text-xs">
                                                {p.prefix || "(empty)"}
                                            </td>
                                            <td className="py-2 pr-4 text-muted-foreground">
                                                {p.description || "—"}
                                            </td>
                                            <td className="py-2 pr-4 text-right font-medium">
                                                {formatNumber(p.count)}
                                            </td>
                                            <td className="py-2 text-right">
                                                {p.flushable ? (
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        className="gap-1"
                                                        onClick={() => setFlushCandidate(p)}
                                                        disabled={p.count === 0}
                                                    >
                                                        <Trash2 className="h-3 w-3" />
                                                        Flush
                                                    </Button>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">
                                                        Protected
                                                    </span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Infrastructure block */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Server className="h-5 w-5" />
                        Replica: {infra?.replica?.hostname ?? "–"}
                    </CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Process-level stats for the backend replica that served this request.
                        For a fleet-wide view, point Prometheus at each replica&apos;s{" "}
                        <code>/metrics</code>.
                    </p>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
                        <InfraStat
                            label="PID"
                            value={infra?.replica?.pid?.toString() ?? "–"}
                        />
                        <InfraStat
                            label="Uptime"
                            value={formatDuration(infra?.replica?.uptime_seconds)}
                        />
                        <InfraStat
                            label="RSS Memory"
                            value={infra?.process?.rss_human ?? "–"}
                        />
                        <InfraStat
                            label="Thread Pool"
                            value={
                                infra?.thread_pool?.max_workers != null
                                    ? `max ${infra.thread_pool.max_workers}`
                                    : "–"
                            }
                        />
                        <InfraStat
                            label="CPU (user)"
                            value={
                                infra?.process?.cpu_user_seconds != null
                                    ? `${infra.process.cpu_user_seconds.toFixed(1)}s`
                                    : "–"
                            }
                        />
                        <InfraStat
                            label="CPU (system)"
                            value={
                                infra?.process?.cpu_system_seconds != null
                                    ? `${infra.process.cpu_system_seconds.toFixed(1)}s`
                                    : "–"
                            }
                        />
                        <InfraStat
                            label="Redis Memory"
                            value={infra?.redis?.used_memory_human ?? "–"}
                        />
                        <InfraStat
                            label="Redis Evictions"
                            value={formatNumber(infra?.redis?.evicted_keys_total)}
                        />
                    </div>

                    <Separator className="my-4" />

                    <h3 className="mb-2 text-sm font-medium">Counters (this replica, since start)</h3>
                    {infra && Object.keys(infra.metrics ?? {}).length > 0 ? (
                        <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-3">
                            {Object.entries(infra.metrics ?? {})
                                .sort((a, b) => b[1] - a[1])
                                .map(([name, value]) => (
                                    <div
                                        key={name}
                                        className="flex items-center justify-between rounded-md border bg-muted/30 px-3 py-1.5 font-mono"
                                    >
                                        <span className="truncate text-muted-foreground" title={name}>
                                            {name.replace(/^spinr_/, "")}
                                        </span>
                                        <span className="ml-2 shrink-0 font-semibold">
                                            {formatNumber(value)}
                                        </span>
                                    </div>
                                ))}
                        </div>
                    ) : (
                        <p className="text-xs text-muted-foreground">No counters recorded yet.</p>
                    )}
                </CardContent>
            </Card>

            {/* Flush confirmation */}
            <AlertDialog open={!!flushCandidate} onOpenChange={(o) => !o && setFlushCandidate(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle className="flex items-center gap-2">
                            <AlertTriangle className="h-5 w-5 text-destructive" />
                            Flush prefix{" "}
                            <code className="rounded bg-muted px-1.5 py-0.5 text-sm">
                                {flushCandidate?.prefix}
                            </code>
                            ?
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            This will delete{" "}
                            <strong>{formatNumber(flushCandidate?.count)} keys</strong> matching
                            this prefix. Cache entries will be rehydrated from Postgres on next
                            read, so this is safe but will cause a brief latency spike on hot
                            endpoints. For idempotency keys, any in-flight client retry within
                            the next ~24h will re-execute the handler instead of replaying the
                            cached response.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={flushing}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={handleFlush}
                            disabled={flushing}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            {flushing ? "Flushing…" : "Confirm Flush"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

function InfraStat({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-md border bg-muted/30 px-3 py-2">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="truncate font-semibold" title={value}>
                {value}
            </p>
        </div>
    );
}
