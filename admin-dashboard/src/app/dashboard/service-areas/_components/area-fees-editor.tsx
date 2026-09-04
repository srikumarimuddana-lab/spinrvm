"use client";

import { useState } from "react";
import { Plus, Trash2, Pencil, DollarSign } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { useCrudToast } from "@/components/ui/use-crud-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { createAreaFee, updateAreaFee, deleteAreaFee } from "@/lib/api";
import { FieldInput, FieldToggle, FieldTextarea } from "./service-area-shared";

// ─── Area Fees Editor ───
// Extracted verbatim from service-areas/page.tsx.

export default function AreaFeesEditor({ areaId, area, fees, loading, onReload, onFieldUpdate }: {
    areaId: string; area: any; fees: any[]; loading: boolean;
    onReload: () => void; onFieldUpdate: (areaId: string, field: string, value: any) => void;
}) {
    const { toast } = useToast();
    const crudToast = useCrudToast();
    const [feeDeleteTarget, setFeeDeleteTarget] = useState<string | null>(null);
    const [editingFee, setEditingFee] = useState<any>(null);
    const [saving, setSaving] = useState(false);

    const FEE_TYPES = [
        { value: 'custom', label: 'Custom Fee' },
        { value: 'airport', label: 'Airport Fee' },
        { value: 'night', label: 'Night Surcharge' },
        { value: 'toll', label: 'Toll Fee' },
        { value: 'event', label: 'Event Fee' },
        { value: 'holiday', label: 'Holiday Fee' },
    ];
    const CALC_MODES = [
        { value: 'flat', label: 'Flat ($)' },
        { value: 'per_km', label: 'Per KM ($)' },
        { value: 'percentage', label: 'Percentage (%)' },
    ];

    const handleCreate = async () => {
        setSaving(true);
        try {
            await createAreaFee(areaId, { fee_name: 'New Fee', fee_type: 'custom', calc_mode: 'flat', amount: 0, is_active: true });
            crudToast.created("Fee");
            onReload();
        } catch (e) { crudToast.error("create fee", e); }
        setSaving(false);
    };

    const handleUpdate = async (feeId: string, data: any) => {
        try {
            await updateAreaFee(areaId, feeId, data);
            crudToast.updated("Fee");
            onReload();
        } catch (e) { crudToast.error("update fee", e); }
    };

    const handleDelete = (feeId: string) => {
        setFeeDeleteTarget(feeId);
    };

    const confirmFeeDelete = async () => {
        if (!feeDeleteTarget) return;
        try {
            await deleteAreaFee(areaId, feeDeleteTarget);
            crudToast.deleted("Fee");
            onReload();
        }
        catch (e) { crudToast.error("delete fee", e); }
        finally { setFeeDeleteTarget(null); }
    };

    return (
        <>
        <div className="space-y-6">
            {/* SECTION 1: Area Fees */}
            <div>
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h4 className="font-bold text-foreground">Area Fees</h4>
                        <p className="text-sm text-muted-foreground">Fees applied to rides in this area. Calculated dynamically based on type.</p>
                    </div>
                    <button onClick={handleCreate} disabled={saving}
                        className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-2 rounded-xl text-sm font-semibold hover:bg-primary/90 disabled:opacity-50">
                        <Plus className="h-4 w-4" /> Add Fee
                    </button>
                </div>

                {loading ? (
                    <div className="text-center py-8 text-muted-foreground">Loading fees...</div>
                ) : fees.length === 0 ? (
                    <div className="text-center py-8 bg-muted rounded-xl border-2 border-dashed">
                        <DollarSign className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
                        <p className="text-sm text-muted-foreground">No fees configured</p>
                        <p className="text-xs text-muted-foreground">Add fees like airport surcharge, night fee, city fee, etc.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                        {fees.map(fee => (
                            <div key={fee.id} className={`rounded-xl border p-4 ${fee.is_active ? 'bg-card' : 'bg-muted opacity-60'}`}>
                                <div className="flex items-start justify-between mb-3">
                                    <div>
                                        <p className="font-bold text-foreground">{fee.fee_name || fee.fee_type || 'Fee'}</p>
                                        <p className="text-xs text-muted-foreground mt-0.5">{fee.fee_type} · {fee.calc_mode || 'flat'}</p>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-lg font-bold text-foreground">
                                            {fee.calc_mode === 'percentage' ? `${fee.amount}%` : `$${Number(fee.amount || 0).toFixed(2)}`}
                                            {fee.calc_mode === 'per_km' && '/km'}
                                        </p>
                                    </div>
                                </div>
                                {fee.description && <p className="text-xs text-muted-foreground mb-3">{fee.description}</p>}
                                {fee.conditions && fee.conditions.start_hour !== undefined && (
                                    <p className="text-xs text-muted-foreground mb-3">Hours: {fee.conditions.start_hour}:00 - {fee.conditions.end_hour}:00</p>
                                )}
                                <div className="flex items-center gap-2 pt-2 border-t">
                                    <button onClick={() => setEditingFee(editingFee?.id === fee.id ? null : fee)} className="text-xs text-muted-foreground hover:text-primary font-medium flex items-center gap-1">
                                        <Pencil className="h-3 w-3" /> Edit
                                    </button>
                                    <button onClick={() => handleUpdate(fee.id, { is_active: !fee.is_active })} className="text-xs text-muted-foreground hover:text-foreground font-medium ml-auto">
                                        {fee.is_active ? 'Disable' : 'Enable'}
                                    </button>
                                    <button onClick={() => handleDelete(fee.id)} className="text-xs text-muted-foreground hover:text-destructive font-medium">
                                        <Trash2 className="h-3 w-3" />
                                    </button>
                                </div>
                                {editingFee?.id === fee.id && (
                                    <FeeEditForm fee={editingFee} feeTypes={FEE_TYPES} calcModes={CALC_MODES}
                                        onSave={async (data) => { await handleUpdate(fee.id, data); setEditingFee(null); }}
                                        onCancel={() => setEditingFee(null)} />
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* SECTION 2: Taxes */}
            <div>
                <h4 className="font-bold text-foreground mb-3">Tax Configuration</h4>
                <div className="bg-card rounded-xl border p-4">
                    <div className="flex items-center gap-4 mb-4">
                        <label className="flex items-center gap-2 text-sm">
                            <input type="radio" name={`tax-${areaId}`} checked={!area.hst_enabled}
                                onChange={() => onFieldUpdate(areaId, 'hst_enabled', false)} className="accent-primary" />
                            GST + PST (separate)
                        </label>
                        <label className="flex items-center gap-2 text-sm">
                            <input type="radio" name={`tax-${areaId}`} checked={!!area.hst_enabled}
                                onChange={() => onFieldUpdate(areaId, 'hst_enabled', true)} className="accent-primary" />
                            HST (harmonized)
                        </label>
                    </div>
                    {area.hst_enabled ? (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FieldInput label="HST Rate (%)" value={area.hst_rate || 0} type="number"
                                onSave={v => onFieldUpdate(areaId, 'hst_rate', parseFloat(v))} />
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <div className="flex items-center gap-2 mb-2">
                                    <FieldToggle label="GST Enabled" value={area.gst_enabled !== false}
                                        onSave={v => onFieldUpdate(areaId, 'gst_enabled', v)} />
                                </div>
                                {area.gst_enabled !== false && (
                                    <FieldInput label="GST Rate (%)" value={area.gst_rate || 5} type="number"
                                        onSave={v => onFieldUpdate(areaId, 'gst_rate', parseFloat(v))} />
                                )}
                            </div>
                            <div>
                                <div className="flex items-center gap-2 mb-2">
                                    <FieldToggle label="PST Enabled" value={!!area.pst_enabled}
                                        onSave={v => onFieldUpdate(areaId, 'pst_enabled', v)} />
                                </div>
                                {area.pst_enabled && (
                                    <FieldInput label="PST Rate (%)" value={area.pst_rate || 0} type="number"
                                        onSave={v => onFieldUpdate(areaId, 'pst_rate', parseFloat(v))} />
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* SECTION 3: Cancellation Fees */}
            <div>
                <h4 className="font-bold text-foreground mb-3">Cancellation Fees</h4>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <FieldInput label="Rider cancel — driver on the way ($)" value={area.rider_cancel_fee_before_driver || 0} type="number" onSave={v => onFieldUpdate(areaId, 'rider_cancel_fee_before_driver', parseFloat(v))} />
                    <FieldInput label="Rider cancel — driver arrived ($)" value={area.rider_cancel_fee_after_arrival || 4.50} type="number" onSave={v => onFieldUpdate(areaId, 'rider_cancel_fee_after_arrival', parseFloat(v))} />
                    <FieldInput label="Driver gets ($)" value={area.cancel_fee_driver_share || 4.00} type="number" onSave={v => onFieldUpdate(areaId, 'cancel_fee_driver_share', parseFloat(v))} />
                    <FieldInput label="Admin gets ($)" value={area.cancel_fee_admin_share || 0.50} type="number" onSave={v => onFieldUpdate(areaId, 'cancel_fee_admin_share', parseFloat(v))} />
                    <FieldInput label="Driver cancel penalty ($)" value={area.driver_cancel_fee || 0} type="number" onSave={v => onFieldUpdate(areaId, 'driver_cancel_fee', parseFloat(v))} />
                    <FieldInput label="Free cancel window (sec)" value={area.free_cancel_window_seconds || 120} type="number" onSave={v => onFieldUpdate(areaId, 'free_cancel_window_seconds', parseInt(v))} />
                </div>
            </div>

            {/* SECTION 4: Referral Rewards */}
            <div>
                <h4 className="font-bold text-foreground mb-3">Referral Rewards</h4>
                <p className="text-xs text-muted-foreground mb-3">
                    Per-area referral rewards (CAD). Riders/drivers whose area resolves here see and earn these amounts; users not mapped to any area fall back to the global default. Set any reward to $0 to turn that side off (e.g. driver referee reward defaults to $0 — set it above 0 to pay a signup bonus to the referred driver). &quot;Rides within (days)&quot; is the deadline to complete the required rides before the referral expires unpaid — set 0 for no deadline.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <FieldInput label="Rider — referrer reward ($)" value={area.rider_referrer_reward ?? 5} type="number" onSave={v => onFieldUpdate(areaId, 'rider_referrer_reward', parseFloat(v))} />
                    <FieldInput label="Rider — referee reward ($)" value={area.rider_referee_reward ?? 5} type="number" onSave={v => onFieldUpdate(areaId, 'rider_referee_reward', parseFloat(v))} />
                    <FieldInput label="Rider — rides required" value={area.rider_referral_rides_required ?? 1} type="number" onSave={v => onFieldUpdate(areaId, 'rider_referral_rides_required', parseInt(v))} />
                    <FieldInput label="Rider — rides within (days)" value={area.rider_referral_window_days ?? 30} type="number" onSave={v => onFieldUpdate(areaId, 'rider_referral_window_days', parseInt(v))} />
                    <FieldInput label="Driver — referrer reward ($)" value={area.driver_referral_reward ?? 10} type="number" onSave={v => onFieldUpdate(areaId, 'driver_referral_reward', parseFloat(v))} />
                    <FieldInput label="Driver — referee reward ($)" value={area.driver_referee_reward ?? 0} type="number" onSave={v => onFieldUpdate(areaId, 'driver_referee_reward', parseFloat(v))} />
                    <FieldInput label="Driver — rides required" value={area.driver_referral_rides_required ?? 10} type="number" onSave={v => onFieldUpdate(areaId, 'driver_referral_rides_required', parseInt(v))} />
                    <FieldInput label="Driver — rides within (days)" value={area.driver_referral_window_days ?? 30} type="number" onSave={v => onFieldUpdate(areaId, 'driver_referral_window_days', parseInt(v))} />
                </div>
                <p className="text-xs text-muted-foreground mt-4 mb-2">
                    Referral Terms &amp; Conditions shown on the rider/driver &quot;Refer &amp; Earn&quot; screens. Leave blank to auto-generate the default sentence from the reward amounts above.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <FieldTextarea label="Rider — referral T&C" value={area.rider_referral_terms ?? ''} placeholder="e.g. Give $5, get $5 when your friend takes their first ride. Rewards credited to your wallet…" onSave={v => onFieldUpdate(areaId, 'rider_referral_terms', v)} />
                    <FieldTextarea label="Driver — referral T&C" value={area.driver_referral_terms ?? ''} placeholder="e.g. Earn $10 for each driver who signs up with your code and completes 10 rides…" onSave={v => onFieldUpdate(areaId, 'driver_referral_terms', v)} />
                </div>
            </div>
        </div>

        <AlertDialog open={!!feeDeleteTarget} onOpenChange={(open) => { if (!open) setFeeDeleteTarget(null); }}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>Delete fee?</AlertDialogTitle>
                    <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={confirmFeeDelete} className="bg-destructive hover:bg-destructive/90">Delete</AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
        </>
    );
}

function FeeEditForm({ fee, feeTypes, calcModes, onSave, onCancel }: {
    fee: any; feeTypes: { value: string; label: string }[]; calcModes: { value: string; label: string }[];
    onSave: (data: any) => Promise<void>; onCancel: () => void;
}) {
    const [form, setForm] = useState({
        fee_name: fee.fee_name || '', fee_type: fee.fee_type || 'custom',
        calc_mode: fee.calc_mode || 'flat', amount: fee.amount || 0,
        description: fee.description || '', conditions: fee.conditions || {},
    });
    const [saving, setSaving] = useState(false);

    return (
        <div className="mt-3 pt-3 border-t space-y-3">
            <div className="grid grid-cols-2 gap-3">
                <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Fee Name</label>
                    <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.fee_name}
                        onChange={e => setForm({ ...form, fee_name: e.target.value })} />
                </div>
                <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Fee Type</label>
                    <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.fee_type}
                        onChange={e => setForm({ ...form, fee_type: e.target.value })}>
                        {feeTypes.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Calculation Mode</label>
                    <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.calc_mode}
                        onChange={e => setForm({ ...form, calc_mode: e.target.value })}>
                        {calcModes.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">
                        Amount {form.calc_mode === 'percentage' ? '(%)' : form.calc_mode === 'per_km' ? '($/km)' : '($)'}
                    </label>
                    <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.01"
                        value={form.amount} onChange={e => setForm({ ...form, amount: parseFloat(e.target.value) || 0 })} />
                </div>
            </div>
            <div>
                <label className="block text-xs font-semibold text-muted-foreground mb-1">Description (optional)</label>
                <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.description}
                    onChange={e => setForm({ ...form, description: e.target.value })} />
            </div>
            {form.fee_type === 'night' && (
                <div className="grid grid-cols-2 gap-3">
                    <div>
                        <label className="block text-xs font-semibold text-muted-foreground mb-1">Start Hour (0-23)</label>
                        <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" min="0" max="23"
                            value={form.conditions.start_hour ?? 23}
                            onChange={e => setForm({ ...form, conditions: { ...form.conditions, start_hour: parseInt(e.target.value) } })} />
                    </div>
                    <div>
                        <label className="block text-xs font-semibold text-muted-foreground mb-1">End Hour (0-23)</label>
                        <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" min="0" max="23"
                            value={form.conditions.end_hour ?? 5}
                            onChange={e => setForm({ ...form, conditions: { ...form.conditions, end_hour: parseInt(e.target.value) } })} />
                    </div>
                </div>
            )}
            <div className="flex gap-2 pt-1">
                <button onClick={async () => { setSaving(true); await onSave(form); setSaving(false); }} disabled={saving}
                    className="bg-primary text-primary-foreground px-4 py-1.5 rounded-lg text-sm font-semibold hover:bg-primary/90 disabled:opacity-50">
                    {saving ? 'Saving...' : 'Save'}
                </button>
                <button onClick={onCancel} className="bg-muted text-foreground px-4 py-1.5 rounded-lg text-sm font-semibold">Cancel</button>
            </div>
        </div>
    );
}
