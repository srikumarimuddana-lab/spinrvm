"use client";

import { useMemo, useState } from "react";
import { driverAction, overrideDriverStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import {
    ShieldCheck, ShieldAlert, Ban, AlertTriangle,
    Pause, Play, CheckCircle, Loader2, ShieldOff, ChevronDown,
} from "lucide-react";

type DriverStatus = "pending" | "active" | "needs_review" | "suspended" | "banned";

interface DriverActionBarProps {
    driver: any;
    // When set, parent merges these fields into its `selected` so the
    // slideout stays open and reflects the new status immediately
    // instead of being torn down on every action.
    onActionComplete: (updates?: Record<string, any>) => void;
}

function getDriverStatus(driver: any): DriverStatus {
    const s = driver.status || "pending";
    if (["active", "needs_review", "suspended", "banned"].includes(s)) return s;
    return "pending";
}

const STATUS_CONFIG: Record<DriverStatus, { label: string; color: string; bg: string; icon: any; description: string }> = {
    pending: { label: "Pending Review", color: "text-blue-700 dark:text-blue-400", bg: "bg-blue-50 dark:bg-blue-900/10 border-blue-200 dark:border-blue-800", icon: ShieldAlert, description: "New driver waiting for document review and approval. Cannot go online." },
    active: { label: "Active", color: "text-emerald-700 dark:text-emerald-400", bg: "bg-emerald-50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800", icon: ShieldCheck, description: "Driver is approved and can go online to accept rides." },
    needs_review: { label: "Needs Review", color: "text-amber-700 dark:text-amber-400", bg: "bg-amber-50 dark:bg-amber-900/10 border-amber-200 dark:border-amber-800", icon: AlertTriangle, description: "Driver has updated documents or vehicle info that need re-verification. Cannot go online." },
    suspended: { label: "Suspended", color: "text-orange-700 dark:text-orange-400", bg: "bg-orange-50 dark:bg-orange-900/10 border-orange-200 dark:border-orange-800", icon: Pause, description: "Driver is temporarily suspended and cannot go online." },
    banned: { label: "Banned", color: "text-red-800 dark:text-red-400", bg: "bg-red-100 dark:bg-red-900/20 border-red-300 dark:border-red-800", icon: Ban, description: "Driver is permanently banned from the platform." },
};

const DESTRUCTIVE_ACTIONS = new Set(["ban", "suspend", "override_banned", "override_suspended"]);

// Per-action reason categories. The category and (when present) duration
// are encoded as a structured prefix in the free-text reason sent to the
// backend so they flow into the audit log + the driver-facing push
// without requiring a backend schema change.
const REASON_CATEGORIES: Record<string, { value: string; label: string; template: string }[]> = {
    suspend: [
        { value: "documentation", label: "Documentation lapse",   template: "Required documents are missing or expired. Please re-upload to restore access." },
        { value: "vehicle",       label: "Vehicle non-compliance", template: "Vehicle does not meet platform requirements. Please update your vehicle profile or contact support." },
        { value: "safety",        label: "Safety concern",         template: "A safety concern was raised about a recent trip. We're reviewing the incident." },
        { value: "behavior",      label: "Driver behavior",        template: "Recent driver behavior is under review. Please contact support for details." },
        { value: "complaint",     label: "Customer complaint",     template: "A customer complaint is under investigation." },
        { value: "other",         label: "Other (write your own)", template: "" },
    ],
    ban: [
        { value: "safety",        label: "Serious safety incident", template: "Serious safety incident — account permanently disabled." },
        { value: "fraud",         label: "Fraud or abuse",          template: "Fraud or abuse detected — account permanently disabled." },
        { value: "background",    label: "Failed background check", template: "Background check criteria not met." },
        { value: "repeated",      label: "Repeated violations",     template: "Repeated policy violations — account permanently disabled." },
        { value: "other",         label: "Other (write your own)",  template: "" },
    ],
    reject: [
        { value: "documents",     label: "Insufficient documents",  template: "Application incomplete — required documents missing or invalid." },
        { value: "eligibility",   label: "Did not meet eligibility", template: "Application did not meet driver eligibility requirements." },
        { value: "background",    label: "Failed background check", template: "Background check criteria not met." },
        { value: "other",         label: "Other (write your own)",  template: "" },
    ],
};

const SUSPEND_DURATIONS: { value: string; label: string; days: number | null }[] = [
    { value: "1d",  label: "1 day",     days: 1   },
    { value: "3d",  label: "3 days",    days: 3   },
    { value: "7d",  label: "7 days",    days: 7   },
    { value: "14d", label: "14 days",   days: 14  },
    { value: "30d", label: "30 days",   days: 30  },
    { value: "indef", label: "Indefinite (manual review)", days: null },
];

const baseAction = (action: string): string => {
    if (action.startsWith("override_")) return action.replace("override_", "override-");
    return action;
};

// Optimistic local update for the action — mirrors what the backend
// writes so the parent slideout can reflect the new status without
// re-fetching. Backend is the source of truth; this just removes the
// "screen blanks out then reloads" flash after every action.
function computeOptimisticUpdates(action: string, reason: string): Record<string, any> {
    const now = new Date().toISOString();
    if (action === "approve") return { status: "active", is_verified: true, verified_at: now, rejection_reason: null };
    if (action === "reject")  return { status: "pending", rejection_reason: reason || null };
    if (action === "suspend") return { status: "suspended", suspension_reason: reason || null, suspended_at: now, is_online: false, is_available: false };
    if (action === "ban")     return { status: "banned", ban_reason: reason || null, is_online: false, is_available: false };
    if (action === "unban")   return { status: "active", ban_reason: null };
    if (action === "reactivate") return { status: "active", suspension_reason: null };
    if (action.startsWith("override_")) {
        const target = action.replace("override_", "");
        return { status: target };
    }
    return {};
}

export default function DriverActionBar({ driver, onActionComplete }: DriverActionBarProps) {
    const { toast } = useToast();
    const [actionDialog, setActionDialog] = useState<{ action: string; title: string; description: string; requiresReason: boolean; buttonLabel: string; buttonClass: string } | null>(null);
    const [pendingDestructiveAction, setPendingDestructiveAction] = useState<{ action: string; title: string; description: string; requiresReason: boolean; buttonLabel: string; buttonClass: string; alertTitle: string; alertDescription: string } | null>(null);
    const [reason, setReason] = useState("");
    const [category, setCategory] = useState("");
    const [duration, setDuration] = useState("7d");
    const [loading, setLoading] = useState(false);
    const [showOverride, setShowOverride] = useState(false);

    const status = getDriverStatus(driver);
    const config = STATUS_CONFIG[status];
    const Icon = config.icon;

    // Which reason categories to surface for this action — keyed by the
    // underlying lifecycle action (suspend / ban / reject). Approve and
    // reactivate don't surface categories because they don't need them.
    const categories = useMemo(() => {
        if (!actionDialog) return [];
        const a = baseAction(actionDialog.action);
        if (a === "suspend" || a === "override-suspended") return REASON_CATEGORIES.suspend;
        if (a === "ban" || a === "override-banned") return REASON_CATEGORIES.ban;
        if (a === "reject") return REASON_CATEGORIES.reject;
        return [];
    }, [actionDialog]);

    const showDuration = useMemo(() => {
        if (!actionDialog) return false;
        const a = baseAction(actionDialog.action);
        return a === "suspend" || a === "override-suspended";
    }, [actionDialog]);

    const openAction = (action: string, title: string, description: string, requiresReason: boolean, buttonLabel: string, buttonClass: string) => {
        setReason("");
        setCategory("");
        setDuration("7d");
        if (DESTRUCTIVE_ACTIONS.has(action)) {
            const isBan = action === "ban" || action === "override_banned";
            setPendingDestructiveAction({
                action, title, description, requiresReason, buttonLabel, buttonClass,
                alertTitle: isBan ? `Ban ${driver.first_name} ${driver.last_name}?` : `Suspend ${driver.first_name} ${driver.last_name}?`,
                alertDescription: isBan
                    ? `This will permanently ban ${driver.first_name} ${driver.last_name}. They will not be able to log in or accept rides. This action should be reviewed by a supervisor.`
                    : `This will suspend ${driver.first_name} ${driver.last_name} temporarily. They will not be able to go online until reactivated.`,
            });
            return;
        }
        setActionDialog({ action, title, description, requiresReason, buttonLabel, buttonClass });
    };

    const confirmDestructiveAction = () => {
        if (!pendingDestructiveAction) return;
        const { alertTitle: _at, alertDescription: _ad, ...actionDetails } = pendingDestructiveAction;
        setPendingDestructiveAction(null);
        setActionDialog(actionDetails);
    };

    const onCategoryChange = (v: string) => {
        setCategory(v);
        const tmpl = categories.find(c => c.value === v)?.template;
        if (tmpl && !reason.trim()) setReason(tmpl);
    };

    // Build the structured reason string sent to the backend. The category
    // and suspension duration get encoded as a single-line prefix so the
    // audit log + the driver-facing push both carry the context, even
    // though the backend schema still only has a free-text `reason` field.
    const buildReason = (): string => {
        const parts: string[] = [];
        const cat = categories.find(c => c.value === category);
        if (cat && cat.value !== "other") parts.push(cat.label);
        if (showDuration) {
            const dur = SUSPEND_DURATIONS.find(d => d.value === duration);
            if (dur) parts.push(dur.label);
        }
        const prefix = parts.length ? `[${parts.join(" · ")}] ` : "";
        return `${prefix}${reason.trim()}`.trim();
    };

    const reasonProvided = reason.trim().length > 0;
    const canSubmit = !actionDialog?.requiresReason || reasonProvided;

    const executeAction = async () => {
        if (!actionDialog) return;
        if (actionDialog.requiresReason && !reasonProvided) return;
        const builtReason = buildReason();
        setLoading(true);
        try {
            if (actionDialog.action.startsWith("override_")) {
                const targetStatus = actionDialog.action.replace("override_", "");
                await overrideDriverStatus(driver.id, targetStatus, builtReason || undefined);
            } else {
                const result = await driverAction(driver.id, actionDialog.action, builtReason || undefined);
                if (result?.audit_log_id) {
                    toast({ title: "Action recorded", description: `Ref: ${result.audit_log_id}` });
                }
            }
            const updates = computeOptimisticUpdates(actionDialog.action, builtReason);
            setActionDialog(null);
            onActionComplete(updates);
        } catch (e: any) {
            toast({ title: "Action failed", description: e?.message, variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    return (
        <>
            {/* Status Banner */}
            <div className={`rounded-xl p-4 border ${config.bg}`}>
                <div className="flex items-start gap-3">
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${config.bg}`}>
                        <Icon className={`h-5 w-5 ${config.color}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                        <h4 className={`text-sm font-bold ${config.color}`}>{config.label}</h4>
                        <p className={`text-xs mt-0.5 ${config.color} opacity-70`}>{config.description}</p>
                        {status === "suspended" && driver.suspension_reason && (
                            <p className="text-xs mt-2 bg-orange-100 dark:bg-orange-900/30 rounded-lg px-2.5 py-1.5 text-orange-700 dark:text-orange-400">
                                <AlertTriangle className="h-3 w-3 inline mr-1" />Reason: {driver.suspension_reason}
                            </p>
                        )}
                        {status === "banned" && driver.ban_reason && (
                            <p className="text-xs mt-2 bg-red-200 dark:bg-red-900/40 rounded-lg px-2.5 py-1.5 text-red-800 dark:text-red-400">
                                <Ban className="h-3 w-3 inline mr-1" />Reason: {driver.ban_reason}
                            </p>
                        )}
                    </div>
                </div>

                {/* Action Buttons — different per status */}
                <div className="flex flex-wrap gap-2 mt-4">
                    {status === "pending" && (<>
                        <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white"
                            onClick={() => openAction("approve", "Approve Driver", "This will activate the driver and allow them to go online and accept rides.", false, "Approve Driver", "bg-emerald-600 hover:bg-emerald-700 text-white")}>
                            <CheckCircle className="h-3.5 w-3.5 mr-1.5" />Approve
                        </Button>
                        <Button size="sm" variant="outline" className="text-orange-600 dark:text-orange-400 border-orange-200 dark:border-orange-800 hover:bg-orange-50 dark:hover:bg-orange-900/20"
                            onClick={() => openAction("suspend", "Suspend Driver", "Suspend this driver without approving. They cannot go online.", true, "Suspend", "bg-orange-600 hover:bg-orange-700 text-white")}>
                            <Pause className="h-3.5 w-3.5 mr-1.5" />Suspend
                        </Button>
                        <Button size="sm" variant="outline" className="text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20"
                            onClick={() => openAction("ban", "Ban Driver", "Permanently block this driver from the platform.", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
                            <Ban className="h-3.5 w-3.5 mr-1.5" />Ban
                        </Button>
                    </>)}

                    {status === "active" && (<>
                        <Button size="sm" variant="outline" className="text-orange-600 dark:text-orange-400 border-orange-200 dark:border-orange-800 hover:bg-orange-50 dark:hover:bg-orange-900/20"
                            onClick={() => openAction("suspend", "Suspend Driver", "Temporarily suspend this driver. They will be taken offline and cannot accept rides until reactivated.", true, "Suspend Driver", "bg-orange-600 hover:bg-orange-700 text-white")}>
                            <Pause className="h-3.5 w-3.5 mr-1.5" />Suspend
                        </Button>
                        <Button size="sm" variant="outline" className="text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20"
                            onClick={() => openAction("ban", "Ban Driver", "Permanently block this driver from the platform.", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
                            <Ban className="h-3.5 w-3.5 mr-1.5" />Ban
                        </Button>
                    </>)}

                    {status === "needs_review" && (<>
                        <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white"
                            onClick={() => openAction("approve", "Re-approve Driver", "Confirm the driver's updated documents/vehicle are valid and set them back to Active.", false, "Re-approve", "bg-emerald-600 hover:bg-emerald-700 text-white")}>
                            <CheckCircle className="h-3.5 w-3.5 mr-1.5" />Re-approve
                        </Button>
                        <Button size="sm" variant="outline" className="text-orange-600 dark:text-orange-400 border-orange-200 dark:border-orange-800 hover:bg-orange-50 dark:hover:bg-orange-900/20"
                            onClick={() => openAction("suspend", "Suspend Driver", "Suspend this driver pending investigation.", true, "Suspend", "bg-orange-600 hover:bg-orange-700 text-white")}>
                            <Pause className="h-3.5 w-3.5 mr-1.5" />Suspend
                        </Button>
                        <Button size="sm" variant="outline" className="text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20"
                            onClick={() => openAction("ban", "Ban Driver", "Permanently block this driver.", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
                            <Ban className="h-3.5 w-3.5 mr-1.5" />Ban
                        </Button>
                    </>)}

                    {status === "suspended" && (<>
                        <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white"
                            onClick={() => openAction("reactivate", "Reactivate Driver", "Lift the suspension and allow the driver to go online again.", false, "Reactivate", "bg-emerald-600 hover:bg-emerald-700 text-white")}>
                            <Play className="h-3.5 w-3.5 mr-1.5" />Reactivate
                        </Button>
                        <Button size="sm" variant="outline" className="text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/20"
                            onClick={() => openAction("ban", "Escalate to Ban", "Permanently block this driver from the platform.", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
                            <Ban className="h-3.5 w-3.5 mr-1.5" />Escalate to Ban
                        </Button>
                    </>)}

                    {status === "banned" && (
                        <Button size="sm" variant="outline" className="text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800 hover:bg-emerald-50 dark:hover:bg-emerald-900/20"
                            onClick={() => openAction("unban", "Unban Driver", "Lift the ban and restore the driver's account. Provide a reason for audit.", true, "Unban Driver", "bg-emerald-600 hover:bg-emerald-700 text-white")}>
                            <ShieldOff className="h-3.5 w-3.5 mr-1.5" />Unban
                        </Button>
                    )}
                </div>

                {/* Admin Override — collapsed by default. Lets a senior admin
                    force-set the status outside the normal workflow. Hidden
                    behind a disclosure to stop reviewers from grabbing it as
                    the default path. */}
                <div className="mt-3 pt-3 border-t border-dashed">
                    <button
                        type="button"
                        onClick={() => setShowOverride(s => !s)}
                        className="flex items-center gap-1 text-[10px] text-muted-foreground font-semibold uppercase tracking-wider hover:text-foreground transition-colors"
                    >
                        <ChevronDown className={`h-3 w-3 transition-transform ${showOverride ? "" : "-rotate-90"}`} />
                        Admin Override
                    </button>
                    {showOverride && (
                        <div className="flex items-center gap-2 mt-2">
                            <span className="text-[11px] text-muted-foreground shrink-0">Force status to:</span>
                            <Select value="" onValueChange={(v) => {
                                if (v && v !== status) {
                                    openAction(
                                        `override_${v}`,
                                        `Move to ${v.charAt(0).toUpperCase() + v.slice(1)}`,
                                        `Manually override this driver's status to "${v}". This bypasses the normal workflow — only use for incident response.`,
                                        v === "suspended" || v === "banned",
                                        `Set ${v.charAt(0).toUpperCase() + v.slice(1)}`,
                                        v === "active" ? "bg-emerald-600 hover:bg-emerald-700 text-white"
                                            : v === "banned" ? "bg-red-700 hover:bg-red-800 text-white"
                                            : v === "suspended" ? "bg-orange-600 hover:bg-orange-700 text-white"
                                            : "bg-primary hover:bg-primary/90 text-white"
                                    );
                                }
                            }}>
                                <SelectTrigger className="h-7 text-[11px] w-[160px]"><SelectValue placeholder="Pick status…" /></SelectTrigger>
                                <SelectContent>
                                    {([
                                    { value: "pending", label: "Pending" },
                                    { value: "active", label: "Active" },
                                    { value: "needs_review", label: "Needs Review" },
                                    { value: "suspended", label: "Suspended" },
                                    { value: "banned", label: "Banned" },
                                ] as const)
                                    .filter(s => s.value !== status)
                                    .map(s => <SelectItem key={s.value} value={s.value} className="text-xs">{s.label}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                    )}
                </div>
            </div>

            {/* Destructive Action Pre-Confirmation */}
            <AlertDialog open={!!pendingDestructiveAction} onOpenChange={(open) => { if (!open) setPendingDestructiveAction(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>{pendingDestructiveAction?.alertTitle}</AlertDialogTitle>
                        <AlertDialogDescription>{pendingDestructiveAction?.alertDescription}</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            className={pendingDestructiveAction?.buttonClass || "bg-destructive text-destructive-foreground hover:bg-destructive/90"}
                            onClick={confirmDestructiveAction}
                        >
                            {pendingDestructiveAction?.buttonLabel}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            {/* Action Confirmation Dialog (reason + category + duration) */}
            <Dialog open={!!actionDialog} onOpenChange={open => { if (!open) setActionDialog(null); }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle>{actionDialog?.title}</DialogTitle>
                        <DialogDescription>{actionDialog?.description}</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3 py-1">
                        {categories.length > 0 && (
                            <div>
                                <label htmlFor="action-category" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 block">Category</label>
                                <Select value={category} onValueChange={onCategoryChange}>
                                    <SelectTrigger id="action-category" className="h-9 text-sm">
                                        <SelectValue placeholder="Pick a category — fills a starter reason" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {categories.map(c => <SelectItem key={c.value} value={c.value} className="text-sm">{c.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}

                        {showDuration && (
                            <div>
                                <label htmlFor="action-duration" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 block">Duration</label>
                                <Select value={duration} onValueChange={setDuration}>
                                    <SelectTrigger id="action-duration" className="h-9 text-sm"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {SUSPEND_DURATIONS.map(d => <SelectItem key={d.value} value={d.value} className="text-sm">{d.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                                <p className="text-[11px] text-muted-foreground mt-1">
                                    Duration is recorded in the audit log + the message sent to the driver. Reactivation is still a manual step.
                                </p>
                            </div>
                        )}

                        <div>
                            <label htmlFor="action-reason" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5 block">
                                Reason {actionDialog?.requiresReason ? <span className="text-destructive normal-case">*</span> : <span className="text-muted-foreground normal-case font-normal">(optional)</span>}
                            </label>
                            <Textarea
                                id="action-reason"
                                value={reason}
                                onChange={e => setReason(e.target.value)}
                                placeholder={actionDialog?.requiresReason ? "Explain the action — visible in the audit log and the message sent to the driver." : "Optional note for the audit log."}
                                className="text-sm min-h-[88px]"
                            />
                            {actionDialog?.requiresReason && !reasonProvided && (
                                <p className="text-[11px] text-destructive mt-1">A reason is required for this action.</p>
                            )}
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setActionDialog(null)} disabled={loading}>Cancel</Button>
                        <Button
                            onClick={executeAction}
                            disabled={loading || !canSubmit}
                            className={actionDialog?.buttonClass || ""}
                        >
                            {loading ? <Loader2 className="h-4 w-4 animate-spin mr-1.5" /> : null}
                            {actionDialog?.buttonLabel}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
