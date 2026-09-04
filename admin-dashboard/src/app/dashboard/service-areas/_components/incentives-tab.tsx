"use client";

import { useEffect, useState } from "react";
import { Trash2, Gift, ToggleLeft, ToggleRight } from "lucide-react";
import { getIncentives, createIncentive, toggleIncentive, deleteIncentive } from "@/lib/api";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";
import { Badge } from "@/components/ui/badge";

// --- Incentives Tab ---
// Extracted verbatim from service-areas/page.tsx.

const INCENTIVE_TYPES = [
  { value: 'per_ride', label: 'Per Ride', desc: 'Flat bonus on every ride' },
  { value: 'peak_hours', label: 'Peak Hours', desc: 'Bonus during busy times' },
  { value: 'time_limited', label: 'Time Limited', desc: 'Limited-time campaign' },
  { value: 'min_distance', label: 'Min Distance', desc: 'Bonus for longer rides' },
  { value: 'area_boost', label: 'Area Boost', desc: 'Boost for this area' },
];

export default function IncentivesTab({ areaId, areaName, vehicleTypes }: { areaId: string; areaName: string; vehicleTypes: { id: string; name: string }[] }) {
  const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");
  const [incentives, setIncentives] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    name: '', description: '', incentive_type: 'per_ride', bonus_amount: 5,
    bonus_type: 'flat' as string, vehicle_type_id: '', is_active: true, priority: 0,
    max_budget: '',
  });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await getIncentives(areaId);
      setIncentives(data || []);
    } catch (e) { console.error('[incentives] load:', e); }
    setLoading(false);
  };

  useEffect(() => { load(); }, [areaId]);

  const handleCreate = async () => {
    setSaving(true);
    try {
      await createIncentive({
        ...form,
        service_area_id: areaId,
        vehicle_type_id: form.vehicle_type_id || null,
        bonus_amount: parseFloat(String(form.bonus_amount)) || 0,
        max_budget: form.max_budget ? parseFloat(form.max_budget) : null,
      });
      setShowForm(false);
      setForm({ name: '', description: '', incentive_type: 'per_ride', bonus_amount: 5, bonus_type: 'flat', vehicle_type_id: '', is_active: true, priority: 0, max_budget: '' });
      await load();
    } catch (e) { console.error('[incentives] create:', e); }
    setSaving(false);
  };

  const handleToggle = async (id: string) => {
    try { await toggleIncentive(id); await load(); } catch (e) { console.error('[incentives] toggle:', e); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this incentive?')) return;
    try { await deleteIncentive(id); await load(); } catch (e) { console.error('[incentives] delete:', e); }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h4 className="font-bold text-foreground">Driver Ride Incentives</h4>
          <p className="text-sm text-muted-foreground">Bonuses shown to drivers on the ride offer screen in {areaName}.</p>
        </div>
        {!showForm && (
          <button onClick={() => setShowForm(true)}
            // eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816)
            className="flex items-center gap-2 bg-amber-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-amber-600">
            <Gift className="h-4 w-4" /> Add Incentive
          </button>
        )}
      </div>

      {showForm && (
        // eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816)
        <div className="bg-amber-50 border border-amber-200 dark:bg-amber-900/20 dark:border-amber-800 rounded-xl p-5 mb-5">
          {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
          <h5 className="font-bold text-amber-900 dark:text-amber-200 mb-3 flex items-center gap-2">
            <Gift className="h-4 w-4" /> New Incentive for {areaName}
          </h5>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <label className="block text-xs font-semibold text-amber-800 dark:text-amber-300 mb-1">Name *</label>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <input className="w-full border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 text-sm"
                value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. Peak Hour Bonus" />
            </div>
            <div>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <label className="block text-xs font-semibold text-amber-800 dark:text-amber-300 mb-1">Type</label>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <select className="w-full border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 text-sm"
                value={form.incentive_type} onChange={e => setForm({ ...form, incentive_type: e.target.value })}>
                {INCENTIVE_TYPES.map(t => <option key={t.value} value={t.value}>{t.label} — {t.desc}</option>)}
              </select>
            </div>
            <div>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <label className="block text-xs font-semibold text-amber-800 dark:text-amber-300 mb-1">Bonus Amount ($)</label>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <input className="w-full border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 text-sm"
                type="number" step="0.50" min="0.50" max="500"
                value={form.bonus_amount} onChange={e => setForm({ ...form, bonus_amount: parseFloat(e.target.value) || 0 })} />
            </div>
            <div>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <label className="block text-xs font-semibold text-amber-800 dark:text-amber-300 mb-1">Vehicle Type (optional)</label>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <select className="w-full border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 text-sm"
                value={form.vehicle_type_id} onChange={e => setForm({ ...form, vehicle_type_id: e.target.value })}>
                <option value="">All vehicle types</option>
                {vehicleTypes.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
            </div>
            <div>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <label className="block text-xs font-semibold text-amber-800 dark:text-amber-300 mb-1">Description</label>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <input className="w-full border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 text-sm"
                value={form.description} onChange={e => setForm({ ...form, description: e.target.value })}
                placeholder="Shown to drivers on ride offer" />
            </div>
            <div>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <label className="block text-xs font-semibold text-amber-800 dark:text-amber-300 mb-1">Budget Cap ($ optional)</label>
              {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
              <input className="w-full border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 text-sm"
                type="number" step="100" min="0"
                value={form.max_budget} onChange={e => setForm({ ...form, max_budget: e.target.value })}
                placeholder="Leave empty for unlimited" />
            </div>
          </div>
          <div className="flex gap-3">
            <button onClick={handleCreate} disabled={saving || !form.name}
              // eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816)
              className="bg-amber-600 text-white px-5 py-2 rounded-lg text-sm font-semibold hover:bg-amber-700 disabled:opacity-50">
              {saving ? 'Creating...' : 'Create Incentive'}
            </button>
            <button onClick={() => setShowForm(false)} className="text-muted-foreground px-4 py-2 rounded-lg text-sm hover:bg-muted">Cancel</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-center py-8 text-muted-foreground">Loading incentives...</div>
      ) : incentives.length === 0 ? (
        <div className="text-center py-8 text-muted-foreground">
          <Gift className="h-8 w-8 mx-auto mb-2 opacity-40" />
          <p className="font-medium">No incentives configured</p>
          <p className="text-sm">Add incentives to attract drivers to accept rides in this area.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {incentives.map(inc => {
            const typeInfo = INCENTIVE_TYPES.find(t => t.value === inc.incentive_type);
            const vtName = inc.vehicle_type_id
              ? vehicleTypes.find((v: any) => v.id === inc.vehicle_type_id)?.name || 'Specific vehicle'
              : 'All vehicles';
            return (
              // eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816)
              <div key={inc.id} className={`flex items-center gap-4 p-4 rounded-xl border ${inc.is_active ? 'bg-card border-amber-200 dark:border-amber-800' : 'bg-muted border-border opacity-60'}`}>
                {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${inc.is_active ? 'bg-amber-100 dark:bg-amber-900/30' : 'bg-muted'}`}>
                  {/* eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent, not a health-state signal (#2816) */}
                  <Gift className={`h-5 w-5 ${inc.is_active ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'}`} />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-foreground">{inc.name}</span>
                    {themeV2Enabled ? (
                      <Badge variant="outline" className="text-xs">${parseFloat(inc.bonus_amount).toFixed(2)}</Badge>
                    ) : (
                      // eslint-disable-next-line no-restricted-syntax -- decorative incentive-feature brand accent for the bonus-amount badge, not a health-state signal (#2816)
                      <span className="px-2 py-0.5 bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 text-xs font-bold rounded-md">${parseFloat(inc.bonus_amount).toFixed(2)}</span>
                    )}
                    <span className="px-2 py-0.5 bg-muted text-muted-foreground text-xs font-semibold rounded-md">{typeInfo?.label || inc.incentive_type}</span>
                  </div>
                  <p className="text-sm text-muted-foreground">{vtName}{inc.description ? ` · ${inc.description}` : ''}</p>
                  {inc.max_budget && (
                    <p className="text-xs text-muted-foreground mt-1">Budget: ${parseFloat(inc.budget_used || 0).toFixed(0)} / ${parseFloat(inc.max_budget).toFixed(0)}</p>
                  )}
                </div>
                <button onClick={() => handleToggle(inc.id)}
                  className={`p-2 rounded-lg ${inc.is_active ? 'text-success hover:bg-success/10' : 'text-muted-foreground hover:bg-muted'}`}
                  title={inc.is_active ? 'Deactivate' : 'Activate'}>
                  {inc.is_active ? <ToggleRight className="h-5 w-5" /> : <ToggleLeft className="h-5 w-5" />}
                </button>
                <button onClick={() => handleDelete(inc.id)}
                  className="p-2 rounded-lg text-destructive/70 hover:bg-destructive/10 hover:text-destructive" title="Delete">
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
