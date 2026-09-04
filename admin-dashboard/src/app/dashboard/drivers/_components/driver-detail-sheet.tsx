// The driver-detail slideout: header (photo, badges, quick stats, edit
// controls) + the 10-tab Tabs shell, composing every driver-detail tab
// component (both pre-existing from PR #4955 and the Overview/Documents/
// Subscriptions tabs extracted alongside this file). Pure code motion out
// of drivers/page.tsx (design-audit follow-up -- PR #4955 deliberately left
// this ~875-line block un-extracted) -- no logic changes.
//
// Controlled/presentational, mirroring driver-list-table.tsx (the sibling
// "list" half of this same page): every piece of state this reads or
// mutates is still owned by DriversPage and passed down as props, typed
// the same way driver-list-table.tsx types its props -- real types for
// every primitive/callback/plain-shape prop, `any` only for driver-record-
// shaped props (selected/sorted lists of driver rows), matching every
// sibling tab component's existing convention (driver-action-bar.tsx,
// driver-payouts-tab.tsx, etc. all use `driver: any` / `data: any` for the
// same reason -- see driver-overview-tab.tsx's header comment for why a
// full Driver type isn't introduced here).
//
// API calls with no page-state dependency (Stripe payout retry/refresh, SIN
// reveal) and the toast dispatcher are imported directly here rather than
// threaded through as props, same as driver-action-bar.tsx importing
// driverAction/overrideDriverStatus directly.

import type { RefObject } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
    ShieldCheck, ShieldAlert, X, Star, Car, MapPin, CreditCard, Clock, DollarSign,
    CheckCircle, Mail, Copy, AlertTriangle, Pencil, Save, Loader2, Ban, Pause, Upload, Trash2,
} from "lucide-react";
import { maskEmail } from "@/lib/pii";
import { formatCurrency } from "@/lib/utils";
import { useToast } from "@/components/ui/use-toast";
import {
    retryPayout, refreshDriverStripeKyc, refreshDriverStripePayouts, revealDriverSin,
    getDriverPayoutsSummary,
    type DriverLiveStats, type DriverPayoutSummary, type DriverReferralSummary, type DriverTraining,
} from "@/lib/api";
import { type SortState } from "@/components/ui/sortable-table";
import { driverDisplayName, QuickStat } from "./driver-detail-shared";
import { VerificationSummaryCard } from "./driver-documents-helpers";
import DriverActionBar from "./driver-action-bar";
import DriverNotes from "./driver-notes";
import DriverTimeline from "./driver-timeline";
import DriverActivity from "./driver-activity";
import DriverDistance from "./driver-distance";
import { DriverPayoutsTab } from "./driver-payouts-tab";
import { DriverReferralsTab } from "./driver-referrals-tab";
import { DriverTrainingTab } from "./driver-training-tab";
import { DriverRidesTab } from "./driver-rides-tab";
import DriverOverviewTab from "./driver-overview-tab";
import DriverDocumentsTab from "./driver-documents-tab";
import DriverSubscriptionsTab from "./driver-subscriptions-tab";

interface DocSummary {
    expiry?: string;
    docStatus: "approved" | "pending" | "rejected" | "missing";
    expiryIsLegacy: boolean;
}

interface RequiredDoc {
    id?: string;
    key: string;
    label: string;
    has_expiry: boolean;
}

export default function DriverDetailSheet({
    selected,
    setSelected,
    editing,
    setEditing,
    saving,
    saveEdits,
    startEditing,
    liveStats,
    photoUploading,
    photoInputRef,
    handlePhotoUpload,
    photoReviewing,
    handlePhotoReview,
    themeV2Enabled,
    showPii,
    payoutSummary,
    setPayoutSummary,
    payoutLoading,
    detailTab,
    setDetailTab,
    loadDriverRides,
    loadDriverReferrals,
    loadDriverSubscriptions,
    loadDriverTraining,
    pendingDocsCount,
    ef,
    setEf,
    allServiceAreas,
    vehicleTypes,
    vehicleTypesByArea,
    serviceAreas,
    vehicleHistory,
    fmtDate,
    driverRides,
    driverRidesTotalCount,
    ridesLoading,
    referrals,
    referralsLoading,
    training,
    trainingLoading,
    trainingError,
    retryingPayoutId,
    setRetryingPayoutId,
    refreshingKyc,
    setRefreshingKyc,
    refreshingPayouts,
    setRefreshingPayouts,
    revealedSin,
    setRevealedSin,
    canRevealSin,
    isSuperAdmin,
    requiredDocs,
    activeDocs,
    docsLoading,
    docBusy,
    canReviewDocuments,
    setUploadDialogOpen,
    setOpenReviewerForDriver,
    openReviewDialog,
    setPreviewUrl,
    getDocSummary,
    setDrivers,
    loadData,
    loadDrivers,
    docKeyToExpiryField,
    subPaymentsLoading,
    driverSubPayments,
    sortedSubPayments,
    subPaymentsSort,
    toggleSubPaymentsSort,
    subPage,
    setSubPage,
    subPageSize,
    setSubPageSize,
}: {
    selected: any;
    setSelected: (d: any) => void;
    editing: boolean;
    setEditing: (v: boolean) => void;
    saving: boolean;
    saveEdits: () => void;
    startEditing: () => void;
    liveStats: DriverLiveStats | null;
    photoUploading: boolean;
    photoInputRef: RefObject<HTMLInputElement | null>;
    handlePhotoUpload: (file: File) => void;
    photoReviewing: boolean;
    handlePhotoReview: (action: "approve" | "reject") => void;
    themeV2Enabled: boolean;
    showPii: boolean;
    payoutSummary: DriverPayoutSummary | null;
    setPayoutSummary: (v: DriverPayoutSummary | null) => void;
    payoutLoading: boolean;
    detailTab: string;
    setDetailTab: (v: string) => void;
    loadDriverRides: (driverId: string) => void;
    loadDriverReferrals: (driverId: string) => void;
    loadDriverSubscriptions: (driverId: string) => void;
    loadDriverTraining: (driverId: string, refresh?: boolean) => void;
    pendingDocsCount: number;
    ef: (field: string) => string;
    setEf: (field: string, value: string) => void;
    allServiceAreas: any[];
    vehicleTypes: { id: string; name: string }[];
    vehicleTypesByArea: Record<string, Set<string>>;
    serviceAreas: { id: string; name: string }[];
    vehicleHistory: any[];
    fmtDate: (d: string) => string;
    driverRides: any[];
    driverRidesTotalCount: number | null;
    ridesLoading: boolean;
    referrals: DriverReferralSummary | null;
    referralsLoading: boolean;
    training: DriverTraining | null;
    trainingLoading: boolean;
    trainingError: string | null;
    retryingPayoutId: string | null;
    setRetryingPayoutId: (v: string | null) => void;
    refreshingKyc: boolean;
    setRefreshingKyc: (v: boolean) => void;
    refreshingPayouts: boolean;
    setRefreshingPayouts: (v: boolean) => void;
    revealedSin: { sin: string; expiresAt: number } | null;
    setRevealedSin: (v: { sin: string; expiresAt: number } | null) => void;
    canRevealSin: boolean;
    isSuperAdmin: boolean;
    requiredDocs: RequiredDoc[];
    activeDocs: any[];
    docsLoading: boolean;
    docBusy: string | null;
    canReviewDocuments: boolean;
    setUploadDialogOpen: (v: boolean) => void;
    setOpenReviewerForDriver: (v: { id: string; name: string }) => void;
    openReviewDialog: (docId: string, action: "approved" | "rejected") => void;
    setPreviewUrl: (url: string | null) => void;
    getDocSummary: (rdId: string | undefined, rdKey: string, rdLabel: string) => DocSummary;
    setDrivers: (updater: (prev: any[]) => any[]) => void;
    loadData: () => void;
    loadDrivers: () => void;
    docKeyToExpiryField: (key: string) => string | null;
    subPaymentsLoading: boolean;
    driverSubPayments: any[];
    sortedSubPayments: any[];
    subPaymentsSort: SortState;
    toggleSubPaymentsSort: (key: string) => void;
    subPage: number;
    setSubPage: (v: number) => void;
    subPageSize: number;
    setSubPageSize: (v: number) => void;
}) {
    const { toast } = useToast();
    return (
            <Sheet open={!!selected} onOpenChange={(open) => { if (!open) { setSelected(null); setEditing(false); } }}>
                <SheetContent side="right" showCloseButton={false} className="w-full sm:max-w-none sm:w-[90vw] lg:w-[80vw] xl:w-[70vw] p-0 overflow-hidden flex flex-col" aria-describedby={undefined}>
                    <SheetTitle className="sr-only">Driver Details</SheetTitle>
                    <SheetDescription className="sr-only">View and edit driver information</SheetDescription>
                    {selected && (<>
                        <div className="border-b bg-gradient-to-r from-primary/5 to-transparent">
                            <div className="p-6">
                                <div className="flex items-start justify-between">
                                    <div className="flex items-center gap-4">
                                        <div className="relative">
                                            {/* Photo comes from live-stats (loaded on open) — the drivers
                                                list no longer ships profile_image. Falls back to selected
                                                in case an older list payload still carries it. */}
                                            {(liveStats?.photo_url || selected.photo_url) ? (
                                                // eslint-disable-next-line @next/next/no-img-element
                                                <img src={liveStats?.photo_url || selected.photo_url} alt="" className="w-16 h-16 rounded-2xl object-cover" />
                                            ) : (
                                                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center text-xl font-bold text-primary">{(selected.first_name?.[0] || "")}{(selected.last_name?.[0] || "")}</div>
                                            )}
                                            <span className={`absolute -bottom-1 -right-1 w-4 h-4 rounded-full border-2 border-background ${selected.is_online ? "bg-success" : "bg-muted-foreground/40"}`} />
                                            <button
                                                type="button"
                                                title="Upload / change profile photo"
                                                disabled={photoUploading}
                                                onClick={() => photoInputRef.current?.click()}
                                                className="absolute -top-1 -left-1 w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow border-2 border-background disabled:opacity-50"
                                            >
                                                {photoUploading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
                                            </button>
                                            <input
                                                ref={photoInputRef}
                                                type="file"
                                                accept="image/jpeg,image/png,image/webp,image/gif"
                                                className="hidden"
                                                onChange={(e) => { const f = e.target.files?.[0]; if (f) void handlePhotoUpload(f); }}
                                            />
                                        </div>
                                        <div>
                                            <h2 className="text-xl font-bold flex items-center gap-2">
                                                {driverDisplayName(selected) || <span className="text-muted-foreground italic">Unnamed driver</span>}
                                                {selected.legacy_import_metadata && Object.keys(selected.legacy_import_metadata).length > 0 && (
                                                    <span className="inline-block text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5 align-middle">
                                                        Imported
                                                    </span>
                                                )}
                                            </h2>
                                            <div className="flex items-center gap-2 mt-1 flex-wrap">
                                                {selected.driver_code && (
                                                    <button onClick={() => navigator.clipboard.writeText(selected.driver_code)} className="flex items-center gap-1 text-xs font-mono font-semibold text-primary bg-primary/10 px-2 py-0.5 rounded" title="Copy driver code">{selected.driver_code}<Copy className="h-3 w-3" /></button>
                                                )}
                                                <button onClick={() => navigator.clipboard.writeText(selected.id)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition font-mono bg-muted/50 px-2 py-0.5 rounded" title="Copy driver UUID">{selected.id?.slice(0, 12)}…<Copy className="h-3 w-3" /></button>
                                                {selected.email && (
                                                    <button onClick={() => navigator.clipboard.writeText(selected.email)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition px-2 py-0.5 rounded hover:bg-muted/50" title={showPii ? `Copy email: ${selected.email}` : "Reveal PII to copy"}>
                                                        <Mail className="h-3 w-3" />
                                                        <span className="truncate max-w-[220px]">{showPii ? selected.email : maskEmail(selected.email)}</span>
                                                    </button>
                                                )}
                                            </div>
                                            {selected.profile_image_status === "pending_review" && (
                                                <div className="flex items-center gap-2 mt-2 p-2 rounded-lg bg-warning/10 border border-warning/30">
                                                    {(liveStats?.photo_url || selected.photo_url) && (
                                                        // eslint-disable-next-line @next/next/no-img-element
                                                        <img src={liveStats?.photo_url || selected.photo_url} alt="" className="w-9 h-9 rounded-full object-cover" />
                                                    )}
                                                    <span className="text-xs text-warning flex-1">Profile photo pending review</span>
                                                    {/* eslint-disable-next-line no-restricted-syntax -- solid-fill white-text success button; --success fails WCAG AA against white text in dark mode (#2816) */}
                                                    <button disabled={photoReviewing} onClick={() => handlePhotoReview("approve")} className="text-xs font-semibold px-2 py-1 rounded bg-emerald-600 text-white disabled:opacity-50">Approve</button>
                                                    <button disabled={photoReviewing} onClick={() => handlePhotoReview("reject")} className="text-xs font-semibold px-2 py-1 rounded bg-destructive text-destructive-foreground disabled:opacity-50">Reject</button>
                                                </div>
                                            )}
                                            {selected.profile_image_status === "rejected" && (
                                                <div className="mt-2 text-xs text-destructive">Profile photo rejected — driver must re-upload.</div>
                                            )}
                                            {/* Same categorical driver-lifecycle-status map as the list row above
                                                (6 states, 5 hues) -- not a #2816 migration target. */}
                                            <div className="flex items-center gap-2 mt-2">
                                                {themeV2Enabled ? (
                                                    selected.account_deleted ? <Badge variant="outline"><Trash2 className="h-3 w-3" /> Deleted</Badge>
                                                    : selected.status === "active" ? <Badge variant="outline-success"><ShieldCheck className="h-3 w-3" /> Active</Badge>
                                                    : selected.status === "needs_review" ? <Badge variant="outline-warning"><AlertTriangle className="h-3 w-3" /> Needs Review</Badge>
                                                    : selected.status === "suspended" ? <Badge variant="outline-destructive"><Pause className="h-3 w-3" /> Suspended</Badge>
                                                    : selected.status === "banned" ? <Badge variant="outline-destructive"><Ban className="h-3 w-3" /> Banned</Badge>
                                                    : <Badge variant="outline"><ShieldAlert className="h-3 w-3" /> Pending</Badge>
                                                ) : (
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    selected.account_deleted ? <Badge className="bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"><Trash2 className="h-3 w-3" /> Deleted</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : selected.status === "active" ? <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"><ShieldCheck className="h-3 w-3" /> Active</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : selected.status === "needs_review" ? <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"><AlertTriangle className="h-3 w-3" /> Needs Review</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : selected.status === "suspended" ? <Badge className="bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400"><Pause className="h-3 w-3" /> Suspended</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : selected.status === "banned" ? <Badge className="bg-red-200 text-red-800 dark:bg-red-900/40 dark:text-red-400"><Ban className="h-3 w-3" /> Banned</Badge>
                                                    // eslint-disable-next-line no-restricted-syntax -- categorical driver-lifecycle-status map (#2816)
                                                    : <Badge className="bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"><ShieldAlert className="h-3 w-3" /> Pending</Badge>
                                                )}
                                                <Badge variant="outline" className={selected.is_online && !selected.account_deleted ? "border-success/40 text-success" : ""}>
                                                    {selected.is_online && !selected.account_deleted ? "Online" : "Offline"}
                                                    {selected.last_status_changed_at && (
                                                        <span className="ml-1.5 text-[10px] opacity-70">
                                                            since {new Date(selected.last_status_changed_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                                                        </span>
                                                    )}
                                                </Badge>
                                                {selected.subscription_status === "active" && (themeV2Enabled ? (
                                                    <Badge variant="outline-accent"><CreditCard className="h-3 w-3" /> Spinr Pass</Badge>
                                                ) : (
                                                    // eslint-disable-next-line no-restricted-syntax -- Spinr Pass brand violet, not a success/warning/destructive signal (#2816)
                                                    <Badge className="bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400"><CreditCard className="h-3 w-3" /> Spinr Pass</Badge>
                                                ))}
                                                {selected.subscription_status === "expired" && <Badge className="bg-destructive/15 text-destructive"><CreditCard className="h-3 w-3" /> Pass Expired</Badge>}
                                            </div>
                                            {/* Profile Completeness Summary */}
                                            {(() => {
                                                const score = selected.profile_completeness_score;
                                                const missingCount = selected.profile_missing_count || 0;
                                                if (score === undefined || score === null) return null;
                                                return (
                                                    <div className="mt-3 p-2.5 rounded-lg border bg-muted/30">
                                                        <div className="flex items-center justify-between mb-1.5">
                                                            <span className="text-xs font-medium text-foreground/80">
                                                                Profile: {score}% complete{missingCount > 0 ? ` (${missingCount} field${missingCount === 1 ? '' : 's'} missing)` : ''}
                                                            </span>
                                                        </div>
                                                        <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                                                            <div
                                                                className={`h-full rounded-full transition-all ${score === 100 ? 'bg-success' : score >= 70 ? 'bg-warning' : 'bg-destructive'}`}
                                                                style={{ width: `${Math.min(score, 100)}%` }}
                                                            />
                                                        </div>
                                                        {score === 100 ? (
                                                            <p className="text-[11px] text-success mt-1.5 flex items-center gap-1"><CheckCircle className="h-3 w-3" />All required fields complete</p>
                                                        ) : selected.profile_missing_fields && selected.profile_missing_fields.length > 0 ? (
                                                            <div className="mt-1.5 flex flex-wrap gap-1">
                                                                {/* Already display labels ("License Plate"), not field
                                                                    names — the backend sends m["label"]. No un-snaking. */}
                                                                {selected.profile_missing_fields.map((label: string) => (
                                                                    <span key={label} className="text-[10px] text-destructive bg-destructive/10 px-1.5 py-0.5 rounded">{label}</span>
                                                                ))}
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                );
                                            })()}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        {!editing ? <Button variant="outline" size="sm" onClick={startEditing}><Pencil className="h-3.5 w-3.5" /> Edit</Button> : (<>
                                            <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
                                            {/* eslint-disable-next-line no-restricted-syntax -- solid-fill white-text success button; --success fails WCAG AA against white text in dark mode (#2816) */}
                                            <Button size="sm" onClick={saveEdits} disabled={saving} className="bg-emerald-600 hover:bg-emerald-700 text-white">{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save</Button>
                                        </>)}
                                        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={() => { setSelected(null); setEditing(false); }}><X className="h-4 w-4" /></Button>
                                    </div>
                                </div>
                                <div className="grid grid-cols-4 gap-3 mt-5">
                                    {/* QuickStats prefer live-stats computed on the backend over
                                        the denormalised drivers.* columns, which were stale or
                                        unset for three of the four metrics. While live-stats are
                                        in flight we show a "\u2026" placeholder so the user sees that
                                        the value is loading instead of a stale 0. */}
                                    <QuickStat
                                        // eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816)
                                        icon={Star} color="text-amber-500" bg="bg-amber-50 dark:bg-amber-900/20"
                                        label="Rating"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : liveStats.avg_rating != null
                                                    ? liveStats.avg_rating.toFixed(1)
                                                    : selected.rating != null && selected.rating > 0
                                                        ? Number(selected.rating).toFixed(1)
                                                        : "New"
                                        }
                                    />
                                    <QuickStat
                                        // eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816)
                                        icon={Car} color="text-blue-500" bg="bg-blue-50 dark:bg-blue-900/20"
                                        label="Rides"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : (liveStats.total_rides || 0).toLocaleString()
                                        }
                                    />
                                    <QuickStat
                                        // eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816)
                                        icon={DollarSign} color="text-emerald-500" bg="bg-emerald-50 dark:bg-emerald-900/20"
                                        label="Earnings"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : formatCurrency(liveStats.total_earnings || 0)
                                        }
                                        sub={
                                            payoutSummary
                                                ? payoutSummary.summary.pending_balance > 0
                                                    ? `${formatCurrency(payoutSummary.summary.pending_balance)} pending`
                                                    : payoutSummary.summary.pending_in_flight > 0
                                                        ? `${formatCurrency(payoutSummary.summary.pending_in_flight)} in flight`
                                                        : "All paid out"
                                                : undefined
                                        }
                                        subTone={
                                            payoutSummary && payoutSummary.summary.pending_balance > 0 ? "amber" : "muted"
                                        }
                                    />
                                    <QuickStat
                                        // eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816)
                                        icon={CheckCircle} color="text-violet-500" bg="bg-violet-50 dark:bg-violet-900/20"
                                        label="Accept Rate"
                                        value={
                                            liveStats === null
                                                ? "\u2026"
                                                : liveStats.acceptance_rate != null
                                                    ? `${liveStats.acceptance_rate}%`
                                                    : "\u2014"
                                        }
                                    />
                                </div>
                            </div>
                        </div>

                        <Tabs value={detailTab} onValueChange={(v) => { setDetailTab(v); if (v === "rides") loadDriverRides(selected.id); if (v === "referrals") loadDriverReferrals(selected.id); if (v === "subscriptions") loadDriverSubscriptions(selected.id); if (v === "training") loadDriverTraining(selected.id); }} className="flex-1 overflow-hidden flex flex-col">
                            <TabsList className="mx-6 mt-4 w-fit">
                                <TabsTrigger value="overview">Overview</TabsTrigger>
                                <TabsTrigger value="documents">Documents{pendingDocsCount > 0 && <span className="ml-1.5 bg-warning/15 text-warning text-[10px] font-bold px-1.5 py-0.5 rounded-full" title={`${pendingDocsCount} document${pendingDocsCount === 1 ? "" : "s"} awaiting review`}>{pendingDocsCount}</span>}</TabsTrigger>
                                <TabsTrigger value="rides">Rides{selected.total_rides > 0 && <span className="ml-1.5 bg-primary/10 text-primary text-[10px] font-bold px-1.5 py-0.5 rounded-full">{(selected.total_rides || 0).toLocaleString()}</span>}</TabsTrigger>
                                <TabsTrigger value="distance">Distance</TabsTrigger>
                                <TabsTrigger value="payouts">Payouts{payoutSummary && payoutSummary.summary.pending_balance > 0 && <span className="ml-1.5 bg-warning/15 text-warning text-[10px] font-bold px-1.5 py-0.5 rounded-full" title={`${formatCurrency(payoutSummary.summary.pending_balance)} pending payout`}>!</span>}</TabsTrigger>
                                <TabsTrigger value="referrals">Referrals</TabsTrigger>
                                <TabsTrigger value="training">Training</TabsTrigger>
                                <TabsTrigger value="subscriptions">Subscriptions</TabsTrigger>
                                <TabsTrigger value="verification">Actions</TabsTrigger>
                                <TabsTrigger value="notes">Notes</TabsTrigger>
                                <TabsTrigger value="history">History</TabsTrigger>
                            </TabsList>
                            <div className="flex-1 overflow-y-auto px-6 pb-6">
                                {/* Overview */}
                                <TabsContent value="overview" className="mt-4 space-y-5">
                                    <DriverOverviewTab
                                        selected={selected}
                                        editing={editing}
                                        ef={ef}
                                        setEf={setEf}
                                        allServiceAreas={allServiceAreas}
                                        vehicleTypes={vehicleTypes}
                                        vehicleTypesByArea={vehicleTypesByArea}
                                        serviceAreas={serviceAreas}
                                        showPii={showPii}
                                        liveStats={liveStats}
                                        vehicleHistory={vehicleHistory}
                                        themeV2Enabled={themeV2Enabled}
                                        fmtDate={fmtDate}
                                    />
                                </TabsContent>

                                {/* Rides */}
                                <TabsContent value="rides" className="mt-4">
                                    <DriverRidesTab
                                        rides={driverRides}
                                        totalCount={driverRidesTotalCount}
                                        loading={ridesLoading}
                                        driverName={driverDisplayName(selected) || "this driver"}
                                        fmtDate={fmtDate}
                                    />
                                </TabsContent>

                                {/* Referrals */}
                                <TabsContent value="referrals" className="mt-4">
                                    <DriverReferralsTab data={referrals} loading={referralsLoading} fmtDate={fmtDate} />
                                </TabsContent>

                                {/* Training (LMS) */}
                                <TabsContent value="training" className="mt-4">
                                    <DriverTrainingTab
                                        data={training}
                                        loading={trainingLoading}
                                        error={trainingError}
                                        onRefresh={() => loadDriverTraining(selected.id, true)}
                                        fmtDate={fmtDate}
                                    />
                                </TabsContent>

                                {/* Payouts */}
                                <TabsContent value="payouts" className="mt-4">
                                    <DriverPayoutsTab
                                        data={payoutSummary}
                                        loading={payoutLoading}
                                        driverId={selected.id}
                                        driverName={driverDisplayName(selected) || "this driver"}
                                        isLegacyImported={!!selected.legacy_import_metadata && Object.keys(selected.legacy_import_metadata).length > 0}
                                        notify={toast}
                                        retryingPayoutId={retryingPayoutId}
                                        refreshingKyc={refreshingKyc}
                                        refreshingPayouts={refreshingPayouts}
                                        revealedSin={revealedSin}
                                        canRevealSin={canRevealSin}
                                        canRefreshPayouts={isSuperAdmin}
                                        onRetry={async (payoutId) => {
                                            setRetryingPayoutId(payoutId);
                                            try {
                                                await retryPayout(payoutId);
                                                toast({ title: "Retry queued", description: "Payout sent back to Stripe for processing." });
                                                const fresh = await getDriverPayoutsSummary(selected.id);
                                                setPayoutSummary(fresh);
                                            } catch (e: any) {
                                                toast({ title: "Retry failed", description: e?.message || "Unknown error", variant: "destructive" });
                                            } finally {
                                                setRetryingPayoutId(null);
                                            }
                                        }}
                                        onRefreshKyc={async () => {
                                            setRefreshingKyc(true);
                                            try {
                                                const res = await refreshDriverStripeKyc(selected.id);
                                                const fresh = await getDriverPayoutsSummary(selected.id);
                                                setPayoutSummary(fresh);
                                                toast({
                                                    title: res.synced ? "Synced from Stripe" : "Not synced",
                                                    description: res.message,
                                                    ...(res.synced ? {} : { variant: "destructive" as const }),
                                                });
                                            } catch (e: any) {
                                                toast({ title: "Refresh failed", description: e?.message || "Unknown error", variant: "destructive" });
                                            } finally {
                                                setRefreshingKyc(false);
                                            }
                                        }}
                                        onRefreshPayouts={async () => {
                                            setRefreshingPayouts(true);
                                            try {
                                                // Failures raise non-2xx (handled in catch); still
                                                // branch on `synced` like onRefreshKyc so a future
                                                // partial-success response can't toast as success.
                                                const res = await refreshDriverStripePayouts(selected.id);
                                                const fresh = await getDriverPayoutsSummary(selected.id);
                                                setPayoutSummary(fresh);
                                                toast({
                                                    title: res.synced ? "Payouts synced from Stripe" : "Not synced",
                                                    description: res.message,
                                                    ...(res.synced ? {} : { variant: "destructive" as const }),
                                                });
                                            } catch (e: any) {
                                                toast({ title: "Payout sync failed", description: e?.message || "Unknown error", variant: "destructive" });
                                            } finally {
                                                setRefreshingPayouts(false);
                                            }
                                        }}
                                        onRevealSin={async () => {
                                            // Confirm before triggering — every reveal writes an
                                            // audit_log row and admins should not click it idly.
                                            if (!window.confirm("Reveal this driver's SIN?\n\nThis decrypts Spinr's encrypted copy. The call is recorded in the audit log with your admin ID and a timestamp. The value will be shown for 30 seconds then hidden.")) return;
                                            try {
                                                const res = await revealDriverSin(selected.id);
                                                setRevealedSin({ sin: res.sin, expiresAt: Date.now() + 30_000 });
                                                toast({ title: "SIN revealed", description: "Auto-hides in 30 seconds. Reveal logged." });
                                            } catch (e: any) {
                                                toast({ title: "Reveal failed", description: e?.message || "Unknown error", variant: "destructive" });
                                            }
                                        }}
                                    />
                                </TabsContent>

                                {/* Documents */}
                                <TabsContent value="documents" className="mt-4 space-y-6">
                                    <DriverDocumentsTab
                                        selected={selected}
                                        requiredDocs={requiredDocs}
                                        activeDocs={activeDocs}
                                        docsLoading={docsLoading}
                                        docBusy={docBusy}
                                        canReviewDocuments={canReviewDocuments}
                                        setUploadDialogOpen={setUploadDialogOpen}
                                        setOpenReviewerForDriver={setOpenReviewerForDriver}
                                        openReviewDialog={openReviewDialog}
                                        setPreviewUrl={setPreviewUrl}
                                        getDocSummary={getDocSummary}
                                    />
                                </TabsContent>

                                {/* Actions & Verification */}
                                <TabsContent value="verification" className="mt-4 space-y-5">
                                    <DriverActionBar
                                        driver={selected}
                                        onActionComplete={(updates) => {
                                            if (updates && Object.keys(updates).length > 0) {
                                                setSelected((prev: any) => prev ? { ...prev, ...updates } : prev);
                                                setDrivers(prevList => prevList.map(d => d.id === selected.id ? { ...d, ...updates } : d));
                                            }
                                            loadData();
                                            loadDrivers();
                                        }}
                                    />
                                    <VerificationSummaryCard
                                        requiredDocs={requiredDocs.length > 0 ? requiredDocs : [
                                            { key: "drivers_license",      label: "Driver's License",    has_expiry: true },
                                            { key: "vehicle_insurance",    label: "Vehicle Insurance",   has_expiry: true },
                                            { key: "vehicle_registration", label: "Vehicle Registration",has_expiry: true },
                                            { key: "vehicle_inspection",   label: "Vehicle Inspection",  has_expiry: true },
                                            { key: "background_check",     label: "Background Check",   has_expiry: true },
                                        ]}
                                        activeDocs={activeDocs}
                                        driver={selected}
                                        docKeyToExpiryField={docKeyToExpiryField}
                                        onOpenDocumentsTab={() => setDetailTab("documents")}
                                    />
                                </TabsContent>

                                {/* Notes */}
                                <TabsContent value="notes" className="mt-4">
                                    <DriverNotes driverId={selected.id} />
                                </TabsContent>

                                {/* Distance Travelled: per-Regina-day phase km + durations,
                                    with per-day Distance Logs drill-down (insurance/ops view) */}
                                <TabsContent value="distance" className="mt-4">
                                    <DriverDistance driverId={selected.id} />
                                </TabsContent>

                                {/* History: daily activity (per-phase km + empty/riding time) + audit timeline */}
                                <TabsContent value="history" className="mt-4 space-y-6">
                                    <DriverActivity driverId={selected.id} />
                                    <DriverTimeline driverId={selected.id} driver={selected} />
                                </TabsContent>

                                {/* Subscription payment history */}
                                <TabsContent value="subscriptions" className="mt-4">
                                    <DriverSubscriptionsTab
                                        subPaymentsLoading={subPaymentsLoading}
                                        driverSubPayments={driverSubPayments}
                                        sortedSubPayments={sortedSubPayments}
                                        subPaymentsSort={subPaymentsSort}
                                        toggleSubPaymentsSort={toggleSubPaymentsSort}
                                        subPage={subPage}
                                        setSubPage={setSubPage}
                                        subPageSize={subPageSize}
                                        setSubPageSize={setSubPageSize}
                                    />
                                </TabsContent>
                            </div>
                        </Tabs>
                    </>)}
                </SheetContent>
            </Sheet>
    );
}
