"use client";

import { useState } from "react";
import { driverAction, overrideDriverStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import {
    ShieldCheck, ShieldAlert, Ban, UserX, UserCheck, AlertTriangle,
    Pause, Play, XCircle, CheckCircle, Loader2, ShieldOff,
} from "lucide-react";

type DriverStatus = "pending" | "active" | "needs_review" | "suspended" | "banned";

interface DriverActionBarProps {
    driver: any;
    onActionComplete: () => void;
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

export default function DriverActionBar({ driver, onActionComplete }: DriverActionBarProps) {
    const [actionDialog, setActionDialog] = useState<{ action: string; title: string; description: string; requiresReason: boolean; buttonLabel: string; buttonClass: string } | null>(null);
    const [reason, setReason] = useState("");
    const [loading, setLoading] = useState(false);

    const status = getDriverStatus(driver);
    const config = STATUS_CONFIG[status];
    const Icon = config.icon;

    const openAction = (action: string, title: string, description: string, requiresReason: boolean, buttonLabel: string, buttonClass: string) => {
        setReason("");
        setActionDialog({ action, title, description, requiresReason, buttonLabel, buttonClass });
    };

    const executeAction = async () => {
        if (!actionDialog) return;
        if (actionDialog.requiresReason && !reason.trim()) return;
        setLoading(true);
        try {
            if (actionDialog.action.startsWith("override_")) {
                const targetStatus = actionDialog.action.replace("override_", "");
                await overrideDriverStatus(driver.id, targetStatus, reason.trim() || undefined);
            } else {
                await driverAction(driver.id, actionDialog.action, reason.trim() || undefined);
            }
            setActionDialog(null);
            onActionComplete();
        } catch (e: any) {
            alert(e?.message || "Action failed");
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
                        {/* Show reason if suspended/banned */}
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
                        <Button size="sm" variant="outline" className="text-orange-600 border-orange-200 hover:bg-orange-50"
                            onClick={() => openAction("suspend", "Suspend Driver", "Suspend this driver without approving. They cannot go online.", true, "Suspend", "bg-orange-600 hover:bg-orange-700 text-white")}>
                            <Pause className="h-3.5 w-3.5 mr-1.5" />Suspend
                        </Button>
                        <Button size="sm" variant="outline" className="text-red-600 border-red-200 hover:bg-red-50"
                            onClick={() => openAction("ban", "Ban Driver", "Permanently block this driver from the platform.", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
                            <Ban className="h-3.5 w-3.5 mr-1.5" />Ban
                        </Button>
                    </>)}

                    {status === "active" && (<>
                        <Button size="sm" variant="outline" className="text-orange-600 border-orange-200 hover:bg-orange-50"
                            onClick={() => openAction("suspend", "Suspend Driver", "Temporarily suspend this driver. They will be taken offline and cannot accept rides until reactivated.", true, "Suspend Driver", "bg-orange-600 hover:bg-orange-700 text-white")}>
                            <Pause className="h-3.5 w-3.5 mr-1.5" />Suspend
                        </Button>
                        <Button size="sm" variant="outline" className="text-red-600 border-red-200 hover:bg-red-50"
                            onClick={() => openAction("ban", "Ban Driver", "Permanently block this driver from the platform.", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
                            <Ban className="h-3.5 w-3.5 mr-1.5" />Ban
                        </Button>
                    </>)}

                    {status === "needs_review" && (<>
                        <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white"
                            onClick={() => openAction("approve", "Re-approve Driver", "Confirm the driver's updated documents/vehicle are valid and set them back to Active.", false, "Re-approve", "bg-emerald-600 hover:bg-emerald-700 text-white")}>
                            <CheckCircle className="h-3.5 w-3.5 mr-1.5" />Re-approve
                        </Button>
                        <Button size="sm" variant="outline" className="text-orange-600 border-orange-200 hover:bg-orange-50"
                            onClick={() => openAction("suspend", "Suspend Driver", "Suspend this driver pending investigation.", true, "Suspend", "bg-orange-600 hover:bg-orange-700 text-white")}>
                            <Pause className="h-3.5 w-3.5 mr-1.5" />Suspend
                        </Button>
                        <Button size="sm" variant="outline" className="text-red-600 border-red-200 hover:bg-red-50"
                            onClick={() => openAction("ban", "Ban Driver", "Permanently block this driver.", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
                            <Ban className="h-3.5 w-3.5 mr-1.5" />Ban
                        </Button>
                    </>)}

                    {status === "suspended" && (<>
                        <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white"
                            onClick={() => openAction("reactivate", "Reactivate Driver", "Lift the suspension and allow the driver to go online again.", false, "Reactivate", "bg-emerald-600 hover:bg-emerald-700 text-white")}>
                            <Play className="h-3.5 w-3.5 mr-1.5" />Reactivate
                        </Button>
                        <Button size="sm" variant="outline" className="text-red-600 border-red-200 hover:bg-red-50"
                            onClick={() => openAction("ban", "Escalate to Ban", "Permanently block this driver from the platform.", true, "Ban Driver", "bg-red-700 hover:bg-red-800 text-white")}>
                            <Ban className="h-3.5 w-3.5 mr-1.5" />Escalate to Ban
                        </Button>
                    </>)}

                    {status === "banned" && (
                        <Button size="sm" variant="outline" className="text-emerald-600 border-emerald-200 hover:bg-emerald-50"
                            onClick={() => openAction("unban", "Unban Driver", "Lift the ban and restore the driver's account. Provide a reason for audit.", true, "Unban Driver", "bg-emerald-600 hover:bg-emerald-700 text-white")}>
                            <ShieldOff className="h-3.5 w-3.5 mr-1.5" />Unban
                        </Button>
                    )}
                </div>

                {/* Manual Status Override */}
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-dashed">
                    <span className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wider shrink-0">Move to:</span>
                    <Select value="" onValueChange={(v) => {
                        if (v && v !== status) {
                            openAction(
                                `override_${v}`,
                                `Move to ${v.charAt(0).toUpperCase() + v.slice(1)}`,
                                `Manually override this driver's status to "${v}". This is an admin override — use with caution.`,
                                v === "rejected" || v === "suspended" || v === "banned",
                                `Set ${v.charAt(0).toUpperCase() + v.slice(1)}`,
                                v === "active" ? "bg-emerald-600 hover:bg-emerald-700 text-white"
                                    : v === "banned" ? "bg-red-700 hover:bg-red-800 text-white"
                                    : v === "suspended" ? "bg-orange-600 hover:bg-orange-700 text-white"
                                    : "bg-primary hover:bg-primary/90 text-white"
                            );
                        }
                    }}>
                        <SelectTrigger className="h-7 text-[11px] w-[140px]"><SelectValue placeholder="Override status..." /></SelectTrigger>
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
            </div>

            {/* Action Confirmation Dialog */}
            <Dialog open={!!actionDialog} onOpenChange={open => { if (!open) setActionDialog(null); }}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{actionDialog?.title}</DialogTitle>
                        <DialogDescription>{actionDialog?.description}</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3 py-2">
                        {actionDialog?.requiresReason && (
                            <div>
                                <label className="text-sm font-medium mb-1.5 block">
                                    Reason <span className="text-red-500">*</span>
                                </label>
                                <Input
                                    value={reason}
                                    onChange={e => setReason(e.target.value)}
                                    placeholder="Provide a reason for this action..."
                                    className="w-full"
                                />
                                {actionDialog.requiresReason && !reason.trim() && (
                                    <p className="text-xs text-red-500 mt-1">Reason is required</p>
                                )}
                            </div>
                        )}
                        {!actionDialog?.requiresReason && (
                            <div>
                                <label className="text-sm font-medium mb-1.5 block">Reason (optional)</label>
                                <Input
                                    value={reason}
                                    onChange={e => setReason(e.target.value)}
                                    placeholder="Optional note..."
                                    className="w-full"
                                />
                            </div>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setActionDialog(null)} disabled={loading}>Cancel</Button>
                        <Button
                            onClick={executeAction}
                            disabled={loading || (actionDialog?.requiresReason && !reason.trim()) as boolean}
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
