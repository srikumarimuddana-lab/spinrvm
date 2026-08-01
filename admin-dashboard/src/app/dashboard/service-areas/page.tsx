"use client";

import { useEffect, useState, lazy, Suspense } from "react";
import { useRouter } from "next/navigation";
import { useToast } from "@/components/ui/use-toast";
import { useCrudToast } from "@/components/ui/use-crud-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { getServiceAreas, createServiceArea, updateServiceArea, deleteServiceArea, getSubscriptionPlans, createSubscriptionPlan, updateSubscriptionPlan, deleteSubscriptionPlan, getDriverSubscriptions, getAreaFees, createAreaFee, updateAreaFee, deleteAreaFee, getVehicleTypes, getIncentives, createIncentive, toggleIncentive, deleteIncentive } from "@/lib/api";
import { Plus, Trash2, Pencil, MapPin, Settings, DollarSign, Car, CreditCard, ChevronDown, ChevronUp, ToggleLeft, ToggleRight, FileText, Clock, ShieldCheck, ShieldAlert, CheckCircle, Image, Plane, Radar, Gift, ArrowRightLeft } from "lucide-react";
import { useRequireModule } from "@/hooks/useRequireModule";

const GeofenceMap = lazy(() => import("@/components/geofence-map"));

const regulatoryDefaultsForProvince = (province: string) => {
  if (province === "SK") return { authority: "SGI", region: "SK" };
  if (province === "AB") return { authority: "Alberta TNC / municipal licensing", region: "AB" };
  if (province === "ON") return { authority: "Municipal vehicle-for-hire / PTC licensing", region: "ON" };
  return { authority: "Provincial / municipal authority", region: province || "" };
};

const CITY_PRESETS: Record<string, { city: string; province: string; center: { lat: number; lng: number }; polygon: { lat: number; lng: number }[] }> = {
  saskatoon: { city: "Saskatoon", province: "SK", center: { lat: 52.13, lng: -106.67 }, polygon: [{ lat: 52.19, lng: -106.75 }, { lat: 52.19, lng: -106.55 }, { lat: 52.08, lng: -106.55 }, { lat: 52.08, lng: -106.75 }] },
  regina: { city: "Regina", province: "SK", center: { lat: 50.45, lng: -104.62 }, polygon: [{ lat: 50.50, lng: -104.72 }, { lat: 50.50, lng: -104.52 }, { lat: 50.40, lng: -104.52 }, { lat: 50.40, lng: -104.72 }] },
  calgary: { city: "Calgary", province: "AB", center: { lat: 51.04, lng: -114.07 }, polygon: [{ lat: 51.15, lng: -114.25 }, { lat: 51.15, lng: -113.90 }, { lat: 50.90, lng: -113.90 }, { lat: 50.90, lng: -114.25 }] },
  edmonton: { city: "Edmonton", province: "AB", center: { lat: 53.55, lng: -113.49 }, polygon: [{ lat: 53.65, lng: -113.65 }, { lat: 53.65, lng: -113.35 }, { lat: 53.45, lng: -113.35 }, { lat: 53.45, lng: -113.65 }] },
  winnipeg: { city: "Winnipeg", province: "MB", center: { lat: 49.90, lng: -97.14 }, polygon: [{ lat: 49.97, lng: -97.30 }, { lat: 49.97, lng: -96.98 }, { lat: 49.80, lng: -96.98 }, { lat: 49.80, lng: -97.30 }] },
};

function polygonToText(polygon: any[]) { return polygon.map(p => `${p.lat}, ${p.lng}`).join("\n"); }

/** Extract polygon points [{lat,lng}] from area data. Backend may store as `polygon` or `geojson`. */
function getAreaPolygon(area: any): { lat: number; lng: number }[] {
  const geo = area.polygon || area.geojson;
  if (!geo) return [];
  // GeoJSON format: { type: "Polygon", coordinates: [[[lng,lat], ...]] }
  if (geo.type === "Polygon" && geo.coordinates?.[0]) {
    return geo.coordinates[0].map((c: number[]) => ({ lat: c[1], lng: c[0] }));
  }
  // Already an array of {lat,lng}
  if (Array.isArray(geo) && geo.length > 0 && geo[0].lat !== undefined) {
    return geo;
  }
  return [];
}

/** Get the center of an area's polygon for map centering. */
function getAreaCenter(area: any): { lat: number; lng: number } {
  const pts = getAreaPolygon(area);
  if (pts.length === 0) {
    // Fallback to city preset
    const key = (area.city || "").toLowerCase();
    return CITY_PRESETS[key]?.center || { lat: 52.13, lng: -106.67 };
  }
  return {
    lat: pts.reduce((s, p) => s + p.lat, 0) / pts.length,
    lng: pts.reduce((s, p) => s + p.lng, 0) / pts.length,
  };
}

export default function ServiceAreasPage() {
  const { allowed } = useRequireModule("service_areas");
  const router = useRouter();
  const { toast } = useToast();
  const crudToast = useCrudToast();
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [areas, setAreas] = useState<any[]>([]);
  const [plans, setPlans] = useState<any[]>([]);
  // Existing vehicle types (from /api/admin/vehicle-types) so the
  // Vehicle Pricing editor can render a dropdown of known types
  // instead of a free-text input the operator has to spell exactly.
  const [vehicleTypes, setVehicleTypes] = useState<{ id: string; name: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editTab, setEditTab] = useState("general");
  const [showCreate, setShowCreate] = useState(false);
  const [areaFees, setAreaFees] = useState<Record<string, any[]>>({});
  const [feesLoading, setFeesLoading] = useState<string | null>(null);

  const loadAreaFees = async (areaId: string) => {
    setFeesLoading(areaId);
    try {
      const fees = await getAreaFees(areaId);
      setAreaFees(prev => ({ ...prev, [areaId]: fees }));
    } catch {}
    setFeesLoading(null);
  };

  // Create form
  const [createForm, setCreateForm] = useState({
    name: "", city: "", province: "SK", preset: "",
    regulatory_authority: regulatoryDefaultsForProvince("SK").authority, regulatory_region: regulatoryDefaultsForProvince("SK").region,
    regulatory_requirements_url: "", regulatory_notes: "",
    polygon: [] as any[], polygonText: "",
    is_active: true, is_airport: false,
  });
  const [mapKey, setMapKey] = useState(0);

  // Airport sub-region create form
  const [addAirportFor, setAddAirportFor] = useState<string | null>(null);
  const [airportForm, setAirportForm] = useState({ name: "", airport_fee: 2.0, polygon: [] as any[] });
  const [airportMapKey, setAirportMapKey] = useState(0);

  useEffect(() => { load(); }, []);

  const load = async () => {
    setLoading(true);
    try {
      const [a, p, vt] = await Promise.all([
        getServiceAreas(),
        getSubscriptionPlans().catch(() => []),
        getVehicleTypes().catch(() => [] as any[]),
      ]);
      setAreas(Array.isArray(a) ? a : []);
      setPlans(p);
      setVehicleTypes((vt || []).map((v: any) => ({ id: v.id, name: v.name })));
    } catch {}
    setLoading(false);
  };

  const handlePreset = (key: string) => {
    if (key === "custom") {
      setCreateForm({ ...createForm, preset: key, city: "", polygon: [], polygonText: "" });
      setMapKey(k => k + 1);
      return;
    }
    const p = CITY_PRESETS[key];
    if (p) {
      setCreateForm({
        ...createForm, preset: key, name: createForm.name || p.city,
        city: p.city, province: p.province,
        regulatory_authority: regulatoryDefaultsForProvince(p.province).authority,
        regulatory_region: regulatoryDefaultsForProvince(p.province).region,
        polygon: p.polygon, polygonText: polygonToText(p.polygon),
      });
      setMapKey(k => k + 1);
    }
  };

  const handleCreate = async () => {
    if (!createForm.name) return;
    try {
      await createServiceArea({
        name: createForm.name, city: createForm.city, province: createForm.province,
        regulatory_authority: createForm.regulatory_authority || regulatoryDefaultsForProvince(createForm.province).authority,
        regulatory_region: createForm.regulatory_region || regulatoryDefaultsForProvince(createForm.province).region,
        regulatory_requirements_url: createForm.regulatory_requirements_url || "",
        regulatory_notes: createForm.regulatory_notes || "",
        geojson: { type: "Polygon", coordinates: [createForm.polygon.map(p => [p.lng, p.lat])] },
        is_active: createForm.is_active, is_airport: createForm.is_airport,
        // Defaults
        airport_fee: createForm.is_airport ? 5.0 : 0,
        gst_rate: 5.0, pst_rate: createForm.province === 'SK' ? 6.0 : createForm.province === 'AB' ? 0 : 7.0,
        insurance_fee_percent: 2.0, vehicle_pricing: [], subscription_plan_ids: [],
        spinr_pass_enabled: true, surge_enabled: false, surge_multiplier: 1.0,
        max_pickup_radius_km: 5.0, currency: 'CAD',
      });
      setShowCreate(false);
      setCreateForm({ name: "", city: "", province: "SK", preset: "", regulatory_authority: regulatoryDefaultsForProvince("SK").authority, regulatory_region: regulatoryDefaultsForProvince("SK").region, regulatory_requirements_url: "", regulatory_notes: "", polygon: [], polygonText: "", is_active: true, is_airport: false });
      crudToast.created("Service area");
      load();
    } catch (e) { crudToast.error("create service area", e); }
  };

  const handleCreateAirportSubRegion = async (parentId: string) => {
    const parent = areas.find(a => a.id === parentId);
    if (!airportForm.name || airportForm.polygon.length < 3) {
      crudToast.warn("Missing airport boundary", "Please enter a name and draw the airport boundary on the map.");
      return;
    }
    try {
      await createServiceArea({
        name: airportForm.name,
        city: parent?.city || "",
        province: parent?.province || "SK",
        geojson: { type: "Polygon", coordinates: [airportForm.polygon.map((p: any) => [p.lng, p.lat])] },
        is_active: true,
        is_airport: true,
        parent_service_area_id: parentId,
        airport_fee: airportForm.airport_fee,
      });
      setAddAirportFor(null);
      setAirportForm({ name: "", airport_fee: 2.0, polygon: [] });
      crudToast.created("Airport zone");
      load();
    } catch (e) { crudToast.error("create airport zone", e); }
  };

  const handleFieldUpdate = async (areaId: string, field: string, value: any) => {
    try {
      await updateServiceArea(areaId, { [field]: value });
      setAreas(prev => prev.map(a => {
        if (a.id === areaId) return { ...a, [field]: value };
        if (a.sub_regions?.length) {
          return { ...a, sub_regions: a.sub_regions.map((s: any) => s.id === areaId ? { ...s, [field]: value } : s) };
        }
        return a;
      }));
      crudToast.updated("Service area");
    } catch (e) { crudToast.error("update field", e); }
  };

  const handleVehiclePricingUpdate = async (areaId: string, pricing: any[]) => {
    await handleFieldUpdate(areaId, 'vehicle_pricing', pricing);
  };

  const handleDelete = (id: string, name: string) => {
    setDeleteTarget({ id, name });
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteServiceArea(deleteTarget.id);
      crudToast.deleted("Service area", `"${deleteTarget.name}" removed.`);
      load();
    }
    catch (e) { crudToast.error("delete service area", e); }
    finally { setDeleteTarget(null); }
  };

  if (!allowed) return null;

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Service Areas</h1>
          <p className="text-muted-foreground mt-1">Configure pricing, fees, taxes & subscriptions per area</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 bg-primary text-primary-foreground px-5 py-2.5 rounded-xl font-semibold hover:bg-primary/90">
          <Plus className="h-5 w-5" /> New Area
        </button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <div className="bg-card rounded-2xl border p-6 mb-6 shadow-sm">
          <h3 className="text-lg font-bold mb-4">Create Service Area</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-muted-foreground mb-1">City Preset</label>
              <select className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.preset} onChange={e => handlePreset(e.target.value)}>
                <option value="">Select city...</option>
                {Object.entries(CITY_PRESETS).map(([k, v]) => <option key={k} value={k}>{v.city}, {v.province}</option>)}
                <option value="custom">Custom</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-muted-foreground mb-1">Area Name *</label>
              <input className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.name} onChange={e => setCreateForm({...createForm, name: e.target.value})} placeholder="e.g. Saskatoon Metro" />
            </div>
            <div>
              <label className="block text-sm font-medium text-muted-foreground mb-1">Province</label>
              <select className="w-full border rounded-xl px-4 py-2.5 text-sm" value={createForm.province} onChange={e => { const province = e.target.value; const defaults = regulatoryDefaultsForProvince(province); setCreateForm({...createForm, province, regulatory_authority: defaults.authority, regulatory_region: defaults.region}); }}>
                {['SK','AB','MB','ON','BC','QC','NS','NB','PE','NL','NT','YT','NU'].map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          </div>
          <div className="flex items-center gap-4 mb-4">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={createForm.is_active} onChange={e => setCreateForm({...createForm, is_active: e.target.checked})} className="accent-primary" /> Active</label>
          </div>

          {/* Geofence Map — always visible, draw polygon or select preset */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-muted-foreground mb-2">
              Service Area Boundary {createForm.polygon.length === 0 && <span className="text-destructive">(select a preset or draw on the map)</span>}
            </label>
            <div className="h-64 rounded-xl overflow-hidden border">
              <Suspense fallback={<div className="h-full bg-muted flex items-center justify-center text-muted-foreground">Loading map...</div>}>
                <GeofenceMap
                  key={mapKey}
                  polygon={createForm.polygon}
                  center={CITY_PRESETS[createForm.preset]?.center || { lat: 52.13, lng: -106.67 }}
                  zoom={createForm.polygon.length > 0 ? 11 : 5}
                  onPolygonChange={(p: any) => setCreateForm({...createForm, polygon: p, polygonText: polygonToText(p)})}
                />
              </Suspense>
            </div>
            {createForm.polygon.length > 0 && (
              <p className="text-xs text-green-600 mt-1">{createForm.polygon.length} points defined</p>
            )}
          </div>

          <div className="flex gap-3">
            <button onClick={handleCreate} className="bg-primary text-primary-foreground px-6 py-2.5 rounded-xl font-semibold hover:bg-primary/90">Create</button>
            <button onClick={() => setShowCreate(false)} className="bg-muted text-foreground px-6 py-2.5 rounded-xl font-semibold">Cancel</button>
          </div>
        </div>
      )}

      {/* Areas List */}
      {loading ? (
        <div className="text-center py-12 text-muted-foreground">Loading...</div>
      ) : areas.length === 0 ? (
        <div className="text-center py-16 bg-card rounded-2xl border">
          <MapPin className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
          <h3 className="text-lg font-bold text-foreground">No service areas</h3>
          <p className="text-muted-foreground">Create your first service area to start operations</p>
        </div>
      ) : (
        <div className="space-y-3">
          {areas.map(area => {
            const isExpanded = expandedId === area.id;
            const subRegions: any[] = area.sub_regions || [];
            return (
              <div key={area.id} className="bg-card rounded-2xl border overflow-hidden">
                {/* Area Header — click to expand */}
                <div className="flex items-center gap-4 p-5 cursor-pointer" role="button" tabIndex={0} aria-expanded={isExpanded} onClick={() => { const newId = isExpanded ? null : area.id; setExpandedId(newId); setEditTab("general"); if (newId && !areaFees[newId]) loadAreaFees(newId); }} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const newId = isExpanded ? null : area.id; setExpandedId(newId); setEditTab("general"); if (newId && !areaFees[newId]) loadAreaFees(newId); } }}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${area.is_active ? 'bg-green-100' : 'bg-muted'}`}>
                    <MapPin className={`h-5 w-5 ${area.is_active ? 'text-green-600' : 'text-muted-foreground'}`} />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="font-bold text-foreground">{area.name}</h4>
                      {area.is_airport && <span className="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs font-bold rounded-md">AIRPORT</span>}
                      {!area.is_active && <span className="px-2 py-0.5 bg-gray-100 text-gray-500 text-xs font-bold rounded-md">INACTIVE</span>}
                      {subRegions.length > 0 && <span className="px-2 py-0.5 bg-violet-100 text-violet-700 text-xs font-bold rounded-md">{subRegions.length} airport zone{subRegions.length > 1 ? 's' : ''}</span>}
                    </div>
                    <p className="text-sm text-muted-foreground">{area.city || ''}{area.province ? `, ${area.province}` : ''} · GST {area.gst_rate || 5}% · PST {area.pst_rate || 0}%</p>
                  </div>
                  <div className="text-sm text-muted-foreground">{area.vehicle_pricing?.length || 0} vehicles · {area.subscription_plan_ids?.length || 0} plans</div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      router.push(`/dashboard/monitoring?areaId=${encodeURIComponent(area.id)}`);
                    }}
                    className="flex items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-semibold text-violet-700 transition-colors hover:bg-violet-100"
                    title="Open this area on the live monitor"
                  >
                    <Radar className="h-3.5 w-3.5" />
                    View on live monitor
                  </button>
                  {isExpanded ? <ChevronUp className="h-5 w-5 text-muted-foreground" /> : <ChevronDown className="h-5 w-5 text-muted-foreground" />}
                </div>

                {/* Expanded Config */}
                {isExpanded && (
                  <div className="border-t">
                    {/* Tabs */}
                    <div className="flex gap-1 px-5 pt-3 bg-muted overflow-x-auto">
                      {[
                        { key: 'general', label: 'General', icon: Settings },
                        { key: 'pricing', label: 'Vehicle Pricing', icon: Car },
                        { key: 'fees', label: 'Fees & Taxes', icon: DollarSign },
                        { key: 'subscriptions', label: 'Spinr Pass', icon: CreditCard },
                        { key: 'documents', label: 'Documents', icon: FileText },
                        { key: 'incentives', label: 'Incentives', icon: Gift },
                        { key: 'subregions', label: 'Airport Zones', icon: Plane },
                        { key: 'cascade', label: 'Dispatch Cascade', icon: ArrowRightLeft },
                      ].map(tab => (
                        <button key={tab.key} onClick={() => setEditTab(tab.key)}
                          className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-semibold rounded-t-lg transition ${editTab === tab.key ? 'bg-card text-primary border-t-2 border-primary' : 'text-muted-foreground hover:text-foreground'}`}>
                          <tab.icon className="h-4 w-4" /> {tab.label}
                        </button>
                      ))}
                    </div>

                    <div className="p-5">
                      {/* General Tab */}
                      {editTab === 'general' && (
                        <GeneralTabForm area={area} onSave={async (updates) => {
                          try {
                            await updateServiceArea(area.id, updates);
                            setAreas(prev => prev.map(a => a.id === area.id ? { ...a, ...updates } : a));
                            crudToast.updated("Service area");
                          } catch (e) { crudToast.error("save service area", e); }
                        }} onDelete={() => handleDelete(area.id, area.name)} />
                      )}

                      {/* Vehicle Pricing Tab */}
                      {editTab === 'pricing' && (
                        <VehiclePricingEditor pricing={area.vehicle_pricing || []} vehicleTypes={vehicleTypes} onSave={p => handleVehiclePricingUpdate(area.id, p)} />
                      )}

                      {/* Fees & Taxes Tab */}
                      {editTab === 'fees' && (
                        <AreaFeesEditor
                          areaId={area.id}
                          area={area}
                          fees={areaFees[area.id] || []}
                          loading={feesLoading === area.id}
                          onReload={() => loadAreaFees(area.id)}
                          onFieldUpdate={handleFieldUpdate}
                        />
                      )}

                      {/* Spinr Pass Tab */}
                      {editTab === 'subscriptions' && (
                        <SpinrPassAreaTab
                          area={area}
                          plans={plans}
                          onToggle={v => handleFieldUpdate(area.id, 'spinr_pass_enabled', v)}
                          onRequiredToggle={v => handleFieldUpdate(area.id, 'subscription_required', v)}
                          onPlansChanged={load}
                        />
                      )}

                      {/* Documents Tab */}
                      {editTab === 'documents' && (
                        <DocumentsEditor
                          docs={area.required_documents || []}
                          onSave={d => handleFieldUpdate(area.id, 'required_documents', d)}
                        />
                      )}

                      {/* Airport Zones (Sub-regions) Tab */}
                      {editTab === 'subregions' && (
                        <div>
                          <div className="flex items-center justify-between mb-4">
                            <div>
                              <h4 className="font-bold text-foreground">Airport Zones</h4>
                              <p className="text-sm text-muted-foreground">Draw airport boundaries inside {area.name}. Rides to/from these zones get an extra airport surcharge.</p>
                            </div>
                            {addAirportFor !== area.id && (
                              <button onClick={() => { setAddAirportFor(area.id); setAirportForm({ name: "", airport_fee: 2.0, polygon: [] }); setAirportMapKey(k => k + 1); }}
                                className="flex items-center gap-2 bg-blue-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-blue-600">
                                <Plane className="h-4 w-4" /> Add Airport Zone
                              </button>
                            )}
                          </div>

                          {/* Add Airport Form */}
                          {addAirportFor === area.id && (
                            <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 mb-5">
                              <h5 className="font-bold text-blue-900 mb-3 flex items-center gap-2">
                                <Plane className="h-4 w-4" /> New Airport Zone in {area.name}
                              </h5>
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                                <div>
                                  <label className="block text-xs font-semibold text-blue-800 mb-1">Airport Zone Name *</label>
                                  <input className="w-full border border-blue-200 rounded-lg px-3 py-2 text-sm bg-white"
                                    value={airportForm.name}
                                    onChange={e => setAirportForm({ ...airportForm, name: e.target.value })}
                                    placeholder={`e.g. ${area.city || area.name} Airport`} />
                                </div>
                                <div>
                                  <label className="block text-xs font-semibold text-blue-800 mb-1">Airport Fee ($)</label>
                                  <input className="w-full border border-blue-200 rounded-lg px-3 py-2 text-sm bg-white"
                                    type="number" step="0.50" min="0"
                                    value={airportForm.airport_fee}
                                    onChange={e => setAirportForm({ ...airportForm, airport_fee: parseFloat(e.target.value) || 0 })} />
                                </div>
                              </div>
                              <div className="mb-4">
                                <label className="block text-xs font-semibold text-blue-800 mb-2">
                                  Draw Airport Boundary on Map {airportForm.polygon.length === 0 && <span className="text-red-500">(required)</span>}
                                </label>
                                <div className="h-64 rounded-xl overflow-hidden border border-blue-200">
                                  <Suspense fallback={<div className="h-full bg-gray-100 flex items-center justify-center text-gray-400">Loading map...</div>}>
                                    <GeofenceMap
                                      key={airportMapKey}
                                      polygon={airportForm.polygon}
                                      center={getAreaCenter(area)}
                                      zoom={12}
                                      onPolygonChange={(p: any) => setAirportForm({ ...airportForm, polygon: p })}
                                    />
                                  </Suspense>
                                </div>
                                {airportForm.polygon.length > 0 && <p className="text-xs text-green-600 mt-1">{airportForm.polygon.length} points defined</p>}
                              </div>
                              <div className="flex gap-3">
                                <button onClick={() => handleCreateAirportSubRegion(area.id)} className="bg-blue-500 text-white px-5 py-2 rounded-xl text-sm font-semibold hover:bg-blue-600">Create Airport Zone</button>
                                <button onClick={() => setAddAirportFor(null)} className="bg-gray-100 text-gray-600 px-5 py-2 rounded-xl text-sm font-semibold">Cancel</button>
                              </div>
                            </div>
                          )}

                          {/* Existing Sub-regions */}
                          {subRegions.length === 0 && addAirportFor !== area.id ? (
                            <div className="text-center py-10 bg-muted rounded-xl">
                              <Plane className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
                              <p className="text-muted-foreground font-medium">No airport zones yet</p>
                              <p className="text-muted-foreground text-sm mt-1">Add an airport zone to automatically charge a surcharge for rides to/from the airport</p>
                            </div>
                          ) : (
                            <div className="space-y-4">
                              {subRegions.map((sub: any) => (
                                <div key={sub.id} className="bg-blue-50 border border-blue-100 rounded-xl p-4">
                                  <div className="flex items-center justify-between mb-3">
                                    <div className="flex items-center gap-2">
                                      <Plane className="h-4 w-4 text-blue-600" />
                                      <span className="font-bold text-blue-900">{sub.name}</span>
                                      <span className="px-2 py-0.5 bg-blue-200 text-blue-800 text-xs font-bold rounded-md">AIRPORT</span>
                                      {!sub.is_active && <span className="px-2 py-0.5 bg-gray-200 text-gray-600 text-xs font-bold rounded-md">INACTIVE</span>}
                                    </div>
                                    <button onClick={() => handleDelete(sub.id, sub.name)} className="text-sm text-red-500 hover:underline">Delete</button>
                                  </div>
                                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
                                    <FieldInput label="Zone Name" value={sub.name} onSave={v => handleFieldUpdate(sub.id, 'name', v)} />
                                    <FieldInput label="Airport Fee ($)" value={sub.airport_fee || 0} type="number" onSave={v => handleFieldUpdate(sub.id, 'airport_fee', parseFloat(v))} />
                                    <FieldToggle label="Active" value={sub.is_active} onSave={v => handleFieldUpdate(sub.id, 'is_active', v)} />
                                  </div>
                                  {/* Airport zone boundary map */}
                                  <div>
                                    <label className="block text-xs font-semibold text-blue-800 mb-2">Airport Zone Boundary</label>
                                    <div className="h-56 rounded-xl overflow-hidden border border-blue-200">
                                      <Suspense fallback={<div className="h-full bg-gray-100 flex items-center justify-center text-gray-400">Loading map...</div>}>
                                        <GeofenceMap
                                          key={`sub-${sub.id}`}
                                          polygon={getAreaPolygon(sub)}
                                          center={getAreaPolygon(sub).length > 0 ? getAreaCenter(sub) : getAreaCenter(area)}
                                          zoom={13}
                                          onPolygonChange={(p: any) => {
                                            const geojson = { type: "Polygon", coordinates: [p.map((pt: any) => [pt.lng, pt.lat])] };
                                            handleFieldUpdate(sub.id, 'polygon', geojson);
                                          }}
                                        />
                                      </Suspense>
                                    </div>
                                    {getAreaPolygon(sub).length > 0 && (
                                      <p className="text-xs text-green-600 mt-1">{getAreaPolygon(sub).length} boundary points</p>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Incentives Tab */}
                      {editTab === 'incentives' && (
                        <IncentivesTab areaId={area.id} areaName={area.name} vehicleTypes={vehicleTypes} />
                      )}

                      {/* Dispatch Cascade Tab */}
                      {editTab === 'cascade' && (
                        <CascadeEditor
                          cascadeMap={area.vehicle_cascade_map || []}
                          vehicleTypes={vehicleTypes.filter(vt =>
                            (area.vehicle_pricing || []).some((p: any) => p.vehicle_type === vt.name)
                          )}
                          onSave={async (map) => {
                            try {
                              await updateServiceArea(area.id, { vehicle_cascade_map: map });
                              setAreas(prev => prev.map(a => a.id === area.id ? { ...a, vehicle_cascade_map: map } : a));
                              crudToast.updated("Dispatch cascade");
                            } catch (e) { crudToast.error("save cascade", e); }
                          }}
                        />
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete "{deleteTarget?.name}"?</AlertDialogTitle>
            <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// ─── Inline Editable Field Components ───

// --- General Tab with single Save button ---

function GeneralTabForm({ area, onSave, onDelete }: { area: any; onSave: (updates: any) => Promise<void>; onDelete: () => void }) {
  const [form, setForm] = useState({
    name: area.name || "",
    city: area.city || "",
    province: area.province || "SK",
    regulatory_authority: area.regulatory_authority || regulatoryDefaultsForProvince(area.province || "SK").authority,
    regulatory_region: area.regulatory_region || area.province || "SK",
    regulatory_requirements_url: area.regulatory_requirements_url || "",
    regulatory_notes: area.regulatory_notes || "",
    max_pickup_radius_km: area.max_pickup_radius_km || 5,
    is_active: area.is_active !== false,
    driver_matching_algorithm: area.driver_matching_algorithm || "nearest",
    search_radius_km: area.search_radius_km || 10,
    min_driver_rating: area.min_driver_rating || 4.0,
    max_simultaneous_offers: area.max_simultaneous_offers || 3,
    use_eta_ranking: area.use_eta_ranking !== false,
    show_demand_heatmap: area.show_demand_heatmap || false,
    // Surge is per-area and admin-gated. The toggle below is the master
    // enable (surge_enabled): until it's on, the backend prices every ride at
    // 1.0× and the surge engine skips this area entirely. surge_multiplier is
    // the manual factor applied once enabled (1.0 = off, 1.5 = +50%, etc.).
    surge_enabled: area.surge_enabled !== undefined ? !!area.surge_enabled : !!area.surge_active,
    surge_multiplier: area.surge_multiplier ?? 1.0,
    surge_justification: "",
  });
  const [pendingPolygon, setPendingPolygon] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const surgeValue = parseFloat(String(form.surge_multiplier)) || 1.0;
  const needsJustification = form.surge_enabled && surgeValue > 2.5;

  const handleSave = async () => {
    // Did the operator actually change surge since load? Unrelated saves
    // (name, radius, etc.) must leave every surge field untouched so we
    // neither freeze auto-surge nor flip a not-currently-surging area active.
    const surgeTouched =
      form.surge_enabled !== (area.surge_enabled !== undefined ? !!area.surge_enabled : !!area.surge_active) ||
      parseFloat(String(form.surge_multiplier)) !== parseFloat(String(area.surge_multiplier ?? 1.0));

    // Only require (and only block on) justification when the operator is
    // actually applying an above-cap surge — not when saving an unrelated
    // field on an already-justified above-cap area.
    if (surgeTouched && needsJustification && !form.surge_justification.trim()) {
      alert("A written justification is required for surge multipliers above 2.5× (regulatory + reputational risk).");
      return;
    }
    setSaving(true);
    const updates: any = {
      name: form.name,
      city: form.city,
      province: form.province,
      regulatory_authority: form.regulatory_authority,
      regulatory_region: form.regulatory_region || form.province,
      regulatory_requirements_url: form.regulatory_requirements_url,
      regulatory_notes: form.regulatory_notes,
      max_pickup_radius_km: parseFloat(String(form.max_pickup_radius_km)) || 5,
      is_active: form.is_active,
      driver_matching_algorithm: form.driver_matching_algorithm,
      search_radius_km: parseFloat(String(form.search_radius_km)) || 10,
      min_driver_rating: parseFloat(String(form.min_driver_rating)) || 4.0,
      max_simultaneous_offers: Math.max(1, Math.min(10, parseInt(String(form.max_simultaneous_offers)) || 3)),
      use_eta_ranking: form.use_eta_ranking,
      show_demand_heatmap: form.show_demand_heatmap,
    };
    // Touch surge only when it actually changed. Sending surge_active on every
    // save would flip an enabled-but-idle auto area (surge_enabled=true,
    // surge_active=false) to active, making the public API advertise surge the
    // engine never triggered; and resending a parked multiplier on disable
    // would keep it in optimistic local state and resurrect it on re-enable.
    if (surgeTouched) {
      updates.surge_source = "manual";
      updates.surge_enabled = form.surge_enabled;
      if (form.surge_enabled) {
        // Enabling / changing the multiplier — apply it immediately.
        updates.surge_active = true;
        updates.surge_multiplier = surgeValue;
        if (needsJustification) updates.surge_justification = form.surge_justification.trim();
      } else {
        // Disabling — mirror the backend's reset so optimistic local state
        // drops the parked multiplier instead of holding it for a re-enable.
        updates.surge_active = false;
        updates.surge_multiplier = 1.0;
      }
    }
    if (pendingPolygon) {
      updates.polygon = pendingPolygon;
    }
    await onSave(updates);
    setPendingPolygon(null);
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <label className="block text-xs font-semibold text-muted-foreground mb-1">Area Name</label>
          <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted-foreground mb-1">City</label>
          <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.city} onChange={e => setForm({ ...form, city: e.target.value })} />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted-foreground mb-1">Province</label>
          <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.province} onChange={e => {
            const province = e.target.value;
            const defaults = regulatoryDefaultsForProvince(province);
            setForm({ ...form, province, regulatory_authority: form.regulatory_authority || defaults.authority, regulatory_region: form.regulatory_region || defaults.region });
          }}>
            {['SK','AB','MB','ON','BC','QC','NS','NB','PE','NL','NT','YT','NU'].map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted-foreground mb-1">Regulatory Authority</label>
          <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.regulatory_authority} onChange={e => setForm({ ...form, regulatory_authority: e.target.value })} placeholder="e.g. SGI, Calgary Livery, Toronto PTC" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted-foreground mb-1">Regulatory Region</label>
          <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.regulatory_region} onChange={e => setForm({ ...form, regulatory_region: e.target.value })} placeholder="e.g. SK, AB, Calgary, Toronto" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted-foreground mb-1">Requirements URL</label>
          <input className="w-full border rounded-lg px-3 py-2 text-sm" value={form.regulatory_requirements_url} onChange={e => setForm({ ...form, regulatory_requirements_url: e.target.value })} placeholder="Official local requirements link" />
        </div>
        <div className="md:col-span-3">
          <label className="block text-xs font-semibold text-muted-foreground mb-1">Regulatory Notes</label>
          <textarea className="w-full border rounded-lg px-3 py-2 text-sm min-h-[80px]" value={form.regulatory_notes} onChange={e => setForm({ ...form, regulatory_notes: e.target.value })} placeholder="Summarize local driver approval/licensing rules for this service area" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted-foreground mb-1">Pickup Radius (km)</label>
          <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.5" value={form.max_pickup_radius_km} onChange={e => setForm({ ...form, max_pickup_radius_km: e.target.value as any })} />
        </div>
        <div className="flex items-center gap-2 pt-5">
          <label className="text-xs font-semibold text-muted-foreground">Active</label>
          <button onClick={() => setForm({ ...form, is_active: !form.is_active })}>
            {form.is_active ? <ToggleRight className="h-6 w-6 text-green-500" /> : <ToggleLeft className="h-6 w-6 text-muted-foreground" />}
          </button>
        </div>
        <div className="flex items-center gap-2 pt-5">
          <label className="text-xs font-semibold text-muted-foreground">Demand Heatmap</label>
          <button onClick={() => setForm({ ...form, show_demand_heatmap: !form.show_demand_heatmap })}>
            {form.show_demand_heatmap ? <ToggleRight className="h-6 w-6 text-green-500" /> : <ToggleLeft className="h-6 w-6 text-muted-foreground" />}
          </button>
          <span className="text-xs text-muted-foreground">Show ride demand overlay to drivers</span>
        </div>
      </div>

      {/* Surge pricing — lives here now, no separate /dashboard/pricing page */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h4 className="font-bold text-foreground">Surge Pricing</h4>
          {area.surge_source === "manual" && (
            <button
              type="button"
              onClick={async () => {
                // Hand this area back to surge_engine. The engine will
                // recompute a multiplier on its next tick based on demand vs
                // available drivers. Send surge_enabled + surge_active too:
                // both fare calculation and recalculate_all_surges() are
                // gated on surge_enabled, so resetting source alone would
                // leave a previously-disabled area skipped by the engine and
                // priced at 1.0× despite the UI saying it's back on auto.
                // Mirrors the backend /surge/auto reset endpoint.
                await onSave({ surge_source: "auto", surge_enabled: true, surge_active: true });
              }}
              className="text-[11px] text-blue-600 hover:underline font-semibold"
            >
              Reset to auto-surge
            </button>
          )}
        </div>
        <p className="text-sm text-muted-foreground mb-3">
          Temporarily raise fares in this area during high-demand periods. When active,
          every vehicle&apos;s fare is multiplied by the surge factor.
          {area.surge_source === "auto" && (
            <span className="text-blue-600"> Currently auto-managed by the surge engine; editing these fields will switch it to manual.</span>
          )}
          {area.surge_source === "manual" && (
            <span className="text-amber-600"> Currently on manual override — surge engine will not touch this area until you reset.</span>
          )}
        </p>
        <div className="flex items-center gap-2 pt-1">
          <button type="button" onClick={() => setForm({ ...form, surge_enabled: !form.surge_enabled })}>
            {form.surge_enabled ? <ToggleRight className="h-6 w-6 text-green-500" /> : <ToggleLeft className="h-6 w-6 text-muted-foreground" />}
          </button>
          <label className="text-xs font-semibold text-muted-foreground">
            Surge {form.surge_enabled ? "ON" : "off"}
          </label>
        </div>
        {form.surge_enabled && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-end mt-3">
              <div>
                <label className="block text-xs font-semibold text-muted-foreground mb-1">Surge Multiplier</label>
                <input
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                  type="number"
                  step="0.1"
                  min="1"
                  max="10"
                  value={form.surge_multiplier}
                  onChange={e => setForm({ ...form, surge_multiplier: e.target.value as any })}
                />
              </div>
              <p className="text-[11px] text-muted-foreground pb-2">1.0 = no surge · 1.5 = +50% · 2.0 = double · Auto cap = 2.5×</p>
            </div>
            {needsJustification && (
              <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 space-y-2 mt-3">
                <p className="text-xs font-semibold text-amber-800">
                  Surge above 2.5× requires documented justification (regulatory + reputational risk).
                </p>
                <textarea
                  className="w-full border border-amber-300 rounded-lg px-3 py-2 text-sm bg-white"
                  rows={2}
                  placeholder="e.g. Major event surge — approved by ops lead @name on 2026-05-21"
                  value={form.surge_justification}
                  onChange={e => setForm({ ...form, surge_justification: e.target.value })}
                />
              </div>
            )}
          </>
        )}
      </div>

      {/* Driver Matching */}
      <div>
        <h4 className="font-bold text-foreground mb-2">Driver Matching</h4>
        <p className="text-sm text-muted-foreground mb-3">Configure how drivers are matched to rides in this area.</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-semibold text-muted-foreground mb-1">Matching Algorithm</label>
            <select className="w-full border rounded-lg px-3 py-2 text-sm" value={form.driver_matching_algorithm} onChange={e => setForm({ ...form, driver_matching_algorithm: e.target.value })}>
              <option value="nearest">Nearest</option>
              <option value="rating_based">Rating Based</option>
              <option value="round_robin">Round Robin</option>
              <option value="combined">Combined</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-muted-foreground mb-1">Search Radius (km)</label>
            <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.5" value={form.search_radius_km} onChange={e => setForm({ ...form, search_radius_km: e.target.value as any })} />
          </div>
          <div>
            <label className="block text-xs font-semibold text-muted-foreground mb-1">Min Driver Rating</label>
            <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" step="0.1" min="1" max="5" value={form.min_driver_rating} onChange={e => setForm({ ...form, min_driver_rating: e.target.value as any })} />
          </div>
          <div>
            <label className="block text-xs font-semibold text-muted-foreground mb-1">Simultaneous Offers</label>
            <input className="w-full border rounded-lg px-3 py-2 text-sm" type="number" min="1" max="10" value={form.max_simultaneous_offers} onChange={e => setForm({ ...form, max_simultaneous_offers: e.target.value as any })} />
          </div>
          <div className="flex items-center gap-2 pt-5">
            <input type="checkbox" id="use_eta" checked={form.use_eta_ranking} onChange={e => setForm({ ...form, use_eta_ranking: e.target.checked })} />
            <label htmlFor="use_eta" className="text-sm font-medium text-foreground">ETA Ranking</label>
          </div>
        </div>
      </div>

      {/* Geofence Editor */}
      <div>
        <h4 className="font-bold text-foreground mb-2">Service Area Boundary</h4>
        <p className="text-sm text-muted-foreground mb-3">Draw or edit the polygon to define the service area boundary.</p>
        <div className="h-80 rounded-xl overflow-hidden border">
          <Suspense fallback={<div className="h-full bg-muted flex items-center justify-center text-muted-foreground">Loading map...</div>}>
            <GeofenceMap
              key={`edit-${area.id}`}
              polygon={pendingPolygon ? pendingPolygon.coordinates[0].map((c: number[]) => ({ lat: c[1], lng: c[0] })) : getAreaPolygon(area)}
              center={getAreaCenter(area)}
              zoom={11}
              onPolygonChange={(p: any) => {
                setPendingPolygon({ type: "Polygon", coordinates: [p.map((pt: any) => [pt.lng, pt.lat])] });
              }}
            />
          </Suspense>
        </div>
        {(pendingPolygon || getAreaPolygon(area).length > 0) && (
          <p className="text-xs text-green-600 mt-1">
            {pendingPolygon ? `${pendingPolygon.coordinates[0].length} points (unsaved)` : `${getAreaPolygon(area).length} boundary points`}
          </p>
        )}
      </div>

      {/* Save + Delete */}
      <div className="flex items-center justify-between pt-2 border-t">
        <button onClick={onDelete} className="text-sm text-red-500 hover:underline">Delete this area</button>
        <button onClick={handleSave} disabled={saving}
          className={`px-6 py-2.5 rounded-xl text-sm font-semibold transition ${saved ? 'bg-green-500 text-white' : 'bg-primary text-primary-foreground hover:bg-primary/90'} disabled:opacity-50`}>
          {saving ? 'Saving...' : saved ? 'Saved!' : 'Save General Settings'}
        </button>
      </div>
    </div>
  );
}

function FieldInput({ label, value, type = "text", onSave }: { label: string; value: any; type?: string; onSave: (v: string) => void }) {
  const [val, setVal] = useState(String(value));
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setVal(String(value)); setDirty(false); }, [value]);
  return (
    <div>
      <label className="block text-xs font-semibold text-muted-foreground mb-1">{label}</label>
      <div className="flex gap-2">
        <input className="flex-1 border rounded-lg px-3 py-2 text-sm" type={type} step={type === 'number' ? '0.01' : undefined} value={val}
          onChange={e => { setVal(e.target.value); setDirty(true); }} />
        {dirty && <button onClick={() => { onSave(val); setDirty(false); }} className="px-3 py-1 bg-primary text-primary-foreground text-xs rounded-lg font-semibold">Save</button>}
      </div>
    </div>
  );
}

function FieldTextarea({ label, value, placeholder, onSave }: { label: string; value: any; placeholder?: string; onSave: (v: string) => void }) {
  const [val, setVal] = useState(String(value ?? ''));
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setVal(String(value ?? '')); setDirty(false); }, [value]);
  return (
    <div>
      <label className="block text-xs font-semibold text-muted-foreground mb-1">{label}</label>
      <textarea className="w-full border rounded-lg px-3 py-2 text-sm" rows={3} value={val} placeholder={placeholder}
        onChange={e => { setVal(e.target.value); setDirty(true); }} />
      {dirty && <button onClick={() => { onSave(val); setDirty(false); }} className="mt-1 px-3 py-1 bg-primary text-primary-foreground text-xs rounded-lg font-semibold">Save</button>}
    </div>
  );
}

function _FieldSelect({ label, value, options, onSave }: { label: string; value: string; options: string[]; onSave: (v: string) => void }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-muted-foreground mb-1">{label}</label>
      <select className="w-full border rounded-lg px-3 py-2 text-sm" value={value} onChange={e => onSave(e.target.value)}>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}

function FieldToggle({ label, value, onSave }: { label: string; value: boolean; onSave: (v: boolean) => void }) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs font-semibold text-muted-foreground">{label}</label>
      <button onClick={() => onSave(!value)}>
        {value ? <ToggleRight className="h-6 w-6 text-green-500" /> : <ToggleLeft className="h-6 w-6 text-muted-foreground" />}
      </button>
    </div>
  );
}

// ─── Vehicle Pricing Editor ───

function VehiclePricingEditor({ pricing, vehicleTypes, onSave }: { pricing: any[]; vehicleTypes: { id: string; name: string }[]; onSave: (p: any[]) => void }) {
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
            <tr className="text-left text-xs text-gray-500 border-b">
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
                      className="w-full border rounded-lg px-2 py-1.5 text-sm bg-white"
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
                  <td className="py-2"><button onClick={() => removeRow(i)} className="text-gray-400 hover:text-red-500"><Trash2 className="h-4 w-4" /></button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {vehicleTypes.length === 0 && (
        <p className="text-xs text-amber-600 mt-2">
          No vehicle types defined yet. Add them under <span className="font-semibold">Dashboard → Vehicle Types</span> first.
        </p>
      )}
      <div className="flex gap-3 mt-3">
        <button onClick={addRow} className="text-sm text-red-500 font-semibold hover:underline">+ Add vehicle type</button>
        <button
          onClick={() => { onSave(rows); setDirty(false); }}
          className={`px-5 py-2 rounded-xl text-sm font-semibold transition ${dirty ? 'bg-red-500 text-white hover:bg-red-600' : 'bg-gray-100 text-gray-400 cursor-not-allowed'}`}
          disabled={!dirty}
        >
          {dirty ? 'Save Pricing*' : 'Saved'}
        </button>
      </div>
    </div>
  );
}

// ─── Documents Editor ───

function DocumentsEditor({ docs, onSave }: { docs: any[]; onSave: (d: any[]) => void }) {
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
          <h4 className="font-bold text-gray-800 text-base">Required Documents</h4>
          <p className="text-sm text-gray-500 mt-0.5">Define which documents drivers must upload to operate in this area.</p>
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1"><FileText className="h-3.5 w-3.5" /> {rows.length} total</span>
          <span className="flex items-center gap-1"><ShieldCheck className="h-3.5 w-3.5 text-emerald-500" /> {requiredCount} required</span>
          <span className="flex items-center gap-1"><Clock className="h-3.5 w-3.5 text-amber-500" /> {expiryCount} with expiry</span>
        </div>
      </div>

      {/* Document Cards Grid */}
      {rows.length === 0 ? (
        <div className="text-center py-12 bg-gray-50 rounded-xl border-2 border-dashed border-gray-200">
          <Image className="h-10 w-10 mx-auto mb-3 text-gray-300" />
          <p className="text-sm font-medium text-gray-500">No documents required</p>
          <p className="text-xs text-gray-400 mt-1">Add document types that drivers need to upload</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {rows.map((r, i) => {
            const isEditing = editingIdx === i;
            return (
              <div key={i} className={`rounded-xl border overflow-hidden transition-all ${isEditing ? 'ring-2 ring-red-200 border-red-300 shadow-md' : 'bg-white hover:shadow-sm border-gray-200'}`}>
                {/* Card Header with preview */}
                <div className={`px-4 py-3 flex items-center gap-3 ${isEditing ? 'bg-red-50' : 'bg-gray-50'}`}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${r.required !== false ? 'bg-emerald-100' : 'bg-gray-100'}`}>
                    <FileText className={`h-5 w-5 ${r.required !== false ? 'text-emerald-600' : 'text-gray-400'}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-bold text-gray-800 truncate">{r.label || 'Untitled Document'}</p>
                    <p className="text-xs text-gray-400 font-mono">{r.key || 'no_key'}</p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button onClick={() => moveDoc(i, i - 1)} disabled={i === 0} className="p-1 text-gray-300 hover:text-gray-500 disabled:opacity-30" title="Move up">
                      <ChevronUp className="h-4 w-4" />
                    </button>
                    <button onClick={() => moveDoc(i, i + 1)} disabled={i === rows.length - 1} className="p-1 text-gray-300 hover:text-gray-500 disabled:opacity-30" title="Move down">
                      <ChevronDown className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* Status badges */}
                <div className="px-4 py-2.5 flex items-center gap-2 flex-wrap">
                  {r.required !== false ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-100 text-emerald-700"><ShieldCheck className="h-3 w-3" /> Required</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-500"><ShieldAlert className="h-3 w-3" /> Optional</span>
                  )}
                  {r.has_expiry ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 text-amber-700"><Clock className="h-3 w-3" /> Has Expiry</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-400">No Expiry</span>
                  )}
                  {r.requires_back_side && (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-blue-100 text-blue-700"><Image className="h-3 w-3" /> Both Sides</span>
                  )}
                </div>

                {/* Expand to edit or show actions */}
                {isEditing ? (
                  <div className="px-4 pb-4 space-y-3 border-t bg-white">
                    <div className="pt-3">
                      <label className="block text-[11px] font-semibold text-gray-500 mb-1">Document Label</label>
                      <input className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-red-200 focus:border-red-300 outline-none" placeholder="e.g. Driver's License" value={r.label} onChange={e => update(i, 'label', e.target.value)} />
                    </div>
                    <div>
                      <label className="block text-[11px] font-semibold text-gray-500 mb-1">Key (identifier)</label>
                      <input className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-red-200 focus:border-red-300 outline-none" placeholder="e.g. drivers_license" value={r.key} onChange={e => update(i, 'key', e.target.value)} />
                    </div>
                    <div className="flex flex-col gap-2.5">
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={r.has_expiry} onChange={e => update(i, 'has_expiry', e.target.checked)} className="accent-red-500 w-4 h-4" />
                        <span>Requires expiry date</span>
                      </label>
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={r.required !== false} onChange={e => update(i, 'required', e.target.checked)} className="accent-red-500 w-4 h-4" />
                        <span>Required</span>
                      </label>
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" checked={!!r.requires_back_side} onChange={e => update(i, 'requires_back_side', e.target.checked)} className="accent-red-500 w-4 h-4" />
                        <span>Requires both sides <span className="text-xs text-gray-400">(front &amp; back photo)</span></span>
                      </label>
                    </div>
                    <div className="flex items-center justify-between pt-2">
                      <button onClick={() => removeDoc(i)} className="flex items-center gap-1 text-sm text-red-500 hover:text-red-700 font-medium">
                        <Trash2 className="h-3.5 w-3.5" /> Remove
                      </button>
                      <button onClick={() => setEditingIdx(null)} className="px-4 py-1.5 bg-gray-100 text-gray-700 text-sm rounded-lg font-semibold hover:bg-gray-200">Done</button>
                    </div>
                  </div>
                ) : (
                  <div className="px-4 pb-3 flex items-center justify-between">
                    <button onClick={() => setEditingIdx(i)} className="flex items-center gap-1 text-xs text-gray-400 hover:text-red-500 font-medium">
                      <Pencil className="h-3 w-3" /> Edit
                    </button>
                    <button onClick={() => removeDoc(i)} className="flex items-center gap-1 text-xs text-gray-300 hover:text-red-500">
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
        <button onClick={addDoc} className="flex items-center gap-1.5 text-sm text-red-500 font-semibold hover:text-red-700 transition">
          <Plus className="h-4 w-4" /> Add document type
        </button>
        <div className="flex-1" />
        {dirty && <span className="text-xs text-amber-600 font-medium">Unsaved changes</span>}
        <button onClick={() => { onSave(rows); setDirty(false); }} className={`px-6 py-2.5 rounded-xl text-sm font-semibold transition ${dirty ? 'bg-red-500 text-white hover:bg-red-600 shadow-sm' : 'bg-gray-100 text-gray-400'}`}>
          <span className="flex items-center gap-1.5"><CheckCircle className="h-4 w-4" /> Save Documents</span>
        </button>
      </div>
    </div>
  );
}

// ─── Area Fees Editor ───

function AreaFeesEditor({ areaId, area, fees, loading, onReload, onFieldUpdate }: {
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
                        <h4 className="font-bold text-gray-800">Area Fees</h4>
                        <p className="text-sm text-gray-500">Fees applied to rides in this area. Calculated dynamically based on type.</p>
                    </div>
                    <button onClick={handleCreate} disabled={saving}
                        className="flex items-center gap-1.5 bg-red-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-red-600 disabled:opacity-50">
                        <Plus className="h-4 w-4" /> Add Fee
                    </button>
                </div>

                {loading ? (
                    <div className="text-center py-8 text-gray-400">Loading fees...</div>
                ) : fees.length === 0 ? (
                    <div className="text-center py-8 bg-gray-50 rounded-xl border-2 border-dashed">
                        <DollarSign className="h-8 w-8 text-gray-300 mx-auto mb-2" />
                        <p className="text-sm text-gray-500">No fees configured</p>
                        <p className="text-xs text-gray-400">Add fees like airport surcharge, night fee, city fee, etc.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                        {fees.map(fee => (
                            <div key={fee.id} className={`rounded-xl border p-4 ${fee.is_active ? 'bg-white' : 'bg-gray-50 opacity-60'}`}>
                                <div className="flex items-start justify-between mb-3">
                                    <div>
                                        <p className="font-bold text-gray-800">{fee.fee_name || fee.fee_type || 'Fee'}</p>
                                        <p className="text-xs text-gray-400 mt-0.5">{fee.fee_type} · {fee.calc_mode || 'flat'}</p>
                                    </div>
                                    <div className="text-right">
                                        <p className="text-lg font-bold text-gray-900">
                                            {fee.calc_mode === 'percentage' ? `${fee.amount}%` : `$${Number(fee.amount || 0).toFixed(2)}`}
                                            {fee.calc_mode === 'per_km' && '/km'}
                                        </p>
                                    </div>
                                </div>
                                {fee.description && <p className="text-xs text-gray-500 mb-3">{fee.description}</p>}
                                {fee.conditions && fee.conditions.start_hour !== undefined && (
                                    <p className="text-xs text-gray-400 mb-3">Hours: {fee.conditions.start_hour}:00 - {fee.conditions.end_hour}:00</p>
                                )}
                                <div className="flex items-center gap-2 pt-2 border-t">
                                    <button onClick={() => setEditingFee(editingFee?.id === fee.id ? null : fee)} className="text-xs text-gray-400 hover:text-red-500 font-medium flex items-center gap-1">
                                        <Pencil className="h-3 w-3" /> Edit
                                    </button>
                                    <button onClick={() => handleUpdate(fee.id, { is_active: !fee.is_active })} className="text-xs text-gray-400 hover:text-gray-600 font-medium ml-auto">
                                        {fee.is_active ? 'Disable' : 'Enable'}
                                    </button>
                                    <button onClick={() => handleDelete(fee.id)} className="text-xs text-gray-300 hover:text-red-500 font-medium">
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
                <h4 className="font-bold text-gray-800 mb-3">Tax Configuration</h4>
                <div className="bg-white rounded-xl border p-4">
                    <div className="flex items-center gap-4 mb-4">
                        <label className="flex items-center gap-2 text-sm">
                            <input type="radio" name={`tax-${areaId}`} checked={!area.hst_enabled}
                                onChange={() => onFieldUpdate(areaId, 'hst_enabled', false)} className="accent-red-500" />
                            GST + PST (separate)
                        </label>
                        <label className="flex items-center gap-2 text-sm">
                            <input type="radio" name={`tax-${areaId}`} checked={!!area.hst_enabled}
                                onChange={() => onFieldUpdate(areaId, 'hst_enabled', true)} className="accent-red-500" />
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
                <h4 className="font-bold text-gray-800 mb-3">Cancellation Fees</h4>
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
                <h4 className="font-bold text-gray-800 mb-3">Referral Rewards</h4>
                <p className="text-xs text-gray-500 mb-3">
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
                <p className="text-xs text-gray-500 mt-4 mb-2">
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
                    <AlertDialogAction onClick={confirmFeeDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
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
                    className="bg-red-500 text-white px-4 py-1.5 rounded-lg text-sm font-semibold hover:bg-red-600 disabled:opacity-50">
                    {saving ? 'Saving...' : 'Save'}
                </button>
                <button onClick={onCancel} className="bg-gray-100 text-gray-600 px-4 py-1.5 rounded-lg text-sm font-semibold">Cancel</button>
            </div>
        </div>
    );
}

// --- Spinr Pass per-area tab with full plan management ---

const DURATION_OPTIONS = [
  { label: "Daily", value: 1 },
  { label: "Weekly", value: 7 },
  { label: "Monthly", value: 30 },
  { label: "Yearly", value: 365 },
];

function SpinrPassAreaTab({ area, plans, onToggle, onRequiredToggle, onPlansChanged }: {
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
    if (!form.name || !form.price) return;
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
      <div className={`flex items-center justify-between p-4 rounded-xl mb-3 ${enabled ? 'bg-green-50 border border-green-200' : 'bg-gray-50 border border-gray-200'}`}>
        <div>
          <h4 className="font-bold text-gray-800">Spinr Pass for {area.name}</h4>
          <p className="text-sm text-gray-500">
            {enabled ? 'Drivers in this area can see and subscribe to plans' : 'Disabled — drivers see "It\'s Free Right Now!" instead'}
          </p>
        </div>
        <FieldToggle label={enabled ? "ON" : "OFF"} value={enabled} onSave={onToggle} />
      </div>

      {/* Mandatory-subscription toggle */}
      <div className={`flex items-center justify-between p-4 rounded-xl mb-5 ${required ? 'bg-amber-50 border border-amber-200' : 'bg-gray-50 border border-gray-200'}`}>
        <div>
          <h4 className="font-bold text-gray-800">Require Subscription to Drive</h4>
          <p className="text-sm text-gray-500">
            {required
              ? 'Drivers must have an active Spinr Pass to go online, receive offers, and accept rides'
              : 'Spinr Pass is optional — all verified drivers can work in this area'}
          </p>
        </div>
        <FieldToggle label={required ? "Required" : "Optional"} value={required} onSave={onRequiredToggle} />
      </div>

      {/* Plan management */}
      <div className="flex items-center justify-between mb-4">
        <h4 className="font-bold text-gray-800">Subscription Plans</h4>
        <button onClick={() => { resetForm(); setShowForm(true); }} className="flex items-center gap-1.5 bg-red-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-red-600">
          <Plus className="h-4 w-4" /> New Plan
        </button>
      </div>

      {/* Create/Edit Form */}
      {showForm && (
        <div className="bg-white rounded-xl border p-5 mb-5 shadow-sm">
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
                <button onClick={() => setForm({...form, rides_per_day: -1})} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${form.rides_per_day === -1 ? "bg-red-500 text-white border-red-500" : "bg-white border-gray-200"}`}>Unlimited</button>
                {[4, 8, 12, 20].map(n => (
                  <button key={n} onClick={() => setForm({...form, rides_per_day: n})} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${form.rides_per_day === n ? "bg-red-500 text-white border-red-500" : "bg-white border-gray-200"}`}>{n}</button>
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
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_active} onChange={e => setForm({...form, is_active: e.target.checked})} className="accent-red-500" /> Active</label>
          </div>
          <div className="flex gap-2">
            <button onClick={handleSubmit} className="bg-red-500 text-white px-5 py-2 rounded-xl text-sm font-semibold hover:bg-red-600">{editingId ? "Save" : "Create Plan"}</button>
            <button onClick={resetForm} className="bg-gray-100 text-gray-600 px-5 py-2 rounded-xl text-sm font-semibold">Cancel</button>
          </div>
        </div>
      )}

      {/* Plans List */}
      {plans.length === 0 ? (
        <div className="text-center py-12 bg-gray-50 rounded-xl">
          <CreditCard className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 font-medium">No subscription plans yet</p>
          <p className="text-gray-400 text-sm mt-1">Create your first Spinr Pass plan above</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
          {plans.map(p => (
            <div key={p.id} className={`bg-white rounded-xl border p-5 relative ${!p.is_active ? "opacity-50" : ""}`}>
              <button onClick={() => handleTogglePlan(p)} className="absolute top-3 right-3">
                {p.is_active ? <ToggleRight className="h-5 w-5 text-green-500" /> : <ToggleLeft className="h-5 w-5 text-gray-300" />}
              </button>
              <h5 className="font-bold text-gray-900 text-lg">{p.name}</h5>
              {p.description && <p className="text-gray-500 text-xs mt-0.5">{p.description}</p>}
              <div className="mt-2 mb-3">
                <span className="text-2xl font-extrabold text-red-500">${p.price?.toFixed(2)}</span>
                <span className="text-gray-400 text-xs ml-1">/ {getDurationLabel(p.duration_days).toLowerCase()}</span>
              </div>
              <p className="text-sm text-gray-600 mb-1">
                {p.rides_per_day === -1 ? 'Unlimited rides/day' : `${p.rides_per_day} rides/day`}
              </p>
              <p className="text-xs text-gray-400">{p.subscriber_count || 0} subscribers</p>
              {(p.features || []).length > 0 && (
                <div className="border-t mt-3 pt-2">
                  {p.features.map((f: string, i: number) => <p key={i} className="text-xs text-gray-500 py-0.5">✓ {f}</p>)}
                </div>
              )}
              <div className="flex gap-2 mt-3 pt-2 border-t">
                <button onClick={() => handleEdit(p)} className="flex-1 text-center py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 rounded-lg"><Pencil className="h-3 w-3 inline mr-1" />Edit</button>
                <button onClick={() => handleDeletePlan(p)} className="flex-1 text-center py-1.5 text-xs font-semibold text-red-500 hover:bg-red-50 rounded-lg"><Trash2 className="h-3 w-3 inline mr-1" />Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Subscribers */}
      {plans.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-bold text-gray-800">Subscribers</h4>
            {!subsLoaded && <button onClick={loadSubs} className="text-sm text-red-500 font-semibold hover:underline">Load subscribers</button>}
          </div>
          {subsLoaded && (
            subs.length === 0 ? (
              <p className="text-gray-400 text-sm">No subscribers yet.</p>
            ) : (
              <div className="bg-white rounded-xl border overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left">
                    <tr>
                      <th className="px-4 py-2 font-semibold text-gray-600 text-xs">Driver</th>
                      <th className="px-4 py-2 font-semibold text-gray-600 text-xs">Plan</th>
                      <th className="px-4 py-2 font-semibold text-gray-600 text-xs">Status</th>
                      <th className="px-4 py-2 font-semibold text-gray-600 text-xs">Expires</th>
                    </tr>
                  </thead>
                  <tbody>
                    {subs.map(s => (
                      <tr key={s.id} className="border-t">
                        <td className="px-4 py-2 font-mono text-xs">{s.driver_id?.slice(0, 8)}...</td>
                        <td className="px-4 py-2">{s.plan_name}</td>
                        <td className="px-4 py-2">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${s.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>{s.status?.toUpperCase()}</span>
                        </td>
                        <td className="px-4 py-2 text-xs text-gray-500">{s.expires_at ? new Date(s.expires_at).toLocaleDateString() : '—'}</td>
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
            <AlertDialogAction onClick={confirmPlanDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// --- Incentives Tab ---

const INCENTIVE_TYPES = [
  { value: 'per_ride', label: 'Per Ride', desc: 'Flat bonus on every ride' },
  { value: 'peak_hours', label: 'Peak Hours', desc: 'Bonus during busy times' },
  { value: 'time_limited', label: 'Time Limited', desc: 'Limited-time campaign' },
  { value: 'min_distance', label: 'Min Distance', desc: 'Bonus for longer rides' },
  { value: 'area_boost', label: 'Area Boost', desc: 'Boost for this area' },
];

// ─── Dispatch Cascade Editor ───

function CascadeEditor({
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
        <h4 className="font-bold text-gray-800">Dispatch Cascade Rules</h4>
        <p className="text-sm text-gray-500 mt-0.5">
          When a ride is booked for a vehicle type and no driver of that type is available,
          dispatch automatically tries the upgrade types listed here.
          Only vehicle types configured under <span className="font-semibold">Vehicle Pricing</span> for
          this area appear below.
        </p>
      </div>

      {vehicleTypes.length === 0 ? (
        <div className="text-center py-10 bg-amber-50 rounded-xl border-2 border-dashed border-amber-200">
          <Car className="h-10 w-10 text-amber-300 mx-auto mb-3" />
          <p className="text-amber-700 font-medium">No vehicle types configured for this area</p>
          <p className="text-amber-600 text-sm mt-1">
            Add vehicle types under the <span className="font-semibold">Vehicle Pricing</span> tab first,
            then return here to set up cascade rules.
          </p>
        </div>
      ) : rules.length === 0 ? (
        <div className="text-center py-10 bg-gray-50 rounded-xl border-2 border-dashed border-gray-200">
          <ArrowRightLeft className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 font-medium">No cascade rules</p>
          <p className="text-gray-400 text-sm mt-1">
            Add a rule to allow larger vehicles to fill in when the exact type has no available driver.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {rules.map((rule, idx) => {
            const toOptions = vehicleTypes.filter(v => v.id !== rule.from);
            const takenFromIds = new Set(rules.filter((_, i) => i !== idx).map(r => r.from));
            return (
              <div key={idx} className="rounded-xl border bg-white p-4">
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

                  <div className="flex items-center pt-6 text-gray-400">
                    <ArrowRightLeft className="h-4 w-4" />
                  </div>

                  <div className="flex-1">
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">Also offer to drivers of</label>
                    <div className="border rounded-lg p-2 space-y-1 min-h-[42px]">
                      {toOptions.length === 0 ? (
                        <p className="text-xs text-gray-400 px-1 py-1">No other vehicle types available</p>
                      ) : toOptions.map(v => (
                        <label key={v.id} className="flex items-center gap-2 text-sm cursor-pointer px-1">
                          <input
                            type="checkbox"
                            checked={rule.to.includes(v.id)}
                            onChange={() => toggleTo(idx, v.id)}
                            className="accent-red-500"
                          />
                          {v.name}
                        </label>
                      ))}
                    </div>
                    {rule.to.length > 0 && (
                      <p className="text-xs text-gray-400 mt-1">
                        Cascade order: {rule.to.map(id => vtName(id)).join(' → ')}
                      </p>
                    )}
                  </div>

                  <button
                    onClick={() => removeRule(idx)}
                    className="pt-6 text-gray-300 hover:text-red-500"
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
          className="flex items-center gap-1.5 text-sm text-red-500 font-semibold hover:text-red-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Plus className="h-4 w-4" /> Add cascade rule
        </button>
        <button
          onClick={handleSave}
          disabled={!dirty || saving}
          className={`px-6 py-2.5 rounded-xl text-sm font-semibold transition ${saved ? 'bg-green-500 text-white' : dirty ? 'bg-red-500 text-white hover:bg-red-600' : 'bg-gray-100 text-gray-400 cursor-not-allowed'} disabled:opacity-50`}
        >
          {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Cascade Rules'}
        </button>
      </div>
    </div>
  );
}

function IncentivesTab({ areaId, areaName, vehicleTypes }: { areaId: string; areaName: string; vehicleTypes: { id: string; name: string }[] }) {
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
          <h4 className="font-bold text-gray-800">Driver Ride Incentives</h4>
          <p className="text-sm text-gray-500">Bonuses shown to drivers on the ride offer screen in {areaName}.</p>
        </div>
        {!showForm && (
          <button onClick={() => setShowForm(true)}
            className="flex items-center gap-2 bg-amber-500 text-white px-4 py-2 rounded-xl text-sm font-semibold hover:bg-amber-600">
            <Gift className="h-4 w-4" /> Add Incentive
          </button>
        )}
      </div>

      {showForm && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-5 mb-5">
          <h5 className="font-bold text-amber-900 mb-3 flex items-center gap-2">
            <Gift className="h-4 w-4" /> New Incentive for {areaName}
          </h5>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-xs font-semibold text-amber-800 mb-1">Name *</label>
              <input className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm bg-white"
                value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. Peak Hour Bonus" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-amber-800 mb-1">Type</label>
              <select className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm bg-white"
                value={form.incentive_type} onChange={e => setForm({ ...form, incentive_type: e.target.value })}>
                {INCENTIVE_TYPES.map(t => <option key={t.value} value={t.value}>{t.label} — {t.desc}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-amber-800 mb-1">Bonus Amount ($)</label>
              <input className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm bg-white"
                type="number" step="0.50" min="0.50" max="500"
                value={form.bonus_amount} onChange={e => setForm({ ...form, bonus_amount: parseFloat(e.target.value) || 0 })} />
            </div>
            <div>
              <label className="block text-xs font-semibold text-amber-800 mb-1">Vehicle Type (optional)</label>
              <select className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm bg-white"
                value={form.vehicle_type_id} onChange={e => setForm({ ...form, vehicle_type_id: e.target.value })}>
                <option value="">All vehicle types</option>
                {vehicleTypes.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-amber-800 mb-1">Description</label>
              <input className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm bg-white"
                value={form.description} onChange={e => setForm({ ...form, description: e.target.value })}
                placeholder="Shown to drivers on ride offer" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-amber-800 mb-1">Budget Cap ($ optional)</label>
              <input className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm bg-white"
                type="number" step="100" min="0"
                value={form.max_budget} onChange={e => setForm({ ...form, max_budget: e.target.value })}
                placeholder="Leave empty for unlimited" />
            </div>
          </div>
          <div className="flex gap-3">
            <button onClick={handleCreate} disabled={saving || !form.name}
              className="bg-amber-600 text-white px-5 py-2 rounded-lg text-sm font-semibold hover:bg-amber-700 disabled:opacity-50">
              {saving ? 'Creating...' : 'Create Incentive'}
            </button>
            <button onClick={() => setShowForm(false)} className="text-gray-500 px-4 py-2 rounded-lg text-sm hover:bg-gray-100">Cancel</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-center py-8 text-gray-400">Loading incentives...</div>
      ) : incentives.length === 0 ? (
        <div className="text-center py-8 text-gray-400">
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
              <div key={inc.id} className={`flex items-center gap-4 p-4 rounded-xl border ${inc.is_active ? 'bg-white border-amber-200' : 'bg-gray-50 border-gray-200 opacity-60'}`}>
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${inc.is_active ? 'bg-amber-100' : 'bg-gray-200'}`}>
                  <Gift className={`h-5 w-5 ${inc.is_active ? 'text-amber-600' : 'text-gray-400'}`} />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-gray-900">{inc.name}</span>
                    <span className="px-2 py-0.5 bg-amber-100 text-amber-700 text-xs font-bold rounded-md">${parseFloat(inc.bonus_amount).toFixed(2)}</span>
                    <span className="px-2 py-0.5 bg-gray-100 text-gray-600 text-xs font-semibold rounded-md">{typeInfo?.label || inc.incentive_type}</span>
                  </div>
                  <p className="text-sm text-gray-500">{vtName}{inc.description ? ` · ${inc.description}` : ''}</p>
                  {inc.max_budget && (
                    <p className="text-xs text-gray-400 mt-1">Budget: ${parseFloat(inc.budget_used || 0).toFixed(0)} / ${parseFloat(inc.max_budget).toFixed(0)}</p>
                  )}
                </div>
                <button onClick={() => handleToggle(inc.id)}
                  className={`p-2 rounded-lg ${inc.is_active ? 'text-green-600 hover:bg-green-50' : 'text-gray-400 hover:bg-gray-100'}`}
                  title={inc.is_active ? 'Deactivate' : 'Activate'}>
                  {inc.is_active ? <ToggleRight className="h-5 w-5" /> : <ToggleLeft className="h-5 w-5" />}
                </button>
                <button onClick={() => handleDelete(inc.id)}
                  className="p-2 rounded-lg text-red-400 hover:bg-red-50 hover:text-red-600" title="Delete">
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
