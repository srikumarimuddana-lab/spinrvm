"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Pagination } from "@/components/ui/pagination";
import VenueMap from "@/components/venue-map";
import { MapPin, Plus, Trash2, Save, X, RefreshCw, Search, Crosshair } from "lucide-react";
import {
  getVenues, createVenue, updateVenue, deleteVenue, getServiceAreas,
  type Venue, type VenueUpsert, type VenuePickupPoint,
} from "@/lib/api";

const EMPTY: VenueUpsert = {
  name: "", center_lat: 0, center_lng: 0, radius_m: 150, pickup_points: [], service_area_id: null, is_active: true,
};

const PAGE_SIZE = 15;
type StatusFilter = "all" | "active" | "inactive";

/** Ground distance in metres — mirrors the backend's own haversine so the
 * "outside radius" warning below matches what /maps/pickup-points will do. */
function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6_371_000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export default function VenuesPage() {
  const [venues, setVenues] = useState<Venue[]>([]);
  const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
  const [areaFilter, setAreaFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<{ id: string | null; data: VenueUpsert } | null>(null);
  // Which pickup point the map is placing. null = the venue centre.
  const [selectedPoint, setSelectedPoint] = useState<number | null>(null);
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

  // Computed columns are flattened onto the row so useTableSort can sort by
  // them directly (it reads plain keys off the object).
  const rows = useMemo(
    () =>
      venues.map((v) => ({
        ...v,
        area_name: v.service_area_id ? areaNameById[v.service_area_id] || "Unknown area" : "Unassigned",
        point_count: (v.pickup_points || []).length,
        status: v.is_active ? "Active" : "Inactive",
      })),
    [venues, areaNameById],
  );

  const activeCount = useMemo(() => rows.filter((r) => r.is_active).length, [rows]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (statusFilter !== "all" && (statusFilter === "active") !== r.is_active) return false;
      if (q && !r.name.toLowerCase().includes(q) && !r.area_name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [rows, statusFilter, query]);

  const { sorted, sort, toggle } = useTableSort(filtered, { key: "name", dir: "asc" });

  // Narrowing the filters shrinks the result set, so the page index is clamped
  // during render rather than corrected in an effect — an effect would paint one
  // blank frame first, and would not cover the list shrinking under a refresh.
  const maxPage = Math.max(0, Math.ceil(sorted.length / PAGE_SIZE) - 1);
  const safePage = Math.min(page, maxPage);

  const pageRows = useMemo(
    () => sorted.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE),
    [sorted, safePage],
  );

  // Changing a filter should land on the first page of the new result set,
  // not merely on a clamped one.
  const applyStatusFilter = (v: StatusFilter) => { setStatusFilter(v); setPage(0); };
  const applyQuery = (v: string) => { setQuery(v); setPage(0); };
  const applyAreaFilter = (v: string) => { setAreaFilter(v); setPage(0); };

  const startNew = () => {
    setEditing({ id: null, data: { ...EMPTY, pickup_points: [] } });
    setSelectedPoint(null);
  };
  const startEdit = (v: Venue) => {
    setEditing({
      id: v.id,
      data: {
        name: v.name, center_lat: v.center_lat, center_lng: v.center_lng, radius_m: v.radius_m,
        pickup_points: (v.pickup_points || []).map((p) => ({ ...p })),
        service_area_id: v.service_area_id ?? null, is_active: v.is_active,
      },
    });
    setSelectedPoint(null);
  };

  const patch = (p: Partial<VenueUpsert>) => setEditing((e) => e && ({ ...e, data: { ...e.data, ...p } }));
  const patchPoint = useCallback((i: number, p: Partial<VenuePickupPoint>) =>
    setEditing((e) => {
      if (!e) return e;
      const pts = [...e.data.pickup_points];
      pts[i] = { ...pts[i], ...p };
      return { ...e, data: { ...e.data, pickup_points: pts } };
    }), []);

  const addPoint = () => {
    const pts = editing?.data.pickup_points || [];
    // Seed a new point at the centre so it is visible on the map immediately,
    // rather than at 0,0 in the Gulf of Guinea.
    patch({
      pickup_points: [...pts, { name: "", lat: editing?.data.center_lat ?? 0, lng: editing?.data.center_lng ?? 0 }],
    });
    setSelectedPoint(pts.length);
  };
  const removePoint = (i: number) => {
    patch({ pickup_points: (editing?.data.pickup_points || []).filter((_, j) => j !== i) });
    setSelectedPoint((s) => (s === i ? null : s !== null && s > i ? s - 1 : s));
  };

  const setCenter = useCallback((lat: number, lng: number) => {
    setEditing((e) => e && ({ ...e, data: { ...e.data, center_lat: lat, center_lng: lng } }));
  }, []);
  const setPointPos = useCallback((i: number, lat: number, lng: number) => {
    patchPoint(i, { lat, lng });
  }, [patchPoint]);

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
      setSelectedPoint(null);
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
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><MapPin className="h-5 w-5" /> Pickup Venues</h1>
          <p className="text-sm text-muted-foreground">
            Curated meeting points for malls, the airport, and other large venues where a pin can land somewhere a car can&apos;t reach.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
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
            <CardTitle className="text-base">{editing.id ? `Edit “${d.name || "venue"}”` : "New venue"}</CardTitle>
            <button onClick={() => { setEditing(null); setSelectedPoint(null); }} aria-label="Close editor" className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="text-sm" htmlFor="venue-name">Name<Input id="venue-name" value={d.name} onChange={(e) => patch({ name: e.target.value })} placeholder="Cornwall Centre" /></label>
                  <label className="text-sm" htmlFor="venue-radius">Detection radius (m)<Input id="venue-radius" type="number" value={d.radius_m} onChange={(e) => patch({ radius_m: parseInt(e.target.value) || 0 })} /></label>
                  <label className="text-sm" htmlFor="venue-lat">Center latitude<Input id="venue-lat" type="number" value={d.center_lat} onChange={(e) => patch({ center_lat: parseFloat(e.target.value) || 0 })} /></label>
                  <label className="text-sm" htmlFor="venue-lng">Center longitude<Input id="venue-lng" type="number" value={d.center_lng} onChange={(e) => patch({ center_lng: parseFloat(e.target.value) || 0 })} /></label>
                  <label className="text-sm" htmlFor="venue-area">Service area
                    <select
                      id="venue-area"
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
                  <div className="flex items-end gap-2 pb-1">
                    <Switch id="venue-active" checked={d.is_active} onCheckedChange={(v) => patch({ is_active: v })} />
                    <label htmlFor="venue-active" className="text-sm">Active</label>
                  </div>
                </div>
                {!d.is_active && (
                  <p className="text-xs text-muted-foreground border-l-2 border-amber-500 pl-2">
                    Inactive venues are never returned by the rider app. Verify the centre and every
                    pickup point on the map before activating — riders are sent to these exact coordinates.
                  </p>
                )}
              </div>

              <VenueMap
                center={{ lat: d.center_lat, lng: d.center_lng }}
                radiusM={d.radius_m}
                points={d.pickup_points}
                selectedIndex={selectedPoint}
                onCenterChange={setCenter}
                onPointChange={setPointPos}
                onSelect={setSelectedPoint}
              />
            </div>

            <div className="border-t pt-3">
              <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
                <div>
                  <p className="text-sm font-semibold">Pickup points</p>
                  <p className="text-xs text-muted-foreground">
                    Select a row to place it on the map. Distance is measured from the venue centre.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {selectedPoint !== null && (
                    <button onClick={() => setSelectedPoint(null)} className="text-xs border rounded-lg px-2.5 py-1.5 hover:bg-muted">
                      Edit centre instead
                    </button>
                  )}
                  <button onClick={addPoint} className="flex items-center gap-1 text-xs font-semibold border rounded-lg px-2.5 py-1.5 hover:bg-muted"><Plus className="h-3.5 w-3.5" /> Add point</button>
                </div>
              </div>
              {d.pickup_points.length === 0 ? (
                <p className="text-xs text-muted-foreground">No pickup points yet. Add the drivable meeting spots (entrances, doors).</p>
              ) : (
                <div className="space-y-2">
                  {d.pickup_points.map((p, i) => {
                    const selected = selectedPoint === i;
                    const dist = haversineM(d.center_lat, d.center_lng, p.lat, p.lng);
                    // A point outside the detection radius can never be offered:
                    // the rider only sees the chooser when their pin is inside it.
                    const outside = Number.isFinite(dist) && dist > d.radius_m;
                    return (
                      <div
                        key={i}
                        className={`grid gap-2 sm:grid-cols-[auto_1fr_auto_auto_auto_auto] items-center rounded-lg p-1.5 transition-colors ${selected ? "bg-amber-50 dark:bg-amber-900/20 ring-1 ring-amber-400" : ""}`}
                      >
                        <button
                          type="button"
                          onClick={() => setSelectedPoint(selected ? null : i)}
                          aria-pressed={selected}
                          aria-label={`${selected ? "Deselect" : "Select"} pickup point ${i + 1} for map placement`}
                          className={`h-7 w-7 rounded-full text-xs font-bold shrink-0 ${selected ? "bg-amber-500 text-white" : "bg-sky-500 text-white hover:bg-sky-600"}`}
                        >
                          {i + 1}
                        </button>
                        <Input value={p.name} onChange={(e) => patchPoint(i, { name: e.target.value })} placeholder="North entrance" />
                        <Input className="w-32" type="number" value={p.lat} onChange={(e) => patchPoint(i, { lat: parseFloat(e.target.value) || 0 })} placeholder="lat" aria-label={`Point ${i + 1} latitude`} />
                        <Input className="w-32" type="number" value={p.lng} onChange={(e) => patchPoint(i, { lng: parseFloat(e.target.value) || 0 })} placeholder="lng" aria-label={`Point ${i + 1} longitude`} />
                        <span className={`text-xs tabular-nums px-1.5 whitespace-nowrap ${outside ? "text-destructive font-semibold" : "text-muted-foreground"}`}>
                          {Number.isFinite(dist) ? `${Math.round(dist)} m` : "—"}
                          {outside && " outside"}
                        </span>
                        <button onClick={() => removePoint(i)} aria-label={`Delete pickup point ${i + 1}`} className="text-destructive hover:bg-destructive/10 rounded-lg p-2"><Trash2 className="h-4 w-4" /></button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button onClick={() => { setEditing(null); setSelectedPoint(null); }} className="text-sm border rounded-lg px-4 py-2 hover:bg-muted">Cancel</button>
              <button onClick={save} disabled={saving || !d.name.trim()} className="flex items-center gap-1.5 text-sm font-semibold bg-primary text-primary-foreground rounded-lg px-4 py-2 disabled:opacity-50 hover:bg-primary/90">
                <Save className="h-4 w-4" /> {saving ? "Saving…" : "Save venue"}
              </button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="space-y-3">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <CardTitle className="text-base">
              Venues{" "}
              <span className="font-normal text-muted-foreground">
                ({activeCount} active, {rows.length - activeCount} inactive)
              </span>
            </CardTitle>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
              <Input
                value={query}
                onChange={(e) => applyQuery(e.target.value)}
                placeholder="Search name or service area…"
                aria-label="Search venues"
                className="pl-8"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => applyStatusFilter(e.target.value as StatusFilter)}
              className="text-sm border rounded-lg px-3 py-2 bg-background hover:bg-muted h-10"
              aria-label="Filter by status"
            >
              <option value="all">All statuses ({rows.length})</option>
              <option value="active">Active ({activeCount})</option>
              <option value="inactive">Inactive ({rows.length - activeCount})</option>
            </select>
            <select
              value={areaFilter}
              onChange={(e) => applyAreaFilter(e.target.value)}
              className="text-sm border rounded-lg px-3 py-2 bg-background hover:bg-muted h-10"
              aria-label="Filter by service area"
            >
              <option value="">All service areas</option>
              {serviceAreas.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-12 text-center text-sm text-muted-foreground">Loading…</div>
          ) : error ? (
            <div className="py-12 text-center text-sm text-destructive">{error}</div>
          ) : rows.length === 0 ? (
            <div className="py-12 text-center text-sm text-muted-foreground">No venues yet. Add one to curate its pickup points.</div>
          ) : filtered.length === 0 ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              No venues match these filters.{" "}
              <button onClick={() => { setQuery(""); setStatusFilter("all"); }} className="underline hover:text-foreground">Clear filters</button>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <SortableHead column="name" sort={sort} onSort={toggle}>Venue</SortableHead>
                      <SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead>
                      <SortableHead column="area_name" sort={sort} onSort={toggle}>Service area</SortableHead>
                      <SortableHead column="radius_m" sort={sort} onSort={toggle} align="right">Radius</SortableHead>
                      <SortableHead column="point_count" sort={sort} onSort={toggle} align="right">Points</SortableHead>
                      <TableHead>Centre</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pageRows.map((v) => (
                      <TableRow
                        key={v.id}
                        onClick={() => startEdit(v)}
                        className={`cursor-pointer ${editing?.id === v.id ? "bg-muted/60" : ""}`}
                      >
                        <TableCell className="font-medium">{v.name}</TableCell>
                        <TableCell>
                          <Badge variant={v.is_active ? "default" : "secondary"}>
                            {v.is_active ? "Active" : "Inactive"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-muted-foreground">{v.area_name}</TableCell>
                        <TableCell className="text-right tabular-nums">{v.radius_m} m</TableCell>
                        <TableCell className="text-right tabular-nums">{v.point_count}</TableCell>
                        {/* Guarded because a null coordinate from the API would
                            otherwise throw inside toFixed and take the whole
                            page down with it, not just this row. */}
                        <TableCell className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                          {Number.isFinite(v.center_lat) && Number.isFinite(v.center_lng)
                            ? `${v.center_lat.toFixed(5)}, ${v.center_lng.toFixed(5)}`
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                            <button
                              onClick={() => startEdit(v)}
                              className="inline-flex items-center gap-1 text-sm border rounded-lg px-2.5 py-1.5 hover:bg-muted"
                            >
                              <Crosshair className="h-3.5 w-3.5" /> Edit
                            </button>
                            <button onClick={() => remove(v)} aria-label={`Delete ${v.name}`} className="text-destructive hover:bg-destructive/10 rounded-lg p-2"><Trash2 className="h-4 w-4" /></button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <Pagination
                page={safePage}
                pageSize={PAGE_SIZE}
                hasNextPage={(safePage + 1) * PAGE_SIZE < sorted.length}
                totalCount={sorted.length}
                onPageChange={setPage}
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
