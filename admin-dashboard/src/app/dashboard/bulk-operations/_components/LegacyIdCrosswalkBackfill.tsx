"use client";

/**
 * Legacy ID Crosswalk Backfill (Step 19) — populates legacy_id_crosswalk
 * (migration 328) so a future support/audit lookup can go straight from an
 * old-app ID (Mongo ObjectId or the Saskatoon numeric driver ID) to a Spinr
 * UUID, instead of re-deriving the phone-match by hand across
 * drivers/rides.
 *
 * Like Pre-Launch Legacy Data Flagging and Driver-Repair Pass, there is no
 * CSV to upload — this reads directly from production
 * (backend/services/legacy_id_crosswalk_service.py). Additive-only inserts
 * into a table nothing else writes to, so it skips the type-to-confirm
 * gate those two use for real state/money writes — the action here is
 * lower-stakes: a new lookup row, never an update to an existing one.
 *
 * This pass is sourced entirely from linkage Supabase already has, which
 * has a real ceiling: it can't recover an old id the original phone-match
 * missed. A rider whose own rides disagree on old_customer_id is reported
 * separately as "ambiguous" rather than guessed at.
 */

import { useState } from "react";
import { AlertTriangle, CheckCircle2, Link2, Loader2 } from "lucide-react";
import {
    adminCommitIdCrosswalkBackfill,
    adminPreviewIdCrosswalkBackfill,
    type CrosswalkBackfillCommitResult,
    type CrosswalkBackfillReport,
    type CrosswalkPopulationCounts,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";

function Stat({ label, value, tone }: { label: string; value: number; tone?: "warn" }) {
    const toneCls = tone === "warn" && value > 0 ? "text-warning" : "text-foreground";
    return (
        <div className="rounded-md border p-3">
            <div className={`text-2xl font-semibold ${toneCls}`}>{value}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
        </div>
    );
}

function PopulationSummary({ title, counts }: { title: string; counts: CrosswalkPopulationCounts }) {
    const eligible = counts.eligible_drivers_found ?? counts.eligible_riders_found ?? 0;
    return (
        <div className="space-y-2">
            <h4 className="text-xs font-medium text-muted-foreground">{title}</h4>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Eligible" value={eligible} />
                <Stat label="New rows to write" value={counts.new_rows_to_write} />
                <Stat label="Already recorded" value={counts.already_recorded} />
                {counts.ambiguous_skipped !== undefined ? (
                    <Stat label="Ambiguous (skipped)" value={counts.ambiguous_skipped} tone="warn" />
                ) : null}
            </div>
        </div>
    );
}

export function LegacyIdCrosswalkBackfill() {
    const { toast } = useToast();

    const [report, setReport] = useState<CrosswalkBackfillReport | null>(null);
    const [committed, setCommitted] = useState<CrosswalkBackfillCommitResult | null>(null);
    const [previewing, setPreviewing] = useState(false);
    const [committing, setCommitting] = useState(false);

    const handlePreview = async () => {
        setPreviewing(true);
        setCommitted(null);
        try {
            setReport(await adminPreviewIdCrosswalkBackfill());
        } catch (e) {
            setReport(null);
            toast({
                title: "Preview failed",
                description: e instanceof Error ? e.message : "Could not build the crosswalk plan.",
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
            const res = await adminCommitIdCrosswalkBackfill({ batch: report.batch });
            setCommitted(res);
            if (!res.committed) {
                setReport({
                    batch: res.batch,
                    can_commit: res.can_commit ?? false,
                    counts: res.counts,
                    ambiguous_riders: res.ambiguous_riders,
                });
                toast({
                    title: "Nothing left to record",
                    description: "Every id was already recorded (likely by a prior run).",
                });
            } else {
                const failed = res.failed ?? 0;
                toast({
                    title: failed > 0 ? "Recorded with some failures" : "Recorded",
                    description: `${res.written ?? 0} crosswalk row(s) written.${
                        failed > 0 ? ` ${failed} failed — see backend logs.` : ""
                    }`,
                    variant: failed > 0 ? "destructive" : undefined,
                });
            }
        } catch (e) {
            toast({
                title: "Commit failed",
                description: e instanceof Error ? e.message : "The crosswalk commit did not complete.",
                variant: "destructive",
            });
        } finally {
            setCommitting(false);
        }
    };

    const c = report?.counts;
    const newRows = c ? c.driver.new_rows_to_write + c.rider.new_rows_to_write : 0;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Link2 className="h-5 w-5" />
                    Legacy ID crosswalk backfill
                </CardTitle>
                <CardDescription>
                    Record which old-app ID(s) — Mongo ObjectId or the Saskatoon numeric driver
                    ID — each Spinr driver/rider resolved from, using the phone-match every
                    importer already ran. No CSV needed — this reads directly from production. A
                    rider whose own rides disagree on the old customer ID is reported as
                    ambiguous and skipped, never guessed.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
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
                        <h3 className="text-sm font-medium">2. Review and record</h3>

                        <PopulationSummary title="Drivers" counts={c.driver} />
                        <PopulationSummary title="Riders" counts={c.rider} />

                        <p className="text-xs text-muted-foreground">
                            Batch <span className="font-mono">{report.batch}</span>.
                        </p>

                        {report.ambiguous_riders > 0 ? (
                            <div className="flex items-start gap-2 rounded-md border border-warning bg-warning/10 p-3 text-sm">
                                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                                <span>
                                    {report.ambiguous_riders} rider(s) skipped — their own imported
                                    rides disagree on the old customer ID, so nothing was guessed.
                                </span>
                            </div>
                        ) : null}

                        {committed?.committed ? (
                            <div className="flex items-center gap-2 rounded-md border border-success bg-success/10 p-3 text-sm">
                                <CheckCircle2 className="h-4 w-4 text-success" />
                                <span>
                                    {committed.written ?? 0} crosswalk row(s) written.
                                    {(committed.failed ?? 0) > 0
                                        ? ` ${committed.failed} failed — see backend logs.`
                                        : ""}
                                </span>
                            </div>
                        ) : report.can_commit ? (
                            <Button onClick={handleCommit} disabled={committing}>
                                {committing ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        Recording…
                                    </>
                                ) : (
                                    `Record ${newRows} crosswalk row(s)`
                                )}
                            </Button>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Nothing new to record — every resolvable id has already been
                                recorded.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
