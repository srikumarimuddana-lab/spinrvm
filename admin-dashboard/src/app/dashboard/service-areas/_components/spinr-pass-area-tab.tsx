"use client";

import { useState } from "react";
import { Plus, Trash2, Pencil, CreditCard, ToggleLeft, ToggleRight } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { createSubscriptionPlan, updateSubscriptionPlan, deleteSubscriptionPlan, getDriverSubscriptions } from "@/lib/api";
import { isSpinrPassPlanNameValid, isSpinrPassPlanPriceValid } from "@/lib/spinrPassAreaPlanSchema";
import { FieldToggle } from "./service-area-shared";

// --- Spinr Pass per-area tab with full plan management ---
// Extracted verbatim from service-areas/page.tsx.

const DURATION_OPTIONS = [
  { label: "Daily", value: 1 },
  { label: "Weekly", value: 7 },
  { label: "Monthly", value: 30 },
  { label: "Yearly", value: 365 },
];

export default function SpinrPassAreaTab({ area, plans, onToggle, onRequiredToggle, onPlansChanged }: {
  area: any; plans: any[]; onToggle: (v: boolean) => void; onRequiredToggle: (v: boolean) => void; onPlansChanged: () => void;
}) {
  const { toast } = useToast();
  const [planDeleteTarget, setPlanDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [subs, setSubs] = useState<any[]>([]);
  const [subsLoaded, setSubsLoaded] = useState(false);
  const [form, setForm] = useState({ name: "", price: "", duration_days: 30, rides_per_day: -1, description: "", features: "", is_active: true });

  const loadSubs = async () => {
    try { const s = await getDriverSubscriptions(); setSubs(s || []); } catch {}
    setSubsLoaded(true);
  };

  const resetForm = () => { setShowForm(false); setEditingId(null); setForm({ name: "", price: "", duration_days: 30, rides_per_day: -1, description: "", features: "", is_active: true }); };

  const handleSubmit = async () => {
    if (!isSpinrPassPlanNameValid(form.name) || !isSpinrPassPlanPriceValid(form.price)) return;
    const data = {
      name: form.name, price: parseFloat(form.price), duration_days: form.duration_days,
      rides_per_day: form.rides_per_day, description: form.description,
      features: form.features ? form.features.split(",").map(f => f.trim()).filter(Boolean) : [],
      is_active: form.is_active,
    };
    try {
      if (editingId) { await updateSubscriptionPlan(editingId, data); }
      else { await createSubscriptionPlan(data); }
      resetForm(); onPlansChanged();
    } catch (e: any) { toast({ title: "Failed to save plan", description: e?.message, variant: "destructive" }); }
  };

  const handleEdit = (p: any) => {
    setEditingId(p.id);
    setForm({ name: p.name, price: String(p.price), duration_days: p.duration_days, rides_per_day: p.rides_per_day, description: p.description || "", features: (p.features || []).join(", "), is_active: p.is_active });
    setShowForm(true);
  };

  const handleDeletePlan = (p: any) => {
    setPlanDeleteTarget({ id: p.id, name: p.name });
  };

  const confirmPlanDelete = async () => {
    if (!planDeleteTarget) return;
    try { await deleteSubscriptionPlan(planDeleteTarget.id); onPlansChanged(); }
    catch (e: any) { toast({ title: "Failed to delete plan", description: e?.message, variant: "destructive" }); }
    finally { setPlanDeleteTarget(null); }
  };

  const handleTogglePlan = async (p: any) => {
    await updateSubscriptionPlan(p.id, { is_active: !p.is_active }); onPlansChanged();
  };

  const getDurationLabel = (days: number) => DURATION_OPTIONS.find(d => d.value === days)?.label || `${days} days`;

  const enabled = area.spinr_pass_enabled !== false;
  const required = area.subscription_required === true;

  return (
    <div>
      {/* Kill switch */}
      <div className={`flex items-center justify-between p-4 rounded-xl mb-3 ${enabled ? 'bg-success/10 border border-success/30' : 'bg-muted border border-border'}`}>
        <div>
          <h4 className="font-bold text-foreground">Spinr Pass for {area.name}</h4>
          <p className="text-sm text-muted-foreground">
            {enabled ? 'Drivers in this area can see and subscribe to plans' : 'Disabled — drivers see "It\'s Free Right Now!" instead'}
          </p>
        </div>
        <FieldToggle label={enabled ? "ON" : "OFF"} value={enabled} onSave={onToggle} />
      </div>

      {/* Mandatory-subscription toggle */}
      <div className={`flex items-center justify-between p-4 rounded-xl mb-5 ${required ? 'bg-warning/10 border border-warning/30' : 'bg-muted border border-border'}`}>
        <div>
          <h4 className="font-bold text-foreground">Require Subscription to Drive</h4>
          <p className="text-sm text-muted-foreground">
            {required
              ? 'Drivers must have an active Spinr Pass to go online, receive offers, and accept rides'
              : 'Spinr Pass is optional — all verified drivers can work in this area'}
          </p>
        </div>
        <FieldToggle label={required ? "Required" : "Optional"} value={required} onSave={onRequiredToggle} />
      </div>

      {/* Plan management */}
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-bold text-foreground">Subscription Plans</h4>
        <button onClick={() => { resetForm(); setShowForm(true); }} className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-2 rounded-xl text-sm font-semibold hover:bg-primary/90">
          <Plus className="h-4 w-4" /> New Plan
        </button>
      </div>

      {/* Create/Edit Form */}
      {showForm && (
        <div className="bg-card rounded-xl border p-5 mb-5 shadow-sm">
          <h5 className="font-bold mb-3">{editingId ? "Edit Plan" : "New Plan"}</h5>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Plan Name *</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="e.g. Basic" value={form.name} onChange={e => setForm({...form, name: e.target.value})} />
            </div>
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Price (CAD) *</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.01" placeholder="19.99" value={form.price} onChange={e => setForm({...form, price: e.target.value})} />
            </div>
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Duration</label>
              <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.duration_days} onChange={e => setForm({...form, duration_days: parseInt(e.target.value)})}>
                {DURATION_OPTIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Rides Per Day</label>
              <div className="flex gap-1.5 flex-wrap">
                <button onClick={() => setForm({...form, rides_per_day: -1})} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${form.rides_per_day === -1 ? "bg-primary text-primary-foreground border-primary" : "bg-card border-border"}`}>Unlimited</button>
                {[4, 8, 12, 20].map(n => (
                  <button key={n} onClick={() => setForm({...form, rides_per_day: n})} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${form.rides_per_day === n ? "bg-primary text-primary-foreground border-primary" : "bg-card border-border"}`}>{n}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-xs font-semibold text-muted-foreground mb-1">Description</label>
              <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="Brief description" value={form.description} onChange={e => setForm({...form, description: e.target.value})} />
            </div>
          </div>
          <div className="mb-3">
            <label className="block text-xs font-semibold text-muted-foreground mb-1">Features (comma-separated)</label>
            <input className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="Priority support, Surge protection" value={form.features} onChange={e => setForm({...form, features: e.target.value})} />
          </div>
          <div className="flex items-center gap-4 mb-4">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_active} onChange={e => setForm({...form, is_active: e.target.checked})} className="accent-primary" /> Active</label>
          </div>
          <div className="flex gap-2">
            <button onClick={handleSubmit} className="bg-primary text-primary-foreground px-5 py-2 rounded-xl text-sm font-semibold hover:bg-primary/90">{editingId ? "Save" : "Create Plan"}</button>
            <button onClick={resetForm} className="bg-muted text-foreground px-5 py-2 rounded-xl text-sm font-semibold">Cancel</button>
          </div>
        </div>
      )}

      {/* Plans List */}
      {plans.length === 0 ? (
        <div className="text-center py-12 bg-muted rounded-xl">
          <CreditCard className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
          <p className="text-muted-foreground font-medium">No subscription plans yet</p>
          <p className="text-muted-foreground text-sm mt-1">Create your first Spinr Pass plan above</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
          {plans.map(p => (
            <div key={p.id} className={`bg-card rounded-xl border p-5 relative ${!p.is_active ? "opacity-50" : ""}`}>
              <button onClick={() => handleTogglePlan(p)} className="absolute top-3 right-3">
                {p.is_active ? <ToggleRight className="h-5 w-5 text-success" /> : <ToggleLeft className="h-5 w-5 text-muted-foreground" />}
              </button>
              <h5 className="font-bold text-foreground text-lg">{p.name}</h5>
              {p.description && <p className="text-muted-foreground text-xs mt-0.5">{p.description}</p>}
              <div className="mt-2 mb-3">
                <span className="text-2xl font-extrabold text-primary">${p.price?.toFixed(2)}</span>
                <span className="text-muted-foreground text-xs ml-1">/ {getDurationLabel(p.duration_days).toLowerCase()}</span>
              </div>
              <p className="text-sm text-foreground mb-1">
                {p.rides_per_day === -1 ? 'Unlimited rides/day' : `${p.rides_per_day} rides/day`}
              </p>
              <p className="text-xs text-muted-foreground">{p.subscriber_count || 0} subscribers</p>
              {(p.features || []).length > 0 && (
                <div className="border-t mt-3 pt-2">
                  {p.features.map((f: string, i: number) => <p key={i} className="text-xs text-muted-foreground py-0.5">✓ {f}</p>)}
                </div>
              )}
              <div className="flex gap-2 mt-3 pt-2 border-t">
                <button onClick={() => handleEdit(p)} className="flex-1 text-center py-1.5 text-xs font-semibold text-foreground hover:bg-muted rounded-lg"><Pencil className="h-3 w-3 inline mr-1" />Edit</button>
                <button onClick={() => handleDeletePlan(p)} className="flex-1 text-center py-1.5 text-xs font-semibold text-destructive hover:bg-destructive/10 rounded-lg"><Trash2 className="h-3 w-3 inline mr-1" />Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Subscribers */}
      {plans.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-bold text-foreground">Subscribers</h4>
            {!subsLoaded && <button onClick={loadSubs} className="text-sm text-primary font-semibold hover:underline">Load subscribers</button>}
          </div>
          {subsLoaded && (
            subs.length === 0 ? (
              <p className="text-muted-foreground text-sm">No subscribers yet.</p>
            ) : (
              <div className="bg-card rounded-xl border overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-muted text-left">
                    <tr>
                      <th className="px-4 py-2 font-semibold text-foreground text-xs">Driver</th>
                      <th className="px-4 py-2 font-semibold text-foreground text-xs">Plan</th>
                      <th className="px-4 py-2 font-semibold text-foreground text-xs">Status</th>
                      <th className="px-4 py-2 font-semibold text-foreground text-xs">Expires</th>
                    </tr>
                  </thead>
                  <tbody>
                    {subs.map(s => (
                      <tr key={s.id} className="border-t">
                        <td className="px-4 py-2 font-mono text-xs">{s.driver_id?.slice(0, 8)}...</td>
                        <td className="px-4 py-2">{s.plan_name}</td>
                        <td className="px-4 py-2">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${s.status === 'active' ? 'bg-success/15 text-success' : 'bg-muted text-muted-foreground'}`}>{s.status?.toUpperCase()}</span>
                        </td>
                        <td className="px-4 py-2 text-xs text-muted-foreground">{s.expires_at ? new Date(s.expires_at).toLocaleDateString() : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </div>
      )}

      <AlertDialog open={!!planDeleteTarget} onOpenChange={(open) => { if (!open) setPlanDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete "{planDeleteTarget?.name}" plan?</AlertDialogTitle>
            <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmPlanDelete} className="bg-destructive hover:bg-destructive/90">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
