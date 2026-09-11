"use client";

/**
 * Pre-Launch Legacy Data Flagging — flags already-migrated production
 * rows that predate Spinr's 2026-03-30 public launch as
 * `legacy_import_metadata.pre_launch_test = true`, so admin views/KPIs can
 * filter them out.
 *
 * Unlike every other tool on this page, there is no CSV to upload — this
 * operates entirely on production data already in `drivers`/`rides` via
 * backend/services/pre_launch_flag_service.py. Additive only: sets one new
 * JSONB key per matched row, never deletes or deactivates anything.
 *
 * Scope (see the service module's own docstring for the full reasoning):
 * - Drivers: only a driver created before launch AND with zero rides ever
 *   driven AND zero driver_insurance_periods rows — a driver who onboarded
 *   pre-launch but has since driven a real ride is left untouched.
 * - Riders: a legacy-imported rider with zero rides ever taken — the same
 *   zero-activity principle as drivers, minus the insurance-period signal
 *   riders don't have. created_at is not used as a gate for riders (their
 *   import doesn't preserve the original old-app signup date the way
 *   drivers' does).
 * - Rides: every ride created before launch (no real customer base existed
 *   to serve before launch, so there is no comparable ambiguity).
 *
 * Same confirm-phrase guard as the wallet importer: this writes across two
 * core production tables, so commit requires typing a confirmation phrase
 * even though the write itself is additive and low-risk.
 */

import { useState } from "react";
import { CheckCircle2, Copy, Loader2, Tag } from "lucide-react";
import {
    adminCommitPreLaunchFlag,
    adminPreviewPreLaunchFlag,
    type PreLaunchFlagCommitResult,
    type PreLaunchFlagReport,
} from "@/lib/api";
import { WhatThisToolDoes } from "@/components/bulk-import/what-this-tool-does";
import { StatTile } from "@/components/bulk-import/stat-tile";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";

const CONFIRM_PHRASE = "FLAG";

// Compact, copy-pasteable markdown table mirroring the stat tiles below.
function buildSummaryText(report: PreLaunchFlagReport): string {
    const c = report.counts;
    const rows: [string, number][] = [
        ["Dormant pre-launch drivers", c.driver_candidates],
        ["Dormant pre-launch riders", c.rider_candidates],
        ["Pre-launch rides", c.ride_candidates],
    ];
    const lines = [
        `Pre-Launch Legacy Data Flagging — batch ${report.batch}`,
        "| Metric | Count |",
        "|---|---|",
        ...rows.map(([label, value]) => `| ${label} | ${value} |`),
    ];
    return lines.join("\n");
}

export function PreLaunchDataFlag() {
    const { toast } = useToast();

    const [report, setReport] = useState<PreLaunchFlagReport | null>(null);
    const [committed, setCommitted] = useState<PreLaunchFlagCommitResult | null>(null);
    const [confirmText, setConfirmText] = useState("");
    const [previewing, setPreviewing] = useState(false);
    const [committing, setCommitting] = useState(false);

    const handlePreview = async () => {
        setPreviewing(true);
        setCommitted(null);
        setConfirmText("");
        try {
            setReport(await adminPreviewPreLaunchFlag());
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
            const res = await adminCommitPreLaunchFlag({ batch: report.batch });
            setCommitted(res);
            if (!res.committed) {
                setReport({ batch: res.batch, can_commit: res.can_commit ?? false, counts: res.counts });
                toast({
                    title: "Nothing left to flag",
                    description: "Every matching row was already flagged (likely by a prior run).",
                });
            } else {
                setConfirmText("");
                const conflicts =
                    (res.driver_conflicts ?? 0) + (res.rider_conflicts ?? 0) + (res.ride_conflicts ?? 0);
                toast({
                    title: conflicts > 0 ? "Flagged with some conflicts" : "Flagged",
                    description: `${res.drivers_flagged ?? 0} driver(s), ${res.riders_flagged ?? 0} rider(s), ${
                        res.rides_flagged ?? 0
                    } ride(s) flagged.${
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
    const canCommit =
        Boolean(report?.can_commit) &&
        confirmText.trim().toUpperCase() === CONFIRM_PHRASE &&
        !committing &&
        !committed?.committed;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Tag className="h-5 w-5" />
                    Pre-launch legacy data flagging
                </CardTitle>
                <CardDescription>
                    Flag already-migrated driver/rider profiles and rides that predate Spinr&apos;s
                    public launch.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <WhatThisToolDoes
                    what={
                        <>
                            Marks already-migrated driver/rider profiles and rides that predate
                            Spinr&apos;s 2026-03-30 public launch as pre-launch test data, so admin
                            views/KPIs can filter them out.
                        </>
                    }
                    why={
                        <>
                            Data imported from before launch was never real production activity —
                            without this flag, it would inflate driver counts, ride counts, and
                            other KPIs with rows that never represented a real customer.
                        </>
                    }
                    whichFiles={<>No file upload — this reads directly from production data already in Spinr.</>}
                    value={
                        <>
                            Admin dashboards and KPI reports show real activity numbers, not
                            numbers padded with pre-launch test data.
                        </>
                    }
                    safetyNote={
                        <>
                            Additive only — sets one new metadata flag per matched row, never
                            deletes or deactivates anything. A driver is only flagged if created
                            before launch <span className="font-medium">and</span> has never driven
                            a ride or held an insurance period — a driver who onboarded pre-launch
                            but has since driven a real ride is left untouched. A rider is only
                            flagged if they have never taken a single ride. Every ride created
                            before launch is flagged (no real customer base existed before launch).
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

                        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                            <StatTile label="Dormant pre-launch drivers" value={c.driver_candidates} />
                            <StatTile label="Dormant pre-launch riders" value={c.rider_candidates} />
                            <StatTile label="Pre-launch rides" value={c.ride_candidates} />
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
                                    {committed.drivers_flagged ?? 0} driver(s), {committed.riders_flagged ?? 0}{" "}
                                    rider(s), {committed.rides_flagged ?? 0} ride(s) flagged.
                                    {(committed.driver_conflicts ?? 0) +
                                        (committed.rider_conflicts ?? 0) +
                                        (committed.ride_conflicts ?? 0) >
                                    0
                                        ? ` ${
                                              (committed.driver_conflicts ?? 0) +
                                              (committed.rider_conflicts ?? 0) +
                                              (committed.ride_conflicts ?? 0)
                                          } row(s) changed concurrently and were skipped — re-run to pick them up.`
                                        : ""}
                                </span>
                            </div>
                        ) : report.can_commit ? (
                            <div className="space-y-2 rounded-md border p-3">
                                <Label htmlFor="pre-launch-flag-confirm" className="text-xs">
                                    This flags {c.driver_candidates} driver(s), {c.rider_candidates} rider(s), and{" "}
                                    {c.ride_candidates} ride(s) in production — additive only, but not undoable from
                                    here. Type <span className="font-mono">{CONFIRM_PHRASE}</span> to enable.
                                </Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="pre-launch-flag-confirm"
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
                                Nothing to flag — every matching row has already been flagged.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
