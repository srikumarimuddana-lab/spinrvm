"use client";

/**
 * Duration-Estimated Marker Backfill (ACTION_ITEMS.md A41 residual gap) --
 * stamps legacy_import_metadata.duration_estimated (plus this backfill's
 * own legacy_duration_estimated_backfill audit key) onto already-imported
 * legacy rides that predate 2026-08-19, when the importer itself started
 * writing that key on every new row going forward.
 *
 * Like Migration Data Quality Scan, there is no CSV to upload -- this
 * operates entirely on production data already in `rides` via
 * backend/services/booking_import_service.py's
 * plan_duration_estimated_backfill/apply_duration_estimated_backfill (the
 * exact pair backend/scripts/backfill_legacy_ride_duration_estimated.py
 * already calls from the CLI). Additive only: it never deletes, never
 * reassigns a driver/rider, never touches rides.status, and never touches
 * duration_minutes itself -- so, like the data-quality scan, it skips the
 * type-to-confirm gate the money/state-writing tools on this page use.
 */

import { useState } from "react";
import { CheckCircle2, Clock, Copy, Loader2 } from "lucide-react";
import {
    adminCommitDurationEstimatedBackfill,
    adminPreviewDurationEstimatedBackfill,
    type DurationEstimatedBackfillCommitResult,
    type DurationEstimatedBackfillReport,
} from "@/lib/api";
import { WhatThisToolDoes } from "@/components/bulk-import/what-this-tool-does";
import { StatTile } from "@/components/bulk-import/stat-tile";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";

// Compact, copy-pasteable markdown table mirroring the stat tiles below.
function buildSummaryText(report: DurationEstimatedBackfillReport): string {
    const c = report.counts;
    const rows: [string, number][] = [
        ["Rows to stamp", c.rows_to_stamp],
        ["duration_estimated=true", c.duration_estimated_true],
        ["duration_estimated=false", c.duration_estimated_false],
        ["Already marked, skipped", c.already_marked_skipped],
        ["Legacy rides scanned", c.legacy_rides_scanned],
    ];
    const lines = [
        `Duration-Estimated Marker Backfill — batch ${report.batch}`,
        "| Metric | Count |",
        "|---|---|",
        ...rows.map(([label, value]) => `| ${label} | ${value} |`),
    ];
    return lines.join("\n");
}

export function DurationEstimatedBackfill() {
    const { toast } = useToast();

    const [report, setReport] = useState<DurationEstimatedBackfillReport | null>(null);
    const [committed, setCommitted] = useState<DurationEstimatedBackfillCommitResult | null>(null);
    const [previewing, setPreviewing] = useState(false);
    const [committing, setCommitting] = useState(false);

    const handlePreview = async () => {
        setPreviewing(true);
        setCommitted(null);
        try {
            setReport(await adminPreviewDurationEstimatedBackfill());
        } catch (e) {
            setReport(null);
            toast({
                title: "Preview failed",
                description: e instanceof Error ? e.message : "Could not build the backfill plan.",
                variant: "destructive",
            });
        } finally {
            setPreviewing(false);
        }
    };

    const handleCommit = async () => {
        if (!report?.can_commit) return;
        setCommitting(true);
        try {
            const res = await adminCommitDurationEstimatedBackfill({ batch: report.batch });
            setCommitted(res);
            if (!res.committed) {
                setReport({ batch: res.batch, can_commit: res.can_commit ?? false, counts: res.counts, errors: res.errors });
                toast({
                    title: "Nothing left to stamp",
                    description: "Every legacy ride was already marked (likely by a prior run).",
                });
            } else {
                const conflicts = res.conflicts ?? 0;
                toast({
                    title: conflicts > 0 ? "Stamped with some conflicts" : "Stamped",
                    description: `${res.updated ?? 0} ride(s) stamped.${
                        conflicts > 0 ? ` ${conflicts} ride(s) changed concurrently — re-run to pick them up.` : ""
                    }`,
                    variant: conflicts > 0 ? "destructive" : undefined,
                });
            }
        } catch (e) {
            toast({
                title: "Commit failed",
                description: e instanceof Error ? e.message : "The backfill commit did not complete.",
                variant: "destructive",
            });
        } finally {
            setCommitting(false);
        }
    };

    const c = report?.counts;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Clock className="h-5 w-5" />
                    Duration-estimated marker backfill
                </CardTitle>
                <CardDescription>
                    Retroactively mark already-imported legacy rides that don&apos;t yet know whether
                    their trip duration was measured or estimated.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <WhatThisToolDoes
                    what={
                        <>
                            Scans legacy-imported rides for ones missing a
                            &quot;duration_estimated&quot; marker, and stamps
                            true/false based on whether the source booking had a real
                            trip-start timestamp.
                        </>
                    }
                    why={
                        <>
                            The importer only started stamping this marker on new rows from
                            2026-08-19 onward — rides imported before that fix has no way to tell an
                            estimated duration apart from a measured one. This backfill closes that
                            gap for the rides already committed.
                        </>
                    }
                    whichFiles={<>No file upload — this reads directly from production data already in Spinr.</>}
                    value={
                        <>
                            Any consumer of a legacy ride&apos;s trip duration (e.g. a driver
                            activity stat) can tell an estimated duration apart from a measured
                            one, for every legacy ride, not just ones imported after the fix.
                        </>
                    }
                    safetyNote={
                        <>
                            Additive only — it never deletes, never reassigns a driver/rider, never
                            touches a ride&apos;s status, and never touches duration_minutes itself.
                            Applying this doesn&apos;t change what any admin view shows by default,
                            which is why this tool skips the type-to-confirm gate other
                            production-writing tools on this page use.
                        </>
                    }
                />

                <div className="space-y-3">
                    <h3 className="text-sm font-medium">1. Preview</h3>
                    <Button onClick={handlePreview} disabled={previewing}>
                        {previewing ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Building plan…
                            </>
                        ) : (
                            "Preview (no writes)"
                        )}
                    </Button>
                </div>

                {report && c ? (
                    <div className="space-y-4">
                        <h3 className="text-sm font-medium">2. Review and stamp</h3>

                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <StatTile label="Rows to stamp" value={c.rows_to_stamp} tone="warn" />
                            <StatTile label="duration_estimated=true" value={c.duration_estimated_true} tone="warn" />
                            <StatTile label="duration_estimated=false" value={c.duration_estimated_false} tone="warn" />
                            <StatTile label="Already marked" value={c.already_marked_skipped} tone="warn" />
                        </div>

                        <p className="text-xs text-muted-foreground">
                            {c.legacy_rides_scanned} legacy ride(s) scanned in total. Batch{" "}
                            <span className="font-mono">{report.batch}</span>.
                        </p>

                        {report.errors.length > 0 ? (
                            <p className="text-sm text-destructive">
                                {report.errors.length} error(s) found — refusing to commit until
                                resolved: {report.errors.join("; ")}
                            </p>
                        ) : null}

                        <div className="flex justify-end">
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                    navigator.clipboard.writeText(buildSummaryText(report));
                                    toast({ description: "Summary copied", duration: 1500 });
                                }}
                            >
                                <Copy className="mr-2 h-4 w-4" />
                                Copy summary
                            </Button>
                        </div>

                        {committed?.committed ? (
                            <div className="flex items-center gap-2 rounded-md border border-success bg-success/10 p-3 text-sm">
                                <CheckCircle2 className="h-4 w-4 text-success" />
                                <span>
                                    {committed.updated ?? 0} ride(s) stamped.
                                    {(committed.conflicts ?? 0) > 0
                                        ? ` ${committed.conflicts} ride(s) changed concurrently and were skipped — re-run to pick them up.`
                                        : ""}
                                </span>
                            </div>
                        ) : report.can_commit ? (
                            <Button onClick={handleCommit} disabled={committing}>
                                {committing ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        Stamping…
                                    </>
                                ) : (
                                    `Stamp ${c.rows_to_stamp} ride(s)`
                                )}
                            </Button>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Nothing to stamp — every legacy ride has already been marked.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
