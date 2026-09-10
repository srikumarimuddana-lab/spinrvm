"use client";

/**
 * Migration Data Quality Scan (Step 17) — scans completed rides for a
 * missing driver, a missing rider, a placeholder pickup/dropoff address, or
 * a $0.00 fare, and additively tags each finding onto
 * legacy_import_metadata.data_quality.issues.
 *
 * Like Pre-Launch Legacy Data Flagging, there is no CSV to upload — this
 * operates entirely on production data already in `rides` via
 * backend/services/migration_data_quality_service.py. Additive only: it
 * never deletes, never reassigns a driver/rider, and never touches
 * rides.status — see docs/runbooks/migration-data-quality-strategy.md §2
 * for why. Unlike the pre-launch flag tool, applying this doesn't change
 * what any admin view shows by default (it only adds a badge visible on
 * the Rides page's "Needs Review" filter), so it skips that tool's
 * type-to-confirm gate — the action is lower-stakes: a label, not a
 * visibility change.
 */

import { useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Loader2 } from "lucide-react";
import {
    adminCommitDataQualityScan,
    adminPreviewDataQualityScan,
    type DataQualityScanCommitResult,
    type DataQualityScanReport,
} from "@/lib/api";
import { WhatThisToolDoes } from "@/components/bulk-import/what-this-tool-does";
import { StatTile } from "@/components/bulk-import/stat-tile";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";

// Compact, copy-pasteable markdown table mirroring the stat tiles below.
function buildSummaryText(report: DataQualityScanReport): string {
    const c = report.counts;
    const rows: [string, number][] = [
        ["Missing driver", c.missing_driver],
        ["Missing rider", c.missing_rider],
        ["Placeholder address", c.placeholder_address],
        ["$0.00 fare", c.zero_fare],
        ["Rides affected (total)", c.rides_affected],
    ];
    const lines = [
        `Migration Data Quality Scan — batch ${report.batch}`,
        "| Metric | Count |",
        "|---|---|",
        ...rows.map(([label, value]) => `| ${label} | ${value} |`),
    ];
    return lines.join("\n");
}

export function DataQualityScan() {
    const { toast } = useToast();

    const [report, setReport] = useState<DataQualityScanReport | null>(null);
    const [committed, setCommitted] = useState<DataQualityScanCommitResult | null>(null);
    const [previewing, setPreviewing] = useState(false);
    const [committing, setCommitting] = useState(false);

    const handlePreview = async () => {
        setPreviewing(true);
        setCommitted(null);
        try {
            setReport(await adminPreviewDataQualityScan());
        } catch (e) {
            setReport(null);
            toast({
                title: "Preview failed",
                description: e instanceof Error ? e.message : "Could not build the scan plan.",
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
            const res = await adminCommitDataQualityScan({ batch: report.batch });
            setCommitted(res);
            if (!res.committed) {
                setReport({ batch: res.batch, can_commit: res.can_commit ?? false, counts: res.counts });
                toast({
                    title: "Nothing left to flag",
                    description: "Every finding was already flagged (likely by a prior run).",
                });
            } else {
                const conflicts = res.conflicts ?? 0;
                toast({
                    title: conflicts > 0 ? "Flagged with some conflicts" : "Flagged",
                    description: `${res.rides_flagged ?? 0} ride(s) flagged for review.${
                        conflicts > 0 ? ` ${conflicts} row(s) changed concurrently — re-run to pick them up.` : ""
                    }`,
                    variant: conflicts > 0 ? "destructive" : undefined,
                });
            }
        } catch (e) {
            toast({
                title: "Commit failed",
                description: e instanceof Error ? e.message : "The scan commit did not complete.",
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
                    <AlertTriangle className="h-5 w-5" />
                    Migration data quality scan
                </CardTitle>
                <CardDescription>
                    Find completed rides with a data-quality issue and flag them for review. Read
                    the full breakdown of what each finding means and why in{" "}
                    <a
                        href="https://github.com/srikumarimuddana-lab/spinrvm/blob/main/docs/runbooks/migration-data-quality-strategy.md"
                        target="_blank"
                        rel="noreferrer"
                        className="underline underline-offset-2"
                    >
                        the data-quality strategy runbook
                    </a>
                    .
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <WhatThisToolDoes
                    what={
                        <>
                            Scans completed rides for a missing driver, a missing rider, a
                            placeholder pickup/dropoff address, or a $0.00 fare, and tags each
                            finding so it&apos;s visible on the Rides page&apos;s &quot;Needs
                            Review&quot; filter.
                        </>
                    }
                    why={
                        <>
                            These are the data-shape issues most likely to have slipped through
                            the migration importers — surfacing them lets an operator find and fix
                            the underlying rows deliberately, instead of a rider/driver noticing a
                            broken-looking ride first.
                        </>
                    }
                    whichFiles={<>No file upload — this reads directly from production data already in Spinr.</>}
                    value={
                        <>
                            Migration data-quality problems get a visible, filterable badge instead
                            of hiding until someone stumbles on them.
                        </>
                    }
                    safetyNote={
                        <>
                            Additive only — it never deletes, never reassigns a driver/rider, and
                            never touches a ride&apos;s status. Applying this doesn&apos;t change
                            what any admin view shows by default; it only adds a badge visible on
                            the &quot;Needs Review&quot; filter, which is why this tool skips the
                            type-to-confirm gate other production-writing tools on this page use.
                        </>
                    }
                />

                <div className="space-y-3">
                    <h3 className="text-sm font-medium">1. Preview</h3>
                    <Button onClick={handlePreview} disabled={previewing}>
                        {previewing ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Scanning…
                            </>
                        ) : (
                            "Preview (no writes)"
                        )}
                    </Button>
                </div>

                {report && c ? (
                    <div className="space-y-4">
                        <h3 className="text-sm font-medium">2. Review and flag</h3>

                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <StatTile label="Missing driver" value={c.missing_driver} tone="warn" />
                            <StatTile label="Missing rider" value={c.missing_rider} tone="warn" />
                            <StatTile label="Placeholder address" value={c.placeholder_address} tone="warn" />
                            <StatTile label="$0.00 fare" value={c.zero_fare} tone="warn" />
                        </div>

                        <p className="text-xs text-muted-foreground">
                            {c.rides_affected} ride(s) affected in total (a ride can have more than
                            one issue). Batch <span className="font-mono">{report.batch}</span>.
                        </p>

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
                                    {committed.rides_flagged ?? 0} ride(s) flagged for review.
                                    {(committed.conflicts ?? 0) > 0
                                        ? ` ${committed.conflicts} row(s) changed concurrently and were skipped — re-run to pick them up.`
                                        : ""}
                                </span>
                            </div>
                        ) : report.can_commit ? (
                            <Button onClick={handleCommit} disabled={committing}>
                                {committing ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        Flagging…
                                    </>
                                ) : (
                                    `Flag ${c.rides_affected} ride(s) for review`
                                )}
                            </Button>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Nothing to flag — every finding has already been flagged. Everything
                                imported so far looks clean.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
