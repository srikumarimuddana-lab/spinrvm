// Payouts tab of the drivers detail slideout. Pure code motion out of
// drivers/page.tsx (design-audit follow-up,
// docs/change-log/2026-09-04-breakup-drivers-page-god-component.md) — no
// logic changes.

import { useState, useEffect } from "react";
import type { DriverPayoutSummary } from "@/lib/api";
import { Pagination } from "@/components/ui/pagination";
import { formatCurrency } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { DollarSign, CreditCard, Shield, Eye, Copy, RefreshCw, Loader2, AlertTriangle, CheckCircle, Clock } from "lucide-react";
import { DriverStatementsPanel } from "./driver-statements-panel";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";

// "processing" has no dedicated semantic token (only success/warning/destructive
// exist), so this stays a hand-picked palette rather than a partial conversion. (#2816)
/* eslint-disable no-restricted-syntax -- categorical payout-status map, no semantic token for "processing" (#2816) */
export const PAYOUT_STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
    completed:  { bg: "bg-success/15", text: "text-success", label: "Paid" },
    pending:    { bg: "bg-warning/15", text: "text-warning", label: "Pending" },
    processing: { bg: "bg-blue-100 dark:bg-blue-900/30",       text: "text-blue-700 dark:text-blue-300",       label: "Processing" },
    failed:     { bg: "bg-destructive/15", text: "text-destructive", label: "Failed" },
};
/* eslint-enable no-restricted-syntax */

// Quiet Console Stage 3: flag-on Badge variant per payout status — reuses
// the existing success/warning/destructive tokens for the three states that
// already carry them above, and falls "processing" back to plain `outline`
// instead of inventing a quiet blue. PAYOUT_STATUS_STYLE itself is untouched.
export const PAYOUT_STATUS_BADGE_VARIANT: Record<string, "outline" | "outline-success" | "outline-warning" | "outline-destructive"> = {
    completed: "outline-success",
    pending: "outline-warning",
    processing: "outline",
    failed: "outline-destructive",
};

export function PayoutMetric({ label, value, tone, sub }: { label: string; value: string; tone?: "emerald" | "amber" | "red" | "neutral"; sub?: string }) {
    const styles = {
        emerald: { bg: "bg-success/10 border-success/30", value: "text-success" },
        amber:   { bg: "bg-warning/10 border-warning/30", value: "text-warning" },
        red:     { bg: "bg-destructive/10 border-destructive/30", value: "text-destructive" },
        neutral: { bg: "bg-card border-border",                                                            value: "text-foreground" },
    }[tone ?? "neutral"];
    return (
        <div className={`rounded-xl p-3.5 border ${styles.bg}`}>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">{label}</p>
            <p className={`text-xl font-bold tabular-nums mt-1 ${styles.value}`}>{value}</p>
            {sub && <p className="text-[10px] text-muted-foreground mt-0.5">{sub}</p>}
        </div>
    );
}

export function DriverPayoutsTab({ data, loading, driverId, driverName, isLegacyImported, retryingPayoutId, onRetry, onRefreshKyc, onRefreshPayouts, onRevealSin, refreshingKyc, refreshingPayouts, revealedSin, canRevealSin, canRefreshPayouts, notify }: {
    data: DriverPayoutSummary | null;
    loading: boolean;
    driverId: string;
    driverName: string;
    // True when selected.legacy_import_metadata is non-empty -- distinguishes
    // "migrated driver whose old-app banking data has no import destination"
    // (banks.csv was never imported, ACTION_ITEMS.md A34 -- permanent, expected)
    // from "new driver who hasn't finished onboarding" (transient, self-serve).
    isLegacyImported: boolean;
    retryingPayoutId: string | null;
    onRetry: (payoutId: string) => Promise<void>;
    onRefreshKyc: () => Promise<void>;
    onRefreshPayouts: () => Promise<void>;
    onRevealSin: () => Promise<void>;
    refreshingKyc: boolean;
    refreshingPayouts: boolean;
    revealedSin: { sin: string; expiresAt: number } | null;
    canRevealSin: boolean;
    canRefreshPayouts: boolean;
    notify: (opts: { title: string; description?: string; variant?: "destructive" }) => void;
}) {
    // Quiet Console Stage 3: gates the flag-on Badge alternate for the
    // payout-status pill below — PAYOUT_STATUS_STYLE stays the flag-off path.
    const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");
    const fmtDateTime = (iso?: string | null) => {
        if (!iso) return "—";
        try {
            const d = new Date(iso);
            return `${d.toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" })} · ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
        } catch { return iso; }
    };
    const fmtMoney = (n: number) => formatCurrency(n);

    // Client-side sort for the payout-history table. Called before the early
    // returns so the hook order stays stable; reads payouts off the nullable
    // data and falls back to an empty list when not yet loaded.
    const { sorted: sortedPayouts, sort: payoutsSort, toggle: togglePayoutsSort } = useTableSort(data?.payouts ?? []);
    const [payoutPage, setPayoutPage] = useState(0);
    const [payoutPageSize, setPayoutPageSize] = useState<number>(25);
    useEffect(() => { setPayoutPage(0); }, [payoutsSort]);

    if (loading && !data) return (
        <div className="space-y-4 animate-pulse">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-20 rounded-xl bg-muted" />)}
            </div>
            <div className="h-32 rounded-xl bg-muted" />
            {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-12 rounded-xl bg-muted" />)}
        </div>
    );

    if (!data) return (
        <div className="py-16 text-center text-muted-foreground">
            <DollarSign className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm font-medium">No payout data available</p>
        </div>
    );

    const { summary, payment_method: pm, payouts } = data;
    const bonuses = data.bonuses ?? [];
    const hasBonuses = (summary.lifetime_bonuses ?? 0) > 0;

    return (
        <div className="space-y-5">
            {/* Refresh Payouts from Stripe — super_admin only, matching the
                backend gate; lower roles never see a button that can only 403. */}
            {canRefreshPayouts && (
                <div className="flex items-center justify-end">
                    <Button
                        variant="outline"
                        size="sm"
                        className="h-8 text-xs"
                        disabled={refreshingPayouts}
                        onClick={onRefreshPayouts}
                        title="Pull all Transfers, bank payouts, and balance transactions from Stripe for this driver"
                    >
                        {refreshingPayouts ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> : <RefreshCw className="h-3.5 w-3.5 mr-1.5" />}
                        Refresh Payouts from Stripe
                    </Button>
                </div>
            )}

            {/* Migrated-driver context. One sentence up front beats four
                cards that each look wrong for a different reason: without
                this, "$0.00 lifetime" sits beside a Rides tab showing
                previous-app trips and a paid-out card full of previous-app
                transfers, and every number invites a support escalation. */}
            {((summary.imported_rides_excluded ?? 0) > 0 || (summary.legacy_stripe_transfers ?? 0) > 0) && (
                // eslint-disable-next-line no-restricted-syntax -- informational note, not a success/warning/destructive signal (#2816)
                <div className="rounded-xl border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/10 p-3 flex items-start gap-3">
                    {/* eslint-disable-next-line no-restricted-syntax -- informational note, not a success/warning/destructive signal (#2816) */}
                    <Clock className="h-4 w-4 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
                    {/* eslint-disable-next-line no-restricted-syntax -- informational note, not a success/warning/destructive signal (#2816) */}
                    <p className="text-xs text-blue-700 dark:text-blue-300">
                        <span className="font-semibold">Migrated from the previous app.</span>{" "}
                        {(summary.imported_rides_excluded ?? 0) > 0 && `${summary.imported_rides_excluded} imported ride${(summary.imported_rides_excluded ?? 0) === 1 ? "" : "s"}`}
                        {(summary.imported_rides_excluded ?? 0) > 0 && (summary.legacy_stripe_transfers ?? 0) > 0 && " and "}
                        {(summary.legacy_stripe_transfers ?? 0) > 0 && `${fmtMoney(summary.legacy_stripe_transfers ?? 0)} in previous-app payouts`}
                        {" "}are kept for history and tax, but are not counted in Spinr earnings or the owed balance below.
                    </p>
                </div>
            )}

            {/* Top 4 metric cards: Pending / Paid out / Lifetime / YTD */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <PayoutMetric
                    label="Pending payout"
                    value={fmtMoney(summary.pending_balance)}
                    tone={summary.pending_balance > 0 ? "amber" : "neutral"}
                    sub={summary.pending_in_flight > 0 ? `${fmtMoney(summary.pending_in_flight)} in flight` : "Owed to driver, not yet queued"}
                />
                <PayoutMetric
                    label="Total paid out"
                    value={fmtMoney(summary.total_paid_out)}
                    tone="emerald"
                    // legacy_stripe_transfers is a slice OF this total, not an
                    // addition to it — say "incl.", never "+". When the whole
                    // total is previous-app money, say THAT: "incl. $327.02"
                    // under a $327.02 headline reads like double-speak.
                    sub={(summary.legacy_stripe_transfers ?? 0) > 0
                        ? ((summary.legacy_stripe_transfers ?? 0) >= summary.total_paid_out
                            ? `${payouts.filter(p => p.status === "completed").length} payouts · all paid by the previous app`
                            : `${payouts.filter(p => p.status === "completed").length} payouts · incl. ${fmtMoney(summary.legacy_stripe_transfers ?? 0)} from the previous app`)
                        : `${payouts.filter(p => p.status === "completed").length} completed payouts`}
                />
                <PayoutMetric
                    label="Lifetime earnings"
                    value={fmtMoney(summary.lifetime_earnings)}
                    // Spell out imported rides. Otherwise this reads "0
                    // completed rides · $0.00" next to a Rides tab showing 15,
                    // and nothing explains why — which looks like data loss.
                    sub={(summary.imported_rides_excluded ?? 0) > 0
                        ? `${summary.rides_count.toLocaleString()} Spinr rides · ${summary.imported_rides_excluded} imported from previous app (not counted)`
                        : hasBonuses
                            ? `${fmtMoney(summary.lifetime_ride_earnings ?? 0)} rides + ${fmtMoney(summary.lifetime_bonuses ?? 0)} bonuses · ${fmtMoney(summary.lifetime_tips)} tips`
                            : `${summary.rides_count.toLocaleString()} completed rides · ${fmtMoney(summary.lifetime_tips)} tips`
                    }
                />
                <PayoutMetric
                    label="Year to date"
                    value={fmtMoney(summary.ytd_earnings)}
                    sub={`${summary.active_days_30d} active days in last 30d`}
                />
            </div>

            {/* On-hold warning if any failed payouts */}
            {summary.on_hold > 0 && summary.last_failed_payout && (
                <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 flex items-start gap-3">
                    <AlertTriangle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
                    <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-destructive">{fmtMoney(summary.on_hold)} on hold from failed payouts</p>
                        <p className="text-xs text-destructive/80 mt-0.5">
                            Most recent failure: {summary.last_failed_payout.error_message || "Unknown error"} · {fmtDateTime(summary.last_failed_payout.created_at)}
                        </p>
                    </div>
                </div>
            )}

            {/* Payment method on file */}
            <div className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="px-4 py-3 border-b border-border flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <CreditCard className="h-4 w-4 text-muted-foreground" />
                        <h3 className="text-sm font-semibold">Payout method</h3>
                    </div>
                    {pm.has_bank_account ? (
                        <Badge className="bg-success/15 text-success text-[10px]">Linked</Badge>
                    ) : (
                        <Badge className="bg-destructive/15 text-destructive text-[10px]">No method linked</Badge>
                    )}
                </div>
                <div className="px-4 py-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {pm.has_bank_account ? (
                        <>
                            <div>
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Bank</p>
                                <p className="text-sm font-medium mt-0.5">{pm.bank_name || "Stripe Connect"}</p>
                            </div>
                            <div>
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Account</p>
                                <p className="text-sm font-medium mt-0.5 font-mono">
                                    •••• {pm.account_last4 || "****"}
                                    {pm.account_type && <span className="text-xs text-muted-foreground ml-2 font-sans">({pm.account_type})</span>}
                                </p>
                            </div>
                            {pm.account_holder_name && (
                                <div>
                                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Holder</p>
                                    <p className="text-sm font-medium mt-0.5">{pm.account_holder_name}</p>
                                </div>
                            )}
                            <div>
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Stripe Connect</p>
                                {pm.stripe_connected ? (
                                    <p className="text-sm font-medium mt-0.5 inline-flex items-center gap-1.5">
                                        <span className="w-2 h-2 rounded-full bg-success" />
                                        Connected
                                        {pm.stripe_account_hint && <span className="text-xs text-muted-foreground font-mono">acct…{pm.stripe_account_hint}</span>}
                                    </p>
                                ) : (
                                    <p className="text-sm font-medium mt-0.5 inline-flex items-center gap-1.5 text-muted-foreground">
                                        <span className="w-2 h-2 rounded-full bg-muted-foreground/40" />
                                        Not connected
                                    </p>
                                )}
                            </div>
                            {pm.is_verified !== null && (
                                <div>
                                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Verification</p>
                                    <p className={`text-sm font-medium mt-0.5 ${pm.is_verified ? "text-success" : "text-warning"}`}>
                                        {pm.is_verified ? "Verified" : "Unverified"}
                                    </p>
                                </div>
                            )}
                        </>
                    ) : isLegacyImported ? (
                        <div className="col-span-full text-sm text-muted-foreground py-2">
                            {driverName}&rsquo;s payout method wasn&rsquo;t migrated from the previous app -- its raw
                            banking data was never imported (a known, permanent gap for legacy drivers, not a bug).
                            Ask {driverName} to add a bank account or Stripe Connect account to resume payouts.
                        </div>
                    ) : (
                        <div className="col-span-full text-sm text-muted-foreground py-2">
                            {driverName} has not added a payout method yet. Payouts cannot be processed until a bank account or Stripe Connect account is linked.
                        </div>
                    )}
                </div>
            </div>

            {/* Tax & Identity (Stripe Connect KYC mirror) */}
            <div className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                        <Shield className="h-4 w-4 text-muted-foreground" />
                        <h3 className="text-sm font-semibold">Tax &amp; Identity</h3>
                        {data.kyc.payouts_enabled ? (
                            <Badge className="bg-success/15 text-success text-[10px]">Verified</Badge>
                        ) : data.kyc.details_submitted ? (
                            <Badge className="bg-warning/15 text-warning text-[10px]">Pending Stripe review</Badge>
                        ) : data.kyc.requirements_due.length > 0 || data.kyc.requirements_past_due.length > 0 ? (
                            <Badge className="bg-destructive/15 text-destructive text-[10px]">Action required</Badge>
                        ) : (
                            <Badge variant="outline" className="text-[10px]">Not started</Badge>
                        )}
                    </div>
                    <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        disabled={refreshingKyc}
                        onClick={onRefreshKyc}
                        title="Pull latest from Stripe"
                    >
                        {refreshingKyc ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
                        Refresh
                    </Button>
                </div>
                <div className="px-4 py-3 space-y-3">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div>
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">SIN</p>
                            {/* Gate on OUR Vault-encrypted copy (sin_on_file), not Stripe's
                                id_number_provided mirror: the reveal endpoint decrypts our
                                column (Stripe's is write-only), and the SIN-before-Stripe
                                flow means drivers now have a SIN on file long before Stripe
                                does. Gating on Stripe's flag hid the Reveal button for
                                exactly those drivers — and showed it for legacy drivers
                                whose reveal call can only 400. */}
                            {data.kyc.sin_on_file ? (
                                <div className="flex items-center gap-2 mt-0.5">
                                    {revealedSin ? (
                                        <p className="text-sm font-medium font-mono">{revealedSin.sin}</p>
                                    ) : (
                                        <p className="text-sm font-medium font-mono">•••-•••-{data.kyc.sin_last4 || "•••"}</p>
                                    )}
                                    {canRevealSin ? (
                                        <Button
                                            size="xs"
                                            variant="outline"
                                            className="h-6 text-[10px] px-2"
                                            onClick={onRevealSin}
                                            disabled={!!revealedSin}
                                            title="One-shot decrypt of Spinr's encrypted copy. Every reveal writes an audit log entry."
                                        >
                                            {revealedSin ? <CheckCircle className="h-3 w-3 mr-1" /> : <Eye className="h-3 w-3 mr-1" />}
                                            {revealedSin ? "Shown" : "Reveal"}
                                        </Button>
                                    ) : (
                                        <span className="text-[10px] text-muted-foreground italic" title="Only super admins can retrieve the full SIN. Contact a super admin if you need this for tax filing.">
                                            super_admin only
                                        </span>
                                    )}
                                </div>
                            ) : data.kyc.id_number_provided ? (
                                <p
                                    className="text-sm text-muted-foreground mt-0.5"
                                    title="This driver entered their SIN into Stripe's own form before Spinr collected SINs in-app. Stripe never returns it (write-only), so it cannot be revealed here — use the tax-ID import or ask the driver via support to get a copy on file for the T4A."
                                >
                                    Held by Stripe only — not revealable
                                </p>
                            ) : (
                                <p className="text-sm text-muted-foreground mt-0.5">Not provided yet</p>
                            )}
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">GST/HST number</p>
                            {data.kyc.gst_hst_number ? (
                                <div className="flex items-center gap-2 mt-0.5">
                                    <p className="text-sm font-medium font-mono">{data.kyc.gst_hst_number}</p>
                                    <button
                                        type="button"
                                        onClick={() => navigator.clipboard.writeText(data.kyc.gst_hst_number!)}
                                        className="text-muted-foreground hover:text-foreground"
                                        title="Copy GST/HST number"
                                    >
                                        <Copy className="h-3 w-3" />
                                    </button>
                                </div>
                            ) : (
                                <p className="text-sm text-muted-foreground mt-0.5">Not registered</p>
                            )}
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Onboarding</p>
                            <p className="text-sm font-medium mt-0.5">
                                {data.kyc.details_submitted ? "Submitted to Stripe" : "Incomplete"}
                            </p>
                        </div>
                        <div>
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Stripe ToS</p>
                            <p className="text-sm font-medium mt-0.5">
                                {data.kyc.tos_accepted_at ? fmtDateTime(data.kyc.tos_accepted_at) : "Not accepted"}
                            </p>
                        </div>
                    </div>

                    {(data.kyc.requirements_due.length > 0 || data.kyc.requirements_past_due.length > 0) && (
                        <div className="rounded-md border border-warning/30 bg-warning/10 p-2.5">
                            <p className="text-[11px] font-semibold text-warning mb-1">
                                {data.kyc.requirements_past_due.length > 0 && `${data.kyc.requirements_past_due.length} past due · `}
                                {data.kyc.requirements_due.length} item{data.kyc.requirements_due.length === 1 ? "" : "s"} needed from driver
                            </p>
                            <ul className="text-[11px] text-warning/80 space-y-0.5">
                                {[...data.kyc.requirements_past_due, ...data.kyc.requirements_due].slice(0, 6).map((req) => (
                                    <li key={req} className="font-mono">{req}</li>
                                ))}
                                {data.kyc.requirements_due.length + data.kyc.requirements_past_due.length > 6 && (
                                    <li className="text-warning/60 italic">…and {data.kyc.requirements_due.length + data.kyc.requirements_past_due.length - 6} more</li>
                                )}
                            </ul>
                        </div>
                    )}

                    {data.kyc.disabled_reason && (
                        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2.5">
                            <p className="text-[11px] font-semibold text-destructive">
                                Payouts disabled: <span className="font-mono">{data.kyc.disabled_reason}</span>
                            </p>
                        </div>
                    )}

                    {revealedSin && (
                        <div className="rounded-md border border-warning/30 bg-warning/10 p-2.5">
                            <p className="text-[11px] text-warning">
                                <AlertTriangle className="h-3 w-3 inline mr-1" />
                                SIN revealed at {new Date(revealedSin.expiresAt - 30000).toLocaleTimeString()}.
                                Auto-hides in {Math.max(0, Math.ceil((revealedSin.expiresAt - Date.now()) / 1000))}s.
                                Never paste this anywhere — audit log captured the reveal.
                            </p>
                        </div>
                    )}

                    {data.kyc.last_synced_at && (
                        <p className="text-[10px] text-muted-foreground">Last synced: {fmtDateTime(data.kyc.last_synced_at)}</p>
                    )}
                </div>
            </div>

            {/* Last payout highlight */}
            {summary.last_payout && (
                <div className="rounded-xl border border-success/30 bg-success/10 p-3 flex items-center gap-3">
                    <CheckCircle className="h-4 w-4 text-success shrink-0" />
                    <div className="flex-1 min-w-0">
                        <p className="text-xs text-success">
                            <span className="font-semibold">Last payout:</span> {fmtMoney(summary.last_payout.amount)}
                            {summary.last_payout.bank_name && ` to ${summary.last_payout.bank_name} ••••${summary.last_payout.account_last4 || ""}`}
                        </p>
                        <p className="text-[10px] text-success/70">{fmtDateTime(summary.last_payout.processed_at)}</p>
                    </div>
                </div>
            )}

            {/* Bonuses (quest/referral/adjustment) */}
            {bonuses.length > 0 && (
                <div className="rounded-xl border border-border overflow-x-auto">
                    <div className="px-4 py-3 border-b border-border flex items-center gap-2">
                        <DollarSign className="h-4 w-4 text-muted-foreground" />
                        <h3 className="text-sm font-semibold">Bonuses &amp; Adjustments</h3>
                        <Badge variant="outline" className="text-[10px] ml-auto">{bonuses.length} entries</Badge>
                    </div>
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-muted/30 hover:bg-muted/30">
                                <TableHead className="h-9 text-[11px] uppercase tracking-wider">Date</TableHead>
                                <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right">Amount</TableHead>
                                <TableHead className="h-9 text-[11px] uppercase tracking-wider">Type</TableHead>
                                <TableHead className="h-9 text-[11px] uppercase tracking-wider">Description</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {bonuses.map((b) => (
                                <TableRow key={b.id} className="hover:bg-muted/20">
                                    <TableCell className="text-xs whitespace-nowrap">{fmtDateTime(b.created_at)}</TableCell>
                                    <TableCell className="text-sm font-semibold text-right tabular-nums">{fmtMoney(b.amount)}</TableCell>
                                    <TableCell>
                                        <Badge variant="outline" className="text-[10px]">{b.kind}</Badge>
                                    </TableCell>
                                    <TableCell className="text-xs text-muted-foreground">{b.description || "—"}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {/* Earnings statements — date-filtered download / email to driver */}
            <DriverStatementsPanel driverId={driverId} driverName={driverName} notify={notify} />

            {/* Payout history table */}
            <div className="rounded-xl border border-border overflow-x-auto">
                <Table>
                    <TableHeader>
                        <TableRow className="bg-muted/30 hover:bg-muted/30">
                            <SortableHead column="processed_at" sort={payoutsSort} onSort={togglePayoutsSort} className="h-9 text-[11px] uppercase tracking-wider">Date</SortableHead>
                            <SortableHead column="amount" sort={payoutsSort} onSort={togglePayoutsSort} align="right" className="h-9 text-[11px] uppercase tracking-wider">Amount</SortableHead>
                            <SortableHead column="status" sort={payoutsSort} onSort={togglePayoutsSort} className="h-9 text-[11px] uppercase tracking-wider">Status</SortableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider">Type</TableHead>
                            <SortableHead column="bank_name" sort={payoutsSort} onSort={togglePayoutsSort} className="h-9 text-[11px] uppercase tracking-wider">Destination</SortableHead>
                            <SortableHead column="stripe_payout_id" sort={payoutsSort} onSort={togglePayoutsSort} className="h-9 text-[11px] uppercase tracking-wider">Stripe Ref</SortableHead>
                            <TableHead className="h-9 text-[11px] uppercase tracking-wider text-right">Action</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {payouts.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={7} className="text-center text-muted-foreground py-12">
                                    <p className="text-sm">No payouts yet.</p>
                                    <p className="text-xs mt-1">When {driverName} requests a withdrawal it will appear here.</p>
                                </TableCell>
                            </TableRow>
                        ) : sortedPayouts.slice(payoutPage * payoutPageSize, (payoutPage + 1) * payoutPageSize).map((p) => {
                            const style = PAYOUT_STATUS_STYLE[p.status] ?? { bg: "bg-muted/30", text: "text-muted-foreground", label: p.status };
                            const isRetrying = retryingPayoutId === p.id;
                            const typeLabel =
                                p.payout_type === "stripe_sync" ? "Stripe Transfer"
                                : p.payout_type === "instant" ? "Instant"
                                : p.payout_type === "legacy_import" ? "Legacy import"
                                : !p.payout_type || p.payout_type === "standard" ? "Standard"
                                : p.payout_type;
                            return (
                                <TableRow key={p.id} className="hover:bg-muted/20">
                                    <TableCell className="text-xs whitespace-nowrap">
                                        {fmtDateTime(p.processed_at || p.created_at)}
                                    </TableCell>
                                    <TableCell className="text-sm font-semibold text-right tabular-nums">
                                        {fmtMoney(p.amount)}
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex flex-col gap-0.5">
                                            {themeV2Enabled ? (
                                                <Badge variant={PAYOUT_STATUS_BADGE_VARIANT[p.status] ?? "outline"} className="text-[10px] w-fit">{style.label}</Badge>
                                            ) : (
                                                <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold w-fit ${style.bg} ${style.text}`}>
                                                    {style.label}
                                                </span>
                                            )}
                                            {p.status === "failed" && p.error_message && (
                                                <span className="text-[10px] text-destructive truncate max-w-[200px]" title={p.error_message}>{p.error_message}</span>
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        <Badge variant="outline" className="text-[10px]">{typeLabel}</Badge>
                                    </TableCell>
                                    <TableCell className="text-xs">
                                        {p.bank_name ? (
                                            <>
                                                <p>{p.bank_name}</p>
                                                {p.account_last4 && <p className="text-[10px] text-muted-foreground font-mono">••••{p.account_last4}</p>}
                                            </>
                                        ) : "—"}
                                    </TableCell>
                                    <TableCell>
                                        {(p.stripe_transfer_id || p.stripe_payout_id) ? (
                                            <button
                                                type="button"
                                                onClick={() => navigator.clipboard.writeText(p.stripe_transfer_id || p.stripe_payout_id || "")}
                                                title={`Copy ${p.stripe_transfer_id || p.stripe_payout_id}`}
                                                className="inline-flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
                                            >
                                                {(p.stripe_transfer_id || p.stripe_payout_id || "").slice(0, 12)}...
                                                <Copy className="h-2.5 w-2.5" />
                                            </button>
                                        ) : (
                                            <span className="text-[10px] text-muted-foreground">—</span>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {p.status === "failed" ? (
                                            <Button
                                                size="xs"
                                                variant="outline"
                                                className="h-7 text-[11px]"
                                                disabled={isRetrying}
                                                onClick={() => onRetry(p.id)}
                                            >
                                                {isRetrying ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                                                Retry
                                            </Button>
                                        ) : (
                                            <span className="text-[10px] text-muted-foreground">—</span>
                                        )}
                                    </TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </div>
            {payouts.length > 0 && (
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Show</span>
                        <Select value={String(payoutPageSize)} onValueChange={(v) => { setPayoutPageSize(Number(v)); setPayoutPage(0); }}>
                            <SelectTrigger className="h-8 text-xs w-[70px]">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {[25, 50, 100].map(n => (
                                    <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <span className="text-xs text-muted-foreground">per page</span>
                    </div>
                    {sortedPayouts.length > payoutPageSize && (
                        <Pagination
                            page={payoutPage}
                            pageSize={payoutPageSize}
                            hasNextPage={sortedPayouts.length > (payoutPage + 1) * payoutPageSize}
                            totalCount={sortedPayouts.length}
                            onPageChange={setPayoutPage}
                        />
                    )}
                </div>
            )}
        </div>
    );
}
