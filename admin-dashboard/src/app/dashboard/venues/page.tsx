"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { MapPin, Plus, Trash2, Save, X, RefreshCw, Ruler } from "lucide-react";
import {
  getVenues, createVenue, updateVenue, deleteVenue, getServiceAreas,
  type Venue, type VenueUpsert, type VenuePickupPoint,
} from "@/lib/api";

const EMPTY: VenueUpsert = {
  name: "", center_lat: 0, center_lng: 0, radius_m: 150, pickup_points: [], service_area_id: null, is_active: true,
};

export default function VenuesPage() {
  const [venues, setVenues] = useState<Venue[]>([]);
  const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
  const [areaFilter, setAreaFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<{ id: string | null; data: VenueUpsert } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getVenues(areaFilter ? { service_area_id: areaFilter } : undefined);
      setVenues(res.venues || []);
    } catch (e: any) {
      setError(e?.message || "Failed to load venues");
    } finally {
      setLoading(false);
    }
  }, [areaFilter]);

  useEffect(() => { load(); }, [load]);

  // Service areas drive both the list filter and the per-venue area label.
  useEffect(() => {
    getServiceAreas()
      .then((rows) => setServiceAreas((rows || []).map((a: any) => ({ id: a.id, name: a.name }))))
      .catch(() => {});
  }, []);

  const areaNameById = useMemo(() => {
    const m: Record<string, string> = {};
    for (const a of serviceAreas) m[a.id] = a.name;
    return m;
  }, [serviceAreas]);

  const startNew = () => setEditing({ id: null, data: { ...EMPTY, pickup_points: [] } });
  const startEdit = (v: Venue) => setEditing({
    id: v.id,
    data: {
      name: v.name, center_lat: v.center_lat, center_lng: v.center_lng, radius_m: v.radius_m,
      pickup_points: (v.pickup_points || []).map((p) => ({ ...p })),
      service_area_id: v.service_area_id ?? null, is_active: v.is_active,
    },
  });

  const patch = (p: Partial<VenueUpsert>) => setEditing((e) => e && ({ ...e, data: { ...e.data, ...p } }));
  const patchPoint = (i: number, p: Partial<VenuePickupPoint>) =>
    setEditing((e) => {
      if (!e) return e;
      const pts = [...e.data.pickup_points];
      pts[i] = { ...pts[i], ...p };
      return { ...e, data: { ...e.data, pickup_points: pts } };
    });
  const addPoint = () => patch({ pickup_points: [...(editing?.data.pickup_points || []), { name: "", lat: 0, lng: 0 }] });
  const removePoint = (i: number) => patch({ pickup_points: (editing?.data.pickup_points || []).filter((_, j) => j !== i) });

  const save = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const body: VenueUpsert = {
        ...editing.data,
        pickup_points: editing.data.pickup_points.filter((p) => p.name.trim()),
      };
      if (editing.id) await updateVenue(editing.id, body);
      else await createVenue(body);
      setEditing(null);
      await load();
    } catch (e: any) {
      alert(e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (v: Venue) => {
    if (!confirm(`Delete venue "${v.name}"? This removes its curated pickup points.`)) return;
    try { await deleteVenue(v.id); await load(); } catch (e: any) { alert(e?.message || "Delete failed"); }
  };

  const d = editing?.data;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><MapPin className="h-5 w-5" /> Pickup Venues</h1>
          <p className="text-sm text-muted-foreground">
            Curated meeting points for malls, the airport, and other large venues where a pin can land somewhere a car can't reach.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={areaFilter}
            onChange={(e) => setAreaFilter(e.target.value)}
            className="text-sm border rounded-lg px-3 py-2 bg-background hover:bg-muted"
            aria-label="Filter by service area"
          >
            <option value="">All service areas</option>
            {serviceAreas.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          <button onClick={load} className="flex items-center gap-1.5 text-sm border rounded-lg px-3 py-2 hover:bg-muted">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
          <button onClick={startNew} className="flex items-center gap-1.5 text-sm font-semibold bg-primary text-primary-foreground rounded-lg px-3 py-2 hover:bg-primary/90">
            <Plus className="h-4 w-4" /> Add venue
          </button>
        </div>
      </div>

      {editing && d && (
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">{editing.id ? "Edit venue" : "New venue"}</CardTitle>
            <button onClick={() => setEditing(null)} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <label className="text-sm">Name<Input value={d.name} onChange={(e) => patch({ name: e.target.value })} placeholder="Cornwall Centre" /></label>
              <label className="text-sm">Center latitude<Input type="number" value={d.center_lat} onChange={(e) => patch({ center_lat: parseFloat(e.target.value) || 0 })} /></label>
              <label className="text-sm">Center longitude<Input type="number" value={d.center_lng} onChange={(e) => patch({ center_lng: parseFloat(e.target.value) || 0 })} /></label>
              <label className="text-sm">Detection radius (m)<Input type="number" value={d.radius_m} onChange={(e) => patch({ radius_m: parseInt(e.target.value) || 0 })} /></label>
              <label className="text-sm">Service area
                <select
                  value={d.service_area_id ?? ""}
                  onChange={(e) => patch({ service_area_id: e.target.value || null })}
                  className="mt-1 w-full text-sm border rounded-md px-3 py-2 bg-background h-10"
                >
                  <option value="">Unassigned</option>
                  {serviceAreas.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={d.is_active} onCheckedChange={(v) => patch({ is_active: v })} />
              <span className="text-sm">Active</span>
            </div>

            <div className="border-t pt-3">
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-semibold">Pickup points</p>
                <button onClick={addPoint} className="flex items-center gap-1 text-xs font-semibold border rounded-lg px-2.5 py-1.5 hover:bg-muted"><Plus className="h-3.5 w-3.5" /> Add point</button>
              </div>
              {d.pickup_points.length === 0 ? (
                <p className="text-xs text-muted-foreground">No pickup points yet. Add the drivable meeting spots (entrances, doors).</p>
              ) : (
                <div className="space-y-2">
                  {d.pickup_points.map((p, i) => (
                    <div key={i} className="grid gap-2 sm:grid-cols-[1fr_auto_auto_auto] items-center">
                      <Input value={p.name} onChange={(e) => patchPoint(i, { name: e.target.value })} placeholder="North entrance" />
                      <Input className="w-32" type="number" value={p.lat} onChange={(e) => patchPoint(i, { lat: parseFloat(e.target.value) || 0 })} placeholder="lat" />
                      <Input className="w-32" type="number" value={p.lng} onChange={(e) => patchPoint(i, { lng: parseFloat(e.target.value) || 0 })} placeholder="lng" />
                      <button onClick={() => removePoint(i)} className="text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg p-2"><Trash2 className="h-4 w-4" /></button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button onClick={() => setEditing(null)} className="text-sm border rounded-lg px-4 py-2 hover:bg-muted">Cancel</button>
              <button onClick={save} disabled={saving || !d.name.trim()} className="flex items-center gap-1.5 text-sm font-semibold bg-primary text-primary-foreground rounded-lg px-4 py-2 disabled:opacity-50 hover:bg-primary/90">
                <Save className="h-4 w-4" /> {saving ? "Saving…" : "Save venue"}
              </button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">Venues {venues.length ? `(${venues.length})` : ""}</CardTitle></CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-12 text-center text-sm text-muted-foreground">Loading…</div>
          ) : error ? (
            <div className="py-12 text-center text-sm text-destructive">{error}</div>
          ) : venues.length === 0 ? (
            <div className="py-12 text-center text-sm text-muted-foreground">No venues yet. Add one to curate its pickup points.</div>
          ) : (
            <div className="divide-y">
              {venues.map((v) => (
                <div key={v.id} className="flex items-center justify-between py-3 gap-4">
                  <div className="min-w-0">
                    <p className="font-medium flex items-center gap-2 flex-wrap">
                      {v.name}
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 inline-flex items-center gap-1">
                        <MapPin className="h-2.5 w-2.5" />
                        {v.service_area_id ? (areaNameById[v.service_area_id] || "Unknown area") : "Unassigned"}
                      </span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-foreground/80 inline-flex items-center gap-1 tabular-nums">
                        <Ruler className="h-2.5 w-2.5" />
                        {v.radius_m} m
                      </span>
                      {!v.is_active && <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">inactive</span>}
                    </p>
                    <p className="text-xs text-muted-foreground tabular-nums">
                      {v.center_lat.toFixed(5)}, {v.center_lng.toFixed(5)} · {(v.pickup_points || []).length} point{(v.pickup_points || []).length === 1 ? "" : "s"}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button onClick={() => startEdit(v)} className="text-sm border rounded-lg px-3 py-1.5 hover:bg-muted">Edit</button>
                    <button onClick={() => remove(v)} className="text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg p-2"><Trash2 className="h-4 w-4" /></button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
