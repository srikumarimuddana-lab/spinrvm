"use client";

import { useEffect, useState } from "react";

import {
    AllowanceTypeValue,
    CorporateAllowance,
    CorporateMember,
    getMemberAllowance,
    putMemberAllowance,
} from "@/lib/api";
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { allowanceFormSchema, isAllowanceFormValid } from "@/lib/allowanceFormSchema";

interface Props {
    companyId: string;
    member: CorporateMember;
    onClose: () => void;
    onSaved: () => void;
}

export default function AllowanceDialog({ companyId, member, onClose, onSaved }: Props) {
    const [type, setType] = useState<AllowanceTypeValue>("fixed_recurring");
    const [amount, setAmount] = useState<string>("500");
    const [periodStart, setPeriodStart] = useState<string>("");
    const [periodEnd, setPeriodEnd] = useState<string>("");
    const [rollover, setRollover] = useState(false);
    const [autoCap, setAutoCap] = useState<string>("");
    const [autoMonthly, setAutoMonthly] = useState<string>("");
    const [saving, setSaving] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    useEffect(() => {
        getMemberAllowance(companyId, member.id)
            .then((a) => {
                if (a && (a as CorporateAllowance).id) {
                    const alw = a as CorporateAllowance;
                    setType(alw.type);
                    setAmount(alw.amount != null ? String(alw.amount) : "");
                    setPeriodStart(alw.period_start ?? "");
                    setPeriodEnd(alw.period_end ?? "");
                    setRollover(Boolean(alw.rollover));
                    setAutoCap(alw.auto_approve_topup_amount != null ? String(alw.auto_approve_topup_amount) : "");
                    setAutoMonthly(alw.auto_approve_monthly_count != null ? String(alw.auto_approve_monthly_count) : "");
                }
            })
            .catch(() => { /* new allowance — leave defaults */ });
    }, [companyId, member.id]);

    async function save() {
        setSaving(true);
        setErr(null);
        try {
            const parsed = allowanceFormSchema.safeParse({ type, amount, periodStart, periodEnd });
            if (!parsed.success) {
                throw new Error(parsed.error.issues[0].message);
            }
            const body: Parameters<typeof putMemberAllowance>[2] = { type };
            if (type === "unlimited") {
                // nothing else
            } else {
                body.amount = Number(amount);
                if (type === "fixed_recurring") {
                    body.period_start = periodStart;
                    body.period_end = periodEnd;
                    body.rollover = rollover;
                    if (autoCap) body.auto_approve_topup_amount = Number(autoCap);
                    if (autoMonthly) body.auto_approve_monthly_count = Number(autoMonthly);
                }
            }
            await putMemberAllowance(companyId, member.id, body);
            onSaved();
        } catch (e) {
            setErr(e instanceof Error ? e.message : "Save failed");
        } finally {
            setSaving(false);
        }
    }

    return (
        <Dialog open onOpenChange={(o) => (o ? undefined : onClose())}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>Allowance — {member.invited_email ?? member.id}</DialogTitle>
                </DialogHeader>

                <div className="space-y-3 py-2">
                    <div>
                        <Label>Type</Label>
                        <Select value={type} onValueChange={(v) => setType(v as AllowanceTypeValue)}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="fixed_recurring">fixed_recurring</SelectItem>
                                <SelectItem value="one_time">one_time</SelectItem>
                                <SelectItem value="unlimited">unlimited</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {type !== "unlimited" && (
                        <div>
                            <Label>Amount (CAD)</Label>
                            <Input
                                type="number"
                                min={1}
                                value={amount}
                                onChange={(e) => setAmount(e.target.value)}
                            />
                        </div>
                    )}

                    {type === "fixed_recurring" && (
                        <>
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <Label>Period start</Label>
                                    <Input
                                        type="date"
                                        value={periodStart}
                                        onChange={(e) => setPeriodStart(e.target.value)}
                                    />
                                </div>
                                <div>
                                    <Label>Period end</Label>
                                    <Input
                                        type="date"
                                        value={periodEnd}
                                        onChange={(e) => setPeriodEnd(e.target.value)}
                                    />
                                </div>
                            </div>
                            <div className="flex items-center gap-2">
                                <Switch checked={rollover} onCheckedChange={setRollover} id="rollover" />
                                <Label htmlFor="rollover">Rollover unused balance</Label>
                            </div>
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <Label>Auto-approve cap (per request)</Label>
                                    <Input
                                        type="number"
                                        placeholder="e.g. 50"
                                        value={autoCap}
                                        onChange={(e) => setAutoCap(e.target.value)}
                                    />
                                </div>
                                <div>
                                    <Label>Auto-approve count / month</Label>
                                    <Input
                                        type="number"
                                        placeholder="e.g. 5"
                                        value={autoMonthly}
                                        onChange={(e) => setAutoMonthly(e.target.value)}
                                    />
                                </div>
                            </div>
                        </>
                    )}

                    {err && <p className="text-sm text-destructive">{err}</p>}
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={onClose}>Cancel</Button>
                    <Button
                        onClick={save}
                        disabled={saving || !isAllowanceFormValid({ type, amount, periodStart, periodEnd })}
                    >
                        {saving ? "Saving…" : "Save"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
