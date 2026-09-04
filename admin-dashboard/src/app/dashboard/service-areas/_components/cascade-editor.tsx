"use client";

import { useEffect, useState } from "react";
import { Plus, Trash2, Car, ArrowRightLeft } from "lucide-react";

// ─── Dispatch Cascade Editor ───
// Extracted verbatim from service-areas/page.tsx.

export default function CascadeEditor({
  cascadeMap,
  vehicleTypes,
  onSave,
}: {
  cascadeMap: { from: string; to: string[] }[];
  vehicleTypes: { id: string; name: string }[];
  onSave: (map: { from: string; to: string[] }[]) => Promise<void>;
}) {
  const [rules, setRules] = useState<{ from: string; to: string[] }[]>(cascadeMap || []);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { setRules(cascadeMap || []); setDirty(false); }, [cascadeMap]);

  const usedFromIds = new Set(rules.map(r => r.from));

  const addRule = () => {
    const available = vehicleTypes.filter(v => !usedFromIds.has(v.id));
    if (available.length === 0) return;
    setRules([...rules, { from: available[0].id, to: [] }]);
    setDirty(true);
  };

  const removeRule = (idx: number) => {
    setRules(rules.filter((_, i) => i !== idx));
    setDirty(true);
  };

  const setFrom = (idx: number, fromId: string) => {
    const next = rules.map((r, i) => i === idx ? { ...r, from: fromId, to: r.to.filter(id => id !== fromId) } : r);
    setRules(next);
    setDirty(true);
  };

  const toggleTo = (idx: number, typeId: string) => {
    const current = rules[idx].to;
    const next = current.includes(typeId) ? current.filter(id => id !== typeId) : [...current, typeId];
    setRules(rules.map((r, i) => i === idx ? { ...r, to: next } : r));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    await onSave(rules.filter(r => r.from && r.to.length > 0));
    setSaving(false);
    setSaved(true);
    setDirty(false);
    setTimeout(() => setSaved(false), 2000);
  };

  const vtName = (id: string) => vehicleTypes.find(v => v.id === id)?.name ?? id;

  return (
    <div>
      <div className="mb-4">
        <h4 className="font-bold text-foreground">Dispatch Cascade Rules</h4>
        <p className="text-sm text-muted-foreground mt-0.5">
          When a ride is booked for a vehicle type and no driver of that type is available,
          dispatch automatically tries the upgrade types listed here.
          Only vehicle types configured under <span className="font-semibold">Vehicle Pricing</span> for
          this area appear below.
        </p>
      </div>

      {vehicleTypes.length === 0 ? (
        <div className="text-center py-10 bg-warning/10 rounded-xl border-2 border-dashed border-warning/30">
          <Car className="h-10 w-10 text-warning mx-auto mb-3" />
          <p className="text-warning font-medium">No vehicle types configured for this area</p>
          <p className="text-warning text-sm mt-1">
            Add vehicle types under the <span className="font-semibold">Vehicle Pricing</span> tab first,
            then return here to set up cascade rules.
          </p>
        </div>
      ) : rules.length === 0 ? (
        <div className="text-center py-10 bg-muted rounded-xl border-2 border-dashed border-border">
          <ArrowRightLeft className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
          <p className="text-muted-foreground font-medium">No cascade rules</p>
          <p className="text-muted-foreground text-sm mt-1">
            Add a rule to allow larger vehicles to fill in when the exact type has no available driver.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {rules.map((rule, idx) => {
            const toOptions = vehicleTypes.filter(v => v.id !== rule.from);
            const takenFromIds = new Set(rules.filter((_, i) => i !== idx).map(r => r.from));
            return (
              <div key={idx} className="rounded-xl border bg-card p-4">
                <div className="flex items-start gap-4">
                  <div className="flex-1">
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">When booked type is</label>
                    <select
                      className="w-full border rounded-lg px-3 py-2 text-sm"
                      value={rule.from}
                      onChange={e => setFrom(idx, e.target.value)}
                    >
                      {vehicleTypes.map(v => (
                        <option key={v.id} value={v.id} disabled={takenFromIds.has(v.id)}>
                          {v.name}{takenFromIds.has(v.id) ? ' (used in another rule)' : ''}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="flex items-center pt-6 text-muted-foreground">
                    <ArrowRightLeft className="h-4 w-4" />
                  </div>

                  <div className="flex-1">
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Also offer to drivers of</label>
                    <div className="border rounded-lg p-2 space-y-1 min-h-[42px]">
                      {toOptions.length === 0 ? (
                        <p className="text-xs text-muted-foreground px-1 py-1">No other vehicle types available</p>
                      ) : toOptions.map(v => (
                        <label key={v.id} className="flex items-center gap-2 text-sm cursor-pointer px-1">
                          <input
                            type="checkbox"
                            checked={rule.to.includes(v.id)}
                            onChange={() => toggleTo(idx, v.id)}
                            className="accent-primary"
                          />
                          {v.name}
                        </label>
                      ))}
                    </div>
                    {rule.to.length > 0 && (
                      <p className="text-xs text-muted-foreground mt-1">
                        Cascade order: {rule.to.map(id => vtName(id)).join(' → ')}
                      </p>
                    )}
                  </div>

                  <button
                    onClick={() => removeRule(idx)}
                    className="pt-6 text-muted-foreground hover:text-destructive"
                    title="Remove rule"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex items-center justify-between mt-4 pt-4 border-t">
        <button
          onClick={addRule}
          disabled={vehicleTypes.every(v => usedFromIds.has(v.id))}
          className="flex items-center gap-1.5 text-sm text-primary font-semibold hover:text-primary/80 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Plus className="h-4 w-4" /> Add cascade rule
        </button>
        <button
          onClick={handleSave}
          disabled={!dirty || saving}
          // eslint-disable-next-line no-restricted-syntax -- solid-fill white-text success state; --success fails WCAG AA against white text in dark mode, cannot convert to bg-success (#2816)
          className={`px-6 py-2.5 rounded-xl text-sm font-semibold transition ${saved ? 'bg-green-500 text-white' : dirty ? 'bg-primary text-primary-foreground hover:bg-primary/90' : 'bg-muted text-muted-foreground cursor-not-allowed'} disabled:opacity-50`}
        >
          {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Cascade Rules'}
        </button>
      </div>
    </div>
  );
}
