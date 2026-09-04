"use client";

import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";

// ─── Vehicle Pricing Editor ───
// Extracted verbatim from service-areas/page.tsx.

export default function VehiclePricingEditor({ pricing, vehicleTypes, onSave }: { pricing: any[]; vehicleTypes: { id: string; name: string }[]; onSave: (p: any[]) => void }) {
  // Mirror saved state exactly — do NOT inject local defaults when empty.
  // Pre-populating defaults caused "delete doesn't save" bugs: deleting all
  // rows and saving empty array would re-show defaults on refresh, looking
  // like the delete failed.
  const [rows, setRows] = useState<any[]>(pricing || []);
  const [dirty, setDirty] = useState(false);

  // Re-sync local rows when parent reloads (e.g. after save).
  useEffect(() => { setRows(pricing || []); setDirty(false); }, [pricing]);

  const update = (idx: number, field: string, val: string) => {
    const next = [...rows];
    next[idx] = { ...next[idx], [field]: field === 'vehicle_type' ? val : parseFloat(val) || 0 };
    setRows(next);
    setDirty(true);
  };

  const addRow = () => { setRows([...rows, { vehicle_type: '', base_fare: 0, per_km: 0, per_min: 0, min_fare: 0, booking_fee: 0 }]); setDirty(true); };
  // Persist immediately on delete so the row doesn't reappear if the user
  // forgets to click "Save Pricing".
  const removeRow = (i: number) => {
    const next = rows.filter((_, idx) => idx !== i);
    setRows(next);
    onSave(next);
    setDirty(false);
  };

  // Vehicle types already taken by other rows — prevents picking the
  // same type twice in the same area. The current row's own value is
  // still allowed so editing doesn't disable its own <option>.
  const takenNames = (currentIdx: number) =>
    new Set(rows.map((r, i) => (i === currentIdx ? null : r.vehicle_type)).filter(Boolean));

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground border-b">
              <th className="pb-2 pr-2">Vehicle Type</th>
              <th className="pb-2 pr-2">Base Fare ($)</th>
              <th className="pb-2 pr-2">Per KM ($)</th>
              <th className="pb-2 pr-2">Per Min ($)</th>
              <th className="pb-2 pr-2">Min Fare ($)</th>
              <th className="pb-2 pr-2">Booking Fee ($)</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const taken = takenNames(i);
              const currentVal = (r as any).vehicle_type || "";
              // If the saved row references a type that no longer exists
              // in vehicle_types (deleted from the Vehicle Types page),
              // keep it as a stale option so the operator still sees it
              // and can pick a replacement.
              const hasCurrentInList = vehicleTypes.some(v => v.name === currentVal);
              return (
                <tr key={i} className="border-b">
                  <td className="py-2 pr-2">
                    <select
                      className="w-full border rounded-lg px-2 py-1.5 text-sm bg-card"
                      value={currentVal}
                      onChange={e => update(i, 'vehicle_type', e.target.value)}
                    >
                      <option value="" disabled>Select vehicle type…</option>
                      {vehicleTypes.map(v => (
                        <option key={v.id} value={v.name} disabled={taken.has(v.name)}>
                          {v.name}{taken.has(v.name) ? ' (already added)' : ''}
                        </option>
                      ))}
                      {currentVal && !hasCurrentInList && (
                        <option value={currentVal}>{currentVal} (deleted)</option>
                      )}
                    </select>
                  </td>
                  {['base_fare', 'per_km', 'per_min', 'min_fare', 'booking_fee'].map(f => (
                    <td key={f} className="py-2 pr-2">
                      <input className="w-full border rounded-lg px-2 py-1.5 text-sm"
                        type="number" step="0.01"
                        value={(r as any)[f]} onChange={e => update(i, f, e.target.value)} />
                    </td>
                  ))}
                  <td className="py-2"><button onClick={() => removeRow(i)} className="text-muted-foreground hover:text-destructive"><Trash2 className="h-4 w-4" /></button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {vehicleTypes.length === 0 && (
        <p className="text-xs text-warning mt-2">
          No vehicle types defined yet. Add them under <span className="font-semibold">Dashboard → Vehicle Types</span> first.
        </p>
      )}
      <div className="flex gap-3 mt-3">
        <button onClick={addRow} className="text-sm text-primary font-semibold hover:underline">+ Add vehicle type</button>
        <button
          onClick={() => { onSave(rows); setDirty(false); }}
          className={`px-5 py-2 rounded-xl text-sm font-semibold transition ${dirty ? 'bg-primary text-primary-foreground hover:bg-primary/90' : 'bg-muted text-muted-foreground cursor-not-allowed'}`}
          disabled={!dirty}
        >
          {dirty ? 'Save Pricing*' : 'Saved'}
        </button>
      </div>
    </div>
  );
}
