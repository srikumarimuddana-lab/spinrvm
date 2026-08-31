"use client";

/**
 * Driver-Repair Pass (Step 18) — re-matches completed rides still missing
 * a driver (migration_data_quality_service's `missing_driver` finding)
 * against the CURRENT drivers table, via
 * legacy_import_metadata.old_driver_id. A driver added in a later import
 * batch than the ride itself is exactly the case this recovers — the ride
 * never had a chance to match at its own import time.
 *
 * No CSV to upload — this operates entirely on production data already in
 * `rides`/`drivers` via backend/services/migration_driver_repair_service.py.
 *
 * **Driver-side only.** There is no rider-side equivalent: no `users` row
 * anywhere stores an old-system customer-id linkage, and the
 * `legacy_id_crosswalk` table built for that is still empty/unbackfilled.
 * A rider-side repair needs either the raw customers.csv export or that
 * crosswalk populated first — not buildable from Supabase data alone.
 *
 * **Not additive-only, unlike Data Quality Scan.** Commit sets
 * `rides.driver_id` plus reconstructs Period 2/3 `driver_insurance_periods`
 * rows and writes one offsetting `payouts` row per driver (so the driver's
 * live payable_balance isn't inflated by a trip already settled in the old
 * app — see the service module's docstring). Same confirm-phrase guard as
 * Legacy Wallet Import / Pre-Launch Data Flagging.
 */

import { useState } from "react";
import { CheckCircle2, Info, Loader2, Wrench } from "lucide-react";
import {
    adminCommitDriverRepair,
    adminPreviewDriverRepair,
    type DriverRepairCommitResult,
    type DriverRepairReport,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";

const CONFIRM_PHRASE = "REPAIR";

function Stat({ label, value }: { label: string; value: number }) {
    return (
        <div className="rounded-md border p-3">
            <div className={`text-2xl font-semibold ${value > 0 ? "text-warning" : "text-foreground"}`}>{value}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
        </div>
    );
}

export function DriverRepairPass() {
    const { toast } = useToast();

    const [report, setReport] = useState<DriverRepairReport | null>(null);
    const [committed, setCommitted] = useState<DriverRepairCommitResult | null>(null);
    const [confirmText, setConfirmText] = useState("");
    const [previewing, setPreviewing] = useState(false);
    const [committing, setCommitting] = useState(false);

    const handlePreview = async () => {
        setPreviewing(true);
        setCommitted(null);
        setConfirmText("");
        try {
            setReport(await adminPreviewDriverRepair());
        } catch (e) {
            setReport(null);
            toast({
                title: "Preview failed",
                description: e instanceof Error ? e.message : "Could not build the repair plan.",
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
            const res = await adminCommitDriverRepair({ batch: report.batch });
            setCommitted(res);
            if (!res.committed) {
                setReport({ batch: res.batch, can_commit: res.can_commit ?? false, counts: res.counts });
                toast({
                    title: "Nothing left to repair",
                    description: "Every repairable ride was already repaired (likely by a prior run).",
                });
            } else {
                setConfirmText("");
                const conflicts = res.conflicts ?? 0;
                toast({
                    title: conflicts > 0 ? "Repaired with some conflicts" : "Repaired",
                    description: `${res.rides_repaired ?? 0} ride(s) repaired, ${
                        res.drivers_recounted ?? 0
                    } driver(s) recounted.${
                        conflicts > 0 ? ` ${conflicts} ride(s) changed concurrently — re-run to pick them up.` : ""
                    }`,
                    variant: conflicts > 0 ? "destructive" : undefined,
                });
            }
        } catch (e) {
            toast({
                title: "Commit failed",
                description: e instanceof Error ? e.message : "The repair commit did not complete.",
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
                    <Wrench className="h-5 w-5" />
                    Driver-repair pass
                </CardTitle>
                <CardDescription>
                    Re-check rides still missing a driver against the drivers table as it stands
                    today — recovers rides whose driver was imported in a later batch than the
                    ride itself. No CSV needed — this reads directly from production. Driver-side
                    only; see the notice below for why there is no rider-side version yet.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="flex gap-2 rounded-md border border-muted bg-muted/30 p-3 text-sm">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="space-y-1 text-muted-foreground">
                        <p>
                            <span className="font-medium text-foreground">Not additive-only</span> —
                            commit sets the ride&apos;s driver, reconstructs its insurance-period
                            audit rows, and writes one offsetting payout per driver so their
                            payable balance isn&apos;t inflated by a trip already settled in the
                            old app.
                        </p>
                        <p>
                            An old driver id claimed by more than one current driver is never
                            guessed at — it&apos;s excluded as ambiguous, not linked to either.
                        </p>
                        <p>
                            <span className="font-medium text-foreground">No rider-side version</span>{" "}
                            exists yet — no old-system customer id is stored on any rider account
                            in Supabase. That needs either the raw customers.csv export or the
                            (currently empty) legacy_id_crosswalk table backfilled first.
                        </p>
                    </div>
                </div>

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
                        <h3 className="text-sm font-medium">2. Review and repair</h3>

                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <Stat label="Repairable now" value={c.repairable} />
                            <Stat label="Still unmatched" value={c.still_unmatched} />
                            <Stat label="Ambiguous (skipped)" value={c.ambiguous_old_driver_id_skipped} />
                            <Stat label="Candidates scanned" value={c.rides_missing_driver_with_old_id} />
                        </div>

                        <p className="text-xs text-muted-foreground">
                            Batch <span className="font-mono">{report.batch}</span>.
                        </p>

                        {committed?.committed ? (
                            <div className="flex items-center gap-2 rounded-md border border-success bg-success/10 p-3 text-sm">
                                <CheckCircle2 className="h-4 w-4 text-success" />
                                <span>
                                    {committed.rides_repaired ?? 0} ride(s) repaired,{" "}
                                    {committed.drivers_recounted ?? 0} driver(s) recounted.
                                    {(committed.conflicts ?? 0) > 0
                                        ? ` ${committed.conflicts} ride(s) changed concurrently and were skipped — re-run to pick them up.`
                                        : ""}
                                </span>
                            </div>
                        ) : report.can_commit ? (
                            <div className="space-y-2 rounded-md border p-3">
                                <Label htmlFor="driver-repair-confirm" className="text-xs">
                                    This links {c.repairable} ride(s) to a driver, backdates their
                                    insurance-period audit rows, and writes offsetting payouts in
                                    production — not undoable from here. Type{" "}
                                    <span className="font-mono">{CONFIRM_PHRASE}</span> to enable.
                                </Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="driver-repair-confirm"
                                        value={confirmText}
                                        onChange={(e) => setConfirmText(e.target.value)}
                                        placeholder={CONFIRM_PHRASE}
                                        className="max-w-40 font-mono"
                                    />
                                    <Button onClick={handleCommit} disabled={!canCommit}>
                                        {committing ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                Repairing…
                                            </>
                                        ) : (
                                            "Apply repairs"
                                        )}
                                    </Button>
                                </div>
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                Nothing to repair — every currently-matchable ride has already been
                                repaired.
                            </p>
                        )}
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
