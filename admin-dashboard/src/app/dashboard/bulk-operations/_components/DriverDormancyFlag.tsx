"use client";

/**
 * Driver Dormancy Flagging — flags already-migrated production driver rows
 * that have gone idle past a threshold as `legacy_import_metadata.dormant
 * = true` (plus a `dormancy_tier`/`dormancy_flag` audit record), so admin
 * views/KPIs can filter them out of active-driver reporting.
 *
 * Like Pre-Launch Legacy Data Flagging, there is no CSV to upload — this
 * operates entirely on production data already in `drivers` via
 * backend/services/driver_dormancy_service.py. Additive only: sets new
 * metadata keys per matched row, never deletes, deactivates, suspends, or
 * changes go-online eligibility.
 *
 * Two tiers (see the service module's own docstring for the full
 * reasoning):
 * - `dormant` (90+ days idle): the soft, informational signal.
 * - `long_dormant` (365+ days idle): lines up with Spinr's own annual
 *   document-renewal cycle — a driver idle a full year needs full
 *   re-verification to come back regardless of anything this tool does.
 *
 * "Idle" is computed from the later of `went_online_at`/`went_offline_at`,
 * falling back to `created_at` only if the driver has never toggled at
 * all. A currently-online driver is never a candidate. Drivers whose
 * inactivity is already explained by tracked state (suspended, banned,
 * rejected, or the abandoned-onboarding `needs_review` bucket) are never
 * candidates either — this tool never double-counts that population.
 *
 * Same confirm-phrase guard as the pre-launch flagging tool: this writes
 * across production driver rows, so commit requires typing a confirmation
 * phrase even though the write itself is additive and low-risk.
 */

import { useState } from "react";
import { CheckCircle2, Copy, Loader2, Moon } from "lucide-react";
import {
    adminCommitDriverDormancy,
    adminPreviewDriverDormancy,
    type DriverDormancyCommitResult,
    type DriverDormancyReport,
} from "@/lib/api";
import { WhatThisToolDoes } from "@/components/bulk-import/what-this-tool-does";
import { StatTile } from "@/components/bulk-import/stat-tile";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";

const CONFIRM_PHRASE = "FLAG";

function buildSummaryText(report: DriverDormancyReport): string {
    const c = report.counts;
    const rows: [string, number][] = [
        ["Dormant (90+ days idle)", c.dormant_candidates],
        ["Long-dormant (365+ days idle)", c.long_dormant_candidates],
        ["Never activated", c.never_activated],
        ["Went dark", c.went_dark],
    ];
    const lines = [
        `Driver Dormancy Flagging — batch ${report.batch}`,
        "| Metric | Count |",
        "|---|---|",
        ...rows.map(([label, value]) => `| ${label} | ${value} |`),
    ];
    return lines.join("\n");
}

export function DriverDormancyFlag() {
    const { toast } = useToast();

    const [report, setReport] = useState<DriverDormancyReport | null>(null);
    const [committed, setCommitted] = useState<DriverDormancyCommitResult | null>(null);
    const [confirmText, setConfirmText] = useState("");
    const [previewing, setPreviewing] = useState(false);
    const [committing, setCommitting] = useState(false);

    const handlePreview = async () => {
        setPreviewing(true);
        setCommitted(null);
        setConfirmText("");
        try {
            setReport(await adminPreviewDriverDormancy());
        } catch (e) {
            setReport(null);
            toast({
                title: "Preview failed",
                description: e instanceof Error ? e.message : "Could not build the flag plan.",
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
            const res = await adminCommitDriverDormancy({ batch: report.batch });
            setCommitted(res);
            if (!res.committed) {
                setReport({ batch: res.batch, can_commit: res.can_commit ?? false, counts: res.counts });
                toast({
                    title: "Nothing left to flag",
                    description: "Every matching driver was already flagged (likely by a prior run).",
                });
            } else {
                setConfirmText("");
                const conflicts = res.conflicts ?? 0;
                toast({
                    title: conflicts > 0 ? "Flagged with some conflicts" : "Flagged",
                    description: `${res.drivers_flagged ?? 0} driver(s) flagged.${
                        conflicts > 0 ? ` ${conflicts} row(s) changed concurrently — re-run to pick them up.` : ""
                    }`,
                    variant: conflicts > 0 ? "destructive" : undefined,
                });
            }
        } catch (e) {
            toast({
                title: "Commit failed",
                description: e instanceof Error ? e.message : "The flag commit did not complete.",
                variant: "destructive",
            });
        } finally {
            setCommitting(false);
        }
    };

    const c = report?.counts;
    const totalCandidates = (c?.dormant_candidates ?? 0) + (c?.long_dormant_candidates ?? 0);
    const canCommit =
        Boolean(report?.can_commit) &&
        confirmText.trim().toUpperCase() === CONFIRM_PHRASE &&
        !committing &&
        !committed?.committed;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Moon className="h-5 w-5" />
                    Driver dormancy flagging
                </CardTitle>
                <CardDescription>
                    Flag driver profiles that have gone idle past a threshold, so admin views/KPIs
                    can filter them out of active-driver reporting.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <WhatThisToolDoes
                    what={
                        <>
                            Marks a driver as dormant (90+ days idle) or long-dormant (365+ days
                            idle) based on their last Go Online/Go Offline activity, so admin views
                            and KPIs can filter them out.
                        </>
                    }
                    why={
                        <>
                            A driver who created an account years ago and never came back still
                            counts toward total driver headcount today — without this flag, that
                            inflates driver counts and skews utilization metrics with accounts that
                            aren&apos;t really part of the active fleet.
                        </>
                    }
                    whichFiles={<>No file upload — this reads directly from production data already in Spinr.</>}
                    value={
                        <>
                            Admin dashboards and KPI reports can show real active-driver numbers,
                            not numbers padded with accounts that have been inactive for months or
                            years.
                        </>
                    }
                    safetyNote={
                        <>
                            Additive only — sets metadata flags, never deletes, deactivates,
                            suspends, or changes go-online eligibility. A driver currently online is
                            never flagged, no matter how old their history looks. Drivers already
                            explained by tracked state (suspended, banned, rejected, or still mid
                            abandoned-onboarding) are never flagged here either — that would just
                            double-count a population already tracked elsewhere.
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
                        <h3 className="text-sm font-medium">2. Review and commit</h3>

                        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                            <StatTile label="Dormant (90+ days)" value={c.dormant_candidates} />
                            <StatTile label="Long-dormant (365+ days)" value={c.long_dormant_candidates} />
                            <StatTile label="Never activated" value={c.never_activated} />
                            <StatTile label="Went dark" value={c.went_dark} />
                        </div>

                        <p className="text-xs text-muted-foreground">
                            Batch <span className="font-mono">{report.batch}</span>.
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
                                    {committed.drivers_flagged ?? 0} driver(s) flagged.
                                    {(committed.conflicts ?? 0) > 0
                                        ? ` ${committed.conflicts} row(s) changed concurrently and were skipped — re-run to pick them up.`
                                        : ""}
                                </span>
                            </div>
                        ) : report.can_commit ? (
                            <div className="space-y-2 rounded-md border p-3">
                                <Label htmlFor="driver-dormancy-confirm" className="text-xs">
                                    This flags {totalCandidates} driver(s) in production — additive
                                    only, but not undoable from here. Type{" "}
                                    <span className="font-mono">{CONFIRM_PHRASE}</span> to enable.
                                </Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="driver-dormancy-confirm"
                                        value={confirmText}
                                        onChange={(e) => setConfirmText(e.target.value)}
                                        placeholder={CONFIRM_PHRASE}
                                        className="max-w-40 font-mono"
                                    />
                                    <Button onClick={handleCommit} disabled={!canCommit}>
                                        {committing ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Flagging…
                                            </>
                                        ) : (
                                            "Apply flags"
                                        )}
                                    </Button>
                                </div>
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Nothing to flag — every matching driver has already been flagged.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
