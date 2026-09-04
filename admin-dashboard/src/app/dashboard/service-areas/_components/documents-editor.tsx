"use client";

import { useState } from "react";
import { Plus, Trash2, Pencil, ChevronDown, ChevronUp, FileText, Clock, ShieldCheck, ShieldAlert, CheckCircle, Image } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";

// ─── Documents Editor ───
// Extracted verbatim from service-areas/page.tsx.

export default function DocumentsEditor({ docs, onSave }: { docs: any[]; onSave: (d: any[]) => void }) {
  const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");
  const [rows, setRows] = useState(docs.length > 0 ? docs : [
    { key: 'drivers_license',       label: "Driver's License",    has_expiry: true,  required: true, requires_back_side: false },
    { key: 'vehicle_insurance',     label: 'Vehicle Insurance',   has_expiry: true,  required: true, requires_back_side: false },
    { key: 'vehicle_registration',  label: 'Vehicle Registration',has_expiry: true,  required: true, requires_back_side: false },
    { key: 'background_check',      label: 'Background Check',    has_expiry: true,  required: true, requires_back_side: false },
    { key: 'vehicle_inspection',    label: 'Vehicle Inspection',  has_expiry: true,  required: true, requires_back_side: false },
  ]);
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);

  const update = (idx: number, field: string, val: any) => {
    const next = [...rows];
    next[idx] = { ...next[idx], [field]: val };
    setRows(next);
    setDirty(true);
  };

  const addDoc = () => {
    setRows([...rows, { key: '', label: '', has_expiry: false, required: true, requires_back_side: false }]);
    setEditingIdx(rows.length);
    setDirty(true);
  };

  const removeDoc = (i: number) => {
    setRows(rows.filter((_, idx) => idx !== i));
    setEditingIdx(null);
    setDirty(true);
  };

  const moveDoc = (from: number, to: number) => {
    if (to < 0 || to >= rows.length) return;
    const next = [...rows];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setRows(next);
    setEditingIdx(to);
    setDirty(true);
  };

  const requiredCount = rows.filter(r => r.required !== false).length;
  const expiryCount = rows.filter(r => r.has_expiry).length;

  return (
    <div>
      {/* Header */}
      <div className="flex items-start justify-between mb-5">
        <div>
          <h4 className="font-bold text-foreground text-base">Required Documents</h4>
          <p className="text-sm text-muted-foreground mt-0.5">Define which documents drivers must upload to operate in this area.</p>
        </div>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1"><FileText className="h-3.5 w-3.5" /> {rows.length} total</span>
          <span className="flex items-center gap-1"><ShieldCheck className="h-3.5 w-3.5 text-success" /> {requiredCount} required</span>
          <span className="flex items-center gap-1"><Clock className="h-3.5 w-3.5 text-warning" /> {expiryCount} with expiry</span>
        </div>
      </div>

      {/* Document Cards Grid */}
      {rows.length === 0 ? (
        <div className="text-center py-12 bg-muted rounded-xl border-2 border-dashed border-border">
          <Image className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
          <p className="text-sm font-medium text-muted-foreground">No documents required</p>
          <p className="text-xs text-muted-foreground mt-1">Add document types that drivers need to upload</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {rows.map((r, i) => {
            const isEditing = editingIdx === i;
            return (
              <div key={i} className={`rounded-xl border overflow-hidden transition-all ${isEditing ? 'ring-2 ring-primary/30 border-primary/50 shadow-md' : 'bg-card hover:shadow-sm border-border'}`}>
                {/* Card Header with preview */}
                <div className={`px-4 py-3 flex items-center gap-3 ${isEditing ? 'bg-primary/10' : 'bg-muted'}`}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${r.required !== false ? 'bg-success/15' : 'bg-muted'}`}>
                    <FileText className={`h-5 w-5 ${r.required !== false ? 'text-success' : 'text-muted-foreground'}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-bold text-foreground truncate">{r.label || 'Untitled Document'}</p>
                    <p className="text-xs text-muted-foreground font-mono">{r.key || 'no_key'}</p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button onClick={() => moveDoc(i, i - 1)} disabled={i === 0} className="p-1 text-muted-foreground hover:text-foreground disabled:opacity-30" title="Move up">
                      <ChevronUp className="h-4 w-4" />
                    </button>
                    <button onClick={() => moveDoc(i, i + 1)} disabled={i === rows.length - 1} className="p-1 text-muted-foreground hover:text-foreground disabled:opacity-30" title="Move down">
                      <ChevronDown className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* Status badges */}
                <div className="px-4 py-2.5 flex items-center gap-2 flex-wrap">
                  {themeV2Enabled ? (
                    <>
                      {r.required !== false ? (
                        <Badge variant="outline-success" className="text-[10px]"><ShieldCheck className="h-3 w-3" /> Required</Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px]"><ShieldAlert className="h-3 w-3" /> Optional</Badge>
                      )}
                      {r.has_expiry ? (
                        <Badge variant="outline-warning" className="text-[10px]"><Clock className="h-3 w-3" /> Has Expiry</Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px]">No Expiry</Badge>
                      )}
                      {r.requires_back_side && (
                        <Badge variant="outline" className="text-[10px]"><Image className="h-3 w-3" /> Both Sides</Badge>
                      )}
                    </>
                  ) : (
                    <>
                      {r.required !== false ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-success/15 text-success"><ShieldCheck className="h-3 w-3" /> Required</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-muted text-muted-foreground"><ShieldAlert className="h-3 w-3" /> Optional</span>
                      )}
                      {r.has_expiry ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-warning/15 text-warning"><Clock className="h-3 w-3" /> Has Expiry</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-muted text-muted-foreground">No Expiry</span>
                      )}
                      {r.requires_back_side && (
                        // eslint-disable-next-line no-restricted-syntax -- decorative informational badge (not a health-state signal) distinguishing a document-format attribute (#2816)
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"><Image className="h-3 w-3" /> Both Sides</span>
                      )}
                    </>
                  )}
                </div>

                {/* Expand to edit or show actions */}
                {isEditing ? (
                  <div className="px-4 pb-4 space-y-3 border-t bg-card">
                    <div className="pt-3">
                      <label className="block text-[11px] font-semibold text-muted-foreground mb-1">Document Label</label>
                      <input className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" placeholder="e.g. Driver's License" value={r.label} onChange={e => update(i, 'label', e.target.value)} />
                    </div>
                    <div>
                      <label className="block text-[11px] font-semibold text-muted-foreground mb-1">Key (identifier)</label>
                      <input className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" placeholder="e.g. drivers_license" value={r.key} onChange={e => update(i, 'key', e.target.value)} />
                    </div>
                    <div className="flex flex-col gap-2.5">
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={r.has_expiry} onChange={e => update(i, 'has_expiry', e.target.checked)} className="accent-primary w-4 h-4" />
                        <span>Requires expiry date</span>
                      </label>
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={r.required !== false} onChange={e => update(i, 'required', e.target.checked)} className="accent-primary w-4 h-4" />
                        <span>Required</span>
                      </label>
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={!!r.requires_back_side} onChange={e => update(i, 'requires_back_side', e.target.checked)} className="accent-primary w-4 h-4" />
                        <span>Requires both sides <span className="text-xs text-muted-foreground">(front &amp; back photo)</span></span>
                      </label>
                    </div>
                    <div className="flex items-center justify-between pt-2">
                      <button onClick={() => removeDoc(i)} className="flex items-center gap-1 text-sm text-destructive hover:text-destructive/80 font-medium">
                        <Trash2 className="h-3.5 w-3.5" /> Remove
                      </button>
                      <button onClick={() => setEditingIdx(null)} className="px-4 py-1.5 bg-muted text-foreground text-sm rounded-lg font-semibold hover:bg-muted/70">Done</button>
                    </div>
                  </div>
                ) : (
                  <div className="px-4 pb-3 flex items-center justify-between">
                    <button onClick={() => setEditingIdx(i)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-primary font-medium">
                      <Pencil className="h-3 w-3" /> Edit
                    </button>
                    <button onClick={() => removeDoc(i)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-destructive">
                      <Trash2 className="h-3 w-3" /> Remove
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-3 mt-5 pt-4 border-t">
        <button onClick={addDoc} className="flex items-center gap-1.5 text-sm text-primary font-semibold hover:text-primary/80 transition">
          <Plus className="h-4 w-4" /> Add document type
        </button>
        <div className="flex-1" />
        {dirty && <span className="text-xs text-warning font-medium">Unsaved changes</span>}
        <button onClick={() => { onSave(rows); setDirty(false); }} className={`px-6 py-2.5 rounded-xl text-sm font-semibold transition ${dirty ? 'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm' : 'bg-muted text-muted-foreground'}`}>
          <span className="flex items-center gap-1.5"><CheckCircle className="h-4 w-4" /> Save Documents</span>
        </button>
      </div>
    </div>
  );
}
