"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    AlertTriangle,
    Bug,
    CheckCircle2,
    ExternalLink,
    Layers,
    Loader2,
    RefreshCw,
    ShieldAlert,
    Users,
    VolumeX,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { useAuthStore } from "@/store/authStore";

import {
    getSentryConfig,
    getSentryIssueDetail,
    getSentryIssues,
    updateSentryIssueStatus,
    type SentryConfig,
    type SentryIssue,
    type SentryIssueDetail,
    type SentryIssueStatus,
    type SentryIssuesResponse,
} from "@/lib/api";

// Status the list is filtered by. "unresolved" is the operator's default triage
// queue; "resolved"/"ignored" let them audit what's been actioned.
const STATUS_OPTIONS: { value: string; label: string }[] = [
    { value: "unresolved", label: "Unresolved" },
    { value: "resolved", label: "Resolved" },
    { value: "ignored", label: "Ignored" },
];

const PERIOD_OPTIONS: { value: string; label: string }[] = [
    { value: "24h", label: "Last 24h" },
    { value: "7d", label: "Last 7 days" },
    { value: "14d", label: "Last 14 days" },
    { value: "30d", label: "Last 30 days" },
    { value: "90d", label: "Last 90 days" },
];

const SURFACE_LABEL: Record<string, string> = {
    backend: "Backend",
    "rider-app": "Rider App",
    "driver-app": "Driver App",
    admin: "Admin",
    unknown: "Unknown",
};

function surfaceBadgeClass(surface: string): string {
    switch (surface) {
        case "backend":
            return "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300";
        case "rider-app":
            return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300";
        case "driver-app":
            return "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300";
        case "admin":
            return "bg-slate-200 text-slate-800 dark:bg-slate-700/50 dark:text-slate-200";
        default:
            return "bg-muted text-muted-foreground";
    }
}

// The priority chip prefers Sentry's high/medium/low triage field and falls
// back to the event level (fatal/error/warning/…) when priority is absent.
function priorityInfo(issue: SentryIssue): { label: string; variant: "destructive" | "secondary" | "outline" } {
    const p = (issue.priority || "").toLowerCase();
    if (p === "high") return { label: "High", variant: "destructive" };
    if (p === "medium") return { label: "Medium", variant: "secondary" };
    if (p === "low") return { label: "Low", variant: "outline" };
    const level = (issue.level || "").toLowerCase();
    if (level === "fatal" || level === "error") return { label: level || "error", variant: "destructive" };
    if (level === "warning") return { label: "warning", variant: "secondary" };
    return { label: level || "info", variant: "outline" };
}

function timeAgo(iso: string | null): string {
    if (!iso) return "–";
    const then = Date.parse(iso);
    if (!Number.isFinite(then)) return "–";
    const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
}

export default function SentryLogsPage() {
    const { toast } = useToast();
    // Strict super_admin, matching the backend's require_super_admin mount for
    // /api/admin/sentry exactly. The sidebar already hides the entry, but a
    // direct URL visit must not render a UI whose every call 403s.
    const me = useAuthStore((s) => s.user);
    const isSuperAdmin = me?.role === "super_admin";

    const [config, setConfig] = useState<SentryConfig | null>(null);
    const [issuesResp, setIssuesResp] = useState<SentryIssuesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    // Filters
    const [surface, setSurface] = useState<string>("all");
    const [status, setStatus] = useState<string>("unresolved");
    const [period, setPeriod] = useState<string>("14d");

    // Detail dialog
    const [detailId, setDetailId] = useState<string | null>(null);
    const [detail, setDetail] = useState<SentryIssueDetail | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);

    // Per-issue action in flight (id being resolved/ignored)
    const [actioningId, setActioningId] = useState<string | null>(null);

    // `isStale` lets the caller abandon a response that arrived after its
    // effect was torn down. Without it, flipping filters quickly can land a
    // slow earlier response after a fast later one, leaving the list showing
    // rows that don't match the active filters.
    const fetchIssues = useCallback(
        async (isStale?: () => boolean) => {
            setErrorMsg(null);
            try {
                const res = await getSentryIssues({
                    surface: surface === "all" ? undefined : surface,
                    status,
                    statsPeriod: period,
                    limit: 50,
                });
                if (isStale?.()) return;
                setIssuesResp(res);
            } catch (err) {
                if (isStale?.()) return;
                setIssuesResp(null);
                setErrorMsg(err instanceof Error ? err.message : "Failed to load Sentry issues");
            }
        },
        [surface, status, period],
    );

    // Config first, on mount only: an unconfigured deployment shows setup
    // guidance instead of firing a doomed issues query. The issue fetch is
    // deliberately NOT done here — the effect below owns every fetch (including
    // the first one, once `configured` flips true), so the initial load issues
    // one request rather than two racing ones.
    useEffect(() => {
        if (!isSuperAdmin) {
            setLoading(false);
            return;
        }
        let cancelled = false;
        (async () => {
            try {
                const cfg = await getSentryConfig();
                if (!cancelled) setConfig(cfg);
            } catch (err) {
                if (!cancelled) {
                    setErrorMsg(err instanceof Error ? err.message : "Failed to load Sentry config");
                    setLoading(false);
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [isSuperAdmin]);

    // Owns every issue fetch: the initial one (when config resolves as
    // configured) and every filter change thereafter.
    useEffect(() => {
        if (config == null) return;
        if (!config.configured) {
            setLoading(false);
            return;
        }
        let cancelled = false;
        (async () => {
            await fetchIssues(() => cancelled);
            if (!cancelled) setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [config, fetchIssues]);

    // Refresh: drop everything currently held and pull fresh data. This is the
    // "clear all existing memory and get new data" behaviour — no client cache
    // survives, and the backend never cached in the first place.
    const handleRefresh = useCallback(async () => {
        setRefreshing(true);
        setIssuesResp(null);
        setDetail(null);
        setDetailId(null);
        try {
            await fetchIssues();
        } finally {
            setRefreshing(false);
        }
    }, [fetchIssues]);

    const openDetail = useCallback(async (issueId: string) => {
        setDetailId(issueId);
        setDetail(null);
        setDetailLoading(true);
        try {
            const d = await getSentryIssueDetail(issueId);
            setDetail(d);
        } catch (err) {
            toast({
                title: "Could not load issue",
                description: err instanceof Error ? err.message : "Unknown error",
                variant: "destructive",
            });
            setDetailId(null);
        } finally {
            setDetailLoading(false);
        }
    }, [toast]);

    const applyStatus = useCallback(
        async (issueId: string, newStatus: SentryIssueStatus) => {
            setActioningId(issueId);
            try {
                await updateSentryIssueStatus(issueId, newStatus);
                toast({
                    title: newStatus === "resolved" ? "Issue resolved" : `Issue ${newStatus}`,
                    description: `Issue ${issueId} is now ${newStatus}.`,
                });
                // Drop it from the current list if it no longer matches the
                // active status filter (e.g. resolved while viewing unresolved).
                setIssuesResp((prev) =>
                    prev && newStatus !== status
                        ? { ...prev, issues: prev.issues.filter((i) => i.id !== issueId), count: prev.count - 1 }
                        : prev,
                );
                if (detailId === issueId && newStatus !== status) {
                    setDetailId(null);
                    setDetail(null);
                }
            } catch (err) {
                toast({
                    title: "Action failed",
                    description: err instanceof Error ? err.message : "Unknown error",
                    variant: "destructive",
                });
            } finally {
                setActioningId(null);
            }
        },
        [toast, status, detailId],
    );

    const enabledSurfaces = useMemo(
        () => (config?.surfaces ?? []).filter((s) => s.enabled),
        [config],
    );

    const issues = issuesResp?.issues ?? [];

    if (!isSuperAdmin) {
        return (
            <div className="flex flex-col items-center justify-center gap-3 p-16 text-center">
                <ShieldAlert className="h-10 w-10 text-muted-foreground" />
                <h2 className="text-lg font-semibold">Super admin only</h2>
                <p className="max-w-md text-sm text-muted-foreground">
                    This page reads raw production error reports — including stacktraces — across
                    every Spinr surface, so it is restricted to super admins.
                </p>
            </div>
        );
    }

    if (loading) {
        return (
            <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading Sentry issues…
            </div>
        );
    }

    return (
        <div className="flex flex-col gap-4 p-6">
            {/* Header */}
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="flex items-center gap-2 text-2xl font-semibold">
                        <Bug className="h-6 w-6" />
                        Sentry Issues
                    </h1>
                    <p className="text-sm text-muted-foreground">
                        Live production errors across every Spinr surface. Data is fetched fresh on
                        each load — nothing is stored. Refresh clears the view and pulls the latest.
                    </p>
                </div>
                {config?.configured && (
                    <Button variant="outline" onClick={handleRefresh} disabled={refreshing} className="gap-2">
                        <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                )}
            </div>

            {/* Errors render at top level, NOT inside the `configured` branch
                below: a failed /config call leaves `config` null, so an error
                card nested in that branch would never mount and the failure
                would be swallowed into a blank page. */}
            {errorMsg && (
                <Card className="border-destructive/50">
                    <CardContent className="flex items-center gap-2 pt-6 text-destructive">
                        <AlertTriangle className="h-5 w-5" />
                        {errorMsg}
                    </CardContent>
                </Card>
            )}

            {/* Not-configured setup panel */}
            {config && !config.configured && (
                <Card className="border-yellow-500/50">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-yellow-600 dark:text-yellow-400">
                            <AlertTriangle className="h-5 w-5" />
                            Sentry API not configured
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3 text-sm text-muted-foreground">
                        <p>
                            Set these backend environment variables to enable the viewer, then
                            redeploy the backend:
                        </p>
                        <ul className="list-disc space-y-1 pl-5 font-mono text-xs">
                            <li>SENTRY_API_TOKEN — org token with event:read + event:write</li>
                            <li>SENTRY_ORG_SLUG — your Sentry organization slug</li>
                            <li>SENTRY_API_BASE_URL — optional; EU region is https://de.sentry.io</li>
                            <li>SENTRY_PROJECT_BACKEND / _RIDER / _DRIVER / _ADMIN — project slugs</li>
                        </ul>
                        <div>
                            <p className="mb-1 font-medium text-foreground">Surface status</p>
                            <div className="flex flex-wrap gap-2">
                                {(config.surfaces ?? []).map((s) => (
                                    <Badge
                                        key={s.surface}
                                        variant={s.enabled ? "default" : "outline"}
                                        className="gap-1"
                                    >
                                        {SURFACE_LABEL[s.surface] ?? s.surface}
                                        {s.enabled ? " · wired" : " · unset"}
                                    </Badge>
                                ))}
                            </div>
                        </div>
                    </CardContent>
                </Card>
            )}

            {config?.configured && (
                <>
                    {/* Filters */}
                    <Card>
                        <CardContent className="flex flex-wrap items-center gap-3 pt-6">
                            <div className="flex items-center gap-2">
                                <Layers className="h-4 w-4 text-muted-foreground" />
                                <Select value={surface} onValueChange={setSurface}>
                                    <SelectTrigger className="w-[160px]">
                                        <SelectValue placeholder="Surface" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="all">All surfaces</SelectItem>
                                        {enabledSurfaces.map((s) => (
                                            <SelectItem key={s.surface} value={s.surface}>
                                                {SURFACE_LABEL[s.surface] ?? s.surface}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <Select value={status} onValueChange={setStatus}>
                                <SelectTrigger className="w-[150px]">
                                    <SelectValue placeholder="Status" />
                                </SelectTrigger>
                                <SelectContent>
                                    {STATUS_OPTIONS.map((o) => (
                                        <SelectItem key={o.value} value={o.value}>
                                            {o.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <Select value={period} onValueChange={setPeriod}>
                                <SelectTrigger className="w-[150px]">
                                    <SelectValue placeholder="Period" />
                                </SelectTrigger>
                                <SelectContent>
                                    {PERIOD_OPTIONS.map((o) => (
                                        <SelectItem key={o.value} value={o.value}>
                                            {o.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <span className="ml-auto text-sm text-muted-foreground">
                                {issues.length} issue{issues.length === 1 ? "" : "s"}
                                {issuesResp?.truncated ? " (showing latest per surface)" : ""}
                            </span>
                        </CardContent>
                    </Card>

                    {/* Issue list */}
                    {!errorMsg && issues.length === 0 && (
                        <Card>
                            <CardContent className="flex flex-col items-center gap-2 py-12 text-muted-foreground">
                                <CheckCircle2 className="h-8 w-8 text-emerald-500" />
                                <p>No {status} issues in this window. Nothing to triage.</p>
                            </CardContent>
                        </Card>
                    )}

                    {issues.map((issue) => {
                        const prio = priorityInfo(issue);
                        const isUnresolved = status === "unresolved";
                        return (
                            <Card key={`${issue.surface}:${issue.id}`} className="transition-colors hover:bg-muted/30">
                                <CardContent className="flex flex-col gap-2 pt-6">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Badge className={surfaceBadgeClass(issue.surface)}>
                                            {SURFACE_LABEL[issue.surface] ?? issue.surface}
                                        </Badge>
                                        <Badge variant={prio.variant} className="capitalize">
                                            {prio.label}
                                        </Badge>
                                        {issue.short_id && (
                                            <code className="text-xs text-muted-foreground">{issue.short_id}</code>
                                        )}
                                        <span className="ml-auto text-xs text-muted-foreground">
                                            {timeAgo(issue.last_seen)}
                                        </span>
                                    </div>
                                    {/* The title is the interactive element rather than the whole
                                        card: the card also holds the Resolve button and the Sentry
                                        link, and nesting those inside a clickable card would mean
                                        nested interactive controls (a11y) plus stopPropagation
                                        plumbing on each one. A real <button> is keyboard-operable
                                        for free. */}
                                    <button
                                        type="button"
                                        className="min-w-0 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
                                        disabled={!issue.id}
                                        onClick={() => issue.id && openDetail(issue.id)}
                                    >
                                        <span
                                            className="block truncate font-medium hover:underline"
                                            title={issue.title ?? undefined}
                                        >
                                            {issue.title || issue.type || "Untitled issue"}
                                        </span>
                                        {issue.culprit && (
                                            <span
                                                className="block truncate text-xs text-muted-foreground"
                                                title={issue.culprit}
                                            >
                                                {issue.culprit}
                                            </span>
                                        )}
                                    </button>
                                    <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
                                        <span className="flex items-center gap-1">
                                            <Bug className="h-3.5 w-3.5" />
                                            {issue.count.toLocaleString()} event{issue.count === 1 ? "" : "s"}
                                        </span>
                                        {issue.user_count != null && (
                                            <span className="flex items-center gap-1">
                                                <Users className="h-3.5 w-3.5" />
                                                {issue.user_count.toLocaleString()} user
                                                {issue.user_count === 1 ? "" : "s"}
                                            </span>
                                        )}
                                        <div className="ml-auto flex items-center gap-2">
                                            {issue.permalink && (
                                                <a
                                                    href={issue.permalink}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="flex items-center gap-1 text-muted-foreground hover:text-foreground"
                                                >
                                                    <ExternalLink className="h-3.5 w-3.5" />
                                                    Sentry
                                                </a>
                                            )}
                                            {isUnresolved && issue.id && (
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    className="h-7 gap-1"
                                                    disabled={actioningId === issue.id}
                                                    onClick={() => applyStatus(issue.id!, "resolved")}
                                                >
                                                    {actioningId === issue.id ? (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    ) : (
                                                        <CheckCircle2 className="h-3.5 w-3.5" />
                                                    )}
                                                    Resolve
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        );
                    })}
                </>
            )}

            {/* Detail dialog */}
            <Dialog
                open={!!detailId}
                onOpenChange={(o) => {
                    if (!o) {
                        setDetailId(null);
                        setDetail(null);
                    }
                }}
            >
                <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2 pr-6">
                            <Bug className="h-5 w-5 shrink-0" />
                            <span className="truncate">{detail?.title || "Issue detail"}</span>
                        </DialogTitle>
                        <DialogDescription className="sr-only">
                            Sentry issue detail, including the latest event&apos;s stacktrace and
                            available actions.
                        </DialogDescription>
                    </DialogHeader>

                    {detailLoading || !detail ? (
                        <div className="flex items-center justify-center py-12 text-muted-foreground">
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            Loading issue…
                        </div>
                    ) : (
                        <div className="flex flex-col gap-4">
                            <div className="flex flex-wrap items-center gap-2">
                                <Badge className={surfaceBadgeClass(detail.surface)}>
                                    {SURFACE_LABEL[detail.surface] ?? detail.surface}
                                </Badge>
                                <Badge variant={priorityInfo(detail).variant} className="capitalize">
                                    {priorityInfo(detail).label}
                                </Badge>
                                {detail.short_id && (
                                    <code className="text-xs text-muted-foreground">{detail.short_id}</code>
                                )}
                                <span className="text-xs text-muted-foreground">
                                    {detail.count.toLocaleString()} events · {detail.user_count ?? 0} users · last seen{" "}
                                    {timeAgo(detail.last_seen)}
                                </span>
                            </div>

                            {(detail.type || detail.value) && (
                                <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
                                    <span className="font-semibold">{detail.type}</span>
                                    {detail.value ? `: ${detail.value}` : ""}
                                </div>
                            )}

                            {detail.culprit && (
                                <p className="text-xs text-muted-foreground">
                                    <span className="font-medium">Culprit:</span> {detail.culprit}
                                </p>
                            )}

                            {/* Tags */}
                            {detail.tags.length > 0 && (
                                <div className="flex flex-wrap gap-1.5">
                                    {detail.tags.map((t) => (
                                        <span
                                            key={`${t.key}:${t.value}`}
                                            className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
                                        >
                                            {t.key}={t.value}
                                        </span>
                                    ))}
                                </div>
                            )}

                            <Separator />

                            {/* Stacktrace */}
                            <div>
                                <h3 className="mb-2 text-sm font-semibold">Stacktrace (latest event)</h3>
                                {detail.exceptions.length === 0 ? (
                                    <p className="text-sm text-muted-foreground">
                                        No stacktrace on the latest event.
                                    </p>
                                ) : (
                                    detail.exceptions.map((exc, ei) => (
                                        <div key={ei} className="mb-3">
                                            <p className="text-sm font-medium text-destructive">
                                                {exc.type}
                                                {exc.value ? `: ${exc.value}` : ""}
                                            </p>
                                            <div className="mt-1 overflow-x-auto rounded-md border bg-muted/30">
                                                {exc.frames.length === 0 ? (
                                                    <p className="px-3 py-2 text-xs text-muted-foreground">
                                                        No frames.
                                                    </p>
                                                ) : (
                                                    exc.frames.map((f, fi) => (
                                                        <div
                                                            key={fi}
                                                            className={`border-b px-3 py-1.5 text-xs last:border-0 ${
                                                                f.in_app ? "" : "opacity-60"
                                                            }`}
                                                        >
                                                            <div className="font-mono">
                                                                <span className="text-foreground">
                                                                    {f.filename || f.module || "?"}
                                                                </span>
                                                                {f.lineno != null && (
                                                                    <span className="text-muted-foreground">
                                                                        :{f.lineno}
                                                                    </span>
                                                                )}
                                                                {f.function && (
                                                                    <span className="text-muted-foreground">
                                                                        {" "}
                                                                        in {f.function}
                                                                    </span>
                                                                )}
                                                            </div>
                                                            {f.context_line && (
                                                                <pre className="mt-0.5 whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
                                                                    {f.context_line.trim()}
                                                                </pre>
                                                            )}
                                                        </div>
                                                    ))
                                                )}
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>

                            <Separator />

                            {/* Actions */}
                            <div className="flex flex-wrap items-center gap-2">
                                {detail.permalink && (
                                    <a
                                        href={detail.permalink}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                                    >
                                        <ExternalLink className="h-4 w-4" />
                                        Open in Sentry
                                    </a>
                                )}
                                <div className="ml-auto flex gap-2">
                                    {detail.id && detail.status !== "ignored" && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="gap-1"
                                            disabled={actioningId === detail.id}
                                            onClick={() => applyStatus(detail.id!, "ignored")}
                                        >
                                            <VolumeX className="h-4 w-4" />
                                            Ignore
                                        </Button>
                                    )}
                                    {detail.id && detail.status === "resolved" ? (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="gap-1"
                                            disabled={actioningId === detail.id}
                                            onClick={() => applyStatus(detail.id!, "unresolved")}
                                        >
                                            <RefreshCw className="h-4 w-4" />
                                            Reopen
                                        </Button>
                                    ) : (
                                        detail.id && (
                                            <Button
                                                size="sm"
                                                className="gap-1"
                                                disabled={actioningId === detail.id}
                                                onClick={() => applyStatus(detail.id!, "resolved")}
                                            >
                                                {actioningId === detail.id ? (
                                                    <Loader2 className="h-4 w-4 animate-spin" />
                                                ) : (
                                                    <CheckCircle2 className="h-4 w-4" />
                                                )}
                                                Resolve (Close)
                                            </Button>
                                        )
                                    )}
                                </div>
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}
